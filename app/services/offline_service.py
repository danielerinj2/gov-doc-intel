from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.document_service import DocumentService


class OfflineService:
    """Offline conflict framework: local results are provisional, central is source of truth."""

    def __init__(self, document_service: DocumentService) -> None:
        self.document_service = document_service

    def create_offline_provisional(
        self,
        *,
        tenant_id: str,
        citizen_id: str,
        file_name: str,
        raw_text: str,
        officer_id: str,
        local_model_versions: dict[str, Any],
        provisional_decision: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = dict(metadata or {})
        payload["offline_processed"] = True
        payload["offline_timestamps"] = {
            "processed_at": datetime.now(timezone.utc).isoformat(),
        }
        payload["local_model_versions"] = local_model_versions
        payload["provisional_decision"] = provisional_decision
        payload["provisional_legal_standing"] = "NONE"

        doc = self.document_service.create_document(
            tenant_id=tenant_id,
            citizen_id=citizen_id,
            file_name=file_name,
            raw_text=raw_text,
            officer_id=officer_id,
            metadata=payload,
        )
        self.document_service.repo.update_document(
            doc["id"],
            offline_processed=True,
            offline_local_model_versions=local_model_versions,
            offline_processed_at=datetime.now(timezone.utc).isoformat(),
            provisional_decision=provisional_decision,
            offline_sync_status="PENDING",
        )
        return self.document_service.repo.get_document(doc["id"], tenant_id=tenant_id) or doc

    def sync_offline_document(self, *, tenant_id: str, document_id: str, officer_id: str) -> dict[str, Any]:
        doc = self.document_service.repo.get_document(document_id, tenant_id=tenant_id)
        if not doc:
            raise ValueError("Document not found for tenant")

        central = self.document_service.process_document(document_id, tenant_id, officer_id)
        local_provisional = doc.get("provisional_decision")
        central_decision = central.get("decision")

        self.document_service.repo.update_document(
            document_id,
            offline_synced_at=datetime.now(timezone.utc).isoformat(),
            offline_sync_status="SYNCED",
        )

        if local_provisional and local_provisional != central_decision:
            self.document_service.emit_custom_event(
                document_id=document_id,
                tenant_id=tenant_id,
                actor_type="SYSTEM",
                actor_id=None,
                event_type="offline.conflict.detected",
                payload={
                    "local_provisional": local_provisional,
                    "central_decision": central_decision,
                },
                reason="Central pipeline is source of truth",
            )

        return self.document_service.repo.get_document(document_id, tenant_id=tenant_id) or central

    def apply_sync_backpressure(
        self,
        *,
        tenant_id: str,
        officer_id: str,
        pending_document_ids: list[str],
        sync_capacity_per_minute: int,
    ) -> dict[str, Any]:
        backlog_size = len(pending_document_ids)
        if backlog_size <= sync_capacity_per_minute:
            return {"queue_overflow": False, "backlog_size": backlog_size}

        for doc_id in pending_document_ids:
            self.document_service.repo.update_document(doc_id, queue_overflow=True, offline_sync_status="QUEUE_OVERFLOW")

        self.document_service.emit_custom_event(
            document_id="OFFLINE_BACKLOG",
            tenant_id=tenant_id,
            actor_type="SYSTEM",
            actor_id=officer_id,
            event_type="offline.queue_overflow",
            payload={
                "backlog_size": backlog_size,
                "sync_capacity_per_minute": sync_capacity_per_minute,
            },
            reason="Offline backlog exceeded configured sync capacity",
        )
        return {"queue_overflow": True, "backlog_size": backlog_size}
