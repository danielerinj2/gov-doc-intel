from __future__ import annotations

import re
from dataclasses import dataclass
import os

from dotenv import load_dotenv


load_dotenv()


def _pick_supabase_key() -> str:
    return (
        os.getenv("SUPABASE_SERVICE_KEY", "").strip()
        or os.getenv("SUPABASE_KEY", "").strip()
        or os.getenv("SUPABASE_ANON_KEY", "").strip()
    )


@dataclass(frozen=True)
class Settings:
    app_env: str
    supabase_url: str
    supabase_key: str
    groq_api_key: str
    groq_model: str
    groq_user_agent: str
    event_bus_backend: str
    kafka_bootstrap_servers: str
    kafka_topic: str
    pulsar_service_url: str
    pulsar_topic: str
    issuer_registry_base_url: str
    issuer_registry_token: str
    ocr_backend: str
    ocr_default_lang: str
    authenticity_backend: str
    fraud_calibration_weights: str

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
        groq_user_agent=os.getenv(
            "GROQ_USER_AGENT",
            (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
        ).strip(),
        event_bus_backend=os.getenv("EVENT_BUS_BACKEND", "inmemory").strip().lower(),
        kafka_bootstrap_servers=os.getenv("KAFKA_BOOTSTRAP_SERVERS", "").strip(),
        kafka_topic=os.getenv("KAFKA_TOPIC", "document-events").strip(),
        pulsar_service_url=os.getenv("PULSAR_SERVICE_URL", "").strip(),
        pulsar_topic=os.getenv("PULSAR_TOPIC", "persistent://public/default/document-events").strip(),
        issuer_registry_base_url=os.getenv("ISSUER_REGISTRY_BASE_URL", "").strip().rstrip("/"),
        issuer_registry_token=os.getenv("ISSUER_REGISTRY_TOKEN", "").strip(),
        ocr_backend=os.getenv("OCR_BACKEND", "tesseract").strip().lower(),
        ocr_default_lang=os.getenv("OCR_DEFAULT_LANG", "eng+hin").strip(),
        authenticity_backend=os.getenv("AUTHENTICITY_BACKEND", "heuristic").strip().lower(),
        fraud_calibration_weights=os.getenv("FRAUD_CALIBRATION_WEIGHTS", "").strip(),
    )


settings = load_settings()
