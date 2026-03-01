from __future__ import annotations

import io
import re
import tempfile
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


def _lang_from_script_hint(script_hint: str | None, default_lang: str) -> str:
    if not script_hint:
        return default_lang
    key = str(script_hint).strip().lower()
    if not key or key == "auto-detect":
        return default_lang
    mapping = {
        "devanagari (hindi/marathi/sanskrit)": "hin+eng",
        "bengali": "ben+eng",
        "tamil": "tam+eng",
        "telugu": "tel+eng",
        "kannada": "kan+eng",
        "malayalam": "mal+eng",
        "gujarati": "guj+eng",
        "gurmukhi (punjabi)": "pan+eng",
        "odia": "ori+eng",
        "urdu (nastaliq)": "urd+eng",
        "latin (english)": "eng",
    }
    return mapping.get(key, default_lang)


def _paddle_lang_from_script_hint(script_hint: str | None) -> str:
    key = str(script_hint or "").strip().lower()
    mapping = {
        "devanagari (hindi/marathi/sanskrit)": "devanagari",
        "bengali": "devanagari",
        "tamil": "ta",
        "telugu": "te",
        "kannada": "ka",
        "malayalam": "devanagari",
        "gujarati": "devanagari",
        "gurmukhi (punjabi)": "devanagari",
        "odia": "devanagari",
        "urdu (nastaliq)": "arabic",
        "latin (english)": "en",
    }
    return mapping.get(key, "en")


class OCRAdapter:
    """Optional OCR adapter; falls back to provided text when OCR runtime is unavailable."""

    def __init__(self) -> None:
        self.backend = settings.ocr_backend
        self.default_lang = settings.ocr_default_lang

    def recognize(
        self,
        *,
        text_fallback: str,
        source_path: str | None = None,
        script_hint: str | None = None,
    ) -> dict[str, Any]:
        text = (text_fallback or "").strip()
        confidence = 0.0 if not text else 0.9
        script = _detect_script(text)
        ocr_lang = _lang_from_script_hint(script_hint, self.default_lang)

        if source_path and Path(source_path).exists() and self.backend in {"tesseract", "easyocr", "paddleocr"}:
            inferred = self._recognize_from_source(source_path, ocr_lang, script_hint)
            if inferred.get("text"):
                text = str(inferred["text"])
                confidence = float(inferred.get("confidence", confidence))
                script = _detect_script(text)
                if script == "UNKNOWN" and script_hint and script_hint != "AUTO-DETECT":
                    script = str(script_hint).split("(", 1)[0].strip().upper().replace(" ", "_")

        return {
            "text": text,
            "confidence": round(confidence, 3),
            "script": script,
            "backend": self.backend,
            "ocr_lang": ocr_lang,
        }

    def _recognize_from_source(self, source_path: str, ocr_lang: str, script_hint: str | None) -> dict[str, Any]:
        suffix = Path(source_path).suffix.lower()
        if suffix == ".pdf":
            return self._recognize_from_pdf(source_path, ocr_lang, script_hint)
        return self._recognize_from_image(source_path, ocr_lang, script_hint)

    def _recognize_from_pdf(self, source_path: str, ocr_lang: str, script_hint: str | None) -> dict[str, Any]:
        try:
            from pypdf import PdfReader  # type: ignore

            reader = PdfReader(source_path)
            embedded_text: list[str] = []
            for page in reader.pages[:5]:
                chunk = (page.extract_text() or "").strip()
                if chunk:
                    embedded_text.append(chunk)
            if embedded_text:
                joined = "\n".join(embedded_text).strip()
                if joined:
                    return {"text": joined, "confidence": 0.78}

            if self.backend in {"tesseract", "paddleocr"}:
                snippets: list[str] = []
                confs: list[float] = []
                for page in reader.pages[:3]:
                    images = list(getattr(page, "images", []) or [])
                    for img in images[:2]:
                        data = getattr(img, "data", None)
                        if not data and hasattr(img, "get_data"):
                            data = img.get_data()
                        if not data:
                            continue
                        if self.backend == "tesseract":
                            extracted = self._tesseract_from_image_bytes(data, ocr_lang)
                        else:
                            extracted = self._paddleocr_from_image_bytes(data, script_hint)
                        txt = str(extracted.get("text", "")).strip()
                        if txt:
                            snippets.append(txt)
                            confs.append(float(extracted.get("confidence", 0.0)))
                if snippets:
                    avg = sum(confs) / max(1, len(confs))
                    return {"text": "\n".join(snippets), "confidence": max(0.0, min(1.0, avg))}
        except Exception:
            return {"text": "", "confidence": 0.0}
        return {"text": "", "confidence": 0.0}

    def _recognize_from_image(self, source_path: str, ocr_lang: str, script_hint: str | None) -> dict[str, Any]:
        if self.backend == "tesseract":
            return self._tesseract(source_path, ocr_lang)
        if self.backend == "easyocr":
            return self._easyocr(source_path, ocr_lang)
        if self.backend == "paddleocr":
            out = self._paddleocr(source_path, script_hint)
            if out.get("text"):
                return out
            # Safe fallback when Paddle runtime/model is unavailable.
            return self._tesseract(source_path, ocr_lang)
        return {"text": "", "confidence": 0.0}

    def _tesseract_from_image_bytes(self, image_bytes: bytes, ocr_lang: str) -> dict[str, Any]:
        try:
            from PIL import Image  # type: ignore

            image = Image.open(io.BytesIO(image_bytes))
            return self._tesseract_from_pil(image, ocr_lang)
        except Exception:
            return {"text": "", "confidence": 0.0}

    def _tesseract(self, source_path: str, ocr_lang: str) -> dict[str, Any]:
        try:
            from PIL import Image  # type: ignore

            image = Image.open(source_path)
            return self._tesseract_from_pil(image, ocr_lang)
        except Exception:
            return {"text": "", "confidence": 0.0}

    def _tesseract_from_pil(self, image: Any, ocr_lang: str) -> dict[str, Any]:
        try:
            import pytesseract  # type: ignore
            from PIL import ImageOps  # type: ignore
        except Exception:
            return {"text": "", "confidence": 0.0}

        # Multi-pass OCR with lightweight preprocessing to improve low-quality scans.
        base = image.convert("RGB")
        gray = ImageOps.grayscale(base)
        autocontrast = ImageOps.autocontrast(gray)
        scaled = autocontrast.resize((max(1, autocontrast.width * 2), max(1, autocontrast.height * 2)))
        binary = autocontrast.point(lambda p: 255 if p > 150 else 0)

        candidates = [base, gray, autocontrast, scaled, binary]
        best_text = ""
        best_conf = 0.0
        best_score = -1.0

        for img in candidates:
            for psm in (6, 11):
                config = f"--oem 1 --psm {psm}"
                try:
                    raw = pytesseract.image_to_string(img, lang=ocr_lang, config=config).strip()
                except Exception:
                    continue
                if not raw:
                    continue

                conf = 0.0
                try:
                    conf_data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT, lang=ocr_lang, config=config)
                    confs = [
                        float(c)
                        for c in conf_data.get("conf", [])
                        if str(c).strip() not in {"", "-1"}
                    ]
                    conf = (sum(confs) / len(confs) / 100.0) if confs else 0.0
                except Exception:
                    conf = 0.0
                conf = max(0.0, min(1.0, conf))

                score = (conf * 0.7) + (min(len(raw), 1600) / 1600.0 * 0.3)
                if score > best_score:
                    best_score = score
                    best_text = raw
                    best_conf = conf if conf > 0 else max(best_conf, 0.6)

        return {"text": best_text, "confidence": best_conf}

    def _easyocr(self, source_path: str, ocr_lang: str) -> dict[str, Any]:
        try:
            import easyocr  # type: ignore

            easy_map = {
                "eng": "en",
                "hin": "hi",
                "ben": "bn",
                "tam": "ta",
                "tel": "te",
                "kan": "kn",
                "mal": "ml",
                "guj": "gu",
                "pan": "pa",
                "ori": "or",
                "urd": "ur",
            }
            langs = [easy_map.get(code, code) for code in ocr_lang.split("+") if code]
            reader = easyocr.Reader(langs or ["en"], gpu=False)
            out = reader.readtext(source_path)
            if not out:
                return {"text": "", "confidence": 0.0}
            text = "\n".join(str(item[1]) for item in out)
            confs = [float(item[2]) for item in out if len(item) >= 3]
            avg_conf = (sum(confs) / len(confs)) if confs else 0.7
            return {"text": text, "confidence": max(0.0, min(1.0, avg_conf))}
        except Exception:
            return {"text": "", "confidence": 0.0}

    def _paddleocr_from_image_bytes(self, image_bytes: bytes, script_hint: str | None) -> dict[str, Any]:
        tmp_path: str | None = None
        try:
            from PIL import Image  # type: ignore

            with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
                image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
                image.save(tmp.name, format="PNG")
                tmp_path = tmp.name
            return self._paddleocr(str(tmp_path), script_hint)
        except Exception:
            return {"text": "", "confidence": 0.0}
        finally:
            if tmp_path:
                try:
                    Path(tmp_path).unlink(missing_ok=True)
                except Exception:
                    pass

    def _paddleocr(self, source_path: str, script_hint: str | None) -> dict[str, Any]:
        try:
            from paddleocr import PaddleOCR  # type: ignore
        except Exception:
            return {"text": "", "confidence": 0.0}

        lang = _paddle_lang_from_script_hint(script_hint)
        use_gpu = False

        try:
            ocr = PaddleOCR(use_angle_cls=True, lang=lang, use_gpu=use_gpu, show_log=False)
        except Exception:
            # Fallback to English if model pack for selected lang isn't available.
            try:
                ocr = PaddleOCR(use_angle_cls=True, lang="en", use_gpu=use_gpu, show_log=False)
            except Exception:
                return {"text": "", "confidence": 0.0}

        try:
            result = ocr.ocr(source_path, cls=True)
        except Exception:
            return {"text": "", "confidence": 0.0}

        lines: list[str] = []
        confs: list[float] = []
        for page in result or []:
            for item in page or []:
                if not isinstance(item, list) or len(item) < 2:
                    continue
                pair = item[1]
                if isinstance(pair, (list, tuple)) and len(pair) >= 2:
                    text = str(pair[0]).strip()
                    if text:
                        lines.append(text)
                    try:
                        confs.append(float(pair[1]))
                    except Exception:
                        pass

        if not lines:
            return {"text": "", "confidence": 0.0}

        avg_conf = sum(confs) / max(1, len(confs))
        return {"text": "\n".join(lines), "confidence": max(0.0, min(1.0, avg_conf))}
