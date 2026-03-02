from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st
from PIL import Image, ImageDraw

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


def _find_focus_bbox(selected_doc: dict[str, Any], value: str) -> list[float] | None:
    val = str(value or "").strip().lower()
    if not val:
        return None
    tokens = (((selected_doc.get("metadata") or {}).get("ocr_tokens")) or [])
    if not isinstance(tokens, list):
        return None

    target_parts = [p for p in re.split(r"\s+", val) if p]
    matched: list[list[float]] = []
    for tok in tokens:
        if not isinstance(tok, dict):
            continue
        t = str(tok.get("text") or "").strip().lower()
        bbox = tok.get("bbox")
        if not t or not isinstance(bbox, list) or len(bbox) != 4:
            continue
        if t in val or any(part in t for part in target_parts):
            try:
                matched.append([float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])])
            except Exception:
                continue
    if not matched:
        return None
    x1 = min(b[0] for b in matched)
    y1 = min(b[1] for b in matched)
    x2 = max(b[2] for b in matched)
    y2 = max(b[3] for b in matched)
    return [x1, y1, x2, y2]


def _field_section(field_id: str) -> str:
    fid = _norm_key(field_id)
    if any(k in fid for k in ["name", "dob", "gender", "father", "spouse", "owner", "student", "head"]):
        return "Personal Details"
    if any(k in fid for k in ["address", "village", "city", "district", "state", "pin"]):
        return "Address"
    if any(k in fid for k in ["expiry", "issue_date", "valid", "date"]):
        return "Validity"
    return "Document Details"


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
    st.markdown("### 3) Verification Workspace")
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

    rows = _build_form_population_rows(selected_doc, selected_doc_type)
    row_by_id = {str(r.get("field_id")): r for r in rows}
    focus_options = [str(r.get("field_id")) for r in rows]
    if not focus_options:
        st.info("No fields in selected schema.")
        return

    z1, z2, z3 = st.columns([4, 3.5, 2.5], gap="large")

    with z2:
        st.markdown("#### Smart Form")
        focus_field_id = st.selectbox(
            "Focus field",
            options=focus_options,
            index=0,
            format_func=lambda fid: str(row_by_id.get(fid, {}).get("label") or fid),
            key=f"focus_field_{doc_id}",
        )

        updated_rows: list[dict[str, Any]] = []
        schema_by_id = {str(f["field_id"]): f for f in FORM_SCHEMAS.get(selected_doc_type, FORM_SCHEMAS["OTHER"])}
        sections = ["Personal Details", "Document Details", "Address", "Validity"]
        color_map = {
            "PASS": "#2e7d32",
            "EMPTY": "#607d8b",
            "MISSING": "#c62828",
            "FAIL_FORMAT": "#ef6c00",
            "FAIL_DATE": "#ef6c00",
            "FAIL_NUMBER": "#ef6c00",
            "FAIL_MIN": "#ef6c00",
            "FAIL_MAX": "#ef6c00",
            "FLAGGED_NOT_PRESENT": "#8e24aa",
        }

        for section in sections:
            section_rows = [r for r in rows if _field_section(str(r.get("field_id"))) == section]
            if not section_rows:
                continue
            st.markdown(f"**{section}**")
            for r in section_rows:
                field_id = str(r.get("field_id"))
                schema_field = schema_by_id.get(field_id, {"mandatory": False, "type": "text"})
                k_val = f"smart_val_{doc_id}_{field_id}"
                k_lock = f"smart_lock_{doc_id}_{field_id}"
                if k_val not in st.session_state:
                    st.session_state[k_val] = str(r.get("value") or "")
                if k_lock not in st.session_state:
                    st.session_state[k_lock] = bool(r.get("locked", False))

                value = st.text_input(str(r.get("label") or field_id), key=k_val, disabled=bool(st.session_state[k_lock]))
                lock_col, meta_col = st.columns([1, 3])
                with lock_col:
                    st.checkbox("Lock", key=k_lock)
                with meta_col:
                    source = "OCR Auto-filled" if value and value == str(r.get("ocr_value") or "") else "Operator Entered" if value else "Missing"
                    if str(value).strip().upper() == "NOT_PRESENT":
                        source = "Operator Marked Not Present"
                    validation_state = _validate_form_value(schema_field, value)
                    badge = _confidence_band(float(r.get("confidence") or 0.0))
                    color = color_map.get(validation_state, "#2e7d32")
                    st.markdown(
                        f"<div style='border-left:4px solid {color};padding-left:0.45rem;margin-bottom:0.4rem'>"
                        f"<small>Confidence: <b>{badge}</b> · Source: <b>{source}</b> · Validation: <b>{validation_state}</b></small>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )

                updated_rows.append(
                    {
                        "field_id": field_id,
                        "label": str(r.get("label") or field_id),
                        "value": str(value or ""),
                        "ocr_value": str(r.get("ocr_value") or ""),
                        "confidence": float(r.get("confidence") or 0.0),
                        "confidence_badge": badge,
                        "source": source,
                        "validation_state": validation_state,
                        "mandatory": bool(r.get("mandatory", False)),
                        "locked": bool(st.session_state[k_lock]),
                    }
                )

        total = len(updated_rows)
        confirmed = len(
            [
                r for r in updated_rows
                if r["validation_state"] == "PASS"
                or (not bool(r.get("mandatory")) and r["validation_state"] in {"EMPTY", "PASS"})
            ]
        )
        st.progress(confirmed / max(1, total))
        st.caption(f"{confirmed} of {total} fields confirmed")

        missing_mandatory = [
            r["field_id"] for r in updated_rows if bool(r.get("mandatory")) and str(r.get("value") or "").strip() == ""
        ]
        if missing_mandatory:
            st.error(f"Mandatory fields missing: {', '.join(missing_mandatory)}")

        notes = st.text_area("Reviewer Notes", height=90, key=f"workspace_review_notes_{doc_id}")
        b1, b2, b3 = st.columns(3)
        with b1:
            if st.button("Approve", use_container_width=True, key=f"workspace_approve_{doc_id}"):
                if missing_mandatory:
                    st.error("Cannot approve: fill mandatory fields first.")
                else:
                    try:
                        out = service.save_form_population(
                            document_id=doc_id,
                            actor_id=actor_id,
                            role=role,
                            document_type=selected_doc_type,
                            populated_rows=updated_rows,
                        )
                        out = service.decide_document(
                            doc_id,
                            actor_id=actor_id,
                            role=role,
                            decision="APPROVE",
                            notes=notes.strip() or None,
                        )
                        st.session_state["last_processed_doc"] = out
                        st.success(f"Decision: {out.get('decision')}")
                    except Exception as exc:
                        st.error(str(exc))
        with b2:
            if st.button("Flag", use_container_width=True, key=f"workspace_flag_{doc_id}"):
                try:
                    out = service.save_form_population(
                        document_id=doc_id,
                        actor_id=actor_id,
                        role=role,
                        document_type=selected_doc_type,
                        populated_rows=updated_rows,
                    )
                    service.log_event(
                        document_id=doc_id,
                        actor_id=actor_id,
                        actor_role=role,
                        event_type="document.flagged",
                        payload={"notes": notes.strip() or None},
                        tenant_id=str(out.get("tenant_id") or ""),
                    )
                    st.session_state["last_processed_doc"] = out
                    st.warning("Document flagged for manual/senior review.")
                except Exception as exc:
                    st.error(str(exc))
        with b3:
            if st.button("Reject", use_container_width=True, key=f"workspace_reject_{doc_id}"):
                try:
                    out = service.save_form_population(
                        document_id=doc_id,
                        actor_id=actor_id,
                        role=role,
                        document_type=selected_doc_type,
                        populated_rows=updated_rows,
                    )
                    out = service.decide_document(
                        doc_id,
                        actor_id=actor_id,
                        role=role,
                        decision="REJECT",
                        notes=notes.strip() or None,
                    )
                    st.session_state["last_processed_doc"] = out
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

    with z1:
        st.markdown("#### Document Viewer")
        file_path = str(selected_doc.get("file_path") or "")
        if not file_path:
            ingestion = ((selected_doc.get("metadata") or {}).get("ingestion") or {})
            file_path = str(ingestion.get("original_file_uri") or "")
        focus_row = row_by_id.get(st.session_state.get(f"focus_field_{doc_id}", focus_options[0]), row_by_id[focus_options[0]])
        focus_value = str(focus_row.get("value") or "")
        bbox = _find_focus_bbox(selected_doc, focus_value)

        if file_path and Path(file_path).exists() and Path(file_path).suffix.lower() in {".png", ".jpg", ".jpeg"}:
            try:
                image = Image.open(file_path).convert("RGB")
                if bbox:
                    draw = ImageDraw.Draw(image)
                    draw.rectangle([(bbox[0], bbox[1]), (bbox[2], bbox[3])], outline="#ff1744", width=5)
                st.image(image, use_container_width=True)
                if bbox:
                    st.caption(f"Focused field highlighted: {focus_row.get('label')}")
            except Exception:
                st.image(file_path, use_container_width=True)
        elif file_path and Path(file_path).suffix.lower() == ".pdf":
            st.info("PDF preview not rendered inline in this build. OCR + form data are still available.")
        else:
            st.info("Source document preview unavailable.")

        fraud = selected_doc.get("fraud_output") or {}
        st.write(
            {
                "stamp_detected": bool(fraud.get("stamp_present")),
                "signature_detected": bool(fraud.get("signature_present")),
            }
        )

    with z3:
        st.markdown("#### Integrity & Audit")
        fraud = selected_doc.get("fraud_output") or {}
        risk_level = str(fraud.get("risk_level") or "UNKNOWN").upper()
        checklist = [
            ("Stamp", bool(fraud.get("stamp_present")), "ok"),
            ("Signature", bool(fraud.get("signature_present")), "ok"),
            ("Expiry", False, "warn"),
            ("Duplicate", False, "ok"),
            ("Tamper", risk_level not in {"HIGH"}, "warn" if risk_level in {"HIGH", "MEDIUM"} else "ok"),
        ]
        phash = str((((selected_doc.get("metadata") or {}).get("ingestion") or {}).get("perceptual_hash") or ""))
        if phash:
            dup_count = 0
            for d in docs:
                if str(d.get("id")) == doc_id:
                    continue
                iph = str((((d.get("metadata") or {}).get("ingestion") or {}).get("perceptual_hash") or ""))
                if iph and iph == phash:
                    dup_count += 1
            if dup_count > 0:
                checklist[3] = ("Duplicate", False, "warn")
            else:
                checklist[3] = ("Duplicate", True, "ok")

        for name, ok, level in checklist:
            icon = "✓" if ok else "⚠"
            color = "#2e7d32" if ok and level == "ok" else "#ef6c00" if level == "warn" else "#c62828"
            st.markdown(f"<div style='color:{color};font-weight:600'>{icon} {name}</div>", unsafe_allow_html=True)

        st.markdown("**Timeline**")
        events = service.list_audit_events(document_id=doc_id, limit=20)
        if events:
            for e in events[:10]:
                ts = str(e.get("created_at") or "")[:19].replace("T", " ")
                et = str(e.get("event_type") or "")
                st.caption(f"{ts} · {et}")
        else:
            st.caption("No events yet.")

        citizen_id = str(selected_doc.get("citizen_id") or "")
        key_fields = {"name", "dob", "aadhaar_number", "pan_number"}
        current_map = {
            _norm_key(r["field_id"]): str(r.get("value") or "").strip()
            for r in rows
            if _norm_key(r["field_id"]) in key_fields and str(r.get("value") or "").strip()
        }
        mismatch_count = 0
        matched_count = 0
        if citizen_id and current_map:
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
                    ov = other_map.get(k)
                    if not ov:
                        continue
                    if ov == v:
                        matched_count += 1
                    else:
                        mismatch_count += 1
        st.markdown("**Cross-document reconciliation**")
        st.caption(f"Matched: {matched_count} · Mismatched: {mismatch_count}")


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
            "GROQ_CONFIGURED": bool(settings.groq_api_key.strip()),
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
