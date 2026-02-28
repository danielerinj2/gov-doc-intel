from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from app.config import settings
from app.services.document_service import DocumentService


st.set_page_config(page_title="Gov Document Intelligence", page_icon="📄", layout="wide")

service = DocumentService()

st.title("Government Document Intelligence")
st.caption("DAG pipeline: preprocess/hash -> branches -> merge -> decision")

with st.expander("Environment status", expanded=False):
    st.write({
        "APP_ENV": settings.app_env,
        "SUPABASE_URL_VALID": settings.supabase_url_valid(),
        "SUPABASE_KEY_PRESENT": settings.supabase_key_present(),
        "GROQ_CONFIGURED": bool(settings.groq_api_key),
        "PERSISTENCE": "Supabase" if service.repo.using_supabase else f"In-memory fallback ({service.repo.error})",
    })

st.divider()

left, right = st.columns([1, 1])

with left:
    st.subheader("Ingest Document")
    tenant_id = st.text_input("Tenant ID", value="dept-education")
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
                metadata=metadata,
            )
            processed = service.process_document(created["id"])
            st.success(f"Processed {processed['id']} with decision: {processed.get('decision')}")
        except Exception as exc:
            st.error(str(exc))

with right:
    st.subheader("Documents")
    docs = service.list_documents(tenant_id.strip() or "dept-education")
    if not docs:
        st.info("No documents yet.")
    else:
        df = pd.DataFrame(docs)
        columns = [c for c in ["id", "state", "decision", "confidence", "risk_score", "dedup_hash", "created_at"] if c in df.columns]
        st.dataframe(df[columns], use_container_width=True, hide_index=True)

        selected_id = st.selectbox("Select document", [d["id"] for d in docs])
        selected = next((d for d in docs if d["id"] == selected_id), None)

        if selected:
            st.write("**Selected document state:**", selected["state"])
            c1, c2, c3 = st.columns(3)

            with c1:
                if st.button("Notify", use_container_width=True):
                    try:
                        updated = service.notify(selected_id)
                        st.success(f"Notification processed. State={updated['state'] if updated else 'N/A'}")
                    except Exception as exc:
                        st.error(str(exc))

            with c2:
                if st.button("Manual Approve", use_container_width=True):
                    try:
                        updated = service.manual_decision(selected_id, "APPROVE")
                        st.success(f"Manual decision: {updated.get('decision')}")
                    except Exception as exc:
                        st.error(str(exc))

            with c3:
                if st.button("Manual Reject", use_container_width=True):
                    try:
                        updated = service.manual_decision(selected_id, "REJECT")
                        st.success(f"Manual decision: {updated.get('decision')}")
                    except Exception as exc:
                        st.error(str(exc))

            st.markdown("### Open Dispute")
            reason = st.text_input("Dispute reason", value="Citizen requests re-verification", key="dispute_reason")
            evidence_note = st.text_input("Evidence note", value="Attached registry screenshot", key="dispute_note")
            if st.button("Open Dispute", use_container_width=True):
                try:
                    row = service.open_dispute(selected_id, reason, evidence_note)
                    st.success(f"Dispute opened: {row['id']}")
                except Exception as exc:
                    st.error(str(exc))

            st.markdown("### Events")
            events = service.list_events(selected_id)
            if events:
                st.dataframe(pd.DataFrame(events), use_container_width=True, hide_index=True)
            else:
                st.info("No events for this document yet.")

st.divider()
st.subheader("Disputes")
disputes = service.list_disputes(tenant_id.strip() or "dept-education")
if disputes:
    st.dataframe(pd.DataFrame(disputes), use_container_width=True, hide_index=True)
else:
    st.info("No disputes yet.")
