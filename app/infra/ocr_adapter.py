from __future__ import annotations

import gc
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config import settings
from app.pipeline.preload import get_ocr_context
from PIL import Image

try:
    from pypdf import PdfReader
except Exception:  # pragma: no cover
    PdfReader = None  # type: ignore[assignment]

try:
    import pytesseract
except Exception:  # pragma: no cover
    pytesseract = None  # type: ignore[assignment]


@dataclass
class OCRResult:
    text: str
    confidence: float
    engine: str
    language: str
    words: list[str]
    bbox: list[list[float]]
    line_confidence: list[float]


def polygon_to_rect(polygon: list[list[float]]) -> list[float]:
    xs = [point[0] for point in polygon]
    ys = [point[1] for point in polygon]
    return [float(min(xs)), float(min(ys)), float(max(xs)), float(max(ys))]


def _remove_by_index(items: list[Any], indices: list[int]) -> list[Any]:
    out = list(items)
    for idx in sorted(indices, reverse=True):
        if 0 <= idx < len(out):
            del out[idx]
    return out


def _normalize_text_preserve_unicode(text: str) -> str:
    # Keep Unicode (Indic scripts), remove control chars and normalize whitespace.
    text = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def clean_ocr_output(words: list[str], bboxes: list[list[list[float]]], confidences: list[float]) -> tuple[list[str], list[list[float]], list[float]]:
    cleaned_words: list[str] = []
    removed_idx: list[int] = []

    for idx, text in enumerate(words):
        norm = _normalize_text_preserve_unicode(str(text or ""))
        if norm:
            cleaned_words.append(norm)
        else:
            removed_idx.append(idx)

    cleaned_conf = _remove_by_index(confidences, removed_idx)
    cleaned_bbox_poly = _remove_by_index(bboxes, removed_idx)
    cleaned_bbox = [polygon_to_rect(poly) for poly in cleaned_bbox_poly]
    return cleaned_words, cleaned_bbox, cleaned_conf


class OCRAdapter:
    def __init__(self, default_lang: str = "en") -> None:
        self.default_lang = default_lang

    @staticmethod
    def _empty(engine: str, language: str) -> OCRResult:
        return OCRResult(
            text="",
            confidence=0.0,
            engine=engine,
            language=language,
            words=[],
            bbox=[],
            line_confidence=[],
        )

    def _extract_pdf_text(self, file_path: str) -> OCRResult:
        if PdfReader is None:
            return self._empty("none", self.default_lang)
        try:
            reader = PdfReader(file_path)
            text = "\n".join((page.extract_text() or "") for page in reader.pages[:10]).strip()
            text = _normalize_text_preserve_unicode(text)
            conf = 0.95 if text else 0.0
            words = [w for w in text.split(" ") if w]
            return OCRResult(
                text=text,
                confidence=conf,
                engine="pypdf",
                language=self.default_lang,
                words=words,
                bbox=[],
                line_confidence=[conf for _ in words],
            )
        except Exception:
            return self._empty("pypdf", self.default_lang)

    def _extract_with_tesseract(self, file_path: str, hint_script: str = "AUTO-DETECT") -> OCRResult:
        if pytesseract is None:
            return self._empty("tesseract-unavailable", self.default_lang)

        suffix = Path(file_path).suffix.lower()
        if suffix not in {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".webp"}:
            return self._empty("tesseract-unsupported-format", self.default_lang)

        lang_map = {
            "AUTO-DETECT": "eng",
            "Latin (English)": "eng",
            "Devanagari (Hindi/Marathi/Sanskrit)": "hin+eng",
            "Bengali": "ben+eng",
            "Tamil": "tam+eng",
            "Telugu": "tel+eng",
            "Kannada": "kan+eng",
            "Malayalam": "mal+eng",
            "Gujarati": "guj+eng",
            "Gurmukhi (Punjabi)": "pan+eng",
            "Odia": "ori+eng",
            "Urdu (Nastaliq)": "urd+eng",
        }
        lang = lang_map.get(hint_script, "eng")

        try:
            image = Image.open(file_path).convert("RGB")
            data = pytesseract.image_to_data(
                image,
                output_type=pytesseract.Output.DICT,
                lang=lang,
                config="--oem 1 --psm 6",
            )

            words: list[str] = []
            bbox: list[list[float]] = []
            conf: list[float] = []
            n = len(data.get("text", []))
            for i in range(n):
                txt = _normalize_text_preserve_unicode(str(data["text"][i] or ""))
                try:
                    sc = float(data.get("conf", ["-1"])[i])
                except Exception:
                    sc = -1.0
                if not txt:
                    continue
                if sc >= 0 and (sc / 100.0) < settings.ocr_min_confidence:
                    continue
                left = float(data.get("left", [0])[i] or 0)
                top = float(data.get("top", [0])[i] or 0)
                width = float(data.get("width", [0])[i] or 0)
                height = float(data.get("height", [0])[i] or 0)
                words.append(txt)
                bbox.append([left, top, left + width, top + height])
                conf.append(max(0.0, sc / 100.0))

            text = " ".join(words).strip()
            avg = (sum(conf) / len(conf)) if conf else 0.0
            return OCRResult(
                text=text,
                confidence=float(avg),
                engine="tesseract",
                language=lang,
                words=words,
                bbox=bbox,
                line_confidence=conf,
            )
        except Exception:
            return self._empty("tesseract", lang)

    def _extract_from_new_predict_api(self, paddle_ocr: Any, file_path: str) -> OCRResult | None:
        try:
            result = paddle_ocr.predict(file_path)
            if not result:
                return None

            candidates: list[tuple[float, Any, str]] = []

            for res in result:
                texts = res.get("rec_texts") or []
                polys = res.get("dt_polys") or []
                scores = res.get("rec_scores") or []
                for score, poly, text in zip(scores, polys, texts):
                    s = float(score or 0.0)
                    candidates.append((s, poly, str(text or "")))

            def _pick_rows(threshold: float) -> tuple[list[str], list[list[list[float]]], list[float]]:
                words: list[str] = []
                bbox: list[list[list[float]]] = []
                conf: list[float] = []
                for s, poly, txt in candidates:
                    if s >= threshold:
                        words.append(txt)
                        bbox.append(poly.tolist() if hasattr(poly, "tolist") else poly)
                        conf.append(s)
                return words, bbox, conf

            # First pass: configured threshold, then relaxed thresholds to avoid blank OCR on low-quality scans.
            raw_words, raw_bbox, raw_conf = _pick_rows(settings.ocr_min_confidence)
            if not raw_words:
                raw_words, raw_bbox, raw_conf = _pick_rows(0.25)
            if not raw_words:
                raw_words = [txt for _, _, txt in candidates]
                raw_bbox = [poly.tolist() if hasattr(poly, "tolist") else poly for _, poly, _ in candidates]
                raw_conf = [s for s, _, _ in candidates]

            words, bbox, conf = clean_ocr_output(raw_words, raw_bbox, raw_conf)
            joined = " ".join(words).strip()
            avg = (sum(conf) / len(conf)) if conf else 0.0
            return OCRResult(
                text=joined,
                confidence=float(avg),
                engine="paddleocr-v5",
                language=self.default_lang,
                words=words,
                bbox=bbox,
                line_confidence=conf,
            )
        except Exception:
            return None

    def _extract_from_legacy_ocr_api(self, paddle_ocr: Any, file_path: str) -> OCRResult:
        try:
            ocr_out = paddle_ocr.ocr(file_path, cls=True)
            candidates: list[tuple[float, Any, str]] = []
            for block in ocr_out or []:
                for line in block or []:
                    if not isinstance(line, (list, tuple)) or len(line) < 2:
                        continue
                    poly = line[0]
                    rec = line[1]
                    text = str(rec[0] or "").strip() if isinstance(rec, (list, tuple)) else ""
                    score = float(rec[1] or 0.0) if isinstance(rec, (list, tuple)) and len(rec) > 1 else 0.0
                    candidates.append((score, poly, text))

            def _pick_rows(threshold: float) -> tuple[list[str], list[list[list[float]]], list[float]]:
                words: list[str] = []
                bbox: list[list[list[float]]] = []
                conf: list[float] = []
                for s, poly, txt in candidates:
                    if s >= threshold:
                        words.append(txt)
                        bbox.append(poly)
                        conf.append(s)
                return words, bbox, conf

            raw_words, raw_bbox, raw_conf = _pick_rows(settings.ocr_min_confidence)
            if not raw_words:
                raw_words, raw_bbox, raw_conf = _pick_rows(0.25)
            if not raw_words:
                raw_words = [txt for _, _, txt in candidates]
                raw_bbox = [poly for _, poly, _ in candidates]
                raw_conf = [s for s, _, _ in candidates]

            words, bbox, conf = clean_ocr_output(raw_words, raw_bbox, raw_conf)
            joined = "\n".join(words).strip()
            avg = (sum(conf) / len(conf)) if conf else 0.0
            return OCRResult(
                text=joined,
                confidence=float(avg),
                engine="paddleocr",
                language=self.default_lang,
                words=words,
                bbox=bbox,
                line_confidence=conf,
            )
        except Exception:
            return self._empty("paddleocr", self.default_lang)

    def extract_text(self, file_path: str, hint_script: str = "AUTO-DETECT") -> OCRResult:
        suffix = Path(file_path).suffix.lower()

        if suffix in {".txt", ".csv", ".json"}:
            try:
                txt = Path(file_path).read_text(encoding="utf-8", errors="ignore").strip()
                txt = _normalize_text_preserve_unicode(txt)
                words = [w for w in txt.split(" ") if w]
                return OCRResult(
                    text=txt,
                    confidence=0.99 if txt else 0.0,
                    engine="text-reader",
                    language="utf-8",
                    words=words,
                    bbox=[],
                    line_confidence=[0.99 for _ in words],
                )
            except Exception:
                return self._empty("text-reader", "utf-8")

        if suffix == ".pdf":
            pdf = self._extract_pdf_text(file_path)
            if pdf.text:
                return pdf

        ctx = get_ocr_context()
        if ctx.ocr is None:
            fallback = self._extract_with_tesseract(file_path, hint_script=hint_script)
            if fallback.text:
                return fallback
            return self._empty(f"paddle-unavailable:{ctx.error or 'not_configured'}", self.default_lang)

        # Prefer modern `predict` output when available.
        if hasattr(ctx.ocr, "predict"):
            out = self._extract_from_new_predict_api(ctx.ocr, file_path)
            if out is not None and out.text:
                self._cleanup_device_cache()
                return out

        out = self._extract_from_legacy_ocr_api(ctx.ocr, file_path)
        self._cleanup_device_cache()
        return out

    @staticmethod
    def _cleanup_device_cache() -> None:
        try:
            import paddle

            if bool(paddle.device.is_compiled_with_cuda()):
                paddle.device.cuda.empty_cache()
        except Exception:
            pass
        gc.collect()
