from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any, Callable

from app.config import settings


def _load_create_client() -> tuple[Callable[..., Any] | None, str | None]:
    # Fast path: normal import.
    try:
        from supabase import create_client as fn  # type: ignore

        return fn, None
    except Exception:
        pass

    # Fallback: this repo has a local `supabase/` SQL folder that can shadow the
    # installed package. Temporarily remove cwd/repo root and retry import.
    repo_root = str(Path(__file__).resolve().parents[2])
    removed: list[str] = []
    for candidate in ("", ".", repo_root):
        while candidate in sys.path:
            sys.path.remove(candidate)
            removed.append(candidate)

    try:
        mod = importlib.import_module("supabase")
        fn = getattr(mod, "create_client", None)
        if callable(fn):
            return fn, None
    except Exception as exc:
        return None, str(exc)
    finally:
        for item in reversed(removed):
            sys.path.insert(0, item)

    return None, "Installed supabase client package not found"


_CREATE_CLIENT, _CREATE_CLIENT_ERR = _load_create_client()


def get_supabase_client() -> tuple[Any | None, str | None]:
    if not settings.supabase_url or not settings.supabase_key:
        return None, "SUPABASE_URL or SUPABASE_KEY missing"

    if not settings.supabase_url_valid():
        return None, "SUPABASE_URL invalid (must look like https://<project-ref>.supabase.co)"

    if _CREATE_CLIENT is None:
        msg = _CREATE_CLIENT_ERR or "Supabase client package unavailable"
        return None, f"Supabase client import failed: {msg}"

    try:
        client = _CREATE_CLIENT(settings.supabase_url, settings.supabase_key)
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

