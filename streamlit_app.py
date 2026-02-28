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
st.caption("Logical multi-tenant architecture with officer-bound tenant isolation")

with st.expander("Environment status", expanded=False):
    st.write(
        {
            "APP_ENV": settings.app_env,
            "SUPABASE_URL_VALID": settings.supabase_url_valid(),
            "SUPABASE_KEY_PRESENT": settings.supabase_key_present(),
            "GROQ_CONFIGURED": bool(settings.groq_api_key),
            "PERSISTENCE": "Supabase" if service.repo.using_supabase else f"In-memory fallback ({service.repo.error})",
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
                "rate_limit_per_min": policy.get("api_rate_limit_per_minute"),
                "max_docs_per_day": policy.get("max_documents_per_day"),
                "cross_tenant_fraud": policy.get("cross_tenant_fraud_enabled"),
                "export_enabled": policy.get("export_enabled"),
                "residency_region": policy.get("residency_region"),
                "storage_bucket": service.repo.get_tenant_bucket(tenant_id.strip()),
            }
        )
    except Exception as exc:
        st.error(str(exc))

st.divider()

left, right = st.columns([1, 1])

with left:
    st.subheader("Ingest Document")
    citizen_id = st.text_input("Citizen ID", value="citizen-001")
    file_name = st.text_input("File name", value="sample_document.txt")
    metadata_raw = st.text_area("Metadata JSON", value='{"source":"portal","department":"education"}', height=100)
    raw_text = st.text_area(
        "Document text/OCR input",
        value="""Name: John Doe
ID: AB12345
Issuer: State Board
This certificate includes official seal and signature.
""",
        height=180,
    )

    if st.button("Create + Process", type="primary", use_container_width=True):
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
            processed = service.process_document(
                document_id=created["id"],
                tenant_id=tenant_id.strip(),
                officer_id=officer_id.strip(),
            )
            st.success(f"Processed {processed['id']} with decision: {processed.get('decision')}")
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
        columns = [
            c
            for c in ["id", "tenant_id", "state", "decision", "confidence", "risk_score", "template_id", "dedup_hash", "expires_at", "created_at"]
            if c in df.columns
        ]
        st.dataframe(df[columns], use_container_width=True, hide_index=True)

        selected_id = st.selectbox("Select document", [d["id"] for d in docs])
        selected = next((d for d in docs if d["id"] == selected_id), None)

        if selected:
            st.write("**Selected document state:**", selected["state"])
            c1, c2, c3 = st.columns(3)

            with c1:
                if st.button("Notify", use_container_width=True):
                    try:
                        updated = service.notify(selected_id, tenant_id.strip(), officer_id.strip())
                        st.success(f"Notification processed. State={updated['state'] if updated else 'N/A'}")
                    except Exception as exc:
                        st.error(str(exc))

            with c2:
                if st.button("Manual Approve", use_container_width=True):
                    try:
                        updated = service.manual_decision(selected_id, "APPROVE", tenant_id.strip(), officer_id.strip())
                        st.success(f"Manual decision: {updated.get('decision')}")
                    except Exception as exc:
                        st.error(str(exc))

            with c3:
                if st.button("Manual Reject", use_container_width=True):
                    try:
                        updated = service.manual_decision(selected_id, "REJECT", tenant_id.strip(), officer_id.strip())
                        st.success(f"Manual decision: {updated.get('decision')}")
                    except Exception as exc:
                        st.error(str(exc))

            st.markdown("### Open Dispute")
            reason = st.text_input("Dispute reason", value="Citizen requests re-verification", key="dispute_reason")
            evidence_note = st.text_input("Evidence note", value="Attached registry screenshot", key="dispute_note")
            if st.button("Open Dispute", use_container_width=True):
                try:
                    row = service.open_dispute(selected_id, reason, evidence_note, tenant_id.strip(), officer_id.strip())
                    st.success(f"Dispute opened: {row['id']}")
                except Exception as exc:
                    st.error(str(exc))

            st.markdown("### Document Events (Tenant Filtered)")
            try:
                events = service.list_events(selected_id, tenant_id.strip(), officer_id.strip())
                if events:
                    st.dataframe(pd.DataFrame(events), use_container_width=True, hide_index=True)
                else:
                    st.info("No events for this document yet.")
            except Exception as exc:
                st.error(str(exc))

st.divider()

c4, c5 = st.columns([1, 1])
with c4:
    st.subheader("Disputes (Tenant Scoped)")
    try:
        disputes = service.list_disputes(tenant_id.strip(), officer_id.strip())
        if disputes:
            st.dataframe(pd.DataFrame(disputes), use_container_width=True, hide_index=True)
        else:
            st.info("No disputes yet.")
    except Exception as exc:
        st.error(str(exc))

with c5:
    st.subheader("Batch Export (Tenant Only)")
    include_raw = st.checkbox("Include raw text", value=False)
    if st.button("Generate CSV Export", use_container_width=True):
        try:
            csv_data = service.batch_export_documents(
                tenant_id=tenant_id.strip(),
                officer_id=officer_id.strip(),
                include_raw_text=include_raw,
            )
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

with st.expander("Tenant Audit Timeline", expanded=False):
    try:
        tenant_events = service.list_tenant_events(tenant_id.strip(), officer_id.strip())
        if tenant_events:
            st.dataframe(pd.DataFrame(tenant_events), use_container_width=True, hide_index=True)
        else:
            st.info("No tenant events yet.")
    except Exception as exc:
        st.error(str(exc))
