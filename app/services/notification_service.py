from __future__ import annotations

from typing import Any

from app.contracts.payloads import NotificationContract
from app.infra.repositories import Repository


class NotificationService:
    def __init__(self, repo: Repository) -> None:
        self.repo = repo

    def handle_event(self, envelope: dict[str, Any]) -> dict[str, Any] | None:
        event_type = envelope["event_type"]
        tenant_id = envelope["tenant_id"]
        document_id = envelope["document_id"]

        triggerable = {
            "document.received",
            "document.flagged.for_review",
            "review.started",
            "document.approved",
            "document.rejected",
            "document.disputed",
            "review.completed",
            "offline.conflict.detected",
        }
        if event_type not in triggerable:
            return None

        doc = self.repo.get_document(document_id, tenant_id=tenant_id)
        if not doc:
            return None

        policy = self.repo.get_tenant_policy(tenant_id)
        channels: list[str] = []
        if bool(policy.get("sms_enabled", True)):
            channels.append("SMS")
        if bool(policy.get("email_enabled", True)):
            channels.append("EMAIL")
        if bool(policy.get("portal_enabled", True)):
            channels.append("PORTAL")
        if bool(policy.get("whatsapp_enabled", False)):
            channels.append("WHATSAPP")

        message = self._build_message(event_type, doc, envelope)
        contract = NotificationContract(
            tenant_id=tenant_id,
            document_id=document_id,
            citizen_id=doc.get("citizen_id", "unknown"),
            event_type=event_type,
            channels=channels,
            message=message,
        )

        for channel in channels:
            self.repo.create_notification(
                tenant_id=contract.tenant_id,
                document_id=contract.document_id,
                citizen_id=contract.citizen_id,
                channel=channel,
                event_type=contract.event_type,
                message=contract.message,
                metadata={
                    "actor_type": envelope.get("actor_type"),
                    "actor_id": envelope.get("actor_id"),
                },
            )

        return {
            "citizen_id": contract.citizen_id,
            "channels": channels,
            "message": contract.message,
        }

    def _build_message(self, event_type: str, doc: dict[str, Any], envelope: dict[str, Any]) -> str:
        decision = doc.get("decision") or "PENDING"
        reasons = (envelope.get("payload") or {}).get("reason_codes", [])

        if event_type == "document.received":
            return "Document received and queued for processing."
        if event_type == "document.flagged.for_review":
            return "Document requires officer review."
        if event_type == "review.started":
            return "Your document is now under officer review."
        if event_type == "document.approved":
            return "Your document has been approved."
        if event_type == "document.rejected":
            reason_text = ", ".join(reasons) if reasons else "Further verification failed"
            return f"Your document was rejected. Reason: {reason_text}."
        if event_type == "document.disputed":
            return "Your dispute has been accepted and moved to senior review."
        if event_type == "review.completed":
            return f"Review completed. Final decision: {decision}."
        if event_type == "offline.conflict.detected":
            return "Provisional offline result has been revised after centralized verification. Please re-upload or visit a center."

        return f"Document status changed: {event_type}."
