from __future__ import annotations

import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from app.config import settings
from app.infra.repositories import (
    ROLE_ADMIN,
    ROLE_AUDITOR,
    ROLE_CASE_WORKER,
    ROLE_PLATFORM_AUDITOR,
    ROLE_PLATFORM_SUPER_ADMIN,
    ROLE_REVIEWER,
    ROLE_TENANT_ADMIN,
    ROLE_TENANT_AUDITOR,
    ROLE_TENANT_OFFICER,
    ROLE_TENANT_OPERATOR,
    ROLE_TENANT_SENIOR_OFFICER,
)
from app.services.document_service import DocumentService
from app.services.governance_service import GovernanceService
from app.services.offline_service import OfflineService


st.set_page_config(page_title="Gov Document Intelligence", page_icon="📄", layout="wide")

service = DocumentService()
governance = GovernanceService(service.repo)
offline_service = OfflineService(service)

ALL_ROLES = [
    ROLE_TENANT_OPERATOR,
    ROLE_TENANT_OFFICER,
    ROLE_TENANT_SENIOR_OFFICER,
    ROLE_TENANT_ADMIN,
    ROLE_TENANT_AUDITOR,
    ROLE_CASE_WORKER,
    ROLE_REVIEWER,
    ROLE_ADMIN,
    ROLE_AUDITOR,
    ROLE_PLATFORM_AUDITOR,
    ROLE_PLATFORM_SUPER_ADMIN,
]

WRITE_ROLES = {
    ROLE_TENANT_OPERATOR,
    ROLE_TENANT_OFFICER,
    ROLE_TENANT_SENIOR_OFFICER,
    ROLE_TENANT_ADMIN,
    ROLE_CASE_WORKER,
    ROLE_REVIEWER,
    ROLE_ADMIN,
}
REVIEW_ROLES = {
    ROLE_TENANT_OFFICER,
    ROLE_TENANT_SENIOR_OFFICER,
    ROLE_TENANT_ADMIN,
    ROLE_REVIEWER,
    ROLE_ADMIN,
}
SENIOR_REVIEW_ROLES = {ROLE_TENANT_SENIOR_OFFICER, ROLE_TENANT_ADMIN, ROLE_ADMIN}
ADMIN_ROLES = {ROLE_TENANT_ADMIN, ROLE_ADMIN}
AUDIT_ROLES = {ROLE_TENANT_AUDITOR, ROLE_AUDITOR, ROLE_TENANT_ADMIN, ROLE_ADMIN}
PLATFORM_ROLES = {ROLE_PLATFORM_AUDITOR, ROLE_PLATFORM_SUPER_ADMIN}
SENSITIVE_VIEW_ROLES = {
    ROLE_TENANT_OPERATOR,
    ROLE_TENANT_OFFICER,
    ROLE_TENANT_SENIOR_OFFICER,
    ROLE_TENANT_ADMIN,
    ROLE_CASE_WORKER,
    ROLE_REVIEWER,
    ROLE_ADMIN,
}

PAGES = [
    "Intake & Processing",
    "Review Workbench",
    "Dispute Desk",
    "Fraud & Authenticity",
    "Citizen Communication",
    "Audit Trail & Explainability",
    "Governance & KPI",
    "Ops & DR Monitor",
    "Integrations (API/Webhook/Export)",
    "Offline Sync Console",
]

PAGE_ACCESS = {
    "Intake & Processing": WRITE_ROLES | AUDIT_ROLES,
    "Review Workbench": REVIEW_ROLES | AUDIT_ROLES,
    "Dispute Desk": REVIEW_ROLES | AUDIT_ROLES,
    "Fraud & Authenticity": REVIEW_ROLES | AUDIT_ROLES,
    "Citizen Communication": WRITE_ROLES | REVIEW_ROLES | AUDIT_ROLES,
    "Audit Trail & Explainability": AUDIT_ROLES | REVIEW_ROLES,
    "Governance & KPI": ADMIN_ROLES | {ROLE_TENANT_AUDITOR, ROLE_AUDITOR} | PLATFORM_ROLES,
    "Ops & DR Monitor": ADMIN_ROLES | REVIEW_ROLES | PLATFORM_ROLES,
    "Integrations (API/Webhook/Export)": ADMIN_ROLES | REVIEW_ROLES,
    "Offline Sync Console": WRITE_ROLES | ADMIN_ROLES,
}


def _safe_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value).replace("Z", "+00:00")
        return datetime.fromisoformat(text)
    except Exception:
        return None


def _mask(value: str, role: str) -> str:
    if role in SENSITIVE_VIEW_ROLES:
        return value
    if len(value) <= 4:
        return "*" * len(value)
    return f"{'*' * (len(value) - 4)}{value[-4:]}"


def _unwrap_record(latest_row: dict[str, Any] | None) -> dict[str, Any]:
    wrapped = dict((latest_row or {}).get("record") or {})
    if "document_record" in wrapped and isinstance(wrapped.get("document_record"), dict):
        return dict(wrapped.get("document_record") or {})
    return wrapped


def _to_table_rows(value: Any, key_name: str = "key", value_name: str = "value") -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [v if isinstance(v, dict) else {value_name: v} for v in value]
    if isinstance(value, dict):
        return [{key_name: k, value_name: v} for k, v in value.items()]
    return []


def _normalize_extracted_fields(extraction_out: dict[str, Any]) -> list[dict[str, Any]]:
    fields = extraction_out.get("fields")
    if isinstance(fields, list):
        rows: list[dict[str, Any]] = []
        for item in fields:
            if isinstance(item, dict):
                rows.append(item)
        return rows
    if isinstance(fields, dict):
        return [{"field_name": k, "normalized_value": v} for k, v in fields.items()]
    return []


def _render_journey(title: str, steps: list[str]) -> None:
    st.markdown(f"### {title}")
    st.info(" -> ".join(steps))


def _read_uploaded_document(uploaded_file: Any) -> tuple[str, str | None]:
    if uploaded_file is None:
        return "", None

    suffix = Path(str(uploaded_file.name)).suffix or ".bin"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as handle:
        handle.write(uploaded_file.getvalue())
        tmp_path = handle.name

    raw_text = ""
    file_ext = suffix.lower()
    if file_ext in {".txt", ".csv", ".json"}:
        try:
            raw_text = uploaded_file.getvalue().decode("utf-8", errors="ignore")
        except Exception:
            raw_text = ""
    elif file_ext == ".pdf":
        try:
            from pypdf import PdfReader  # type: ignore

            reader = PdfReader(tmp_path)
            chunks = []
            for page in reader.pages[:10]:
                chunks.append(page.extract_text() or "")
            raw_text = "\n".join(chunks).strip()
        except Exception:
            raw_text = ""

    return raw_text, tmp_path


def _render_env_status() -> None:
    with st.expander("Environment status", expanded=False):
        st.write(
            {
                "APP_ENV": settings.app_env,
                "SUPABASE_URL_VALID": settings.supabase_url_valid(),
                "SUPABASE_KEY_PRESENT": settings.supabase_key_present(),
                "GROQ_CONFIGURED": bool(settings.groq_api_key),
                "EVENT_BUS_BACKEND": settings.event_bus_backend,
                "OCR_BACKEND": settings.ocr_backend,
                "AUTHENTICITY_BACKEND": settings.authenticity_backend,
                "PERSISTENCE": "Supabase" if service.repo.using_supabase else f"In-memory fallback ({service.repo.error})",
                "PART2_SCHEMA_READY": service.repo.part2_schema_ready,
                "PART3_SCHEMA_READY": service.repo.part3_schema_ready,
                "PART4_SCHEMA_READY": service.repo.part4_schema_ready,
                "PART5_SCHEMA_READY": service.repo.part5_schema_ready,
                "DR_PLAN": service.dr_service.describe(),
            }
        )


def _load_docs(
    tenant_id: str,
    officer_id: str,
    role: str,
) -> tuple[list[dict[str, Any]], str | None]:
    try:
        return service.list_documents(tenant_id, officer_id), None
    except Exception as exc:
        return [], str(exc)


def _render_document_header(
    *,
    role: str,
    tenant_id: str,
    policy: dict[str, Any],
    selected_doc: dict[str, Any] | None,
    selected_record: dict[str, Any],
) -> None:
    st.markdown("## Document Header")
    if not selected_doc:
        st.info("Select a document from the sidebar to load shared document header fields.")
        return

    metadata = dict(selected_doc.get("metadata") or {})
    ingestion = dict(metadata.get("ingestion") or {})
    human_review = dict(metadata.get("human_review") or {})
    classification = dict(selected_record.get("classification_output") or {})
    template_ref = dict(selected_record.get("template_definition_ref") or {})
    fraud_out = dict(selected_record.get("fraud_risk_output") or {})

    received_at = ingestion.get("received_at") or selected_doc.get("created_at")
    updated_at = selected_doc.get("updated_at")
    review_sla_days = int(policy.get("review_sla_days", 3))
    due_dt = _safe_dt(received_at)
    sla_due = (due_dt + timedelta(days=review_sla_days)).isoformat() if due_dt else None

    row = {
        "document_id": selected_doc.get("id"),
        "job_id": selected_doc.get("last_job_id"),
        "tenant_id": tenant_id,
        "citizen_id": _mask(str(selected_doc.get("citizen_id", "")), role),
        "doc_type": classification.get("doc_type") or classification.get("document_type") or "UNKNOWN",
        "template_id": selected_doc.get("template_id") or template_ref.get("template_id"),
        "template_version": template_ref.get("template_version"),
        "current_state": selected_doc.get("state"),
        "decision": selected_doc.get("decision"),
        "confidence": selected_doc.get("confidence"),
        "fraud_risk_score": selected_doc.get("risk_score") or fraud_out.get("aggregate_fraud_risk_score"),
        "risk_level": fraud_out.get("risk_level", "UNKNOWN"),
        "received_at": received_at,
        "last_updated_at": updated_at,
        "assigned_officer": human_review.get("assigned_to_officer_id"),
        "SLA_due_at": sla_due,
    }

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("State", str(row["current_state"]))
    c2.metric("Decision", str(row["decision"] or "PENDING"))
    c3.metric("Confidence", str(row["confidence"] if row["confidence"] is not None else "-"))
    c4.metric("Fraud Risk", str(row["fraud_risk_score"] if row["fraud_risk_score"] is not None else "-"))
    st.dataframe(pd.DataFrame([row]), use_container_width=True, hide_index=True)


def _render_stakeholder_snapshot(
    *,
    role: str,
    tenant_id: str,
    officer_id: str,
    selected_doc: dict[str, Any] | None,
    selected_record: dict[str, Any],
) -> None:
    st.markdown("### Stakeholder-Specific Fields")

    if not selected_doc:
        st.caption("Select a document to load role-specific context.")
        return

    derived = dict(selected_doc.get("derived") or {})
    events: list[dict[str, Any]] = []
    try:
        events = service.list_events(str(selected_doc.get("id")), tenant_id, officer_id)
    except Exception:
        events = []

    if role in {ROLE_TENANT_OPERATOR, ROLE_CASE_WORKER}:
        st.write(
            {
                "intake_quality_score": (derived.get("preprocessing_hashing") or {}).get("quality_score"),
                "ocr_confidence": (derived.get("ocr_multi_script") or {}).get("ocr_confidence"),
                "missing_fields": (derived.get("validation") or {}).get("missing_fields", []),
                "upload_errors": [e for e in events if str(e.get("event_type")) == "document.failed"],
                "queue_status": selected_doc.get("state"),
            }
        )

    elif role in {ROLE_TENANT_OFFICER, ROLE_REVIEWER}:
        st.write(
            {
                "extracted_fields_count": len(_normalize_extracted_fields(dict(selected_record.get("extraction_output") or {}))),
                "validation_overall": (selected_record.get("validation_output") or {}).get("overall_status"),
                "authenticity_score": (selected_record.get("visual_authenticity_output") or {}).get("visual_authenticity_score"),
                "explainability_reasons": (selected_record.get("explainability") or {}).get("document_explanations", []),
                "actions": ["approve", "reject", "field_correction"],
            }
        )

    elif role == ROLE_TENANT_SENIOR_OFFICER:
        disputes = service.list_disputes(tenant_id, officer_id)
        overrides = [
            e
            for e in events
            if str(e.get("event_type")) in {"review.completed", "document.approved", "document.rejected"}
        ]
        st.write(
            {
                "escalations": service.list_review_escalations(tenant_id, officer_id),
                "dispute_history": disputes,
                "override_log": overrides,
                "internal_disagreement_resolution": "Enabled",
            }
        )

    elif role in {ROLE_TENANT_ADMIN, ROLE_ADMIN}:
        policy = service.repo.get_tenant_policy(tenant_id)
        partition = service.repo.get_tenant_partition_config(tenant_id)
        data_policy = service.repo.get_tenant_data_policy(tenant_id)
        assignments = service.list_review_assignments(tenant_id, officer_id)
        breaches = [a for a in assignments if str(a.get("status")) == "WAITING_FOR_REVIEW"]
        st.write(
            {
                "template_version": (selected_record.get("template_definition_ref") or {}).get("template_version"),
                "rule_set_id": (selected_record.get("validation_output") or {}).get("rule_set_id"),
                "sla_breaches": len(breaches),
                "officer_workload": assignments,
                "tenant_policy_config": policy,
                "partition_data_retention": {"partition": partition, "retention": data_policy},
            }
        )

    elif role in {ROLE_TENANT_AUDITOR, ROLE_AUDITOR}:
        st.write(
            {
                "full_state_history": (selected_record.get("state_machine") or {}).get("history", []),
                "event_timeline": events,
                "model_versions": {
                    "ocr": (selected_record.get("ocr_output") or {}).get("model_metadata"),
                    "classification": (selected_record.get("classification_output") or {}).get("model_metadata"),
                    "validation": (selected_record.get("validation_output") or {}).get("model_metadata"),
                },
                "human_overrides": (((selected_record.get("human_review") or {}).get("review_events")) or []),
                "immutable_audit_logs": service.list_model_audit_logs(tenant_id, officer_id, document_id=str(selected_doc.get("id"))),
            }
        )

    elif role in PLATFORM_ROLES:
        st.write(
            {
                "cross_tenant_aggregates": "Visible only with explicit platform grant",
                "isolation_checks": "RLS + tenant-bound officer scope",
                "incident_summaries": service.monitoring_dashboard(tenant_id, officer_id),
            }
        )


def _render_intake_processing(
    *,
    role: str,
    tenant_id: str,
    officer_id: str,
    docs: list[dict[str, Any]],
    selected_doc: dict[str, Any] | None,
) -> None:
    _render_journey(
        "User Journey - Citizen Online (Backend-mediated)",
        [
            "citizen submits on govt portal",
            "backend receives document",
            "pipeline decision/review",
            "citizen notified via portal/SMS",
        ],
    )
    _render_journey(
        "User Journey - Service Center Assisted",
        [
            "operator scans/captures",
            "optional offline queue",
            "sync to central",
            "central decision + citizen feedback",
        ],
    )

    can_write = role in WRITE_ROLES
    intake_tab1, intake_tab2, intake_tab3 = st.tabs(
        ["Online Submission Intake", "Service Center Intake", "Operational Inputs"]
    )

    with intake_tab1:
        st.markdown("### Portal/API Ingestion")
        uploaded = st.file_uploader(
            "Upload document (PDF/JPG/PNG/TXT)",
            type=["pdf", "jpg", "jpeg", "png", "txt", "csv", "json"],
            key="portal_upload",
        )
        with st.form("intake_form_online"):
            citizen_id = st.text_input("Citizen ID", value="citizen-001")
            file_name = st.text_input("File name", value="portal_submission.txt")
            prefilled_json = st.text_area(
                "Pre-filled application data JSON (cross-verify)",
                value='{"name":"John Doe","document_number":"AB12345"}',
                height=80,
            )
            metadata_raw = st.text_area("Metadata JSON", value='{"source":"ONLINE_PORTAL"}', height=80)
            fallback_text = st.text_area("Fallback text (if upload has no text)", value="", height=110)
            create = st.form_submit_button("Create", disabled=not can_write, use_container_width=True)
            create_process = st.form_submit_button("Create + Process", disabled=not can_write, use_container_width=True)

        if create or create_process:
            try:
                parsed_prefilled = json.loads(prefilled_json) if prefilled_json.strip() else {}
                metadata = json.loads(metadata_raw) if metadata_raw.strip() else {}
                upload_text, source_path = _read_uploaded_document(uploaded)
                raw_text = upload_text or fallback_text or ""
                final_name = file_name.strip() or (uploaded.name if uploaded else "uploaded_document")
                ingestion = dict(metadata.get("ingestion") or {})
                if source_path:
                    ingestion["original_file_uri"] = source_path
                ingestion.setdefault("source", "ONLINE_PORTAL")
                metadata["ingestion"] = ingestion
                metadata["prefilled_form_data"] = parsed_prefilled
                created = service.create_document(
                    tenant_id=tenant_id,
                    citizen_id=citizen_id.strip(),
                    file_name=final_name,
                    raw_text=raw_text,
                    officer_id=officer_id,
                    metadata=metadata,
                )
                if create_process:
                    processed = service.process_document(str(created["id"]), tenant_id, officer_id)
                    st.success(f"Created and processed {processed['id']} -> {processed.get('state')}")
                else:
                    st.success(f"Created document {created['id']}")
            except Exception as exc:
                st.error(str(exc))

    with intake_tab2:
        st.markdown("### Service Center Capture")
        sc_uploaded = st.file_uploader(
            "Center capture (scanner/camera)",
            type=["pdf", "jpg", "jpeg", "png", "txt"],
            key="center_upload",
        )
        with st.form("intake_form_center"):
            citizen_id_sc = st.text_input("Citizen ID", value="citizen-center-001")
            file_name_sc = st.text_input("File name", value="center_capture.txt")
            center_id = st.text_input("Service center ID", value="center-01")
            reference_no = st.text_input("Citizen reference number", value="REF-001")
            center_meta_raw = st.text_area(
                "Center metadata JSON",
                value='{"source":"SERVICE_CENTER","service_type":"WELFARE_SCHEME"}',
                height=80,
            )
            center_fallback_text = st.text_area("Fallback text", value="", height=100)
            save_only = st.form_submit_button("Save Intake", disabled=not can_write, use_container_width=True)
            process_now = st.form_submit_button("Save + Process", disabled=not can_write, use_container_width=True)
            save_offline = st.form_submit_button("Save as Offline Provisional", disabled=not can_write, use_container_width=True)

        if save_only or process_now or save_offline:
            try:
                metadata_sc = json.loads(center_meta_raw) if center_meta_raw.strip() else {}
                upload_text, source_path = _read_uploaded_document(sc_uploaded)
                raw_text_sc = upload_text or center_fallback_text or ""
                final_name_sc = file_name_sc.strip() or (sc_uploaded.name if sc_uploaded else "center_capture")
                ingestion_sc = dict(metadata_sc.get("ingestion") or {})
                ingestion_sc.setdefault("source", "SERVICE_CENTER")
                ingestion_sc["operator_center_id"] = center_id.strip()
                ingestion_sc["citizen_reference_number"] = reference_no.strip()
                if source_path:
                    ingestion_sc["original_file_uri"] = source_path
                metadata_sc["ingestion"] = ingestion_sc

                if save_offline:
                    out = offline_service.create_offline_provisional(
                        tenant_id=tenant_id,
                        citizen_id=citizen_id_sc.strip(),
                        file_name=final_name_sc,
                        raw_text=raw_text_sc,
                        officer_id=officer_id,
                        local_model_versions={"ocr_model_id": "ocr-lite-v1", "classifier_model_id": "classifier-lite-v1"},
                        provisional_decision="REVIEW",
                        metadata=metadata_sc,
                    )
                    st.success(f"Offline provisional created: {out.get('id')}")
                else:
                    created = service.create_document(
                        tenant_id=tenant_id,
                        citizen_id=citizen_id_sc.strip(),
                        file_name=final_name_sc,
                        raw_text=raw_text_sc,
                        officer_id=officer_id,
                        metadata=metadata_sc,
                    )
                    if process_now:
                        processed = service.process_document(str(created["id"]), tenant_id, officer_id)
                        st.success(f"Saved and processed {processed['id']} -> {processed.get('state')}")
                    else:
                        st.success(f"Saved service-center intake {created['id']}")
            except Exception as exc:
                st.error(str(exc))

    with intake_tab3:
        st.markdown("### Operational / Training Inputs")
        st.write(
            {
                "operator_corrections": "Use Review Workbench -> Field Correction",
                "new_templates_rules": "Use Governance & KPI -> A1 Template/Rule Management",
                "offline_sync_inputs": "Use Offline Sync Console",
            }
        )

    if not can_write:
        st.warning("Actions are hidden for this role. Read-only access only.")

    st.markdown("### Queue Status")
    if not docs:
        st.info("No documents yet for this tenant.")
    else:
        df = pd.DataFrame(docs)
        f1, f2, f3 = st.columns(3)
        with f1:
            state_filter = st.selectbox("Filter by state", ["ALL"] + sorted({str(x) for x in df.get("state", []).tolist()}), index=0)
        with f2:
            min_risk = st.slider("Min risk score", min_value=0.0, max_value=1.0, value=0.0, step=0.05)
        with f3:
            sla_only = st.checkbox("SLA nearing breach (<=24h)", value=False)

        if state_filter != "ALL":
            df = df[df["state"] == state_filter]
        if "risk_score" in df.columns:
            df = df[df["risk_score"].fillna(0.0) >= float(min_risk)]
        if sla_only:
            now = datetime.now(timezone.utc)
            policy = service.repo.get_tenant_policy(tenant_id)
            review_sla_days = int(policy.get("review_sla_days", 3))
            due_cutoff = now + timedelta(hours=24)
            flags: list[bool] = []
            for _, row in df.iterrows():
                created_at = _safe_dt(row.get("created_at"))
                if not created_at:
                    flags.append(False)
                    continue
                due_at = created_at + timedelta(days=review_sla_days)
                flags.append(due_at <= due_cutoff and str(row.get("state")) in {"WAITING_FOR_REVIEW", "REVIEW_IN_PROGRESS"})
            df = df[pd.Series(flags, index=df.index)]

        cols = [
            c
            for c in [
                "id",
                "state",
                "decision",
                "confidence",
                "risk_score",
                "template_id",
                "queue_overflow",
                "created_at",
                "updated_at",
            ]
            if c in df.columns
        ]
        st.dataframe(df[cols], use_container_width=True, hide_index=True)

    st.markdown("### Tenant Operator Fields")
    if selected_doc:
        derived = dict(selected_doc.get("derived") or {})
        try:
            events = service.list_events(str(selected_doc.get("id")), tenant_id, officer_id)
        except Exception:
            events = []
        st.write(
            {
                "intake_quality_score": (derived.get("preprocessing_hashing") or {}).get("quality_score"),
                "ocr_confidence": (derived.get("ocr_multi_script") or {}).get("ocr_confidence"),
                "missing_fields": (derived.get("validation") or {}).get("missing_fields", []),
                "upload_errors": [e for e in events if str(e.get("event_type")) == "document.failed"],
                "queue_status": selected_doc.get("state"),
            }
        )


def _render_review_workbench(
    *,
    role: str,
    tenant_id: str,
    officer_id: str,
    selected_doc: dict[str, Any] | None,
    selected_record: dict[str, Any],
) -> None:
    _render_journey(
        "User Journey - Officer Review",
        [
            "pick from queue",
            "inspect explainability + evidence",
            "correct fields if needed",
            "final decision",
        ],
    )

    try:
        assignments = service.list_review_assignments(tenant_id, officer_id)
        if assignments:
            st.markdown("### Review Queue")
            st.dataframe(pd.DataFrame(assignments), use_container_width=True, hide_index=True)
    except Exception as exc:
        st.error(str(exc))

    if not selected_doc:
        st.info("Select a document from sidebar.")
        return

    extraction_out = dict(selected_record.get("extraction_output") or {})
    validation_out = dict(selected_record.get("validation_output") or {})
    visual_out = dict(selected_record.get("visual_authenticity_output") or {})
    explainability = dict(selected_record.get("explainability") or {})

    st.markdown("### Extracted Fields")
    extracted_rows = _normalize_extracted_fields(extraction_out)
    if extracted_rows:
        st.dataframe(pd.DataFrame(extracted_rows), use_container_width=True, hide_index=True)
    else:
        st.info("No extracted fields available.")

    st.markdown("### Validation Results")
    field_results = validation_out.get("field_results") or []
    if field_results:
        st.dataframe(pd.DataFrame(field_results), use_container_width=True, hide_index=True)
    else:
        st.write({
            "overall_status": validation_out.get("overall_status"),
            "rule_set_id": validation_out.get("rule_set_id"),
        })

    derived_validation = (selected_doc.get("derived") or {}).get("validation", {})
    st.markdown("### Pre-filled Data Cross-Verification")
    st.write(
        {
            "status": derived_validation.get("prefilled_consistency_status", "NOT_AVAILABLE"),
            "match_count": derived_validation.get("prefilled_match_count", 0),
            "mismatch_count": derived_validation.get("prefilled_mismatch_count", 0),
        }
    )
    mismatches = list(derived_validation.get("prefilled_mismatches") or [])
    if mismatches:
        st.dataframe(pd.DataFrame(mismatches), use_container_width=True, hide_index=True)

    st.markdown("### Authenticity Markers")
    marker_rows = visual_out.get("markers") or []
    if marker_rows:
        st.dataframe(pd.DataFrame(marker_rows), use_container_width=True, hide_index=True)
    else:
        st.write({"visual_authenticity_score": visual_out.get("visual_authenticity_score")})

    st.markdown("### Explainability Reasons")
    st.write(
        {
            "field_explanations": explainability.get("field_explanations", []),
            "document_explanations": explainability.get("document_explanations", []),
        }
    )

    can_review = role in REVIEW_ROLES
    if not can_review:
        st.warning("Approve/Reject actions are hidden for this role.")
        return

    c1, c2, c3 = st.columns(3)
    with c1:
        if st.button("Start Review", use_container_width=True, key="wb_start_review"):
            try:
                out = service.start_review(str(selected_doc.get("id")), tenant_id, officer_id, review_level="L1")
                st.success(f"Review started. State={out.get('state')}")
            except Exception as exc:
                st.error(str(exc))
    with c2:
        if st.button("Approve", use_container_width=True, key="wb_approve"):
            try:
                out = service.manual_decision(str(selected_doc.get("id")), "APPROVE", tenant_id, officer_id)
                st.success(f"Decision={out.get('decision')} state={out.get('state')}")
            except Exception as exc:
                st.error(str(exc))
    with c3:
        if st.button("Reject", use_container_width=True, key="wb_reject"):
            try:
                out = service.manual_decision(str(selected_doc.get("id")), "REJECT", tenant_id, officer_id)
                st.success(f"Decision={out.get('decision')} state={out.get('state')}")
            except Exception as exc:
                st.error(str(exc))

    st.markdown("### Field Correction")
    with st.form("wb_correction_form"):
        corr_field = st.text_input("Field name", value="TOTAL_MARKS")
        corr_old = st.text_input("Old value", value="580")
        corr_new = st.text_input("New value", value="560")
        corr_reason = st.text_input("Reason", value="Corrected as per physical document")
        submit_correction = st.form_submit_button("Log Correction", use_container_width=True)
    if submit_correction:
        try:
            gate = service.record_field_correction(
                document_id=str(selected_doc.get("id")),
                tenant_id=tenant_id,
                officer_id=officer_id,
                field_name=corr_field.strip(),
                old_value=corr_old.strip() or None,
                new_value=corr_new.strip() or None,
                reason=corr_reason.strip() or "FIELD_CORRECTION",
            )
            st.success(f"Correction logged. Gate status={gate['gate'].get('status')}")
        except Exception as exc:
            st.error(str(exc))


def _render_dispute_desk(
    *,
    role: str,
    tenant_id: str,
    officer_id: str,
    selected_doc: dict[str, Any] | None,
) -> None:
    _render_journey(
        "User Journey - Dispute",
        [
            "citizen appeal",
            "DISPUTED",
            "senior review",
            "final resolution",
            "archived",
        ],
    )

    try:
        disputes = service.list_disputes(tenant_id, officer_id)
        if disputes:
            st.dataframe(pd.DataFrame(disputes), use_container_width=True, hide_index=True)
        else:
            st.info("No disputes for this tenant.")
    except Exception as exc:
        st.error(str(exc))

    can_open_dispute = role in WRITE_ROLES or role in REVIEW_ROLES
    if selected_doc and can_open_dispute:
        st.markdown("### Open Dispute")
        with st.form("open_dispute_form"):
            reason = st.text_input("Dispute reason", value="Citizen requests re-verification")
            note = st.text_input("Evidence note", value="Attached supporting reference")
            submit = st.form_submit_button("Submit Dispute", use_container_width=True)
        if submit:
            try:
                row = service.open_dispute(str(selected_doc.get("id")), reason, note, tenant_id, officer_id)
                st.success(f"Dispute submitted: {row['id']}")
            except Exception as exc:
                st.error(str(exc))

    if role in SENIOR_REVIEW_ROLES and selected_doc:
        st.markdown("### Senior Resolution Tools")
        reason = st.text_input("Internal disagreement reason", value="Officer assessments conflict", key="disp_internal_reason")
        if st.button("Flag Internal Disagreement", use_container_width=True):
            try:
                res = service.flag_internal_disagreement(
                    document_id=str(selected_doc.get("id")),
                    tenant_id=tenant_id,
                    officer_id=officer_id,
                    reason=reason.strip() or "OFFICER_DECISION_CONFLICT",
                )
                st.success(f"Escalated disagreement: {res['escalation'].get('id')}")
            except Exception as exc:
                st.error(str(exc))


def _render_fraud_authenticity(*, selected_doc: dict[str, Any] | None, selected_record: dict[str, Any]) -> None:
    _render_journey(
        "User Journey - Fraud Escalation",
        [
            "high risk flag",
            "fraud desk review",
            "officer decision support",
            "audit retained",
        ],
    )

    if not selected_doc:
        st.info("Select a document from sidebar.")
        return

    fraud_out = dict(selected_record.get("fraud_risk_output") or {})
    visual_out = dict(selected_record.get("visual_authenticity_output") or {})
    issuer_out = dict(selected_record.get("issuer_verification_output") or {})

    st.markdown("### Fraud Team Fields")
    st.write(
        {
            "tamper_signals": ((visual_out.get("image_forensics") or {}).get("tamper_signals", [])),
            "duplicate_clusters": (((fraud_out.get("components") or {}).get("behavioral_component") or {}).get("signals", [])),
            "behavioral_flags": (((fraud_out.get("components") or {}).get("behavioral_component") or {}).get("signals", [])),
            "issuer_mismatch_component": (((fraud_out.get("components") or {}).get("issuer_mismatch_component") or {})),
            "related_job_links": (((fraud_out.get("components") or {}).get("behavioral_component") or {}).get("related_job_ids", [])),
            "aggregate_fraud_risk_score": fraud_out.get("aggregate_fraud_risk_score"),
            "risk_level": fraud_out.get("risk_level"),
        }
    )

    components = fraud_out.get("components") or {}
    if components:
        st.dataframe(pd.DataFrame(_to_table_rows(components, key_name="component", value_name="details")), use_container_width=True, hide_index=True)

    st.markdown("### Authenticity")
    markers = visual_out.get("markers") or []
    if markers:
        st.dataframe(pd.DataFrame(markers), use_container_width=True, hide_index=True)
    else:
        st.write({"visual_authenticity_score": visual_out.get("visual_authenticity_score")})

    st.markdown("### Issuer Verification")
    st.write(issuer_out or {"status": "NOT_AVAILABLE"})

    st.markdown("### Operator Guidance")
    risk_level = str(fraud_out.get("risk_level", "MEDIUM")).upper()
    guidance: list[str] = []
    if risk_level in {"HIGH", "CRITICAL"}:
        guidance.append("Escalate to Fraud Desk queue before final approval.")
    if ((visual_out.get("image_forensics") or {}).get("tamper_signals")):
        guidance.append("Manually inspect highlighted tamper regions and compare with source scan.")
    issuer_status = str(issuer_out.get("status") or issuer_out.get("registry_status") or "UNKNOWN").upper()
    if issuer_status in {"MISMATCH", "UNVERIFIED", "NOT_FOUND", "ERROR"}:
        guidance.append("Request alternate issuer proof or direct verification reference.")
    if not guidance:
        guidance.append("No critical fraud signals detected; continue standard review workflow.")
    st.write({"suggestions": guidance})


def _render_citizen_communication(
    *,
    role: str,
    tenant_id: str,
    officer_id: str,
    selected_doc: dict[str, Any] | None,
) -> None:
    st.caption("Citizen interactions are handled through government portals/service centers; this screen is backend operator view.")
    _render_journey(
        "User Journey - Citizen Notifications",
        [
            "document received",
            "review updates",
            "approved/rejected",
            "plain-language reasons",
            "next steps/dispute",
        ],
    )

    try:
        notifications = service.list_notifications(tenant_id, officer_id)
        if notifications:
            st.dataframe(pd.DataFrame(notifications), use_container_width=True, hide_index=True)
        else:
            st.info("No notifications yet.")
    except Exception as exc:
        st.error(str(exc))

    can_notify = role in WRITE_ROLES or role in REVIEW_ROLES
    if selected_doc and can_notify and st.button("Send Notification Event", use_container_width=True):
        try:
            service.notify(str(selected_doc.get("id")), tenant_id, officer_id)
            st.success("Notification event emitted")
        except Exception as exc:
            st.error(str(exc))

    if not selected_doc:
        return

    st.markdown("### Citizen View")
    default_citizen = str(selected_doc.get("citizen_id", ""))
    lookup = st.text_input("Citizen ID", value=default_citizen, key="citizen_lookup_case")
    if st.button("Load Citizen Case", use_container_width=True):
        try:
            view = service.get_citizen_case_view(tenant_id, str(selected_doc.get("id")), lookup.strip())
            st.write(
                {
                    "status": view.get("state"),
                    "decision": view.get("decision"),
                    "rejection_reason_plain": view.get("explanation_text"),
                    "next_steps": view.get("next_steps"),
                    "dispute_option": "AVAILABLE",
                    "notification_history": notifications if "notifications" in locals() else [],
                }
            )
        except Exception as exc:
            st.error(str(exc))


def _render_audit_explainability(
    *,
    tenant_id: str,
    officer_id: str,
    selected_doc: dict[str, Any] | None,
    selected_record: dict[str, Any],
) -> None:
    if not selected_doc:
        st.info("Select a document from sidebar.")
        return

    st.markdown("### State History")
    state_history = ((selected_record.get("state_machine") or {}).get("history")) or []
    if state_history:
        st.dataframe(pd.DataFrame(state_history), use_container_width=True, hide_index=True)
    else:
        st.info("No state history found in latest document record.")

    st.markdown("### Event Timeline")
    try:
        events = service.list_events(str(selected_doc.get("id")), tenant_id, officer_id)
        st.dataframe(pd.DataFrame(events), use_container_width=True, hide_index=True)
    except Exception as exc:
        st.error(str(exc))

    st.markdown("### Model and Rule Versions")
    st.write(
        {
            "ocr_model": (selected_record.get("ocr_output") or {}).get("model_metadata"),
            "classification_model": (selected_record.get("classification_output") or {}).get("model_metadata"),
            "validation_models": (selected_record.get("validation_output") or {}).get("model_metadata"),
            "rule_set_id": (selected_record.get("validation_output") or {}).get("rule_set_id"),
        }
    )

    st.markdown("### Human Overrides")
    review_events = ((selected_record.get("human_review") or {}).get("review_events")) or []
    if review_events:
        st.dataframe(pd.DataFrame(review_events), use_container_width=True, hide_index=True)
    else:
        st.info("No human override events recorded.")

    st.markdown("### Immutable AI Audit Logs")
    try:
        logs = service.list_model_audit_logs(tenant_id, officer_id, document_id=str(selected_doc.get("id")))
        if logs:
            st.dataframe(pd.DataFrame(logs), use_container_width=True, hide_index=True)
        else:
            st.info("No model audit logs yet.")
    except Exception as exc:
        st.error(str(exc))

    with st.expander("Unified document_record JSON", expanded=False):
        st.json(selected_record)


def _render_governance_kpi(*, role: str, tenant_id: str, officer_id: str) -> None:
    _render_journey(
        "User Journey - Governance Cycle",
        [
            "monthly KPI/risk/runbook review",
            "policy update",
            "audit sign-off",
        ],
    )

    try:
        snapshot = governance.get_tenant_governance_snapshot(tenant_id, officer_id)
        st.json(snapshot)
    except Exception as exc:
        st.error(str(exc))

    try:
        kpi = governance.get_kpi_dashboard(tenant_id, officer_id)
        st.markdown("### KPI Dashboard")
        st.json(kpi)
    except Exception as exc:
        st.error(str(exc))

    if role not in ADMIN_ROLES:
        st.info("Policy/config update actions are hidden for this role.")
    else:
        st.markdown("### Tenant Policy Config")
        with st.form("gov_policy_form"):
            raw_years = st.number_input("Raw image retention (years)", min_value=1, max_value=30, value=7, step=1)
            structured_years = st.number_input("Structured data retention (years)", min_value=1, max_value=30, value=10, step=1)
            fraud_years = st.number_input("Fraud logs retention (years)", min_value=1, max_value=30, value=10, step=1)
            purge_policy = st.selectbox("Purge policy", ["ANONYMIZE_AFTER_EXPIRY", "HARD_DELETE_AFTER_EXPIRY"], index=0)
            save_policy = st.form_submit_button("Save Data Policy", use_container_width=True)
        if save_policy:
            try:
                row = governance.update_tenant_data_policy(
                    tenant_id,
                    officer_id,
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

        with st.form("gov_partition_form"):
            partition_mode = st.selectbox(
                "Partition mode",
                ["LOGICAL_SHARED", "DEDICATED_SCHEMA", "DEDICATED_CLUSTER", "DEDICATED_DEPLOYMENT"],
                index=0,
            )
            residency_region = st.text_input("Residency region", value="default")
            region_cluster = st.text_input("Region cluster", value="region-a")
            physical_iso = st.checkbox("Physical isolation required", value=False)
            save_partition = st.form_submit_button("Save Partition Config", use_container_width=True)
        if save_partition:
            try:
                row = governance.update_tenant_partition_config(
                    tenant_id,
                    officer_id,
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

        st.markdown("### A1 - Template Management")
        try:
            tpl_rows = service.list_tenant_templates(tenant_id, officer_id)
            if tpl_rows:
                st.dataframe(pd.DataFrame(tpl_rows), use_container_width=True, hide_index=True)
            else:
                st.info("No tenant templates found.")
        except Exception as exc:
            st.error(str(exc))

        with st.form("template_upsert_form"):
            doc_type = st.text_input("Document type", value="AADHAAR_CARD")
            template_id = st.text_input("Template ID", value="aadhaar_template_default")
            template_ver = st.text_input("Template version", value="2025.1.0")
            template_schema_ver = st.number_input("Template config version", min_value=1, value=1, step=1)
            rule_set_ref = st.text_input("Policy rule_set_id", value="RULESET_AADHAAR_DEFAULT")
            lifecycle = st.selectbox("Lifecycle status", ["ACTIVE", "DEPRECATED", "RETIRED"], index=0)
            active = st.checkbox("Active template", value=True)
            cfg_raw = st.text_area("Template config JSON", value='{"fields":[],"visual_markers":[]}', height=80)
            save_tpl = st.form_submit_button("Save Template", use_container_width=True)
        if save_tpl:
            try:
                cfg = json.loads(cfg_raw) if cfg_raw.strip() else {}
                row = service.save_tenant_template(
                    tenant_id=tenant_id,
                    officer_id=officer_id,
                    document_type=doc_type.strip(),
                    template_id=template_id.strip(),
                    version=int(template_schema_ver),
                    template_version=template_ver.strip(),
                    policy_rule_set_id=rule_set_ref.strip() or None,
                    config=cfg,
                    lifecycle_status=lifecycle,
                    is_active=active,
                )
                st.success(f"Template saved: {row.get('template_id')} v{row.get('version')}")
            except Exception as exc:
                st.error(str(exc))

        st.markdown("### A1 - Rule Management")
        try:
            rule_rows = service.list_tenant_rules(tenant_id, officer_id)
            if rule_rows:
                st.dataframe(pd.DataFrame(rule_rows), use_container_width=True, hide_index=True)
            else:
                st.info("No tenant rules found.")
        except Exception as exc:
            st.error(str(exc))

        with st.form("rule_upsert_form"):
            rule_doc_type = st.text_input("Rule doc type", value="AADHAAR_CARD")
            rule_name = st.text_input("Rule name", value="rule_aadhaar_default")
            rule_set_id = st.text_input("Rule set ID", value="RULESET_AADHAAR_DEFAULT")
            rule_version = st.number_input("Rule version", min_value=1, value=1, step=1)
            min_extract = st.slider("Min extract confidence", 0.0, 1.0, 0.6, 0.01)
            min_approval = st.slider("Min approval confidence", 0.0, 1.0, 0.72, 0.01)
            max_risk = st.slider("Max approval risk", 0.0, 1.0, 0.35, 0.01)
            reg_required = st.checkbox("Registry required", value=True)
            rule_active = st.checkbox("Active rule", value=True)
            rule_cfg_raw = st.text_area("Rule config JSON", value='{"checks":[]}', height=80)
            save_rule = st.form_submit_button("Save Rule", use_container_width=True)
        if save_rule:
            try:
                rule_cfg = json.loads(rule_cfg_raw) if rule_cfg_raw.strip() else {}
                row = service.save_tenant_rule(
                    tenant_id=tenant_id,
                    officer_id=officer_id,
                    document_type=rule_doc_type.strip(),
                    rule_name=rule_name.strip(),
                    version=int(rule_version),
                    rule_set_id=rule_set_id.strip(),
                    min_extract_confidence=float(min_extract),
                    min_approval_confidence=float(min_approval),
                    max_approval_risk=float(max_risk),
                    registry_required=bool(reg_required),
                    config=rule_cfg,
                    is_active=bool(rule_active),
                )
                st.success(f"Rule saved: {row.get('rule_name')} v{row.get('version')}")
            except Exception as exc:
                st.error(str(exc))

        st.markdown("### A2 - User and Role Management")
        try:
            officers = service.list_officers(tenant_id, officer_id)
            if officers:
                st.dataframe(pd.DataFrame(officers), use_container_width=True, hide_index=True)
            else:
                st.info("No officers configured.")
        except Exception as exc:
            st.error(str(exc))

        with st.form("officer_upsert_form"):
            target_id = st.text_input("Officer ID", value="officer-new-001")
            target_role = st.selectbox(
                "Officer role",
                [
                    ROLE_TENANT_OPERATOR,
                    ROLE_TENANT_OFFICER,
                    ROLE_TENANT_SENIOR_OFFICER,
                    ROLE_TENANT_ADMIN,
                    ROLE_TENANT_AUDITOR,
                ],
                index=0,
            )
            target_status = st.selectbox("Officer status", ["ACTIVE", "INACTIVE"], index=0)
            save_officer = st.form_submit_button("Save Officer Account", use_container_width=True)
        if save_officer:
            try:
                row = service.upsert_officer_account(
                    tenant_id=tenant_id,
                    admin_officer_id=officer_id,
                    target_officer_id=target_id.strip(),
                    role=target_role,
                    status=target_status,
                )
                st.success(f"Officer saved: {row.get('officer_id')} ({row.get('role')}, {row.get('status')})")
            except Exception as exc:
                st.error(str(exc))

        if st.button("Seed Part-5 Baseline", use_container_width=True):
            try:
                res = governance.seed_part5_baseline(tenant_id, officer_id)
                st.success(f"Seeded: {res}")
            except Exception as exc:
                st.error(str(exc))

        with st.form("gov_kpi_snapshot"):
            kpi_key = st.text_input("KPI key", value="tamper_detection_recall_pct")
            measured = st.number_input("Measured value", value=86.0, step=0.1)
            src = st.selectbox("Source", ["MANUAL", "AUDIT", "MONITORING"], index=0)
            notes = st.text_area("Notes", value="Monthly QA sample")
            save_kpi = st.form_submit_button("Save KPI Snapshot", use_container_width=True)
        if save_kpi:
            try:
                row = governance.record_kpi_snapshot(
                    tenant_id=tenant_id,
                    officer_id=officer_id,
                    kpi_key=kpi_key.strip(),
                    measured_value=float(measured),
                    source=src,
                    notes=notes.strip() or None,
                )
                st.success(f"Snapshot saved: {row['id']}")
            except Exception as exc:
                st.error(str(exc))

    if role in PLATFORM_ROLES:
        st.markdown("### Platform Auditor View")
        st.write({
            "rule": "Cross-tenant aggregates are only available with explicit platform grant.",
            "platform_access_grants": governance.list_platform_access(actor_id=officer_id),
        })
        if st.button("Load Cross-Tenant Overview", use_container_width=True):
            try:
                st.json(governance.cross_tenant_audit_overview(officer_id))
            except Exception as exc:
                st.error(str(exc))
    elif role in AUDIT_ROLES and role not in ADMIN_ROLES:
        st.markdown("### Tenant Auditor Read-Only Config View")
        st.write(
            {
                "templates": service.repo.list_tenant_templates(tenant_id),
                "rules": service.repo.list_tenant_rules(tenant_id),
                "officers": service.repo.list_officers(tenant_id),
            }
        )


def _render_ops_dr_monitor(*, role: str, tenant_id: str, officer_id: str) -> None:
    st.markdown("### SRE / Ops Fields")
    try:
        dashboard = service.monitoring_dashboard(tenant_id, officer_id)
        st.write(
            {
                "throughput": (dashboard.get("mlops") or {}).get("throughput_docs"),
                "latency": (dashboard.get("mlops") or {}).get("avg_latency_ms"),
                "failure_rates": "Check event summary below",
                "backlog": dashboard.get("review_workload"),
                "dr_status": service.dr_service.describe(),
                "webhook_failures": len(service.list_webhook_outbox(tenant_id, officer_id, status="PENDING")),
            }
        )
    except Exception as exc:
        st.error(str(exc))

    if role in PLATFORM_ROLES:
        st.markdown("### P1 - Platform Monitoring and Tenants")
        tenants = service.repo.list_platform_tenants()
        if tenants:
            st.dataframe(pd.DataFrame(tenants), use_container_width=True, hide_index=True)
        else:
            st.info("No tenant rows available.")
        try:
            overview = governance.cross_tenant_audit_overview(officer_id)
            st.write({"cross_tenant_overview": overview})
        except Exception as exc:
            st.error(str(exc))

    try:
        docs = service.list_documents(tenant_id, officer_id)
        events = service.list_tenant_events(tenant_id, officer_id)
        failed = [e for e in events if str(e.get("event_type")) == "document.failed"]
        failure_rate = round((len(failed) * 100.0 / max(1, len(docs))), 2)
        st.write(
            {
                "documents_total": len(docs),
                "failed_events": len(failed),
                "failure_rate_pct": failure_rate,
                "waiting_for_review": len([d for d in docs if str(d.get("state")) == "WAITING_FOR_REVIEW"]),
                "review_in_progress": len([d for d in docs if str(d.get("state")) == "REVIEW_IN_PROGRESS"]),
            }
        )
    except Exception as exc:
        st.error(str(exc))

    st.markdown("### ML Team Fields")
    try:
        corrections_pending = len(service.repo.list_correction_gate_records(tenant_id, status="PENDING_QA"))
        corrections_approved = len(service.repo.list_correction_gate_records(tenant_id, status="TRAINING_APPROVED"))
        metrics = service.repo.list_module_metrics(tenant_id=tenant_id, limit=1000)
        ok_metrics = len([m for m in metrics if str(m.get("status")) == "OK"])
        metric_health = round((ok_metrics * 100.0 / max(1, len(metrics))), 2)
        audit_logs = service.repo.list_model_audit_logs(tenant_id=tenant_id)
        ocr_conf = []
        for row in audit_logs:
            if str(row.get("module_name")) == "ocr_multi_script":
                try:
                    ocr_conf.append(float((row.get("output") or {}).get("ocr_confidence", 0.0)))
                except Exception:
                    pass
        drift_indicator = "STABLE"
        if ocr_conf:
            latest = sum(ocr_conf[: min(20, len(ocr_conf))]) / min(20, len(ocr_conf))
            baseline = sum(ocr_conf) / len(ocr_conf)
            if latest < (baseline - 0.1):
                drift_indicator = "OCR_CONFIDENCE_DOWNWARD_DRIFT"

        training_flags = []
        for d in docs[:200]:
            flags = (((d.get("metadata") or {}).get("ml_training_flags")) or {}).get("eligible_for_training", {})
            training_flags.append(flags if isinstance(flags, dict) else {})
        eligible_counts = {
            "ocr": len([f for f in training_flags if bool(f.get("ocr", False))]),
            "classification": len([f for f in training_flags if bool(f.get("classification", False))]),
            "extraction": len([f for f in training_flags if bool(f.get("extraction", False))]),
            "fraud": len([f for f in training_flags if bool(f.get("fraud", False))]),
        }

        st.write(
            {
                "correction_gate_status": {
                    "pending_qa": corrections_pending,
                    "training_approved": corrections_approved,
                },
                "drift_indicators": drift_indicator,
                "module_accuracy_trend_proxy_ok_pct": metric_health,
                "training_eligibility_flags": eligible_counts,
            }
        )
    except Exception as exc:
        st.error(str(exc))

    if role in ADMIN_ROLES or role in REVIEW_ROLES:
        c1, c2 = st.columns(2)
        with c1:
            if st.button("Enforce Review SLA", use_container_width=True):
                try:
                    escalations = service.enforce_review_sla(tenant_id, officer_id)
                    st.success(f"Escalations generated: {len(escalations)}")
                except Exception as exc:
                    st.error(str(exc))
        with c2:
            if st.button("Apply Retention Lifecycle", use_container_width=True):
                try:
                    res = service.apply_retention_lifecycle(tenant_id, officer_id)
                    st.success(f"Lifecycle result: {res}")
                except Exception as exc:
                    st.error(str(exc))


def _render_integrations(*, role: str, tenant_id: str, officer_id: str) -> None:
    st.markdown("### API & Webhook Integration Surface")
    endpoints = [
        {"method": "POST", "path": "/documents", "purpose": "ingest"},
        {"method": "POST", "path": "/documents/{document_id}/process", "purpose": "run pipeline"},
        {"method": "GET", "path": "/documents/{document_id}/status", "purpose": "poll status"},
        {"method": "GET", "path": "/documents/{document_id}/result", "purpose": "final result"},
        {"method": "GET", "path": "/documents/{document_id}/events", "purpose": "audit timeline"},
        {"method": "POST", "path": "/tenants/{tenant_id}/offline/sync", "purpose": "offline sync"},
    ]
    st.dataframe(pd.DataFrame(endpoints), use_container_width=True, hide_index=True)

    st.markdown("### Main Inputs and Outputs")
    st.write(
        {
            "inputs": [
                "Document files (image/PDF/text)",
                "tenant_id, citizen_id, application metadata",
                "Officer decisions and corrections",
                "Template/rule/policy configuration",
            ],
            "outputs": [
                "document_record JSON (versioned)",
                "Approval/Reject/Review state transitions",
                "Field extraction + validation statuses",
                "Fraud/authenticity/issuer signals",
                "Notifications, webhooks, CSV export",
            ],
        }
    )

    if role in ADMIN_ROLES:
        st.markdown("### Create Tenant API Key")
        with st.form("create_api_key_form"):
            label = st.text_input("Key label", value="backend-service")
            raw_key = st.text_input("Raw key", value="change-me-very-long-secret", type="password")
            create_key = st.form_submit_button("Create API Key", use_container_width=True)
        if create_key:
            try:
                row = service.create_tenant_api_key(tenant_id, officer_id, label.strip(), raw_key.strip())
                st.success(f"API key created for label={row.get('key_label')}")
            except Exception as exc:
                st.error(str(exc))

    st.markdown("### Webhook Outbox")
    try:
        outbox = service.list_webhook_outbox(tenant_id, officer_id, status="PENDING")
        if outbox:
            st.dataframe(pd.DataFrame(outbox), use_container_width=True, hide_index=True)
        else:
            st.info("No pending webhooks.")
    except Exception as exc:
        st.error(str(exc))

    if role in REVIEW_ROLES or role in ADMIN_ROLES:
        st.markdown("### Batch Export")
        include_raw = st.checkbox("Include raw text", value=False)
        if st.button("Generate CSV Export", use_container_width=True):
            try:
                csv_data = service.batch_export_documents(tenant_id, officer_id, include_raw_text=include_raw)
                if not csv_data:
                    st.info("No rows to export.")
                else:
                    st.download_button(
                        "Download Export",
                        data=csv_data,
                        file_name=f"{tenant_id}_documents_export.csv",
                        mime="text/csv",
                        use_container_width=True,
                    )
            except Exception as exc:
                st.error(str(exc))


def _render_offline_sync_console(*, role: str, tenant_id: str, officer_id: str) -> None:
    _render_journey(
        "User Journey - Service Center Offline",
        [
            "capture provisional",
            "sync",
            "central reprocess",
            "conflict message if changed",
        ],
    )

    can_write = role in WRITE_ROLES

    if can_write:
        st.markdown("### Create Offline Provisional Record")
        with st.form("offline_provisional_form"):
            citizen = st.text_input("Citizen ID", value="citizen-offline-001")
            file_name = st.text_input("File name", value="offline_doc.txt")
            raw_text = st.text_area("Captured text", value="Offline captured document text", height=100)
            provisional_decision = st.selectbox("Local provisional decision", ["VALID", "REVIEW", "REJECT"], index=1)
            model_versions_raw = st.text_area(
                "Local model versions JSON",
                value='{"ocr_model_id":"ocr-lite-v1","classifier_model_id":"doc-classifier-lite-v1"}',
                height=70,
            )
            submit = st.form_submit_button("Create Provisional", use_container_width=True)
        if submit:
            try:
                versions = json.loads(model_versions_raw) if model_versions_raw.strip() else {}
                out = offline_service.create_offline_provisional(
                    tenant_id=tenant_id,
                    citizen_id=citizen.strip(),
                    file_name=file_name.strip(),
                    raw_text=raw_text,
                    officer_id=officer_id,
                    local_model_versions=versions,
                    provisional_decision=provisional_decision,
                    metadata={"source": "SERVICE_CENTER", "offline_node_id": "center-node-01"},
                )
                st.success(f"Offline provisional created: {out.get('id')}")
            except Exception as exc:
                st.error(str(exc))

    st.markdown("### Pending Offline Queue")
    pending = service.repo.list_pending_offline_documents(tenant_id, limit=500)
    if pending:
        st.dataframe(pd.DataFrame(pending), use_container_width=True, hide_index=True)
    else:
        st.info("No pending offline documents.")

    if can_write:
        capacity = st.number_input("Sync capacity per run", min_value=1, max_value=500, value=50, step=1)
        if st.button("Run Offline Sync", use_container_width=True):
            pending_ids = [str(row.get("id")) for row in pending if row.get("id")]
            try:
                backpressure = offline_service.apply_sync_backpressure(
                    tenant_id=tenant_id,
                    officer_id=officer_id,
                    pending_document_ids=pending_ids,
                    sync_capacity_per_minute=int(capacity),
                )
                synced = 0
                failed: list[dict[str, Any]] = []
                for doc_id in pending_ids[: int(capacity)]:
                    try:
                        offline_service.sync_offline_document(tenant_id=tenant_id, document_id=doc_id, officer_id=officer_id)
                        synced += 1
                    except Exception as exc:
                        failed.append({"document_id": doc_id, "error": str(exc)})
                st.success(
                    f"Sync run complete. pending={len(pending_ids)} synced={synced} failed={len(failed)} queue_overflow={backpressure.get('queue_overflow')}"
                )
                if failed:
                    st.dataframe(pd.DataFrame(failed), use_container_width=True, hide_index=True)
            except Exception as exc:
                st.error(str(exc))


def main() -> None:
    st.title("Government Document Intelligence")
    st.caption("Role-aware journeys, tenant-scoped controls, explainable verification")
    _render_env_status()

    with st.sidebar:
        st.header("Access Context")
        tenant_id = st.text_input("Tenant ID", value="dept-education").strip()
        role = st.selectbox("Role", ALL_ROLES, index=0)
        officer_id = st.text_input("Actor / Officer ID", value="officer-001").strip()

        if st.button("Register / Bind Officer", use_container_width=True):
            try:
                row = service.register_officer(officer_id, tenant_id, role)
                st.success(f"Bound {row['officer_id']} -> {row['tenant_id']} ({row['role']})")
            except Exception as exc:
                st.error(str(exc))

        policy = service.repo.get_tenant_policy(tenant_id)
        st.markdown("### Tenant Policy")
        st.write(
            {
                "review_sla_days": policy.get("review_sla_days"),
                "retention_days": policy.get("data_retention_days"),
                "cross_tenant_fraud": policy.get("cross_tenant_fraud_enabled"),
                "export_enabled": policy.get("export_enabled"),
                "residency_region": policy.get("residency_region"),
            }
        )

        accessible_pages = [p for p in PAGES if role in PAGE_ACCESS[p]]
        page = st.radio("App Sections", accessible_pages, index=0)

        docs, docs_error = _load_docs(
            tenant_id=tenant_id,
            officer_id=officer_id,
            role=role,
        )
        if docs_error:
            st.error(docs_error)

        doc_ids = [str(d.get("id")) for d in docs if d.get("id")]
        selected_doc_id = st.selectbox("Selected Document", [""] + doc_ids, index=0)

    selected_doc = next((d for d in docs if str(d.get("id")) == selected_doc_id), None) if selected_doc_id else None
    latest_record_row = service.repo.get_latest_document_record(tenant_id, selected_doc_id) if selected_doc_id else None
    selected_record = _unwrap_record(latest_record_row)

    _render_document_header(
        role=role,
        tenant_id=tenant_id,
        policy=policy,
        selected_doc=selected_doc,
        selected_record=selected_record,
    )
    _render_stakeholder_snapshot(
        role=role,
        tenant_id=tenant_id,
        officer_id=officer_id,
        selected_doc=selected_doc,
        selected_record=selected_record,
    )

    st.divider()

    if page == "Intake & Processing":
        _render_intake_processing(role=role, tenant_id=tenant_id, officer_id=officer_id, docs=docs, selected_doc=selected_doc)
    elif page == "Review Workbench":
        _render_review_workbench(
            role=role,
            tenant_id=tenant_id,
            officer_id=officer_id,
            selected_doc=selected_doc,
            selected_record=selected_record,
        )
    elif page == "Dispute Desk":
        _render_dispute_desk(role=role, tenant_id=tenant_id, officer_id=officer_id, selected_doc=selected_doc)
    elif page == "Fraud & Authenticity":
        _render_fraud_authenticity(selected_doc=selected_doc, selected_record=selected_record)
    elif page == "Citizen Communication":
        _render_citizen_communication(role=role, tenant_id=tenant_id, officer_id=officer_id, selected_doc=selected_doc)
    elif page == "Audit Trail & Explainability":
        _render_audit_explainability(
            tenant_id=tenant_id,
            officer_id=officer_id,
            selected_doc=selected_doc,
            selected_record=selected_record,
        )
    elif page == "Governance & KPI":
        _render_governance_kpi(role=role, tenant_id=tenant_id, officer_id=officer_id)
    elif page == "Ops & DR Monitor":
        _render_ops_dr_monitor(role=role, tenant_id=tenant_id, officer_id=officer_id)
    elif page == "Integrations (API/Webhook/Export)":
        _render_integrations(role=role, tenant_id=tenant_id, officer_id=officer_id)
    elif page == "Offline Sync Console":
        _render_offline_sync_console(role=role, tenant_id=tenant_id, officer_id=officer_id)

    st.divider()
    st.caption(
        "Non-negotiable UI controls enforced: tenant-scoped data views, role-gated actions, sensitive field masking, and auditable state-changing actions."
    )


if __name__ == "__main__":
    main()
