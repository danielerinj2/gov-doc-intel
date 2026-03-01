from __future__ import annotations

import re
from dataclasses import dataclass
import os
from typing import Any

from dotenv import load_dotenv


load_dotenv()


def _to_secret_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _get_streamlit_secret(*keys: str) -> str:
    try:
        import streamlit as st
    except Exception:
        return ""

    normalized: list[str] = []
    for key in keys:
        normalized.extend([key, key.lower(), key.upper()])

    try:
        for key in normalized:
            value = _to_secret_str(st.secrets.get(key))
            if value:
                return value
    except Exception:
        pass

    section_aliases = {
        "supabase": {
            "supabase_url": ["url", "supabase_url"],
            "supabase_service_key": ["service_key", "service_role_key", "supabase_service_key"],
            "supabase_key": ["key", "anon_key", "supabase_key", "supabase_anon_key"],
            "supabase_anon_key": ["anon_key", "key", "supabase_anon_key", "supabase_key"],
        },
        "sendgrid": {
            "sendgrid_api_key": ["api_key", "sendgrid_api_key", "sendgrid_key"],
            "sendgrid_key": ["api_key", "sendgrid_api_key", "sendgrid_key"],
            "send_grid_api_key": ["api_key", "sendgrid_api_key", "sendgrid_key"],
            "sendgrid_from_email": ["from_email", "sender", "sendgrid_from_email"],
            "email_from": ["from_email", "sender", "sendgrid_from_email"],
        },
        "app": {
            "app_login_url": ["login_url", "app_login_url"],
            "supabase_password_reset_redirect_url": ["password_reset_redirect_url", "supabase_password_reset_redirect_url"],
        },
    }

    for section_name, alias_map in section_aliases.items():
        try:
            section_data = st.secrets.get(section_name)
        except Exception:
            section_data = None
        if not isinstance(section_data, dict):
            continue
        nested = {str(k).lower(): _to_secret_str(v) for k, v in section_data.items()}
        for key in keys:
            for alias in alias_map.get(key.lower(), []):
                value = nested.get(alias.lower(), "")
                if value:
                    return value

    return ""


def _get_config_value(*keys: str, default: str = "") -> str:
    for key in keys:
        value = os.getenv(key, "").strip()
        if value:
            return value
    secret_value = _get_streamlit_secret(*keys)
    if secret_value:
        return secret_value
    return default


def _pick_supabase_key() -> str:
    return (
        _get_config_value("SUPABASE_SERVICE_KEY", "SUPABASE_SERVICE_ROLE_KEY")
        or _get_config_value("SUPABASE_KEY")
        or _get_config_value("SUPABASE_ANON_KEY")
    )


def _pick_sendgrid_api_key() -> str:
    return _get_config_value(
        "SENDGRID_API_KEY",
        "SENDGRID_KEY",
        "SEND_GRID_API_KEY",
        "SENDGRID_APIKEY",
    )


def _pick_sendgrid_from_email() -> str:
    return _get_config_value(
        "SENDGRID_FROM_EMAIL",
        "EMAIL_FROM",
        "FROM_EMAIL",
        "SENDGRID_SENDER",
    )


@dataclass(frozen=True)
class Settings:
    app_env: str
    default_workspace_id: str
    app_login_url: str
    password_reset_redirect_url: str
    supabase_url: str
    supabase_key: str
    sendgrid_api_key: str
    sendgrid_from_email: str
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
    ocr_fast_scan_budget_seconds: float
    ocr_fast_max_side_px: int
    authenticity_backend: str
    fraud_calibration_weights: str

    def supabase_url_valid(self) -> bool:
        # Must be project URL, not postgres DSN.
        return bool(re.match(r"^https://[a-z0-9-]+\.supabase\.co$", self.supabase_url))

    def supabase_key_present(self) -> bool:
        return bool(self.supabase_key)


def load_settings() -> Settings:
    app_login_url = _get_config_value(
        "APP_LOGIN_URL",
        "GOVDOCIQ_LOGIN_URL",
        default="https://govdociq.streamlit.app",
    )
    return Settings(
        app_env=_get_config_value("APP_ENV", default="dev"),
        default_workspace_id=_get_config_value(
            "DEFAULT_WORKSPACE_ID",
            "DEFAULT_DEPARTMENT_ID",
            "DEPARTMENT_ID",
            default="workspace-default",
        ),
        app_login_url=app_login_url,
        password_reset_redirect_url=_get_config_value(
            "SUPABASE_PASSWORD_RESET_REDIRECT_URL",
            "PASSWORD_RESET_REDIRECT_URL",
            default=app_login_url,
        ),
        supabase_url=_get_config_value("SUPABASE_URL").rstrip("/"),
        supabase_key=_pick_supabase_key(),
        sendgrid_api_key=_pick_sendgrid_api_key(),
        sendgrid_from_email=_pick_sendgrid_from_email(),
        groq_api_key=_get_config_value("GROQ_API_KEY"),
        groq_model=_get_config_value("GROQ_MODEL", default="llama-3.1-70b-versatile"),
        groq_user_agent=_get_config_value(
            "GROQ_USER_AGENT",
            default=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
        ),
        event_bus_backend=_get_config_value("EVENT_BUS_BACKEND", default="inmemory").lower(),
        kafka_bootstrap_servers=_get_config_value("KAFKA_BOOTSTRAP_SERVERS"),
        kafka_topic=_get_config_value("KAFKA_TOPIC", default="document-events"),
        pulsar_service_url=_get_config_value("PULSAR_SERVICE_URL"),
        pulsar_topic=_get_config_value("PULSAR_TOPIC", default="persistent://public/default/document-events"),
        issuer_registry_base_url=_get_config_value("ISSUER_REGISTRY_BASE_URL").rstrip("/"),
        issuer_registry_token=_get_config_value("ISSUER_REGISTRY_TOKEN"),
        ocr_backend=_get_config_value("OCR_BACKEND", default="paddleocr").lower(),
        ocr_default_lang=_get_config_value("OCR_DEFAULT_LANG", default="eng+hin"),
        ocr_fast_scan_budget_seconds=float(_get_config_value("OCR_FAST_SCAN_BUDGET_SECONDS", default="3.0") or 3.0),
        ocr_fast_max_side_px=int(_get_config_value("OCR_FAST_MAX_SIDE_PX", default="1280") or 1280),
        authenticity_backend=_get_config_value("AUTHENTICITY_BACKEND", default="heuristic").lower(),
        fraud_calibration_weights=_get_config_value("FRAUD_CALIBRATION_WEIGHTS"),
    )


settings = load_settings()
