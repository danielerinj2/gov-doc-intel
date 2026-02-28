from __future__ import annotations

import csv
import io
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from app.domain.models import Dispute, Document, DocumentEvent, Officer
from app.domain.state_machine import StateMachine
from app.domain.states import DocumentState
from app.infra.groq_adapter import GroqAdapter
from app.infra.repositories import (
    ADMIN_ROLES,
    REVIEW_ROLES,
    WRITER_ROLES,
    Repository,
)
from app.pipeline.dag import DAG, Node
from app.pipeline.nodes import PipelineNodes


class DocumentService:
    _rate_windows: dict[tuple[str, str], list[float]] = defaultdict(list)

    def __init__(self) -> None:
        self.repo = Repository()
        self.sm = StateMachine()
        self.nodes = PipelineNodes(GroqAdapter())
        self.dag = DAG(
            [
                Node("preprocessing_hashing", self.nodes.preprocessing_hashing, []),
                Node("ocr_multi_script", self.nodes.ocr_multi_script, ["preprocessing_hashing"]),
                Node("dedup_cross_submission", self.nodes.dedup_cross_submission, ["preprocessing_hashing"]),
                Node("classification", self.nodes.classification, ["ocr_multi_script"]),
                Node("stamps_seals", self.nodes.stamps_seals, ["ocr_multi_script"]),
                Node("tamper_forensics", self.nodes.tamper_forensics, ["ocr_multi_script"]),
                Node("template_map", self.nodes.template_map, ["classification"]),
                Node("image_features", self.nodes.image_features, ["tamper_forensics", "preprocessing_hashing"]),
                Node("field_extract", self.nodes.field_extract, ["template_map", "ocr_multi_script", "classification"]),
                Node("fraud_behavioral_engine", self.nodes.fraud_behavioral_engine, ["dedup_cross_submission", "tamper_forensics", "image_features"]),
                Node("issuer_registry_verification", self.nodes.issuer_registry_verification, ["field_extract", "classification"]),
                Node("validation", self.nodes.validation, ["field_extract", "issuer_registry_verification"]),
                Node("merge_node", self.nodes.merge_node, ["validation", "fraud_behavioral_engine", "issuer_registry_verification", "stamps_seals", "tamper_forensics"]),
                Node("decision_explainability", self.nodes.decision_explainability, ["merge_node"]),
                Node("output_notification", self.nodes.output_notification, ["decision_explainability"]),
            ]
        )

    def register_officer(self, officer_id: str, tenant_id: str, role: str) -> dict[str, Any]:
        officer = Officer(officer_id=officer_id, tenant_id=tenant_id, role=role)
        row = self.repo.upsert_officer(officer)
        self.repo.get_tenant_policy(tenant_id, create_if_missing=True)
        return row

    def create_document(
        self,
        tenant_id: str,
        citizen_id: str,
        file_name: str,
        raw_text: str,
        officer_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._authorize(officer_id, tenant_id, WRITER_ROLES)
        self._enforce_daily_quota(tenant_id)

        policy = self.repo.get_tenant_policy(tenant_id)
        retention_days = int(policy.get("data_retention_days", 365))
        expires_at = (datetime.now(timezone.utc) + timedelta(days=retention_days)).isoformat()
        bucket_name = self.repo.get_tenant_bucket(tenant_id)

        m = metadata or {}
        m.setdefault("tenant_storage_bucket", bucket_name)

        doc = Document(
            tenant_id=tenant_id,
            citizen_id=citizen_id,
            file_name=file_name,
            raw_text=raw_text,
            metadata=m,
            expires_at=expires_at,
        )
        row = self.repo.create_document(doc)
        self._event(
            doc.id,
            tenant_id,
            officer_id,
            "document.received",
            {
                "file_name": file_name,
                "retention_days": retention_days,
                "expires_at": expires_at,
                "tenant_storage_bucket": bucket_name,
            },
        )
        return row

    def process_document(self, document_id: str, tenant_id: str, officer_id: str) -> dict[str, Any]:
        self._authorize(officer_id, tenant_id, WRITER_ROLES)
        doc = self.repo.get_document(document_id, tenant_id=tenant_id)
        if not doc:
            raise ValueError("Document not found for tenant")

        policy = self.repo.get_tenant_policy(tenant_id)

        # State progression before merge decision.
        self._transition(doc, DocumentState.PREPROCESSED, officer_id)
        self._transition(doc, DocumentState.OCR_DONE, officer_id)
        self._transition(doc, DocumentState.CLASSIFIED, officer_id)
        self._transition(doc, DocumentState.EXTRACTED, officer_id)
        self._transition(doc, DocumentState.VALIDATED, officer_id)

        ctx = self.dag.run(
            {
                "raw_text": doc.get("raw_text", ""),
                "tenant_id": tenant_id,
                "document_id": doc["id"],
                "repo": self.repo,
                "tenant_policy": policy,
            }
        )

        dedup_hash = ctx["dedup_cross_submission"]["dedup_hash"]
        decision = ctx["output_notification"]["final_decision"]
        confidence = ctx["decision_explainability"]["confidence"]
        risk = ctx["decision_explainability"]["risk_score"]
        template_id = ctx["template_map"]["template_id"]

        if decision in {"APPROVE", "REJECT"}:
            self._transition(doc, DocumentState.VERIFIED, officer_id)
            self._transition(doc, DocumentState.APPROVED if decision == "APPROVE" else DocumentState.REJECTED, officer_id)
        else:
            self._transition(doc, DocumentState.REVIEW_REQUIRED, officer_id)

        updated = self.repo.update_document(
            doc["id"],
            dedup_hash=dedup_hash,
            confidence=confidence,
            risk_score=risk,
            decision=decision,
            template_id=template_id,
            derived=ctx["node_outputs"],
        )
        if not updated:
            raise RuntimeError("Document update failed")

        self._event(
            doc["id"],
            tenant_id,
            officer_id,
            "decision.finalized",
            {
                "decision": decision,
                "confidence": confidence,
                "risk_score": risk,
                "template_id": template_id,
                "execution_order": ctx["execution_order"],
            },
        )
        return updated

    def notify(self, document_id: str, tenant_id: str, officer_id: str) -> dict[str, Any] | None:
        self._authorize(officer_id, tenant_id, WRITER_ROLES)
        doc = self.repo.get_document(document_id, tenant_id=tenant_id)
        if not doc:
            return None

        state = DocumentState(doc["state"])
        if state not in {DocumentState.APPROVED, DocumentState.REJECTED}:
            return doc

        self._transition(doc, DocumentState.NOTIFIED, officer_id)
        self._event(doc["id"], tenant_id, officer_id, "notification.sent", {"channel": "PORTAL"})
        return self.repo.get_document(document_id, tenant_id=tenant_id)

    def open_dispute(self, document_id: str, reason: str, evidence_note: str, tenant_id: str, officer_id: str) -> dict[str, Any]:
        self._authorize(officer_id, tenant_id, WRITER_ROLES)
        doc = self.repo.get_document(document_id, tenant_id=tenant_id)
        if not doc:
            raise ValueError("Document not found for tenant")

        state = DocumentState(doc["state"])
        if state == DocumentState.REJECTED:
            self._transition(doc, DocumentState.DISPUTED, officer_id)
        elif state != DocumentState.NOTIFIED:
            raise ValueError("Dispute allowed after rejection/notification only")

        dispute = Dispute(
            document_id=doc["id"],
            tenant_id=tenant_id,
            reason=reason,
            evidence_note=evidence_note,
        )
        row = self.repo.create_dispute(dispute)
        self._event(doc["id"], tenant_id, officer_id, "dispute.opened", {"reason": reason})
        return row

    def manual_decision(self, document_id: str, decision: str, tenant_id: str, officer_id: str) -> dict[str, Any]:
        self._authorize(officer_id, tenant_id, REVIEW_ROLES)
        doc = self.repo.get_document(document_id, tenant_id=tenant_id)
        if not doc:
            raise ValueError("Document not found for tenant")

        if DocumentState(doc["state"]) != DocumentState.REVIEW_REQUIRED:
            raise ValueError("Manual decision is only allowed from REVIEW_REQUIRED")

        if decision == "APPROVE":
            self._transition(doc, DocumentState.APPROVED, officer_id)
        elif decision == "REJECT":
            self._transition(doc, DocumentState.REJECTED, officer_id)
        else:
            raise ValueError("Unsupported decision")

        updated = self.repo.update_document(doc["id"], decision=decision)
        if not updated:
            raise RuntimeError("Update failed")

        self._event(doc["id"], tenant_id, officer_id, "manual.review.completed", {"decision": decision})
        return updated

    def list_documents(self, tenant_id: str, officer_id: str) -> list[dict[str, Any]]:
        self._authorize(officer_id, tenant_id, None)
        return self.repo.list_documents(tenant_id)

    def list_events(self, document_id: str, tenant_id: str, officer_id: str) -> list[dict[str, Any]]:
        self._authorize(officer_id, tenant_id, None)
        return self.repo.list_events(document_id, tenant_id=tenant_id)

    def list_tenant_events(self, tenant_id: str, officer_id: str) -> list[dict[str, Any]]:
        self._authorize(officer_id, tenant_id, None)
        return self.repo.list_events_by_tenant(tenant_id)

    def list_disputes(self, tenant_id: str, officer_id: str) -> list[dict[str, Any]]:
        self._authorize(officer_id, tenant_id, None)
        return self.repo.list_disputes(tenant_id)

    def batch_export_documents(self, tenant_id: str, officer_id: str, include_raw_text: bool = False) -> str:
        self._authorize(officer_id, tenant_id, REVIEW_ROLES)
        policy = self.repo.get_tenant_policy(tenant_id)
        if not bool(policy.get("export_enabled", True)):
            raise PermissionError("Batch export is disabled for this tenant")

        rows = self.repo.export_documents_for_tenant(tenant_id, include_raw_text=include_raw_text)
        if not rows:
            return ""

        # Tenant-only export: filtered at repository by tenant_id.
        columns = [
            "id",
            "tenant_id",
            "citizen_id",
            "file_name",
            "state",
            "decision",
            "confidence",
            "risk_score",
            "template_id",
            "created_at",
            "updated_at",
            "expires_at",
        ]
        if include_raw_text:
            columns.append("raw_text")

        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k) for k in columns})
        return buffer.getvalue()

    def _authorize(self, officer_id: str, tenant_id: str, allowed_roles: set[str] | None) -> dict[str, Any]:
        row = self.repo.assert_officer_access(officer_id, tenant_id, allowed_roles)
        self._enforce_rate_limit(officer_id, tenant_id)
        return row

    def _enforce_rate_limit(self, officer_id: str, tenant_id: str) -> None:
        policy = self.repo.get_tenant_policy(tenant_id)
        limit = int(policy.get("api_rate_limit_per_minute", 120))
        if limit <= 0:
            return

        now = time.time()
        key = (tenant_id, officer_id)
        bucket = DocumentService._rate_windows[key]
        cutoff = now - 60
        bucket[:] = [ts for ts in bucket if ts >= cutoff]

        if len(bucket) >= limit:
            raise PermissionError("Tenant API rate limit exceeded")

        bucket.append(now)

    def _enforce_daily_quota(self, tenant_id: str) -> None:
        policy = self.repo.get_tenant_policy(tenant_id)
        max_per_day = int(policy.get("max_documents_per_day", 25000))
        if max_per_day <= 0:
            return

        current = self.repo.count_documents_created_today(tenant_id)
        if current >= max_per_day:
            raise PermissionError("Tenant daily document quota exceeded")

    def _transition(self, doc: dict[str, Any], target: DocumentState, officer_id: str) -> None:
        current = DocumentState(doc["state"])
        nxt = self.sm.transition(current, target)
        updated = self.repo.update_document(doc["id"], state=nxt)
        if not updated:
            raise RuntimeError("State update failed")

        doc.update(updated)
        self._event(doc["id"], doc["tenant_id"], officer_id, "document.state.changed", {"from": current.value, "to": target.value})

    def _event(
        self,
        document_id: str,
        tenant_id: str,
        officer_id: str | None,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        event = DocumentEvent(
            document_id=document_id,
            tenant_id=tenant_id,
            officer_id=officer_id,
            event_type=event_type,
            payload=payload,
        )
        self.repo.add_event(event)
