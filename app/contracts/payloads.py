from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class IngestionContract(BaseModel):
    tenant_id: str = Field(min_length=2)
    citizen_id: str = Field(min_length=2)
    file_name: str = Field(min_length=1)
    raw_text: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DecisionContract(BaseModel):
    decision: str
    confidence: float
    risk_score: float
    reason_codes: list[str]


class NotificationContract(BaseModel):
    tenant_id: str
    document_id: str
    citizen_id: str
    event_type: str
    channels: list[str]
    message: str


class DisputeContract(BaseModel):
    tenant_id: str
    document_id: str
    reason: str = Field(min_length=3)
    evidence_note: str = Field(default="")
