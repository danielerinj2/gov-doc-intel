from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from app.domain.models import Dispute, Document, DocumentEvent
from app.domain.states import DocumentState
from app.infra.supabase_client import exec_query, get_supabase_client


class MemoryStore:
    documents: dict[str, dict[str, Any]] = {}
    events: dict[str, list[dict[str, Any]]] = defaultdict(list)
    disputes: dict[str, list[dict[str, Any]]] = defaultdict(list)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Repository:
    def __init__(self) -> None:
        self.client, self.error = get_supabase_client()
        if self.client and not self._probe_documents_access():
            self.client = None
            if not self.error:
                self.error = "Supabase connected but documents table access failed (check schema/RLS/key)"

    @property
    def using_supabase(self) -> bool:
        return self.client is not None

    def _probe_documents_access(self) -> bool:
        if not self.client:
            return False
        result = exec_query(self.client.table("documents").select("id").limit(1))
        return result is not None

    def create_document(self, doc: Document) -> dict[str, Any]:
        row = {
            "id": doc.id,
            "tenant_id": doc.tenant_id,
            "citizen_id": doc.citizen_id,
            "file_name": doc.file_name,
            "raw_text": doc.raw_text,
            "metadata": doc.metadata,
            "state": doc.state.value,
            "dedup_hash": doc.dedup_hash,
            "confidence": doc.confidence,
            "risk_score": doc.risk_score,
            "decision": doc.decision,
            "created_at": doc.created_at,
            "updated_at": doc.updated_at,
        }
        if self.client:
            result = exec_query(self.client.table("documents").insert(row))
            if result and result.get("data"):
                return result["data"][0]

        MemoryStore.documents[doc.id] = row
        return row

    def update_document(self, document_id: str, **updates: Any) -> dict[str, Any] | None:
        updates["updated_at"] = _now()
        if "state" in updates and isinstance(updates["state"], DocumentState):
            updates["state"] = updates["state"].value

        if self.client:
            result = exec_query(self.client.table("documents").update(updates).eq("id", document_id))
            if result and result.get("data"):
                row = self.get_document(document_id)
                if row:
                    return row

        row = MemoryStore.documents.get(document_id)
        if not row:
            return None
        row.update(updates)
        return row

    def get_document(self, document_id: str) -> dict[str, Any] | None:
        if self.client:
            result = exec_query(self.client.table("documents").select("*").eq("id", document_id).limit(1))
            if result and result.get("data"):
                rows = result["data"]
                return rows[0] if rows else None
        return MemoryStore.documents.get(document_id)

    def list_documents(self, tenant_id: str) -> list[dict[str, Any]]:
        if self.client:
            result = exec_query(
                self.client.table("documents")
                .select("*")
                .eq("tenant_id", tenant_id)
                .order("created_at", desc=True)
            )
            if result and result.get("data") is not None:
                return result["data"]

        rows = [r for r in MemoryStore.documents.values() if r["tenant_id"] == tenant_id]
        return sorted(rows, key=lambda x: x["created_at"], reverse=True)

    def count_by_hash(self, tenant_id: str, dedup_hash: str, exclude_document_id: str | None = None) -> int:
        if self.client:
            query = (
                self.client.table("documents")
                .select("id", count="exact")
                .eq("tenant_id", tenant_id)
                .eq("dedup_hash", dedup_hash)
            )
            if exclude_document_id:
                query = query.neq("id", exclude_document_id)
            result = exec_query(query)
            if result is not None:
                # supabase-py returns count on response object but fallback via length if unavailable
                data = result.get("data") or []
                return len(data)

        count = 0
        for row in MemoryStore.documents.values():
            if row["tenant_id"] != tenant_id:
                continue
            if row.get("dedup_hash") != dedup_hash:
                continue
            if exclude_document_id and row["id"] == exclude_document_id:
                continue
            count += 1
        return count

    def add_event(self, event: DocumentEvent) -> dict[str, Any]:
        row = {
            "id": event.id,
            "document_id": event.document_id,
            "tenant_id": event.tenant_id,
            "event_type": event.event_type,
            "payload": event.payload,
            "created_at": event.created_at,
        }
        if self.client:
            result = exec_query(self.client.table("document_events").insert(row))
            if result and result.get("data"):
                return result["data"][0]

        MemoryStore.events[event.document_id].append(row)
        return row

    def list_events(self, document_id: str) -> list[dict[str, Any]]:
        if self.client:
            result = exec_query(
                self.client.table("document_events")
                .select("*")
                .eq("document_id", document_id)
                .order("created_at", desc=False)
            )
            if result and result.get("data") is not None:
                return result["data"]
        return MemoryStore.events.get(document_id, [])

    def create_dispute(self, dispute: Dispute) -> dict[str, Any]:
        row = {
            "id": dispute.id,
            "document_id": dispute.document_id,
            "tenant_id": dispute.tenant_id,
            "reason": dispute.reason,
            "evidence_note": dispute.evidence_note,
            "status": dispute.status,
            "created_at": dispute.created_at,
        }
        if self.client:
            result = exec_query(self.client.table("disputes").insert(row))
            if result and result.get("data"):
                return result["data"][0]

        MemoryStore.disputes[dispute.document_id].append(row)
        return row

    def list_disputes(self, tenant_id: str) -> list[dict[str, Any]]:
        if self.client:
            result = exec_query(
                self.client.table("disputes")
                .select("*")
                .eq("tenant_id", tenant_id)
                .order("created_at", desc=True)
            )
            if result and result.get("data") is not None:
                return result["data"]

        rows: list[dict[str, Any]] = []
        for items in MemoryStore.disputes.values():
            rows.extend([it for it in items if it["tenant_id"] == tenant_id])
        return sorted(rows, key=lambda x: x["created_at"], reverse=True)
