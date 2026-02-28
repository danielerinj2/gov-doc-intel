from __future__ import annotations

import json
from typing import Any

from app.config import settings


class FraudCalibrationAdapter:
    """Calibrates aggregate fraud score from weighted components.

    FRAUD_CALIBRATION_WEIGHTS example:
    {"image":0.35,"behavioral":0.35,"issuer":0.30}
    """

    def __init__(self) -> None:
        self.weights = {"image": 0.35, "behavioral": 0.35, "issuer": 0.30}
        raw = settings.fraud_calibration_weights
        if raw:
            try:
                parsed = json.loads(raw)
                for key in ["image", "behavioral", "issuer"]:
                    if key in parsed:
                        self.weights[key] = float(parsed[key])
                total = sum(self.weights.values())
                if total > 0:
                    self.weights = {k: v / total for k, v in self.weights.items()}
            except Exception:
                pass

    def score(self, *, image_score: float, behavioral_score: float, issuer_score: float) -> float:
        out = (
            image_score * self.weights["image"]
            + behavioral_score * self.weights["behavioral"]
            + issuer_score * self.weights["issuer"]
        )
        return round(max(0.0, min(1.0, out)), 3)
