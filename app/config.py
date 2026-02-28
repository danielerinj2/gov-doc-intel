from __future__ import annotations

import re
from dataclasses import dataclass
import os

from dotenv import load_dotenv


load_dotenv()


def _pick_supabase_key() -> str:
    return (
        os.getenv("SUPABASE_KEY", "").strip()
        or os.getenv("SUPABASE_SERVICE_KEY", "").strip()
        or os.getenv("SUPABASE_ANON_KEY", "").strip()
    )


@dataclass(frozen=True)
class Settings:
    app_env: str
    supabase_url: str
    supabase_key: str
    groq_api_key: str
    groq_model: str

    def supabase_url_valid(self) -> bool:
        # Must be project URL, not postgres DSN.
        return bool(re.match(r"^https://[a-z0-9-]+\.supabase\.co$", self.supabase_url))

    def supabase_key_present(self) -> bool:
        return bool(self.supabase_key)


def load_settings() -> Settings:
    return Settings(
        app_env=os.getenv("APP_ENV", "dev").strip(),
        supabase_url=os.getenv("SUPABASE_URL", "").strip().rstrip("/"),
        supabase_key=_pick_supabase_key(),
        groq_api_key=os.getenv("GROQ_API_KEY", "").strip(),
        groq_model=os.getenv("GROQ_MODEL", "llama-3.1-70b-versatile").strip(),
    )


settings = load_settings()
