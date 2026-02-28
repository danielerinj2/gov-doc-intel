from __future__ import annotations

from typing import Any

CORE_EVENTS = {
    "document.received",
    "document.preprocessed",
    "ocr.completed",
    "branch.started",
    "document.merged",
    "document.flagged.for_review",
    "review.started",
    "review.completed",
    "document.approved",
    "document.rejected",
    "document.disputed",
    "document.archived",
    "document.failed",
    "offline.conflict.detected",
    "offline.queue_overflow",
    "notification.sent",
    "review.escalated",
}

BRANCH_MODULES = {
    "classification",
    "stamps_seals",
    "tamper_forensics",
    "fraud_behavioral_engine",
    "issuer_registry_verification",
}


def _branch_event(module_name: str) -> str:
    return f"branch.completed.{module_name}"


def is_valid_event_type(event_type: str) -> bool:
    if event_type in CORE_EVENTS:
        return True
    return any(event_type == _branch_event(mod) for mod in BRANCH_MODULES)


EVENT_REQUIRED_KEYS: dict[str, set[str]] = {
    "document.received": {"file_name"},
    "document.preprocessed": {"quality_score", "dedup_hash"},
    "ocr.completed": {"ocr_confidence"},
    "branch.started": {"modules"},
    "document.merged": {"confidence", "risk_score"},
    "document.flagged.for_review": {"reason_codes"},
    "review.started": {"review_level"},
    "review.completed": {"decision"},
    "document.approved": {"decision"},
    "document.rejected": {"decision", "reason_codes"},
    "document.disputed": {"reason"},
    "document.archived": {"archive_reason"},
    "document.failed": {"error"},
    "offline.conflict.detected": {"local_provisional", "central_decision"},
    "offline.queue_overflow": {"backlog_size", "sync_capacity_per_minute"},
    "notification.sent": {"channels", "message"},
    "review.escalated": {"escalation_level", "assignee_role"},
}

for module_name in BRANCH_MODULES:
    EVENT_REQUIRED_KEYS[_branch_event(module_name)] = {"module", "status"}


def validate_event_payload(event_type: str, payload: dict[str, Any]) -> None:
    if not is_valid_event_type(event_type):
        raise ValueError(f"Unsupported event type: {event_type}")

    required = EVENT_REQUIRED_KEYS.get(event_type)
    if required is None and event_type.startswith("branch.completed."):
        required = {"module", "status"}

    if not required:
        return

    missing = [k for k in required if k not in payload]
    if missing:
        raise ValueError(f"Event payload missing required keys for {event_type}: {missing}")


def build_event_envelope(
    *,
    event_type: str,
    tenant_id: str,
    document_id: str,
    actor_type: str,
    actor_id: str | None,
    payload: dict[str, Any],
    reason: str | None,
    policy_version: int | None,
    model_versions: dict[str, Any] | None,
    correlation_id: str | None,
    causation_id: str | None,
) -> dict[str, Any]:
    validate_event_payload(event_type, payload)
    return {
        "event_type": event_type,
        "tenant_id": tenant_id,
        "document_id": document_id,
        "actor_type": actor_type,
        "actor_id": actor_id,
        "payload": payload,
        "reason": reason,
        "policy_version": policy_version,
        "model_versions": model_versions or {},
        "correlation_id": correlation_id,
        "causation_id": causation_id,
    }
