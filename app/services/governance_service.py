from __future__ import annotations

from typing import Any

from app.infra.repositories import (
    PLATFORM_ROLES,
    ROLE_PLATFORM_AUDITOR,
    ROLE_PLATFORM_SUPER_ADMIN,
    ADMIN_ROLES,
    Repository,
)


AI_POLICY_STATEMENT = (
    "The platform assists officers by automating document analysis and risk flagging. "
    "Final legal decisions remain with human officers acting under applicable laws and policies."
)


class GovernanceService:
    def __init__(self, repo: Repository) -> None:
        self.repo = repo

    def get_tenant_governance_snapshot(self, tenant_id: str, officer_id: str) -> dict[str, Any]:
        self.repo.assert_officer_access(officer_id, tenant_id, None)
        return {
            "tenant_id": tenant_id,
            "tenant_policy": self.repo.get_tenant_policy(tenant_id),
            "data_policy": self.repo.get_tenant_data_policy(tenant_id),
            "partition_config": self.repo.get_tenant_partition_config(tenant_id),
            "runbooks": self.repo.list_runbooks(tenant_id),
            "audit_reviews": self.repo.list_governance_audit_reviews(tenant_id),
            "ai_policy_statement": AI_POLICY_STATEMENT,
        }

    def update_tenant_data_policy(self, tenant_id: str, officer_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        self.repo.assert_officer_access(officer_id, tenant_id, ADMIN_ROLES)
        return self.repo.upsert_tenant_data_policy(tenant_id, updates)

    def update_tenant_partition_config(self, tenant_id: str, officer_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        self.repo.assert_officer_access(officer_id, tenant_id, ADMIN_ROLES)
        return self.repo.upsert_tenant_partition_config(tenant_id, updates)

    def create_runbook(
        self,
        *,
        tenant_id: str,
        officer_id: str,
        event_type: str,
        severity: str,
        title: str,
        steps: list[str],
        owner_role: str,
    ) -> dict[str, Any]:
        self.repo.assert_officer_access(officer_id, tenant_id, ADMIN_ROLES)
        return self.repo.create_runbook(
            tenant_id=tenant_id,
            event_type=event_type,
            severity=severity,
            title=title,
            steps=steps,
            owner_role=owner_role,
        )

    def log_audit_review(
        self,
        *,
        tenant_id: str,
        officer_id: str,
        review_type: str,
        status: str,
        findings: list[str],
    ) -> dict[str, Any]:
        self.repo.assert_officer_access(officer_id, tenant_id, ADMIN_ROLES)
        return self.repo.create_governance_audit_review(
            tenant_id=tenant_id,
            review_type=review_type,
            status=status,
            findings=findings,
            reviewed_by=officer_id,
        )

    def grant_platform_access(
        self,
        *,
        actor_id: str,
        platform_role: str,
        justification: str,
        approved_by_actor_id: str,
    ) -> dict[str, Any]:
        # Platform grants are administrative and not scoped to tenant.
        if platform_role.lower() not in {r.lower() for r in PLATFORM_ROLES}:
            raise ValueError("Invalid platform role")
        return self.repo.upsert_platform_access_grant(
            actor_id=actor_id,
            platform_role=platform_role,
            justification=justification,
            approved_by=approved_by_actor_id,
            status="ACTIVE",
        )

    def list_platform_access(self, actor_id: str | None = None) -> list[dict[str, Any]]:
        return self.repo.list_platform_access_grants(actor_id=actor_id)

    def cross_tenant_audit_overview(self, actor_id: str) -> dict[str, Any]:
        if not self.repo.has_platform_access(actor_id, {ROLE_PLATFORM_AUDITOR, ROLE_PLATFORM_SUPER_ADMIN}):
            raise PermissionError("Platform-level cross-tenant access not granted")

        tenants = self.repo.list_platform_tenants()
        coverage: list[dict[str, Any]] = []
        for tenant in tenants:
            tenant_id = str(tenant.get("tenant_id"))
            docs = self.repo.list_documents(tenant_id)
            coverage.append(
                {
                    "tenant_id": tenant_id,
                    "documents": len(docs),
                    "open_escalations": len(self.repo.list_review_escalations(tenant_id, only_open=True)),
                    "pending_webhooks": len(self.repo.list_webhook_outbox(tenant_id, status="PENDING")),
                    "pending_correction_qa": len(self.repo.list_correction_gate_records(tenant_id, status="PENDING_QA")),
                }
            )

        return {
            "actor_id": actor_id,
            "scope": "PLATFORM_AUDIT",
            "tenants": coverage,
        }
