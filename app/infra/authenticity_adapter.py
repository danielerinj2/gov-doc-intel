from __future__ import annotations

from typing import Any

from app.config import settings


class AuthenticityAdapter:
    """Pluggable authenticity model adapter. Uses heuristic mode by default."""

    def __init__(self) -> None:
        self.backend = settings.authenticity_backend

    def infer_markers(self, *, text: str) -> dict[str, Any]:
        # Inference hook for future CV model serving.
        t = text.lower()
        stamp = any(tok in t for tok in ["stamp", "seal", "emblem"])
        signature = any(tok in t for tok in ["signature", "signed", "sign"])
        return {
            "stamp_present": stamp,
            "signature_present": signature,
            "confidence": 0.6 if stamp or signature else 0.35,
            "backend": self.backend,
        }

    def infer_forensics(self, *, text: str) -> dict[str, Any]:
        t = text.lower()
        signals = [tok for tok in ["tampered", "photoshop", "edited", "forged", "clone", "recompressed"] if tok in t]
        risk = min(1.0, 0.15 + 0.17 * len(signals))
        return {
            "signals": signals,
            "risk": round(risk, 3),
            "global_image_score": round(max(0.0, 1 - risk), 3),
            "backend": self.backend,
        }
