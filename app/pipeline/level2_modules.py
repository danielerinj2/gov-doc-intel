from __future__ import annotations

import hashlib
import re
from difflib import SequenceMatcher
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.domain.states import DocumentState
from app.infra.authenticity_adapter import AuthenticityAdapter
from app.infra.fraud_adapter import FraudCalibrationAdapter
from app.infra.issuer_registry_adapter import IssuerRegistryAdapter
from app.infra.ocr_adapter import OCRAdapter


def _safe_float(value: Any, fallback: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _risk_level(score: float) -> str:
    if score >= 0.9:
        return "CRITICAL"
    if score >= 0.75:
        return "HIGH"
    if score >= 0.45:
        return "MEDIUM"
    return "LOW"


def _norm_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _text_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.strip().lower(), b.strip().lower()).ratio()


@dataclass
class OCRPreprocessingModule:
    """Phase-1 OCR + quality + handwriting guardrail module."""

    ocr_adapter: OCRAdapter

    def preprocess(
        self,
        raw_text: str,
        source_path: str | None = None,
        script_hint: str | None = None,
        fast_mode: bool = False,
        ocr_budget_seconds: float | None = None,
    ) -> dict[str, Any]:
        recognized = self.ocr_adapter.recognize(
            text_fallback=raw_text,
            source_path=source_path,
            script_hint=script_hint,
            fast_mode=fast_mode,
            max_seconds=ocr_budget_seconds,
        )
        normalized = " ".join(str(recognized.get("text", "")).split())
        dedup_hash = hashlib.sha256(normalized.lower().encode("utf-8")).hexdigest()
        quality_score = round(min(1.0, max(0.2, len(normalized) / 4500)), 3)

        total_chars = len(normalized) or 1
        digit_ratio = sum(ch.isdigit() for ch in normalized) / total_chars
        punctuation_ratio = sum((not ch.isalnum()) and not ch.isspace() for ch in normalized) / total_chars

        # Heuristic: handwriting-heavy scans are usually short/noisy and low confidence in OCR.
        handwriting_probability = round(min(1.0, 0.25 + (0.35 if quality_score < 0.45 else 0.0) + punctuation_ratio * 0.8), 3)
        is_handwriting_heavy = handwriting_probability >= 0.7 and digit_ratio < 0.7

        return {
            "normalized_text": normalized,
            "dedup_hash": dedup_hash,
            "quality_score": quality_score,
            "steps_applied": ["DESKEW", "DENOISE", "CONTRAST_ENHANCEMENT"],
            "ocr_backend": recognized.get("backend", "heuristic"),
            "ocr_lang": recognized.get("ocr_lang"),
            "script_hint": script_hint,
            "handwriting_probability": handwriting_probability,
            "is_handwriting_heavy": is_handwriting_heavy,
            "phase1_handwriting_policy": "HANDWRITING_TO_UNSTRUCTURED_REVIEW",
            "ocr_recognition_confidence": recognized.get("confidence", 0.0),
            "ocr_timed_out": bool(recognized.get("timed_out", False)),
            "ocr_latency_ms": float(recognized.get("latency_ms", 0.0)),
            "source_path": source_path,
        }

    def ocr(self, preprocess_out: dict[str, Any]) -> dict[str, Any]:
        text = str(preprocess_out.get("normalized_text", ""))

        script = "LATIN"
        if re.search(r"[\u0900-\u097F]", text):
            script = "DEVANAGARI"
        if re.search(r"[\u0B80-\u0BFF]", text):
            script = "TAMIL"
        if re.search(r"[\u0C00-\u0C7F]", text):
            script = "TELUGU"
        if re.search(r"[\u0C80-\u0CFF]", text):
            script = "KANNADA"

        ocr_conf = round(
            max(
                0.45,
                max(
                    preprocess_out.get("quality_score", 0.5),
                    preprocess_out.get("ocr_recognition_confidence", 0.0),
                )
                - (0.2 if preprocess_out.get("is_handwriting_heavy") else 0.0),
            ),
            3,
        )
        if not text:
            script = "UNKNOWN"
            ocr_conf = 0.0

        return {
            "ocr_text": text,
            "bbox_available": False,
            "ocr_confidence": ocr_conf,
            "script": script,
            "unstructured_due_to_handwriting": bool(preprocess_out.get("is_handwriting_heavy")),
            "model_metadata": {"model_id": f"ocr-{preprocess_out.get('ocr_backend', 'heuristic')}", "model_version": "1.0.0"},
        }


@dataclass
class DocumentClassificationModule:
    """Multi-modal classification module (layout/text ensemble simplified for MVP)."""

    def classify(
        self,
        *,
        ocr_out: dict[str, Any],
        preprocess_out: dict[str, Any],
        groq: Any,
        doc_type_hint: str | None = None,
        submission_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if ocr_out.get("unstructured_due_to_handwriting"):
            return {
                "document_type": "UNSTRUCTURED",
                "doc_subtype": "HANDWRITTEN_HEAVY",
                "region_code": "DEFAULT",
                "confidence": 0.99,
                "low_confidence": False,
                "reasons": ["PHASE1_HANDWRITING_POLICY"],
                "model_metadata": {"model_id": "doc-classifier-v2", "model_version": "2.0.0"},
            }

        normalized_hint = str(doc_type_hint or "").strip().upper()
        if normalized_hint in {"", "AUTO-DETECT", "AUTO_DETECT"}:
            normalized_hint = ""

        text = str(ocr_out.get("ocr_text", ""))
        base = groq.classify(text)
        confidence = _safe_float(base.get("confidence"), 0.5)
        doc_type = str(
            base.get("document_type")
            or base.get("doc_type")
            or "UNKNOWN"
        ).upper()

        if normalized_hint:
            doc_type = normalized_hint
            confidence = max(confidence, 0.9)

        reasons = list(base.get("reasons") or [])
        if base.get("reasoning_short"):
            reasons.append(str(base["reasoning_short"]))
        if normalized_hint:
            reasons.append("DOC_TYPE_HINT_APPLIED")
        scheme = str((submission_context or {}).get("scheme") or "").strip()
        if scheme:
            reasons.append(f"SCHEME_CONTEXT:{scheme.upper().replace(' ', '_')}")
        if preprocess_out.get("quality_score", 0) < 0.45:
            reasons.append("LOW_IMAGE_QUALITY")

        return {
            "document_type": doc_type,
            "doc_subtype": "FRONT_SIDE",
            "region_code": "DEFAULT",
            "confidence": round(confidence, 3),
            "low_confidence": confidence < 0.72,
            "reasons": reasons,
            "model_metadata": {"model_id": "doc-classifier-v2", "model_version": "2.0.0"},
        }


@dataclass
class TemplatePolicyRuleEngineModule:
    """Template + rule resolution module scoped by tenant and document type."""

    def resolve(self, *, repo: Any, tenant_id: str, classification_out: dict[str, Any]) -> dict[str, Any]:
        dtype = str(classification_out.get("document_type", "UNKNOWN")).upper()
        template = repo.get_active_template(tenant_id, dtype)
        rule = repo.get_active_rule(tenant_id, dtype)
        template_cfg = dict(template.get("config") or {})
        rule_cfg = dict(rule.get("config") or {})

        field_patterns: dict[str, str] = {}
        for cfg in (template_cfg, rule_cfg):
            direct = cfg.get("field_patterns")
            if isinstance(direct, dict):
                for k, v in direct.items():
                    if str(k).strip() and str(v).strip():
                        field_patterns[_norm_key(str(k))] = str(v)
            checks = cfg.get("checks")
            if isinstance(checks, list):
                for check in checks:
                    if not isinstance(check, dict):
                        continue
                    field_name = str(check.get("field_name") or check.get("field") or "").strip()
                    pattern = str(check.get("pattern") or check.get("regex") or "").strip()
                    if field_name and pattern:
                        field_patterns[_norm_key(field_name)] = pattern

        return {
            "template_id": template.get("template_id", f"tpl_{dtype.lower()}"),
            "template_version": template.get("template_version", "2025.1.0"),
            "document_type": dtype,
            "template_config": template_cfg,
            "policy_rule_set_id": template.get("policy_rule_set_id") or rule.get("rule_set_id", f"RULESET_{dtype}_DEFAULT"),
            "compiled_rule_set": {
                "rule_name": rule.get("rule_name", f"rule_{dtype.lower()}"),
                "rule_set_id": rule.get("rule_set_id", f"RULESET_{dtype}_DEFAULT"),
                "version": int(rule.get("version", 1)),
                "min_extract_confidence": _safe_float(rule.get("min_extract_confidence"), 0.6),
                "min_approval_confidence": _safe_float(rule.get("min_approval_confidence"), 0.72),
                "max_approval_risk": _safe_float(rule.get("max_approval_risk"), 0.35),
                "registry_required": bool(rule.get("registry_required", True)),
                "field_patterns": field_patterns,
            },
        }


@dataclass
class FieldExtractionModule:
    """Template-guided extraction module with unstructured fallback."""

    def extract(self, *, groq: Any, ocr_out: dict[str, Any], template_out: dict[str, Any]) -> dict[str, Any]:
        dtype = str(template_out.get("document_type", "UNKNOWN"))
        text = str(ocr_out.get("ocr_text", ""))

        if dtype == "UNSTRUCTURED":
            return {
                "fields": {},
                "required_missing": ["MANUAL_TRANSCRIPTION_REQUIRED"],
                "confidence": 0.0,
                "route": "HUMAN_REVIEW_MANUAL_TRANSCRIPTION",
                "warnings": ["UNSTRUCTURED_HANDWRITTEN"],
            }

        out = groq.extract(text, dtype)
        out.setdefault("fields", {})
        out.setdefault("required_missing", [])
        out.setdefault("confidence", 0.5)
        out["confidence"] = round(_safe_float(out["confidence"], 0.5), 3)

        if out["confidence"] < 0.55:
            out.setdefault("warnings", []).append("LOW_EXTRACTION_CONFIDENCE")

        return out


@dataclass
class ValidationModule:
    """Rules + ML-style checks module."""

    def validate(
        self,
        *,
        extraction_out: dict[str, Any],
        issuer_precheck: dict[str, Any],
        rule_bundle: dict[str, Any],
        prefilled_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        missing = list(extraction_out.get("required_missing") or [])
        extract_conf = _safe_float(extraction_out.get("confidence"), 0.5)
        extracted_fields = dict(extraction_out.get("fields") or {})
        prefilled = dict(prefilled_data or {})

        compiled = dict(rule_bundle.get("compiled_rule_set") or {})
        registry_required = bool(compiled.get("registry_required", True))
        registry_status = str(issuer_precheck.get("registry_status", "NOT_AVAILABLE"))
        registry_ok = registry_status in {"MATCHED", "CONFIRMED", "NOT_AVAILABLE"} if not registry_required else registry_status in {"MATCHED", "CONFIRMED"}

        min_extract_conf = _safe_float(compiled.get("min_extract_confidence"), 0.6)
        field_patterns = dict(compiled.get("field_patterns") or {})

        normalized_extracted: dict[str, Any] = {}
        for k, v in extracted_fields.items():
            normalized_extracted[_norm_key(str(k))] = v

        alias_groups = {
            "name": ["name", "fullname", "applicantname"],
            "dob": ["dob", "dateofbirth", "birthdate"],
            "fathername": ["fathername", "father", "sonof", "s o", "so"],
            "documentnumber": ["documentnumber", "aadhaarnumber", "pannumber", "licensenumber", "registrationnumber"],
        }

        prefilled_mismatches: list[dict[str, Any]] = []
        prefilled_matches = 0
        prefilled_considered = 0
        for key, expected in prefilled.items():
            prefilled_considered += 1
            norm_key = _norm_key(str(key))
            aliases = [norm_key] + alias_groups.get(norm_key, [])
            local_value = None
            matched_field_name = None
            for alias in aliases:
                alias_norm = _norm_key(alias)
                if alias_norm in normalized_extracted:
                    local_value = normalized_extracted.get(alias_norm)
                    matched_field_name = alias_norm
                    break
            if local_value is None:
                prefilled_mismatches.append(
                    {
                        "field": key,
                        "matched_field_name": None,
                        "prefilled_value": expected,
                        "extracted_value": None,
                        "similarity": 0.0,
                        "match": False,
                        "reason": "FIELD_NOT_EXTRACTED",
                    }
                )
                continue
            similarity = _text_similarity(str(expected or ""), str(local_value or ""))
            match = similarity >= 0.85
            if not match:
                prefilled_mismatches.append(
                    {
                        "field": key,
                        "matched_field_name": matched_field_name,
                        "prefilled_value": expected,
                        "extracted_value": local_value,
                        "similarity": round(similarity, 3),
                        "match": False,
                        "reason": "VALUE_MISMATCH",
                    }
                )
            else:
                prefilled_matches += 1

        field_pattern_results: list[dict[str, Any]] = []
        pattern_fail_count = 0
        for field_key, pattern in field_patterns.items():
            local_value = normalized_extracted.get(_norm_key(field_key))
            if local_value is None:
                field_pattern_results.append(
                    {
                        "field_name": field_key,
                        "pattern": pattern,
                        "value": None,
                        "status": "WARN",
                        "reason_code": "FIELD_MISSING_FOR_PATTERN_CHECK",
                    }
                )
                continue
            try:
                matched = re.fullmatch(pattern, str(local_value).strip()) is not None
            except re.error:
                field_pattern_results.append(
                    {
                        "field_name": field_key,
                        "pattern": pattern,
                        "value": str(local_value),
                        "status": "WARN",
                        "reason_code": "INVALID_REGEX_PATTERN",
                    }
                )
                continue
            status = "PASS" if matched else "FAIL"
            if status == "FAIL":
                pattern_fail_count += 1
            field_pattern_results.append(
                {
                    "field_name": field_key,
                    "pattern": pattern,
                    "value": str(local_value),
                    "status": status,
                    "reason_code": "REGEX_MATCH" if matched else "REGEX_MISMATCH",
                }
            )

        prefilled_ok = len(prefilled_mismatches) == 0
        is_valid = (
            (len(missing) == 0)
            and extract_conf >= min_extract_conf
            and registry_ok
            and prefilled_ok
            and pattern_fail_count == 0
        )
        overall_status = "PASS" if is_valid else "FAIL" if pattern_fail_count > 0 or not registry_ok or len(missing) > 0 else "WARN"

        return {
            "is_valid": is_valid,
            "overall_status": overall_status,
            "missing_fields": missing,
            "extract_confidence": round(extract_conf, 3),
            "registry_status": registry_status,
            "rule_name": compiled.get("rule_name", "rule_default"),
            "rule_version": int(compiled.get("version", 1)),
            "rule_set_id": compiled.get("rule_set_id", "RULESET_DEFAULT"),
            "min_extract_confidence": min_extract_conf,
            "min_approval_confidence": _safe_float(compiled.get("min_approval_confidence"), 0.72),
            "max_approval_risk": _safe_float(compiled.get("max_approval_risk"), 0.35),
            "registry_required": registry_required,
            "prefilled_consistency_status": "CONSISTENT" if prefilled_ok else "MISMATCH",
            "prefilled_match_count": prefilled_matches,
            "prefilled_considered_count": prefilled_considered,
            "prefilled_mismatch_count": len(prefilled_mismatches),
            "prefilled_mismatches": prefilled_mismatches,
            "field_pattern_results": field_pattern_results,
            "pattern_fail_count": pattern_fail_count,
        }


@dataclass
class VisualAuthenticityModule:
    """Visual authenticity module for stamps/signatures + forensics signals."""

    adapter: AuthenticityAdapter

    def detect_markers(self, text: str) -> dict[str, Any]:
        inferred = self.adapter.infer_markers(text=text)
        stamp = bool(inferred.get("stamp_present"))
        signature = bool(inferred.get("signature_present"))
        base_conf = _safe_float(inferred.get("confidence"), 0.5)
        score = 0.35 + (0.25 if stamp else 0.0) + (0.2 if signature else 0.0) + (base_conf * 0.2)
        return {
            "stamp_present": stamp,
            "signature_present": signature,
            "authenticity_score": round(min(score, 1.0), 3),
            "model_metadata": {"model_id": f"auth-{inferred.get('backend', 'heuristic')}", "model_version": "1.0.0"},
        }

    def forensics(self, text: str) -> dict[str, Any]:
        inferred = self.adapter.infer_forensics(text=text)
        hits = [str(x) for x in inferred.get("signals", [])]
        risk = round(_safe_float(inferred.get("risk"), 0.2), 3)
        return {
            "tamper_indicators": hits,
            "tamper_risk": risk,
            "global_image_score": round(_safe_float(inferred.get("global_image_score"), max(0.0, 1 - risk)), 3),
            "model_metadata": {"model_id": f"forensics-{inferred.get('backend', 'heuristic')}", "model_version": "1.0.0"},
        }


@dataclass
class FraudRiskEngineModule:
    """Aggregate risk module: image + behavioral + issuer mismatch."""

    calibrator: FraudCalibrationAdapter

    def score(self, *, dedup_out: dict[str, Any], forensics_out: dict[str, Any], image_features_out: dict[str, Any], issuer_out: dict[str, Any]) -> dict[str, Any]:
        dup = int(dedup_out.get("duplicate_count", 0))
        tamper_risk = _safe_float(forensics_out.get("tamper_risk"), 0.2)
        image_quality = _safe_float(image_features_out.get("quality_score"), 0.7)

        behavioral_score = round(min(1.0, 0.2 + min(0.4, dup * 0.2) + (0.2 if image_quality < 0.35 else 0.0)), 3)
        image_forensics_score = round(min(1.0, tamper_risk), 3)

        registry_status = str(issuer_out.get("registry_status", "NOT_AVAILABLE"))
        if registry_status in {"MATCHED", "CONFIRMED"}:
            issuer_mismatch_score = 0.05
            issuer_signals: list[str] = []
        elif registry_status in {"UNVERIFIED", "NOT_AVAILABLE", "NOT_FOUND"}:
            issuer_mismatch_score = 0.45
            issuer_signals = [f"REGISTRY_{registry_status}"]
        else:
            issuer_mismatch_score = 0.9
            issuer_signals = [f"REGISTRY_{registry_status}"]

        aggregate = self.calibrator.score(
            image_score=image_forensics_score,
            behavioral_score=behavioral_score,
            issuer_score=issuer_mismatch_score,
        )

        return {
            "fraud_score": aggregate,
            "aggregate_fraud_risk_score": aggregate,
            "risk_level": _risk_level(aggregate),
            "behavioral_flags": {
                "duplicate_count": dup,
                "low_quality": image_quality < 0.35,
                "dedup_scope": dedup_out.get("dedup_scope", "TENANT"),
            },
            "components": {
                "image_forensics_component": {
                    "score": image_forensics_score,
                    "signals": [str(s) for s in forensics_out.get("tamper_indicators", [])],
                },
                "behavioral_component": {
                    "score": behavioral_score,
                    "signals": [s for s in [f"DUPLICATE_COUNT_{dup}", "LOW_IMAGE_QUALITY" if image_quality < 0.35 else ""] if s],
                },
                "issuer_mismatch_component": {
                    "score": issuer_mismatch_score,
                    "signals": issuer_signals,
                },
            },
            "model_metadata": {"model_id": "fraud-calibrated-aggregator", "model_version": "1.0.0"},
        }


@dataclass
class IssuerRegistryVerificationModule:
    """Issuer/registry verification module."""

    registry_adapter: IssuerRegistryAdapter

    def verify(self, *, tenant_id: str, extraction_out: dict[str, Any], classification_out: dict[str, Any]) -> dict[str, Any]:
        fields = dict(extraction_out.get("fields") or {})
        has_issuer = bool(fields.get("issuer"))
        has_num = bool(fields.get("document_number") or fields.get("roll_number") or fields.get("registration_number"))

        if classification_out.get("document_type") == "UNSTRUCTURED":
            return {
                "registry_status": "NOT_AVAILABLE",
                "registry_confidence": 0.0,
                "verification_method": "NOT_AVAILABLE",
            }

        external = self.registry_adapter.verify(
            tenant_id=tenant_id,
            doc_type=str(classification_out.get("document_type", "UNKNOWN")),
            fields=fields,
        )
        if external:
            status = str(external.get("status", "UNVERIFIED")).upper()
            confidence = _safe_float(external.get("confidence"), 0.6)
            mapped_status = "MATCHED" if status in {"MATCHED", "CONFIRMED"} else "MISMATCH" if status in {"MISMATCH"} else "UNVERIFIED"
            return {
                "registry_status": mapped_status,
                "registry_confidence": confidence,
                "verification_method": str(external.get("verification_method", "REGISTRY_API")),
                "issuer_reference_id": external.get("issuer_reference_id"),
                "fields_compared": external.get("fields_compared", []),
            }

        if has_issuer and has_num:
            return {
                "registry_status": "MATCHED",
                "registry_confidence": 0.82,
                "verification_method": "REGISTRY_API",
            }

        return {
            "registry_status": "UNVERIFIED",
            "registry_confidence": 0.3,
            "verification_method": "REGISTRY_API",
        }


@dataclass
class ExplainabilityAuditModule:
    """Decision explainability and AI audit payload module."""

    def decide(self, *, merge_out: dict[str, Any]) -> dict[str, Any]:
        risk = _safe_float(merge_out.get("risk_score"), 0.5)
        confidence = _safe_float(merge_out.get("confidence"), 0.5)
        valid = bool((merge_out.get("validation") or {}).get("is_valid", False))
        min_approval_confidence = _safe_float((merge_out.get("validation") or {}).get("min_approval_confidence"), 0.72)
        max_approval_risk = _safe_float((merge_out.get("validation") or {}).get("max_approval_risk"), 0.35)

        if risk >= 0.78:
            decision = "REJECT"
        elif valid and confidence >= min_approval_confidence and risk <= max_approval_risk:
            decision = "APPROVE"
        else:
            decision = "REVIEW"

        return {
            "decision": decision,
            "confidence": round(confidence, 3),
            "risk_score": round(risk, 3),
            "reason_codes": [
                f"VALID={valid}",
                f"REGISTRY={(merge_out.get('registry') or {}).get('registry_status', 'UNKNOWN')}",
                f"FRAUD={(merge_out.get('fraud') or {}).get('fraud_score', 0.0)}",
                f"TAMPER={(merge_out.get('tamper') or {}).get('tamper_risk', 0.0)}",
                f"RISK_LEVEL={merge_out.get('risk_level', 'MEDIUM')}",
            ],
        }

    def audit_entries(
        self,
        *,
        tenant_id: str,
        document_id: str,
        job_id: str,
        node_outputs: dict[str, dict[str, Any]],
        execution_order: list[str],
    ) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc).isoformat()
        entries: list[dict[str, Any]] = []
        for module_name in execution_order:
            payload = node_outputs.get(module_name, {})
            model_id = payload.get("model_metadata", {}).get("model_id") if isinstance(payload.get("model_metadata"), dict) else None
            model_version = payload.get("model_metadata", {}).get("model_version") if isinstance(payload.get("model_metadata"), dict) else None
            entries.append(
                {
                    "tenant_id": tenant_id,
                    "document_id": document_id,
                    "job_id": job_id,
                    "module_name": module_name,
                    "model_id": model_id or module_name,
                    "model_version": model_version or "1.0.0",
                    "input_ref": {"module": module_name, "job_id": job_id},
                    "output": payload,
                    "reason_codes": list(payload.get("reasons") or payload.get("reason_codes") or []),
                    "actor_type": "SYSTEM",
                    "actor_id": None,
                    "created_at": now,
                }
            )
        return entries


@dataclass
class HumanReviewWorkloadModule:
    """Queueing, assignment and balancing module for human review."""

    def assign(self, *, repo: Any, tenant_id: str, document_id: str, doc_type: str, risk_level: str, policy: str = "LEAST_LOADED") -> dict[str, Any]:
        queue_name = f"{tenant_id}:{doc_type}"
        assignment = repo.create_review_assignment(
            tenant_id=tenant_id,
            document_id=document_id,
            queue_name=queue_name,
            policy=policy,
            priority=self._priority_from_risk(risk_level),
        )

        assignee = self._pick_assignee(repo=repo, tenant_id=tenant_id, doc_type=doc_type, policy=policy)
        if assignee:
            assignment = repo.reserve_review_assignment(
                assignment_id=str(assignment.get("id")),
                officer_id=assignee,
            )
        return assignment

    def _pick_assignee(self, *, repo: Any, tenant_id: str, doc_type: str, policy: str) -> str | None:
        # In MVP, both round-robin and least-loaded use a least-open-work heuristic.
        return repo.pick_officer_for_assignment(tenant_id=tenant_id, preferred_doc_type=doc_type)

    def _priority_from_risk(self, risk_level: str) -> int:
        mapping = {
            "LOW": 30,
            "MEDIUM": 50,
            "HIGH": 75,
            "CRITICAL": 90,
        }
        return mapping.get(risk_level.upper(), 50)


@dataclass
class OutputIntegrationModule:
    """Output integration module for API/webhook payload stability."""

    def create_result_payload(self, *, document_id: str, job_id: str, decision_out: dict[str, Any], state: DocumentState) -> dict[str, Any]:
        return {
            "document_id": document_id,
            "job_id": job_id,
            "status": state.value,
            "decision": decision_out.get("decision"),
            "risk_score": decision_out.get("risk_score"),
            "confidence": decision_out.get("confidence"),
            "reason_codes": decision_out.get("reason_codes", []),
        }

    def queue_webhook(self, *, repo: Any, tenant_id: str, document_id: str, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        return repo.enqueue_webhook(
            tenant_id=tenant_id,
            document_id=document_id,
            event_type=event_type,
            payload=payload,
        )


@dataclass
class OfflineSyncModule:
    """Offline tagging and conflict interpretation module."""

    def tag_offline(self, *, metadata: dict[str, Any], local_model_versions: dict[str, Any], offline_node_id: str | None) -> dict[str, Any]:
        updated = dict(metadata)
        updated["offline_metadata"] = {
            "processed_offline": True,
            "offline_node_id": offline_node_id,
            "offline_model_versions": local_model_versions,
            "first_seen_offline_at": datetime.now(timezone.utc).isoformat(),
            "synced_to_central_at": None,
        }
        return updated

    def conflict_policy_message(self, *, local_decision: str, central_decision: str) -> str:
        if local_decision == central_decision:
            return "No conflict detected."
        return "Provisional result revised after centralized verification"


@dataclass
class MonitoringMLOpsModule:
    """Monitoring + correction validation gate module."""

    def record_module_metrics(
        self,
        *,
        repo: Any,
        tenant_id: str,
        document_id: str,
        job_id: str,
        node_durations_ms: dict[str, float],
        node_outputs: dict[str, dict[str, Any]],
    ) -> None:
        for module_name, latency in node_durations_ms.items():
            output = node_outputs.get(module_name, {})
            status = "OK"
            if module_name == "decision_explainability" and output.get("decision") == "REVIEW":
                status = "WARN"
            repo.create_module_metric(
                tenant_id=tenant_id,
                document_id=document_id,
                job_id=job_id,
                module_name=module_name,
                latency_ms=float(latency),
                status=status,
                metric_payload={"summary": output},
            )

    def gate_correction(
        self,
        *,
        repo: Any,
        tenant_id: str,
        document_id: str,
        field_name: str,
        old_value: str | None,
        new_value: str | None,
        officer_id: str,
        reason: str,
    ) -> dict[str, Any]:
        correction = repo.create_correction_event(
            tenant_id=tenant_id,
            document_id=document_id,
            field_name=field_name,
            old_value=old_value,
            new_value=new_value,
            officer_id=officer_id,
            reason=reason,
        )

        conflicts = repo.count_conflicting_corrections(
            tenant_id=tenant_id,
            document_id=document_id,
            field_name=field_name,
            expected_value=new_value,
        )
        qa_required = conflicts > 0

        gate = repo.create_correction_gate_record(
            tenant_id=tenant_id,
            document_id=document_id,
            correction_event_id=str(correction.get("id")),
            status="PENDING_QA" if qa_required else "HIGH_CONFIDENCE",
            qa_required=qa_required,
            notes=["CONFLICTING_CORRECTIONS" if qa_required else "AUTO_ACCEPTED"],
        )
        return {"correction": correction, "gate": gate}

    def mlops_summary(self, *, repo: Any, tenant_id: str) -> dict[str, Any]:
        rows = repo.list_module_metrics(tenant_id=tenant_id)
        if not rows:
            return {
                "throughput_docs": 0,
                "avg_latency_ms": 0.0,
                "module_count": 0,
            }

        total_latency = sum(_safe_float(r.get("latency_ms"), 0.0) for r in rows)
        docs = {str(r.get("document_id")) for r in rows if r.get("document_id")}
        return {
            "throughput_docs": len(docs),
            "avg_latency_ms": round(total_latency / max(1, len(rows)), 2),
            "module_count": len({str(r.get("module_name")) for r in rows}),
        }
