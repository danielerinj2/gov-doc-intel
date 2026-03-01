from __future__ import annotations

from typing import Any

from app.infra.authenticity_adapter import AuthenticityAdapter
from app.infra.fraud_adapter import FraudCalibrationAdapter
from app.infra.groq_adapter import GroqAdapter
from app.infra.issuer_registry_adapter import IssuerRegistryAdapter
from app.infra.ocr_adapter import OCRAdapter
from app.pipeline.level2_modules import (
    DocumentClassificationModule,
    ExplainabilityAuditModule,
    FieldExtractionModule,
    FraudRiskEngineModule,
    IssuerRegistryVerificationModule,
    OCRPreprocessingModule,
    TemplatePolicyRuleEngineModule,
    ValidationModule,
    VisualAuthenticityModule,
)


class PipelineNodes:
    def __init__(self, groq: GroqAdapter) -> None:
        self.groq = groq
        self.ocr_module = OCRPreprocessingModule(ocr_adapter=OCRAdapter())
        self.classifier_module = DocumentClassificationModule()
        self.template_module = TemplatePolicyRuleEngineModule()
        self.extractor_module = FieldExtractionModule()
        self.validation_module = ValidationModule()
        self.visual_module = VisualAuthenticityModule(adapter=AuthenticityAdapter())
        self.fraud_module = FraudRiskEngineModule(calibrator=FraudCalibrationAdapter())
        self.issuer_module = IssuerRegistryVerificationModule(registry_adapter=IssuerRegistryAdapter())
        self.explainability_module = ExplainabilityAuditModule()

    def preprocessing_hashing(self, ctx: dict[str, Any]) -> dict[str, Any]:
        text = str(ctx.get("raw_text") or "")
        source_path = ctx.get("source_path")
        return self.ocr_module.preprocess(text, source_path=str(source_path) if source_path else None)

    def ocr_multi_script(self, ctx: dict[str, Any]) -> dict[str, Any]:
        return self.ocr_module.ocr(ctx["preprocessing_hashing"])

    def dedup_cross_submission(self, ctx: dict[str, Any]) -> dict[str, Any]:
        repo = ctx["repo"]
        tenant_id = ctx["tenant_id"]
        document_id = ctx["document_id"]
        dedup_hash = ctx["preprocessing_hashing"]["dedup_hash"]
        policy = ctx.get("tenant_policy") or repo.get_tenant_policy(tenant_id)
        cross_tenant = bool(policy.get("cross_tenant_fraud_enabled", False))

        if cross_tenant:
            prior_count = repo.count_by_hash_global(dedup_hash, exclude_document_id=document_id)
            scope = "GLOBAL"
        else:
            prior_count = repo.count_by_hash(tenant_id, dedup_hash, exclude_document_id=document_id)
            scope = "TENANT"

        return {
            "dedup_hash": dedup_hash,
            "duplicate_count": prior_count,
            "suspected_duplicate": prior_count > 0,
            "dedup_scope": scope,
        }

    def classification(self, ctx: dict[str, Any]) -> dict[str, Any]:
        return self.classifier_module.classify(
            ocr_out=ctx["ocr_multi_script"],
            preprocess_out=ctx["preprocessing_hashing"],
            groq=self.groq,
        )

    def stamps_seals(self, ctx: dict[str, Any]) -> dict[str, Any]:
        text = str(ctx["ocr_multi_script"].get("ocr_text", ""))
        return self.visual_module.detect_markers(text)

    def tamper_forensics(self, ctx: dict[str, Any]) -> dict[str, Any]:
        text = str(ctx["ocr_multi_script"].get("ocr_text", ""))
        return self.visual_module.forensics(text)

    def template_map(self, ctx: dict[str, Any]) -> dict[str, Any]:
        repo = ctx["repo"]
        tenant_id = ctx["tenant_id"]
        return self.template_module.resolve(
            repo=repo,
            tenant_id=tenant_id,
            classification_out=ctx["classification"],
        )

    def image_features(self, ctx: dict[str, Any]) -> dict[str, Any]:
        tamper_risk = float(ctx["tamper_forensics"]["tamper_risk"])
        quality = float(ctx["preprocessing_hashing"]["quality_score"])
        return {
            "texture_consistency": round(max(0.0, 1 - tamper_risk), 3),
            "quality_score": quality,
        }

    def field_extract(self, ctx: dict[str, Any]) -> dict[str, Any]:
        return self.extractor_module.extract(
            groq=self.groq,
            ocr_out=ctx["ocr_multi_script"],
            template_out=ctx["template_map"],
        )

    def issuer_registry_verification(self, ctx: dict[str, Any]) -> dict[str, Any]:
        return self.issuer_module.verify(
            tenant_id=str(ctx.get("tenant_id", "")),
            extraction_out=ctx["field_extract"],
            classification_out=ctx["classification"],
        )

    def validation(self, ctx: dict[str, Any]) -> dict[str, Any]:
        return self.validation_module.validate(
            extraction_out=ctx["field_extract"],
            issuer_precheck=ctx["issuer_registry_verification"],
            rule_bundle=ctx["template_map"],
            prefilled_data=dict(ctx.get("prefilled_data") or {}),
        )

    def fraud_behavioral_engine(self, ctx: dict[str, Any]) -> dict[str, Any]:
        return self.fraud_module.score(
            dedup_out=ctx["dedup_cross_submission"],
            forensics_out=ctx["tamper_forensics"],
            image_features_out=ctx["image_features"],
            issuer_out=ctx.get("issuer_registry_verification", {"registry_status": "UNVERIFIED"}),
        )

    def merge_node(self, ctx: dict[str, Any]) -> dict[str, Any]:
        validation = ctx["validation"]
        fraud = ctx["fraud_behavioral_engine"]
        registry = ctx["issuer_registry_verification"]
        auth = ctx["stamps_seals"]
        tamper = ctx["tamper_forensics"]

        confidence = round(
            (
                float(validation["extract_confidence"]) * 0.4
                + float(auth["authenticity_score"]) * 0.3
                + float(registry["registry_confidence"]) * 0.3
            ),
            3,
        )
        risk_score = round(
            min(1.0, float(fraud["fraud_score"]) * 0.7 + float(tamper["tamper_risk"]) * 0.3),
            3,
        )

        return {
            "confidence": confidence,
            "risk_score": risk_score,
            "risk_level": fraud.get("risk_level", "MEDIUM"),
            "validation": validation,
            "fraud": fraud,
            "registry": registry,
            "authenticity": auth,
            "tamper": tamper,
        }

    def decision_explainability(self, ctx: dict[str, Any]) -> dict[str, Any]:
        return self.explainability_module.decide(merge_out=ctx["merge_node"])

    def output_notification(self, ctx: dict[str, Any]) -> dict[str, Any]:
        d = ctx["decision_explainability"]
        return {
            "final_decision": d["decision"],
            "notify": d["decision"] in {"APPROVE", "REJECT", "REVIEW"},
            "notification_channel": "PORTAL",
            "risk_level": ctx["merge_node"].get("risk_level", "MEDIUM"),
        }
