from __future__ import annotations

import csv
import hashlib
import io
import time
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.config import settings
from app.contracts.schemas import (
    CitizenCommEvent,
    CitizenCommunicationRecord,
    ClassificationOutput,
    DocumentRecord,
    ExplainabilityRecord,
    ExtractionOutput,
    ExtractedField,
    FieldComparison,
    DocumentCheckResult,
    FieldValidationResult,
    FraudComponent,
    FraudRiskComponents,
    FraudRiskOutput,
    HumanReviewRecord,
    ImageForensics,
    IngestionRecord,
    IngestionSubmittedBy,
    IssuerResponseMetadata,
    IssuerVerificationOutput,
    MLCheckResult,
    MLTrainingFlagsRecord,
    ModelMetadata,
    OCRLine,
    OCROutput,
    OCRPage,
    OCRWord,
    OfflineMetadataRecord,
    PreprocessingMetadata,
    RetentionPolicyRecord,
    RuleResult,
    ScoreRecord,
    StateHistoryEvent,
    StateMachineRecord,
    TemplateReference,
    ValidationModelMetadata,
    ValidationOutput,
    VisualAuthenticityOutput,
    VisualMarkerResult,
)
from app.domain.models import Dispute, Document, DocumentEvent, HumanReviewEvent, Officer
from app.domain.state_machine import StateMachine
from app.domain.states import DocumentState
from app.events.backends import build_event_bus
from app.events.contracts import BRANCH_MODULES, build_event_envelope
from app.infra.groq_adapter import GroqAdapter
from app.infra.repositories import (
    ADMIN_ROLES,
    REVIEW_ROLES,
    ROLE_VERIFIER,
    WRITER_ROLES,
    Repository,
)
from app.pipeline.dag import DAG, Node
from app.pipeline.level2_modules import (
    ExplainabilityAuditModule,
    HumanReviewWorkloadModule,
    MonitoringMLOpsModule,
    OutputIntegrationModule,
)
from app.pipeline.nodes import PipelineNodes
from app.services.dr_service import DRService
from app.services.notification_service import NotificationService


class DocumentService:
    _rate_windows: dict[tuple[str, str], list[float]] = defaultdict(list)

    def __init__(self) -> None:
        self.repo = Repository()
        self.sm = StateMachine()
        self.nodes = PipelineNodes(GroqAdapter())
        self.bus = build_event_bus()
        self.notification_service = NotificationService(self.repo)
        self.dr_service = DRService()
        self.explainability_audit_module = ExplainabilityAuditModule()
        self.review_workload_module = HumanReviewWorkloadModule()
        self.output_integration_module = OutputIntegrationModule()
        self.monitoring_mlops_module = MonitoringMLOpsModule()
        self.dag = DAG(
            [
                Node("preprocessing_hashing", self.nodes.preprocessing_hashing, []),
                Node("ocr_multi_script", self.nodes.ocr_multi_script, ["preprocessing_hashing"]),
                Node("dedup_cross_submission", self.nodes.dedup_cross_submission, ["preprocessing_hashing"]),
                Node("classification", self.nodes.classification, ["ocr_multi_script"]),
                Node("stamps_seals", self.nodes.stamps_seals, ["ocr_multi_script"]),
                Node("tamper_forensics", self.nodes.tamper_forensics, ["ocr_multi_script"]),
                Node("template_map", self.nodes.template_map, ["classification"]),
                Node("image_features", self.nodes.image_features, ["tamper_forensics", "preprocessing_hashing"]),
                Node("field_extract", self.nodes.field_extract, ["template_map", "ocr_multi_script", "classification"]),
                Node("fraud_behavioral_engine", self.nodes.fraud_behavioral_engine, ["dedup_cross_submission", "tamper_forensics", "image_features"]),
                Node("issuer_registry_verification", self.nodes.issuer_registry_verification, ["field_extract", "classification"]),
                Node("validation", self.nodes.validation, ["field_extract", "issuer_registry_verification"]),
                Node("merge_node", self.nodes.merge_node, ["validation", "fraud_behavioral_engine", "issuer_registry_verification", "stamps_seals", "tamper_forensics"]),
                Node("decision_explainability", self.nodes.decision_explainability, ["merge_node"]),
                Node("output_notification", self.nodes.output_notification, ["decision_explainability"]),
            ]
        )

    def register_officer(self, officer_id: str, tenant_id: str, role: str, display_name: str | None = None) -> dict[str, Any]:
        self.repo.ensure_tenant(tenant_id=tenant_id, display_name=display_name or tenant_id)
        officer = Officer(officer_id=officer_id, tenant_id=tenant_id, role=role)
        row = self.repo.upsert_officer(officer)
        self.repo.upsert_tenant_membership(user_id=officer_id, tenant_id=tenant_id, role=role, status="ACTIVE")
        self.repo.get_tenant_policy(tenant_id, create_if_missing=True)
        return row

    def get_officer_profile(self, officer_id: str) -> dict[str, Any] | None:
        return self.repo.get_officer(officer_id)

    def supabase_persistence_probe(self, *, tenant_id: str, officer_id: str) -> dict[str, Any]:
        profile = self._authorize(officer_id, tenant_id, None)
        if not self.repo.using_supabase:
            return {
                "ok": False,
                "persistence": "memory",
                "message": "Supabase is not active; currently using in-memory repository.",
            }

        # Non-destructive write/read checks.
        upserted = self.register_officer(
            officer_id=officer_id,
            tenant_id=tenant_id,
            role=str(profile.get("role", "verifier")),
            display_name=tenant_id,
        )
        policy = self.repo.get_tenant_policy(tenant_id, create_if_missing=True)
        record = self.repo.get_officer(officer_id)
        return {
            "ok": bool(record) and bool(policy),
            "persistence": "supabase",
            "officer_id": upserted.get("officer_id"),
            "tenant_id": upserted.get("tenant_id"),
            "schema_ready": {
                "part2": self.repo.part2_schema_ready,
                "part3": self.repo.part3_schema_ready,
                "part4": self.repo.part4_schema_ready,
                "part5": self.repo.part5_schema_ready,
            },
        }

    def create_tenant_api_key(self, tenant_id: str, officer_id: str, key_label: str, raw_key: str) -> dict[str, Any]:
        self._authorize(officer_id, tenant_id, ADMIN_ROLES)
        return self.repo.create_tenant_api_key(tenant_id, key_label, raw_key)

    def list_officers(self, tenant_id: str, officer_id: str) -> list[dict[str, Any]]:
        self._authorize(officer_id, tenant_id, ADMIN_ROLES)
        return self.repo.list_officers(tenant_id)

    def upsert_officer_account(
        self,
        *,
        tenant_id: str,
        admin_officer_id: str,
        target_officer_id: str,
        role: str,
        status: str = "ACTIVE",
    ) -> dict[str, Any]:
        self._authorize(admin_officer_id, tenant_id, ADMIN_ROLES)
        officer = Officer(
            officer_id=target_officer_id,
            tenant_id=tenant_id,
            role=role,
            status=status,
        )
        return self.repo.upsert_officer(officer)

    def list_tenant_templates(self, tenant_id: str, officer_id: str, document_type: str | None = None) -> list[dict[str, Any]]:
        self._authorize(officer_id, tenant_id, ADMIN_ROLES)
        return self.repo.list_tenant_templates(tenant_id, document_type=document_type)

    def save_tenant_template(
        self,
        *,
        tenant_id: str,
        officer_id: str,
        document_type: str,
        template_id: str,
        version: int,
        template_version: str,
        policy_rule_set_id: str | None,
        config: dict[str, Any],
        doc_subtype: str | None = None,
        region_code: str | None = None,
        description: str | None = None,
        lifecycle_status: str = "ACTIVE",
        is_active: bool = True,
    ) -> dict[str, Any]:
        self._authorize(officer_id, tenant_id, ADMIN_ROLES)
        return self.repo.upsert_tenant_template(
            tenant_id=tenant_id,
            document_type=document_type,
            template_id=template_id,
            version=version,
            template_version=template_version,
            policy_rule_set_id=policy_rule_set_id,
            config=config,
            doc_subtype=doc_subtype,
            region_code=region_code,
            description=description,
            lifecycle_status=lifecycle_status,
            is_active=is_active,
        )

    def list_tenant_rules(self, tenant_id: str, officer_id: str, document_type: str | None = None) -> list[dict[str, Any]]:
        self._authorize(officer_id, tenant_id, ADMIN_ROLES)
        return self.repo.list_tenant_rules(tenant_id, document_type=document_type)

    def save_tenant_rule(
        self,
        *,
        tenant_id: str,
        officer_id: str,
        document_type: str,
        rule_name: str,
        version: int,
        rule_set_id: str,
        min_extract_confidence: float,
        min_approval_confidence: float,
        max_approval_risk: float,
        registry_required: bool,
        config: dict[str, Any],
        is_active: bool = True,
    ) -> dict[str, Any]:
        self._authorize(officer_id, tenant_id, ADMIN_ROLES)
        return self.repo.upsert_tenant_rule(
            tenant_id=tenant_id,
            document_type=document_type,
            rule_name=rule_name,
            version=version,
            rule_set_id=rule_set_id,
            min_extract_confidence=min_extract_confidence,
            min_approval_confidence=min_approval_confidence,
            max_approval_risk=max_approval_risk,
            registry_required=registry_required,
            config=config,
            is_active=is_active,
        )

    def create_document(
        self,
        tenant_id: str,
        citizen_id: str,
        file_name: str,
        raw_text: str,
        officer_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._authorize(officer_id, tenant_id, WRITER_ROLES)
        self._enforce_daily_quota(tenant_id)

        policy = self.repo.get_tenant_policy(tenant_id)
        retention_days = int(policy.get("data_retention_days", 365))
        retention_until = (datetime.now(timezone.utc) + timedelta(days=retention_days)).isoformat()
        bucket_name = self.repo.get_tenant_bucket(tenant_id)

        m = dict(metadata or {})
        ingestion = dict(m.get("ingestion") or {})

        source = str(ingestion.get("source") or m.get("source") or "ONLINE_PORTAL")
        if source not in {"ONLINE_PORTAL", "SERVICE_CENTER", "BATCH_UPLOAD", "API"}:
            source = "ONLINE_PORTAL"

        submitted_by_raw = ingestion.get("submitted_by")
        if isinstance(submitted_by_raw, dict):
            actor_type = str(submitted_by_raw.get("actor_type") or "OPERATOR").upper()
            if actor_type not in {"CITIZEN", "OPERATOR", "SYSTEM"}:
                actor_type = "OPERATOR"
            actor_id = str(submitted_by_raw.get("actor_id") or officer_id)
        elif isinstance(submitted_by_raw, str) and submitted_by_raw.strip():
            actor_type = "OPERATOR"
            actor_id = submitted_by_raw.strip()
        else:
            actor_type = "OPERATOR"
            actor_id = officer_id

        received_at = str(ingestion.get("received_at") or datetime.now(timezone.utc).isoformat())
        original_file_uri = ingestion.get("original_file_uri") or m.get("original_file_uri")

        perceptual_hash = str(ingestion.get("perceptual_hash") or "").strip()
        if not perceptual_hash:
            try:
                if isinstance(original_file_uri, str) and original_file_uri:
                    candidate = Path(original_file_uri)
                    if candidate.exists() and candidate.is_file():
                        perceptual_hash = hashlib.sha256(candidate.read_bytes()).hexdigest()
            except Exception:
                perceptual_hash = ""

        if not perceptual_hash:
            if raw_text:
                perceptual_hash = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
            else:
                perceptual_hash = hashlib.sha256(file_name.encode("utf-8")).hexdigest()

        dedup_matches = ingestion.get("dedup_matches")
        if not isinstance(dedup_matches, list):
            dedup_matches = []

        ingestion.update(
            {
                "source": source,
                "submitted_by": {"actor_type": actor_type, "actor_id": actor_id},
                "received_at": received_at,
                "original_file_uri": original_file_uri,
                "perceptual_hash": perceptual_hash,
                "dedup_matches": dedup_matches,
            }
        )
        m["ingestion"] = ingestion

        m.setdefault(
            "state_history",
            [
                {
                    "from_state": None,
                    "to_state": DocumentState.RECEIVED.value,
                    "at": received_at,
                    "by": "SYSTEM",
                    "reason": "INITIAL_INGEST",
                }
            ],
        )
        m.setdefault("human_review", {"assigned_to_officer_id": None, "assigned_at": None, "review_events": []})
        m.setdefault("citizen_communication", {"preferred_channels": ["SMS", "PORTAL"], "events": []})
        m.setdefault(
            "offline_metadata",
            {
                "processed_offline": False,
                "offline_node_id": None,
                "offline_model_versions": {},
                "first_seen_offline_at": None,
                "synced_to_central_at": None,
            },
        )
        m.setdefault(
            "ml_training_flags",
            {
                "eligible_for_training": {
                    "ocr": True,
                    "classification": True,
                    "extraction": True,
                    "fraud": True,
                },
                "data_quality_notes": [],
            },
        )
        m.setdefault(
            "retention_policy",
            {
                "policy_id": f"TENANT_{tenant_id}_DOC_RETAIN_{retention_days}D",
                "retention_until": retention_until,
                "archival_status": "ACTIVE",
            },
        )
        m.setdefault("tenant_storage_bucket", bucket_name)

        doc = Document(
            tenant_id=tenant_id,
            citizen_id=citizen_id,
            file_name=file_name,
            raw_text=raw_text,
            metadata=m,
            expires_at=retention_until,
            state=DocumentState.RECEIVED,
        )
        row = self.repo.create_document(doc)

        self._event(
            document_id=doc.id,
            tenant_id=tenant_id,
            actor_type="SYSTEM",
            actor_id=officer_id,
            event_type="document.received",
            payload={"file_name": file_name},
            reason="INITIAL_INGEST",
            policy_version=1,
            model_versions={"pipeline": "1.0.0"},
        )

        return row

    def _process_document_fast_scan(
        self,
        *,
        document_id: str,
        tenant_id: str,
        officer_id: str,
        max_latency_seconds: float | None,
    ) -> dict[str, Any]:
        self._authorize(officer_id, tenant_id, WRITER_ROLES)
        doc = self.repo.get_document(document_id, tenant_id=tenant_id)
        if not doc:
            raise ValueError("Document not found for tenant")

        job_id = str(uuid4())
        correlation_id = job_id
        policy_version = 1
        model_versions = {
            "ocr_model_id": "ocr-fast-scan-v1",
        }
        budget_seconds = (
            max(0.5, float(max_latency_seconds))
            if max_latency_seconds is not None
            else max(0.5, float(settings.ocr_fast_scan_budget_seconds))
        )
        started = time.perf_counter()

        try:
            self._transition(doc, DocumentState.PREPROCESSING, "SYSTEM", officer_id, "FAST_SCAN_PIPELINE_STARTED")

            seed = {
                "raw_text": doc.get("raw_text", ""),
                "source_path": ((doc.get("metadata") or {}).get("ingestion") or {}).get("original_file_uri"),
                "script_hint": ((doc.get("metadata") or {}).get("ingestion") or {}).get("script_hint"),
                "tenant_id": tenant_id,
                "document_id": doc["id"],
                "repo": self.repo,
                "fast_scan": True,
                "ocr_budget_seconds": max(0.2, budget_seconds - 0.4),
            }
            preprocess_out = self.nodes.preprocessing_hashing(seed)
            ocr_out = self.nodes.ocr_multi_script({"preprocessing_hashing": preprocess_out})

            self._event(
                document_id=doc["id"],
                tenant_id=tenant_id,
                actor_type="SYSTEM",
                actor_id=officer_id,
                event_type="document.preprocessed",
                payload={
                    "quality_score": preprocess_out.get("quality_score"),
                    "dedup_hash": preprocess_out.get("dedup_hash"),
                    "fast_scan": True,
                },
                reason="FAST_SCAN_PREPROCESSING_COMPLETE",
                policy_version=policy_version,
                model_versions=model_versions,
                correlation_id=correlation_id,
            )

            self._transition(doc, DocumentState.OCR_COMPLETE, "SYSTEM", officer_id, "FAST_SCAN_OCR_STAGE_COMPLETE")
            self._event(
                document_id=doc["id"],
                tenant_id=tenant_id,
                actor_type="SYSTEM",
                actor_id=officer_id,
                event_type="ocr.completed",
                payload={
                    "ocr_confidence": ocr_out.get("ocr_confidence"),
                    "ocr_latency_ms": preprocess_out.get("ocr_latency_ms"),
                    "ocr_timed_out": preprocess_out.get("ocr_timed_out"),
                    "fast_scan": True,
                },
                reason="FAST_SCAN_OCR_COMPLETE",
                policy_version=policy_version,
                model_versions=model_versions,
                correlation_id=correlation_id,
            )

            self._transition(doc, DocumentState.BRANCHED, "SYSTEM", officer_id, "FAST_SCAN_BRANCH_MARKER")
            self._transition(doc, DocumentState.MERGED, "SYSTEM", officer_id, "FAST_SCAN_MERGE_MARKER")
            self._transition(doc, DocumentState.WAITING_FOR_REVIEW, "SYSTEM", officer_id, "FAST_SCAN_REVIEW_REQUIRED")

            elapsed_ms = (time.perf_counter() - started) * 1000
            quick_derived = {
                "preprocessing_hashing": preprocess_out,
                "ocr_multi_script": ocr_out,
                "quick_scan": {
                    "enabled": True,
                    "budget_seconds": round(budget_seconds, 3),
                    "elapsed_ms": round(elapsed_ms, 2),
                    "within_budget": elapsed_ms <= (budget_seconds * 1000),
                },
            }

            assignment = self.review_workload_module.assign(
                repo=self.repo,
                tenant_id=tenant_id,
                document_id=doc["id"],
                doc_type="UNKNOWN",
                risk_level="MEDIUM",
                policy="LEAST_LOADED",
            )
            self._event(
                document_id=doc["id"],
                tenant_id=tenant_id,
                actor_type="SYSTEM",
                actor_id=officer_id,
                event_type="review.assignment.created",
                payload={
                    "assignment_id": str(assignment.get("id")),
                    "queue_name": str(assignment.get("queue_name")),
                    "policy": str(assignment.get("assignment_policy", "LEAST_LOADED")),
                    "fast_scan": True,
                },
                reason="FAST_SCAN_AUTO_ASSIGNMENT",
                policy_version=policy_version,
                model_versions=model_versions,
                correlation_id=correlation_id,
            )

            self._event(
                document_id=doc["id"],
                tenant_id=tenant_id,
                actor_type="SYSTEM",
                actor_id=officer_id,
                event_type="document.flagged.for_review",
                payload={"reason_codes": ["FAST_SCAN_REVIEW_REQUIRED"]},
                reason="FAST_SCAN_REVIEW_REQUIRED",
                policy_version=policy_version,
                model_versions=model_versions,
                correlation_id=correlation_id,
            )

            updated = self.repo.update_document(
                doc["id"],
                dedup_hash=preprocess_out.get("dedup_hash"),
                confidence=float(ocr_out.get("ocr_confidence", 0.0)),
                risk_score=0.5,
                decision="REVIEW",
                state=DocumentState.WAITING_FOR_REVIEW,
                derived=quick_derived,
                last_job_id=job_id,
            )
            if not updated:
                raise RuntimeError("Document update failed")
            return self.repo.get_document(doc["id"], tenant_id=tenant_id) or updated
        except Exception as exc:
            safe_doc = self.repo.get_document(document_id, tenant_id=tenant_id)
            if safe_doc and safe_doc.get("state") != DocumentState.ARCHIVED.value:
                try:
                    self._transition(safe_doc, DocumentState.FAILED, "SYSTEM", officer_id, "FAST_SCAN_EXCEPTION")
                except Exception:
                    pass
            self.emit_custom_event(
                document_id=document_id,
                tenant_id=tenant_id,
                actor_type="SYSTEM",
                actor_id=officer_id,
                event_type="document.failed",
                payload={"error": str(exc), "fast_scan": True},
                reason="FAST_SCAN_EXCEPTION",
            )
            raise

    def process_document(
        self,
        document_id: str,
        tenant_id: str,
        officer_id: str,
        *,
        fast_scan: bool = False,
        max_latency_seconds: float | None = None,
    ) -> dict[str, Any]:
        if fast_scan:
            return self._process_document_fast_scan(
                document_id=document_id,
                tenant_id=tenant_id,
                officer_id=officer_id,
                max_latency_seconds=max_latency_seconds,
            )
        self._authorize(officer_id, tenant_id, WRITER_ROLES)
        doc = self.repo.get_document(document_id, tenant_id=tenant_id)
        if not doc:
            raise ValueError("Document not found for tenant")

        job_id = str(uuid4())
        correlation_id = job_id
        policy = self.repo.get_tenant_policy(tenant_id)
        policy_version = 1
        model_versions = {
            "ocr_model_id": "ocr-devanagari-v1",
            "classifier_model_id": "doc-classifier-v2",
            "extractor_model_id": "layout-extractor-v1",
            "fraud_model_id": "fraud-aggregator-v1",
        }

        try:
            self._transition(doc, DocumentState.PREPROCESSING, "SYSTEM", officer_id, "PIPELINE_STARTED")

            ctx = self.dag.run(
                {
                    "raw_text": doc.get("raw_text", ""),
                    "source_path": ((doc.get("metadata") or {}).get("ingestion") or {}).get("original_file_uri"),
                    "script_hint": ((doc.get("metadata") or {}).get("ingestion") or {}).get("script_hint"),
                    "doc_type_hint": ((doc.get("metadata") or {}).get("ingestion") or {}).get("doc_type_hint"),
                    "submission_context": (doc.get("metadata") or {}).get("submission_context") or {},
                    "prefilled_data": (doc.get("metadata") or {}).get("prefilled_form_data") or {},
                    "tenant_id": tenant_id,
                    "document_id": doc["id"],
                    "repo": self.repo,
                    "tenant_policy": policy,
                }
            )

            self.monitoring_mlops_module.record_module_metrics(
                repo=self.repo,
                tenant_id=tenant_id,
                document_id=doc["id"],
                job_id=job_id,
                node_durations_ms=dict(ctx.get("node_durations_ms") or {}),
                node_outputs=dict(ctx.get("node_outputs") or {}),
            )

            audit_entries = self.explainability_audit_module.audit_entries(
                tenant_id=tenant_id,
                document_id=doc["id"],
                job_id=job_id,
                node_outputs=dict(ctx.get("node_outputs") or {}),
                execution_order=list(ctx.get("execution_order") or []),
            )
            for entry in audit_entries:
                self.repo.create_model_audit_log(
                    tenant_id=entry["tenant_id"],
                    document_id=entry["document_id"],
                    job_id=entry["job_id"],
                    module_name=entry["module_name"],
                    model_id=entry["model_id"],
                    model_version=entry["model_version"],
                    input_ref=entry["input_ref"],
                    output=entry["output"],
                    reason_codes=list(entry.get("reason_codes") or []),
                    actor_type=entry.get("actor_type", "SYSTEM"),
                    actor_id=entry.get("actor_id"),
                )

            self._event(
                document_id=doc["id"],
                tenant_id=tenant_id,
                actor_type="SYSTEM",
                actor_id=officer_id,
                event_type="document.preprocessed",
                payload={
                    "quality_score": ctx["preprocessing_hashing"]["quality_score"],
                    "dedup_hash": ctx["preprocessing_hashing"]["dedup_hash"],
                },
                reason="PREPROCESSING_COMPLETE",
                policy_version=policy_version,
                model_versions=model_versions,
                correlation_id=correlation_id,
            )

            self._transition(doc, DocumentState.OCR_COMPLETE, "SYSTEM", officer_id, "OCR_STAGE_COMPLETE")
            self._event(
                document_id=doc["id"],
                tenant_id=tenant_id,
                actor_type="SYSTEM",
                actor_id=officer_id,
                event_type="ocr.completed",
                payload={"ocr_confidence": ctx["ocr_multi_script"]["ocr_confidence"]},
                reason="OCR_COMPLETE",
                policy_version=policy_version,
                model_versions=model_versions,
                correlation_id=correlation_id,
            )

            self._transition(doc, DocumentState.BRANCHED, "SYSTEM", officer_id, "PARALLEL_BRANCHING")
            self._event(
                document_id=doc["id"],
                tenant_id=tenant_id,
                actor_type="SYSTEM",
                actor_id=officer_id,
                event_type="branch.started",
                payload={"modules": sorted(list(BRANCH_MODULES))},
                reason="BRANCH_FANOUT",
                policy_version=policy_version,
                model_versions=model_versions,
                correlation_id=correlation_id,
            )

            for module_name in sorted(BRANCH_MODULES):
                out = ctx.get(module_name, {})
                self._event(
                    document_id=doc["id"],
                    tenant_id=tenant_id,
                    actor_type="SYSTEM",
                    actor_id=officer_id,
                    event_type=f"branch.completed.{module_name}",
                    payload={"module": module_name, "status": "COMPLETED", "summary": out},
                    reason="BRANCH_COMPLETE",
                    policy_version=policy_version,
                    model_versions=model_versions,
                    correlation_id=correlation_id,
                )

            self._transition(doc, DocumentState.MERGED, "SYSTEM", officer_id, "BRANCHES_MERGED")
            self._event(
                document_id=doc["id"],
                tenant_id=tenant_id,
                actor_type="SYSTEM",
                actor_id=officer_id,
                event_type="document.merged",
                payload={
                    "confidence": ctx["merge_node"]["confidence"],
                    "risk_score": ctx["merge_node"]["risk_score"],
                },
                reason="MERGE_COMPLETE",
                policy_version=policy_version,
                model_versions=model_versions,
                correlation_id=correlation_id,
            )

            if str(ctx["merge_node"].get("risk_level", "LOW")) in {"HIGH", "CRITICAL"}:
                self._event(
                    document_id=doc["id"],
                    tenant_id=tenant_id,
                    actor_type="SYSTEM",
                    actor_id=officer_id,
                    event_type="document.fraud_flagged",
                    payload={
                        "risk_level": ctx["merge_node"]["risk_level"],
                        "aggregate_fraud_risk_score": ctx["merge_node"]["risk_score"],
                    },
                    reason="HIGH_FRAUD_RISK",
                    policy_version=policy_version,
                    model_versions=model_versions,
                    correlation_id=correlation_id,
                )

            decision = ctx["output_notification"]["final_decision"]
            reason_codes = ctx["decision_explainability"]["reason_codes"]

            if decision == "REVIEW":
                self._transition(doc, DocumentState.WAITING_FOR_REVIEW, "SYSTEM", officer_id, "FLAGGED_FOR_REVIEW")
                self._event(
                    document_id=doc["id"],
                    tenant_id=tenant_id,
                    actor_type="SYSTEM",
                    actor_id=officer_id,
                    event_type="document.flagged.for_review",
                    payload={"reason_codes": reason_codes},
                    reason="FLAGGED_FOR_OFFICER_REVIEW",
                    policy_version=policy_version,
                    model_versions=model_versions,
                    correlation_id=correlation_id,
                )
                assignment = self.review_workload_module.assign(
                    repo=self.repo,
                    tenant_id=tenant_id,
                    document_id=doc["id"],
                    doc_type=str(ctx["template_map"].get("document_type", "UNKNOWN")),
                    risk_level=str(ctx["merge_node"].get("risk_level", "MEDIUM")),
                    policy="LEAST_LOADED",
                )
                self._event(
                    document_id=doc["id"],
                    tenant_id=tenant_id,
                    actor_type="SYSTEM",
                    actor_id=officer_id,
                    event_type="review.assignment.created",
                    payload={
                        "assignment_id": str(assignment.get("id")),
                        "queue_name": str(assignment.get("queue_name")),
                        "policy": str(assignment.get("assignment_policy", "LEAST_LOADED")),
                    },
                    reason="AUTO_ASSIGNMENT",
                    policy_version=policy_version,
                    model_versions=model_versions,
                    correlation_id=correlation_id,
                )
                final_state = DocumentState.WAITING_FOR_REVIEW
            elif decision == "APPROVE":
                self._transition(doc, DocumentState.APPROVED, "SYSTEM", officer_id, "AUTO_APPROVAL")
                self._event(
                    document_id=doc["id"],
                    tenant_id=tenant_id,
                    actor_type="SYSTEM",
                    actor_id=officer_id,
                    event_type="document.approved",
                    payload={"decision": "APPROVED"},
                    reason="AUTO_APPROVAL",
                    policy_version=policy_version,
                    model_versions=model_versions,
                    correlation_id=correlation_id,
                )
                final_state = DocumentState.APPROVED
            else:
                self._transition(doc, DocumentState.REJECTED, "SYSTEM", officer_id, "AUTO_REJECTION")
                self._event(
                    document_id=doc["id"],
                    tenant_id=tenant_id,
                    actor_type="SYSTEM",
                    actor_id=officer_id,
                    event_type="document.rejected",
                    payload={"decision": "REJECTED", "reason_codes": reason_codes},
                    reason="AUTO_REJECTION",
                    policy_version=policy_version,
                    model_versions=model_versions,
                    correlation_id=correlation_id,
                )
                final_state = DocumentState.REJECTED
            if final_state in {DocumentState.APPROVED, DocumentState.REJECTED}:
                self.repo.resolve_review_assignment(document_id=doc["id"], tenant_id=tenant_id, status="RESOLVED")

            updated = self.repo.update_document(
                doc["id"],
                dedup_hash=ctx["dedup_cross_submission"]["dedup_hash"],
                confidence=ctx["decision_explainability"]["confidence"],
                risk_score=ctx["decision_explainability"]["risk_score"],
                decision=decision,
                template_id=ctx["template_map"]["template_id"],
                state=final_state,
                derived=ctx["node_outputs"],
                last_job_id=job_id,
            )
            if not updated:
                raise RuntimeError("Document update failed")

            latest = self.repo.get_document(doc["id"], tenant_id=tenant_id) or updated
            record = self._build_document_record(latest, job_id, ctx)
            self.repo.save_document_record(tenant_id, doc["id"], job_id, "1.0", {"document_record": record.model_dump()})

            result_payload = self.output_integration_module.create_result_payload(
                document_id=doc["id"],
                job_id=job_id,
                decision_out=ctx["decision_explainability"],
                state=final_state,
            )
            webhook_event = (
                "document.approved"
                if final_state == DocumentState.APPROVED
                else "document.rejected"
                if final_state == DocumentState.REJECTED
                else "document.flagged.for_review"
            )
            outbox = self.output_integration_module.queue_webhook(
                repo=self.repo,
                tenant_id=tenant_id,
                document_id=doc["id"],
                event_type=webhook_event,
                payload=result_payload,
            )
            self._event(
                document_id=doc["id"],
                tenant_id=tenant_id,
                actor_type="SYSTEM",
                actor_id=officer_id,
                event_type="webhook.queued",
                payload={"event_type": webhook_event, "outbox_id": str(outbox.get("id"))},
                reason="OUTPUT_INTEGRATION",
                policy_version=policy_version,
                model_versions=model_versions,
                correlation_id=correlation_id,
            )

            # Offline conflict handling: central result overrides local provisional result.
            provisional = latest.get("provisional_decision")
            if provisional and provisional != decision:
                self.emit_custom_event(
                    document_id=doc["id"],
                    tenant_id=tenant_id,
                    actor_type="SYSTEM",
                    actor_id=officer_id,
                    event_type="offline.conflict.detected",
                    payload={"local_provisional": provisional, "central_decision": decision},
                    reason="CENTRAL_PIPELINE_OVERRIDE",
                )
                self.emit_custom_event(
                    document_id=doc["id"],
                    tenant_id=tenant_id,
                    actor_type="SYSTEM",
                    actor_id=officer_id,
                    event_type="document.requires_reupload",
                    payload={
                        "message": "Provisional result revised after centralized verification.",
                        "reason_code": "PROVISIONAL_REVISED",
                    },
                    reason="OFFLINE_CONFLICT_REUPLOAD_REQUIRED",
                )

            return latest
        except Exception as exc:
            safe_doc = self.repo.get_document(document_id, tenant_id=tenant_id)
            if safe_doc and safe_doc.get("state") != DocumentState.ARCHIVED.value:
                try:
                    self._transition(safe_doc, DocumentState.FAILED, "SYSTEM", officer_id, "PIPELINE_EXCEPTION")
                except Exception:
                    pass

            self.emit_custom_event(
                document_id=document_id,
                tenant_id=tenant_id,
                actor_type="SYSTEM",
                actor_id=officer_id,
                event_type="document.failed",
                payload={"error": str(exc)},
                reason="PIPELINE_EXCEPTION",
            )
            raise

    def start_review(self, document_id: str, tenant_id: str, officer_id: str, review_level: str = "L1") -> dict[str, Any]:
        self._authorize(officer_id, tenant_id, REVIEW_ROLES)
        doc = self.repo.get_document(document_id, tenant_id=tenant_id)
        if not doc:
            raise ValueError("Document not found for tenant")

        state = DocumentState(doc["state"])
        if state not in {DocumentState.WAITING_FOR_REVIEW, DocumentState.DISPUTED}:
            raise ValueError("Review can only start from WAITING_FOR_REVIEW or DISPUTED")

        self._transition(doc, DocumentState.REVIEW_IN_PROGRESS, "OFFICER", officer_id, "OFFICER_PICKED_REVIEW")

        metadata = dict(doc.get("metadata") or {})
        human_review = dict(metadata.get("human_review") or {})
        human_review["assigned_to_officer_id"] = officer_id
        human_review["assigned_at"] = datetime.now(timezone.utc).isoformat()
        metadata["human_review"] = human_review
        updated = self.repo.update_document(doc["id"], metadata=metadata)
        if updated:
            doc.update(updated)

        open_assignments = self.repo.list_review_assignments(tenant_id, status="WAITING_FOR_REVIEW")
        for assignment in open_assignments:
            if str(assignment.get("document_id")) == str(doc["id"]):
                self.repo.claim_review_assignment(assignment_id=str(assignment.get("id")), officer_id=officer_id)
                break

        self._event(
            document_id=doc["id"],
            tenant_id=tenant_id,
            actor_type="OFFICER",
            actor_id=officer_id,
            event_type="review.started",
            payload={"review_level": review_level},
            reason="REVIEW_STARTED",
            policy_version=1,
            model_versions=None,
        )
        return self.repo.get_document(doc["id"], tenant_id=tenant_id) or doc

    def manual_decision(
        self,
        document_id: str,
        decision: str,
        tenant_id: str,
        officer_id: str,
        reason: str = "OFFICER_DECISION",
    ) -> dict[str, Any]:
        self._authorize(officer_id, tenant_id, REVIEW_ROLES)
        doc = self.repo.get_document(document_id, tenant_id=tenant_id)
        if not doc:
            raise ValueError("Document not found for tenant")

        if DocumentState(doc["state"]) != DocumentState.REVIEW_IN_PROGRESS:
            raise ValueError("Manual decision is only allowed from REVIEW_IN_PROGRESS")

        if decision == "APPROVE":
            self._transition(doc, DocumentState.APPROVED, "OFFICER", officer_id, reason)
            target_event = "document.approved"
            payload = {"decision": "APPROVED"}
        elif decision == "REJECT":
            self._transition(doc, DocumentState.REJECTED, "OFFICER", officer_id, reason)
            target_event = "document.rejected"
            payload = {"decision": "REJECTED", "reason_codes": [reason]}
        else:
            raise ValueError("Unsupported decision")

        updated = self.repo.update_document(doc["id"], decision=decision)
        if not updated:
            raise RuntimeError("Update failed")

        self._event(
            document_id=doc["id"],
            tenant_id=tenant_id,
            actor_type="OFFICER",
            actor_id=officer_id,
            event_type="review.completed",
            payload={"decision": decision},
            reason=reason,
            policy_version=1,
            model_versions=None,
        )
        self._event(
            document_id=doc["id"],
            tenant_id=tenant_id,
            actor_type="OFFICER",
            actor_id=officer_id,
            event_type=target_event,
            payload=payload,
            reason=reason,
            policy_version=1,
            model_versions=None,
        )

        self._append_human_review_event(
            updated,
            HumanReviewEvent(
                officer_id=officer_id,
                action="DECISION_MADE",
                decision=decision,
                reason=reason,
                at=datetime.now(timezone.utc).isoformat(),
            ),
        )

        self.repo.resolve_review_assignment(document_id=doc["id"], tenant_id=tenant_id, status="RESOLVED")
        outbox = self.output_integration_module.queue_webhook(
            repo=self.repo,
            tenant_id=tenant_id,
            document_id=doc["id"],
            event_type=target_event,
            payload={
                "document_id": doc["id"],
                "decision": decision,
                "state": "APPROVED" if decision == "APPROVE" else "REJECTED",
                "reason": reason,
            },
        )
        self._event(
            document_id=doc["id"],
            tenant_id=tenant_id,
            actor_type="SYSTEM",
            actor_id=officer_id,
            event_type="webhook.queued",
            payload={"event_type": target_event, "outbox_id": str(outbox.get("id"))},
            reason="OUTPUT_INTEGRATION",
            policy_version=1,
            model_versions=None,
        )

        return self.repo.get_document(doc["id"], tenant_id=tenant_id) or updated

    def open_dispute(
        self,
        document_id: str,
        reason: str,
        evidence_note: str,
        tenant_id: str,
        officer_id: str,
        citizen_actor_id: str | None = None,
    ) -> dict[str, Any]:
        self._authorize(officer_id, tenant_id, WRITER_ROLES)
        doc = self.repo.get_document(document_id, tenant_id=tenant_id)
        if not doc:
            raise ValueError("Document not found for tenant")

        if DocumentState(doc["state"]) != DocumentState.REJECTED:
            raise ValueError("Dispute allowed only from REJECTED state")

        self._transition(doc, DocumentState.DISPUTED, "CITIZEN", citizen_actor_id or doc.get("citizen_id"), "DISPUTE_SUBMITTED")

        dispute = Dispute(
            document_id=doc["id"],
            tenant_id=tenant_id,
            reason=reason,
            evidence_note=evidence_note,
            status="DISPUTE_SUBMITTED",
        )
        row = self.repo.create_dispute(dispute)
        self._event(
            document_id=doc["id"],
            tenant_id=tenant_id,
            actor_type="CITIZEN",
            actor_id=citizen_actor_id or doc.get("citizen_id"),
            event_type="document.disputed",
            payload={"reason": reason},
            reason="DISPUTE_SUBMITTED",
            policy_version=1,
            model_versions=None,
        )
        outbox = self.output_integration_module.queue_webhook(
            repo=self.repo,
            tenant_id=tenant_id,
            document_id=doc["id"],
            event_type="document.disputed",
            payload={"document_id": doc["id"], "reason": reason, "status": "DISPUTED"},
        )
        self._event(
            document_id=doc["id"],
            tenant_id=tenant_id,
            actor_type="SYSTEM",
            actor_id=officer_id,
            event_type="webhook.queued",
            payload={"event_type": "document.disputed", "outbox_id": str(outbox.get("id"))},
            reason="OUTPUT_INTEGRATION",
            policy_version=1,
            model_versions=None,
        )
        assignment = self.review_workload_module.assign(
            repo=self.repo,
            tenant_id=tenant_id,
            document_id=doc["id"],
            doc_type="DISPUTE",
            risk_level="HIGH",
            policy="SENIOR_OFFICER",
        )
        self._event(
            document_id=doc["id"],
            tenant_id=tenant_id,
            actor_type="SYSTEM",
            actor_id=officer_id,
            event_type="review.assignment.created",
            payload={
                "assignment_id": str(assignment.get("id")),
                "queue_name": str(assignment.get("queue_name")),
                "policy": str(assignment.get("assignment_policy", "SENIOR_OFFICER")),
            },
            reason="DISPUTE_ESCALATION_QUEUE",
            policy_version=1,
            model_versions=None,
        )
        return row

    def flag_internal_disagreement(
        self,
        *,
        document_id: str,
        tenant_id: str,
        officer_id: str,
        reason: str = "OFFICER_DECISION_CONFLICT",
    ) -> dict[str, Any]:
        self._authorize(officer_id, tenant_id, REVIEW_ROLES)
        doc = self.repo.get_document(document_id, tenant_id=tenant_id)
        if not doc:
            raise ValueError("Document not found for tenant")

        metadata = dict(doc.get("metadata") or {})
        conflicts = list((metadata.get("internal_conflicts") or []))
        conflicts.append(
            {
                "status": "DISPUTED_INTERNAL",
                "officer_id": officer_id,
                "reason": reason,
                "at": datetime.now(timezone.utc).isoformat(),
            }
        )
        metadata["internal_conflicts"] = conflicts
        updated = self.repo.update_document(document_id, metadata=metadata)

        escalation = self.repo.create_review_escalation(
            tenant_id=tenant_id,
            document_id=document_id,
            escalation_level=3,
            assignee_role="admin",
            reason=f"Internal disagreement: {reason}",
        )
        self._event(
            document_id=document_id,
            tenant_id=tenant_id,
            actor_type="OFFICER",
            actor_id=officer_id,
            event_type="review.escalated",
            payload={"escalation_level": 3, "assignee_role": "admin"},
            reason="INTERNAL_DISAGREEMENT",
            policy_version=1,
            model_versions=None,
        )
        return {"document": updated or doc, "escalation": escalation}

    def archive_document(self, document_id: str, tenant_id: str, officer_id: str, archive_reason: str = "RETENTION_LIFECYCLE") -> dict[str, Any]:
        self._authorize(officer_id, tenant_id, REVIEW_ROLES)
        doc = self.repo.get_document(document_id, tenant_id=tenant_id)
        if not doc:
            raise ValueError("Document not found for tenant")

        state = DocumentState(doc["state"])
        if state not in {DocumentState.APPROVED, DocumentState.REJECTED, DocumentState.EXPIRED, DocumentState.FAILED}:
            raise ValueError("Document can only be archived from APPROVED, REJECTED, EXPIRED, or FAILED")

        self._transition(doc, DocumentState.ARCHIVED, "SYSTEM", officer_id, archive_reason)
        self._event(
            document_id=doc["id"],
            tenant_id=tenant_id,
            actor_type="SYSTEM",
            actor_id=officer_id,
            event_type="document.archived",
            payload={"archive_reason": archive_reason},
            reason=archive_reason,
            policy_version=1,
            model_versions=None,
        )

        metadata = dict(doc.get("metadata") or {})
        retention = dict(metadata.get("retention_policy") or {})
        retention["archival_status"] = "ARCHIVED"
        metadata["retention_policy"] = retention
        updated = self.repo.update_document(doc["id"], metadata=metadata)
        return updated or doc

    def apply_retention_lifecycle(self, tenant_id: str, officer_id: str) -> dict[str, int]:
        self._authorize(officer_id, tenant_id, REVIEW_ROLES)
        now = datetime.now(timezone.utc)
        archived = 0
        expired = 0

        docs = self.repo.list_documents(tenant_id)
        policy = self.repo.get_tenant_policy(tenant_id)
        review_sla_days = int(policy.get("review_sla_days", 3))

        for doc in docs:
            state = DocumentState(doc["state"])
            created_at = _parse_dt(str(doc.get("created_at", now.isoformat())))
            expires_at = _parse_dt(str(doc.get("expires_at"))) if doc.get("expires_at") else None

            if state == DocumentState.WAITING_FOR_REVIEW and (now - created_at).days > review_sla_days:
                self._transition(doc, DocumentState.EXPIRED, "SYSTEM", officer_id, "REVIEW_SLA_EXPIRED")
                expired += 1

            state = DocumentState(doc["state"])
            if state in {DocumentState.APPROVED, DocumentState.REJECTED, DocumentState.EXPIRED, DocumentState.FAILED}:
                if expires_at and now >= expires_at:
                    self.archive_document(doc["id"], tenant_id, officer_id, "RETENTION_LIFECYCLE")
                    archived += 1

        return {"archived": archived, "expired": expired}

    def enforce_review_sla(self, tenant_id: str, officer_id: str) -> list[dict[str, Any]]:
        self._authorize(officer_id, tenant_id, REVIEW_ROLES)
        policy = self.repo.get_tenant_policy(tenant_id)
        sla_days = int(policy.get("review_sla_days", 3))
        step_days = max(1, int(policy.get("escalation_step_days", 1)))

        waiting_docs = self.repo.list_documents_by_state(tenant_id, DocumentState.WAITING_FOR_REVIEW)
        escalations: list[dict[str, Any]] = []

        now = datetime.now(timezone.utc)
        for doc in waiting_docs:
            age_days = (now - _parse_dt(str(doc.get("created_at", now.isoformat())))).days
            if age_days <= sla_days:
                continue

            overdue_days = age_days - sla_days
            level = min(3, 1 + (overdue_days // step_days))
            assignee_role = "verifier" if level <= 2 else "senior_verifier"
            reason = f"WAITING_FOR_REVIEW exceeds SLA by {overdue_days} day(s)"

            row = self.repo.create_review_escalation(
                tenant_id=tenant_id,
                document_id=doc["id"],
                escalation_level=level,
                assignee_role=assignee_role,
                reason=reason,
            )
            escalations.append(row)

            self._event(
                document_id=doc["id"],
                tenant_id=tenant_id,
                actor_type="SYSTEM",
                actor_id=officer_id,
                event_type="review.escalated",
                payload={"escalation_level": level, "assignee_role": assignee_role},
                reason=reason,
                policy_version=1,
                model_versions=None,
            )

        return escalations

    def notify(self, document_id: str, tenant_id: str, officer_id: str) -> dict[str, Any] | None:
        self._authorize(officer_id, tenant_id, WRITER_ROLES)
        doc = self.repo.get_document(document_id, tenant_id=tenant_id)
        if not doc:
            return None

        message = f"Status update for document {document_id}: {doc.get('state')}"
        channels = ["PORTAL"]
        self._event(
            document_id=document_id,
            tenant_id=tenant_id,
            actor_type="SYSTEM",
            actor_id=officer_id,
            event_type="notification.sent",
            payload={"channels": channels, "message": message},
            reason="MANUAL_NOTIFY",
            policy_version=1,
            model_versions=None,
        )
        return doc

    def list_documents(self, tenant_id: str, officer_id: str) -> list[dict[str, Any]]:
        self._authorize(officer_id, tenant_id, None)
        return self.repo.list_documents(tenant_id)

    def list_events(self, document_id: str, tenant_id: str, officer_id: str) -> list[dict[str, Any]]:
        self._authorize(officer_id, tenant_id, None)
        return self.repo.list_events(document_id, tenant_id=tenant_id)

    def list_tenant_events(self, tenant_id: str, officer_id: str) -> list[dict[str, Any]]:
        self._authorize(officer_id, tenant_id, None)
        return self.repo.list_events_by_tenant(tenant_id)

    def list_disputes(self, tenant_id: str, officer_id: str) -> list[dict[str, Any]]:
        self._authorize(officer_id, tenant_id, None)
        return self.repo.list_disputes(tenant_id)

    def list_notifications(self, tenant_id: str, officer_id: str, document_id: str | None = None) -> list[dict[str, Any]]:
        self._authorize(officer_id, tenant_id, None)
        return self.repo.list_notifications(tenant_id, document_id=document_id)

    def list_review_escalations(self, tenant_id: str, officer_id: str, only_open: bool = True) -> list[dict[str, Any]]:
        self._authorize(officer_id, tenant_id, REVIEW_ROLES)
        return self.repo.list_review_escalations(tenant_id, only_open=only_open)

    def list_review_assignments(self, tenant_id: str, officer_id: str, status: str | None = None) -> list[dict[str, Any]]:
        self._authorize(officer_id, tenant_id, REVIEW_ROLES)
        return self.repo.list_review_assignments(tenant_id, status=status)

    def list_model_audit_logs(self, tenant_id: str, officer_id: str, document_id: str | None = None) -> list[dict[str, Any]]:
        self._authorize(officer_id, tenant_id, None)
        return self.repo.list_model_audit_logs(tenant_id, document_id=document_id)

    def list_webhook_outbox(self, tenant_id: str, officer_id: str, status: str = "PENDING") -> list[dict[str, Any]]:
        self._authorize(officer_id, tenant_id, REVIEW_ROLES)
        return self.repo.list_webhook_outbox(tenant_id, status=status)

    def record_field_correction(
        self,
        *,
        document_id: str,
        tenant_id: str,
        officer_id: str,
        field_name: str,
        old_value: str | None,
        new_value: str | None,
        reason: str,
    ) -> dict[str, Any]:
        self._authorize(officer_id, tenant_id, REVIEW_ROLES)
        doc = self.repo.get_document(document_id, tenant_id=tenant_id)
        if not doc:
            raise ValueError("Document not found for tenant")

        self._append_human_review_event(
            doc,
            HumanReviewEvent(
                officer_id=officer_id,
                action="FIELD_CORRECTED",
                field_name=field_name,
                old_value=old_value,
                new_value=new_value,
                reason=reason,
                at=datetime.now(timezone.utc).isoformat(),
            ),
        )

        gate_result = self.monitoring_mlops_module.gate_correction(
            repo=self.repo,
            tenant_id=tenant_id,
            document_id=document_id,
            field_name=field_name,
            old_value=old_value,
            new_value=new_value,
            officer_id=officer_id,
            reason=reason,
        )

        latest = self.repo.get_document(document_id, tenant_id=tenant_id) or doc
        metadata = dict(latest.get("metadata") or {})
        ml_flags = dict(metadata.get("ml_training_flags") or {})
        eligibility = dict(ml_flags.get("eligible_for_training") or {})
        notes = list(ml_flags.get("data_quality_notes") or [])

        gate_status = str((gate_result.get("gate") or {}).get("status", "UNKNOWN"))
        if gate_status != "HIGH_CONFIDENCE":
            eligibility["extraction"] = False
            if "CORRECTION_PENDING_QA" not in notes:
                notes.append("CORRECTION_PENDING_QA")

        ml_flags["eligible_for_training"] = eligibility
        ml_flags["data_quality_notes"] = notes
        metadata["ml_training_flags"] = ml_flags
        self.repo.update_document(document_id, metadata=metadata)

        self._event(
            document_id=document_id,
            tenant_id=tenant_id,
            actor_type="OFFICER",
            actor_id=officer_id,
            event_type="correction.logged",
            payload={
                "field_name": field_name,
                "officer_id": officer_id,
                "gate_status": gate_status,
            },
            reason=reason,
            policy_version=1,
            model_versions=None,
        )
        return gate_result

    def monitoring_dashboard(self, tenant_id: str, officer_id: str) -> dict[str, Any]:
        self._authorize(officer_id, tenant_id, None)
        mlops = self.monitoring_mlops_module.mlops_summary(repo=self.repo, tenant_id=tenant_id)
        waiting = len(self.repo.list_review_assignments(tenant_id, status="WAITING_FOR_REVIEW"))
        in_progress = len(self.repo.list_review_assignments(tenant_id, status="REVIEW_IN_PROGRESS"))
        corrections_pending = len(self.repo.list_correction_gate_records(tenant_id, status="PENDING_QA"))
        return {
            "mlops": mlops,
            "review_workload": {
                "waiting_for_review": waiting,
                "review_in_progress": in_progress,
            },
            "correction_validation": {
                "pending_qa": corrections_pending,
            },
        }

    def batch_export_documents(self, tenant_id: str, officer_id: str, include_raw_text: bool = False) -> str:
        self._authorize(officer_id, tenant_id, REVIEW_ROLES)
        policy = self.repo.get_tenant_policy(tenant_id)
        if not bool(policy.get("export_enabled", True)):
            raise PermissionError("Batch export is disabled for this tenant")

        rows = self.repo.export_documents_for_tenant(tenant_id, include_raw_text=include_raw_text)
        if not rows:
            return ""

        columns = [
            "id",
            "tenant_id",
            "citizen_id",
            "file_name",
            "state",
            "decision",
            "confidence",
            "risk_score",
            "template_id",
            "last_job_id",
            "doc_type",
            "validation_status",
            "issuer_status",
            "fraud_risk_level",
            "fraud_signals_json",
            "extracted_fields_json",
            "explainability_json",
            "audit_event_count",
            "created_at",
            "updated_at",
            "expires_at",
        ]
        if include_raw_text:
            columns.append("raw_text")

        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            document_id = str(row.get("id", ""))
            latest_record_row = self.repo.get_latest_document_record(tenant_id, document_id) if document_id else None
            wrapped = dict((latest_record_row or {}).get("record") or {})
            record = (
                dict(wrapped.get("document_record"))
                if isinstance(wrapped.get("document_record"), dict)
                else wrapped
            )

            extraction = dict(record.get("extraction_output") or {})
            validation = dict(record.get("validation_output") or {})
            fraud = dict(record.get("fraud_risk_output") or {})
            issuer = dict(record.get("issuer_verification_output") or {})
            explainability = dict(record.get("explainability") or {})
            events = self.repo.list_events(document_id, tenant_id=tenant_id) if document_id else []
            enriched = dict(row)
            enriched["doc_type"] = (record.get("classification_output") or {}).get("doc_type")
            enriched["validation_status"] = validation.get("overall_status")
            enriched["issuer_status"] = issuer.get("status") or issuer.get("registry_status")
            enriched["fraud_risk_level"] = fraud.get("risk_level")
            enriched["fraud_signals_json"] = json.dumps(
                ((fraud.get("components") or {}).get("image_forensics_component") or {}).get("signals", []),
                ensure_ascii=False,
            )
            extracted_fields = extraction.get("fields")
            if isinstance(extracted_fields, list):
                compact_fields = {
                    str(item.get("field_name", "")): item.get("normalized_value")
                    for item in extracted_fields
                    if isinstance(item, dict)
                }
            else:
                compact_fields = extracted_fields if isinstance(extracted_fields, dict) else {}
            enriched["extracted_fields_json"] = json.dumps(compact_fields, ensure_ascii=False)
            enriched["explainability_json"] = json.dumps(
                {
                    "document_explanations": explainability.get("document_explanations", []),
                    "field_explanations": explainability.get("field_explanations", []),
                },
                ensure_ascii=False,
            )
            enriched["audit_event_count"] = len(events)
            writer.writerow({k: enriched.get(k) for k in columns})
        return buffer.getvalue()

    def citizen_submit_document(
        self,
        *,
        tenant_id: str,
        citizen_id: str,
        file_name: str,
        raw_text: str,
        metadata: dict[str, Any] | None = None,
        process_now: bool = True,
    ) -> dict[str, Any]:
        system_actor_id = f"citizen-intake-{tenant_id}"
        try:
            existing = self.repo.get_officer(system_actor_id)
            if not existing:
                self.register_officer(system_actor_id, tenant_id, ROLE_VERIFIER)
        except Exception:
            # Best effort for local/demo mode.
            self.register_officer(system_actor_id, tenant_id, ROLE_VERIFIER)

        payload = dict(metadata or {})
        payload.setdefault("source", "ONLINE_PORTAL")
        payload.setdefault(
            "ingestion",
            {
                "source": "ONLINE_PORTAL",
                "submitted_by": {"actor_type": "CITIZEN", "actor_id": citizen_id},
                "received_at": datetime.now(timezone.utc).isoformat(),
                "original_file_uri": payload.get("original_file_uri"),
                "dedup_matches": [],
            },
        )

        row = self.create_document(
            tenant_id=tenant_id,
            citizen_id=citizen_id,
            file_name=file_name,
            raw_text=raw_text,
            officer_id=system_actor_id,
            metadata=payload,
        )
        if process_now:
            row = self.process_document(str(row["id"]), tenant_id, system_actor_id)
        return row

    def list_citizen_documents(self, tenant_id: str, citizen_id: str) -> list[dict[str, Any]]:
        docs = self.repo.list_documents(tenant_id)
        rows = [d for d in docs if str(d.get("citizen_id")) == citizen_id]
        return sorted(rows, key=lambda x: str(x.get("created_at", "")), reverse=True)

    def citizen_open_dispute(
        self,
        *,
        tenant_id: str,
        document_id: str,
        citizen_id: str,
        reason: str,
        evidence_note: str,
    ) -> dict[str, Any]:
        doc = self.repo.get_document(document_id, tenant_id=tenant_id)
        if not doc:
            raise ValueError("Document not found")
        if str(doc.get("citizen_id")) != citizen_id:
            raise PermissionError("Citizen cannot dispute another applicant's document")

        system_actor_id = f"citizen-dispute-{tenant_id}"
        existing = self.repo.get_officer(system_actor_id)
        if not existing:
            self.register_officer(system_actor_id, tenant_id, ROLE_VERIFIER)

        return self.open_dispute(
            document_id=document_id,
            reason=reason,
            evidence_note=evidence_note,
            tenant_id=tenant_id,
            officer_id=system_actor_id,
            citizen_actor_id=citizen_id,
        )

    def get_citizen_case_view(self, tenant_id: str, document_id: str, citizen_id: str) -> dict[str, Any]:
        doc = self.repo.get_document(document_id, tenant_id=tenant_id)
        if not doc:
            raise ValueError("Document not found")
        if doc.get("citizen_id") != citizen_id:
            raise PermissionError("Citizen cannot access another applicant's document")

        latest_record = self.repo.get_latest_document_record(tenant_id, document_id)
        record = (latest_record or {}).get("record", {})
        explanation = (((record or {}).get("document_record") or {}).get("explainability") or {})
        doc_explanations = explanation.get("document_explanations") or []

        decision = doc.get("decision") or "PENDING"
        if decision == "REJECT":
            next_steps = [
                "Review rejection reasons",
                "Upload corrected document or visit service center",
                "Submit dispute if you disagree",
            ]
        elif decision == "APPROVE":
            next_steps = ["No further action required"]
        else:
            next_steps = ["Wait for officer review", "Track updates in portal notifications"]

        return {
            "document_id": document_id,
            "state": doc.get("state"),
            "decision": decision,
            "rejection_reasons": doc_explanations,
            "explanation_text": self._citizen_explanation(doc),
            "next_steps": next_steps,
            "escalation_channels": ["SERVICE_CENTER", "DEPARTMENT_HELPDESK", "PORTAL_DISPUTE"],
        }

    def emit_custom_event(
        self,
        *,
        document_id: str,
        tenant_id: str,
        actor_type: str,
        actor_id: str | None,
        event_type: str,
        payload: dict[str, Any],
        reason: str,
    ) -> None:
        self._event(
            document_id=document_id,
            tenant_id=tenant_id,
            actor_type=actor_type,
            actor_id=actor_id,
            event_type=event_type,
            payload=payload,
            reason=reason,
            policy_version=1,
            model_versions=None,
        )

    def _authorize(self, officer_id: str, tenant_id: str, allowed_roles: set[str] | None) -> dict[str, Any]:
        row = self.repo.assert_officer_access(officer_id, tenant_id, allowed_roles)
        self._enforce_rate_limit(officer_id, tenant_id)
        return row

    def _enforce_rate_limit(self, officer_id: str, tenant_id: str) -> None:
        policy = self.repo.get_tenant_policy(tenant_id)
        limit = int(policy.get("api_rate_limit_per_minute", 120))
        if limit <= 0:
            return

        now = time.time()
        key = (tenant_id, officer_id)
        bucket = DocumentService._rate_windows[key]
        cutoff = now - 60
        bucket[:] = [ts for ts in bucket if ts >= cutoff]

        if len(bucket) >= limit:
            raise PermissionError("Tenant API rate limit exceeded")
        bucket.append(now)

    def _enforce_daily_quota(self, tenant_id: str) -> None:
        policy = self.repo.get_tenant_policy(tenant_id)
        max_per_day = int(policy.get("max_documents_per_day", 25000))
        if max_per_day <= 0:
            return

        current = self.repo.count_documents_created_today(tenant_id)
        if current >= max_per_day:
            raise PermissionError("Tenant daily document quota exceeded")

    def _transition(
        self,
        doc: dict[str, Any],
        target: DocumentState,
        actor_type: str,
        actor_id: str | None,
        reason: str,
    ) -> None:
        current = DocumentState(doc["state"])
        nxt = self.sm.transition(current, target)
        metadata = dict(doc.get("metadata") or {})
        history = list(metadata.get("state_history") or [])
        history.append(
            {
                "from_state": current.value,
                "to_state": nxt.value,
                "at": datetime.now(timezone.utc).isoformat(),
                "by": actor_type,
                "reason": reason,
            }
        )
        metadata["state_history"] = history

        updated = self.repo.update_document(doc["id"], state=nxt, metadata=metadata)
        if not updated:
            raise RuntimeError("State update failed")
        doc.update(updated)

    def _event(
        self,
        *,
        document_id: str,
        tenant_id: str,
        actor_type: str,
        actor_id: str | None,
        event_type: str,
        payload: dict[str, Any],
        reason: str,
        policy_version: int,
        model_versions: dict[str, Any] | None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> None:
        envelope = build_event_envelope(
            event_type=event_type,
            tenant_id=tenant_id,
            document_id=document_id,
            actor_type=actor_type,
            actor_id=actor_id,
            payload=payload,
            reason=reason,
            policy_version=policy_version,
            model_versions=model_versions,
            correlation_id=correlation_id,
            causation_id=causation_id,
        )
        event = DocumentEvent(
            document_id=document_id,
            tenant_id=tenant_id,
            actor_type=actor_type,
            actor_id=actor_id,
            event_type=event_type,
            payload=payload,
            reason=reason,
            policy_version=policy_version,
            model_versions=model_versions,
            correlation_id=correlation_id,
            causation_id=causation_id,
        )
        self.repo.add_event(event)
        self.bus.publish(event_type, envelope)

        notification_result = self.notification_service.handle_event(envelope)
        if notification_result:
            self.repo.create_notification(
                tenant_id=tenant_id,
                document_id=document_id,
                citizen_id=notification_result["citizen_id"],
                channel="AUDIT",
                event_type="notification.sent",
                message=notification_result["message"],
                metadata={"channels": notification_result["channels"]},
            )
            if event_type != "notification.sent":
                self._event(
                    document_id=document_id,
                    tenant_id=tenant_id,
                    actor_type="SYSTEM",
                    actor_id=actor_id,
                    event_type="notification.sent",
                    payload={
                        "channels": notification_result["channels"],
                        "message": notification_result["message"],
                    },
                    reason="NOTIFICATION_DISPATCHED",
                    policy_version=policy_version,
                    model_versions=model_versions,
                    correlation_id=correlation_id,
                    causation_id=event.id,
                )

    def _build_document_record(self, doc: dict[str, Any], job_id: str, ctx: dict[str, Any]) -> DocumentRecord:
        document_id = str(doc["id"])
        tenant_id = str(doc["tenant_id"])
        metadata = dict(doc.get("metadata") or {})

        ocr_text = str(ctx["ocr_multi_script"].get("ocr_text", ""))
        lines = [seg.strip() for seg in ocr_text.splitlines() if seg.strip()]
        if not lines and ocr_text.strip():
            lines = [ocr_text.strip()]

        ocr_lines: list[OCRLine] = []
        for idx, text in enumerate(lines, start=1):
            y0 = min(0.95, 0.05 + (idx * 0.03))
            words = text.split()
            word_models: list[OCRWord] = []
            if words:
                width = 0.7 / len(words)
                for w_idx, word in enumerate(words, start=1):
                    x0 = 0.1 + (w_idx - 1) * width
                    word_models.append(
                        OCRWord(
                            word_id=f"word_{idx}_{w_idx}",
                            text=word,
                            confidence=float(ctx["ocr_multi_script"].get("ocr_confidence", 0.9)),
                            bbox={"page_number": 1, "x_min": x0, "y_min": y0, "x_max": min(0.95, x0 + width * 0.9), "y_max": min(0.99, y0 + 0.02)},
                            is_uncertain=False,
                        )
                    )

            ocr_lines.append(
                OCRLine(
                    line_id=f"line_{idx}",
                    text=text,
                    confidence=float(ctx["ocr_multi_script"].get("ocr_confidence", 0.9)),
                    bbox={"page_number": 1, "x_min": 0.1, "y_min": y0, "x_max": 0.85, "y_max": min(0.99, y0 + 0.02)},
                    words=word_models,
                )
            )

        ocr_output = OCROutput(
            document_id=document_id,
            job_id=job_id,
            tenant_id=tenant_id,
            pages=[
                OCRPage(
                    page_number=1,
                    width_px=2480,
                    height_px=3508,
                    script="MIXED",
                    lines=ocr_lines,
                )
            ],
            preprocessing_metadata=PreprocessingMetadata(
                steps_applied=["DESKEW", "DENOISE", "CONTRAST_ENHANCEMENT"],
                original_dpi=200,
                estimated_quality_score=float(ctx["preprocessing_hashing"].get("quality_score", 0.7)),
            ),
            model_metadata=ModelMetadata(model_id="ocr-devanagari-v1", model_version="1.3.0"),
        )

        classification = ClassificationOutput(
            document_id=document_id,
            job_id=job_id,
            tenant_id=tenant_id,
            doc_type=str(ctx["template_map"].get("document_type", "UNKNOWN")),
            doc_subtype="FRONT_SIDE",
            region_code="DEFAULT",
            template_id=str(ctx["template_map"].get("template_id", "tpl_unknown")),
            template_version=str(ctx["template_map"].get("template_version", "2025.1.0")),
            confidence=float(ctx["classification"].get("confidence", 0.5)),
            model_metadata=ModelMetadata(model_id="doc-classifier-v2", model_version="2.0.0"),
            low_confidence=float(ctx["classification"].get("confidence", 0.5)) < 0.7,
            reasons=list(ctx["decision_explainability"].get("reason_codes", [])),
        )

        extracted_fields = []
        for field_name, value in (ctx["field_extract"].get("fields") or {}).items():
            extracted_fields.append(
                ExtractedField(
                    field_name=field_name.upper(),
                    raw_text=str(value),
                    normalized_value=str(value),
                    bbox={"page_number": 1, "x_min": 0.2, "y_min": 0.3, "x_max": 0.8, "y_max": 0.35},
                    source="OCR",
                    confidence=float(ctx["field_extract"].get("confidence", 0.6)),
                    page_number=1,
                    line_ids=["line_1"],
                    word_ids=["word_1_1"],
                    warnings=[],
                )
            )

        extraction = ExtractionOutput(
            document_id=document_id,
            job_id=job_id,
            tenant_id=tenant_id,
            template_id=classification.template_id,
            template_version=classification.template_version,
            fields=extracted_fields,
            model_metadata=ModelMetadata(model_id="layout-extractor-v1", model_version="1.0.2"),
        )

        field_results = []
        for item in extraction.fields:
            field_results.append(
                FieldValidationResult(
                    field_name=item.field_name,
                    status="PASS" if item.confidence >= 0.6 else "WARN",
                    rule_results=[
                        RuleResult(
                            rule_id="RULE_NON_EMPTY",
                            status="PASS" if item.raw_text else "FAIL",
                            reason_code="RULE_PASSED" if item.raw_text else "EMPTY",
                            message="Field has value" if item.raw_text else "Field is empty",
                        )
                    ],
                    ml_checks=[
                        MLCheckResult(
                            check_id="FIELD_CONFIDENCE_CHECK",
                            status="PASS" if item.confidence >= 0.6 else "WARN",
                            score=float(item.confidence),
                            reason_code="CONFIDENCE_OK" if item.confidence >= 0.6 else "LOW_CONFIDENCE",
                            message="Confidence check on extracted field",
                        )
                    ],
                    final_status="PASS" if item.confidence >= 0.6 else "WARN",
                    final_reason_code="FIELD_VALID" if item.confidence >= 0.6 else "SOFT_VALIDATION_WARNING",
                )
            )

        validation = ValidationOutput(
            document_id=document_id,
            job_id=job_id,
            tenant_id=tenant_id,
            template_id=classification.template_id,
            template_version=classification.template_version,
            rule_set_id=str(ctx["validation"].get("rule_set_id", "RULESET_DEFAULT")),
            field_results=field_results,
            document_level_results=[
                DocumentCheckResult(
                    check_id=f"PREFILLED_MATCH_{str(item.get('field', 'UNKNOWN')).upper()}",
                    status="WARN",
                    reason_code=str(item.get("reason", "PREFILLED_MISMATCH")),
                    message=(
                        f"Prefilled '{item.get('prefilled_value')}' differs from extracted "
                        f"'{item.get('extracted_value')}' for field {item.get('field')}"
                    ),
                )
                for item in list(ctx["validation"].get("prefilled_mismatches") or [])
            ],
            overall_status="PASS" if ctx["validation"].get("is_valid") else "WARN",
            model_metadata=ValidationModelMetadata(
                rule_engine_version="1.4.0",
                ml_validator_model_id="validator-v2",
                ml_validator_model_version="2.1.0",
            ),
        )

        visual_auth = VisualAuthenticityOutput(
            document_id=document_id,
            job_id=job_id,
            tenant_id=tenant_id,
            template_id=classification.template_id,
            template_version=classification.template_version,
            markers=[
                VisualMarkerResult(
                    marker_type="STAMP",
                    marker_name="GOVT_ROUND_SEAL",
                    expected=True,
                    detected=bool(ctx["stamps_seals"].get("stamp_present")),
                    confidence=float(ctx["stamps_seals"].get("authenticity_score", 0.5)),
                    bbox={"page_number": 1, "x_min": 0.72, "y_min": 0.82, "x_max": 0.88, "y_max": 0.94},
                    reason_code="STAMP_PRESENT_IN_EXPECTED_REGION" if bool(ctx["stamps_seals"].get("stamp_present")) else "STAMP_NOT_DETECTED",
                ),
                VisualMarkerResult(
                    marker_type="SIGNATURE",
                    marker_name="ISSUING_OFFICER_SIGNATURE",
                    expected=True,
                    detected=bool(ctx["stamps_seals"].get("signature_present")),
                    confidence=float(ctx["stamps_seals"].get("authenticity_score", 0.5)),
                    bbox={"page_number": 1, "x_min": 0.60, "y_min": 0.75, "x_max": 0.90, "y_max": 0.82},
                    reason_code="SIGNATURE_PRESENT" if bool(ctx["stamps_seals"].get("signature_present")) else "SIGNATURE_MISSING",
                ),
            ],
            image_forensics=ImageForensics(
                tamper_signals=[
                    {
                        "signal_id": f"SIG_{sig.upper()}",
                        "severity": "MEDIUM",
                        "message": f"Detected {sig} indicator",
                        "bbox": None,
                    }
                    for sig in ctx["tamper_forensics"].get("tamper_indicators", [])
                ],
                global_image_score=max(0.0, 1 - float(ctx["tamper_forensics"].get("tamper_risk", 0.2))),
            ),
            visual_authenticity_score=float(ctx["stamps_seals"].get("authenticity_score", 0.5)),
            model_metadata={"stamp_model_id": "stamp-detector-v1", "forensics_model_id": "forensics-v1"},
        )

        fraud = FraudRiskOutput(
            document_id=document_id,
            job_id=job_id,
            tenant_id=tenant_id,
            components=FraudRiskComponents(
                image_forensics_component=FraudComponent(
                    score=float(ctx["tamper_forensics"].get("tamper_risk", 0.2)),
                    signals=[str(x) for x in ctx["tamper_forensics"].get("tamper_indicators", [])],
                ),
                behavioral_component=FraudComponent(
                    score=float(ctx["fraud_behavioral_engine"].get("fraud_score", 0.2)),
                    signals=[f"DUPLICATE_COUNT_{ctx['dedup_cross_submission'].get('duplicate_count', 0)}"],
                    related_job_ids=[],
                ),
                issuer_mismatch_component=FraudComponent(
                    score=0.1 if ctx["issuer_registry_verification"].get("registry_status") == "MATCHED" else 0.9,
                    signals=[f"REGISTRY_{ctx['issuer_registry_verification'].get('registry_status', 'UNKNOWN')}"],
                ),
            ),
            aggregate_fraud_risk_score=float(ctx["merge_node"].get("risk_score", 0.5)),
            risk_level=str(ctx["merge_node"].get("risk_level", "MEDIUM")),
            disclaimer="System flags risk; final decision lies with human officers and policies.",
            model_metadata={"fraud_model_id": "fraud-aggregator-v1", "fraud_model_version": "1.0.0"},
        )

        fields_compared: list[FieldComparison] = []
        for field in extraction.fields[:5]:
            fields_compared.append(
                FieldComparison(
                    field_name=field.field_name,
                    local_value=field.normalized_value,
                    issuer_value=field.normalized_value if ctx["issuer_registry_verification"].get("registry_status") == "MATCHED" else None,
                    match=ctx["issuer_registry_verification"].get("registry_status") == "MATCHED",
                )
            )

        issuer = IssuerVerificationOutput(
            document_id=document_id,
            job_id=job_id,
            tenant_id=tenant_id,
            doc_type=classification.doc_type,
            issuer_name=str((ctx["field_extract"].get("fields") or {}).get("issuer", "UNKNOWN_ISSUER")),
            verification_method="REGISTRY_API" if ctx["issuer_registry_verification"].get("registry_status") != "NOT_CHECKED" else "NOT_AVAILABLE",
            status="CONFIRMED" if ctx["issuer_registry_verification"].get("registry_status") == "MATCHED" else "MISMATCH",
            issuer_reference_id=f"REG-{document_id[:8]}",
            fields_compared=fields_compared,
            issuer_authenticity_score=float(ctx["issuer_registry_verification"].get("registry_confidence", 0.3)),
            response_metadata=IssuerResponseMetadata(
                registry_request_id=f"req-{job_id[:8]}",
                response_time_ms=320,
                error_code=None,
            ),
        )

        state_history = [StateHistoryEvent(**ev) for ev in list((metadata.get("state_history") or []))]

        notifications = self.repo.list_notifications(tenant_id, document_id=document_id)
        comm_events = [
            CitizenCommEvent(
                event_type="NOTIFICATION_SENT",
                channel=str(n.get("channel", "PORTAL")),
                message_template_id=str(n.get("event_type", "UNKNOWN")),
                sent_at=str(n.get("sent_at", n.get("created_at", datetime.now(timezone.utc).isoformat()))),
            )
            for n in notifications
        ]

        explainability = ExplainabilityRecord(
            field_explanations=[
                {
                    "field_name": field.field_name,
                    "messages": [f"Field extracted with confidence {field.confidence:.2f}"],
                    "reason_codes": ["EXTRACTED_FROM_EXPECTED_REGION"],
                }
                for field in extraction.fields
            ],
            document_explanations=[
                {
                    "reason_code": code,
                    "message": f"Pipeline reason: {code}",
                }
                for code in ctx["decision_explainability"].get("reason_codes", [])
            ],
        )

        retention = dict(metadata.get("retention_policy") or {})

        return DocumentRecord(
            document_id=document_id,
            job_id=job_id,
            tenant_id=tenant_id,
            ingestion=IngestionRecord(
                **dict(metadata.get("ingestion") or {
                    "source": "ONLINE_PORTAL",
                    "submitted_by": {"actor_type": "SYSTEM", "actor_id": "system"},
                    "received_at": doc.get("created_at"),
                    "original_file_uri": None,
                    "perceptual_hash": hashlib.sha256(str(doc.get("raw_text", "")).encode("utf-8")).hexdigest(),
                    "dedup_matches": [],
                })
            ),
            state_machine=StateMachineRecord(current_state=str(doc.get("state")), history=state_history),
            ocr_output=ocr_output,
            classification_output=classification,
            template_definition_ref=TemplateReference(template_id=classification.template_id, template_version=classification.template_version),
            extraction_output=extraction,
            validation_output=validation,
            visual_authenticity_output=visual_auth,
            fraud_risk_output=fraud,
            issuer_verification_output=issuer,
            explainability=explainability,
            scores=ScoreRecord(
                validation_status=validation.overall_status,
                visual_authenticity_score=visual_auth.visual_authenticity_score,
                issuer_authenticity_score=issuer.issuer_authenticity_score,
                aggregate_fraud_risk_score=fraud.aggregate_fraud_risk_score,
            ),
            human_review=HumanReviewRecord(**dict(metadata.get("human_review") or {})),
            citizen_communication=CitizenCommunicationRecord(
                preferred_channels=list((metadata.get("citizen_communication") or {}).get("preferred_channels", ["SMS", "PORTAL"])),
                events=comm_events,
            ),
            offline_metadata=OfflineMetadataRecord(**dict(metadata.get("offline_metadata") or {})),
            ml_training_flags=MLTrainingFlagsRecord(**dict(metadata.get("ml_training_flags") or {})),
            retention_policy=RetentionPolicyRecord(
                policy_id=str(retention.get("policy_id", f"TENANT_{tenant_id}_DOC_RETAIN_DEFAULT")),
                retention_until=str(retention.get("retention_until", doc.get("expires_at", datetime.now(timezone.utc).isoformat()))),
                archival_status=str(retention.get("archival_status", "ACTIVE")),
            ),
        )

    def _append_human_review_event(self, doc: dict[str, Any], review_event: HumanReviewEvent) -> None:
        metadata = dict(doc.get("metadata") or {})
        human_review = dict(metadata.get("human_review") or {"review_events": []})
        review_events = list(human_review.get("review_events") or [])
        review_events.append(asdict(review_event))
        human_review["review_events"] = review_events
        metadata["human_review"] = human_review
        updated = self.repo.update_document(doc["id"], metadata=metadata)
        if updated:
            doc.update(updated)

    def _citizen_explanation(self, doc: dict[str, Any]) -> str:
        decision = str(doc.get("decision") or "PENDING")
        state = str(doc.get("state") or "UNKNOWN")
        if decision == "REJECT":
            return "Document rejected after centralized verification. Please review reasons and submit dispute if needed."
        if decision == "APPROVE":
            return "Document approved after centralized verification checks."
        if state == DocumentState.WAITING_FOR_REVIEW.value:
            return "Document is queued for officer review."
        if state == DocumentState.REVIEW_IN_PROGRESS.value:
            return "Document is currently being reviewed by an officer."
        return "Document is under processing."


def _parse_dt(value: str) -> datetime:
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return datetime.fromisoformat(value)
    except Exception:
        return datetime.now(timezone.utc)
