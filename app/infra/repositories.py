from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.domain.models import Dispute, Document, DocumentEvent, Officer, TenantPolicy
from app.domain.states import DocumentState
from app.infra.supabase_client import exec_query, get_supabase_client


ROLE_OPERATOR = "tenant_operator"
ROLE_OFFICER = "tenant_officer"
ROLE_SENIOR_OFFICER = "tenant_senior_officer"
ROLE_ADMIN = "tenant_admin"
ROLE_AUDITOR = "tenant_auditor"
ROLE_PLATFORM_SUPER_ADMIN = "platform_super_admin"
ROLE_PLATFORM_AUDITOR = "platform_auditor"

TENANT_ROLE_ALIASES = {
    # Legacy role names kept for backward compatibility with existing records.
    "case_worker": ROLE_OPERATOR,
    "reviewer": ROLE_OFFICER,
    "admin": ROLE_ADMIN,
    "auditor": ROLE_AUDITOR,
    ROLE_OPERATOR: ROLE_OPERATOR,
    ROLE_OFFICER: ROLE_OFFICER,
    ROLE_SENIOR_OFFICER: ROLE_SENIOR_OFFICER,
    ROLE_ADMIN: ROLE_ADMIN,
    ROLE_AUDITOR: ROLE_AUDITOR,
}

TENANT_WRITER_ROLES = {
    ROLE_OPERATOR,
    ROLE_OFFICER,
    ROLE_SENIOR_OFFICER,
    ROLE_ADMIN,
}
TENANT_REVIEW_ROLES = {
    ROLE_OFFICER,
    ROLE_SENIOR_OFFICER,
    ROLE_ADMIN,
}
TENANT_ADMIN_ROLES = {ROLE_ADMIN}
PLATFORM_ROLES = {ROLE_PLATFORM_SUPER_ADMIN, ROLE_PLATFORM_AUDITOR}

WRITER_ROLES = set(TENANT_WRITER_ROLES)
REVIEW_ROLES = set(TENANT_REVIEW_ROLES)
ADMIN_ROLES = set(TENANT_ADMIN_ROLES)


class MemoryStore:
    documents: dict[str, dict[str, Any]] = {}
    events: dict[str, list[dict[str, Any]]] = defaultdict(list)
    disputes: dict[str, list[dict[str, Any]]] = defaultdict(list)
    officers: dict[str, dict[str, Any]] = {}
    tenant_policies: dict[str, dict[str, Any]] = {}
    tenant_templates: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    tenant_rules: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    tenant_api_keys: list[dict[str, Any]] = []
    notifications: list[dict[str, Any]] = []
    review_escalations: list[dict[str, Any]] = []
    document_records: dict[tuple[str, str], dict[str, Any]] = {}
    model_audit_logs: list[dict[str, Any]] = []
    module_metrics: list[dict[str, Any]] = []
    review_assignments: list[dict[str, Any]] = []
    webhook_outbox: list[dict[str, Any]] = []
    correction_events: list[dict[str, Any]] = []
    correction_gate_records: list[dict[str, Any]] = []
    tenant_data_policies: dict[str, dict[str, Any]] = {}
    tenant_partition_configs: dict[str, dict[str, Any]] = {}
    operational_runbooks: list[dict[str, Any]] = []
    governance_audit_reviews: list[dict[str, Any]] = []
    platform_access_grants: dict[str, dict[str, Any]] = {}
    tenant_kpi_targets: list[dict[str, Any]] = []
    tenant_kpi_snapshots: list[dict[str, Any]] = []
    tenant_rollout_phases: list[dict[str, Any]] = []
    tenant_risk_register: list[dict[str, Any]] = []


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today_start_iso() -> str:
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()


def _hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _normalize_role(role: str) -> str:
    canonical = role.strip().lower()
    return TENANT_ROLE_ALIASES.get(canonical, canonical)


def _sanitize_tenant_for_bucket(tenant_id: str) -> str:
    return "".join(ch if ch.isalnum() or ch == "-" else "-" for ch in tenant_id.lower())


class Repository:
    def __init__(self) -> None:
        self.client, self.error = get_supabase_client()
        self.schema_gaps: list[str] = []
        self.part2_schema_ready: bool = False
        self.part3_schema_gaps: list[str] = []
        self.part3_schema_ready: bool = False
        self.part4_schema_gaps: list[str] = []
        self.part4_schema_ready: bool = False
        self.part5_schema_gaps: list[str] = []
        self.part5_schema_ready: bool = False
        if self.client and not self._probe_documents_access():
            self.client = None
            if not self.error:
                self.error = "Supabase connected but documents table access failed (check schema/RLS/key)"
        if self.client:
            self.schema_gaps = self._detect_part2_schema_gaps()
            self.part2_schema_ready = len(self.schema_gaps) == 0
            self.part3_schema_gaps = self._detect_part3_schema_gaps()
            self.part3_schema_ready = len(self.part3_schema_gaps) == 0
            self.part4_schema_gaps = self._detect_part4_schema_gaps()
            self.part4_schema_ready = len(self.part4_schema_gaps) == 0
            self.part5_schema_gaps = self._detect_part5_schema_gaps()
            self.part5_schema_ready = len(self.part5_schema_gaps) == 0
            if self.schema_gaps:
                gap_preview = ", ".join(self.schema_gaps[:4])
                self.error = (
                    "Supabase schema is outdated for Part-2 contracts. "
                    f"Missing/incompatible: {gap_preview}"
                )
            elif self.part3_schema_gaps:
                gap_preview = ", ".join(self.part3_schema_gaps[:4])
                self.error = (
                    "Supabase schema is missing Part-3 operational tables. "
                    f"Missing/incompatible: {gap_preview}"
                )
            elif self.part4_schema_gaps:
                gap_preview = ", ".join(self.part4_schema_gaps[:4])
                self.error = (
                    "Supabase schema is missing Part-4 governance tables. "
                    f"Missing/incompatible: {gap_preview}"
                )
            elif self.part5_schema_gaps:
                gap_preview = ", ".join(self.part5_schema_gaps[:4])
                self.error = (
                    "Supabase schema is missing Part-5 KPI/rollout tables. "
                    f"Missing/incompatible: {gap_preview}"
                )

    @property
    def using_supabase(self) -> bool:
        return self.client is not None

    def _probe_documents_access(self) -> bool:
        if not self.client:
            return False
        result = exec_query(self.client.table("documents").select("id").limit(1))
        return result is not None

    def _detect_part2_schema_gaps(self) -> list[str]:
        if not self.client:
            return []

        gaps: list[str] = []
        required_tables = [
            "citizen_notifications",
            "review_escalations",
            "document_records",
        ]
        for table in required_tables:
            result = exec_query(self.client.table(table).select("*").limit(1))
            if result is None:
                gaps.append(f"table:{table}")

        # Check new document_events columns used by formal audit contracts.
        result_cols = exec_query(
            self.client.table("document_events")
            .select("actor_type,actor_id,reason,policy_version,model_versions,correlation_id,causation_id")
            .limit(1)
        )
        if result_cols is None:
            gaps.append("columns:document_events.actor_*_reason_policy_model_corr")

        result_docs_cols = exec_query(
            self.client.table("documents")
            .select("last_job_id,offline_processed,offline_sync_status,provisional_decision,queue_overflow")
            .limit(1)
        )
        if result_docs_cols is None:
            gaps.append("columns:documents.part2_offline_job_fields")

        return gaps

    def _detect_part3_schema_gaps(self) -> list[str]:
        if not self.client:
            return []

        gaps: list[str] = []
        required_tables = [
            "model_audit_logs",
            "module_metrics",
            "human_review_assignments",
            "webhook_outbox",
            "correction_events",
            "correction_validation_gate",
        ]
        for table in required_tables:
            result = exec_query(self.client.table(table).select("*").limit(1))
            if result is None:
                gaps.append(f"table:{table}")
        return gaps

    def _detect_part4_schema_gaps(self) -> list[str]:
        if not self.client:
            return []

        gaps: list[str] = []
        required_tables = [
            "tenant_data_policies",
            "tenant_partition_configs",
            "operational_runbooks",
            "governance_audit_reviews",
            "platform_access_grants",
        ]
        for table in required_tables:
            result = exec_query(self.client.table(table).select("*").limit(1))
            if result is None:
                gaps.append(f"table:{table}")
        return gaps

    def _detect_part5_schema_gaps(self) -> list[str]:
        if not self.client:
            return []

        gaps: list[str] = []
        required_tables = [
            "tenant_kpi_targets",
            "tenant_kpi_snapshots",
            "tenant_rollout_phases",
            "tenant_risk_register",
        ]
        for table in required_tables:
            result = exec_query(self.client.table(table).select("*").limit(1))
            if result is None:
                gaps.append(f"table:{table}")
        return gaps

    def _default_policy(self, tenant_id: str) -> dict[str, Any]:
        policy = TenantPolicy(tenant_id=tenant_id)
        return {
            "tenant_id": policy.tenant_id,
            "data_retention_days": policy.data_retention_days,
            "api_rate_limit_per_minute": policy.api_rate_limit_per_minute,
            "max_documents_per_day": policy.max_documents_per_day,
            "cross_tenant_fraud_enabled": policy.cross_tenant_fraud_enabled,
            "export_enabled": policy.export_enabled,
            "sms_enabled": policy.sms_enabled,
            "email_enabled": policy.email_enabled,
            "portal_enabled": policy.portal_enabled,
            "whatsapp_enabled": policy.whatsapp_enabled,
            "review_sla_days": policy.review_sla_days,
            "escalation_step_days": policy.escalation_step_days,
            "residency_region": policy.residency_region,
            "created_at": policy.created_at,
            "updated_at": policy.updated_at,
        }

    def _default_template(self, tenant_id: str, document_type: str) -> dict[str, Any]:
        dtype = (document_type or "UNKNOWN").upper()
        return {
            "tenant_id": tenant_id,
            "document_type": dtype,
            "template_id": f"tpl_{dtype.lower()}",
            "template_version": "2025.1.0",
            "version": 1,
            "is_active": True,
            "config": {},
            "created_at": _now(),
        }

    def _default_rule(self, tenant_id: str, document_type: str) -> dict[str, Any]:
        dtype = (document_type or "UNKNOWN").upper()
        return {
            "tenant_id": tenant_id,
            "document_type": dtype,
            "rule_name": f"rule_{dtype.lower()}",
            "rule_set_id": f"RULESET_{dtype}_DEFAULT",
            "version": 1,
            "is_active": True,
            "min_extract_confidence": 0.6,
            "min_approval_confidence": 0.72,
            "max_approval_risk": 0.35,
            "registry_required": True,
            "config": {},
            "created_at": _now(),
        }

    def _default_data_policy(self, tenant_id: str) -> dict[str, Any]:
        return {
            "tenant_id": tenant_id,
            "raw_image_retention_years": 7,
            "structured_data_retention_years": 10,
            "fraud_logs_retention_years": 10,
            "archival_strategy": "COLD_STORAGE_AFTER_RETENTION_WINDOW",
            "purge_policy": "ANONYMIZE_AFTER_EXPIRY",
            "training_data_policy": "PSEUDONYMIZED_AND_GATED",
            "legal_basis": "SERVICE_DELIVERY_FRAUD_PREVENTION_AUDIT",
            "updated_at": _now(),
        }

    def _default_partition_config(self, tenant_id: str) -> dict[str, Any]:
        return {
            "tenant_id": tenant_id,
            "partition_mode": "LOGICAL_SHARED",
            "residency_region": "default",
            "region_cluster": "region-a",
            "physical_isolation_required": False,
            "sensitivity_tier": "STANDARD",
            "updated_at": _now(),
        }

    def _default_kpi_targets(self, tenant_id: str) -> list[dict[str, Any]]:
        now = _now()
        return [
            {"tenant_id": tenant_id, "kpi_key": "processing_time_online_sec", "target_value": 5.0, "unit": "seconds", "direction": "LTE", "description": "Avg. processing time per document (online)", "updated_at": now},
            {"tenant_id": tenant_id, "kpi_key": "ocr_accuracy_major_scripts_pct", "target_value": 90.0, "unit": "percent", "direction": "GTE", "description": "OCR accuracy for major scripts", "updated_at": now},
            {"tenant_id": tenant_id, "kpi_key": "template_classification_accuracy_pct", "target_value": 95.0, "unit": "percent", "direction": "GTE", "description": "Template classification accuracy", "updated_at": now},
            {"tenant_id": tenant_id, "kpi_key": "tamper_detection_recall_pct", "target_value": 85.0, "unit": "percent", "direction": "GTE", "description": "Tamper detection recall", "updated_at": now},
            {"tenant_id": tenant_id, "kpi_key": "fraud_flag_precision_pct", "target_value": 80.0, "unit": "percent", "direction": "GTE", "description": "Fraud flag precision", "updated_at": now},
            {"tenant_id": tenant_id, "kpi_key": "fraud_false_positive_rate_pct", "target_value": 10.0, "unit": "percent", "direction": "LTE", "description": "Fraud false-positive rate", "updated_at": now},
            {"tenant_id": tenant_id, "kpi_key": "auto_clear_rate_pct", "target_value": 70.0, "unit": "percent", "direction": "GTE", "description": "Auto-cleared documents after 6 months", "updated_at": now},
            {"tenant_id": tenant_id, "kpi_key": "review_queue_turnaround_hours", "target_value": 24.0, "unit": "hours", "direction": "LTE", "description": "Review queue turnaround", "updated_at": now},
            {"tenant_id": tenant_id, "kpi_key": "availability_pct", "target_value": 99.9, "unit": "percent", "direction": "GTE", "description": "Platform availability", "updated_at": now},
            {"tenant_id": tenant_id, "kpi_key": "rpo_minutes", "target_value": 5.0, "unit": "minutes", "direction": "LTE", "description": "Recovery Point Objective", "updated_at": now},
            {"tenant_id": tenant_id, "kpi_key": "rto_minutes", "target_value": 15.0, "unit": "minutes", "direction": "LTE", "description": "Recovery Time Objective", "updated_at": now},
            {"tenant_id": tenant_id, "kpi_key": "sync_backlog_clearance_minutes", "target_value": 60.0, "unit": "minutes", "direction": "LTE", "description": "Sync backlog clearance after outage", "updated_at": now},
            {"tenant_id": tenant_id, "kpi_key": "audit_traceability_pct", "target_value": 100.0, "unit": "percent", "direction": "GTE", "description": "Decisions audit-traceable", "updated_at": now},
            {"tenant_id": tenant_id, "kpi_key": "cross_tenant_isolation_proven_pct", "target_value": 100.0, "unit": "percent", "direction": "GTE", "description": "Cross-tenant isolation proof coverage", "updated_at": now},
            {"tenant_id": tenant_id, "kpi_key": "unauthorized_access_incidents", "target_value": 0.0, "unit": "count", "direction": "LTE", "description": "Unauthorized access incidents", "updated_at": now},
        ]

    def _default_rollout_phases(self, tenant_id: str) -> list[dict[str, Any]]:
        now = _now()
        return [
            {"tenant_id": tenant_id, "phase_key": "PHASE_0_FOUNDATION", "title": "Foundation", "duration_months_min": 2, "duration_months_max": 3, "status": "PLANNED", "description": "DAG orchestration, OCR/classification/extraction core, onboarding baseline monitoring", "updated_at": now},
            {"tenant_id": tenant_id, "phase_key": "PHASE_1_CORE_AUTOMATION", "title": "Core Automation", "duration_months_min": 3, "duration_months_max": 4, "status": "PLANNED", "description": "Authenticity, validation, fraud, event backbone, offline MVP, onboarding 3-5 departments", "updated_at": now},
            {"tenant_id": tenant_id, "phase_key": "PHASE_2_TRUST_GOVERNANCE", "title": "Trust and Governance", "duration_months_min": 3, "duration_months_max": 3, "status": "PLANNED", "description": "Issuer integrations, advanced fraud, explainability v2, DR/failover, audit dashboards, correction gate", "updated_at": now},
            {"tenant_id": tenant_id, "phase_key": "PHASE_3_SCALE_EXPANSION", "title": "Scale and Expansion", "duration_months_min": 6, "duration_months_max": 12, "status": "PLANNED", "description": "20+ departments, full offline-first, handwriting OCR selective, fraud workspace, multi-region", "updated_at": now},
        ]

    def _default_risk_register(self, tenant_id: str) -> list[dict[str, Any]]:
        now = _now()
        return [
            {"tenant_id": tenant_id, "risk_code": "RISK_1_DOC_QUALITY", "title": "Variability in document quality", "mitigation": "Preprocessing + quality scoring + fallback to review", "owner_team": "Platform Ops", "impact": "HIGH", "likelihood": "HIGH", "status": "OPEN", "updated_at": now},
            {"tenant_id": tenant_id, "risk_code": "RISK_2_MARKER_FN", "title": "Stamp/signature false negatives", "mitigation": "Conservative thresholds + human escalation", "owner_team": "Fraud Team", "impact": "HIGH", "likelihood": "MEDIUM", "status": "OPEN", "updated_at": now},
            {"tenant_id": tenant_id, "risk_code": "RISK_3_MODEL_OVERRELIANCE", "title": "Over-reliance on model output", "mitigation": "Human-in-loop + explainability + controlled MLOps", "owner_team": "Governance", "impact": "HIGH", "likelihood": "MEDIUM", "status": "OPEN", "updated_at": now},
            {"tenant_id": tenant_id, "risk_code": "RISK_4_NOISY_CORRECTIONS", "title": "Noisy corrections poisoning training data", "mitigation": "Correction Validation Gate + sampling QA", "owner_team": "ML Team", "impact": "HIGH", "likelihood": "MEDIUM", "status": "OPEN", "updated_at": now},
            {"tenant_id": tenant_id, "risk_code": "RISK_5_OFFLINE_CONFLICTS", "title": "Offline contradictions", "mitigation": "Local provisional policy, central truth authoritative", "owner_team": "Program", "impact": "MEDIUM", "likelihood": "HIGH", "status": "OPEN", "updated_at": now},
            {"tenant_id": tenant_id, "risk_code": "RISK_6_TENANT_LEAK", "title": "Cross-tenant data leakage", "mitigation": "RLS + storage isolation + RBAC + monitoring", "owner_team": "Security", "impact": "CRITICAL", "likelihood": "LOW", "status": "OPEN", "updated_at": now},
            {"tenant_id": tenant_id, "risk_code": "RISK_7_OFFLINE_BURST", "title": "Mass sync load spike", "mitigation": "Rate limiting + autoscaling + overflow queues", "owner_team": "SRE", "impact": "HIGH", "likelihood": "MEDIUM", "status": "OPEN", "updated_at": now},
            {"tenant_id": tenant_id, "risk_code": "RISK_8_RULE_DRIFT", "title": "Inconsistent department rules", "mitigation": "Versioned rule engine + shadow rollout", "owner_team": "Tenant Admin", "impact": "MEDIUM", "likelihood": "MEDIUM", "status": "OPEN", "updated_at": now},
        ]

    def create_document(self, doc: Document) -> dict[str, Any]:
        row = {
            "id": doc.id,
            "tenant_id": doc.tenant_id,
            "citizen_id": doc.citizen_id,
            "file_name": doc.file_name,
            "raw_text": doc.raw_text,
            "metadata": doc.metadata,
            "state": doc.state.value,
            "dedup_hash": doc.dedup_hash,
            "confidence": doc.confidence,
            "risk_score": doc.risk_score,
            "decision": doc.decision,
            "template_id": doc.template_id,
            "expires_at": doc.expires_at,
            "created_at": doc.created_at,
            "updated_at": doc.updated_at,
        }
        if self.client:
            exec_query(self.client.table("documents").insert(row))
            remote = self.get_document(doc.id, tenant_id=doc.tenant_id)
            if remote:
                return remote

        MemoryStore.documents[doc.id] = row
        return row

    def update_document(self, document_id: str, **updates: Any) -> dict[str, Any] | None:
        updates["updated_at"] = _now()
        if "state" in updates and isinstance(updates["state"], DocumentState):
            updates["state"] = updates["state"].value

        if self.client:
            exec_query(self.client.table("documents").update(updates).eq("id", document_id))
            row = self.get_document(document_id)
            if row:
                return row

        row = MemoryStore.documents.get(document_id)
        if not row:
            return None
        row.update(updates)
        return row

    def get_document(self, document_id: str, tenant_id: str | None = None) -> dict[str, Any] | None:
        if self.client:
            query = self.client.table("documents").select("*").eq("id", document_id).limit(1)
            if tenant_id:
                query = query.eq("tenant_id", tenant_id)
            result = exec_query(query)
            if result and result.get("data"):
                rows = result["data"]
                return rows[0] if rows else None

        row = MemoryStore.documents.get(document_id)
        if not row:
            return None
        if tenant_id and row.get("tenant_id") != tenant_id:
            return None
        return row

    def list_documents(self, tenant_id: str) -> list[dict[str, Any]]:
        if self.client:
            result = exec_query(
                self.client.table("documents")
                .select("*")
                .eq("tenant_id", tenant_id)
                .order("created_at", desc=True)
            )
            if result and result.get("data") is not None:
                return result["data"]

        rows = [r for r in MemoryStore.documents.values() if r.get("tenant_id") == tenant_id]
        return sorted(rows, key=lambda x: x["created_at"], reverse=True)

    def list_platform_tenants(self) -> list[dict[str, Any]]:
        if self.client:
            result = exec_query(self.client.table("tenants").select("*").order("tenant_id", desc=False))
            if result and result.get("data") is not None:
                return result["data"]

        tenant_ids = sorted({r.get("tenant_id") for r in MemoryStore.documents.values() if r.get("tenant_id")})
        rows = [{"tenant_id": tid, "display_name": tid, "residency_region": "default"} for tid in tenant_ids]
        if not rows:
            # fallback from policies if documents are empty
            rows = [
                {"tenant_id": tid, "display_name": tid, "residency_region": p.get("residency_region", "default")}
                for tid, p in MemoryStore.tenant_policies.items()
            ]
        return rows

    def list_documents_by_state(self, tenant_id: str, state: DocumentState) -> list[dict[str, Any]]:
        if self.client:
            result = exec_query(
                self.client.table("documents")
                .select("*")
                .eq("tenant_id", tenant_id)
                .eq("state", state.value)
                .order("created_at", desc=False)
            )
            if result and result.get("data") is not None:
                return result["data"]

        rows = [r for r in MemoryStore.documents.values() if r.get("tenant_id") == tenant_id and r.get("state") == state.value]
        return sorted(rows, key=lambda x: x["created_at"])

    def count_documents_created_today(self, tenant_id: str) -> int:
        start_iso = _today_start_iso()
        if self.client:
            result = exec_query(
                self.client.table("documents")
                .select("id", count="exact")
                .eq("tenant_id", tenant_id)
                .gte("created_at", start_iso)
            )
            if result is not None:
                data = result.get("data") or []
                return len(data)

        count = 0
        for row in MemoryStore.documents.values():
            if row.get("tenant_id") != tenant_id:
                continue
            if str(row.get("created_at", "")) >= start_iso:
                count += 1
        return count

    def count_by_hash(self, tenant_id: str, dedup_hash: str, exclude_document_id: str | None = None) -> int:
        if self.client:
            query = (
                self.client.table("documents")
                .select("id", count="exact")
                .eq("tenant_id", tenant_id)
                .eq("dedup_hash", dedup_hash)
            )
            if exclude_document_id:
                query = query.neq("id", exclude_document_id)
            result = exec_query(query)
            if result is not None:
                data = result.get("data") or []
                return len(data)

        count = 0
        for row in MemoryStore.documents.values():
            if row.get("tenant_id") != tenant_id:
                continue
            if row.get("dedup_hash") != dedup_hash:
                continue
            if exclude_document_id and row.get("id") == exclude_document_id:
                continue
            count += 1
        return count

    def count_by_hash_global(self, dedup_hash: str, exclude_document_id: str | None = None) -> int:
        if self.client:
            query = self.client.table("documents").select("id", count="exact").eq("dedup_hash", dedup_hash)
            if exclude_document_id:
                query = query.neq("id", exclude_document_id)
            result = exec_query(query)
            if result is not None:
                data = result.get("data") or []
                return len(data)

        count = 0
        for row in MemoryStore.documents.values():
            if row.get("dedup_hash") != dedup_hash:
                continue
            if exclude_document_id and row.get("id") == exclude_document_id:
                continue
            count += 1
        return count

    def add_event(self, event: DocumentEvent) -> dict[str, Any]:
        row = {
            "id": event.id,
            "document_id": event.document_id,
            "tenant_id": event.tenant_id,
            "actor_type": event.actor_type,
            "actor_id": event.actor_id,
            "event_type": event.event_type,
            "payload": event.payload,
            "reason": event.reason,
            "policy_version": event.policy_version,
            "model_versions": event.model_versions or {},
            "correlation_id": event.correlation_id,
            "causation_id": event.causation_id,
            "created_at": event.created_at,
        }
        if self.client:
            exec_query(self.client.table("document_events").insert(row))
            result = exec_query(self.client.table("document_events").select("*").eq("id", event.id).limit(1))
            if result and result.get("data"):
                data = result["data"]
                return data[0] if data else row

        MemoryStore.events[event.document_id].append(row)
        return row

    def list_events(self, document_id: str, tenant_id: str | None = None) -> list[dict[str, Any]]:
        if self.client:
            query = (
                self.client.table("document_events")
                .select("*")
                .eq("document_id", document_id)
                .order("created_at", desc=False)
            )
            if tenant_id:
                query = query.eq("tenant_id", tenant_id)
            result = exec_query(query)
            if result and result.get("data") is not None:
                data = result["data"]
                if data:
                    return data

        rows = MemoryStore.events.get(document_id, [])
        if tenant_id:
            rows = [r for r in rows if r.get("tenant_id") == tenant_id]
        return rows

    def list_events_by_tenant(self, tenant_id: str) -> list[dict[str, Any]]:
        if self.client:
            result = exec_query(
                self.client.table("document_events")
                .select("*")
                .eq("tenant_id", tenant_id)
                .order("created_at", desc=False)
            )
            if result and result.get("data") is not None:
                data = result["data"]
                if data:
                    return data

        rows: list[dict[str, Any]] = []
        for doc_events in MemoryStore.events.values():
            rows.extend([r for r in doc_events if r.get("tenant_id") == tenant_id])
        return sorted(rows, key=lambda x: x["created_at"])

    def create_dispute(self, dispute: Dispute) -> dict[str, Any]:
        row = {
            "id": dispute.id,
            "document_id": dispute.document_id,
            "tenant_id": dispute.tenant_id,
            "reason": dispute.reason,
            "evidence_note": dispute.evidence_note,
            "status": dispute.status,
            "created_at": dispute.created_at,
        }
        if self.client:
            exec_query(self.client.table("disputes").insert(row))
            result = exec_query(self.client.table("disputes").select("*").eq("id", dispute.id).limit(1))
            if result and result.get("data"):
                data = result["data"]
                return data[0] if data else row

        MemoryStore.disputes[dispute.document_id].append(row)
        return row

    def list_disputes(self, tenant_id: str) -> list[dict[str, Any]]:
        if self.client:
            result = exec_query(
                self.client.table("disputes")
                .select("*")
                .eq("tenant_id", tenant_id)
                .order("created_at", desc=True)
            )
            if result and result.get("data") is not None:
                return result["data"]

        rows: list[dict[str, Any]] = []
        for items in MemoryStore.disputes.values():
            rows.extend([it for it in items if it.get("tenant_id") == tenant_id])
        return sorted(rows, key=lambda x: x["created_at"], reverse=True)

    def create_notification(
        self,
        *,
        tenant_id: str,
        document_id: str,
        citizen_id: str,
        channel: str,
        event_type: str,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        row = {
            "tenant_id": tenant_id,
            "document_id": document_id,
            "citizen_id": citizen_id,
            "channel": channel,
            "event_type": event_type,
            "message": message,
            "status": "SENT",
            "metadata": metadata or {},
            "created_at": _now(),
            "sent_at": _now(),
        }
        if self.client:
            result = exec_query(self.client.table("citizen_notifications").insert(row))
            if result and result.get("data"):
                data = result["data"]
                return data[0] if data else row

        MemoryStore.notifications.append(row)
        return row

    def list_notifications(self, tenant_id: str, document_id: str | None = None) -> list[dict[str, Any]]:
        if self.client:
            query = self.client.table("citizen_notifications").select("*").eq("tenant_id", tenant_id).order("created_at", desc=False)
            if document_id:
                query = query.eq("document_id", document_id)
            result = exec_query(query)
            if result and result.get("data") is not None:
                return result["data"]

        rows = [n for n in MemoryStore.notifications if n.get("tenant_id") == tenant_id]
        if document_id:
            rows = [n for n in rows if n.get("document_id") == document_id]
        return rows

    def create_review_escalation(
        self,
        *,
        tenant_id: str,
        document_id: str,
        escalation_level: int,
        assignee_role: str,
        reason: str,
    ) -> dict[str, Any]:
        row = {
            "tenant_id": tenant_id,
            "document_id": document_id,
            "escalation_level": escalation_level,
            "assignee_role": assignee_role,
            "reason": reason,
            "status": "OPEN",
            "created_at": _now(),
            "resolved_at": None,
        }

        if self.client:
            result = exec_query(self.client.table("review_escalations").insert(row))
            if result and result.get("data"):
                data = result["data"]
                return data[0] if data else row

        MemoryStore.review_escalations.append(row)
        return row

    def list_review_escalations(self, tenant_id: str, only_open: bool = True) -> list[dict[str, Any]]:
        if self.client:
            query = self.client.table("review_escalations").select("*").eq("tenant_id", tenant_id).order("created_at", desc=False)
            if only_open:
                query = query.eq("status", "OPEN")
            result = exec_query(query)
            if result and result.get("data") is not None:
                return result["data"]

        rows = [r for r in MemoryStore.review_escalations if r.get("tenant_id") == tenant_id]
        if only_open:
            rows = [r for r in rows if r.get("status") == "OPEN"]
        return rows

    def save_document_record(self, tenant_id: str, document_id: str, job_id: str, schema_version: str, record: dict[str, Any]) -> dict[str, Any]:
        row = {
            "tenant_id": tenant_id,
            "document_id": document_id,
            "job_id": job_id,
            "schema_version": schema_version,
            "record": record,
            "created_at": _now(),
        }
        if self.client:
            exec_query(self.client.table("document_records").upsert(row, on_conflict="document_id,job_id"))
            result = exec_query(
                self.client.table("document_records")
                .select("*")
                .eq("tenant_id", tenant_id)
                .eq("document_id", document_id)
                .eq("job_id", job_id)
                .limit(1)
            )
            if result and result.get("data"):
                data = result["data"]
                return data[0] if data else row

        MemoryStore.document_records[(document_id, job_id)] = row
        return row

    def get_latest_document_record(self, tenant_id: str, document_id: str) -> dict[str, Any] | None:
        if self.client:
            result = exec_query(
                self.client.table("document_records")
                .select("*")
                .eq("tenant_id", tenant_id)
                .eq("document_id", document_id)
                .order("created_at", desc=True)
                .limit(1)
            )
            if result and result.get("data"):
                rows = result["data"]
                return rows[0] if rows else None

        rows = [v for (doc_id, _job), v in MemoryStore.document_records.items() if doc_id == document_id and v.get("tenant_id") == tenant_id]
        rows = sorted(rows, key=lambda x: x.get("created_at", ""), reverse=True)
        return rows[0] if rows else None

    def create_model_audit_log(
        self,
        *,
        tenant_id: str,
        document_id: str,
        job_id: str,
        module_name: str,
        model_id: str,
        model_version: str,
        input_ref: dict[str, Any],
        output: dict[str, Any],
        reason_codes: list[str],
        actor_type: str = "SYSTEM",
        actor_id: str | None = None,
    ) -> dict[str, Any]:
        row = {
            "id": str(uuid4()),
            "tenant_id": tenant_id,
            "document_id": document_id,
            "job_id": job_id,
            "module_name": module_name,
            "model_id": model_id,
            "model_version": model_version,
            "input_ref": input_ref,
            "output": output,
            "reason_codes": reason_codes,
            "actor_type": actor_type,
            "actor_id": actor_id,
            "created_at": _now(),
        }
        if self.client:
            result = exec_query(self.client.table("model_audit_logs").insert(row))
            if result and result.get("data"):
                data = result["data"]
                return data[0] if data else row

        MemoryStore.model_audit_logs.append(row)
        return row

    def list_model_audit_logs(self, tenant_id: str, document_id: str | None = None) -> list[dict[str, Any]]:
        if self.client:
            query = self.client.table("model_audit_logs").select("*").eq("tenant_id", tenant_id).order("created_at", desc=False)
            if document_id:
                query = query.eq("document_id", document_id)
            result = exec_query(query)
            if result and result.get("data") is not None:
                return result["data"]

        rows = [r for r in MemoryStore.model_audit_logs if r.get("tenant_id") == tenant_id]
        if document_id:
            rows = [r for r in rows if r.get("document_id") == document_id]
        return rows

    def create_module_metric(
        self,
        *,
        tenant_id: str,
        document_id: str,
        job_id: str,
        module_name: str,
        latency_ms: float,
        status: str,
        metric_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        row = {
            "id": str(uuid4()),
            "tenant_id": tenant_id,
            "document_id": document_id,
            "job_id": job_id,
            "module_name": module_name,
            "latency_ms": float(latency_ms),
            "status": status,
            "metric_payload": metric_payload or {},
            "created_at": _now(),
        }
        if self.client:
            result = exec_query(self.client.table("module_metrics").insert(row))
            if result and result.get("data"):
                data = result["data"]
                return data[0] if data else row

        MemoryStore.module_metrics.append(row)
        return row

    def list_module_metrics(self, tenant_id: str, limit: int = 500) -> list[dict[str, Any]]:
        if self.client:
            result = exec_query(
                self.client.table("module_metrics")
                .select("*")
                .eq("tenant_id", tenant_id)
                .order("created_at", desc=True)
                .limit(limit)
            )
            if result and result.get("data") is not None:
                return result["data"]

        rows = [r for r in MemoryStore.module_metrics if r.get("tenant_id") == tenant_id]
        rows.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return rows[:limit]

    def create_review_assignment(
        self,
        *,
        tenant_id: str,
        document_id: str,
        queue_name: str,
        policy: str,
        priority: int,
    ) -> dict[str, Any]:
        row = {
            "id": str(uuid4()),
            "tenant_id": tenant_id,
            "document_id": document_id,
            "queue_name": queue_name,
            "assignment_policy": policy,
            "priority": int(priority),
            "status": "WAITING_FOR_REVIEW",
            "assigned_officer_id": None,
            "claimed_at": None,
            "resolved_at": None,
            "created_at": _now(),
        }
        if self.client:
            result = exec_query(self.client.table("human_review_assignments").insert(row))
            if result and result.get("data"):
                data = result["data"]
                return data[0] if data else row

        MemoryStore.review_assignments.append(row)
        return row

    def claim_review_assignment(self, *, assignment_id: str, officer_id: str) -> dict[str, Any]:
        updates = {
            "assigned_officer_id": officer_id,
            "status": "REVIEW_IN_PROGRESS",
            "claimed_at": _now(),
        }
        if self.client:
            exec_query(self.client.table("human_review_assignments").update(updates).eq("id", assignment_id))
            result = exec_query(self.client.table("human_review_assignments").select("*").eq("id", assignment_id).limit(1))
            if result and result.get("data"):
                data = result["data"]
                return data[0] if data else {"id": assignment_id, **updates}

        for row in MemoryStore.review_assignments:
            if str(row.get("id")) == assignment_id:
                row.update(updates)
                return row
        return {"id": assignment_id, **updates}

    def reserve_review_assignment(self, *, assignment_id: str, officer_id: str) -> dict[str, Any]:
        updates = {
            "assigned_officer_id": officer_id,
        }
        if self.client:
            exec_query(self.client.table("human_review_assignments").update(updates).eq("id", assignment_id))
            result = exec_query(self.client.table("human_review_assignments").select("*").eq("id", assignment_id).limit(1))
            if result and result.get("data"):
                data = result["data"]
                return data[0] if data else {"id": assignment_id, **updates}

        for row in MemoryStore.review_assignments:
            if str(row.get("id")) == assignment_id:
                row.update(updates)
                return row
        return {"id": assignment_id, **updates}

    def resolve_review_assignment(self, *, document_id: str, tenant_id: str, status: str = "RESOLVED") -> None:
        updates = {"status": status, "resolved_at": _now()}
        if self.client:
            exec_query(
                self.client.table("human_review_assignments")
                .update(updates)
                .eq("tenant_id", tenant_id)
                .eq("document_id", document_id)
                .in_("status", ["WAITING_FOR_REVIEW", "REVIEW_IN_PROGRESS"])
            )
            return

        for row in MemoryStore.review_assignments:
            if row.get("tenant_id") == tenant_id and row.get("document_id") == document_id and row.get("status") in {"WAITING_FOR_REVIEW", "REVIEW_IN_PROGRESS"}:
                row.update(updates)

    def list_review_assignments(self, tenant_id: str, status: str | None = None) -> list[dict[str, Any]]:
        if self.client:
            query = self.client.table("human_review_assignments").select("*").eq("tenant_id", tenant_id).order("priority", desc=True)
            if status:
                query = query.eq("status", status)
            result = exec_query(query)
            if result and result.get("data") is not None:
                return result["data"]

        rows = [r for r in MemoryStore.review_assignments if r.get("tenant_id") == tenant_id]
        if status:
            rows = [r for r in rows if r.get("status") == status]
        rows.sort(key=lambda x: x.get("priority", 0), reverse=True)
        return rows

    def pick_officer_for_assignment(self, *, tenant_id: str, preferred_doc_type: str | None = None) -> str | None:
        officers = [
            row
            for row in self.list_officers(tenant_id)
            if str(row.get("status", "ACTIVE")) == "ACTIVE"
            and _normalize_role(str(row.get("role", ""))) in TENANT_REVIEW_ROLES
        ]
        if not officers:
            return None

        workload: dict[str, int] = {}
        for officer in officers:
            officer_id = str(officer.get("officer_id"))
            open_count = 0
            for assignment in self.list_review_assignments(tenant_id):
                if assignment.get("assigned_officer_id") == officer_id and assignment.get("status") in {"WAITING_FOR_REVIEW", "REVIEW_IN_PROGRESS"}:
                    open_count += 1
            workload[officer_id] = open_count

        return sorted(workload.items(), key=lambda item: (item[1], item[0]))[0][0]

    def enqueue_webhook(self, *, tenant_id: str, document_id: str, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        row = {
            "id": str(uuid4()),
            "tenant_id": tenant_id,
            "document_id": document_id,
            "event_type": event_type,
            "payload": payload,
            "status": "PENDING",
            "attempt_count": 0,
            "last_error": None,
            "created_at": _now(),
            "dispatched_at": None,
        }
        if self.client:
            result = exec_query(self.client.table("webhook_outbox").insert(row))
            if result and result.get("data"):
                data = result["data"]
                return data[0] if data else row

        MemoryStore.webhook_outbox.append(row)
        return row

    def list_webhook_outbox(self, tenant_id: str, status: str = "PENDING") -> list[dict[str, Any]]:
        if self.client:
            result = exec_query(
                self.client.table("webhook_outbox")
                .select("*")
                .eq("tenant_id", tenant_id)
                .eq("status", status)
                .order("created_at", desc=False)
            )
            if result and result.get("data") is not None:
                return result["data"]

        return [r for r in MemoryStore.webhook_outbox if r.get("tenant_id") == tenant_id and r.get("status") == status]

    def create_correction_event(
        self,
        *,
        tenant_id: str,
        document_id: str,
        field_name: str,
        old_value: str | None,
        new_value: str | None,
        officer_id: str,
        reason: str,
    ) -> dict[str, Any]:
        row = {
            "id": str(uuid4()),
            "tenant_id": tenant_id,
            "document_id": document_id,
            "field_name": field_name,
            "old_value": old_value,
            "new_value": new_value,
            "officer_id": officer_id,
            "reason": reason,
            "created_at": _now(),
        }
        if self.client:
            result = exec_query(self.client.table("correction_events").insert(row))
            if result and result.get("data"):
                data = result["data"]
                return data[0] if data else row

        MemoryStore.correction_events.append(row)
        return row

    def count_conflicting_corrections(
        self,
        *,
        tenant_id: str,
        document_id: str,
        field_name: str,
        expected_value: str | None,
    ) -> int:
        if self.client:
            result = exec_query(
                self.client.table("correction_events")
                .select("*")
                .eq("tenant_id", tenant_id)
                .eq("document_id", document_id)
                .eq("field_name", field_name)
            )
            if result and result.get("data") is not None:
                rows = result["data"]
                return len([r for r in rows if r.get("new_value") != expected_value])

        rows = [
            r
            for r in MemoryStore.correction_events
            if r.get("tenant_id") == tenant_id and r.get("document_id") == document_id and r.get("field_name") == field_name
        ]
        return len([r for r in rows if r.get("new_value") != expected_value])

    def create_correction_gate_record(
        self,
        *,
        tenant_id: str,
        document_id: str,
        correction_event_id: str,
        status: str,
        qa_required: bool,
        notes: list[str],
    ) -> dict[str, Any]:
        row = {
            "id": str(uuid4()),
            "tenant_id": tenant_id,
            "document_id": document_id,
            "correction_event_id": correction_event_id,
            "status": status,
            "qa_required": qa_required,
            "notes": notes,
            "created_at": _now(),
            "validated_at": None,
        }
        if self.client:
            result = exec_query(self.client.table("correction_validation_gate").insert(row))
            if result and result.get("data"):
                data = result["data"]
                return data[0] if data else row

        MemoryStore.correction_gate_records.append(row)
        return row

    def list_correction_gate_records(self, tenant_id: str, status: str | None = None) -> list[dict[str, Any]]:
        if self.client:
            query = self.client.table("correction_validation_gate").select("*").eq("tenant_id", tenant_id).order("created_at", desc=True)
            if status:
                query = query.eq("status", status)
            result = exec_query(query)
            if result and result.get("data") is not None:
                return result["data"]

        rows = [r for r in MemoryStore.correction_gate_records if r.get("tenant_id") == tenant_id]
        if status:
            rows = [r for r in rows if r.get("status") == status]
        return rows

    def list_correction_events(self, tenant_id: str, document_id: str | None = None, limit: int = 1000) -> list[dict[str, Any]]:
        if self.client:
            query = (
                self.client.table("correction_events")
                .select("*")
                .eq("tenant_id", tenant_id)
                .order("created_at", desc=True)
                .limit(limit)
            )
            if document_id:
                query = query.eq("document_id", document_id)
            result = exec_query(query)
            if result and result.get("data") is not None:
                return result["data"]

        rows = [r for r in MemoryStore.correction_events if r.get("tenant_id") == tenant_id]
        if document_id:
            rows = [r for r in rows if r.get("document_id") == document_id]
        rows.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return rows[:limit]

    def get_correction_event(self, correction_event_id: str) -> dict[str, Any] | None:
        if self.client:
            result = exec_query(
                self.client.table("correction_events")
                .select("*")
                .eq("id", correction_event_id)
                .limit(1)
            )
            if result and result.get("data"):
                rows = result["data"]
                return rows[0] if rows else None

        for row in MemoryStore.correction_events:
            if str(row.get("id")) == correction_event_id:
                return row
        return None

    def update_correction_gate_record(
        self,
        gate_id: str,
        *,
        status: str,
        notes: list[str] | None = None,
        validated_at: str | None = None,
    ) -> dict[str, Any] | None:
        updates: dict[str, Any] = {"status": status}
        if notes is not None:
            updates["notes"] = notes
        updates["validated_at"] = validated_at or _now()

        if self.client:
            exec_query(self.client.table("correction_validation_gate").update(updates).eq("id", gate_id))
            result = exec_query(
                self.client.table("correction_validation_gate")
                .select("*")
                .eq("id", gate_id)
                .limit(1)
            )
            if result and result.get("data"):
                rows = result["data"]
                return rows[0] if rows else None

        for row in MemoryStore.correction_gate_records:
            if str(row.get("id")) == gate_id:
                row.update(updates)
                return row
        return None

    def list_pending_offline_documents(self, tenant_id: str, limit: int = 200) -> list[dict[str, Any]]:
        statuses = ["PENDING", "QUEUE_OVERFLOW"]
        if self.client:
            result = exec_query(
                self.client.table("documents")
                .select("*")
                .eq("tenant_id", tenant_id)
                .eq("offline_processed", True)
                .in_("offline_sync_status", statuses)
                .order("created_at", desc=False)
                .limit(limit)
            )
            if result and result.get("data") is not None:
                return result["data"]

        rows = [
            r
            for r in MemoryStore.documents.values()
            if r.get("tenant_id") == tenant_id
            and bool(r.get("offline_processed"))
            and str(r.get("offline_sync_status", "")).upper() in statuses
        ]
        rows.sort(key=lambda x: x.get("created_at", ""))
        return rows[:limit]

    def export_documents_for_tenant(self, tenant_id: str, include_raw_text: bool = False) -> list[dict[str, Any]]:
        docs = self.list_documents(tenant_id)
        if include_raw_text:
            return docs
        redacted: list[dict[str, Any]] = []
        for row in docs:
            item = dict(row)
            item.pop("raw_text", None)
            redacted.append(item)
        return redacted

    def upsert_officer(self, officer: Officer) -> dict[str, Any]:
        row = {
            "officer_id": officer.officer_id,
            "tenant_id": officer.tenant_id,
            "role": _normalize_role(officer.role),
            "status": officer.status,
            "created_at": officer.created_at,
        }

        if self.client:
            exec_query(self.client.table("officers").upsert(row, on_conflict="officer_id"))
            result = exec_query(self.client.table("officers").select("*").eq("officer_id", officer.officer_id).limit(1))
            if result and result.get("data"):
                data = result["data"]
                return data[0] if data else row

        MemoryStore.officers[officer.officer_id] = row
        return row

    def get_officer(self, officer_id: str) -> dict[str, Any] | None:
        if self.client:
            result = exec_query(self.client.table("officers").select("*").eq("officer_id", officer_id).limit(1))
            if result and result.get("data"):
                rows = result["data"]
                return rows[0] if rows else None

        return MemoryStore.officers.get(officer_id)

    def list_officers(self, tenant_id: str) -> list[dict[str, Any]]:
        if self.client:
            result = exec_query(
                self.client.table("officers")
                .select("*")
                .eq("tenant_id", tenant_id)
                .order("officer_id", desc=False)
            )
            if result and result.get("data") is not None:
                return result["data"]

        rows = [r for r in MemoryStore.officers.values() if r.get("tenant_id") == tenant_id]
        return sorted(rows, key=lambda x: str(x.get("officer_id", "")))

    def assert_officer_access(self, officer_id: str, tenant_id: str, allowed_roles: set[str] | None = None) -> dict[str, Any]:
        row = self.get_officer(officer_id)
        if not row:
            raise PermissionError("Officer not registered")

        if row.get("status") != "ACTIVE":
            raise PermissionError("Officer is not active")

        if row.get("tenant_id") != tenant_id:
            raise PermissionError("Officer is bound to a different tenant")

        role = _normalize_role(str(row.get("role", "")))
        if allowed_roles and role not in {_normalize_role(r) for r in allowed_roles}:
            raise PermissionError("Officer role is not permitted for this action")

        return row

    def get_tenant_policy(self, tenant_id: str, create_if_missing: bool = True) -> dict[str, Any]:
        if self.client:
            result = exec_query(self.client.table("tenant_policies").select("*").eq("tenant_id", tenant_id).limit(1))
            if result and result.get("data"):
                rows = result["data"]
                if rows:
                    return rows[0]

            if create_if_missing:
                default = self._default_policy(tenant_id)
                exec_query(self.client.table("tenant_policies").insert(default))
                result2 = exec_query(self.client.table("tenant_policies").select("*").eq("tenant_id", tenant_id).limit(1))
                if result2 and result2.get("data"):
                    rows = result2["data"]
                    if rows:
                        return rows[0]

        if tenant_id not in MemoryStore.tenant_policies and create_if_missing:
            MemoryStore.tenant_policies[tenant_id] = self._default_policy(tenant_id)
        return MemoryStore.tenant_policies.get(tenant_id, self._default_policy(tenant_id))

    def get_tenant_data_policy(self, tenant_id: str, create_if_missing: bool = True) -> dict[str, Any]:
        if self.client:
            result = exec_query(self.client.table("tenant_data_policies").select("*").eq("tenant_id", tenant_id).limit(1))
            if result and result.get("data"):
                rows = result["data"]
                if rows:
                    return rows[0]

            if create_if_missing:
                default = self._default_data_policy(tenant_id)
                exec_query(self.client.table("tenant_data_policies").upsert(default, on_conflict="tenant_id"))
                result2 = exec_query(self.client.table("tenant_data_policies").select("*").eq("tenant_id", tenant_id).limit(1))
                if result2 and result2.get("data"):
                    rows = result2["data"]
                    if rows:
                        return rows[0]

        if tenant_id not in MemoryStore.tenant_data_policies and create_if_missing:
            MemoryStore.tenant_data_policies[tenant_id] = self._default_data_policy(tenant_id)
        return MemoryStore.tenant_data_policies.get(tenant_id, self._default_data_policy(tenant_id))

    def upsert_tenant_data_policy(self, tenant_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        row = dict(self.get_tenant_data_policy(tenant_id, create_if_missing=True))
        row.update(updates)
        row["tenant_id"] = tenant_id
        row["updated_at"] = _now()

        if self.client:
            exec_query(self.client.table("tenant_data_policies").upsert(row, on_conflict="tenant_id"))
            result = exec_query(self.client.table("tenant_data_policies").select("*").eq("tenant_id", tenant_id).limit(1))
            if result and result.get("data"):
                data = result["data"]
                return data[0] if data else row

        MemoryStore.tenant_data_policies[tenant_id] = row
        return row

    def get_tenant_partition_config(self, tenant_id: str, create_if_missing: bool = True) -> dict[str, Any]:
        if self.client:
            result = exec_query(self.client.table("tenant_partition_configs").select("*").eq("tenant_id", tenant_id).limit(1))
            if result and result.get("data"):
                rows = result["data"]
                if rows:
                    return rows[0]

            if create_if_missing:
                default = self._default_partition_config(tenant_id)
                exec_query(self.client.table("tenant_partition_configs").upsert(default, on_conflict="tenant_id"))
                result2 = exec_query(self.client.table("tenant_partition_configs").select("*").eq("tenant_id", tenant_id).limit(1))
                if result2 and result2.get("data"):
                    rows = result2["data"]
                    if rows:
                        return rows[0]

        if tenant_id not in MemoryStore.tenant_partition_configs and create_if_missing:
            MemoryStore.tenant_partition_configs[tenant_id] = self._default_partition_config(tenant_id)
        return MemoryStore.tenant_partition_configs.get(tenant_id, self._default_partition_config(tenant_id))

    def upsert_tenant_partition_config(self, tenant_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        row = dict(self.get_tenant_partition_config(tenant_id, create_if_missing=True))
        row.update(updates)
        row["tenant_id"] = tenant_id
        row["updated_at"] = _now()

        if self.client:
            exec_query(self.client.table("tenant_partition_configs").upsert(row, on_conflict="tenant_id"))
            result = exec_query(self.client.table("tenant_partition_configs").select("*").eq("tenant_id", tenant_id).limit(1))
            if result and result.get("data"):
                data = result["data"]
                return data[0] if data else row

        MemoryStore.tenant_partition_configs[tenant_id] = row
        return row

    def create_runbook(
        self,
        *,
        tenant_id: str,
        event_type: str,
        severity: str,
        title: str,
        steps: list[str],
        owner_role: str,
    ) -> dict[str, Any]:
        row = {
            "id": str(uuid4()),
            "tenant_id": tenant_id,
            "event_type": event_type,
            "severity": severity,
            "title": title,
            "steps": steps,
            "owner_role": owner_role,
            "is_active": True,
            "updated_at": _now(),
            "created_at": _now(),
        }
        if self.client:
            result = exec_query(self.client.table("operational_runbooks").insert(row))
            if result and result.get("data"):
                data = result["data"]
                return data[0] if data else row

        MemoryStore.operational_runbooks.append(row)
        return row

    def list_runbooks(self, tenant_id: str, only_active: bool = True) -> list[dict[str, Any]]:
        if self.client:
            query = self.client.table("operational_runbooks").select("*").eq("tenant_id", tenant_id).order("created_at", desc=False)
            if only_active:
                query = query.eq("is_active", True)
            result = exec_query(query)
            if result and result.get("data") is not None:
                return result["data"]

        rows = [r for r in MemoryStore.operational_runbooks if r.get("tenant_id") == tenant_id]
        if only_active:
            rows = [r for r in rows if bool(r.get("is_active", True))]
        return rows

    def create_governance_audit_review(
        self,
        *,
        tenant_id: str,
        review_type: str,
        status: str,
        findings: list[str],
        reviewed_by: str,
    ) -> dict[str, Any]:
        row = {
            "id": str(uuid4()),
            "tenant_id": tenant_id,
            "review_type": review_type,
            "status": status,
            "findings": findings,
            "reviewed_by": reviewed_by,
            "created_at": _now(),
        }
        if self.client:
            result = exec_query(self.client.table("governance_audit_reviews").insert(row))
            if result and result.get("data"):
                data = result["data"]
                return data[0] if data else row

        MemoryStore.governance_audit_reviews.append(row)
        return row

    def list_governance_audit_reviews(self, tenant_id: str) -> list[dict[str, Any]]:
        if self.client:
            result = exec_query(
                self.client.table("governance_audit_reviews")
                .select("*")
                .eq("tenant_id", tenant_id)
                .order("created_at", desc=True)
            )
            if result and result.get("data") is not None:
                return result["data"]

        rows = [r for r in MemoryStore.governance_audit_reviews if r.get("tenant_id") == tenant_id]
        return sorted(rows, key=lambda x: x.get("created_at", ""), reverse=True)

    def upsert_platform_access_grant(
        self,
        *,
        actor_id: str,
        platform_role: str,
        justification: str,
        approved_by: str,
        status: str = "ACTIVE",
        expires_at: str | None = None,
    ) -> dict[str, Any]:
        row = {
            "actor_id": actor_id,
            "platform_role": platform_role,
            "justification": justification,
            "approved_by": approved_by,
            "status": status,
            "granted_at": _now(),
            "expires_at": expires_at,
        }
        if self.client:
            exec_query(self.client.table("platform_access_grants").upsert(row, on_conflict="actor_id,platform_role"))
            result = exec_query(
                self.client.table("platform_access_grants")
                .select("*")
                .eq("actor_id", actor_id)
                .eq("platform_role", platform_role)
                .limit(1)
            )
            if result and result.get("data"):
                data = result["data"]
                return data[0] if data else row

        MemoryStore.platform_access_grants[f"{actor_id}:{platform_role}"] = row
        return row

    def list_platform_access_grants(self, actor_id: str | None = None) -> list[dict[str, Any]]:
        if self.client:
            query = self.client.table("platform_access_grants").select("*").order("granted_at", desc=True)
            if actor_id:
                query = query.eq("actor_id", actor_id)
            result = exec_query(query)
            if result and result.get("data") is not None:
                return result["data"]

        rows = list(MemoryStore.platform_access_grants.values())
        if actor_id:
            rows = [r for r in rows if r.get("actor_id") == actor_id]
        return rows

    def has_platform_access(self, actor_id: str, allowed_roles: set[str]) -> bool:
        grants = self.list_platform_access_grants(actor_id=actor_id)
        allowed = {r.lower() for r in allowed_roles}
        for row in grants:
            if str(row.get("status", "ACTIVE")).upper() != "ACTIVE":
                continue
            if str(row.get("platform_role", "")).lower() in allowed:
                return True
        return False

    def seed_part5_baseline(self, tenant_id: str) -> dict[str, int]:
        inserted = {"kpi_targets": 0, "rollout_phases": 0, "risks": 0}
        existing_targets = self.list_kpi_targets(tenant_id)
        if not existing_targets:
            for row in self._default_kpi_targets(tenant_id):
                self.upsert_kpi_target(
                    tenant_id=tenant_id,
                    kpi_key=row["kpi_key"],
                    target_value=float(row["target_value"]),
                    unit=str(row["unit"]),
                    direction=str(row["direction"]),
                    description=str(row["description"]),
                )
                inserted["kpi_targets"] += 1

        existing_phases = self.list_rollout_phases(tenant_id)
        if not existing_phases:
            for row in self._default_rollout_phases(tenant_id):
                self.upsert_rollout_phase(
                    tenant_id=tenant_id,
                    phase_key=row["phase_key"],
                    title=row["title"],
                    duration_months_min=int(row["duration_months_min"]),
                    duration_months_max=int(row["duration_months_max"]),
                    status=row["status"],
                    description=row["description"],
                )
                inserted["rollout_phases"] += 1

        existing_risks = self.list_risks(tenant_id)
        if not existing_risks:
            for row in self._default_risk_register(tenant_id):
                self.upsert_risk(
                    tenant_id=tenant_id,
                    risk_code=row["risk_code"],
                    title=row["title"],
                    mitigation=row["mitigation"],
                    owner_team=row["owner_team"],
                    impact=row["impact"],
                    likelihood=row["likelihood"],
                    status=row["status"],
                )
                inserted["risks"] += 1
        return inserted

    def upsert_kpi_target(
        self,
        *,
        tenant_id: str,
        kpi_key: str,
        target_value: float,
        unit: str,
        direction: str,
        description: str,
    ) -> dict[str, Any]:
        row = {
            "tenant_id": tenant_id,
            "kpi_key": kpi_key,
            "target_value": float(target_value),
            "unit": unit,
            "direction": direction,
            "description": description,
            "updated_at": _now(),
        }
        if self.client:
            exec_query(self.client.table("tenant_kpi_targets").upsert(row, on_conflict="tenant_id,kpi_key"))
            result = exec_query(
                self.client.table("tenant_kpi_targets")
                .select("*")
                .eq("tenant_id", tenant_id)
                .eq("kpi_key", kpi_key)
                .limit(1)
            )
            if result and result.get("data"):
                data = result["data"]
                return data[0] if data else row

        replaced = False
        for idx, existing in enumerate(MemoryStore.tenant_kpi_targets):
            if existing.get("tenant_id") == tenant_id and existing.get("kpi_key") == kpi_key:
                MemoryStore.tenant_kpi_targets[idx] = row
                replaced = True
                break
        if not replaced:
            MemoryStore.tenant_kpi_targets.append(row)
        return row

    def list_kpi_targets(self, tenant_id: str) -> list[dict[str, Any]]:
        if self.client:
            result = exec_query(
                self.client.table("tenant_kpi_targets")
                .select("*")
                .eq("tenant_id", tenant_id)
                .order("kpi_key", desc=False)
            )
            if result and result.get("data") is not None:
                return result["data"]

        rows = [r for r in MemoryStore.tenant_kpi_targets if r.get("tenant_id") == tenant_id]
        return sorted(rows, key=lambda x: str(x.get("kpi_key", "")))

    def create_kpi_snapshot(
        self,
        *,
        tenant_id: str,
        kpi_key: str,
        measured_value: float,
        source: str,
        notes: str | None = None,
        measured_at: str | None = None,
    ) -> dict[str, Any]:
        row = {
            "id": str(uuid4()),
            "tenant_id": tenant_id,
            "kpi_key": kpi_key,
            "measured_value": float(measured_value),
            "source": source,
            "notes": notes,
            "measured_at": measured_at or _now(),
            "created_at": _now(),
        }
        if self.client:
            result = exec_query(self.client.table("tenant_kpi_snapshots").insert(row))
            if result and result.get("data"):
                data = result["data"]
                return data[0] if data else row

        MemoryStore.tenant_kpi_snapshots.append(row)
        return row

    def list_kpi_snapshots(self, tenant_id: str, kpi_key: str | None = None) -> list[dict[str, Any]]:
        if self.client:
            query = self.client.table("tenant_kpi_snapshots").select("*").eq("tenant_id", tenant_id).order("measured_at", desc=True)
            if kpi_key:
                query = query.eq("kpi_key", kpi_key)
            result = exec_query(query)
            if result and result.get("data") is not None:
                return result["data"]

        rows = [r for r in MemoryStore.tenant_kpi_snapshots if r.get("tenant_id") == tenant_id]
        if kpi_key:
            rows = [r for r in rows if r.get("kpi_key") == kpi_key]
        return sorted(rows, key=lambda x: str(x.get("measured_at", "")), reverse=True)

    def upsert_rollout_phase(
        self,
        *,
        tenant_id: str,
        phase_key: str,
        title: str,
        duration_months_min: int,
        duration_months_max: int,
        status: str,
        description: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, Any]:
        row = {
            "tenant_id": tenant_id,
            "phase_key": phase_key,
            "title": title,
            "duration_months_min": int(duration_months_min),
            "duration_months_max": int(duration_months_max),
            "status": status,
            "description": description,
            "start_date": start_date,
            "end_date": end_date,
            "updated_at": _now(),
        }
        if self.client:
            exec_query(self.client.table("tenant_rollout_phases").upsert(row, on_conflict="tenant_id,phase_key"))
            result = exec_query(
                self.client.table("tenant_rollout_phases")
                .select("*")
                .eq("tenant_id", tenant_id)
                .eq("phase_key", phase_key)
                .limit(1)
            )
            if result and result.get("data"):
                data = result["data"]
                return data[0] if data else row

        replaced = False
        for idx, existing in enumerate(MemoryStore.tenant_rollout_phases):
            if existing.get("tenant_id") == tenant_id and existing.get("phase_key") == phase_key:
                MemoryStore.tenant_rollout_phases[idx] = row
                replaced = True
                break
        if not replaced:
            MemoryStore.tenant_rollout_phases.append(row)
        return row

    def list_rollout_phases(self, tenant_id: str) -> list[dict[str, Any]]:
        if self.client:
            result = exec_query(
                self.client.table("tenant_rollout_phases")
                .select("*")
                .eq("tenant_id", tenant_id)
                .order("phase_key", desc=False)
            )
            if result and result.get("data") is not None:
                return result["data"]

        rows = [r for r in MemoryStore.tenant_rollout_phases if r.get("tenant_id") == tenant_id]
        return sorted(rows, key=lambda x: str(x.get("phase_key", "")))

    def upsert_risk(
        self,
        *,
        tenant_id: str,
        risk_code: str,
        title: str,
        mitigation: str,
        owner_team: str,
        impact: str,
        likelihood: str,
        status: str,
    ) -> dict[str, Any]:
        row = {
            "tenant_id": tenant_id,
            "risk_code": risk_code,
            "title": title,
            "mitigation": mitigation,
            "owner_team": owner_team,
            "impact": impact,
            "likelihood": likelihood,
            "status": status,
            "updated_at": _now(),
        }
        if self.client:
            exec_query(self.client.table("tenant_risk_register").upsert(row, on_conflict="tenant_id,risk_code"))
            result = exec_query(
                self.client.table("tenant_risk_register")
                .select("*")
                .eq("tenant_id", tenant_id)
                .eq("risk_code", risk_code)
                .limit(1)
            )
            if result and result.get("data"):
                data = result["data"]
                return data[0] if data else row

        replaced = False
        for idx, existing in enumerate(MemoryStore.tenant_risk_register):
            if existing.get("tenant_id") == tenant_id and existing.get("risk_code") == risk_code:
                MemoryStore.tenant_risk_register[idx] = row
                replaced = True
                break
        if not replaced:
            MemoryStore.tenant_risk_register.append(row)
        return row

    def list_risks(self, tenant_id: str) -> list[dict[str, Any]]:
        if self.client:
            result = exec_query(
                self.client.table("tenant_risk_register")
                .select("*")
                .eq("tenant_id", tenant_id)
                .order("risk_code", desc=False)
            )
            if result and result.get("data") is not None:
                return result["data"]

        rows = [r for r in MemoryStore.tenant_risk_register if r.get("tenant_id") == tenant_id]
        return sorted(rows, key=lambda x: str(x.get("risk_code", "")))

    def get_active_template(self, tenant_id: str, document_type: str) -> dict[str, Any]:
        dtype = (document_type or "UNKNOWN").upper()
        if self.client:
            result = exec_query(
                self.client.table("tenant_templates")
                .select("*")
                .eq("tenant_id", tenant_id)
                .eq("document_type", dtype)
                .eq("is_active", True)
                .order("version", desc=True)
                .limit(1)
            )
            if result and result.get("data"):
                rows = result["data"]
                if rows:
                    if "template_version" not in rows[0]:
                        rows[0]["template_version"] = "2025.1.0"
                    return rows[0]

        key = (tenant_id, dtype)
        rows = MemoryStore.tenant_templates.get(key, [])
        active = [r for r in rows if r.get("is_active")]
        if active:
            row = sorted(active, key=lambda x: x.get("version", 1), reverse=True)[0]
            row.setdefault("template_version", "2025.1.0")
            return row

        return self._default_template(tenant_id, dtype)

    def list_tenant_templates(self, tenant_id: str, document_type: str | None = None) -> list[dict[str, Any]]:
        if self.client:
            query = (
                self.client.table("tenant_templates")
                .select("*")
                .eq("tenant_id", tenant_id)
                .order("document_type", desc=False)
                .order("version", desc=True)
            )
            if document_type:
                query = query.eq("document_type", document_type.upper())
            result = exec_query(query)
            if result and result.get("data") is not None:
                return result["data"]

        rows: list[dict[str, Any]] = []
        for (tid, dtype), items in MemoryStore.tenant_templates.items():
            if tid != tenant_id:
                continue
            if document_type and dtype != document_type.upper():
                continue
            rows.extend(items)
        rows.sort(key=lambda x: (str(x.get("document_type", "")), int(x.get("version", 1))), reverse=False)
        return rows

    def upsert_tenant_template(
        self,
        *,
        tenant_id: str,
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
        dtype = document_type.upper()
        row = {
            "tenant_id": tenant_id,
            "document_type": dtype,
            "doc_subtype": doc_subtype,
            "region_code": region_code,
            "description": description,
            "template_id": template_id,
            "template_version": template_version,
            "policy_rule_set_id": policy_rule_set_id,
            "version": int(version),
            "is_active": bool(is_active),
            "config": config or {},
            "lifecycle_status": lifecycle_status,
            "effective_from": _now(),
            "effective_to": None,
            "created_at": _now(),
        }

        if self.client:
            exec_query(
                self.client.table("tenant_templates").upsert(
                    row,
                    on_conflict="tenant_id,document_type,version",
                )
            )
            if is_active:
                exec_query(
                    self.client.table("tenant_templates")
                    .update({"is_active": False})
                    .eq("tenant_id", tenant_id)
                    .eq("document_type", dtype)
                    .neq("version", int(version))
                )
            result = exec_query(
                self.client.table("tenant_templates")
                .select("*")
                .eq("tenant_id", tenant_id)
                .eq("document_type", dtype)
                .eq("version", int(version))
                .limit(1)
            )
            if result and result.get("data"):
                data = result["data"]
                return data[0] if data else row

        key = (tenant_id, dtype)
        current = MemoryStore.tenant_templates.get(key, [])
        replaced = False
        for idx, existing in enumerate(current):
            if int(existing.get("version", 1)) == int(version):
                current[idx] = row
                replaced = True
                break
        if not replaced:
            current.append(row)
        if is_active:
            for item in current:
                if int(item.get("version", 1)) != int(version):
                    item["is_active"] = False
        MemoryStore.tenant_templates[key] = current
        return row

    def get_active_rule(self, tenant_id: str, document_type: str) -> dict[str, Any]:
        dtype = (document_type or "UNKNOWN").upper()
        if self.client:
            result = exec_query(
                self.client.table("tenant_rules")
                .select("*")
                .eq("tenant_id", tenant_id)
                .eq("document_type", dtype)
                .eq("is_active", True)
                .order("version", desc=True)
                .limit(1)
            )
            if result and result.get("data"):
                rows = result["data"]
                if rows:
                    rows[0].setdefault("rule_set_id", f"RULESET_{dtype}_DEFAULT")
                    return rows[0]

        key = (tenant_id, dtype)
        rows = MemoryStore.tenant_rules.get(key, [])
        active = [r for r in rows if r.get("is_active")]
        if active:
            row = sorted(active, key=lambda x: x.get("version", 1), reverse=True)[0]
            row.setdefault("rule_set_id", f"RULESET_{dtype}_DEFAULT")
            return row

        return self._default_rule(tenant_id, dtype)

    def list_tenant_rules(self, tenant_id: str, document_type: str | None = None) -> list[dict[str, Any]]:
        if self.client:
            query = (
                self.client.table("tenant_rules")
                .select("*")
                .eq("tenant_id", tenant_id)
                .order("document_type", desc=False)
                .order("version", desc=True)
            )
            if document_type:
                query = query.eq("document_type", document_type.upper())
            result = exec_query(query)
            if result and result.get("data") is not None:
                return result["data"]

        rows: list[dict[str, Any]] = []
        for (tid, dtype), items in MemoryStore.tenant_rules.items():
            if tid != tenant_id:
                continue
            if document_type and dtype != document_type.upper():
                continue
            rows.extend(items)
        rows.sort(key=lambda x: (str(x.get("document_type", "")), int(x.get("version", 1))), reverse=False)
        return rows

    def upsert_tenant_rule(
        self,
        *,
        tenant_id: str,
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
        dtype = document_type.upper()
        row = {
            "tenant_id": tenant_id,
            "document_type": dtype,
            "rule_set_id": rule_set_id,
            "rule_name": rule_name,
            "version": int(version),
            "is_active": bool(is_active),
            "min_extract_confidence": float(min_extract_confidence),
            "min_approval_confidence": float(min_approval_confidence),
            "max_approval_risk": float(max_approval_risk),
            "registry_required": bool(registry_required),
            "config": config or {},
            "created_at": _now(),
        }

        if self.client:
            exec_query(
                self.client.table("tenant_rules").upsert(
                    row,
                    on_conflict="tenant_id,document_type,version",
                )
            )
            if is_active:
                exec_query(
                    self.client.table("tenant_rules")
                    .update({"is_active": False})
                    .eq("tenant_id", tenant_id)
                    .eq("document_type", dtype)
                    .neq("version", int(version))
                )
            result = exec_query(
                self.client.table("tenant_rules")
                .select("*")
                .eq("tenant_id", tenant_id)
                .eq("document_type", dtype)
                .eq("version", int(version))
                .limit(1)
            )
            if result and result.get("data"):
                data = result["data"]
                return data[0] if data else row

        key = (tenant_id, dtype)
        current = MemoryStore.tenant_rules.get(key, [])
        replaced = False
        for idx, existing in enumerate(current):
            if int(existing.get("version", 1)) == int(version):
                current[idx] = row
                replaced = True
                break
        if not replaced:
            current.append(row)
        if is_active:
            for item in current:
                if int(item.get("version", 1)) != int(version):
                    item["is_active"] = False
        MemoryStore.tenant_rules[key] = current
        return row

    def get_tenant_bucket(self, tenant_id: str) -> str:
        if self.client:
            result = exec_query(
                self.client.table("tenant_storage_buckets").select("bucket_name").eq("tenant_id", tenant_id).eq("is_active", True).limit(1)
            )
            if result and result.get("data"):
                rows = result["data"]
                if rows and rows[0].get("bucket_name"):
                    return str(rows[0]["bucket_name"])

        return f"tenant-{_sanitize_tenant_for_bucket(tenant_id)}"

    def create_tenant_api_key(self, tenant_id: str, key_label: str, raw_key: str) -> dict[str, Any]:
        row = {
            "tenant_id": tenant_id,
            "key_label": key_label,
            "key_hash": _hash_key(raw_key),
            "status": "ACTIVE",
            "created_at": _now(),
            "last_used_at": None,
        }

        if self.client:
            result = exec_query(self.client.table("tenant_api_keys").insert(row))
            if result and result.get("data"):
                data = result["data"]
                return data[0] if data else row

        MemoryStore.tenant_api_keys.append(row)
        return row

    def validate_tenant_api_key(self, tenant_id: str, raw_key: str) -> bool:
        key_hash = _hash_key(raw_key)

        if self.client:
            result = exec_query(
                self.client.table("tenant_api_keys")
                .select("*")
                .eq("tenant_id", tenant_id)
                .eq("key_hash", key_hash)
                .eq("status", "ACTIVE")
                .limit(1)
            )
            if result and result.get("data"):
                rows = result["data"]
                if rows:
                    exec_query(
                        self.client.table("tenant_api_keys")
                        .update({"last_used_at": _now()})
                        .eq("tenant_id", tenant_id)
                        .eq("key_hash", key_hash)
                    )
                    return True

        for row in MemoryStore.tenant_api_keys:
            if row.get("tenant_id") == tenant_id and row.get("key_hash") == key_hash and row.get("status") == "ACTIVE":
                row["last_used_at"] = _now()
                return True
        return False
