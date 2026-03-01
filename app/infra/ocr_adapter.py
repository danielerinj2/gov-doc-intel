from __future__ import annotations

import concurrent.futures
import io
import re
import tempfile
import time
from functools import lru_cache
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


@lru_cache(maxsize=16)
def _cached_paddle_ocr(lang: str, use_gpu: bool, fast_mode: bool) -> Any:
    from paddleocr import PaddleOCR  # type: ignore

    return PaddleOCR(
        use_angle_cls=not fast_mode,
        lang=lang,
        use_gpu=use_gpu,
        show_log=False,
    )


class OCRAdapter:
    """Optional OCR adapter; falls back to provided text when OCR runtime is unavailable."""

    def __init__(self) -> None:
        self.backend = settings.ocr_backend
        self.default_lang = settings.ocr_default_lang
        self.fast_scan_budget_seconds = max(0.5, float(settings.ocr_fast_scan_budget_seconds))
        self.fast_max_side_px = max(640, int(settings.ocr_fast_max_side_px))

    def recognize(
        self,
        *,
        text_fallback: str,
        source_path: str | None = None,
        script_hint: str | None = None,
        fast_mode: bool = False,
        max_seconds: float | None = None,
    ) -> dict[str, Any]:
        started_at = time.perf_counter()
        text = (text_fallback or "").strip()
        confidence = 0.0 if not text else 0.9
        script = _detect_script(text)
        ocr_lang = _lang_from_script_hint(script_hint, self.default_lang)
        timed_out = False

        ocr_budget_seconds: float | None = None
        if max_seconds is not None:
            ocr_budget_seconds = max(0.1, float(max_seconds))
        elif fast_mode:
            ocr_budget_seconds = self.fast_scan_budget_seconds

        if source_path and Path(source_path).exists() and self.backend in {"tesseract", "easyocr", "paddleocr"}:
            inferred = self._recognize_from_source_with_budget(
                source_path=source_path,
                ocr_lang=ocr_lang,
                script_hint=script_hint,
                fast_mode=fast_mode,
                timeout_seconds=ocr_budget_seconds,
            )
            if inferred.get("text"):
                text = str(inferred["text"])
                confidence = float(inferred.get("confidence", confidence))
                script = _detect_script(text)
                if script == "UNKNOWN" and script_hint and script_hint != "AUTO-DETECT":
                    script = str(script_hint).split("(", 1)[0].strip().upper().replace(" ", "_")
            timed_out = bool(inferred.get("timed_out", False))

        elapsed_ms = (time.perf_counter() - started_at) * 1000
        return {
            "text": text,
            "confidence": round(confidence, 3),
            "script": script,
            "backend": self.backend,
            "ocr_lang": ocr_lang,
            "timed_out": timed_out,
            "latency_ms": round(elapsed_ms, 2),
        }

    def _recognize_from_source_with_budget(
        self,
        *,
        source_path: str,
        ocr_lang: str,
        script_hint: str | None,
        fast_mode: bool,
        timeout_seconds: float | None,
    ) -> dict[str, Any]:
        if timeout_seconds is None:
            return self._recognize_from_source(source_path, ocr_lang, script_hint, fast_mode=fast_mode)

        timeout = max(0.1, float(timeout_seconds))
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = executor.submit(
            self._recognize_from_source,
            source_path,
            ocr_lang,
            script_hint,
            fast_mode,
        )
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            future.cancel()
            return {"text": "", "confidence": 0.0, "timed_out": True}
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

    def _recognize_from_source(
        self,
        source_path: str,
        ocr_lang: str,
        script_hint: str | None,
        fast_mode: bool = False,
    ) -> dict[str, Any]:
        suffix = Path(source_path).suffix.lower()
        if suffix == ".pdf":
            return self._recognize_from_pdf(source_path, ocr_lang, script_hint, fast_mode=fast_mode)
        return self._recognize_from_image(source_path, ocr_lang, script_hint, fast_mode=fast_mode)

    def _recognize_from_pdf(
        self,
        source_path: str,
        ocr_lang: str,
        script_hint: str | None,
        fast_mode: bool = False,
    ) -> dict[str, Any]:
        try:
            from pypdf import PdfReader  # type: ignore

            reader = PdfReader(source_path)
            embedded_text: list[str] = []
            pdf_text_pages = 1 if fast_mode else 5
            for page in reader.pages[:pdf_text_pages]:
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
                pdf_ocr_pages = 1 if fast_mode else 3
                images_per_page = 1 if fast_mode else 2
                for page in reader.pages[:pdf_ocr_pages]:
                    images = list(getattr(page, "images", []) or [])
                    for img in images[:images_per_page]:
                        data = getattr(img, "data", None)
                        if not data and hasattr(img, "get_data"):
                            data = img.get_data()
                        if not data:
                            continue
                        if self.backend == "tesseract":
                            extracted = self._tesseract_from_image_bytes(data, ocr_lang, fast_mode=fast_mode)
                        else:
                            extracted = self._paddleocr_from_image_bytes(data, script_hint, fast_mode=fast_mode)
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

    def _recognize_from_image(
        self,
        source_path: str,
        ocr_lang: str,
        script_hint: str | None,
        fast_mode: bool = False,
    ) -> dict[str, Any]:
        if self.backend == "tesseract":
            return self._tesseract(source_path, ocr_lang, fast_mode=fast_mode)
        if self.backend == "easyocr":
            return self._easyocr(source_path, ocr_lang)
        if self.backend == "paddleocr":
            out = self._paddleocr(source_path, script_hint, fast_mode=fast_mode)
            if out.get("text"):
                return out
            # Safe fallback when Paddle runtime/model is unavailable.
            return self._tesseract(source_path, ocr_lang, fast_mode=fast_mode)
        return {"text": "", "confidence": 0.0}

    def _tesseract_from_image_bytes(
        self,
        image_bytes: bytes,
        ocr_lang: str,
        fast_mode: bool = False,
    ) -> dict[str, Any]:
        try:
            from PIL import Image  # type: ignore

            image = Image.open(io.BytesIO(image_bytes))
            return self._tesseract_from_pil(image, ocr_lang, fast_mode=fast_mode)
        except Exception:
            return {"text": "", "confidence": 0.0}

    def _tesseract(self, source_path: str, ocr_lang: str, fast_mode: bool = False) -> dict[str, Any]:
        try:
            from PIL import Image  # type: ignore

            image = Image.open(source_path)
            return self._tesseract_from_pil(image, ocr_lang, fast_mode=fast_mode)
        except Exception:
            return {"text": "", "confidence": 0.0}

    def _tesseract_from_pil(self, image: Any, ocr_lang: str, fast_mode: bool = False) -> dict[str, Any]:
        try:
            import pytesseract  # type: ignore
            from PIL import ImageOps  # type: ignore
        except Exception:
            return {"text": "", "confidence": 0.0}

        # Multi-pass OCR for quality mode; single-pass for fast scans.
        base = image.convert("RGB")
        gray = ImageOps.grayscale(base)
        autocontrast = ImageOps.autocontrast(gray)
        if fast_mode:
            candidates = [autocontrast]
            psm_values = (6,)
        else:
            scaled = autocontrast.resize((max(1, autocontrast.width * 2), max(1, autocontrast.height * 2)))
            binary = autocontrast.point(lambda p: 255 if p > 150 else 0)
            candidates = [base, gray, autocontrast, scaled, binary]
            psm_values = (6, 11)
        best_text = ""
        best_conf = 0.0
        best_score = -1.0

        for img in candidates:
            for psm in psm_values:
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

    def _paddleocr_from_image_bytes(
        self,
        image_bytes: bytes,
        script_hint: str | None,
        fast_mode: bool = False,
    ) -> dict[str, Any]:
        tmp_path: str | None = None
        try:
            from PIL import Image  # type: ignore

            with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
                image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
                image.save(tmp.name, format="PNG")
                tmp_path = tmp.name
            return self._paddleocr(str(tmp_path), script_hint, fast_mode=fast_mode)
        except Exception:
            return {"text": "", "confidence": 0.0}
        finally:
            if tmp_path:
                try:
                    Path(tmp_path).unlink(missing_ok=True)
                except Exception:
                    pass

    def _maybe_downscale_for_fast_scan(self, source_path: str) -> tuple[str, str | None]:
        try:
            from PIL import Image  # type: ignore
        except Exception:
            return source_path, None

        temp_path: str | None = None
        try:
            with Image.open(source_path) as img:
                width, height = img.size
                max_side = max(width, height)
                if max_side <= self.fast_max_side_px:
                    return source_path, None
                scale = self.fast_max_side_px / float(max_side)
                new_size = (
                    max(1, int(width * scale)),
                    max(1, int(height * scale)),
                )
                resized = img.convert("RGB").resize(new_size)
                with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp:
                    resized.save(tmp.name, format="JPEG", quality=85, optimize=True)
                    temp_path = tmp.name
            return temp_path or source_path, temp_path
        except Exception:
            if temp_path:
                try:
                    Path(temp_path).unlink(missing_ok=True)
                except Exception:
                    pass
            return source_path, None

    def _paddleocr(self, source_path: str, script_hint: str | None, fast_mode: bool = False) -> dict[str, Any]:
        try:
            # Deferred import validation; object construction is cached by lang/mode.
            import paddleocr  # type: ignore  # noqa: F401
        except Exception:
            return {"text": "", "confidence": 0.0}

        lang = _paddle_lang_from_script_hint(script_hint)
        use_gpu = False
        run_path = source_path
        cleanup_path: str | None = None

        try:
            if fast_mode:
                run_path, cleanup_path = self._maybe_downscale_for_fast_scan(source_path)
            ocr = _cached_paddle_ocr(lang=lang, use_gpu=use_gpu, fast_mode=fast_mode)
        except Exception:
            # Fallback to English if model pack for selected lang isn't available.
            try:
                ocr = _cached_paddle_ocr(lang="en", use_gpu=use_gpu, fast_mode=fast_mode)
            except Exception:
                return {"text": "", "confidence": 0.0}

        try:
            result = ocr.ocr(run_path, cls=not fast_mode)
        except Exception:
            return {"text": "", "confidence": 0.0}
        finally:
            if cleanup_path:
                try:
                    Path(cleanup_path).unlink(missing_ok=True)
                except Exception:
                    pass

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
