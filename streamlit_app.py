from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from app.config import settings
from app.infra.repositories import (
    ROLE_CASE_WORKER,
    ROLE_REVIEWER,
    ROLE_ADMIN,
    ROLE_AUDITOR,
    ROLE_TENANT_OPERATOR,
    ROLE_TENANT_OFFICER,
    ROLE_TENANT_SENIOR_OFFICER,
    ROLE_TENANT_ADMIN,
    ROLE_TENANT_AUDITOR,
    ROLE_PLATFORM_AUDITOR,
    ROLE_PLATFORM_SUPER_ADMIN,
)
from app.services.document_service import DocumentService
from app.services.governance_service import GovernanceService


st.set_page_config(page_title="Gov Document Intelligence", page_icon="📄", layout="wide")

service = DocumentService()
governance = GovernanceService(service.repo)

st.title("Government Document Intelligence")
st.caption("Formal state machine + event-driven pipeline + tenant-scoped governance")

with st.expander("Environment status", expanded=False):
    st.write(
        {
            "APP_ENV": settings.app_env,
            "SUPABASE_URL_VALID": settings.supabase_url_valid(),
            "SUPABASE_KEY_PRESENT": settings.supabase_key_present(),
            "GROQ_CONFIGURED": bool(settings.groq_api_key),
            "PERSISTENCE": "Supabase" if service.repo.using_supabase else f"In-memory fallback ({service.repo.error})",
            "PART2_SCHEMA_READY": service.repo.part2_schema_ready,
            "PART2_SCHEMA_GAPS": service.repo.schema_gaps[:8],
            "PART3_SCHEMA_READY": service.repo.part3_schema_ready,
            "PART3_SCHEMA_GAPS": service.repo.part3_schema_gaps[:8],
            "PART4_SCHEMA_READY": service.repo.part4_schema_ready,
            "PART4_SCHEMA_GAPS": service.repo.part4_schema_gaps[:8],
            "PART5_SCHEMA_READY": service.repo.part5_schema_ready,
            "PART5_SCHEMA_GAPS": service.repo.part5_schema_gaps[:8],
            "DR_PLAN": service.dr_service.describe(),
        }
    )

with st.sidebar:
    st.header("Access Context")
    tenant_id = st.text_input("Tenant ID", value="dept-education")
    officer_id = st.text_input("Officer ID", value="officer-001")
    role = st.selectbox(
        "Officer Role",
        [
            ROLE_TENANT_OPERATOR,
            ROLE_TENANT_OFFICER,
            ROLE_TENANT_SENIOR_OFFICER,
            ROLE_TENANT_ADMIN,
            ROLE_TENANT_AUDITOR,
            ROLE_CASE_WORKER,
            ROLE_REVIEWER,
            ROLE_ADMIN,
            ROLE_AUDITOR,
        ],
        index=0,
    )

    if st.button("Register / Bind Officer", use_container_width=True):
        try:
            row = service.register_officer(officer_id.strip(), tenant_id.strip(), role)
            st.success(f"Bound {row['officer_id']} -> {row['tenant_id']} ({row['role']})")
        except Exception as exc:
            st.error(str(exc))

    try:
        policy = service.repo.get_tenant_policy(tenant_id.strip())
        st.markdown("### Tenant Policy")
        st.write(
            {
                "retention_days": policy.get("data_retention_days"),
                "review_sla_days": policy.get("review_sla_days"),
                "escalation_step_days": policy.get("escalation_step_days"),
                "rate_limit_per_min": policy.get("api_rate_limit_per_minute"),
                "max_docs_per_day": policy.get("max_documents_per_day"),
                "cross_tenant_fraud": policy.get("cross_tenant_fraud_enabled"),
                "export_enabled": policy.get("export_enabled"),
                "channels": {
                    "sms": policy.get("sms_enabled"),
                    "email": policy.get("email_enabled"),
                    "portal": policy.get("portal_enabled"),
                    "whatsapp": policy.get("whatsapp_enabled"),
                },
                "residency_region": policy.get("residency_region"),
            }
        )
    except Exception as exc:
        st.error(str(exc))

st.divider()

left, right = st.columns([1, 1])

with left:
    st.subheader("Ingest + Run Pipeline")
    citizen_id = st.text_input("Citizen ID", value="citizen-001")
    file_name = st.text_input("File name", value="sample_document.txt")
    metadata_raw = st.text_area("Metadata JSON", value='{"source":"ONLINE_PORTAL"}', height=90)
    raw_text = st.text_area(
        "Document text / OCR input",
        value="""Name: John Doe
ID: AB12345
Issuer: State Board
This certificate includes official seal and signature.
""",
        height=170,
    )

    col_ing_1, col_ing_2 = st.columns(2)
    with col_ing_1:
        if st.button("Create", type="primary", use_container_width=True):
            try:
                metadata = json.loads(metadata_raw) if metadata_raw.strip() else {}
                created = service.create_document(
                    tenant_id=tenant_id.strip(),
                    citizen_id=citizen_id.strip(),
                    file_name=file_name.strip(),
                    raw_text=raw_text,
                    officer_id=officer_id.strip(),
                    metadata=metadata,
                )
                st.success(f"Created {created['id']}")
            except Exception as exc:
                st.error(str(exc))
    with col_ing_2:
        if st.button("Create + Process", use_container_width=True):
            try:
                metadata = json.loads(metadata_raw) if metadata_raw.strip() else {}
                created = service.create_document(
                    tenant_id=tenant_id.strip(),
                    citizen_id=citizen_id.strip(),
                    file_name=file_name.strip(),
                    raw_text=raw_text,
                    officer_id=officer_id.strip(),
                    metadata=metadata,
                )
                processed = service.process_document(created["id"], tenant_id.strip(), officer_id.strip())
                st.success(f"Processed {processed['id']} -> {processed.get('state')} ({processed.get('decision')})")
            except Exception as exc:
                st.error(str(exc))

with right:
    st.subheader("Documents (Tenant Scoped)")
    try:
        docs = service.list_documents(tenant_id.strip(), officer_id.strip())
    except Exception as exc:
        docs = []
        st.error(str(exc))

    if not docs:
        st.info("No documents yet for this tenant.")
    else:
        df = pd.DataFrame(docs)
        cols = [
            c
            for c in [
                "id",
                "state",
                "decision",
                "confidence",
                "risk_score",
                "template_id",
                "last_job_id",
                "queue_overflow",
                "created_at",
            ]
            if c in df.columns
        ]
        st.dataframe(df[cols], use_container_width=True, hide_index=True)

        selected_id = st.selectbox("Select document", [d["id"] for d in docs])
        selected = next((d for d in docs if d["id"] == selected_id), None)

        if selected:
            st.write("**Selected state:**", selected["state"])
            a1, a2, a3 = st.columns(3)
            with a1:
                if st.button("Process Selected", use_container_width=True):
                    try:
                        out = service.process_document(selected_id, tenant_id.strip(), officer_id.strip())
                        st.success(f"Processed -> {out.get('state')} ({out.get('decision')})")
                    except Exception as exc:
                        st.error(str(exc))
            with a2:
                if st.button("Start Review", use_container_width=True):
                    try:
                        out = service.start_review(selected_id, tenant_id.strip(), officer_id.strip(), review_level="L1")
                        st.success(f"Review started. State={out.get('state')}")
                    except Exception as exc:
                        st.error(str(exc))
            with a3:
                if st.button("Notify", use_container_width=True):
                    try:
                        service.notify(selected_id, tenant_id.strip(), officer_id.strip())
                        st.success("Notification event emitted")
                    except Exception as exc:
                        st.error(str(exc))

            b1, b2, b3 = st.columns(3)
            with b1:
                if st.button("Manual Approve", use_container_width=True):
                    try:
                        out = service.manual_decision(selected_id, "APPROVE", tenant_id.strip(), officer_id.strip())
                        st.success(f"Decision -> {out.get('state')} / {out.get('decision')}")
                    except Exception as exc:
                        st.error(str(exc))
            with b2:
                if st.button("Manual Reject", use_container_width=True):
                    try:
                        out = service.manual_decision(selected_id, "REJECT", tenant_id.strip(), officer_id.strip())
                        st.success(f"Decision -> {out.get('state')} / {out.get('decision')}")
                    except Exception as exc:
                        st.error(str(exc))
            with b3:
                if st.button("Archive", use_container_width=True):
                    try:
                        out = service.archive_document(selected_id, tenant_id.strip(), officer_id.strip())
                        st.success(f"Archived. State={out.get('state')}")
                    except Exception as exc:
                        st.error(str(exc))

            conflict_reason = st.text_input("Internal disagreement reason", value="Officer assessments conflict", key="int_conflict_reason")
            if st.button("Flag Internal Disagreement", use_container_width=True):
                try:
                    res = service.flag_internal_disagreement(
                        document_id=selected_id,
                        tenant_id=tenant_id.strip(),
                        officer_id=officer_id.strip(),
                        reason=conflict_reason.strip() or "OFFICER_DECISION_CONFLICT",
                    )
                    st.success(f"Escalated internal disagreement: {res['escalation'].get('id')}")
                except Exception as exc:
                    st.error(str(exc))

            st.markdown("### Dispute")
            disp_reason = st.text_input("Dispute reason", value="Citizen requests re-verification", key="disp_reason")
            disp_note = st.text_input("Evidence note", value="Attached registry screenshot", key="disp_note")
            if st.button("Open Dispute", use_container_width=True):
                try:
                    row = service.open_dispute(selected_id, disp_reason, disp_note, tenant_id.strip(), officer_id.strip())
                    st.success(f"Dispute submitted: {row['id']}")
                except Exception as exc:
                    st.error(str(exc))

            st.markdown("### Field Correction (MLOps Gate)")
            corr_field = st.text_input("Field name", value="TOTAL_MARKS", key="corr_field")
            corr_old = st.text_input("Old value", value="580", key="corr_old")
            corr_new = st.text_input("New value", value="560", key="corr_new")
            corr_reason = st.text_input("Correction reason", value="Corrected as per physical document", key="corr_reason")
            if st.button("Log Field Correction", use_container_width=True):
                try:
                    gate = service.record_field_correction(
                        document_id=selected_id,
                        tenant_id=tenant_id.strip(),
                        officer_id=officer_id.strip(),
                        field_name=corr_field.strip(),
                        old_value=corr_old.strip() or None,
                        new_value=corr_new.strip() or None,
                        reason=corr_reason.strip() or "FIELD_CORRECTION",
                    )
                    st.success(f"Correction logged. Gate status={gate['gate'].get('status')}")
                except Exception as exc:
                    st.error(str(exc))

            st.markdown("### Citizen View")
            citizen_lookup = st.text_input("Citizen ID for case view", value=selected.get("citizen_id", ""), key="cit_lookup")
            if st.button("Load Citizen Case View", use_container_width=True):
                try:
                    view = service.get_citizen_case_view(tenant_id.strip(), selected_id, citizen_lookup.strip())
                    st.json(view)
                except Exception as exc:
                    st.error(str(exc))

            st.markdown("### Events")
            try:
                events = service.list_events(selected_id, tenant_id.strip(), officer_id.strip())
                st.dataframe(pd.DataFrame(events), use_container_width=True, hide_index=True)
            except Exception as exc:
                st.error(str(exc))

            st.markdown("### Model Audit Logs")
            try:
                audit_logs = service.list_model_audit_logs(tenant_id.strip(), officer_id.strip(), document_id=selected_id)
                if audit_logs:
                    st.dataframe(pd.DataFrame(audit_logs), use_container_width=True, hide_index=True)
                else:
                    st.info("No model audit entries yet.")
            except Exception as exc:
                st.error(str(exc))

            st.markdown("### Latest Document Record (Unified Contract)")
            latest_record = service.repo.get_latest_document_record(tenant_id.strip(), selected_id)
            if latest_record:
                st.json(latest_record)
            else:
                st.info("No document_record stored yet for this document.")

st.divider()

c1, c2 = st.columns(2)
with c1:
    st.subheader("SLA + Lifecycle")
    x1, x2 = st.columns(2)
    with x1:
        if st.button("Enforce Review SLA", use_container_width=True):
            try:
                escalations = service.enforce_review_sla(tenant_id.strip(), officer_id.strip())
                st.success(f"Escalations generated: {len(escalations)}")
            except Exception as exc:
                st.error(str(exc))
    with x2:
        if st.button("Apply Retention Lifecycle", use_container_width=True):
            try:
                res = service.apply_retention_lifecycle(tenant_id.strip(), officer_id.strip())
                st.success(f"Lifecycle applied: {res}")
            except Exception as exc:
                st.error(str(exc))

    try:
        escalations = service.list_review_escalations(tenant_id.strip(), officer_id.strip())
        if escalations:
            st.dataframe(pd.DataFrame(escalations), use_container_width=True, hide_index=True)
        else:
            st.info("No open escalations.")
    except Exception as exc:
        st.error(str(exc))

    st.markdown("### Review Assignments")
    try:
        assignments = service.list_review_assignments(tenant_id.strip(), officer_id.strip())
        if assignments:
            st.dataframe(pd.DataFrame(assignments), use_container_width=True, hide_index=True)
        else:
            st.info("No review assignments.")
    except Exception as exc:
        st.error(str(exc))

with c2:
    st.subheader("Notifications + Export")
    include_raw = st.checkbox("Include raw text in export", value=False)
    if st.button("Generate Tenant CSV Export", use_container_width=True):
        try:
            csv_data = service.batch_export_documents(tenant_id.strip(), officer_id.strip(), include_raw_text=include_raw)
            if not csv_data:
                st.info("No rows to export.")
            else:
                st.download_button(
                    label="Download Export",
                    data=csv_data,
                    file_name=f"{tenant_id.strip()}_documents_export.csv",
                    mime="text/csv",
                    use_container_width=True,
                )
        except Exception as exc:
            st.error(str(exc))

    try:
        notifications = service.list_notifications(tenant_id.strip(), officer_id.strip())
        if notifications:
            st.dataframe(pd.DataFrame(notifications), use_container_width=True, hide_index=True)
        else:
            st.info("No notifications yet.")
    except Exception as exc:
        st.error(str(exc))

    st.markdown("### Webhook Outbox")
    try:
        outbox = service.list_webhook_outbox(tenant_id.strip(), officer_id.strip(), status="PENDING")
        if outbox:
            st.dataframe(pd.DataFrame(outbox), use_container_width=True, hide_index=True)
        else:
            st.info("No pending webhooks.")
    except Exception as exc:
        st.error(str(exc))

st.divider()
st.subheader("Monitoring + MLOps")
try:
    dashboard = service.monitoring_dashboard(tenant_id.strip(), officer_id.strip())
    st.json(dashboard)
except Exception as exc:
    st.error(str(exc))

st.divider()
st.subheader("Governance + Tenancy Layer")
g1, g2 = st.columns(2)
with g1:
    try:
        snapshot = governance.get_tenant_governance_snapshot(tenant_id.strip(), officer_id.strip())
        st.json(snapshot)
    except Exception as exc:
        st.error(str(exc))

    st.markdown("### Update Data Governance Policy")
    raw_years = st.number_input("Raw image retention (years)", min_value=1, max_value=30, value=7, step=1)
    structured_years = st.number_input("Structured data retention (years)", min_value=1, max_value=30, value=10, step=1)
    fraud_years = st.number_input("Fraud logs retention (years)", min_value=1, max_value=30, value=10, step=1)
    purge_policy = st.selectbox("Purge policy", ["ANONYMIZE_AFTER_EXPIRY", "HARD_DELETE_AFTER_EXPIRY"], index=0)
    if st.button("Save Governance Policy", use_container_width=True):
        try:
            row = governance.update_tenant_data_policy(
                tenant_id.strip(),
                officer_id.strip(),
                {
                    "raw_image_retention_years": int(raw_years),
                    "structured_data_retention_years": int(structured_years),
                    "fraud_logs_retention_years": int(fraud_years),
                    "purge_policy": purge_policy,
                },
            )
            st.success(f"Saved policy for {row['tenant_id']}")
        except Exception as exc:
            st.error(str(exc))

    st.markdown("### Update Partition Config")
    partition_mode = st.selectbox(
        "Partition mode",
        ["LOGICAL_SHARED", "DEDICATED_SCHEMA", "DEDICATED_CLUSTER", "DEDICATED_DEPLOYMENT"],
        index=0,
    )
    residency_region = st.text_input("Residency region", value="default")
    region_cluster = st.text_input("Region cluster", value="region-a")
    physical_iso = st.checkbox("Physical isolation required", value=False)
    if st.button("Save Partition Config", use_container_width=True):
        try:
            row = governance.update_tenant_partition_config(
                tenant_id.strip(),
                officer_id.strip(),
                {
                    "partition_mode": partition_mode,
                    "residency_region": residency_region.strip() or "default",
                    "region_cluster": region_cluster.strip() or "region-a",
                    "physical_isolation_required": bool(physical_iso),
                },
            )
            st.success(f"Saved partition config: {row['partition_mode']}")
        except Exception as exc:
            st.error(str(exc))

with g2:
    st.markdown("### Runbook Management")
    rb_event = st.text_input("Runbook event type", value="PIPELINE_FAILURE")
    rb_severity = st.selectbox("Runbook severity", ["SEV1", "SEV2", "SEV3"], index=1)
    rb_title = st.text_input("Runbook title", value="OCR cluster degraded")
    rb_steps = st.text_area("Runbook steps (one per line)", value="Check OCR health endpoint\nShift traffic to standby pool\nNotify tenant admins")
    if st.button("Create Runbook", use_container_width=True):
        try:
            row = governance.create_runbook(
                tenant_id=tenant_id.strip(),
                officer_id=officer_id.strip(),
                event_type=rb_event.strip(),
                severity=rb_severity,
                title=rb_title.strip(),
                steps=[s.strip() for s in rb_steps.splitlines() if s.strip()],
                owner_role=ROLE_TENANT_ADMIN,
            )
            st.success(f"Runbook created: {row['id']}")
        except Exception as exc:
            st.error(str(exc))

    st.markdown("### Governance Audit Review")
    review_type = st.selectbox("Review type", ["INTERNAL_AUDIT", "EXTERNAL_AUDIT", "FAIRNESS_REVIEW"], index=0)
    review_status = st.selectbox("Review status", ["OPEN", "CLOSED", "ACTION_REQUIRED"], index=0)
    findings_raw = st.text_area("Findings (one per line)", value="No cross-tenant data leak observed")
    if st.button("Log Audit Review", use_container_width=True):
        try:
            row = governance.log_audit_review(
                tenant_id=tenant_id.strip(),
                officer_id=officer_id.strip(),
                review_type=review_type,
                status=review_status,
                findings=[s.strip() for s in findings_raw.splitlines() if s.strip()],
            )
            st.success(f"Audit review logged: {row['id']}")
        except Exception as exc:
            st.error(str(exc))

    st.markdown("### Platform Oversight Access")
    platform_actor = st.text_input("Platform actor ID", value="platform-auditor-001")
    platform_role = st.selectbox("Platform role", [ROLE_PLATFORM_AUDITOR, ROLE_PLATFORM_SUPER_ADMIN], index=0)
    platform_justification = st.text_area("Justification", value="National ombudsman cross-tenant oversight")
    if st.button("Grant Platform Access", use_container_width=True):
        try:
            row = governance.grant_platform_access(
                actor_id=platform_actor.strip(),
                platform_role=platform_role,
                justification=platform_justification.strip(),
                approved_by_actor_id=officer_id.strip(),
            )
            st.success(f"Platform access granted: {row['platform_role']}")
        except Exception as exc:
            st.error(str(exc))

    if st.button("Load Cross-Tenant Overview", use_container_width=True):
        try:
            overview = governance.cross_tenant_audit_overview(platform_actor.strip())
            st.json(overview)
        except Exception as exc:
            st.error(str(exc))

st.divider()
st.subheader("Part 5 - KPI, Rollout, Risk Register")
p1, p2 = st.columns(2)
with p1:
    if st.button("Seed Part 5 Baseline", use_container_width=True):
        try:
            res = governance.seed_part5_baseline(tenant_id.strip(), officer_id.strip())
            st.success(f"Baseline seeded: {res}")
        except Exception as exc:
            st.error(str(exc))

    st.markdown("### KPI Dashboard")
    try:
        kpi_dash = governance.get_kpi_dashboard(tenant_id.strip(), officer_id.strip())
        st.json(kpi_dash)
    except Exception as exc:
        st.error(str(exc))

    st.markdown("### Record KPI Snapshot")
    kpi_key = st.text_input("KPI key", value="tamper_detection_recall_pct")
    kpi_value = st.number_input("Measured value", value=86.0, step=0.1)
    kpi_source = st.selectbox("Snapshot source", ["MANUAL", "AUDIT", "MONITORING"], index=0)
    kpi_notes = st.text_area("Snapshot notes", value="Validated on labelled set for current month")
    if st.button("Save KPI Snapshot", use_container_width=True):
        try:
            row = governance.record_kpi_snapshot(
                tenant_id=tenant_id.strip(),
                officer_id=officer_id.strip(),
                kpi_key=kpi_key.strip(),
                measured_value=float(kpi_value),
                source=kpi_source,
                notes=kpi_notes.strip() or None,
            )
            st.success(f"KPI snapshot recorded: {row['id']}")
        except Exception as exc:
            st.error(str(exc))

with p2:
    st.markdown("### Rollout Plan")
    try:
        phases = governance.get_rollout_plan(tenant_id.strip(), officer_id.strip())
        if phases:
            st.dataframe(pd.DataFrame(phases), use_container_width=True, hide_index=True)
        else:
            st.info("No rollout phases yet.")
    except Exception as exc:
        st.error(str(exc))

    st.markdown("### Risk Register")
    try:
        risks = governance.get_risk_register(tenant_id.strip(), officer_id.strip())
        if risks:
            st.dataframe(pd.DataFrame(risks), use_container_width=True, hide_index=True)
        else:
            st.info("No risks logged yet.")
    except Exception as exc:
        st.error(str(exc))

    st.markdown("### Update Risk")
    risk_code = st.text_input("Risk code", value="RISK_2_MARKER_FN")
    risk_title = st.text_input("Risk title", value="Stamp/signature detection false negatives")
    risk_mitigation = st.text_area("Mitigation", value="Conservative thresholds + human review escalation")
    risk_owner = st.text_input("Owner team", value="Fraud Team")
    risk_impact = st.selectbox("Impact", ["LOW", "MEDIUM", "HIGH", "CRITICAL"], index=2)
    risk_likelihood = st.selectbox("Likelihood", ["LOW", "MEDIUM", "HIGH"], index=1)
    risk_status = st.selectbox("Risk status", ["OPEN", "MITIGATED", "ACCEPTED", "CLOSED"], index=0)
    if st.button("Save Risk", use_container_width=True):
        try:
            row = governance.update_risk(
                tenant_id=tenant_id.strip(),
                officer_id=officer_id.strip(),
                risk_code=risk_code.strip(),
                title=risk_title.strip(),
                mitigation=risk_mitigation.strip(),
                owner_team=risk_owner.strip(),
                impact=risk_impact,
                likelihood=risk_likelihood,
                status=risk_status,
            )
            st.success(f"Risk upserted: {row['risk_code']}")
        except Exception as exc:
            st.error(str(exc))

with st.expander("Tenant Audit Timeline", expanded=False):
    try:
        tenant_events = service.list_tenant_events(tenant_id.strip(), officer_id.strip())
        if tenant_events:
            st.dataframe(pd.DataFrame(tenant_events), use_container_width=True, hide_index=True)
        else:
            st.info("No tenant events yet.")
    except Exception as exc:
        st.error(str(exc))
