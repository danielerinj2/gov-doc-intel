from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import requests

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

    def _llm_field_keys_for_doc_type(self, doc_type: str) -> list[str]:
        dt = str(doc_type or "OTHER").upper()
        mapping = {
            "AADHAAR_CARD": ["name", "dob", "gender", "aadhaar_number", "address"],
            "PAN_CARD": ["name", "father_name", "dob", "pan_number"],
            "INCOME_CERTIFICATE": ["name", "certificate_number", "annual_income", "issuing_authority", "issue_date"],
            "CASTE_CERTIFICATE": ["name", "caste", "certificate_number", "issuing_authority"],
            "DOMICILE_CERTIFICATE": ["name", "address", "certificate_number"],
            "LAND_RECORD": ["owner_name", "survey_number", "village"],
            "BIRTH_CERTIFICATE": ["name", "dob", "registration_number"],
            "DEATH_CERTIFICATE": ["name", "date_of_death", "registration_number"],
            "RATION_CARD": ["head_name", "ration_card_number", "address"],
            "MARRIAGE_CERTIFICATE": ["spouse_1_name", "spouse_2_name", "marriage_date"],
            "BONAFIDE_CERTIFICATE": ["student_name", "institution", "certificate_number"],
            "DISABILITY_CERTIFICATE": ["name", "disability_type", "disability_percent"],
            "BANK_PASSBOOK": ["account_holder_name", "account_number", "ifsc_code", "bank_name"],
        }
        return mapping.get(dt, ["name", "reference_number", "dob", "address"])

    def _extract_fields_with_groq(self, doc_type: str, text: str) -> list[dict[str, Any]]:
        if not settings.groq_api_key.strip():
            return []
        content = str(text or "").strip()
        if not content:
            return []

        keys = self._llm_field_keys_for_doc_type(doc_type)
        prompt = (
            "Extract fields from OCR text for Indian government documents.\n"
            f"Document type: {doc_type}\n"
            f"Target field keys: {keys}\n"
            "Return ONLY compact JSON object with these keys. "
            "Unknown values must be empty string. Preserve value exactly."
        )

        body = {
            "model": settings.groq_model,
            "temperature": 0.0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": "You are an information extraction engine. Output valid JSON only."},
                {"role": "user", "content": f"{prompt}\n\nOCR_TEXT:\n{content[:16000]}"},
            ],
        }

        try:
            res = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.groq_api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=20,
            )
            if res.status_code >= 400:
                return []
            payload = res.json() or {}
            raw = (
                (((payload.get("choices") or [{}])[0].get("message") or {}).get("content"))
                or "{}"
            )
            parsed = json.loads(str(raw))
            if not isinstance(parsed, dict):
                return []
            out: list[dict[str, Any]] = []
            for k in keys:
                v = str(parsed.get(k, "") or "").strip()
                if not v:
                    continue
                out.append(
                    {
                        "field_name": k,
                        "normalized_value": v,
                        "confidence": 0.78,
                        "source": "LLM_ASSISTED",
                    }
                )
            return out
        except Exception:
            return []

    @staticmethod
    def _extract_json_from_text(raw: str) -> dict[str, Any] | None:
        txt = str(raw or "").strip()
        if not txt:
            return None
        # Handle fenced JSON output.
        if txt.startswith("```"):
            txt = txt.strip("`").strip()
            if txt.lower().startswith("json"):
                txt = txt[4:].strip()
        try:
            parsed = json.loads(txt)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
        # Fallback: take the first JSON object span.
        start = txt.find("{")
        end = txt.rfind("}")
        if start >= 0 and end > start:
            try:
                parsed = json.loads(txt[start : end + 1])
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                return None
        return None

    def _extract_fields_with_claude(self, doc_type: str, text: str) -> list[dict[str, Any]]:
        if not settings.anthropic_api_key.strip():
            return []
        content = str(text or "").strip()
        if not content:
            return []

        keys = self._llm_field_keys_for_doc_type(doc_type)
        payload_schema = {k: {"value": None, "confidence": 0} for k in keys}
        prompt = (
            "You are a document field extractor for Indian government documents.\n"
            f"Document type: {doc_type}\n"
            "Extract fields from the OCR text and return ONLY valid JSON (no markdown).\n"
            f"Output schema: {json.dumps(payload_schema, ensure_ascii=False)}\n"
            "Rules:\n"
            "- confidence is 0-100\n"
            "- Use null for not found values\n"
            "- Never guess\n"
            "- Normalize dates to DD/MM/YYYY when possible\n"
            "- Keep identifiers exactly as present except trimming spaces\n"
            "- S/O, Son of, Putra map to father_name where applicable\n\n"
            f"OCR text:\n{content[:18000]}"
        )

        body = {
            "model": settings.model_name,
            "max_tokens": 1200,
            "temperature": 0,
            "messages": [{"role": "user", "content": prompt}],
        }
        try:
            res = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": settings.anthropic_api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json=body,
                timeout=25,
            )
            if res.status_code >= 400:
                return []
            out = res.json() or {}
            chunks = out.get("content") or []
            text_parts = [
                str(ch.get("text") or "")
                for ch in chunks
                if isinstance(ch, dict) and str(ch.get("type") or "") == "text"
            ]
            parsed = self._extract_json_from_text("\n".join(text_parts))
            if not parsed:
                return []

            alias_map: dict[str, str] = {
                "date_of_birth": "dob",
                "birth_date": "dob",
                "aadhaar": "aadhaar_number",
                "uid": "aadhaar_number",
                "pan": "pan_number",
            }
            rows: list[dict[str, Any]] = []
            for k in keys:
                raw_val = parsed.get(k)
                if raw_val is None:
                    for ak, canon in alias_map.items():
                        if canon == k and ak in parsed:
                            raw_val = parsed.get(ak)
                            break
                confidence = 0.0
                value = ""
                if isinstance(raw_val, dict):
                    value = str(raw_val.get("value") or "").strip()
                    try:
                        confidence = float(raw_val.get("confidence") or 0.0)
                    except Exception:
                        confidence = 0.0
                else:
                    value = str(raw_val or "").strip()
                if not value:
                    continue
                if confidence > 1:
                    confidence = confidence / 100.0
                confidence = max(0.0, min(1.0, confidence if confidence > 0 else 0.78))
                rows.append(
                    {
                        "field_name": k,
                        "normalized_value": value,
                        "confidence": round(confidence, 3),
                        "source": "LLM_ASSISTED_CLAUDE",
                    }
                )
            return rows
        except Exception:
            return []

    def _extract_fields_with_llm(self, doc_type: str, text: str) -> tuple[list[dict[str, Any]], str]:
        # If Claude is configured, use Claude as the LLM extraction backend.
        if settings.anthropic_api_key.strip():
            claude_fields = self._extract_fields_with_claude(doc_type, text)
            if claude_fields:
                return claude_fields, "claude"
            return [], "claude"

        # Legacy fallback path when Claude is not configured.
        groq_fields = self._extract_fields_with_groq(doc_type, text)
        if groq_fields:
            return groq_fields, "groq"
        return [], ""

    def _merge_extracted_fields(
        self,
        base_fields: list[dict[str, Any]],
        llm_fields: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {}
        for item in base_fields:
            key = str(item.get("field_name") or "").strip().lower()
            if key:
                merged[key] = dict(item)
        for item in llm_fields:
            key = str(item.get("field_name") or "").strip().lower()
            if not key:
                continue
            existing = merged.get(key)
            if not existing:
                merged[key] = dict(item)
                continue
            existing_val = str(existing.get("normalized_value") or "").strip()
            existing_conf = float(existing.get("confidence") or 0.0)
            incoming_val = str(item.get("normalized_value") or "").strip()
            incoming_conf = float(item.get("confidence") or 0.0)
            incoming_src = str(item.get("source") or "").upper()

            # Claude-assisted extraction takes precedence for segregation/population.
            if incoming_val and "CLAUDE" in incoming_src:
                merged[key] = dict(item)
                continue
            if (not existing_val and incoming_val) or (incoming_val and incoming_conf > existing_conf + 0.15):
                merged[key] = dict(item)
        return list(merged.values())

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
        llm_fields, llm_provider = self._extract_fields_with_llm(
            str(classification.get("doc_type") or "OTHER"),
            merged_text,
        )
        if llm_fields:
            extraction = {"fields": self._merge_extracted_fields(list(extraction.get("fields") or []), llm_fields)}
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

        metadata = dict(doc.get("metadata") or {})
        metadata["ocr_tokens"] = [
            {"text": str(w), "bbox": b, "confidence": c}
            for w, b, c in zip(
                list(ocr_result.words or [])[:600],
                list(ocr_result.bbox or [])[:600],
                list(ocr_result.line_confidence or [])[:600],
            )
        ]
        metadata["llm_extraction_used"] = bool(llm_fields)
        metadata["llm_extraction_provider"] = llm_provider or None
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
                "metadata": metadata,
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
                "llm_extraction_used": bool(llm_fields),
                "llm_provider": llm_provider or None,
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

    def save_form_population(
        self,
        *,
        document_id: str,
        actor_id: str,
        role: str,
        document_type: str,
        populated_rows: list[dict[str, Any]],
    ) -> dict[str, Any]:
        doc = self.repo.get_document(document_id)
        if not doc:
            raise ValueError(f"Document not found: {document_id}")

        doc_type = str(document_type or "OTHER").strip().upper()
        classification = dict(doc.get("classification_output") or {})
        classification["doc_type"] = doc_type
        classification["confidence"] = max(float(classification.get("confidence") or 0.0), 0.7)

        extracted_fields: list[dict[str, Any]] = []
        for row in populated_rows:
            field_id = str(row.get("field_id") or "").strip()
            value = str(row.get("value") or "").strip()
            if not field_id:
                continue
            if not value:
                continue
            extracted_fields.append(
                {
                    "field_name": field_id,
                    "normalized_value": value,
                    "confidence": float(row.get("confidence") or 0.0),
                    "source": str(row.get("source") or ""),
                    "validation_state": str(row.get("validation_state") or ""),
                    "locked": bool(row.get("locked", False)),
                }
            )

        validation = validate_fields(doc_type, extracted_fields)
        fraud = fraud_signals(
            str(doc.get("ocr_text") or ""),
            float(classification.get("confidence") or 0.0),
            validation,
        )
        conf = overall_confidence(
            ocr_confidence=float(doc.get("ocr_confidence") or 0.0),
            classification_confidence=float(classification.get("confidence") or 0.0),
            validation_output=validation,
            fraud_output=fraud,
        )

        metadata = dict(doc.get("metadata") or {})
        metadata["form_population"] = {
            "document_type": doc_type,
            "updated_at": self._utc_now(),
            "updated_by": actor_id,
            "rows": populated_rows,
        }

        updated = self.repo.update_document(
            document_id,
            {
                "classification_output": classification,
                "extraction_output": {"fields": extracted_fields},
                "validation_output": validation,
                "fraud_output": fraud,
                "confidence": conf,
                "risk_score": float(fraud.get("aggregate_fraud_risk_score") or 0.0),
                "metadata": metadata,
                "state": "REVIEW_IN_PROGRESS",
                "last_actor": actor_id,
                "last_actor_role": role,
            },
        )

        self.log_event(
            document_id=document_id,
            actor_id=actor_id,
            actor_role=role,
            event_type="document.form_population_saved",
            payload={
                "document_type": doc_type,
                "row_count": len(populated_rows),
                "filled_count": len([r for r in populated_rows if str(r.get("value") or "").strip()]),
            },
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
