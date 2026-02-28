from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.domain.states import DocumentState


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Document:
    tenant_id: str
    citizen_id: str
    file_name: str
    raw_text: str
    metadata: dict[str, Any]
    id: str = field(default_factory=lambda: str(uuid4()))
    state: DocumentState = DocumentState.RECEIVED
    dedup_hash: str | None = None
    confidence: float | None = None
    risk_score: float | None = None
    decision: str | None = None
    template_id: str | None = None
    expires_at: str | None = None
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)


@dataclass
class DocumentEvent:
    document_id: str
    tenant_id: str
    officer_id: str | None
    event_type: str
    payload: dict[str, Any]
    id: str = field(default_factory=lambda: str(uuid4()))
    created_at: str = field(default_factory=utc_now)


@dataclass
class Dispute:
    document_id: str
    tenant_id: str
    reason: str
    evidence_note: str
    status: str = "OPEN"
    id: str = field(default_factory=lambda: str(uuid4()))
    created_at: str = field(default_factory=utc_now)


@dataclass
class TenantPolicy:
    tenant_id: str
    data_retention_days: int = 365
    api_rate_limit_per_minute: int = 120
    max_documents_per_day: int = 25000
    cross_tenant_fraud_enabled: bool = False
    export_enabled: bool = True
    residency_region: str = "default"
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)


@dataclass
class Officer:
    officer_id: str
    tenant_id: str
    role: str
    status: str = "ACTIVE"
    created_at: str = field(default_factory=utc_now)
