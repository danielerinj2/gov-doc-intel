from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app.config import settings


def _detect_script(text: str) -> str:
    if re.search(r"[\u0900-\u097F]", text):
        return "DEVANAGARI"
    if re.search(r"[\u0B80-\u0BFF]", text):
        return "TAMIL"
    if re.search(r"[\u0C00-\u0C7F]", text):
        return "TELUGU"
    if re.search(r"[\u0C80-\u0CFF]", text):
        return "KANNADA"
    if re.search(r"[A-Za-z]", text):
        return "LATIN"
    return "UNKNOWN"


class OCRAdapter:
    """Optional OCR adapter; falls back to provided text when OCR runtime is unavailable."""

    def __init__(self) -> None:
        self.backend = settings.ocr_backend
        self.default_lang = settings.ocr_default_lang

    def recognize(self, *, text_fallback: str, source_path: str | None = None) -> dict[str, Any]:
        text = (text_fallback or "").strip()
        confidence = 0.0 if not text else 0.9
        script = _detect_script(text)

        if source_path and Path(source_path).exists() and self.backend in {"tesseract", "easyocr"}:
            inferred = self._recognize_from_image(source_path)
            if inferred.get("text"):
                text = str(inferred["text"])
                confidence = float(inferred.get("confidence", confidence))
                script = _detect_script(text)

        return {
            "text": text,
            "confidence": round(confidence, 3),
            "script": script,
            "backend": self.backend,
        }

    def _recognize_from_image(self, source_path: str) -> dict[str, Any]:
        if self.backend == "tesseract":
            return self._tesseract(source_path)
        if self.backend == "easyocr":
            return self._easyocr(source_path)
        return {"text": "", "confidence": 0.0}

    def _tesseract(self, source_path: str) -> dict[str, Any]:
        try:
            import pytesseract  # type: ignore
            from PIL import Image  # type: ignore

            raw = pytesseract.image_to_string(Image.open(source_path), lang=self.default_lang)
            conf_data = pytesseract.image_to_data(Image.open(source_path), output_type=pytesseract.Output.DICT)
            conf = [float(c) for c in conf_data.get("conf", []) if str(c).strip() not in {"", "-1"}]
            avg_conf = (sum(conf) / len(conf) / 100.0) if conf else 0.75
            return {"text": raw.strip(), "confidence": max(0.0, min(1.0, avg_conf))}
        except Exception:
            return {"text": "", "confidence": 0.0}

    def _easyocr(self, source_path: str) -> dict[str, Any]:
        try:
            import easyocr  # type: ignore

            reader = easyocr.Reader([self.default_lang], gpu=False)
            out = reader.readtext(source_path)
            if not out:
                return {"text": "", "confidence": 0.0}
            text = "\n".join(str(item[1]) for item in out)
            confs = [float(item[2]) for item in out if len(item) >= 3]
            avg_conf = (sum(confs) / len(confs)) if confs else 0.7
            return {"text": text, "confidence": max(0.0, min(1.0, avg_conf))}
        except Exception:
            return {"text": "", "confidence": 0.0}
