from __future__ import annotations

from typing import Any

from supabase import Client, create_client

from app.config import settings


def get_supabase_client() -> tuple[Client | None, str | None]:
    if not settings.supabase_url or not settings.supabase_key:
        return None, "SUPABASE_URL or SUPABASE_KEY missing"

    if not settings.supabase_url_valid():
        return None, "SUPABASE_URL invalid (must look like https://<project-ref>.supabase.co)"

    try:
        client = create_client(settings.supabase_url, settings.supabase_key)
        return client, None
    except Exception as exc:  # pragma: no cover
        return None, f"Supabase init failed: {exc}"


def exec_query(query: Any) -> dict[str, Any] | None:
    try:
        resp = query.execute()
        data = getattr(resp, "data", None)
        return {"data": data}
    except Exception:
        return None
