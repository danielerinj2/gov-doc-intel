from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    load_dotenv = None  # type: ignore[assignment]


if load_dotenv is not None:
    # Ensure local development picks up .env automatically.
    load_dotenv(override=False)


def _secret_lookup(key: str) -> str:
    env_val = os.getenv(key)
    if env_val is not None and env_val != "":
        return env_val

    try:
        import streamlit as st  # type: ignore

        sec = st.secrets.get(key)
        if sec is None:
            return ""
        return str(sec)
    except Exception:
        return ""


@dataclass(frozen=True)
class Settings:
    app_env: str = _secret_lookup("APP_ENV") or "dev"

    supabase_url: str = _secret_lookup("SUPABASE_URL")
    supabase_key: str = _secret_lookup("SUPABASE_KEY")
    supabase_anon_key: str = _secret_lookup("SUPABASE_ANON_KEY")
    supabase_service_key: str = _secret_lookup("SUPABASE_SERVICE_KEY")

    ocr_backend: str = _secret_lookup("OCR_BACKEND") or "paddleocr"
    ocr_default_lang: str = _secret_lookup("OCR_DEFAULT_LANG") or "en"
    ocr_device: str = _secret_lookup("OCR_DEVICE") or "auto"
    ocr_min_confidence: float = float(_secret_lookup("OCR_MIN_CONFIDENCE") or "0.55")
    ocr_det_model_name: str = _secret_lookup("OCR_DET_MODEL_NAME")
    ocr_rec_model_name: str = _secret_lookup("OCR_REC_MODEL_NAME")
    ocr_use_doc_orientation_classify: bool = (_secret_lookup("OCR_USE_DOC_ORIENTATION_CLASSIFY") or "true").lower() == "true"
    ocr_use_doc_unwarping: bool = (_secret_lookup("OCR_USE_DOC_UNWARPING") or "true").lower() == "true"
    ocr_use_textline_orientation: bool = (_secret_lookup("OCR_USE_TEXTLINE_ORIENTATION") or "true").lower() == "true"

    classifier_backend: str = (_secret_lookup("CLASSIFIER_BACKEND") or "heuristic").lower()
    layoutlm_model_dir: str = _secret_lookup("LAYOUTLM_MODEL_DIR") or "./saved_model"
    fusion_model_path: str = _secret_lookup("FUSION_MODEL_PATH") or "checkpoints/final_fusion_model.pt"
    fusion_label_map_path: str = _secret_lookup("FUSION_LABEL_MAP_PATH") or "data/label_map.json"

    issuer_registry_base_url: str = _secret_lookup("ISSUER_REGISTRY_BASE_URL")
    issuer_registry_token: str = _secret_lookup("ISSUER_REGISTRY_TOKEN")

    sendgrid_api_key: str = _secret_lookup("SENDGRID_API_KEY")
    sendgrid_from_email: str = _secret_lookup("SENDGRID_FROM_EMAIL")

    app_login_url: str = _secret_lookup("APP_LOGIN_URL") or "https://govdociq.streamlit.app"
    supabase_password_reset_redirect_url: str = (
        _secret_lookup("SUPABASE_PASSWORD_RESET_REDIRECT_URL") or "https://govdociq.streamlit.app"
    )

    data_dir: str = _secret_lookup("DATA_DIR") or ".data"

    def supabase_url_valid(self) -> bool:
        return self.supabase_url.startswith("https://") and ".supabase.co" in self.supabase_url

    def supabase_key_present(self) -> bool:
        return bool((self.supabase_service_key or self.supabase_key or self.supabase_anon_key).strip())


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
