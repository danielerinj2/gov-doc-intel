from __future__ import annotations

import gc
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config import settings
from app.pipeline.preload import get_ocr_context

try:
    from pypdf import PdfReader
except Exception:  # pragma: no cover
    PdfReader = None  # type: ignore[assignment]


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

    def _extract_from_new_predict_api(self, paddle_ocr: Any, file_path: str) -> OCRResult | None:
        try:
            result = paddle_ocr.predict(file_path)
            if not result:
                return None

            raw_words: list[str] = []
            raw_bbox: list[list[list[float]]] = []
            raw_conf: list[float] = []

            for res in result:
                texts = res.get("rec_texts") or []
                polys = res.get("dt_polys") or []
                scores = res.get("rec_scores") or []
                for score, poly, text in zip(scores, polys, texts):
                    s = float(score or 0.0)
                    if s >= settings.ocr_min_confidence:
                        raw_words.append(str(text or ""))
                        raw_bbox.append(poly.tolist() if hasattr(poly, "tolist") else poly)
                        raw_conf.append(s)

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
            raw_words: list[str] = []
            raw_bbox: list[list[list[float]]] = []
            raw_conf: list[float] = []
            for block in ocr_out or []:
                for line in block or []:
                    if not isinstance(line, (list, tuple)) or len(line) < 2:
                        continue
                    poly = line[0]
                    rec = line[1]
                    text = str(rec[0] or "").strip() if isinstance(rec, (list, tuple)) else ""
                    score = float(rec[1] or 0.0) if isinstance(rec, (list, tuple)) and len(rec) > 1 else 0.0
                    if score >= settings.ocr_min_confidence:
                        raw_words.append(text)
                        raw_bbox.append(poly)
                        raw_conf.append(score)

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
