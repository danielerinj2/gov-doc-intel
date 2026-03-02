from __future__ import annotations

from datetime import datetime, timezone
import json
import re
import time
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


def _extract_missing_column_name(message: str) -> str | None:
    # Handles messages like:
    # "Could not find the 'file_path' column of 'documents' in the schema cache"
    patterns = [
        r"'([^']+)'\s+column",
        r"column\s+'([^']+)'",
        r'Could not find the "([^"]+)" column',
    ]
    for pat in patterns:
        m = re.search(pat, message, flags=re.IGNORECASE)
        if m:
            return m.group(1)
    return None


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
        # Retry by dropping unknown columns reported by PostgREST schema cache.
        for _ in range(20):
            try:
                res = self.client.table(table).insert(payload).execute()
                if not res.data:
                    raise RepositoryError(f"Insert failed for {table}")
                return dict(res.data[0])
            except Exception as exc:
                missing_col = _extract_missing_column_name(str(exc))
                if missing_col and missing_col in payload:
                    payload.pop(missing_col, None)
                    continue
                raise RepositoryError(f"Insert failed for {table}: {exc}") from exc
        raise RepositoryError(f"Insert failed for {table}: too many schema-mismatch retries")

    def create_document(self, row: dict[str, Any]) -> dict[str, Any]:
        return self._insert_one("documents", row)

    def update_document(self, document_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        payload = dict(updates)
        payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        for _ in range(20):
            try:
                res = self.client.table("documents").update(payload).eq("id", document_id).execute()
                if not res.data:
                    raise RepositoryError(f"Update failed for document {document_id}")
                return dict(res.data[0])
            except Exception as exc:
                missing_col = _extract_missing_column_name(str(exc))
                if missing_col and missing_col in payload:
                    payload.pop(missing_col, None)
                    continue
                raise RepositoryError(f"Update failed for document {document_id}: {exc}") from exc
        raise RepositoryError(f"Update failed for document {document_id}: too many schema-mismatch retries")

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
        for _ in range(20):
            try:
                out = self._rest("POST", "documents", payload=payload)
                if not out:
                    raise RepositoryError("Insert failed for documents")
                return out[0]
            except Exception as exc:
                missing_col = _extract_missing_column_name(str(exc))
                if missing_col and missing_col in payload:
                    payload.pop(missing_col, None)
                    continue
                raise
        raise RepositoryError("Insert failed for documents: too many schema-mismatch retries")

    def update_document(self, document_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        payload = dict(updates)
        payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        for _ in range(20):
            try:
                out = self._rest(
                    "PATCH",
                    "documents",
                    params={"id": f"eq.{document_id}"},
                    payload=payload,
                )
                if not out:
                    raise RepositoryError(f"Update failed for document {document_id}")
                return out[0]
            except Exception as exc:
                missing_col = _extract_missing_column_name(str(exc))
                if missing_col and missing_col in payload:
                    payload.pop(missing_col, None)
                    continue
                raise
        raise RepositoryError(f"Update failed for document {document_id}: too many schema-mismatch retries")

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


class AppwriteRepository(DocumentRepository):
    def __init__(self) -> None:
        self.endpoint = settings.appwrite_endpoint.rstrip("/")
        self.project_id = settings.appwrite_project_id
        self.api_key = settings.appwrite_api_key
        self.database_id = settings.appwrite_database_id
        self.documents_col = settings.appwrite_documents_collection_id
        self.reviews_col = settings.appwrite_reviews_collection_id
        self.audit_col = settings.appwrite_audit_collection_id

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            raise RepositoryError("APPWRITE_API_KEY is required for database persistence.")
        return {
            "X-Appwrite-Project": self.project_id,
            "X-Appwrite-Key": self.api_key,
            "Content-Type": "application/json",
        }

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        ok_conflict: bool = True,
    ) -> dict[str, Any]:
        url = f"{self.endpoint}{path}"
        res = requests.request(
            method,
            url,
            headers=self._headers(),
            json=payload,
            params=params,
            timeout=25,
        )
        if res.status_code == 409 and ok_conflict:
            return {"conflict": True}
        if res.status_code >= 400:
            raise RepositoryError(f"Appwrite {method} {path} failed [{res.status_code}] {res.text[:400]}")
        if not res.text:
            return {}
        try:
            return res.json() or {}
        except Exception:
            return {}

    def _ensure_database(self) -> None:
        self._request(
            "POST",
            "/databases",
            payload={
                "databaseId": self.database_id,
                "name": settings.appwrite_project_name or "GovDocIQ DB",
                "enabled": True,
            },
            ok_conflict=True,
        )

    def _ensure_collection(self, collection_id: str, name: str) -> None:
        self._request(
            "POST",
            f"/databases/{self.database_id}/collections",
            payload={
                "collectionId": collection_id,
                "name": name,
                "permissions": [],
                "documentSecurity": False,
                "enabled": True,
            },
            ok_conflict=True,
        )

    def _ensure_string_attr(self, collection_id: str, key: str, size: int, required: bool = False) -> None:
        self._request(
            "POST",
            f"/databases/{self.database_id}/collections/{collection_id}/attributes/string",
            payload={
                "key": key,
                "size": size,
                "required": required,
                "default": None,
                "array": False,
            },
            ok_conflict=True,
        )

    def _ensure_schema(self, collection_id: str) -> None:
        self._ensure_string_attr(collection_id, "doc_id", 64, required=True)
        self._ensure_string_attr(collection_id, "row_type", 64, required=True)
        self._ensure_string_attr(collection_id, "tenant_id", 128, required=True)
        self._ensure_string_attr(collection_id, "document_id", 64, required=False)
        self._ensure_string_attr(collection_id, "state", 64, required=False)
        self._ensure_string_attr(collection_id, "decision", 64, required=False)
        self._ensure_string_attr(collection_id, "created_at", 64, required=True)
        self._ensure_string_attr(collection_id, "updated_at", 64, required=True)
        self._ensure_string_attr(collection_id, "data_json", 65535, required=True)

    def _wait_attributes(self, collection_id: str, timeout_sec: int = 60) -> None:
        started = datetime.now(timezone.utc).timestamp()
        while (datetime.now(timezone.utc).timestamp() - started) < timeout_sec:
            out = self._request(
                "GET",
                f"/databases/{self.database_id}/collections/{collection_id}/attributes",
                ok_conflict=False,
            )
            attrs = out.get("attributes") or []
            if attrs and all(str(a.get("status", "")).lower() == "available" for a in attrs):
                return
            time.sleep(1.2)
        raise RepositoryError(f"Timed out waiting for Appwrite attributes: {collection_id}")

    def bootstrap_storage(self) -> None:
        self._ensure_database()
        for cid, name in [
            (self.documents_col, "Documents"),
            (self.reviews_col, "Reviews"),
            (self.audit_col, "Audit Events"),
        ]:
            self._ensure_collection(cid, name)
            self._ensure_schema(cid)
            self._wait_attributes(cid, timeout_sec=60)

    @staticmethod
    def _is_bootstrap_candidate_error(message: str) -> bool:
        msg = str(message).lower()
        return any(
            k in msg
            for k in [
                "database_not_found",
                "collection_not_found",
                "attribute_not_found",
                "could not find",
            ]
        )

    def _row_to_doc(self, row: dict[str, Any], *, row_type: str) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        data = dict(row)
        data.setdefault("id", str(uuid4()))
        data.setdefault("created_at", now)
        data.setdefault("updated_at", now)
        tenant_id = str(data.get("tenant_id") or settings.default_workspace_id or "workspace-default")
        data["tenant_id"] = tenant_id
        return {
            "doc_id": str(data["id"]),
            "row_type": row_type,
            "tenant_id": tenant_id,
            "document_id": str(data.get("document_id") or ""),
            "state": str(data.get("state") or ""),
            "decision": str(data.get("decision") or ""),
            "created_at": str(data.get("created_at") or now),
            "updated_at": str(data.get("updated_at") or now),
            "data_json": json.dumps(data, ensure_ascii=False),
        }

    def _doc_to_row(self, doc: dict[str, Any]) -> dict[str, Any]:
        payload = str(doc.get("data_json") or "{}")
        try:
            row = json.loads(payload)
            if isinstance(row, dict):
                return row
        except Exception:
            pass
        return {}

    def _create_document(self, collection_id: str, document_id: str, data: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.endpoint}/databases/{self.database_id}/collections/{collection_id}/documents"
        body = {"documentId": document_id, "data": data}
        res = requests.post(url, headers=self._headers(), json=body, timeout=20)
        if res.status_code >= 400:
            raise RepositoryError(f"Appwrite create failed [{res.status_code}] {res.text[:400]}")
        return res.json() or {}

    def _update_document(self, collection_id: str, document_id: str, data: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.endpoint}/databases/{self.database_id}/collections/{collection_id}/documents/{document_id}"
        body = {"data": data}
        res = requests.patch(url, headers=self._headers(), json=body, timeout=20)
        if res.status_code >= 400:
            raise RepositoryError(f"Appwrite update failed [{res.status_code}] {res.text[:400]}")
        return res.json() or {}

    def _get_document(self, collection_id: str, document_id: str) -> dict[str, Any] | None:
        url = f"{self.endpoint}/databases/{self.database_id}/collections/{collection_id}/documents/{document_id}"
        res = requests.get(url, headers=self._headers(), timeout=20)
        if res.status_code == 404:
            return None
        if res.status_code >= 400:
            raise RepositoryError(f"Appwrite get failed [{res.status_code}] {res.text[:400]}")
        return res.json() or {}

    def _list_documents(self, collection_id: str, limit: int = 500) -> list[dict[str, Any]]:
        url = f"{self.endpoint}/databases/{self.database_id}/collections/{collection_id}/documents"
        params = {"limit": max(1, min(limit, 5000))}
        res = requests.get(url, headers=self._headers(), params=params, timeout=20)
        if res.status_code >= 400:
            raise RepositoryError(f"Appwrite list failed [{res.status_code}] {res.text[:400]}")
        data = res.json() or {}
        return [dict(d) for d in (data.get("documents") or [])]

    def create_document(self, row: dict[str, Any]) -> dict[str, Any]:
        doc = self._row_to_doc(row, row_type="document")
        self._create_document(self.documents_col, str(doc["doc_id"]), doc)
        return self._doc_to_row(doc)

    def update_document(self, document_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        existing_doc = self._get_document(self.documents_col, document_id)
        if not existing_doc:
            raise RepositoryError(f"Document not found: {document_id}")
        existing = self._doc_to_row(existing_doc)
        existing.update(dict(updates))
        existing["id"] = document_id
        existing["updated_at"] = datetime.now(timezone.utc).isoformat()
        doc = self._row_to_doc(existing, row_type="document")
        self._update_document(self.documents_col, document_id, doc)
        return existing

    def get_document(self, document_id: str) -> dict[str, Any] | None:
        doc = self._get_document(self.documents_col, document_id)
        if not doc:
            return None
        return self._doc_to_row(doc)

    def list_documents(self, limit: int = 500) -> list[dict[str, Any]]:
        docs = self._list_documents(self.documents_col, limit=limit)
        rows = [self._doc_to_row(d) for d in docs]
        rows = [r for r in rows if isinstance(r, dict)]
        rows.sort(key=lambda r: str(r.get("updated_at", r.get("created_at", ""))), reverse=True)
        return rows[:limit]

    def create_review(self, row: dict[str, Any]) -> dict[str, Any]:
        doc = self._row_to_doc(row, row_type="review")
        self._create_document(self.reviews_col, str(doc["doc_id"]), doc)
        return self._doc_to_row(doc)

    def list_reviews(self, document_id: str | None = None) -> list[dict[str, Any]]:
        docs = self._list_documents(self.reviews_col, limit=5000)
        rows = [self._doc_to_row(d) for d in docs]
        if document_id:
            rows = [r for r in rows if str(r.get("document_id")) == document_id]
        rows.sort(key=lambda r: str(r.get("created_at", "")), reverse=True)
        return rows

    def create_audit_event(self, row: dict[str, Any]) -> dict[str, Any]:
        doc = self._row_to_doc(row, row_type="audit_event")
        self._create_document(self.audit_col, str(doc["doc_id"]), doc)
        return self._doc_to_row(doc)

    def list_audit_events(self, document_id: str | None = None, limit: int = 1000) -> list[dict[str, Any]]:
        docs = self._list_documents(self.audit_col, limit=max(limit, 1000))
        rows = [self._doc_to_row(d) for d in docs]
        if document_id:
            rows = [r for r in rows if str(r.get("document_id")) == document_id]
        rows.sort(key=lambda r: str(r.get("created_at", "")), reverse=True)
        return rows[:limit]


def build_repository() -> tuple[DocumentRepository, bool, str | None]:
    if settings.auth_provider == "appwrite":
        if not settings.appwrite_configured():
            return InMemoryRepository(), False, "Appwrite not configured; using in-memory repository."
        try:
            repo = AppwriteRepository()
            # Basic reachability check.
            repo._list_documents(repo.documents_col, limit=1)
            return repo, False, None
        except Exception as exc:
            try:
                repo = AppwriteRepository()
                if repo._is_bootstrap_candidate_error(str(exc)):
                    repo.bootstrap_storage()
                    repo._list_documents(repo.documents_col, limit=1)
                    return repo, False, "Appwrite storage auto-created and ready."
            except Exception as boot_exc:
                exc = boot_exc
            return (
                InMemoryRepository(),
                False,
                f"Appwrite unavailable or collections missing ({exc}). "
                "Run scripts/setup_appwrite.py and restart. Using in-memory repository.",
            )

    client = get_supabase_client()
    required_cols = "id"

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
