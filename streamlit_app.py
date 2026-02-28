from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from app.config import settings
from app.infra.repositories import ROLE_ADMIN, ROLE_AUDITOR, ROLE_CASE_WORKER, ROLE_REVIEWER
from app.services.document_service import DocumentService


st.set_page_config(page_title="Gov Document Intelligence", page_icon="📄", layout="wide")

service = DocumentService()

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
            "DR_PLAN": service.dr_service.describe(),
        }
    )

with st.sidebar:
    st.header("Access Context")
    tenant_id = st.text_input("Tenant ID", value="dept-education")
    officer_id = st.text_input("Officer ID", value="officer-001")
    role = st.selectbox("Officer Role", [ROLE_CASE_WORKER, ROLE_REVIEWER, ROLE_ADMIN, ROLE_AUDITOR], index=0)

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

with st.expander("Tenant Audit Timeline", expanded=False):
    try:
        tenant_events = service.list_tenant_events(tenant_id.strip(), officer_id.strip())
        if tenant_events:
            st.dataframe(pd.DataFrame(tenant_events), use_container_width=True, hide_index=True)
        else:
            st.info("No tenant events yet.")
    except Exception as exc:
        st.error(str(exc))
