from __future__ import annotations

from datetime import datetime, timezone
from threading import RLock
from typing import Any
from uuid import uuid4

from app.infra.supabase_client import get_supabase_client

ROLE_VERIFIER = "verifier"
ROLE_SENIOR_VERIFIER = "senior_verifier"
ROLE_AUDITOR = "auditor"
ROLE_PLATFORM_ADMIN = "platform_admin"


class RepositoryError(RuntimeError):
    pass


class DocumentRepository:
    def create_document(self, row: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def update_document(self, document_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def get_document(self, document_id: str) -> dict[str, Any] | None:
        raise NotImplementedError

    def list_documents(self, limit: int = 500) -> list[dict[str, Any]]:
        raise NotImplementedError

    def create_review(self, row: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def list_reviews(self, document_id: str | None = None) -> list[dict[str, Any]]:
        raise NotImplementedError

    def create_audit_event(self, row: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def list_audit_events(self, document_id: str | None = None, limit: int = 1000) -> list[dict[str, Any]]:
        raise NotImplementedError


class InMemoryRepository(DocumentRepository):
    def __init__(self) -> None:
        self._lock = RLock()
        self._documents: dict[str, dict[str, Any]] = {}
        self._reviews: list[dict[str, Any]] = []
        self._events: list[dict[str, Any]] = []

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def create_document(self, row: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            now = self._utc_now()
            item = {
                "id": row.get("id") or str(uuid4()),
                "created_at": row.get("created_at") or now,
                "updated_at": row.get("updated_at") or now,
                **row,
            }
            self._documents[str(item["id"])] = item
            return dict(item)

    def update_document(self, document_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            existing = self._documents.get(document_id)
            if not existing:
                raise RepositoryError(f"Document not found: {document_id}")
            existing.update(updates)
            existing["updated_at"] = self._utc_now()
            return dict(existing)

    def get_document(self, document_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._documents.get(document_id)
            return dict(row) if row else None

    def list_documents(self, limit: int = 500) -> list[dict[str, Any]]:
        with self._lock:
            rows = sorted(
                self._documents.values(),
                key=lambda r: str(r.get("updated_at", r.get("created_at", ""))),
                reverse=True,
            )
            return [dict(r) for r in rows[:limit]]

    def create_review(self, row: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            item = {
                "id": row.get("id") or str(uuid4()),
                "created_at": row.get("created_at") or self._utc_now(),
                **row,
            }
            self._reviews.append(item)
            return dict(item)

    def list_reviews(self, document_id: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._reviews
            if document_id:
                rows = [r for r in rows if str(r.get("document_id")) == document_id]
            return [dict(r) for r in rows]

    def create_audit_event(self, row: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            item = {
                "id": row.get("id") or str(uuid4()),
                "created_at": row.get("created_at") or self._utc_now(),
                **row,
            }
            self._events.append(item)
            return dict(item)

    def list_audit_events(self, document_id: str | None = None, limit: int = 1000) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._events
            if document_id:
                rows = [r for r in rows if str(r.get("document_id")) == document_id]
            rows = sorted(rows, key=lambda r: str(r.get("created_at", "")), reverse=True)
            return [dict(r) for r in rows[:limit]]


class SupabaseRepository(DocumentRepository):
    def __init__(self, client: Any) -> None:
        self.client = client

    def _insert_one(self, table: str, row: dict[str, Any]) -> dict[str, Any]:
        payload = dict(row)
        if "id" not in payload:
            payload["id"] = str(uuid4())
        res = self.client.table(table).insert(payload).execute()
        if not res.data:
            raise RepositoryError(f"Insert failed for {table}")
        return dict(res.data[0])

    def create_document(self, row: dict[str, Any]) -> dict[str, Any]:
        return self._insert_one("documents", row)

    def update_document(self, document_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        payload = dict(updates)
        payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        res = self.client.table("documents").update(payload).eq("id", document_id).execute()
        if not res.data:
            raise RepositoryError(f"Update failed for document {document_id}")
        return dict(res.data[0])

    def get_document(self, document_id: str) -> dict[str, Any] | None:
        res = self.client.table("documents").select("*").eq("id", document_id).limit(1).execute()
        if not res.data:
            return None
        return dict(res.data[0])

    def list_documents(self, limit: int = 500) -> list[dict[str, Any]]:
        res = self.client.table("documents").select("*").order("updated_at", desc=True).limit(limit).execute()
        return [dict(r) for r in (res.data or [])]

    def create_review(self, row: dict[str, Any]) -> dict[str, Any]:
        return self._insert_one("reviews", row)

    def list_reviews(self, document_id: str | None = None) -> list[dict[str, Any]]:
        q = self.client.table("reviews").select("*").order("created_at", desc=True)
        if document_id:
            q = q.eq("document_id", document_id)
        res = q.execute()
        return [dict(r) for r in (res.data or [])]

    def create_audit_event(self, row: dict[str, Any]) -> dict[str, Any]:
        return self._insert_one("audit_events", row)

    def list_audit_events(self, document_id: str | None = None, limit: int = 1000) -> list[dict[str, Any]]:
        q = self.client.table("audit_events").select("*").order("created_at", desc=True).limit(limit)
        if document_id:
            q = q.eq("document_id", document_id)
        res = q.execute()
        return [dict(r) for r in (res.data or [])]


def build_repository() -> tuple[DocumentRepository, bool, str | None]:
    client = get_supabase_client()
    if client is None:
        return InMemoryRepository(), False, "Supabase client unavailable; using in-memory repository."

    try:
        # Lightweight connectivity check
        client.table("documents").select("id").limit(1).execute()
        return SupabaseRepository(client), True, None
    except Exception as exc:
        return InMemoryRepository(), False, f"Supabase unavailable ({exc}); using in-memory repository."
