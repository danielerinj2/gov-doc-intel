from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.config import settings
from app.infra.ocr_adapter import OCRAdapter
from app.infra.repositories import DocumentRepository, build_repository
from app.pipeline.level2_modules import (
    classify_document,
    extract_fields,
    fraud_signals,
    overall_confidence,
    preprocess_image,
    validate_fields,
)


class DocumentService:
    def __init__(self) -> None:
        repo, using_supabase, repo_error = build_repository()
        self.repo: DocumentRepository = repo
        self.using_supabase = using_supabase
        self.repo_error = repo_error
        self.persistence_backend = type(repo).__name__
        self.ocr = OCRAdapter(default_lang=settings.ocr_default_lang)
        self.data_dir = Path(settings.data_dir)
        self.upload_dir = self.data_dir / "uploads"
        self.processed_dir = self.data_dir / "processed"
        self.default_tenant_id = (
            settings.default_workspace_id
            or settings.appwrite_project_id
            or "workspace-default"
        )
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.processed_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _hash_bytes(self, payload: bytes) -> str:
        return hashlib.sha256(payload).hexdigest()[:24]

    def _build_ingestion_metadata(
        self,
        metadata: dict[str, Any],
        *,
        submitted_by: str,
        file_name: str,
        file_hash: str,
        source: str,
        file_uri: str | None = None,
    ) -> dict[str, Any]:
        out = dict(metadata)
        ingestion = dict(out.get("ingestion") or {})
        ingestion.setdefault("submitted_by", submitted_by)
        ingestion.setdefault("received_at", self._utc_now())
        ingestion.setdefault("perceptual_hash", file_hash)
        ingestion.setdefault("source", source)
        ingestion.setdefault("original_file_name", file_name)
        if file_uri:
            ingestion.setdefault("original_file_uri", file_uri)
        out["ingestion"] = ingestion
        return out

    def _write_upload(self, file_name: str, payload: bytes) -> str:
        suffix = Path(file_name).suffix or ".bin"
        path = self.upload_dir / f"{uuid4()}{suffix}"
        path.write_bytes(payload)
        return str(path)

    def _read_plain_text_if_possible(self, file_name: str, payload: bytes) -> str:
        suffix = Path(file_name).suffix.lower()
        if suffix in {".txt", ".csv", ".json"}:
            try:
                return payload.decode("utf-8", errors="ignore").strip()
            except Exception:
                return ""
        return ""

    def create_document(
        self,
        *,
        citizen_id: str,
        file_name: str,
        file_bytes: bytes,
        actor_id: str,
        role: str,
        source: str = "ONLINE_PORTAL",
        script_hint: str = "AUTO-DETECT",
        doc_type_hint: str = "AUTO-DETECT",
        notes: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        metadata = dict(metadata or {})
        tenant_id = str(
            metadata.get("tenant_id")
            or self.default_tenant_id
            or "workspace-default"
        )
        metadata["tenant_id"] = tenant_id
        file_hash = self._hash_bytes(file_bytes)
        file_path = self._write_upload(file_name=file_name, payload=file_bytes)

        metadata = self._build_ingestion_metadata(
            metadata,
            submitted_by=actor_id,
            file_name=file_name,
            file_hash=file_hash,
            source=source,
            file_uri=file_path,
        )
        metadata["script_hint"] = script_hint
        metadata["doc_type_hint"] = doc_type_hint
        if notes:
            metadata["operator_notes"] = notes

        raw_text = self._read_plain_text_if_possible(file_name=file_name, payload=file_bytes)

        row = self.repo.create_document(
            {
                "tenant_id": tenant_id,
                "citizen_id": citizen_id,
                "file_name": file_name,
                "file_path": file_path,
                "raw_text": raw_text,
                "ocr_text": "",
                "ocr_confidence": 0.0,
                "classification_output": {},
                "extraction_output": {"fields": []},
                "validation_output": {},
                "fraud_output": {},
                "confidence": 0.0,
                "risk_score": 0.0,
                "state": "INGESTED",
                "decision": None,
                "metadata": metadata,
                "last_actor": actor_id,
                "last_actor_role": role,
            }
        )

        self.log_event(
            document_id=str(row["id"]),
            actor_id=actor_id,
            actor_role=role,
            event_type="document.ingested",
            payload={"file_name": file_name, "citizen_id": citizen_id, "source": source},
            tenant_id=tenant_id,
        )
        return row

    def _resolve_document_file_path(self, doc: dict[str, Any]) -> tuple[str, bool]:
        direct = str(doc.get("file_path") or "").strip()
        if direct:
            return direct, False

        metadata = dict(doc.get("metadata") or {})
        ingestion = dict(metadata.get("ingestion") or {})
        fallback_uri = str(ingestion.get("original_file_uri") or "").strip()
        if fallback_uri:
            return fallback_uri, True

        raw_text = str(doc.get("raw_text") or "").strip()
        if raw_text:
            doc_id = str(doc.get("id") or uuid4())
            fallback_txt = self.upload_dir / f"{doc_id}_recovered.txt"
            fallback_txt.write_text(raw_text, encoding="utf-8")
            return str(fallback_txt), True

        return "", False

    def process_document(self, document_id: str, actor_id: str, role: str) -> dict[str, Any]:
        doc = self.repo.get_document(document_id)
        if not doc:
            raise ValueError(f"Document not found: {document_id}")

        file_path, recovered = self._resolve_document_file_path(doc)
        if not file_path:
            raise ValueError(
                "Document has no file path. Re-upload this document from Intake so the file URI can be stored."
            )

        patch_updates: dict[str, Any] = {}
        if recovered:
            patch_updates["file_path"] = file_path
            metadata = dict(doc.get("metadata") or {})
            ingestion = dict(metadata.get("ingestion") or {})
            ingestion.setdefault("original_file_uri", file_path)
            metadata["ingestion"] = ingestion
            patch_updates["metadata"] = metadata
            self.repo.update_document(document_id, patch_updates)
            doc["file_path"] = file_path
            doc["metadata"] = metadata

        suffix = Path(file_path).suffix.lower()
        preprocess_out = {"output_path": file_path, "steps": ["skipped"], "quality_score": 0.5}
        processed_path = file_path
        if suffix in {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".webp"}:
            processed_path = str(self.processed_dir / f"{Path(file_path).stem}_processed{suffix}")
            preprocess_out = preprocess_image(file_path, processed_path)
            processed_path = str(preprocess_out.get("output_path") or file_path)

        metadata = dict(doc.get("metadata") or {})
        script_hint = str(metadata.get("script_hint") or "AUTO-DETECT")
        hint_type = str(metadata.get("doc_type_hint") or "AUTO-DETECT")

        ocr_result = self.ocr.extract_text(processed_path, hint_script=script_hint)

        merged_text = "\n".join(
            part.strip() for part in [str(doc.get("raw_text") or ""), ocr_result.text] if part and part.strip()
        ).strip()

        classification = classify_document(
            text=merged_text,
            file_name=str(doc.get("file_name") or ""),
            image_path=processed_path,
            words=ocr_result.words,
            bbox=ocr_result.bbox,
        )
        if hint_type and hint_type != "AUTO-DETECT":
            classification["doc_type"] = hint_type
            classification["confidence"] = max(float(classification.get("confidence") or 0.0), 0.7)

        extraction = extract_fields(str(classification.get("doc_type") or "OTHER"), merged_text)
        validation = validate_fields(str(classification.get("doc_type") or "OTHER"), extraction.get("fields", []))
        fraud = fraud_signals(merged_text, float(classification.get("confidence") or 0.0), validation)

        conf = overall_confidence(
            ocr_confidence=float(ocr_result.confidence or 0.0),
            classification_confidence=float(classification.get("confidence") or 0.0),
            validation_output=validation,
            fraud_output=fraud,
        )

        decision = None
        state = "WAITING_FOR_REVIEW"
        if validation.get("overall_status") == "PASS" and conf >= 0.87 and float(fraud.get("aggregate_fraud_risk_score") or 1.0) < 0.25:
            decision = "APPROVE"
            state = "APPROVED"

        updated = self.repo.update_document(
            document_id,
            {
                "ocr_text": merged_text,
                "ocr_confidence": float(ocr_result.confidence or 0.0),
                "ocr_engine": ocr_result.engine,
                "preprocess_output": preprocess_out,
                "classification_output": classification,
                "extraction_output": extraction,
                "validation_output": validation,
                "fraud_output": fraud,
                "confidence": conf,
                "risk_score": float(fraud.get("aggregate_fraud_risk_score") or 0.0),
                "decision": decision,
                "state": state,
                "processed_at": self._utc_now(),
                "last_actor": actor_id,
                "last_actor_role": role,
            },
        )

        self.log_event(
            document_id=document_id,
            actor_id=actor_id,
            actor_role=role,
            event_type="document.processed",
            payload={
                "state": state,
                "decision": decision,
                "doc_type": classification.get("doc_type"),
                "confidence": conf,
                "risk_score": updated.get("risk_score"),
            },
            tenant_id=str(doc.get("tenant_id") or self.default_tenant_id),
        )
        return updated

    def update_extracted_fields(
        self,
        document_id: str,
        actor_id: str,
        role: str,
        fields: list[dict[str, Any]],
        reason: str = "manual_correction",
    ) -> dict[str, Any]:
        doc = self.repo.get_document(document_id)
        if not doc:
            raise ValueError(f"Document not found: {document_id}")

        extraction = {"fields": fields}
        doc_type = str((doc.get("classification_output") or {}).get("doc_type") or "OTHER")
        validation = validate_fields(doc_type, fields)
        fraud = fraud_signals(str(doc.get("ocr_text") or ""), float((doc.get("classification_output") or {}).get("confidence") or 0.0), validation)
        conf = overall_confidence(
            ocr_confidence=float(doc.get("ocr_confidence") or 0.0),
            classification_confidence=float((doc.get("classification_output") or {}).get("confidence") or 0.0),
            validation_output=validation,
            fraud_output=fraud,
        )

        updated = self.repo.update_document(
            document_id,
            {
                "extraction_output": extraction,
                "validation_output": validation,
                "fraud_output": fraud,
                "confidence": conf,
                "risk_score": float(fraud.get("aggregate_fraud_risk_score") or 0.0),
                "state": "REVIEW_IN_PROGRESS",
                "last_actor": actor_id,
                "last_actor_role": role,
            },
        )
        self.log_event(
            document_id=document_id,
            actor_id=actor_id,
            actor_role=role,
            event_type="document.fields_corrected",
            payload={"reason": reason, "fields": fields},
            tenant_id=str(doc.get("tenant_id") or self.default_tenant_id),
        )
        return updated

    def decide_document(
        self,
        document_id: str,
        actor_id: str,
        role: str,
        decision: str,
        notes: str | None = None,
    ) -> dict[str, Any]:
        dec = decision.strip().upper()
        if dec not in {"APPROVE", "REJECT"}:
            raise ValueError("Decision must be APPROVE or REJECT")

        state = "APPROVED" if dec == "APPROVE" else "REJECTED"
        row = self.repo.update_document(
            document_id,
            {
                "decision": dec,
                "state": state,
                "reviewed_at": self._utc_now(),
                "review_notes": notes,
                "last_actor": actor_id,
                "last_actor_role": role,
            },
        )

        self.repo.create_review(
            {
                "tenant_id": str(row.get("tenant_id") or self.default_tenant_id),
                "document_id": document_id,
                "actor_id": actor_id,
                "actor_role": role,
                "decision": dec,
                "notes": notes,
                "payload": {
                    "confidence": row.get("confidence"),
                    "risk_score": row.get("risk_score"),
                    "doc_type": (row.get("classification_output") or {}).get("doc_type"),
                },
            }
        )

        self.log_event(
            document_id=document_id,
            actor_id=actor_id,
            actor_role=role,
            event_type="document.review_decision",
            payload={"decision": dec, "state": state, "notes": notes},
            tenant_id=str(row.get("tenant_id") or self.default_tenant_id),
        )
        return row

    def log_event(
        self,
        *,
        document_id: str,
        actor_id: str,
        actor_role: str,
        event_type: str,
        payload: dict[str, Any],
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        row: dict[str, Any] = {
            "document_id": document_id,
            "actor_id": actor_id,
            "actor_role": actor_role,
            "event_type": event_type,
            "payload": payload,
        }
        if tenant_id:
            row["tenant_id"] = tenant_id
        return self.repo.create_audit_event(row)

    def list_documents(self, limit: int = 500) -> list[dict[str, Any]]:
        return self.repo.list_documents(limit=limit)

    def get_document(self, document_id: str) -> dict[str, Any] | None:
        return self.repo.get_document(document_id)

    def list_reviews(self, document_id: str | None = None) -> list[dict[str, Any]]:
        return self.repo.list_reviews(document_id=document_id)

    def list_audit_events(self, document_id: str | None = None, limit: int = 1000) -> list[dict[str, Any]]:
        return self.repo.list_audit_events(document_id=document_id, limit=limit)

    def export_document_json(self, document_id: str) -> str:
        doc = self.repo.get_document(document_id)
        if not doc:
            raise ValueError("Document not found")
        return json.dumps(doc, indent=2, ensure_ascii=False)
