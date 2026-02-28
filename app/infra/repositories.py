from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from app.domain.models import Dispute, Document, DocumentEvent, Officer, TenantPolicy
from app.domain.states import DocumentState
from app.infra.supabase_client import exec_query, get_supabase_client


ROLE_CASE_WORKER = "case_worker"
ROLE_REVIEWER = "reviewer"
ROLE_ADMIN = "admin"
ROLE_AUDITOR = "auditor"

WRITER_ROLES = {ROLE_CASE_WORKER, ROLE_REVIEWER, ROLE_ADMIN}
REVIEW_ROLES = {ROLE_REVIEWER, ROLE_ADMIN}
ADMIN_ROLES = {ROLE_ADMIN}


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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today_start_iso() -> str:
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()


def _hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _normalize_role(role: str) -> str:
    return role.strip().lower()


def _sanitize_tenant_for_bucket(tenant_id: str) -> str:
    return "".join(ch if ch.isalnum() or ch == "-" else "-" for ch in tenant_id.lower())


class Repository:
    def __init__(self) -> None:
        self.client, self.error = get_supabase_client()
        self.schema_gaps: list[str] = []
        self.part2_schema_ready: bool = False
        if self.client and not self._probe_documents_access():
            self.client = None
            if not self.error:
                self.error = "Supabase connected but documents table access failed (check schema/RLS/key)"
        if self.client:
            self.schema_gaps = self._detect_part2_schema_gaps()
            self.part2_schema_ready = len(self.schema_gaps) == 0
            if self.schema_gaps:
                gap_preview = ", ".join(self.schema_gaps[:4])
                self.error = (
                    "Supabase schema is outdated for Part-2 contracts. "
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
