from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from app.config import settings


def _resolve_device() -> str:
    if settings.ocr_device.lower() != "auto":
        return settings.ocr_device
    try:
        import paddle

        return "gpu" if bool(paddle.device.is_compiled_with_cuda()) else "cpu"
    except Exception:
        return "cpu"


@dataclass
class OCRContext:
    ocr: Any | None
    error: str | None


@lru_cache(maxsize=1)
def get_ocr_context() -> OCRContext:
    try:
        from paddleocr import PaddleOCR  # type: ignore

        kwargs: dict[str, Any] = {
            "use_doc_orientation_classify": settings.ocr_use_doc_orientation_classify,
            "use_doc_unwarping": settings.ocr_use_doc_unwarping,
            "use_textline_orientation": settings.ocr_use_textline_orientation,
            "device": _resolve_device(),
        }

        # Support both old/new constructor params.
        if settings.ocr_det_model_name:
            kwargs["text_detection_model_name"] = settings.ocr_det_model_name
        if settings.ocr_rec_model_name:
            kwargs["text_recognition_model_name"] = settings.ocr_rec_model_name

        try:
            ocr = PaddleOCR(**kwargs)
        except TypeError:
            # Fallback for older PaddleOCR signatures.
            fallback = {
                "use_angle_cls": True,
                "lang": settings.ocr_default_lang,
                "show_log": False,
            }
            ocr = PaddleOCR(**fallback)

        return OCRContext(ocr=ocr, error=None)
    except Exception as exc:
        return OCRContext(ocr=None, error=str(exc))
