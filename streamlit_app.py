from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from app.config import settings
from app.infra.repositories import (
    ROLE_AUDITOR,
    ROLE_PLATFORM_ADMIN,
    ROLE_SENIOR_VERIFIER,
    ROLE_VERIFIER,
)
from app.services.auth_service import AuthService
from app.services.document_service import DocumentService

st.set_page_config(page_title="GovDocIQ", page_icon="🏛️", layout="wide")

st.markdown(
    """
    <style>
    .stApp { background: #f4f7fb; }
    section[data-testid="stSidebar"] {
        background: #ffffff;
        border-right: 1px solid #e6edf7;
    }
    section[data-testid="stSidebar"] * { color: #132341 !important; }
    .kpi {
        background:#ffffff;
        border:1px solid #e1e8f3;
        border-radius:10px;
        padding:0.8rem 1rem;
    }
    .kpi .v { font-size:1.5rem; font-weight:700; color:#0f4dbd; }
    .kpi .l { font-size:0.75rem; color:#4c6186; text-transform:uppercase; }
    .card {
        background:#ffffff;
        border:1px solid #e1e8f3;
        border-radius:10px;
        padding:0.8rem 1rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

ALL_ROLES = [ROLE_VERIFIER, ROLE_SENIOR_VERIFIER, ROLE_AUDITOR, ROLE_PLATFORM_ADMIN]
ROLE_META = {
    ROLE_VERIFIER: {"icon": "🧑‍💼", "label": "Verifier", "color": "#4fc3f7"},
    ROLE_SENIOR_VERIFIER: {"icon": "🎖️", "label": "Senior Verifier", "color": "#ffb74d"},
    ROLE_AUDITOR: {"icon": "🔎", "label": "Auditor", "color": "#80deea"},
    ROLE_PLATFORM_ADMIN: {"icon": "👑", "label": "Platform Admin", "color": "#ffcc02"},
}

PAGES = ["🏠 Unified Workspace"]

SCRIPT_OPTIONS = [
    "AUTO-DETECT",
    "Devanagari (Hindi/Marathi/Sanskrit)",
    "Bengali",
    "Tamil",
    "Telugu",
    "Kannada",
    "Malayalam",
    "Gujarati",
    "Gurmukhi (Punjabi)",
    "Odia",
    "Urdu (Nastaliq)",
    "Latin (English)",
]

DOC_TYPE_HINTS = ["AUTO-DETECT", "AADHAAR_CARD", "PAN_CARD", "INCOME_CERTIFICATE"]

FORM_SCHEMAS: dict[str, list[dict[str, Any]]] = {
    "AADHAAR_CARD": [
        {"field_id": "name", "label": "Name", "type": "text", "mandatory": True, "aliases": ["name", "applicant_name", "full_name"]},
        {"field_id": "dob", "label": "Date of Birth", "type": "date", "mandatory": True, "aliases": ["dob", "date_of_birth", "birth_date"]},
        {"field_id": "gender", "label": "Gender", "type": "text", "mandatory": False, "aliases": ["gender", "sex"]},
        {"field_id": "aadhaar_number", "label": "Aadhaar Number", "type": "id", "mandatory": True, "regex": r"^\d{12}$", "aliases": ["aadhaar_number", "aadhaar", "uid"]},
        {"field_id": "address", "label": "Address", "type": "text", "mandatory": False, "aliases": ["address", "residential_address"]},
    ],
    "PAN_CARD": [
        {"field_id": "name", "label": "Name", "type": "text", "mandatory": True, "aliases": ["name", "full_name"]},
        {"field_id": "father_name", "label": "Father Name", "type": "text", "mandatory": False, "aliases": ["father_name", "father", "s_o"]},
        {"field_id": "dob", "label": "Date of Birth", "type": "date", "mandatory": True, "aliases": ["dob", "date_of_birth"]},
        {"field_id": "pan_number", "label": "PAN Number", "type": "id", "mandatory": True, "regex": r"^[A-Z]{5}\d{4}[A-Z]$", "aliases": ["pan_number", "pan"]},
    ],
    "INCOME_CERTIFICATE": [
        {"field_id": "name", "label": "Applicant Name", "type": "text", "mandatory": True, "aliases": ["name", "applicant_name"]},
        {"field_id": "certificate_number", "label": "Certificate Number", "type": "id", "mandatory": True, "aliases": ["certificate_number", "cert_no", "reference_number"]},
        {"field_id": "annual_income", "label": "Annual Income", "type": "number", "mandatory": True, "min": 0, "aliases": ["annual_income", "income"]},
        {"field_id": "issuing_authority", "label": "Issuing Authority", "type": "text", "mandatory": True, "aliases": ["issuing_authority", "authority"]},
        {"field_id": "issue_date", "label": "Issue Date", "type": "date", "mandatory": False, "aliases": ["issue_date", "date_of_issue"]},
    ],
    "CASTE_CERTIFICATE": [
        {"field_id": "name", "label": "Applicant Name", "type": "text", "mandatory": True, "aliases": ["name", "applicant_name"]},
        {"field_id": "caste", "label": "Caste", "type": "text", "mandatory": True, "aliases": ["caste", "community"]},
        {"field_id": "certificate_number", "label": "Certificate Number", "type": "id", "mandatory": True, "aliases": ["certificate_number", "cert_no"]},
        {"field_id": "issuing_authority", "label": "Issuing Authority", "type": "text", "mandatory": True, "aliases": ["issuing_authority"]},
    ],
    "DOMICILE_CERTIFICATE": [
        {"field_id": "name", "label": "Applicant Name", "type": "text", "mandatory": True, "aliases": ["name", "applicant_name"]},
        {"field_id": "address", "label": "Address", "type": "text", "mandatory": True, "aliases": ["address"]},
        {"field_id": "certificate_number", "label": "Certificate Number", "type": "id", "mandatory": True, "aliases": ["certificate_number", "cert_no"]},
    ],
    "LAND_RECORD": [
        {"field_id": "owner_name", "label": "Owner Name", "type": "text", "mandatory": True, "aliases": ["owner_name", "name"]},
        {"field_id": "survey_number", "label": "Survey Number", "type": "text", "mandatory": True, "aliases": ["survey_number", "plot_number"]},
        {"field_id": "village", "label": "Village", "type": "text", "mandatory": True, "aliases": ["village"]},
    ],
    "BIRTH_CERTIFICATE": [
        {"field_id": "name", "label": "Name", "type": "text", "mandatory": True, "aliases": ["name"]},
        {"field_id": "dob", "label": "Date of Birth", "type": "date", "mandatory": True, "aliases": ["dob", "date_of_birth"]},
        {"field_id": "registration_number", "label": "Registration Number", "type": "id", "mandatory": True, "aliases": ["registration_number", "reg_no"]},
    ],
    "DEATH_CERTIFICATE": [
        {"field_id": "name", "label": "Name", "type": "text", "mandatory": True, "aliases": ["name"]},
        {"field_id": "date_of_death", "label": "Date of Death", "type": "date", "mandatory": True, "aliases": ["date_of_death", "dod"]},
        {"field_id": "registration_number", "label": "Registration Number", "type": "id", "mandatory": True, "aliases": ["registration_number", "reg_no"]},
    ],
    "RATION_CARD": [
        {"field_id": "head_name", "label": "Head of Family", "type": "text", "mandatory": True, "aliases": ["head_name", "name"]},
        {"field_id": "ration_card_number", "label": "Ration Card Number", "type": "id", "mandatory": True, "aliases": ["ration_card_number", "card_number"]},
        {"field_id": "address", "label": "Address", "type": "text", "mandatory": False, "aliases": ["address"]},
    ],
    "MARRIAGE_CERTIFICATE": [
        {"field_id": "spouse_1_name", "label": "Spouse 1 Name", "type": "text", "mandatory": True, "aliases": ["spouse_1_name", "groom_name"]},
        {"field_id": "spouse_2_name", "label": "Spouse 2 Name", "type": "text", "mandatory": True, "aliases": ["spouse_2_name", "bride_name"]},
        {"field_id": "marriage_date", "label": "Marriage Date", "type": "date", "mandatory": True, "aliases": ["marriage_date"]},
    ],
    "BONAFIDE_CERTIFICATE": [
        {"field_id": "student_name", "label": "Student Name", "type": "text", "mandatory": True, "aliases": ["student_name", "name"]},
        {"field_id": "institution", "label": "Institution", "type": "text", "mandatory": True, "aliases": ["institution", "school_name"]},
        {"field_id": "certificate_number", "label": "Certificate Number", "type": "id", "mandatory": False, "aliases": ["certificate_number", "cert_no"]},
    ],
    "DISABILITY_CERTIFICATE": [
        {"field_id": "name", "label": "Applicant Name", "type": "text", "mandatory": True, "aliases": ["name"]},
        {"field_id": "disability_type", "label": "Disability Type", "type": "text", "mandatory": True, "aliases": ["disability_type"]},
        {"field_id": "disability_percent", "label": "Disability %", "type": "number", "mandatory": True, "min": 0, "max": 100, "aliases": ["disability_percent"]},
    ],
    "BANK_PASSBOOK": [
        {"field_id": "account_holder_name", "label": "Account Holder Name", "type": "text", "mandatory": True, "aliases": ["account_holder_name", "name"]},
        {"field_id": "account_number", "label": "Account Number", "type": "id", "mandatory": True, "aliases": ["account_number", "a_c_no"]},
        {"field_id": "ifsc_code", "label": "IFSC Code", "type": "id", "mandatory": False, "regex": r"^[A-Z]{4}0[A-Z0-9]{6}$", "aliases": ["ifsc_code", "ifsc"]},
        {"field_id": "bank_name", "label": "Bank Name", "type": "text", "mandatory": False, "aliases": ["bank_name"]},
    ],
    "OTHER": [
        {"field_id": "name", "label": "Name", "type": "text", "mandatory": False, "aliases": ["name"]},
        {"field_id": "reference_number", "label": "Reference Number", "type": "id", "mandatory": False, "aliases": ["reference_number", "id_number"]},
        {"field_id": "dob", "label": "Date", "type": "date", "mandatory": False, "aliases": ["dob", "date"]},
    ],
}


def _norm_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def _confidence_band(conf: float) -> str:
    if conf >= 0.85:
        return "HIGH"
    if conf >= 0.6:
        return "MEDIUM"
    if conf > 0:
        return "LOW"
    return "MISSING"


def _validate_form_value(field_schema: dict[str, Any], value: str) -> str:
    val = (value or "").strip()
    if not val:
        return "MISSING" if bool(field_schema.get("mandatory")) else "EMPTY"
    if val.upper() == "NOT_PRESENT":
        return "FLAGGED_NOT_PRESENT"

    pattern = str(field_schema.get("regex") or "").strip()
    if pattern and not re.fullmatch(pattern, val):
        return "FAIL_FORMAT"

    field_type = str(field_schema.get("type") or "text")
    if field_type == "date":
        parsed = False
        for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"):
            try:
                datetime.strptime(val, fmt)
                parsed = True
                break
            except Exception:
                continue
        if not parsed:
            return "FAIL_DATE"
    if field_type == "number":
        try:
            num = float(val.replace(",", ""))
        except Exception:
            return "FAIL_NUMBER"
        if "min" in field_schema and num < float(field_schema["min"]):
            return "FAIL_MIN"
        if "max" in field_schema and num > float(field_schema["max"]):
            return "FAIL_MAX"
    return "PASS"


def _build_form_population_rows(selected_doc: dict[str, Any], document_type: str) -> list[dict[str, Any]]:
    schema = FORM_SCHEMAS.get(document_type, FORM_SCHEMAS["OTHER"])
    extracted = (selected_doc.get("extraction_output") or {}).get("fields") or []
    ext_map: dict[str, dict[str, Any]] = {}
    for item in extracted:
        if not isinstance(item, dict):
            continue
        k = _norm_key(item.get("field_name"))
        if k:
            ext_map[k] = item

    previous_rows = (
        (((selected_doc.get("metadata") or {}).get("form_population") or {}).get("rows"))
        or []
    )
    prev_map = {
        _norm_key(r.get("field_id")): r
        for r in previous_rows
        if isinstance(r, dict) and str(r.get("field_id") or "").strip()
    }

    rows: list[dict[str, Any]] = []
    for field in schema:
        field_id = str(field["field_id"])
        aliases = [_norm_key(field_id)] + [_norm_key(a) for a in (field.get("aliases") or [])]

        matched: dict[str, Any] | None = None
        for alias in aliases:
            if alias in ext_map:
                matched = ext_map[alias]
                break

        ocr_value = str((matched or {}).get("normalized_value") or "")
        confidence = float((matched or {}).get("confidence") or 0.0)

        prev = prev_map.get(_norm_key(field_id), {})
        value = str(prev.get("value") or ocr_value or "")
        locked = bool(prev.get("locked", False))

        if value and value != ocr_value:
            source = "Operator Entered"
        elif value:
            source = "OCR Auto-filled"
        else:
            source = "Missing"
        if value.upper() == "NOT_PRESENT":
            source = "Operator Marked Not Present"

        validation_state = _validate_form_value(field, value)
        rows.append(
            {
                "field_id": field_id,
                "label": field.get("label", field_id),
                "value": value,
                "ocr_value": ocr_value,
                "confidence": round(confidence, 3),
                "confidence_badge": _confidence_band(confidence),
                "source": source,
                "validation_state": validation_state,
                "mandatory": bool(field.get("mandatory", False)),
                "locked": locked,
            }
        )
    return rows


@st.cache_resource
def get_service() -> DocumentService:
    return DocumentService()


@st.cache_resource
def get_auth_service() -> AuthService:
    return AuthService()


def _kpi(label: str, value: Any) -> str:
    return f'<div class="kpi"><div class="v">{value}</div><div class="l">{label}</div></div>'


def _doc_summary_row(doc: dict[str, Any]) -> dict[str, Any]:
    cls = doc.get("classification_output") or {}
    return {
        "id": doc.get("id"),
        "citizen_id": doc.get("citizen_id"),
        "file_name": doc.get("file_name"),
        "doc_type": cls.get("doc_type"),
        "state": doc.get("state"),
        "decision": doc.get("decision") or "PENDING",
        "confidence": doc.get("confidence"),
        "risk_score": doc.get("risk_score"),
        "updated_at": doc.get("updated_at"),
    }


def _init_session() -> None:
    st.session_state.setdefault("auth_user", None)
    st.session_state.setdefault("active_profile", ROLE_VERIFIER)


def _continue_local_mode(name: str, email: str = "") -> None:
    safe_name = name.strip() or "Local User"
    safe_email = email.strip() or "local@offline"
    st.session_state["auth_user"] = {
        "user_id": f"local-{safe_name.lower().replace(' ', '-')}",
        "email": safe_email,
        "name": safe_name,
        "role": ROLE_VERIFIER,
        "auth_mode": "local",
    }
    st.session_state["active_profile"] = ROLE_VERIFIER
    st.rerun()


def _render_auth_page(auth_service: AuthService) -> None:
    st.title("GovDocIQ Access")
    st.caption("Sign in to access your workspace.")
    st.caption(f"Auth provider: {auth_service.provider}")

    if not auth_service.configured():
        st.warning(f"{auth_service.provider.capitalize()} authentication is unavailable. Continue in local mode.")
        if auth_service.provider == "appwrite":
            st.caption(
                "Detected config: "
                f"endpoint_set={bool(settings.appwrite_endpoint.strip())}, "
                f"project_id_set={bool(settings.appwrite_project_id.strip())}"
            )
        local_name = st.text_input("Name", key="local_name")
        local_email = st.text_input("Email (optional)", key="local_email")
        if st.button("Continue in Local Mode", use_container_width=True, key="local_continue_btn"):
            if not local_name.strip():
                st.error("Name is required.")
            else:
                _continue_local_mode(local_name, local_email)
        st.caption("Local mode uses in-memory storage and does not require Supabase Auth.")
        return

    t1, t2 = st.tabs(["Sign In", "Sign Up"])

    with t1:
        email = st.text_input("Email", key="signin_email")
        password = st.text_input("Password", type="password", key="signin_password")

        if st.button("Sign In", use_container_width=True, key="signin_btn"):
            out = auth_service.sign_in(email=email.strip(), password=password)
            if out.ok and out.data:
                st.session_state["auth_user"] = out.data
                default_role = str(out.data.get("role") or ROLE_VERIFIER)
                st.session_state["active_profile"] = default_role if default_role in ALL_ROLES else ROLE_VERIFIER
                st.rerun()
            else:
                st.error(out.message)

        st.markdown("---")
        recovery_action = st.selectbox(
            "Recovery",
            ["None", "Forgot password", "Forgot username"],
            index=0,
            key="recovery_action",
        )
        recovery_email = st.text_input("Recovery email", key="recovery_email")

        if recovery_action == "Forgot password":
            if st.button("Send password reset", use_container_width=True, key="send_pw_reset"):
                out = auth_service.send_password_reset(email=recovery_email.strip() or email.strip())
                if out.ok:
                    st.success(out.message)
                else:
                    st.error(out.message)

        if recovery_action == "Forgot username":
            if st.button("Send username reminder", use_container_width=True, key="send_user_rem"):
                out = auth_service.send_username_reminder(email=recovery_email.strip() or email.strip())
                if out.ok:
                    st.success(out.message)
                else:
                    st.error(out.message)

    with t2:
        su_name = st.text_input("Name", key="signup_name")
        su_email = st.text_input("Email", key="signup_email")
        su_password = st.text_input("Password", type="password", key="signup_password")
        su_confirm = st.text_input("Confirm Password", type="password", key="signup_confirm")
        su_role = st.selectbox("Role", ALL_ROLES, index=0, key="signup_role")

        if st.button("Sign Up", use_container_width=True, key="signup_btn"):
            if not su_name.strip():
                st.error("Name is required.")
            elif su_password != su_confirm:
                st.error("Password and Confirm Password do not match.")
            else:
                out = auth_service.sign_up(
                    name=su_name.strip(),
                    email=su_email.strip(),
                    password=su_password,
                    role=su_role,
                )
                if out.ok:
                    st.success(out.message)
                else:
                    st.error(out.message)


def _render_dashboard(service: DocumentService, role: str) -> None:
    docs = service.list_documents(limit=1000)
    waiting = [d for d in docs if str(d.get("state")) in {"WAITING_FOR_REVIEW", "REVIEW_IN_PROGRESS"}]
    approved = [d for d in docs if str(d.get("decision")) == "APPROVE"]
    rejected = [d for d in docs if str(d.get("decision")) == "REJECT"]

    c1, c2, c3, c4 = st.columns(4)
    c1.markdown(_kpi("Total Documents", len(docs)), unsafe_allow_html=True)
    c2.markdown(_kpi("Review Queue", len(waiting)), unsafe_allow_html=True)
    c3.markdown(_kpi("Approved", len(approved)), unsafe_allow_html=True)
    c4.markdown(_kpi("Rejected", len(rejected)), unsafe_allow_html=True)


def _render_ingestion(service: DocumentService, actor_id: str, role: str) -> None:
    st.markdown("### 1) Document Setup & Upload")

    uploaded = st.file_uploader("Upload document", type=["pdf", "jpg", "jpeg", "png", "txt", "csv", "json"])
    c1, c2 = st.columns(2)
    with c1:
        script_hint = st.selectbox("Script hint", SCRIPT_OPTIONS, index=0)
    with c2:
        doc_type_hint = st.selectbox("Document type hint", DOC_TYPE_HINTS, index=0)

    if uploaded:
        suffix = Path(uploaded.name).suffix.lower()
        if suffix in {".jpg", ".jpeg", ".png"}:
            st.image(uploaded, caption=uploaded.name, use_container_width=True)
        elif suffix in {".txt", ".csv", ".json"}:
            st.code(uploaded.getvalue().decode("utf-8", errors="ignore")[:2000])

    if st.button("Process Document", use_container_width=True, disabled=uploaded is None):
        citizen_id = "citizen-001"
        notes = None
        if not uploaded:
            st.error("Upload a file first.")
        else:
            try:
                created = service.create_document(
                    citizen_id=citizen_id,
                    file_name=uploaded.name,
                    file_bytes=uploaded.getvalue(),
                    actor_id=actor_id,
                    role=role,
                    source="ONLINE_PORTAL",
                    script_hint=script_hint,
                    doc_type_hint=doc_type_hint,
                    notes=notes,
                )
                processed = service.process_document(str(created["id"]), actor_id=actor_id, role=role)
                st.session_state["last_processed_doc"] = processed
                st.session_state["review_doc_target_id"] = str(processed.get("id") or "")
                st.success(
                    f"Processed {processed['id']} | state={processed.get('state')} | "
                    f"doc_type={(processed.get('classification_output') or {}).get('doc_type')}"
                )
                ocr_engine = str(processed.get("ocr_engine") or "")
                if ocr_engine.startswith("paddle-unavailable:"):
                    st.error(
                        "OCR engine is unavailable for this runtime/file. "
                        "Enable PaddleOCR, or ensure Tesseract + PDF raster support are installed."
                    )
            except Exception as exc:
                st.error(str(exc))

    last_processed = st.session_state.get("last_processed_doc")
    if isinstance(last_processed, dict):
        st.markdown("### 2) OCR Output")
        ocr_text = str(last_processed.get("ocr_text") or "").strip()
        if ocr_text:
            st.text_area("OCR Text", value=ocr_text, height=220, disabled=True)
        else:
            st.warning("OCR returned empty text for this file. Try a clearer scan/image or re-run processing.")


def _render_structured_fields(service: DocumentService, actor_id: str, role: str) -> None:
    st.markdown("### 3) Document Classification")
    docs = service.list_documents(limit=500)
    if not docs:
        st.info("No processed documents yet. Upload and process a document first.")
        return

    by_id = {str(d.get("id")): d for d in docs if d.get("id")}
    labels = {_build_doc_label(d): d for d in docs}
    label_list = list(labels.keys())

    target_id = str(st.session_state.get("review_doc_target_id") or "")
    if not target_id:
        last_processed = st.session_state.get("last_processed_doc")
        if isinstance(last_processed, dict):
            target_id = str(last_processed.get("id") or "")

    lock_latest = st.checkbox(
        "Lock to latest processed document",
        value=True,
        key="workspace_lock_latest_doc",
    )

    selected_doc: dict[str, Any]
    if lock_latest and target_id and target_id in by_id:
        selected_doc = by_id[target_id]
        st.caption(f"Locked to: `{target_id}`")
    else:
        default_idx = 0
        if target_id:
            for idx, lb in enumerate(label_list):
                if lb.startswith(f"{target_id} |"):
                    default_idx = idx
                    break
        selected_label = st.selectbox(
            "Selected Document",
            options=label_list,
            index=default_idx,
            key="workspace_doc_select",
        )
        selected_doc = labels[selected_label]

    doc_id = str(selected_doc.get("id"))
    st.session_state["review_doc_target_id"] = doc_id

    cls = selected_doc.get("classification_output") or {}
    detected_doc_type = str(cls.get("doc_type") or "OTHER").upper()
    if detected_doc_type not in FORM_SCHEMAS:
        detected_doc_type = "OTHER"

    schema_types = sorted(FORM_SCHEMAS.keys())
    selected_doc_type = st.selectbox(
        "Detected document type (override if incorrect)",
        options=schema_types,
        index=schema_types.index(detected_doc_type) if detected_doc_type in schema_types else schema_types.index("OTHER"),
        key=f"workspace_doc_type_{doc_id}",
    )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Detected Type", detected_doc_type)
    c2.metric("Selected Type", selected_doc_type)
    c3.metric("Classification Conf.", f"{float(cls.get('confidence') or 0.0):.2f}")
    c4.metric("Pipeline Confidence", f"{float(selected_doc.get('confidence') or 0.0):.2f}")

    c5, c6 = st.columns(2)
    c5.metric("State", selected_doc.get("state", "UNKNOWN"))
    c6.metric("Decision", selected_doc.get("decision") or "PENDING")

    if cls:
        with st.expander("Classification details", expanded=False):
            st.json(cls)

    st.markdown("### 4) OCR Data -> Form Population Engine")
    rows = _build_form_population_rows(selected_doc, selected_doc_type)
    population_df = pd.DataFrame(rows)

    edited = st.data_editor(
        population_df,
        use_container_width=True,
        num_rows="fixed",
        key=f"workspace_form_population_{doc_id}",
        disabled=[
            "field_id",
            "label",
            "ocr_value",
            "confidence",
            "confidence_badge",
            "source",
            "validation_state",
            "mandatory",
        ],
    )

    schema_by_id = {str(f["field_id"]): f for f in FORM_SCHEMAS.get(selected_doc_type, FORM_SCHEMAS["OTHER"])}
    resolved_rows: list[dict[str, Any]] = []
    for row in edited.to_dict(orient="records"):
        field_id = str(row.get("field_id") or "")
        schema_field = schema_by_id.get(field_id, {"mandatory": False, "type": "text"})
        value = str(row.get("value") or "")
        ocr_value = str(row.get("ocr_value") or "")
        confidence = float(row.get("confidence") or 0.0)
        source = "OCR Auto-filled" if value and value == ocr_value else "Operator Entered" if value else "Missing"
        if value.strip().upper() == "NOT_PRESENT":
            source = "Operator Marked Not Present"
        validation_state = _validate_form_value(schema_field, value)
        resolved_rows.append(
            {
                "field_id": field_id,
                "label": str(row.get("label") or field_id),
                "value": value,
                "ocr_value": ocr_value,
                "confidence": round(confidence, 3),
                "confidence_badge": _confidence_band(confidence),
                "source": source,
                "validation_state": validation_state,
                "mandatory": bool(row.get("mandatory", False)),
                "locked": bool(row.get("locked", False)),
            }
        )

    preview_df = pd.DataFrame(resolved_rows)
    st.markdown("#### Population Preview")
    st.dataframe(
        preview_df[["field_id", "label", "value", "confidence_badge", "source", "validation_state", "mandatory", "locked"]],
        use_container_width=True,
        hide_index=True,
    )

    missing_mandatory = [
        r["field_id"]
        for r in resolved_rows
        if bool(r.get("mandatory")) and str(r.get("value") or "").strip() == ""
    ]
    not_present_mandatory = [
        r["field_id"]
        for r in resolved_rows
        if bool(r.get("mandatory")) and str(r.get("value") or "").strip().upper() == "NOT_PRESENT"
    ]
    if missing_mandatory:
        st.error(f"Mandatory fields missing: {', '.join(missing_mandatory)}")
    if not_present_mandatory:
        st.warning(
            "Mandatory fields marked NOT_PRESENT (supervisor sign-off required): "
            + ", ".join(not_present_mandatory)
        )

    citizen_id = str(selected_doc.get("citizen_id") or "")
    if citizen_id:
        key_fields = {"name", "dob", "aadhaar_number", "pan_number"}
        current_map = {
            _norm_key(r["field_id"]): str(r.get("value") or "").strip()
            for r in resolved_rows
            if _norm_key(r["field_id"]) in key_fields and str(r.get("value") or "").strip()
        }
        mismatches: list[dict[str, Any]] = []
        for other in docs:
            if str(other.get("id")) == doc_id:
                continue
            if str(other.get("citizen_id") or "") != citizen_id:
                continue
            ofields = (other.get("extraction_output") or {}).get("fields") or []
            other_map = {
                _norm_key(f.get("field_name")): str(f.get("normalized_value") or "").strip()
                for f in ofields
                if isinstance(f, dict)
            }
            for k, v in current_map.items():
                ov = other_map.get(k, "")
                if ov and ov != v:
                    mismatches.append(
                        {
                            "field_id": k,
                            "current_document": v,
                            "other_document": ov,
                            "other_document_id": str(other.get("id")),
                        }
                    )
        if mismatches:
            with st.expander("Cross-document reconciliation alerts", expanded=False):
                st.dataframe(pd.DataFrame(mismatches), use_container_width=True, hide_index=True)

    notes = st.text_area("Reviewer Notes", height=100, key=f"workspace_review_notes_{doc_id}")
    a1, a2, a3 = st.columns(3)
    with a1:
        if st.button("Save Populated Form", use_container_width=True, key=f"workspace_save_pop_{doc_id}"):
            if missing_mandatory:
                st.error("Cannot save: mandatory fields are missing.")
            else:
                try:
                    out = service.save_form_population(
                        document_id=doc_id,
                        actor_id=actor_id,
                        role=role,
                        document_type=selected_doc_type,
                        populated_rows=resolved_rows,
                    )
                    st.session_state["last_processed_doc"] = out
                    st.success(f"Saved form population. State: {out.get('state')}")
                except Exception as exc:
                    st.error(str(exc))
    with a2:
        if st.button("Approve", use_container_width=True, key=f"workspace_approve_{doc_id}"):
            if missing_mandatory:
                st.error("Cannot approve: fill mandatory fields first.")
            else:
                try:
                    out = service.decide_document(
                        doc_id,
                        actor_id=actor_id,
                        role=role,
                        decision="APPROVE",
                        notes=notes.strip() or None,
                    )
                    st.success(f"Decision: {out.get('decision')}")
                except Exception as exc:
                    st.error(str(exc))
    with a3:
        if st.button("Reject", use_container_width=True, key=f"workspace_reject_{doc_id}"):
            try:
                out = service.decide_document(
                    doc_id,
                    actor_id=actor_id,
                    role=role,
                    decision="REJECT",
                    notes=notes.strip() or None,
                )
                st.warning(f"Decision: {out.get('decision')}")
            except Exception as exc:
                st.error(str(exc))

    st.download_button(
        "Save & Export JSON",
        data=service.export_document_json(doc_id),
        file_name=f"{doc_id}.json",
        mime="application/json",
        use_container_width=True,
        key=f"workspace_export_{doc_id}",
    )

    with st.expander("Audit Logs", expanded=False):
        events = service.list_audit_events(document_id=doc_id, limit=500)
        reviews = service.list_reviews(document_id=doc_id)
        st.markdown("**Audit Events**")
        if events:
            st.dataframe(pd.DataFrame(events), use_container_width=True, hide_index=True)
        else:
            st.info("No audit events yet.")
        st.markdown("**Review Decisions**")
        if reviews:
            st.dataframe(pd.DataFrame(reviews), use_container_width=True, hide_index=True)
        else:
            st.info("No review decisions yet.")


def _build_doc_label(doc: dict[str, Any]) -> str:
    return f"{doc.get('id')} | {doc.get('citizen_id')} | {doc.get('file_name')} | {doc.get('state')}"


def _render_review(service: DocumentService, actor_id: str, role: str) -> None:
    docs = service.list_documents(limit=500)
    review_docs = [d for d in docs if str(d.get("state")) in {"WAITING_FOR_REVIEW", "REVIEW_IN_PROGRESS", "APPROVED", "REJECTED"}]

    if not review_docs:
        st.info("No reviewable documents yet. Submit and process a document first.")
        return

    labels = {_build_doc_label(d): d for d in review_docs}
    label_list = list(labels.keys())
    target_id = str(st.session_state.pop("review_doc_target_id", "") or "")
    default_idx = 0
    if target_id:
        for idx, lb in enumerate(label_list):
            if lb.startswith(f"{target_id} |"):
                default_idx = idx
                break
    selected_label = st.selectbox("Select document", options=label_list, index=default_idx)
    selected_doc = labels[selected_label]
    doc_id = str(selected_doc.get("id"))

    left, right = st.columns([2, 1])
    with left:
        st.markdown("### Evidence")
        file_path = str(selected_doc.get("file_path") or "")
        if not file_path:
            ingestion = ((selected_doc.get("metadata") or {}).get("ingestion") or {})
            file_path = str(ingestion.get("original_file_uri") or "")
        if file_path and Path(file_path).exists() and Path(file_path).suffix.lower() in {".png", ".jpg", ".jpeg"}:
            st.image(file_path, caption=selected_doc.get("file_name") or "uploaded", use_container_width=True)
        st.text_area("OCR Text", value=str(selected_doc.get("ocr_text") or selected_doc.get("raw_text") or ""), height=220)
        if str(selected_doc.get("ocr_engine") or "").startswith("paddle-unavailable:"):
            st.error(
                "OCR dependencies are unavailable for this runtime/file. "
                "Enable PaddleOCR, or ensure Tesseract + PDF raster support are installed."
            )

        cls = selected_doc.get("classification_output") or {}
        val = selected_doc.get("validation_output") or {}
        frd = selected_doc.get("fraud_output") or {}

        c1, c2, c3 = st.columns(3)
        c1.metric("Doc Type", cls.get("doc_type", "UNKNOWN"))
        c2.metric("Confidence", f"{float(selected_doc.get('confidence') or 0.0):.2f}")
        c3.metric("Risk", f"{float(selected_doc.get('risk_score') or 0.0):.2f}")

        st.write(
            {
                "validation_status": val.get("overall_status", "UNKNOWN"),
                "failed_fields": val.get("failed_count", 0),
                "risk_level": frd.get("risk_level", "UNKNOWN"),
                "stamp_present": frd.get("stamp_present"),
                "signature_present": frd.get("signature_present"),
            }
        )

        fields = (selected_doc.get("extraction_output") or {}).get("fields") or [{"field_name": "", "normalized_value": "", "confidence": 0.0}]
        edited = st.data_editor(pd.DataFrame(fields), use_container_width=True, num_rows="dynamic", key=f"edit_fields_{doc_id}")
        if st.button("Save Field Corrections", use_container_width=True, key=f"save_fields_{doc_id}"):
            payload = edited.fillna("").to_dict(orient="records")
            payload = [r for r in payload if str(r.get("field_name", "")).strip()]
            try:
                out = service.update_extracted_fields(doc_id, actor_id=actor_id, role=role, fields=payload)
                st.success(f"Fields saved. State: {out.get('state')}")
            except Exception as exc:
                st.error(str(exc))

    with right:
        st.markdown("### Decision")
        st.write(
            {
                "document_id": doc_id,
                "state": selected_doc.get("state"),
                "decision": selected_doc.get("decision") or "PENDING",
                "citizen_id": selected_doc.get("citizen_id"),
            }
        )
        notes = st.text_area("Reviewer notes", height=120, key=f"review_notes_{doc_id}")

        if st.button("Re-run Processing", use_container_width=True, key=f"rerun_{doc_id}"):
            try:
                out = service.process_document(doc_id, actor_id=actor_id, role=role)
                st.success(f"Reprocessed. State: {out.get('state')}")
            except Exception as exc:
                st.error(str(exc))

        c1, c2 = st.columns(2)
        with c1:
            if st.button("Approve", use_container_width=True, key=f"approve_{doc_id}"):
                try:
                    out = service.decide_document(doc_id, actor_id=actor_id, role=role, decision="APPROVE", notes=notes.strip() or None)
                    st.success(f"Decision: {out.get('decision')}")
                except Exception as exc:
                    st.error(str(exc))
        with c2:
            if st.button("Reject", use_container_width=True, key=f"reject_{doc_id}"):
                try:
                    out = service.decide_document(doc_id, actor_id=actor_id, role=role, decision="REJECT", notes=notes.strip() or None)
                    st.warning(f"Decision: {out.get('decision')}")
                except Exception as exc:
                    st.error(str(exc))


def _render_audit(service: DocumentService) -> None:
    docs = service.list_documents(limit=500)
    scope = st.selectbox("Audit scope", ["ALL"] + [str(d.get("id")) for d in docs], index=0)
    doc_id = None if scope == "ALL" else scope

    events = service.list_audit_events(document_id=doc_id, limit=1000)
    st.markdown("### Audit Events")
    if events:
        st.dataframe(pd.DataFrame(events), use_container_width=True, hide_index=True)
    else:
        st.info("No audit events yet.")

    reviews = service.list_reviews(document_id=doc_id)
    st.markdown("### Review Decisions")
    if reviews:
        st.dataframe(pd.DataFrame(reviews), use_container_width=True, hide_index=True)
    else:
        st.info("No review decisions yet.")


def _render_system(service: DocumentService, auth_service: AuthService) -> None:
    st.markdown("### Runtime Status")
    st.write(
        {
            "APP_ENV": settings.app_env,
            "OCR_BACKEND": settings.ocr_backend,
            "SUPABASE_URL_VALID": settings.supabase_url_valid(),
            "SUPABASE_KEY_PRESENT": settings.supabase_key_present(),
            "APPWRITE_CONFIGURED": settings.appwrite_configured(),
            "APPWRITE_ENDPOINT_SET": bool(settings.appwrite_endpoint.strip()),
            "APPWRITE_PROJECT_ID_SET": bool(settings.appwrite_project_id.strip()),
            "AUTH_PROVIDER": auth_service.provider,
            "AUTH_CONFIGURED": auth_service.configured(),
            "SENDGRID_CONFIGURED": auth_service.email_adapter.configured(),
            "PERSISTENCE": service.persistence_backend,
            "PERSISTENCE_NOTE": service.repo_error,
        }
    )
    if st.button("Test Auth Backend Connectivity", use_container_width=True):
        out = auth_service.connection_check()
        if out.ok:
            st.success(out.message)
            if out.data:
                st.json(out.data)
        else:
            st.error(out.message)


def main() -> None:
    _init_session()
    service = get_service()
    auth_service = get_auth_service()

    user = st.session_state.get("auth_user")
    if not user:
        _render_auth_page(auth_service)
        return

    with st.sidebar:
        st.markdown("## GovDocIQ")
        st.caption("Document Intelligence Platform")

        user_name = str(user.get("name") or "User")
        user_email = str(user.get("email") or "")
        st.markdown(f"**Name:** {user_name}")
        if user_email:
            st.markdown(f"**Email:** {user_email}")
        if str(user.get("auth_mode") or "") == "local":
            st.caption("Mode: Local (offline/no Supabase auth)")

        role_idx = ALL_ROLES.index(st.session_state.get("active_profile", ROLE_VERIFIER)) if st.session_state.get("active_profile", ROLE_VERIFIER) in ALL_ROLES else 0
        active_profile = st.selectbox("Profile", ALL_ROLES, index=role_idx)
        st.session_state["active_profile"] = active_profile

        actor_id = str(user.get("user_id") or user_email or "user-001")

        if st.button("Sign out", use_container_width=True):
            st.session_state["auth_user"] = None
            st.session_state["active_profile"] = ROLE_VERIFIER
            st.rerun()

    meta = ROLE_META.get(active_profile, {"icon": "👤", "label": active_profile})
    st.markdown(f"# {meta['icon']} GovDocIQ Workspace")
    st.caption(f"Active profile: {meta['label']} · User: {user_name}")
    _render_dashboard(service=service, role=active_profile)
    st.divider()
    _render_ingestion(service=service, actor_id=actor_id, role=active_profile)
    st.divider()
    _render_structured_fields(service=service, actor_id=actor_id, role=active_profile)
    with st.expander("System Status", expanded=False):
        _render_system(service=service, auth_service=auth_service)


if __name__ == "__main__":
    main()
