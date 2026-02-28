from __future__ import annotations

import hashlib
from typing import Any

from app.infra.groq_adapter import GroqAdapter


class PipelineNodes:
    def __init__(self, groq: GroqAdapter) -> None:
        self.groq = groq

    def preprocessing_hashing(self, ctx: dict[str, Any]) -> dict[str, Any]:
        text = (ctx.get("raw_text") or "").strip()
        normalized = " ".join(text.split())
        dedup_hash = hashlib.sha256(normalized.lower().encode("utf-8")).hexdigest()
        return {
            "normalized_text": normalized,
            "dedup_hash": dedup_hash,
            "quality_score": round(min(1.0, max(0.2, len(normalized) / 4500)), 3),
        }

    def ocr_multi_script(self, ctx: dict[str, Any]) -> dict[str, Any]:
        # Placeholder OCR in MVP; text comes from ingestion.
        text = ctx["preprocessing_hashing"]["normalized_text"]
        return {
            "ocr_text": text,
            "bbox_available": False,
            "ocr_confidence": 0.9,
        }

    def dedup_cross_submission(self, ctx: dict[str, Any]) -> dict[str, Any]:
        repo = ctx["repo"]
        tenant_id = ctx["tenant_id"]
        document_id = ctx["document_id"]
        dedup_hash = ctx["preprocessing_hashing"]["dedup_hash"]
        prior_count = repo.count_by_hash(tenant_id, dedup_hash, exclude_document_id=document_id)
        return {
            "dedup_hash": dedup_hash,
            "duplicate_count": prior_count,
            "suspected_duplicate": prior_count > 0,
        }

    def classification(self, ctx: dict[str, Any]) -> dict[str, Any]:
        text = ctx["ocr_multi_script"]["ocr_text"]
        return self.groq.classify(text)

    def stamps_seals(self, ctx: dict[str, Any]) -> dict[str, Any]:
        t = ctx["ocr_multi_script"]["ocr_text"].lower()
        stamp = "stamp" in t or "seal" in t
        signature = "signature" in t or "signed" in t
        score = 0.45 + (0.25 if stamp else 0.0) + (0.2 if signature else 0.0)
        return {"stamp_present": stamp, "signature_present": signature, "authenticity_score": round(min(score, 1.0), 3)}

    def tamper_forensics(self, ctx: dict[str, Any]) -> dict[str, Any]:
        t = ctx["ocr_multi_script"]["ocr_text"].lower()
        tokens = ["tampered", "photoshop", "edited", "forged", "fake"]
        hits = [tok for tok in tokens if tok in t]
        risk = round(min(1.0, 0.15 + 0.17 * len(hits)), 3)
        return {"tamper_indicators": hits, "tamper_risk": risk}

    def template_map(self, ctx: dict[str, Any]) -> dict[str, Any]:
        dtype = str(ctx["classification"].get("document_type", "UNKNOWN")).lower()
        return {"template_id": f"tpl_{dtype}"}

    def image_features(self, ctx: dict[str, Any]) -> dict[str, Any]:
        tamper_risk = ctx["tamper_forensics"]["tamper_risk"]
        quality = ctx["preprocessing_hashing"]["quality_score"]
        return {"texture_consistency": round(max(0.0, 1 - tamper_risk), 3), "quality_score": quality}

    def field_extract(self, ctx: dict[str, Any]) -> dict[str, Any]:
        dtype = ctx["classification"].get("document_type", "UNKNOWN")
        text = ctx["ocr_multi_script"]["ocr_text"]
        return self.groq.extract(text, str(dtype))

    def fraud_behavioral_engine(self, ctx: dict[str, Any]) -> dict[str, Any]:
        dup = ctx["dedup_cross_submission"]["duplicate_count"]
        tamper_risk = ctx["tamper_forensics"]["tamper_risk"]
        image_quality = ctx["image_features"]["quality_score"]
        score = 0.2 + min(0.4, dup * 0.2) + (tamper_risk * 0.5) + (0.2 if image_quality < 0.35 else 0.0)
        score = round(min(score, 1.0), 3)
        return {"fraud_score": score, "behavioral_flags": {"duplicate_count": dup, "low_quality": image_quality < 0.35}}

    def issuer_registry_verification(self, ctx: dict[str, Any]) -> dict[str, Any]:
        fields = ctx["field_extract"].get("fields", {})
        has_issuer = bool(fields.get("issuer"))
        has_num = bool(fields.get("document_number"))
        if has_issuer and has_num:
            return {"registry_status": "MATCHED", "registry_confidence": 0.82}
        return {"registry_status": "UNVERIFIED", "registry_confidence": 0.3}

    def validation(self, ctx: dict[str, Any]) -> dict[str, Any]:
        extracted = ctx["field_extract"]
        missing = extracted.get("required_missing", []) or []
        extract_conf = float(extracted.get("confidence", 0.5))
        registry = ctx["issuer_registry_verification"]["registry_status"]
        valid = len(missing) == 0 and extract_conf >= 0.6 and registry == "MATCHED"
        return {
            "is_valid": valid,
            "missing_fields": missing,
            "extract_confidence": round(extract_conf, 3),
            "registry_status": registry,
        }

    def merge_node(self, ctx: dict[str, Any]) -> dict[str, Any]:
        validation = ctx["validation"]
        fraud = ctx["fraud_behavioral_engine"]
        registry = ctx["issuer_registry_verification"]
        auth = ctx["stamps_seals"]
        tamper = ctx["tamper_forensics"]

        confidence = round(
            (
                validation["extract_confidence"] * 0.4
                + auth["authenticity_score"] * 0.3
                + registry["registry_confidence"] * 0.3
            ),
            3,
        )
        risk_score = round(min(1.0, fraud["fraud_score"] * 0.7 + tamper["tamper_risk"] * 0.3), 3)

        return {
            "confidence": confidence,
            "risk_score": risk_score,
            "validation": validation,
            "fraud": fraud,
            "registry": registry,
            "authenticity": auth,
            "tamper": tamper,
        }

    def decision_explainability(self, ctx: dict[str, Any]) -> dict[str, Any]:
        merged = ctx["merge_node"]
        risk = merged["risk_score"]
        confidence = merged["confidence"]
        valid = merged["validation"]["is_valid"]

        if risk >= 0.78:
            decision = "REJECT"
        elif valid and confidence >= 0.72 and risk <= 0.35:
            decision = "APPROVE"
        else:
            decision = "REVIEW"

        explanation = {
            "decision": decision,
            "confidence": confidence,
            "risk_score": risk,
            "reason_codes": [
                f"VALID={valid}",
                f"REGISTRY={merged['registry']['registry_status']}",
                f"FRAUD={merged['fraud']['fraud_score']}",
                f"TAMPER={merged['tamper']['tamper_risk']}",
            ],
        }
        return explanation

    def output_notification(self, ctx: dict[str, Any]) -> dict[str, Any]:
        d = ctx["decision_explainability"]
        return {
            "final_decision": d["decision"],
            "notify": d["decision"] in {"APPROVE", "REJECT"},
            "notification_channel": "PORTAL",
        }
