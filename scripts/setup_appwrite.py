from __future__ import annotations

import sys
from pathlib import Path
import time
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import settings


class SetupError(RuntimeError):
    pass


def _headers() -> dict[str, str]:
    if not settings.appwrite_project_id or not settings.appwrite_api_key:
        raise SetupError("APPWRITE_PROJECT_ID and APPWRITE_API_KEY are required.")
    return {
        "X-Appwrite-Project": settings.appwrite_project_id,
        "X-Appwrite-Key": settings.appwrite_api_key,
        "Content-Type": "application/json",
    }


def _request(method: str, path: str, payload: dict[str, Any] | None = None, ok_conflict: bool = True) -> dict[str, Any]:
    endpoint = settings.appwrite_endpoint.rstrip("/")
    if not endpoint:
        raise SetupError("APPWRITE_ENDPOINT is required.")
    url = f"{endpoint}{path}"
    res = requests.request(method, url, headers=_headers(), json=payload, timeout=30)
    if res.status_code == 409 and ok_conflict:
        return {"conflict": True}
    if res.status_code >= 400:
        raise SetupError(f"{method} {path} failed [{res.status_code}] {res.text[:400]}")
    if not res.text:
        return {}
    try:
        return res.json()
    except Exception:
        return {}


def ensure_database() -> None:
    _request(
        "POST",
        "/databases",
        {
            "databaseId": settings.appwrite_database_id,
            "name": settings.appwrite_project_name or "GovDocIQ DB",
            "enabled": True,
        },
    )


def ensure_collection(collection_id: str, name: str) -> None:
    _request(
        "POST",
        f"/databases/{settings.appwrite_database_id}/collections",
        {
            "collectionId": collection_id,
            "name": name,
            "permissions": [],
            "documentSecurity": False,
            "enabled": True,
        },
    )


def ensure_string_attr(collection_id: str, key: str, size: int, required: bool = False) -> None:
    _request(
        "POST",
        f"/databases/{settings.appwrite_database_id}/collections/{collection_id}/attributes/string",
        {
            "key": key,
            "size": size,
            "required": required,
            "default": None,
            "array": False,
        },
    )


def ensure_schema(collection_id: str) -> None:
    ensure_string_attr(collection_id, "doc_id", 64, required=True)
    ensure_string_attr(collection_id, "row_type", 64, required=True)
    ensure_string_attr(collection_id, "tenant_id", 128, required=True)
    ensure_string_attr(collection_id, "document_id", 64, required=False)
    ensure_string_attr(collection_id, "state", 64, required=False)
    ensure_string_attr(collection_id, "decision", 64, required=False)
    ensure_string_attr(collection_id, "created_at", 64, required=True)
    ensure_string_attr(collection_id, "updated_at", 64, required=True)
    ensure_string_attr(collection_id, "data_json", 65535, required=True)


def wait_attributes(collection_id: str, timeout_sec: int = 60) -> None:
    start = time.time()
    while time.time() - start < timeout_sec:
        out = _request(
            "GET",
            f"/databases/{settings.appwrite_database_id}/collections/{collection_id}/attributes",
            payload=None,
        )
        attrs = out.get("attributes") or []
        if attrs and all(str(a.get("status", "")).lower() == "available" for a in attrs):
            return
        time.sleep(1.2)
    raise SetupError(f"Timed out waiting for attributes in {collection_id}")


def smoke_test_documents_collection() -> None:
    doc_id = f"setup-test-{int(time.time())}"
    payload = {
        "documentId": doc_id,
        "data": {
            "doc_id": doc_id,
            "row_type": "document",
            "tenant_id": settings.default_workspace_id,
            "document_id": "",
            "state": "SETUP_TEST",
            "decision": "",
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "data_json": "{}",
        },
    }
    _request(
        "POST",
        f"/databases/{settings.appwrite_database_id}/collections/{settings.appwrite_documents_collection_id}/documents",
        payload,
        ok_conflict=False,
    )
    _request(
        "DELETE",
        f"/databases/{settings.appwrite_database_id}/collections/{settings.appwrite_documents_collection_id}/documents/{doc_id}",
        payload=None,
        ok_conflict=False,
    )


def main() -> None:
    print("Setting up Appwrite database and collections...")
    ensure_database()

    for cid, name in [
        (settings.appwrite_documents_collection_id, "Documents"),
        (settings.appwrite_reviews_collection_id, "Reviews"),
        (settings.appwrite_audit_collection_id, "Audit Events"),
    ]:
        ensure_collection(cid, name)
        ensure_schema(cid)
        wait_attributes(cid)

    smoke_test_documents_collection()
    print("Appwrite setup complete and smoke test passed.")


if __name__ == "__main__":
    main()
