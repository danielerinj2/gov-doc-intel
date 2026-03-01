from __future__ import annotations

from typing import Any

from app.config import settings

try:
    from supabase import Client, create_client
except Exception:  # pragma: no cover - optional runtime dependency
    Client = Any  # type: ignore[assignment]
    create_client = None  # type: ignore[assignment]


def get_supabase_client() -> Client | None:
    if create_client is None:
        return None

    key = settings.supabase_service_key or settings.supabase_key
    if not settings.supabase_url_valid() or not key:
        return None

    try:
        return create_client(settings.supabase_url, key)
    except Exception:
        return None
