from __future__ import annotations

from datetime import datetime, timezone
from threading import RLock
from typing import Any
from uuid import uuid4

import requests

from app.config import settings
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


class SupabaseRESTRepository(DocumentRepository):
    def __init__(self, *, base_url: str, service_key: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.service_key = service_key

    def _headers(self, include_json: bool = True) -> dict[str, str]:
        h = {
            "apikey": self.service_key,
            "Authorization": f"Bearer {self.service_key}",
            "Prefer": "return=representation",
        }
        if include_json:
            h["Content-Type"] = "application/json"
        return h

    def _rest(self, method: str, table: str, *, params: dict[str, Any] | None = None, payload: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        url = f"{self.base_url}/rest/v1/{table}"
        res = requests.request(
            method,
            url,
            params=params,
            json=payload,
            headers=self._headers(include_json=True),
            timeout=20,
        )
        if res.status_code >= 400:
            raise RepositoryError(f"Supabase REST error [{res.status_code}] {res.text[:300]}")
        try:
            data = res.json()
        except Exception:
            data = []
        if isinstance(data, list):
            return [dict(r) for r in data]
        if isinstance(data, dict):
            return [data]
        return []

    def create_document(self, row: dict[str, Any]) -> dict[str, Any]:
        payload = dict(row)
        payload.setdefault("id", str(uuid4()))
        out = self._rest("POST", "documents", payload=payload)
        if not out:
            raise RepositoryError("Insert failed for documents")
        return out[0]

    def update_document(self, document_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        payload = dict(updates)
        payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        out = self._rest(
            "PATCH",
            "documents",
            params={"id": f"eq.{document_id}"},
            payload=payload,
        )
        if not out:
            raise RepositoryError(f"Update failed for document {document_id}")
        return out[0]

    def get_document(self, document_id: str) -> dict[str, Any] | None:
        out = self._rest(
            "GET",
            "documents",
            params={"select": "*", "id": f"eq.{document_id}", "limit": 1},
            payload=None,
        )
        return out[0] if out else None

    def list_documents(self, limit: int = 500) -> list[dict[str, Any]]:
        return self._rest(
            "GET",
            "documents",
            params={"select": "*", "order": "updated_at.desc", "limit": limit},
            payload=None,
        )

    def create_review(self, row: dict[str, Any]) -> dict[str, Any]:
        payload = dict(row)
        payload.setdefault("id", str(uuid4()))
        out = self._rest("POST", "reviews", payload=payload)
        if not out:
            raise RepositoryError("Insert failed for reviews")
        return out[0]

    def list_reviews(self, document_id: str | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"select": "*", "order": "created_at.desc"}
        if document_id:
            params["document_id"] = f"eq.{document_id}"
        return self._rest("GET", "reviews", params=params, payload=None)

    def create_audit_event(self, row: dict[str, Any]) -> dict[str, Any]:
        payload = dict(row)
        payload.setdefault("id", str(uuid4()))
        out = self._rest("POST", "audit_events", payload=payload)
        if not out:
            raise RepositoryError("Insert failed for audit_events")
        return out[0]

    def list_audit_events(self, document_id: str | None = None, limit: int = 1000) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"select": "*", "order": "created_at.desc", "limit": limit}
        if document_id:
            params["document_id"] = f"eq.{document_id}"
        return self._rest("GET", "audit_events", params=params, payload=None)


def build_repository() -> tuple[DocumentRepository, bool, str | None]:
    client = get_supabase_client()
    required_cols = "id,ocr_engine,preprocess_output,classification_output,extraction_output,validation_output,fraud_output"

    if client is not None:
        try:
            # Connectivity + schema compatibility check (required columns used by the app).
            client.table("documents").select(required_cols).limit(1).execute()
            return SupabaseRepository(client), True, None
        except Exception as exc:
            return (
                InMemoryRepository(),
                False,
                "Supabase unavailable or schema mismatch "
                f"({exc}). Run `supabase/schema.sql` (or patch SQL) and restart. "
                "Using in-memory repository.",
            )

    # Fallback path when supabase-py isn't available: use REST directly.
    base_url = settings.supabase_url.strip()
    key = (settings.supabase_service_key or settings.supabase_key).strip()
    if settings.supabase_url_valid() and key:
        try:
            repo = SupabaseRESTRepository(base_url=base_url, service_key=key)
            repo._rest(
                "GET",
                "documents",
                params={"select": required_cols, "limit": 1},
                payload=None,
            )
            return repo, True, None
        except Exception as exc:
            return (
                InMemoryRepository(),
                False,
                "Supabase REST unavailable or schema mismatch "
                f"({exc}). Run `supabase/schema.sql` (or patch SQL) and restart. "
                "Using in-memory repository.",
            )

    return InMemoryRepository(), False, "Supabase client unavailable; using in-memory repository."
