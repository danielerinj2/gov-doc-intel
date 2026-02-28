from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.services.governance_service import GovernanceService
from app.services.document_service import DocumentService
from app.services.offline_service import OfflineService


app = FastAPI(title="Gov Document Intelligence API", version="1.0.0")
service = DocumentService()
governance_service = GovernanceService(service.repo)
offline_service = OfflineService(service)


class IngestRequest(BaseModel):
    citizen_id: str = Field(min_length=1)
    file_name: str = Field(min_length=1)
    raw_text: str = Field(default="")
    metadata: dict[str, Any] = Field(default_factory=dict)
    process_now: bool = False


class DecisionRequest(BaseModel):
    decision: str
    reason: str = "OFFICER_DECISION"


class DisputeRequest(BaseModel):
    reason: str
    evidence_note: str = ""


class OfflineSyncRequest(BaseModel):
    capacity_per_minute: int = 50
    fetch_limit: int = 500


class ApiKeyCreateRequest(BaseModel):
    key_label: str = Field(min_length=1)
    raw_key: str = Field(min_length=16)


def _ctx(
    x_tenant_id: str = Header(..., alias="X-Tenant-ID"),
    x_officer_id: str = Header(..., alias="X-Officer-ID"),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> dict[str, str | None]:
    tenant_id = x_tenant_id.strip()
    officer_id = x_officer_id.strip()

    if x_api_key:
        if not service.repo.validate_tenant_api_key(tenant_id, x_api_key.strip()):
            raise HTTPException(status_code=401, detail="Invalid tenant API key")
    return {"tenant_id": tenant_id, "officer_id": officer_id}


@app.exception_handler(ValueError)
async def value_error_handler(_request: Request, exc: ValueError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(PermissionError)
async def permission_error_handler(_request: Request, exc: PermissionError) -> JSONResponse:
    return JSONResponse(status_code=403, content={"detail": str(exc)})


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "persistence": "supabase" if service.repo.using_supabase else "memory",
        "schema_ready": {
            "part2": service.repo.part2_schema_ready,
            "part3": service.repo.part3_schema_ready,
            "part4": service.repo.part4_schema_ready,
            "part5": service.repo.part5_schema_ready,
        },
    }


@app.post("/documents")
def create_document(payload: IngestRequest, ctx: dict[str, str | None] = Depends(_ctx)) -> dict[str, Any]:
    tenant_id = str(ctx["tenant_id"])
    officer_id = str(ctx["officer_id"])

    row = service.create_document(
        tenant_id=tenant_id,
        citizen_id=payload.citizen_id,
        file_name=payload.file_name,
        raw_text=payload.raw_text,
        officer_id=officer_id,
        metadata=payload.metadata,
    )
    if payload.process_now:
        row = service.process_document(str(row["id"]), tenant_id, officer_id)
    return row


@app.post("/documents/{document_id}/process")
def process_document(document_id: str, ctx: dict[str, str | None] = Depends(_ctx)) -> dict[str, Any]:
    return service.process_document(document_id, str(ctx["tenant_id"]), str(ctx["officer_id"]))


@app.get("/documents/{document_id}/status")
def get_status(document_id: str, ctx: dict[str, str | None] = Depends(_ctx)) -> dict[str, Any]:
    row = service.repo.get_document(document_id, tenant_id=str(ctx["tenant_id"]))
    if not row:
        raise HTTPException(status_code=404, detail="Document not found")
    return {
        "document_id": document_id,
        "state": row.get("state"),
        "decision": row.get("decision"),
        "confidence": row.get("confidence"),
        "risk_score": row.get("risk_score"),
        "updated_at": row.get("updated_at"),
    }


@app.get("/documents/{document_id}/result")
def get_result(document_id: str, ctx: dict[str, str | None] = Depends(_ctx)) -> dict[str, Any]:
    record = service.repo.get_latest_document_record(str(ctx["tenant_id"]), document_id)
    if not record:
        raise HTTPException(status_code=404, detail="Result not found")
    return record


@app.get("/documents/{document_id}/events")
def get_events(document_id: str, ctx: dict[str, str | None] = Depends(_ctx)) -> list[dict[str, Any]]:
    return service.list_events(document_id, str(ctx["tenant_id"]), str(ctx["officer_id"]))


@app.post("/documents/{document_id}/review/start")
def start_review(document_id: str, ctx: dict[str, str | None] = Depends(_ctx)) -> dict[str, Any]:
    return service.start_review(document_id, str(ctx["tenant_id"]), str(ctx["officer_id"]))


@app.post("/documents/{document_id}/review/decision")
def review_decision(document_id: str, payload: DecisionRequest, ctx: dict[str, str | None] = Depends(_ctx)) -> dict[str, Any]:
    return service.manual_decision(
        document_id=document_id,
        decision=payload.decision,
        tenant_id=str(ctx["tenant_id"]),
        officer_id=str(ctx["officer_id"]),
        reason=payload.reason,
    )


@app.post("/documents/{document_id}/dispute")
def open_dispute(document_id: str, payload: DisputeRequest, ctx: dict[str, str | None] = Depends(_ctx)) -> dict[str, Any]:
    return service.open_dispute(
        document_id=document_id,
        reason=payload.reason,
        evidence_note=payload.evidence_note,
        tenant_id=str(ctx["tenant_id"]),
        officer_id=str(ctx["officer_id"]),
    )


@app.get("/tenants/{tenant_id}/dashboard")
def get_dashboard(tenant_id: str, ctx: dict[str, str | None] = Depends(_ctx)) -> dict[str, Any]:
    if tenant_id != str(ctx["tenant_id"]):
        raise HTTPException(status_code=403, detail="Cross-tenant access forbidden")
    return service.monitoring_dashboard(tenant_id, str(ctx["officer_id"]))


@app.get("/tenants/{tenant_id}/governance")
def get_governance_snapshot(tenant_id: str, ctx: dict[str, str | None] = Depends(_ctx)) -> dict[str, Any]:
    if tenant_id != str(ctx["tenant_id"]):
        raise HTTPException(status_code=403, detail="Cross-tenant access forbidden")
    return governance_service.get_tenant_governance_snapshot(tenant_id, str(ctx["officer_id"]))


@app.get("/tenants/{tenant_id}/kpis")
def get_kpi_dashboard(tenant_id: str, ctx: dict[str, str | None] = Depends(_ctx)) -> dict[str, Any]:
    if tenant_id != str(ctx["tenant_id"]):
        raise HTTPException(status_code=403, detail="Cross-tenant access forbidden")
    return governance_service.get_kpi_dashboard(tenant_id, str(ctx["officer_id"]))


@app.post("/tenants/{tenant_id}/offline/sync")
def run_offline_sync(
    tenant_id: str,
    payload: OfflineSyncRequest,
    ctx: dict[str, str | None] = Depends(_ctx),
) -> dict[str, Any]:
    if tenant_id != str(ctx["tenant_id"]):
        raise HTTPException(status_code=403, detail="Cross-tenant access forbidden")

    pending = service.repo.list_pending_offline_documents(tenant_id, limit=max(1, payload.fetch_limit))
    pending_ids = [str(row.get("id")) for row in pending if row.get("id")]
    if not pending_ids:
        return {
            "tenant_id": tenant_id,
            "pending": 0,
            "synced": 0,
            "failed": 0,
            "queue_overflow": False,
        }

    backpressure = offline_service.apply_sync_backpressure(
        tenant_id=tenant_id,
        officer_id=str(ctx["officer_id"]),
        pending_document_ids=pending_ids,
        sync_capacity_per_minute=max(1, payload.capacity_per_minute),
    )

    synced = 0
    failures: list[dict[str, Any]] = []
    for document_id in pending_ids[: max(1, payload.capacity_per_minute)]:
        try:
            offline_service.sync_offline_document(
                tenant_id=tenant_id,
                document_id=document_id,
                officer_id=str(ctx["officer_id"]),
            )
            synced += 1
        except Exception as exc:  # pragma: no cover
            failures.append({"document_id": document_id, "error": str(exc)})

    return {
        "tenant_id": tenant_id,
        "pending": len(pending_ids),
        "attempted": min(len(pending_ids), max(1, payload.capacity_per_minute)),
        "synced": synced,
        "failed": len(failures),
        "queue_overflow": bool(backpressure.get("queue_overflow", False)),
        "backlog_size": int(backpressure.get("backlog_size", len(pending_ids))),
        "failures": failures[:20],
    }


@app.post("/tenants/{tenant_id}/api-keys")
def create_tenant_api_key(
    tenant_id: str,
    payload: ApiKeyCreateRequest,
    ctx: dict[str, str | None] = Depends(_ctx),
) -> dict[str, Any]:
    if tenant_id != str(ctx["tenant_id"]):
        raise HTTPException(status_code=403, detail="Cross-tenant access forbidden")
    row = service.create_tenant_api_key(
        tenant_id=tenant_id,
        officer_id=str(ctx["officer_id"]),
        key_label=payload.key_label,
        raw_key=payload.raw_key,
    )
    return {
        "tenant_id": row.get("tenant_id"),
        "key_label": row.get("key_label"),
        "status": row.get("status"),
        "created_at": row.get("created_at"),
    }
