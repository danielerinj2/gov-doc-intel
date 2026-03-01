from __future__ import annotations

import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from app.config import settings
from app.infra.email_adapter import GovDocIQEmailAdapter
from app.infra.supabase_client import get_supabase_client
from app.infra.repositories import (
    ROLE_AUDITOR,
    ROLE_PLATFORM_ADMIN,
    ROLE_SENIOR_VERIFIER,
    ROLE_VERIFIER,
)
from app.services.document_service import DocumentService
from app.services.governance_service import GovernanceService
from app.services.offline_service import OfflineService

# ── Page config ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="GovDocIQ – Document Intelligence",
    page_icon="🏛️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Global CSS ─────────────────────────────────────────────────────────────────
st.markdown(
    """
    <style>
    /* ─── Base typography & spacing ─── */
    .block-container { padding-top: 1.5rem !important; }

    /* ─── Hero banner ─── */
    .hero-banner {
        background: linear-gradient(135deg, #0d1b2a 0%, #1b3a5c 100%);
        border-radius: 12px;
        padding: 2rem 2.5rem;
        margin-bottom: 1.5rem;
        border: 1px solid #1e3a5f;
    }
    .hero-banner h1 {
        font-size: 1.75rem;
        font-weight: 800;
        color: #e3f2fd;
        margin: 0 0 0.25rem 0;
    }
    .hero-banner .hero-sub {
        font-size: 0.88rem;
        color: #78909c;
        margin: 0;
    }
    .hero-banner .hero-role {
        display: inline-block;
        margin-top: 0.6rem;
        padding: 0.25rem 0.8rem;
        border-radius: 999px;
        font-size: 0.78rem;
        font-weight: 700;
    }

    /* ─── Journey stepper ─── */
    .journey-stepper {
        display: flex;
        align-items: center;
        flex-wrap: wrap;
        gap: 0;
        margin: 0.5rem 0 1rem 0;
        padding: 0.6rem 1rem;
        background: #0d1b2a;
        border-radius: 8px;
        border: 1px solid #1e3a5f;
    }
    .journey-step {
        background: #1e3a5f;
        color: #b0bec5;
        padding: 0.3rem 0.75rem;
        border-radius: 20px;
        font-size: 0.72rem;
        font-weight: 600;
        white-space: nowrap;
    }
    .journey-arrow {
        color: #37474f;
        font-size: 0.9rem;
        padding: 0 0.25rem;
        font-weight: 700;
    }
    .journey-step.active {
        background: #1565c0;
        color: #ffffff;
        box-shadow: 0 0 0 2px #42a5f5;
    }

    /* ─── KPI cards ─── */
    .kpi-card {
        background: #0d1b2a;
        border: 1px solid #1e3a5f;
        border-radius: 10px;
        padding: 0.9rem 1rem;
        text-align: center;
        min-height: 90px;
    }
    .kpi-card .kpi-value {
        font-size: 1.75rem;
        font-weight: 800;
        color: #4fc3f7;
        line-height: 1.2;
    }
    .kpi-card .kpi-label {
        font-size: 0.68rem;
        color: #78909c;
        margin-top: 0.15rem;
        text-transform: uppercase;
        letter-spacing: 0.06em;
    }
    .kpi-card .kpi-delta { font-size: 0.75rem; margin-top: 0.2rem; }
    .kpi-delta-good { color: #66bb6a; }
    .kpi-delta-bad  { color: #ef5350; }

    /* ─── Action card ─── */
    .action-card {
        background: #0d1b2a;
        border: 1px solid #1e3a5f;
        border-radius: 10px;
        padding: 1.2rem 1.5rem;
        margin-bottom: 0.75rem;
        transition: border-color 0.2s;
    }
    .action-card:hover { border-color: #42a5f5; }
    .action-card .ac-icon { font-size: 1.5rem; margin-bottom: 0.3rem; }
    .action-card .ac-title {
        font-size: 0.92rem;
        font-weight: 700;
        color: #e3f2fd;
        margin-bottom: 0.2rem;
    }
    .action-card .ac-desc {
        font-size: 0.78rem;
        color: #78909c;
        line-height: 1.4;
    }

    /* ─── Risk badge ─── */
    .badge {
        display: inline-block;
        padding: 0.2rem 0.65rem;
        border-radius: 999px;
        font-size: 0.7rem;
        font-weight: 700;
        letter-spacing: 0.04em;
    }
    .badge-critical { background: #b71c1c; color: #fff; }
    .badge-high     { background: #e65100; color: #fff; }
    .badge-medium   { background: #f57f17; color: #000; }
    .badge-low      { background: #1b5e20; color: #fff; }
    .badge-unknown  { background: #37474f; color: #fff; }

    /* ─── Section header ─── */
    .section-header {
        font-size: 0.95rem;
        font-weight: 700;
        color: #90caf9;
        border-left: 3px solid #1565c0;
        padding-left: 0.6rem;
        margin: 1.1rem 0 0.5rem 0;
    }

    /* ─── Confidence bar ─── */
    .conf-bar-wrap { background:#1e3a5f; border-radius:4px; height:8px; width:100%; }
    .conf-bar-fill { border-radius:4px; height:8px; }

    /* ─── Diff display ─── */
    .diff-old { color:#ef9a9a; text-decoration:line-through; font-family:monospace; }
    .diff-new { color:#a5d6a7; font-family:monospace; font-weight:600; }

    /* ─── Sidebar (light, readable) ─── */
    section[data-testid="stSidebar"] {
        background: #f3f6fb !important;
        border-right: 1px solid #d9e2ef;
    }
    section[data-testid="stSidebar"] * {
        color: #1f2a44 !important;
    }
    section[data-testid="stSidebar"] .stRadio > div { gap: 0.15rem !important; }
    section[data-testid="stSidebar"] label { font-size: 0.82rem !important; }
    section[data-testid="stSidebar"] .stTextInput input,
    section[data-testid="stSidebar"] .stSelectbox [data-baseweb="select"] > div,
    section[data-testid="stSidebar"] .stSelectbox div[role="combobox"],
    section[data-testid="stSidebar"] .stSelectbox div[role="listbox"] {
        background: #ffffff !important;
        color: #1f2a44 !important;
        border: 1px solid #c9d6e8 !important;
    }
    section[data-testid="stSidebar"] button {
        background: #ffffff !important;
        color: #1f2a44 !important;
        border: 1px solid #c9d6e8 !important;
    }

    /* ─── Alert banners ─── */
    .alert-info {
        background: #0d47a1; border-left: 4px solid #42a5f5;
        padding: 0.55rem 1rem; border-radius: 6px; margin: 0.4rem 0;
        font-size: 0.85rem; color: #e3f2fd;
    }
    .alert-success {
        background: #1b5e20; border-left: 4px solid #66bb6a;
        padding: 0.55rem 1rem; border-radius: 6px; margin: 0.4rem 0;
        font-size: 0.85rem; color: #e8f5e9;
    }
    .alert-warn {
        background: #bf360c; border-left: 4px solid #ff8a65;
        padding: 0.55rem 1rem; border-radius: 6px; margin: 0.4rem 0;
        font-size: 0.85rem; color: #fbe9e7;
    }

    /* ─── Status dot ─── */
    .status-dot {
        display: inline-block;
        width: 8px; height: 8px;
        border-radius: 50%;
        margin-right: 0.35rem;
        vertical-align: middle;
    }
    .status-dot.green  { background: #66bb6a; }
    .status-dot.yellow { background: #ffb74d; }
    .status-dot.red    { background: #ef5350; }
    .status-dot.gray   { background: #546e7a; }

    /* ─── Document context bar ─── */
    .doc-context-bar {
        background: #0d1b2a;
        border: 1px solid #1e3a5f;
        border-radius: 8px;
        padding: 0.6rem 1rem;
        display: flex;
        align-items: center;
        gap: 1.5rem;
        font-size: 0.8rem;
        color: #b0bec5;
        margin-bottom: 0.75rem;
        flex-wrap: wrap;
    }
    .doc-context-bar .dcb-item { white-space: nowrap; }
    .doc-context-bar .dcb-label { color: #546e7a; font-size: 0.7rem; text-transform: uppercase; }
    .doc-context-bar .dcb-value { color: #e3f2fd; font-weight: 600; }

    /* ─── Clean up Streamlit defaults ─── */
    #MainMenu { visibility: hidden; }
    footer { visibility: hidden; }
    .stDeployButton { display: none; }
    </style>
    """,
    unsafe_allow_html=True,
)

# ── Services ───────────────────────────────────────────────────────────────────
service = DocumentService()
governance = GovernanceService(service.repo)
offline_service = OfflineService(service)
email_adapter = GovDocIQEmailAdapter()

# ── Role constants ─────────────────────────────────────────────────────────────
ALL_ROLES = [
    ROLE_VERIFIER,
    ROLE_SENIOR_VERIFIER,
    ROLE_AUDITOR,
    ROLE_PLATFORM_ADMIN,
]

# Both Verifier and Senior Verifier can write and review.
# The sets are intentionally identical — the gate structure
# is preserved so they can be split again if needed later.
WRITE_ROLES = {ROLE_VERIFIER, ROLE_SENIOR_VERIFIER}
REVIEW_ROLES = {ROLE_VERIFIER, ROLE_SENIOR_VERIFIER}

# Only Senior Verifier can resolve disputes, manage config,
# govern templates/rules, and manage officer accounts.
SENIOR_ROLES = {ROLE_SENIOR_VERIFIER}

# Auditor and Senior Verifier can both read audit data.
# Senior Verifier needs audit read to support governance decisions.
AUDIT_ROLES = {ROLE_AUDITOR, ROLE_SENIOR_VERIFIER}

# Platform Admin only — cross-tenant operations.
PLATFORM_ROLES = {ROLE_PLATFORM_ADMIN}

# Who can see unmasked PII fields.
SENSITIVE_VIEW_ROLES = {ROLE_VERIFIER, ROLE_SENIOR_VERIFIER}

# ── Pages ──────────────────────────────────────────────────────────────────────
PAGES = [
    "🏠 Dashboard",
    "📥 Intake & Processing",
    "🔍 Review Workbench",
    "⚖️ Dispute Desk",
    "🛡️ Fraud & Authenticity",
    "💬 Citizen Communication",
    "📋 Audit Trail",
    "📊 Governance & KPI",
    "🖥️ Ops Monitor",
    "🧩 Integrations",
    "📴 Offline Sync",
    "🤖 ML Training",
]

PAGE_ACCESS: dict[str, set[str]] = {
    "🏠 Dashboard":             set(ALL_ROLES),
    "📥 Intake & Processing":   WRITE_ROLES,
    "🔍 Review Workbench":      REVIEW_ROLES,
    "⚖️ Dispute Desk":          SENIOR_ROLES,
    "🛡️ Fraud & Authenticity":  REVIEW_ROLES,
    "💬 Citizen Communication": WRITE_ROLES,
    "📋 Audit Trail":           AUDIT_ROLES | PLATFORM_ROLES,
    "📊 Governance & KPI":      SENIOR_ROLES | AUDIT_ROLES | PLATFORM_ROLES,
    "🖥️ Ops Monitor":           SENIOR_ROLES | AUDIT_ROLES | PLATFORM_ROLES,
    "🧩 Integrations":          SENIOR_ROLES | PLATFORM_ROLES,
    "📴 Offline Sync":          WRITE_ROLES,
    "🤖 ML Training":           SENIOR_ROLES | AUDIT_ROLES | PLATFORM_ROLES,
}

ROLE_META: dict[str, dict[str, str]] = {
    ROLE_VERIFIER: {
        "icon": "🧑‍💼",
        "label": "Verifier",
        "color": "#4fc3f7",
        "desc": "Frontline document intake, OCR, and standard review decisions.",
    },
    ROLE_SENIOR_VERIFIER: {
        "icon": "🎖️",
        "label": "Senior Verifier",
        "color": "#ffb74d",
        "desc": "Team lead. Disputes, escalations, governance, officer management.",
    },
    ROLE_AUDITOR: {
        "icon": "🔎",
        "label": "Auditor",
        "color": "#80deea",
        "desc": "Read-only compliance view. Audit trail, KPIs, ML logs.",
    },
    ROLE_PLATFORM_ADMIN: {
        "icon": "👑",
        "label": "Platform Admin",
        "color": "#ffcc02",
        "desc": "Cross-tenant platform operations, tenant provisioning, DR.",
    },
}

SCRIPT_OPTIONS = [
    "AUTO-DETECT", "Devanagari (Hindi/Marathi/Sanskrit)", "Bengali", "Tamil",
    "Telugu", "Kannada", "Malayalam", "Gujarati", "Gurmukhi (Punjabi)",
    "Odia", "Urdu (Nastaliq)", "Latin (English)",
]

DOC_TYPE_OPTIONS = [
    "AUTO-DETECT", "AADHAAR_CARD", "PAN_CARD", "VOTER_ID", "PASSPORT",
    "DRIVING_LICENSE", "RATION_CARD", "BIRTH_CERTIFICATE", "INCOME_CERTIFICATE",
    "CASTE_CERTIFICATE", "DOMICILE_CERTIFICATE", "LAND_RECORD", "MARKSHEET", "OTHER",
]


# ══════════════════════════════════════════════════════════════════════════════
# Utility helpers
# ══════════════════════════════════════════════════════════════════════════════

def _safe_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
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
        return dict(wrapped["document_record"])
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
        return [item for item in fields if isinstance(item, dict)]
    if isinstance(fields, dict):
        return [{"field_name": k, "normalized_value": v} for k, v in fields.items()]
    return []


def _load_docs(tenant_id: str, officer_id: str, role: str) -> tuple[list[dict[str, Any]], str | None]:
    try:
        return service.list_documents(tenant_id, officer_id), None
    except Exception as exc:
        return [], str(exc)


def _read_uploaded_document(uploaded_file: Any, script_hint: str | None = None) -> tuple[str, str | None]:
    if uploaded_file is None:
        return "", None
    suffix = Path(str(uploaded_file.name)).suffix or ".bin"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as handle:
        handle.write(uploaded_file.getvalue())
        tmp_path = handle.name
    raw_text = ""
    if suffix.lower() in {".txt", ".csv", ".json"}:
        try:
            raw_text = uploaded_file.getvalue().decode("utf-8", errors="ignore")
        except Exception:
            pass
    elif suffix.lower() == ".pdf":
        try:
            from pypdf import PdfReader
            reader = PdfReader(tmp_path)
            raw_text = "\n".join((p.extract_text() or "") for p in reader.pages[:10]).strip()
        except Exception:
            pass
    elif suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}:
        # Defer OCR to processing stage to avoid duplicate work and keep intake submit fast.
        raw_text = ""
    return raw_text, tmp_path


# ══════════════════════════════════════════════════════════════════════════════
# UI primitives
# ══════════════════════════════════════════════════════════════════════════════

def _section(title: str) -> None:
    st.markdown(f'<div class="section-header">{title}</div>', unsafe_allow_html=True)


def _render_journey(title: str, steps: list[str], active_index: int = -1) -> None:
    parts: list[str] = []
    for i, step in enumerate(steps):
        cls = "journey-step active" if i == active_index else "journey-step"
        parts.append(f'<span class="{cls}">{step}</span>')
        if i < len(steps) - 1:
            parts.append('<span class="journey-arrow">›</span>')
    st.markdown(
        f'<div style="font-size:0.78rem;font-weight:600;color:#546e7a;margin-bottom:0.2rem">{title}</div>'
        f'<div class="journey-stepper">{"".join(parts)}</div>',
        unsafe_allow_html=True,
    )


def _kpi_card(label: str, value: Any, delta: str = "", delta_good: bool = True) -> str:
    delta_cls = "kpi-delta-good" if delta_good else "kpi-delta-bad"
    delta_html = f'<div class="kpi-delta {delta_cls}">{delta}</div>' if delta else ""
    return f"""<div class="kpi-card">
        <div class="kpi-value">{value}</div>
        <div class="kpi-label">{label}</div>{delta_html}
    </div>"""


def _render_kpi_row(cards: list[dict[str, Any]]) -> None:
    if not cards:
        return
    cols = st.columns(len(cards))
    for col, card in zip(cols, cards):
        with col:
            st.markdown(
                _kpi_card(
                    label=card.get("label", ""),
                    value=card.get("value", "-"),
                    delta=card.get("delta", ""),
                    delta_good=card.get("delta_good", True),
                ),
                unsafe_allow_html=True,
            )


def _risk_badge(level: str) -> str:
    cls_map = {"CRITICAL": "badge-critical", "HIGH": "badge-high", "MEDIUM": "badge-medium", "LOW": "badge-low"}
    cls = cls_map.get(str(level).upper(), "badge-unknown")
    return f'<span class="badge {cls}">{level}</span>'


def _confidence_bar(value: float | None, label: str = "") -> None:
    pct = int((value or 0.0) * 100)
    color = "#ef5350" if pct < 50 else "#ffb74d" if pct < 75 else "#66bb6a"
    st.markdown(
        f'<div style="margin:0.2rem 0">'
        f'<div style="display:flex;justify-content:space-between;font-size:0.72rem;color:#78909c">'
        f'<span>{label}</span><span>{pct}%</span></div>'
        f'<div class="conf-bar-wrap"><div class="conf-bar-fill" style="width:{pct}%;background:{color}"></div></div></div>',
        unsafe_allow_html=True,
    )


def _action_card(icon: str, title: str, desc: str) -> str:
    return (
        f'<div class="action-card">'
        f'<div class="ac-icon">{icon}</div>'
        f'<div class="ac-title">{title}</div>'
        f'<div class="ac-desc">{desc}</div>'
        f'</div>'
    )


def _status_dot(color: str) -> str:
    return f'<span class="status-dot {color}"></span>'


def _hero_banner(role: str, tenant_id: str, officer_id: str) -> None:
    meta = ROLE_META.get(role, {"icon": "👤", "label": role, "color": "#90a4ae"})
    using_sb = service.repo.using_supabase
    dot = _status_dot("green" if using_sb else "yellow")
    st.markdown(
        f'<div class="hero-banner">'
        f'<h1>🏛️ GovDocIQ</h1>'
        f'<p class="hero-sub">{dot} AI-Powered Document Verification Platform &nbsp;·&nbsp; '
        f'Officer: <strong>{officer_id}</strong></p>'
        f'<span class="hero-role" style="background:{meta["color"]}22;'
        f'border:1px solid {meta["color"]};color:{meta["color"]}">'
        f'{meta["icon"]} {meta["label"]}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )


def _doc_context_bar(selected_doc: dict[str, Any] | None, role: str) -> None:
    """Compact context bar shown when a document is selected — replaces the old giant header."""
    if not selected_doc:
        return
    state = str(selected_doc.get("state", ""))
    decision = str(selected_doc.get("decision") or "PENDING")
    confidence = selected_doc.get("confidence")
    risk = selected_doc.get("risk_score")
    doc_id = str(selected_doc.get("id", ""))[:12]
    citizen = _mask(str(selected_doc.get("citizen_id", "")), role)

    conf_str = f"{int(float(confidence) * 100)}%" if confidence is not None else "—"
    risk_str = f"{float(risk):.2f}" if risk is not None else "—"

    # Color for state
    state_color = "green" if "APPROVED" in state or "COMPLETED" in state else "red" if "REJECTED" in state or "FAILED" in state else "yellow" if "REVIEW" in state else "gray"

    st.markdown(
        f'<div class="doc-context-bar">'
        f'<div class="dcb-item"><span class="dcb-label">Document</span><br>'
        f'<span class="dcb-value">{doc_id}…</span></div>'
        f'<div class="dcb-item"><span class="dcb-label">Citizen</span><br>'
        f'<span class="dcb-value">{citizen}</span></div>'
        f'<div class="dcb-item"><span class="dcb-label">State</span><br>'
        f'{_status_dot(state_color)}<span class="dcb-value">{state}</span></div>'
        f'<div class="dcb-item"><span class="dcb-label">Decision</span><br>'
        f'<span class="dcb-value">{decision}</span></div>'
        f'<div class="dcb-item"><span class="dcb-label">Confidence</span><br>'
        f'<span class="dcb-value">{conf_str}</span></div>'
        f'<div class="dcb-item"><span class="dcb-label">Risk</span><br>'
        f'<span class="dcb-value">{risk_str}</span></div>'
        f'</div>',
        unsafe_allow_html=True,
    )


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: Dashboard  ← COMPLETELY REDESIGNED
# ══════════════════════════════════════════════════════════════════════════════

def _render_dashboard(
    *, role: str, tenant_id: str, officer_id: str, docs: list[dict[str, Any]]
) -> None:
    total = len(docs)
    waiting = len([d for d in docs if str(d.get("state")) == "WAITING_FOR_REVIEW"])
    in_progress = len([d for d in docs if str(d.get("state")) == "REVIEW_IN_PROGRESS"])
    approved = len([d for d in docs if str(d.get("decision")) == "APPROVE"])
    rejected = len([d for d in docs if str(d.get("decision")) == "REJECT"])
    high_risk = len([d for d in docs if float(d.get("risk_score") or 0) >= 0.6])
    now = datetime.now(timezone.utc)
    policy = service.repo.get_tenant_policy(tenant_id)
    sla_days = int(policy.get("review_sla_days", 3))
    sla_breached = sum(
        1 for d in docs
        if (
            _safe_dt(d.get("created_at")) is not None
            and now > _safe_dt(d.get("created_at")) + timedelta(days=sla_days)
            and str(d.get("state")) in {"WAITING_FOR_REVIEW", "REVIEW_IN_PROGRESS"}
        )
    )

    # ── KPI strip ─────────────────────────────────────────────────────────────
    _render_kpi_row([
        {"label": "Total Documents",  "value": total},
        {"label": "Awaiting Review",  "value": waiting,    "delta": f"⏳ {waiting}" if waiting > 0 else "", "delta_good": waiting == 0},
        {"label": "In Progress",      "value": in_progress},
        {"label": "Approved",         "value": approved,   "delta": "✅" if approved > 0 else ""},
        {"label": "Rejected",         "value": rejected},
        {"label": "High Risk",        "value": high_risk,  "delta": "🔴" if high_risk > 0 else "", "delta_good": high_risk == 0},
    ])

    if sla_breached > 0:
        st.markdown(
            f'<div class="alert-warn">🚨 <strong>{sla_breached}</strong> document(s) have breached SLA. '
            f'Go to <strong>Ops Monitor</strong> to escalate.</div>',
            unsafe_allow_html=True,
        )

    st.markdown("")  # spacer

    # ── Role-specific action cards ────────────────────────────────────────────
    _section("⚡ Quick Actions")

    if role == ROLE_VERIFIER:
        c1, c2, c3 = st.columns(3)
        with c1:
            st.markdown(_action_card(
                "📤", "Submit Document",
                "Upload and process a citizen document through the AI verification pipeline."
            ), unsafe_allow_html=True)
            if st.button("Go to Intake →", key="qa_intake", use_container_width=True):
                st.session_state["_nav_override"] = "📥 Intake & Processing"
                st.rerun()
        with c2:
            st.markdown(_action_card(
                "🔍", "Review Queue",
                f"{waiting} document(s) awaiting review. Inspect evidence and decide."
            ), unsafe_allow_html=True)
            if st.button("Go to Review →", key="qa_review", use_container_width=True):
                st.session_state["_nav_override"] = "🔍 Review Workbench"
                st.rerun()
        with c3:
            st.markdown(_action_card(
                "📴", "Offline Capture",
                "Create a provisional record for service centers without connectivity."
            ), unsafe_allow_html=True)
            if st.button("Go to Offline →", key="qa_offline", use_container_width=True):
                st.session_state["_nav_override"] = "📴 Offline Sync"
                st.rerun()

    elif role == ROLE_SENIOR_VERIFIER:
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            st.markdown(_action_card(
                "⚖️", "Disputes",
                "Resolve citizen appeals and internal officer disagreements."
            ), unsafe_allow_html=True)
            if st.button("Go to Disputes →", key="qa_disp", use_container_width=True):
                st.session_state["_nav_override"] = "⚖️ Dispute Desk"
                st.rerun()
        with c2:
            st.markdown(_action_card(
                "🔍", "Review Queue",
                f"{waiting + in_progress} document(s) in the review pipeline."
            ), unsafe_allow_html=True)
            if st.button("Go to Review →", key="qa_rev2", use_container_width=True):
                st.session_state["_nav_override"] = "🔍 Review Workbench"
                st.rerun()
        with c3:
            st.markdown(_action_card(
                "📊", "Governance",
                "Manage templates, rules, policies, and officer accounts."
            ), unsafe_allow_html=True)
            if st.button("Go to Governance →", key="qa_gov", use_container_width=True):
                st.session_state["_nav_override"] = "📊 Governance & KPI"
                st.rerun()
        with c4:
            ops_label = f"🚨 {sla_breached} SLA breach(es)" if sla_breached else "✅ All clear"
            st.markdown(_action_card("🖥️", "Ops Monitor", ops_label), unsafe_allow_html=True)
            if st.button("Go to Ops →", key="qa_ops", use_container_width=True):
                st.session_state["_nav_override"] = "🖥️ Ops Monitor"
                st.rerun()

    elif role == ROLE_AUDITOR:
        c1, c2, c3 = st.columns(3)
        with c1:
            st.markdown(_action_card(
                "📋", "Audit Trail",
                "Full state history, event timeline, model versions, and human overrides."
            ), unsafe_allow_html=True)
            if st.button("Go to Audit →", key="qa_aud", use_container_width=True):
                st.session_state["_nav_override"] = "📋 Audit Trail"
                st.rerun()
        with c2:
            st.markdown(_action_card(
                "📊", "Governance",
                "Read-only view of templates, rules, and compliance metrics."
            ), unsafe_allow_html=True)
            if st.button("Go to Governance →", key="qa_gov2", use_container_width=True):
                st.session_state["_nav_override"] = "📊 Governance & KPI"
                st.rerun()
        with c3:
            st.markdown(_action_card(
                "🖥️", "Ops Monitor",
                "Read-only system health, throughput, and SLA status."
            ), unsafe_allow_html=True)
            if st.button("Go to Ops →", key="qa_ops_aud", use_container_width=True):
                st.session_state["_nav_override"] = "🖥️ Ops Monitor"
                st.rerun()

    elif role == ROLE_PLATFORM_ADMIN:
        c1, c2, c3 = st.columns(3)
        with c1:
            st.markdown(_action_card(
                "🌐", "Cross-Tenant Overview",
                "Platform-wide audit, tenant isolation checks, and incident summaries."
            ), unsafe_allow_html=True)
            if st.button("Go to Governance →", key="qa_plat", use_container_width=True):
                st.session_state["_nav_override"] = "📊 Governance & KPI"
                st.rerun()
        with c2:
            st.markdown(_action_card(
                "🖥️", "Platform Ops",
                "Multi-tenant health, throughput, and DR status."
            ), unsafe_allow_html=True)
            if st.button("Go to Ops →", key="qa_plat_ops", use_container_width=True):
                st.session_state["_nav_override"] = "🖥️ Ops Monitor"
                st.rerun()
        with c3:
            st.markdown(_action_card(
                "🧩", "Integrations",
                "API keys, webhooks, and batch export across tenants."
            ), unsafe_allow_html=True)
            if st.button("Go to Integrations →", key="qa_plat_int", use_container_width=True):
                st.session_state["_nav_override"] = "🧩 Integrations"
                st.rerun()

    # ── Recent activity + documents ───────────────────────────────────────────
    col1, col2 = st.columns([1, 2])

    with col1:
        _section("🕒 Recent Events")
        try:
            events = service.list_tenant_events(tenant_id, officer_id)
            if events:
                for ev in sorted(events, key=lambda e: str(e.get("created_at", "")), reverse=True)[:8]:
                    ev_type = str(ev.get("event_type", ""))
                    ts = str(ev.get("created_at", ""))[:16].replace("T", " ")
                    icon = "✅" if "approved" in ev_type else "❌" if "failed" in ev_type or "rejected" in ev_type else "⚠️" if "escalat" in ev_type else "🔄"
                    st.markdown(f"<span style='font-size:0.8rem;color:#b0bec5'>{icon} <code>{ev_type}</code> <span style='color:#546e7a'>· {ts}</span></span>", unsafe_allow_html=True)
            else:
                st.caption("No events yet. Submit a document to get started.")
        except Exception:
            st.caption("Events unavailable.")

    with col2:
        _section("📄 Recent Documents")
        if docs:
            recent = sorted(docs, key=lambda d: str(d.get("updated_at", d.get("created_at", ""))), reverse=True)[:10]
            df = pd.DataFrame(recent)
            show_cols = [c for c in ["id", "state", "decision", "confidence", "risk_score", "created_at"] if c in df.columns]
            st.dataframe(df[show_cols], use_container_width=True, hide_index=True)
        else:
            st.caption("No documents yet. Use **Intake & Processing** to submit your first document.")

    # ── State distribution chart (only if there's data) ───────────────────────
    if docs and len(docs) >= 3:
        _section("📈 State Distribution")
        df_all = pd.DataFrame(docs)
        if "state" in df_all.columns:
            counts = df_all["state"].value_counts().reset_index()
            counts.columns = ["State", "Count"]
            st.bar_chart(counts.set_index("State"))


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: Intake & Processing
# ══════════════════════════════════════════════════════════════════════════════

def _render_intake_processing(
    *, role: str, tenant_id: str, officer_id: str,
    docs: list[dict[str, Any]], selected_doc: dict[str, Any] | None,
) -> None:
    _render_journey(
        "Document Verification Pipeline",
        ["Upload", "OCR", "Classify", "Extract", "Validate", "Review / Auto-Approve", "Notify"],
        active_index=0,
    )

    can_write = role in WRITE_ROLES
    if not can_write:
        st.markdown('<div class="alert-warn">🔒 Read-only mode. Intake actions are disabled for your role.</div>', unsafe_allow_html=True)

    tab_online, tab_center, tab_ocr, tab_queue = st.tabs([
        "🌐 Online Portal", "🏢 Service Center", "🔬 OCR & Preprocessing", "📋 Queue"
    ])

    with tab_online:
        _section("Upload & Submit")
        uploaded = st.file_uploader(
            "Document file (PDF / image / text)",
            type=["pdf", "jpg", "jpeg", "png", "txt", "csv", "json"],
            key="portal_upload",
            help="Scanned images, PDFs, or plain text. Up to 10 pages extracted from PDFs.",
        )

        c1, c2 = st.columns(2)
        with c1:
            hint_script = st.selectbox("Script hint", SCRIPT_OPTIONS, index=0, help="Helps OCR focus on the correct script family.")
        with c2:
            hint_doc_type = st.selectbox("Document type hint", DOC_TYPE_OPTIONS, index=0, help="Pre-maps to a template. Leave AUTO if unsure.")

        file_sig = ""
        fallback_required = False
        if uploaded is not None:
            file_sig = f"{uploaded.name}:{getattr(uploaded, 'size', 0)}"
            fallback_required = bool(
                st.session_state.get("portal_fallback_required")
                and st.session_state.get("portal_fallback_file") == file_sig
            )

        with st.form("intake_form_online"):
            citizen_id = st.text_input("Citizen ID *", value="citizen-001")
            scheme_context = st.text_input(
                "Scheme / Service Context (optional)",
                value="",
                help="Example: scholarship, pension, land mutation, welfare scheme.",
            )
            notes = st.text_area("Notes (optional)", value="", height=55)
            fallback_text = ""
            if fallback_required:
                fallback_text = st.text_area(
                    "Fallback text (required because extraction failed)",
                    value="",
                    height=80,
                )

            submit_process = st.form_submit_button(
                "⚡ Submit & Quick Scan (≤3s)",
                disabled=not can_write,
                use_container_width=True,
            )

        if submit_process:
            if not citizen_id.strip():
                st.error("Citizen ID is required.")
            elif uploaded is None:
                st.error("Please upload a document.")
            else:
                upload_text, source_path = _read_uploaded_document(uploaded, script_hint=hint_script)
                raw_text = (upload_text or "").strip()
                fallback_clean = (fallback_text or "").strip()

                if not raw_text and not fallback_clean:
                    st.session_state["portal_fallback_required"] = True
                    st.session_state["portal_fallback_file"] = file_sig
                    st.warning(
                        "No immediate text was extracted from upload. "
                        "Proceeding with fast OCR scan from source file."
                    )

                st.session_state["portal_fallback_required"] = False
                st.session_state["portal_fallback_file"] = ""

                final_raw_text = raw_text or fallback_clean
                ingestion = {
                    "source": "ONLINE_PORTAL",
                    "original_file_uri": source_path,
                }
                if hint_script != "AUTO-DETECT":
                    ingestion["script_hint"] = hint_script
                if hint_doc_type != "AUTO-DETECT":
                    ingestion["doc_type_hint"] = hint_doc_type
                metadata = {
                    "ingestion": ingestion,
                    "operator_notes": notes.strip() or None,
                }
                if scheme_context.strip():
                    metadata["submission_context"] = {
                        "scheme": scheme_context.strip(),
                        "workspace": tenant_id,
                    }

                file_name = uploaded.name

                try:
                    created_doc = service.create_document(
                        tenant_id=tenant_id, citizen_id=citizen_id.strip(),
                        file_name=file_name, raw_text=final_raw_text,
                        officer_id=officer_id, metadata=metadata,
                    )
                    processed = service.process_document(
                        str(created_doc["id"]),
                        tenant_id,
                        officer_id,
                        fast_scan=True,
                        max_latency_seconds=3.0,
                    )
                    quick_scan_meta = ((processed.get("derived") or {}).get("quick_scan") or {})
                    elapsed = quick_scan_meta.get("elapsed_ms")
                    elapsed_note = f" · {elapsed} ms" if elapsed is not None else ""
                    st.markdown(
                        f'<div class="alert-success">✅ Quick scan completed '
                        f'<code>{processed["id"]}</code> → <strong>{processed.get("state")}</strong> '
                        f'(target: ≤3s{elapsed_note})</div>',
                        unsafe_allow_html=True,
                    )
                except Exception as exc:
                    st.error(str(exc))

    with tab_center:
        _section("Service Center Capture")
        _render_journey("Assisted Intake", ["Scan", "Offline?", "Sync", "Pipeline", "Notify"], active_index=0)
        sc_uploaded = st.file_uploader("Captured file", type=["pdf", "jpg", "jpeg", "png", "txt"], key="center_upload")

        with st.form("intake_form_center"):
            f1, f2 = st.columns(2)
            with f1:
                citizen_id_sc = st.text_input("Citizen ID", value="citizen-center-001")
                center_id = st.text_input("Service Center ID", value="center-01")
            with f2:
                file_name_sc = st.text_input("File name", value="center_capture.txt")
                reference_no = st.text_input("Reference No.", value="REF-001")
            center_meta_raw = st.text_area("Metadata (JSON)", value='{"source":"SERVICE_CENTER","service_type":"WELFARE_SCHEME"}', height=65)
            center_fallback = st.text_area("Fallback text", value="", height=70)

            bc1, bc2, bc3 = st.columns(3)
            with bc1:
                save_only = st.form_submit_button("💾 Save", disabled=not can_write, use_container_width=True)
            with bc2:
                process_now = st.form_submit_button("⚡ Save + Quick Scan (≤3s)", disabled=not can_write, use_container_width=True)
            with bc3:
                save_offline = st.form_submit_button("📴 Offline Provisional", disabled=not can_write, use_container_width=True)

        if save_only or process_now or save_offline:
            try:
                metadata_sc = json.loads(center_meta_raw) if center_meta_raw.strip() else {}
                upload_text, source_path = _read_uploaded_document(sc_uploaded, script_hint=None)
                raw_text_sc = upload_text or center_fallback or ""
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
                        tenant_id=tenant_id, citizen_id=citizen_id_sc.strip(),
                        file_name=final_name_sc, raw_text=raw_text_sc,
                        officer_id=officer_id,
                        local_model_versions={"ocr_model_id": "ocr-lite-v1", "classifier_model_id": "classifier-lite-v1"},
                        provisional_decision="REVIEW", metadata=metadata_sc,
                    )
                    st.markdown(f'<div class="alert-success">📴 Offline provisional: <code>{out.get("id")}</code></div>', unsafe_allow_html=True)
                else:
                    created_doc = service.create_document(
                        tenant_id=tenant_id, citizen_id=citizen_id_sc.strip(),
                        file_name=final_name_sc, raw_text=raw_text_sc,
                        officer_id=officer_id, metadata=metadata_sc,
                    )
                    if process_now:
                        processed = service.process_document(
                            str(created_doc["id"]),
                            tenant_id,
                            officer_id,
                            fast_scan=True,
                            max_latency_seconds=3.0,
                        )
                        quick_scan_meta = ((processed.get("derived") or {}).get("quick_scan") or {})
                        elapsed = quick_scan_meta.get("elapsed_ms")
                        elapsed_note = f" · {elapsed} ms" if elapsed is not None else ""
                        st.markdown(
                            f'<div class="alert-success">✅ Quick scan completed '
                            f'<code>{processed["id"]}</code> → <strong>{processed.get("state")}</strong> '
                            f'(target: ≤3s{elapsed_note})</div>',
                            unsafe_allow_html=True,
                        )
                    else:
                        st.markdown(f'<div class="alert-success">✅ Saved: <code>{created_doc["id"]}</code></div>', unsafe_allow_html=True)
            except Exception as exc:
                st.error(str(exc))

    with tab_ocr:
        _section("OCR Engine & Preprocessing")
        st.caption("Preprocessing pipeline for scanned documents before OCR and classification.")
        oc1, oc2 = st.columns(2)
        with oc1:
            st.markdown("**Preprocessing Steps**")
            for name, desc in [("Deskew", "±15° rotation correction"), ("Binarization", "Adaptive threshold for faded ink"),
                               ("Noise Removal", "Median filter for artifacts"), ("Contrast", "CLAHE for low-quality scans"),
                               ("Segmentation", "Text blocks, stamps, signatures")]:
                st.markdown(f"- **{name}** — {desc}")
        with oc2:
            st.markdown("**Supported Scripts**")
            for s, l in [("Devanagari", "Hindi, Marathi, Sanskrit"), ("Bengali", "Bengali, Assamese"),
                         ("Dravidian", "Tamil, Telugu, Kannada, Malayalam"), ("Perso-Arabic", "Urdu"),
                         ("Other Indic", "Gujarati, Punjabi, Odia"), ("Latin", "English")]:
                st.markdown(f"- **{s}**: {l}")

        if selected_doc:
            _section("Per-Document Signals")
            derived = dict(selected_doc.get("derived") or {})
            quality = (derived.get("preprocessing_hashing") or {}).get("quality_score")
            ocr_conf = (derived.get("ocr_multi_script") or {}).get("ocr_confidence")
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Quality", f"{quality or '—'}")
            m2.metric("OCR Conf.", f"{ocr_conf or '—'}")
            m3.metric("Script", (derived.get("ocr_multi_script") or {}).get("detected_script", "—"))
            m4.metric("Language", (derived.get("ocr_multi_script") or {}).get("detected_language", "—"))
            if ocr_conf is not None:
                _confidence_bar(float(ocr_conf), "OCR Confidence")

    with tab_queue:
        _section("Document Queue")
        if not docs:
            st.caption("No documents yet. Submit one via the Online Portal or Service Center tab.")
        else:
            df = pd.DataFrame(docs)
            f1, f2, f3 = st.columns(3)
            with f1:
                state_opts = ["ALL"] + sorted({str(x) for x in df.get("state", pd.Series()).tolist()})
                state_filter = st.selectbox("State", state_opts, index=0)
            with f2:
                min_risk = st.slider("Min risk", 0.0, 1.0, 0.0, 0.05)
            with f3:
                sla_only = st.checkbox("SLA ≤24h", value=False)

            if state_filter != "ALL":
                df = df[df["state"] == state_filter]
            if "risk_score" in df.columns:
                df = df[df["risk_score"].fillna(0.0) >= float(min_risk)]
            if sla_only:
                now = datetime.now(timezone.utc)
                sla_d = int(service.repo.get_tenant_policy(tenant_id).get("review_sla_days", 3))
                cutoff = now + timedelta(hours=24)
                flags = []
                for _, r in df.iterrows():
                    ca = _safe_dt(r.get("created_at"))
                    flags.append(ca is not None and ca + timedelta(days=sla_d) <= cutoff and str(r.get("state")) in {"WAITING_FOR_REVIEW", "REVIEW_IN_PROGRESS"})
                df = df[pd.Series(flags, index=df.index)]

            cols = [c for c in ["id", "state", "decision", "confidence", "risk_score", "created_at", "updated_at"] if c in df.columns]
            st.dataframe(df[cols], use_container_width=True, hide_index=True)
            st.caption(f"Showing {len(df)} of {len(docs)}")


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: Review Workbench
# ══════════════════════════════════════════════════════════════════════════════

def _render_review_workbench(
    *, role: str, tenant_id: str, officer_id: str,
    selected_doc: dict[str, Any] | None, selected_record: dict[str, Any],
) -> None:
    _render_journey("Officer Review", ["Queue", "Evidence", "Explain", "Correct", "Decide", "Archive"], active_index=1)

    can_review = role in REVIEW_ROLES
    review_states = {"WAITING_FOR_REVIEW", "DISPUTED", "REVIEW_IN_PROGRESS"}

    try:
        assignments = service.list_review_assignments(tenant_id, officer_id)
        if assignments:
            _section("📌 Your Assignments")
            st.dataframe(pd.DataFrame(assignments), use_container_width=True, hide_index=True)
    except Exception as exc:
        st.error(str(exc))

    if not selected_doc:
        st.markdown('<div class="alert-info">Select a document from the sidebar to begin review.</div>', unsafe_allow_html=True)
        return

    try:
        all_docs = service.list_documents(tenant_id, officer_id)
        queue_docs = [d for d in all_docs if str(d.get("state", "")).upper() in review_states]
        if queue_docs:
            _section("🗂️ Review Queue Snapshot")
            qdf = pd.DataFrame(queue_docs)
            show_cols = [c for c in ["id", "state", "decision", "confidence", "risk_score", "created_at"] if c in qdf.columns]
            st.dataframe(qdf[show_cols], use_container_width=True, hide_index=True)
    except Exception:
        pass

    extraction_out = dict(selected_record.get("extraction_output") or {})
    validation_out = dict(selected_record.get("validation_output") or {})
    visual_out = dict(selected_record.get("visual_authenticity_output") or {})
    explainability = dict(selected_record.get("explainability") or {})
    classification_out = dict(selected_record.get("classification_output") or {})
    left_col, right_col = st.columns([2, 1])
    doc_id = str(selected_doc.get("id"))
    current_state = str(selected_doc.get("state", "")).upper()

    with left_col:
        lt1, lt2, lt3, lt4 = st.tabs(["📑 Fields", "✔️ Validation", "🔏 Authenticity", "🧠 Explain"])

        with lt1:
            _section("Extracted Fields")
            doc_type = classification_out.get("doc_type") or classification_out.get("document_type") or "UNKNOWN"
            cls_conf = classification_out.get("confidence")
            st.markdown(f"**Classified as:** `{doc_type}`")
            if cls_conf is not None:
                _confidence_bar(float(cls_conf), "Classification Confidence")
            extracted = _normalize_extracted_fields(extraction_out)
            if extracted:
                st.dataframe(pd.DataFrame(extracted), use_container_width=True, hide_index=True)
                conf_fields = [r for r in extracted if r.get("confidence") is not None or r.get("extraction_confidence") is not None]
                if conf_fields:
                    _section("Per-Field Confidence")
                    for r in conf_fields:
                        cv = r.get("confidence") or r.get("extraction_confidence")
                        try:
                            _confidence_bar(float(cv), str(r.get("field_name") or r.get("key") or "field"))
                        except Exception:
                            pass
            else:
                st.caption("No extracted fields available.")

            _section("Cross-Verification")
            dv = (selected_doc.get("derived") or {}).get("validation", {})
            pf_status = str(dv.get("prefilled_consistency_status", "NOT_AVAILABLE")).upper()
            if pf_status == "CONSISTENT":
                pf_status = "PASS"
            pf_match = dv.get("prefilled_match_count", 0)
            pf_mismatch = dv.get("prefilled_mismatch_count", 0)
            c = "#66bb6a" if pf_status == "PASS" else "#ef5350" if pf_mismatch > 0 else "#546e7a"
            st.markdown(
                f'<div style="padding:0.45rem 0.9rem;border-radius:6px;background:{c}15;border-left:3px solid {c};font-size:0.85rem">'
                f'{pf_status} — ✅ {pf_match} match · ⚠️ {pf_mismatch} mismatch</div>',
                unsafe_allow_html=True,
            )
            mm = list(dv.get("prefilled_mismatches") or [])
            if mm:
                st.dataframe(pd.DataFrame(mm), use_container_width=True, hide_index=True)

        with lt2:
            _section("Validation Results")
            overall = validation_out.get("overall_status", "—")
            rule_set = validation_out.get("rule_set_id", "—")
            vc = "#66bb6a" if overall == "PASS" else "#ef5350" if overall == "FAIL" else "#ffb74d"
            st.markdown(
                f'<div style="padding:0.5rem 0.9rem;border-radius:6px;background:{vc}18;border:1px solid {vc};font-weight:700;font-size:0.9rem">'
                f'{overall} · Rule Set: <code>{rule_set}</code></div>',
                unsafe_allow_html=True,
            )
            fr = validation_out.get("field_results") or []
            if fr:
                st.dataframe(pd.DataFrame(fr), use_container_width=True, hide_index=True)
                failed = [r for r in fr if str(r.get("status", "")).upper() in {"FAIL", "FAILED", "ERROR"}]
                if failed:
                    st.markdown(f'<div class="alert-warn">⚠️ {len(failed)} field(s) failed validation.</div>', unsafe_allow_html=True)

        with lt3:
            _section("Stamp, Seal & Signature Detection")
            auth_score = visual_out.get("visual_authenticity_score")
            if auth_score is not None:
                _confidence_bar(float(auth_score), "Visual Authenticity")
            markers = visual_out.get("markers") or []
            if markers:
                st.dataframe(pd.DataFrame(markers), use_container_width=True, hide_index=True)
                for m in markers:
                    mt = str(m.get("type") or m.get("marker_type") or "MARKER")
                    present = m.get("present") or m.get("detected")
                    pos = m.get("position") or m.get("bounding_box")
                    ic = "✅" if present else "❌"
                    line = f"{ic} **{mt}**"
                    if pos:
                        line += f" · `{pos}`"
                    st.markdown(line)
            else:
                st.caption("No marker data — visual authenticity score only.")
            forensics = visual_out.get("image_forensics") or {}
            tamper = forensics.get("tamper_signals", [])
            if tamper:
                st.markdown(f'<div class="alert-warn">🔴 {len(tamper)} tamper signal(s).</div>', unsafe_allow_html=True)
                for t_item in tamper:
                    st.markdown(f"- ⚠️ {t_item}")
            else:
                st.markdown('<div class="alert-success">✅ No tamper signals.</div>', unsafe_allow_html=True)

        with lt4:
            _section("Explainability")
            st.caption("AI-generated reasons to help officers understand the verification outcome.")
            doc_exp = explainability.get("document_explanations", [])
            field_exp = explainability.get("field_explanations", [])
            if doc_exp:
                for i, r in enumerate(doc_exp, 1):
                    st.markdown(f"{i}. {r}")
            else:
                st.caption("No document-level explanations.")
            if field_exp:
                _section("Field-Level Reasons")
                if isinstance(field_exp, list) and field_exp and isinstance(field_exp[0], dict):
                    st.dataframe(pd.DataFrame(field_exp), use_container_width=True, hide_index=True)
                else:
                    st.write(field_exp)

            _section("Citizen-Facing Preview")
            decision = selected_doc.get("decision") or "PENDING"
            reasons = "; ".join(str(r) for r in doc_exp[:3]) if doc_exp else "Review in progress."
            st.text_area(
                "Plain-language summary",
                value=f"Status: {decision}. Reason(s): {reasons}",
                height=65,
                disabled=True,
            )

    with right_col:
        _section("✅ Review Actions")
        ingestion = dict((selected_doc.get("metadata") or {}).get("ingestion") or {})
        submitted_by = dict(ingestion.get("submitted_by") or {})
        st.markdown(f"**Document ID:** `{doc_id}`")
        st.markdown(f"**Current State:** `{current_state}`")
        st.markdown(f"**Citizen ID:** `{selected_doc.get('citizen_id', '—')}`")
        st.markdown(f"**Submitted By:** `{submitted_by.get('actor_id', '—')}`")
        st.markdown(f"**Received At:** `{ingestion.get('received_at', '—')}`")

        verifier_notes = st.text_area(
            "Verifier Notes",
            placeholder="Required for Reject and Return to Citizen",
            height=90,
            key=f"wb_notes_{doc_id}",
        )

        if not can_review:
            st.markdown('<div class="alert-warn">🔒 Decision actions are disabled for your role.</div>', unsafe_allow_html=True)
        else:
            start_allowed = current_state in {"WAITING_FOR_REVIEW", "DISPUTED"}
            decision_allowed = current_state == "REVIEW_IN_PROGRESS"

            if st.button("▶️ Start Review", use_container_width=True, key=f"wb_start_{doc_id}", disabled=not start_allowed):
                try:
                    out = service.start_review(doc_id, tenant_id, officer_id, review_level="L1")
                    st.markdown(f'<div class="alert-success">Review started → <strong>{out.get("state")}</strong></div>', unsafe_allow_html=True)
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))

            if st.button("✅ Approve", use_container_width=True, key=f"wb_approve_{doc_id}", disabled=not decision_allowed):
                try:
                    out = service.manual_decision(doc_id, "APPROVE", tenant_id, officer_id)
                    st.markdown(f'<div class="alert-success">✅ {out.get("decision")} · {out.get("state")}</div>', unsafe_allow_html=True)
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))

            if st.button("❌ Reject", use_container_width=True, key=f"wb_reject_{doc_id}", disabled=not decision_allowed):
                if not verifier_notes.strip():
                    st.error("Verifier notes are required for rejection.")
                else:
                    try:
                        out = service.manual_decision(
                            doc_id,
                            "REJECT",
                            tenant_id,
                            officer_id,
                            reason=f"REJECTED:{verifier_notes.strip()[:120]}",
                        )
                        st.markdown(f'<div class="alert-warn">❌ {out.get("decision")} · {out.get("state")}</div>', unsafe_allow_html=True)
                        st.rerun()
                    except Exception as exc:
                        st.error(str(exc))

            if st.button("🚩 Escalate", use_container_width=True, key=f"wb_escalate_{doc_id}"):
                try:
                    res = service.flag_internal_disagreement(
                        document_id=doc_id,
                        tenant_id=tenant_id,
                        officer_id=officer_id,
                        reason=verifier_notes.strip() or "REVIEW_ESCALATION",
                    )
                    st.markdown(
                        f'<div class="alert-warn">Escalated: <code>{res["escalation"].get("id")}</code></div>',
                        unsafe_allow_html=True,
                    )
                except Exception as exc:
                    st.error(str(exc))

            if st.button("↩️ Return to Citizen", use_container_width=True, key=f"wb_return_{doc_id}", disabled=not decision_allowed):
                if not verifier_notes.strip():
                    st.error("Verifier notes are required to return to citizen.")
                else:
                    try:
                        out = service.manual_decision(
                            doc_id,
                            "REJECT",
                            tenant_id,
                            officer_id,
                            reason=f"RETURNED_TO_CITIZEN:{verifier_notes.strip()[:120]}",
                        )
                        try:
                            service.notify(doc_id, tenant_id, officer_id)
                        except Exception:
                            pass
                        st.markdown(
                            f'<div class="alert-warn">↩️ Returned to citizen · {out.get("state")}</div>',
                            unsafe_allow_html=True,
                        )
                        st.rerun()
                    except Exception as exc:
                        st.error(str(exc))

        _section("Field Correction")
        st.caption("Corrections are logged immutably and feed the ML training pipeline.")
        with st.form(f"wb_correction_{doc_id}"):
            cc1, cc2 = st.columns(2)
            with cc1:
                corr_field = st.text_input("Field", value="TOTAL_MARKS")
                corr_old = st.text_input("Old value", value="580")
            with cc2:
                corr_new = st.text_input("Corrected value", value="560")
                corr_reason = st.text_input("Reason", value="Per physical document")
            if st.form_submit_button("📝 Log Correction", use_container_width=True):
                try:
                    gate = service.record_field_correction(
                        document_id=doc_id,
                        tenant_id=tenant_id,
                        officer_id=officer_id,
                        field_name=corr_field.strip(),
                        old_value=corr_old.strip() or None,
                        new_value=corr_new.strip() or None,
                        reason=corr_reason.strip() or "FIELD_CORRECTION",
                    )
                    st.markdown(
                        f'<div class="alert-success">✅ Gate: <strong>{gate["gate"].get("status")}</strong> — '
                        f'<span class="diff-old">{corr_old}</span> → <span class="diff-new">{corr_new}</span></div>',
                        unsafe_allow_html=True,
                    )
                except Exception as exc:
                    st.error(str(exc))


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: Dispute Desk
# ══════════════════════════════════════════════════════════════════════════════

def _render_dispute_desk(*, role: str, tenant_id: str, officer_id: str, selected_doc: dict[str, Any] | None) -> None:
    _render_journey("Dispute Resolution", ["Appeal", "DISPUTED", "Senior Review", "Resolution", "Archive"], active_index=0)
    try:
        disputes = service.list_disputes(tenant_id, officer_id)
        if disputes:
            open_ct = len([d for d in disputes if str(d.get("status", "")) not in {"RESOLVED", "CLOSED"}])
            st.metric("Open Disputes", open_ct)
            st.dataframe(pd.DataFrame(disputes), use_container_width=True, hide_index=True)
        else:
            st.markdown('<div class="alert-success">✅ No disputes.</div>', unsafe_allow_html=True)
    except Exception as exc:
        st.error(str(exc))

    if selected_doc and role in WRITE_ROLES:
        _section("Open Dispute")
        with st.form("dispute_form"):
            reason = st.text_input("Reason", value="Citizen requests re-verification")
            note = st.text_input("Evidence note", value="Supporting reference attached")
            if st.form_submit_button("📩 Submit", use_container_width=True):
                try:
                    row = service.open_dispute(str(selected_doc.get("id")), reason, note, tenant_id, officer_id)
                    st.markdown(f'<div class="alert-success">Dispute: <code>{row["id"]}</code></div>', unsafe_allow_html=True)
                except Exception as exc:
                    st.error(str(exc))

    if role in SENIOR_ROLES and selected_doc:
        _section("🎖️ Senior Resolution")
        ireason = st.text_input("Disagreement reason", value="Officer assessments conflict", key="disp_ir")
        if st.button("🚨 Flag Disagreement", use_container_width=True):
            try:
                res = service.flag_internal_disagreement(
                    document_id=str(selected_doc.get("id")), tenant_id=tenant_id,
                    officer_id=officer_id, reason=ireason.strip() or "CONFLICT",
                )
                st.markdown(f'<div class="alert-warn">Escalated: <code>{res["escalation"].get("id")}</code></div>', unsafe_allow_html=True)
            except Exception as exc:
                st.error(str(exc))


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: Fraud & Authenticity
# ══════════════════════════════════════════════════════════════════════════════

def _render_fraud_authenticity(*, selected_doc: dict[str, Any] | None, selected_record: dict[str, Any]) -> None:
    _render_journey("Fraud Escalation", ["High Risk", "Fraud Desk", "Decision Support", "Audit"], active_index=0)
    if not selected_doc:
        st.markdown('<div class="alert-info">Select a document to view fraud signals.</div>', unsafe_allow_html=True)
        return

    fraud_out = dict(selected_record.get("fraud_risk_output") or {})
    visual_out = dict(selected_record.get("visual_authenticity_output") or {})
    issuer_out = dict(selected_record.get("issuer_verification_output") or {})

    agg = fraud_out.get("aggregate_fraud_risk_score")
    rl = str(fraud_out.get("risk_level", "UNKNOWN")).upper()

    c1, c2 = st.columns([1, 3])
    with c1:
        st.markdown(
            f'<div style="padding:1.2rem;text-align:center;border-radius:10px;background:#0d1b2a;border:1px solid #1e3a5f">'
            f'<div style="font-size:0.68rem;color:#546e7a;text-transform:uppercase">Risk Level</div>'
            f'<div style="margin:0.4rem 0">{_risk_badge(rl)}</div>'
            f'<div style="font-size:1.3rem;font-weight:700;color:#4fc3f7">{"—" if agg is None else f"{float(agg):.3f}"}</div>'
            f'</div>', unsafe_allow_html=True)
    with c2:
        if agg is not None:
            _confidence_bar(1.0 - float(agg), "Safety Score")
        a_score = visual_out.get("visual_authenticity_score")
        if a_score is not None:
            _confidence_bar(float(a_score), "Visual Authenticity")

    ft1, ft2, ft3, ft4 = st.tabs(["⚠️ Signals", "🔏 Tamper", "🏛️ Issuer", "💡 Guidance"])

    with ft1:
        components = fraud_out.get("components") or {}
        if components:
            st.dataframe(pd.DataFrame(_to_table_rows(components, "component", "details")), use_container_width=True, hide_index=True)
        behavioral = (components.get("behavioral_component") or {})
        for sig in behavioral.get("signals", []):
            st.markdown(f"- 🔴 {sig}")

    with ft2:
        forensics = visual_out.get("image_forensics") or {}
        tamper = forensics.get("tamper_signals", [])
        if tamper:
            st.markdown(f'<div class="alert-warn">🔴 {len(tamper)} tamper signal(s).</div>', unsafe_allow_html=True)
            for t_item in tamper:
                st.markdown(f"- {t_item}")
        else:
            st.markdown('<div class="alert-success">✅ Clean.</div>', unsafe_allow_html=True)
        for item in forensics.get("layout_inconsistencies", []):
            st.markdown(f"- ⚡ {item}")
        markers = visual_out.get("markers") or []
        if markers:
            st.dataframe(pd.DataFrame(markers), use_container_width=True, hide_index=True)

    with ft3:
        issuer_status = str(issuer_out.get("status") or issuer_out.get("registry_status") or "UNKNOWN").upper()
        cm = {"VERIFIED": "#66bb6a", "MISMATCH": "#ef5350", "NOT_FOUND": "#ef5350", "UNVERIFIED": "#ffb74d", "ERROR": "#ef5350"}
        ic = cm.get(issuer_status, "#546e7a")
        st.markdown(f'<div style="padding:0.5rem 0.9rem;border-radius:6px;background:{ic}18;border-left:3px solid {ic};font-weight:700">{issuer_status}</div>', unsafe_allow_html=True)
        if issuer_out:
            st.write(issuer_out)

    with ft4:
        _section("Operator Guidance")
        guidance = []
        if rl in {"HIGH", "CRITICAL"}:
            guidance.append("🚨 Escalate to Fraud Desk before approval.")
        if tamper:
            guidance.append("🔍 Inspect tamper regions against original scan.")
        if issuer_status in {"MISMATCH", "UNVERIFIED", "NOT_FOUND", "ERROR"}:
            guidance.append("📋 Request alternate issuer proof.")
        if not guidance:
            guidance.append("✅ No critical signals. Continue standard review.")
        for g in guidance:
            st.markdown(f"- {g}")


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: Citizen Communication
# ══════════════════════════════════════════════════════════════════════════════

def _render_citizen_communication(*, role: str, tenant_id: str, officer_id: str, selected_doc: dict[str, Any] | None) -> None:
    _render_journey("Citizen Notifications", ["Received", "Updates", "Decision", "Reasons", "Next Steps"], active_index=0)
    st.caption("Backend operator view of citizen-facing notifications.")

    try:
        notifs = service.list_notifications(tenant_id, officer_id)
        if notifs:
            st.dataframe(pd.DataFrame(notifs), use_container_width=True, hide_index=True)
        else:
            st.caption("No notifications yet.")
    except Exception as exc:
        st.error(str(exc))

    if selected_doc and (role in WRITE_ROLES or role in REVIEW_ROLES):
        if st.button("📣 Send Notification", use_container_width=True):
            try:
                service.notify(str(selected_doc.get("id")), tenant_id, officer_id)
                st.markdown('<div class="alert-success">✅ Sent.</div>', unsafe_allow_html=True)
            except Exception as exc:
                st.error(str(exc))

    if selected_doc:
        _section("Citizen Case View")
        lookup = st.text_input("Citizen ID", value=str(selected_doc.get("citizen_id", "")), key="cit_lk")
        if st.button("Load Case", use_container_width=True):
            try:
                view = service.get_citizen_case_view(tenant_id, str(selected_doc.get("id")), lookup.strip())
                c1, c2 = st.columns(2)
                c1.metric("Status", view.get("state", "—"))
                c2.metric("Decision", view.get("decision", "—"))
                st.info(view.get("explanation_text", "No explanation."))
                st.info(view.get("next_steps", "No further steps."))
            except Exception as exc:
                st.error(str(exc))


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: Audit Trail
# ══════════════════════════════════════════════════════════════════════════════

def _render_audit_trail(*, tenant_id: str, officer_id: str, selected_doc: dict[str, Any] | None, selected_record: dict[str, Any]) -> None:
    if not selected_doc:
        st.markdown('<div class="alert-info">Select a document to view its audit trail.</div>', unsafe_allow_html=True)
        return

    t1, t2, t3, t4 = st.tabs(["🗂️ States", "📅 Events", "🤖 Models", "👤 Overrides"])

    with t1:
        history = (selected_record.get("state_machine") or {}).get("history") or []
        if history:
            st.dataframe(pd.DataFrame(history), use_container_width=True, hide_index=True)
            _render_journey("Progression", [str(h.get("to_state") or h.get("state") or h.get("to") or h) for h in history])
        else:
            st.caption("No state history.")

    with t2:
        try:
            events = service.list_events(str(selected_doc.get("id")), tenant_id, officer_id)
            if events:
                st.dataframe(pd.DataFrame(events), use_container_width=True, hide_index=True)
            else:
                st.caption("No events.")
        except Exception as exc:
            st.error(str(exc))

    with t3:
        for label, key, sub in [("OCR", "ocr_output", "model_metadata"), ("Classification", "classification_output", "model_metadata"),
                                  ("Validation", "validation_output", "model_metadata")]:
            val = (selected_record.get(key) or {}).get(sub)
            st.markdown(f"**{label}:** `{val or '—'}`")
        st.markdown(f"**Rule Set:** `{(selected_record.get('validation_output') or {}).get('rule_set_id', '—')}`")

        _section("AI Audit Logs")
        try:
            logs = service.list_model_audit_logs(tenant_id, officer_id, document_id=str(selected_doc.get("id")))
            if logs:
                st.dataframe(pd.DataFrame(logs), use_container_width=True, hide_index=True)
            else:
                st.caption("No audit logs.")
        except Exception as exc:
            st.error(str(exc))

    with t4:
        rev = ((selected_record.get("human_review") or {}).get("review_events")) or []
        if rev:
            st.dataframe(pd.DataFrame(rev), use_container_width=True, hide_index=True)
        else:
            st.caption("No human overrides.")
        with st.expander("Full document_record JSON", expanded=False):
            st.json(selected_record)


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: Governance & KPI
# ══════════════════════════════════════════════════════════════════════════════

def _render_governance_kpi(*, role: str, tenant_id: str, officer_id: str) -> None:
    _render_journey("Governance Cycle", ["Review", "KPI Assessment", "Policy Update", "Sign-off"], active_index=0)
    is_read_only = role in AUDIT_ROLES and role not in SENIOR_ROLES
    if is_read_only:
        st.markdown(
            '<div class="alert-info">🔎 Read-only mode. '
            'You can view all governance data but cannot make changes.</div>',
            unsafe_allow_html=True,
        )

    gk1, gk2, gk3, gk4 = st.tabs(["📈 KPIs", "📜 Policy", "🗂️ Templates & Rules", "👥 Users"])

    with gk1:
        try:
            kpi = governance.get_kpi_dashboard(tenant_id, officer_id)
            if isinstance(kpi, dict):
                cards = [{"label": k, "value": v} for k, v in kpi.items() if not isinstance(v, (dict, list))]
                if cards:
                    _render_kpi_row(cards[:6])
            with st.expander("Full KPI data", expanded=False):
                st.json(kpi)
        except Exception as exc:
            st.error(str(exc))

        try:
            snapshot = governance.get_tenant_governance_snapshot(tenant_id, officer_id)
            with st.expander("Governance snapshot", expanded=False):
                st.json(snapshot)
        except Exception as exc:
            st.error(str(exc))

        if role in SENIOR_ROLES and not is_read_only:
            _section("Record KPI")
            with st.form("kpi_snap"):
                k1, k2 = st.columns(2)
                with k1:
                    kpi_key = st.text_input("Key", value="tamper_detection_recall_pct")
                    measured = st.number_input("Value", value=86.0, step=0.1)
                with k2:
                    src = st.selectbox("Source", ["MANUAL", "AUDIT", "MONITORING"])
                    notes = st.text_area("Notes", value="Monthly QA", height=55)
                if st.form_submit_button("💾 Save", use_container_width=True):
                    try:
                        row = governance.record_kpi_snapshot(tenant_id=tenant_id, officer_id=officer_id, kpi_key=kpi_key.strip(), measured_value=float(measured), source=src, notes=notes.strip() or None)
                        st.markdown(f'<div class="alert-success">Saved: <code>{row["id"]}</code></div>', unsafe_allow_html=True)
                    except Exception as exc:
                        st.error(str(exc))

    with gk2:
        if role not in SENIOR_ROLES:
            _section("Data Retention (Read-Only)")
            st.json(governance.get_tenant_governance_snapshot(tenant_id, officer_id).get("data_policy", {}))
            _section("Partition Config (Read-Only)")
            st.json(governance.get_tenant_governance_snapshot(tenant_id, officer_id).get("partition_config", {}))
        else:
            _section("Data Retention")
            with st.form("policy_form"):
                p1, p2 = st.columns(2)
                with p1:
                    raw_y = st.number_input("Raw image (years)", 1, 30, 7, 1)
                    struct_y = st.number_input("Structured (years)", 1, 30, 10, 1)
                with p2:
                    fraud_y = st.number_input("Fraud logs (years)", 1, 30, 10, 1)
                    purge = st.selectbox("Purge", ["ANONYMIZE_AFTER_EXPIRY", "HARD_DELETE_AFTER_EXPIRY"])
                if st.form_submit_button("💾 Save", use_container_width=True):
                    try:
                        row = governance.update_tenant_data_policy(tenant_id, officer_id, {"raw_image_retention_years": int(raw_y), "structured_data_retention_years": int(struct_y), "fraud_logs_retention_years": int(fraud_y), "purge_policy": purge})
                        st.markdown(f'<div class="alert-success">Saved for <code>{row["tenant_id"]}</code></div>', unsafe_allow_html=True)
                    except Exception as exc:
                        st.error(str(exc))

            _section("Partition Config")
            with st.form("partition_form"):
                pa1, pa2 = st.columns(2)
                with pa1:
                    pm = st.selectbox("Mode", ["LOGICAL_SHARED", "DEDICATED_SCHEMA", "DEDICATED_CLUSTER", "DEDICATED_DEPLOYMENT"])
                    rr = st.text_input("Region", value="default")
                with pa2:
                    rc = st.text_input("Cluster", value="region-a")
                    pi = st.checkbox("Physical isolation", value=False)
                if st.form_submit_button("💾 Save", use_container_width=True):
                    try:
                        row = governance.update_tenant_partition_config(tenant_id, officer_id, {"partition_mode": pm, "residency_region": rr.strip() or "default", "region_cluster": rc.strip() or "region-a", "physical_isolation_required": bool(pi)})
                        st.markdown(f'<div class="alert-success">Partition: <code>{row["partition_mode"]}</code></div>', unsafe_allow_html=True)
                    except Exception as exc:
                        st.error(str(exc))

    with gk3:
        if role not in SENIOR_ROLES:
            st.write({"templates": service.repo.list_tenant_templates(tenant_id), "rules": service.repo.list_tenant_rules(tenant_id)})
        else:
            _section("Templates")
            try:
                tpls = service.list_tenant_templates(tenant_id, officer_id)
                if tpls:
                    st.dataframe(pd.DataFrame(tpls), use_container_width=True, hide_index=True)
            except Exception:
                pass

            with st.form("tpl_form"):
                t1c, t2c = st.columns(2)
                with t1c:
                    dt = st.selectbox("Doc type", DOC_TYPE_OPTIONS[1:], index=0)
                    tid = st.text_input("Template ID", value="aadhaar_template_default")
                    tv = st.text_input("Version", value="2025.1.0")
                with t2c:
                    tsv = st.number_input("Config ver", 1, value=1, step=1)
                    rsr = st.text_input("Rule set", value="RULESET_AADHAAR_DEFAULT")
                    lc = st.selectbox("Lifecycle", ["ACTIVE", "DEPRECATED", "RETIRED"])
                active = st.checkbox("Active", value=True)
                cfg = st.text_area("Config JSON", value='{"fields":[],"visual_markers":[]}', height=70)
                if st.form_submit_button("💾 Save Template", use_container_width=True):
                    try:
                        row = service.save_tenant_template(tenant_id=tenant_id, officer_id=officer_id, document_type=str(dt), template_id=tid.strip(), version=int(tsv), template_version=tv.strip(), policy_rule_set_id=rsr.strip() or None, config=json.loads(cfg) if cfg.strip() else {}, lifecycle_status=lc, is_active=active)
                        st.markdown(f'<div class="alert-success">Template: <code>{row.get("template_id")}</code></div>', unsafe_allow_html=True)
                    except Exception as exc:
                        st.error(str(exc))

            _section("Rules")
            try:
                rules = service.list_tenant_rules(tenant_id, officer_id)
                if rules:
                    st.dataframe(pd.DataFrame(rules), use_container_width=True, hide_index=True)
            except Exception:
                pass

            with st.form("rule_form"):
                r1, r2 = st.columns(2)
                with r1:
                    rdt = st.selectbox("Doc type", DOC_TYPE_OPTIONS[1:], index=0, key="rule_dt")
                    rn = st.text_input("Rule name", value="rule_aadhaar_default")
                    rsi = st.text_input("Rule set ID", value="RULESET_AADHAAR_DEFAULT")
                    rv = st.number_input("Version", 1, value=1, step=1, key="rule_v")
                with r2:
                    me = st.slider("Min extract conf", 0.0, 1.0, 0.6, 0.01)
                    ma = st.slider("Min approval conf", 0.0, 1.0, 0.72, 0.01)
                    mr = st.slider("Max risk", 0.0, 1.0, 0.35, 0.01)
                rr_req = st.checkbox("Registry required", True)
                ra = st.checkbox("Active rule", True)
                rcfg = st.text_area("Rule config", value='{"checks":[]}', height=65)
                if st.form_submit_button("💾 Save Rule", use_container_width=True):
                    try:
                        row = service.save_tenant_rule(tenant_id=tenant_id, officer_id=officer_id, document_type=str(rdt), rule_name=rn.strip(), version=int(rv), rule_set_id=rsi.strip(), min_extract_confidence=float(me), min_approval_confidence=float(ma), max_approval_risk=float(mr), registry_required=bool(rr_req), config=json.loads(rcfg) if rcfg.strip() else {}, is_active=bool(ra))
                        st.markdown(f'<div class="alert-success">Rule: <code>{row.get("rule_name")}</code></div>', unsafe_allow_html=True)
                    except Exception as exc:
                        st.error(str(exc))

            if st.button("🌱 Seed Part-5 Baseline", use_container_width=True):
                try:
                    st.success(str(governance.seed_part5_baseline(tenant_id, officer_id)))
                except Exception as exc:
                    st.error(str(exc))

    with gk4:
        if role not in SENIOR_ROLES:
            _section("Officers (Read-Only)")
            try:
                officers = service.list_officers(tenant_id, officer_id)
                if officers:
                    st.dataframe(pd.DataFrame(officers), use_container_width=True, hide_index=True)
                else:
                    st.caption("No officers configured.")
            except Exception as exc:
                st.error(str(exc))
        else:
            _section("Officers")
            try:
                officers = service.list_officers(tenant_id, officer_id)
                if officers:
                    st.dataframe(pd.DataFrame(officers), use_container_width=True, hide_index=True)
            except Exception:
                pass
            with st.form("officer_form"):
                o1, o2 = st.columns(2)
                with o1:
                    oid = st.text_input("Officer ID", value="officer-new-001")
                    orole = st.selectbox(
                        "Role",
                        [ROLE_VERIFIER, ROLE_SENIOR_VERIFIER, ROLE_AUDITOR, ROLE_PLATFORM_ADMIN],
                        format_func=lambda r: ROLE_META.get(r, {}).get("label", r),
                    )
                with o2:
                    ost = st.selectbox("Status", ["ACTIVE", "INACTIVE"])
                if st.form_submit_button("💾 Save", use_container_width=True):
                    try:
                        row = service.upsert_officer_account(tenant_id=tenant_id, admin_officer_id=officer_id, target_officer_id=oid.strip(), role=orole, status=ost)
                        st.markdown(f'<div class="alert-success"><code>{row.get("officer_id")}</code> ({row.get("role")})</div>', unsafe_allow_html=True)
                    except Exception as exc:
                        st.error(str(exc))

        if role in PLATFORM_ROLES:
            _section("🌐 Platform View")
            if st.button("Load Cross-Tenant Overview", use_container_width=True):
                try:
                    st.json(governance.cross_tenant_audit_overview(officer_id))
                except Exception as exc:
                    st.error(str(exc))


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: Ops Monitor
# ══════════════════════════════════════════════════════════════════════════════

def _render_ops_monitor(*, role: str, tenant_id: str, officer_id: str) -> None:
    is_read_only = role in AUDIT_ROLES and role not in SENIOR_ROLES
    if is_read_only:
        st.markdown(
            '<div class="alert-info">🔎 Read-only mode. '
            "You can view operational metrics but cannot trigger actions.</div>",
            unsafe_allow_html=True,
        )

    try:
        dashboard = service.monitoring_dashboard(tenant_id, officer_id)
        mlops = dashboard.get("mlops") or {}
        _render_kpi_row([
            {"label": "Throughput", "value": mlops.get("throughput_docs", "—")},
            {"label": "Latency (ms)", "value": mlops.get("avg_latency_ms", "—")},
            {"label": "DR", "value": service.dr_service.describe()},
            {"label": "Webhooks", "value": len(service.list_webhook_outbox(tenant_id, officer_id, status="PENDING"))},
        ])
    except Exception as exc:
        st.error(str(exc))

    ot1, ot2, ot3 = st.tabs(["📊 Health", "🤖 ML", "🏗️ Actions"])

    with ot1:
        try:
            docs = service.list_documents(tenant_id, officer_id)
            events = service.list_tenant_events(tenant_id, officer_id)
            failed = [e for e in events if str(e.get("event_type")) == "document.failed"]
            fr = round(len(failed) * 100.0 / max(1, len(docs)), 2)
            _render_kpi_row([
                {"label": "Docs", "value": len(docs)},
                {"label": "Failed", "value": len(failed), "delta_good": len(failed) == 0},
                {"label": "Fail %", "value": f"{fr}%", "delta_good": fr < 5},
                {"label": "Waiting", "value": len([d for d in docs if str(d.get("state")) == "WAITING_FOR_REVIEW"])},
            ])
        except Exception as exc:
            st.error(str(exc))
        if role in PLATFORM_ROLES:
            _section("All Tenants")
            tenants = service.repo.list_platform_tenants()
            if tenants:
                st.dataframe(pd.DataFrame(tenants), use_container_width=True, hide_index=True)

    with ot2:
        try:
            cp = len(service.repo.list_correction_gate_records(tenant_id, status="PENDING_QA"))
            ca = len(service.repo.list_correction_gate_records(tenant_id, status="TRAINING_APPROVED"))
            metrics = service.repo.list_module_metrics(tenant_id=tenant_id, limit=1000)
            ok = len([m for m in metrics if str(m.get("status")) == "OK"])
            mh = round(ok * 100.0 / max(1, len(metrics)), 2)
            _render_kpi_row([
                {"label": "QA Pending", "value": cp},
                {"label": "Approved", "value": ca},
                {"label": "Module OK %", "value": f"{mh}%", "delta_good": mh > 90},
            ])
        except Exception as exc:
            st.error(str(exc))

    with ot3:
        if role in SENIOR_ROLES and not is_read_only:
            c1, c2 = st.columns(2)
            with c1:
                if st.button("⏰ Enforce SLA", use_container_width=True):
                    try:
                        esc = service.enforce_review_sla(tenant_id, officer_id)
                        st.markdown(f'<div class="alert-success">Escalations: <strong>{len(esc)}</strong></div>', unsafe_allow_html=True)
                    except Exception as exc:
                        st.error(str(exc))
            with c2:
                if st.button("🗑️ Retention Lifecycle", use_container_width=True):
                    try:
                        st.success(str(service.apply_retention_lifecycle(tenant_id, officer_id)))
                    except Exception as exc:
                        st.error(str(exc))
        else:
            st.caption("Action controls are disabled for your role.")


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: Integrations
# ══════════════════════════════════════════════════════════════════════════════

def _render_integrations(*, role: str, tenant_id: str, officer_id: str) -> None:
    it1, it2, it3 = st.tabs(["📡 API", "📤 Webhooks", "📁 Export"])

    with it1:
        endpoints = [
            {"Method": "POST", "Path": "/documents", "Purpose": "Ingest"},
            {"Method": "POST", "Path": "/documents/{id}/process", "Purpose": "Pipeline"},
            {"Method": "GET",  "Path": "/documents/{id}/status", "Purpose": "Status"},
            {"Method": "GET",  "Path": "/documents/{id}/result", "Purpose": "Result"},
            {"Method": "GET",  "Path": "/documents/{id}/events", "Purpose": "Audit"},
            {"Method": "POST", "Path": "/tenants/{id}/offline/sync", "Purpose": "Sync"},
        ]
        st.dataframe(pd.DataFrame(endpoints), use_container_width=True, hide_index=True)

        if role in SENIOR_ROLES:
            _section("API Key")
            with st.form("api_key"):
                a1, a2 = st.columns(2)
                with a1:
                    label = st.text_input("Label", value="backend-service")
                with a2:
                    raw_key = st.text_input("Key", value="change-me", type="password")
                if st.form_submit_button("🔑 Create", use_container_width=True):
                    try:
                        row = service.create_tenant_api_key(tenant_id, officer_id, label.strip(), raw_key.strip())
                        st.markdown(f'<div class="alert-success">Key: <code>{row.get("key_label")}</code></div>', unsafe_allow_html=True)
                    except Exception as exc:
                        st.error(str(exc))

    with it2:
        try:
            outbox = service.list_webhook_outbox(tenant_id, officer_id, status="PENDING")
            if outbox:
                st.dataframe(pd.DataFrame(outbox), use_container_width=True, hide_index=True)
            else:
                st.markdown('<div class="alert-success">✅ No pending webhooks.</div>', unsafe_allow_html=True)
        except Exception as exc:
            st.error(str(exc))

    with it3:
        if role in SENIOR_ROLES:
            raw = st.checkbox("Include raw text", False)
            if st.button("📦 Generate CSV", use_container_width=True):
                try:
                    csv_data = service.batch_export_documents(tenant_id, officer_id, include_raw_text=raw)
                    if csv_data:
                        st.download_button("⬇️ Download", data=csv_data, file_name=f"{tenant_id}_export.csv", mime="text/csv", use_container_width=True)
                    else:
                        st.caption("No data.")
                except Exception as exc:
                    st.error(str(exc))


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: Offline Sync
# ══════════════════════════════════════════════════════════════════════════════

def _render_offline_sync(*, role: str, tenant_id: str, officer_id: str) -> None:
    _render_journey("Offline Sync", ["Capture", "Queue", "Connect", "Sync", "Reprocess", "Resolve"], active_index=0)
    can_write = role in WRITE_ROLES

    ot1, ot2 = st.tabs(["📝 Create", "🔄 Sync"])

    with ot1:
        if not can_write:
            st.info("🔒 Write role required.")
        else:
            with st.form("offline_form"):
                c1, c2 = st.columns(2)
                with c1:
                    cit = st.text_input("Citizen ID", value="citizen-offline-001")
                    fn = st.text_input("File name", value="offline_doc.txt")
                with c2:
                    pd_sel = st.selectbox("Provisional decision", ["VALID", "REVIEW", "REJECT"], index=1)
                    mv = st.text_area("Model versions (JSON)", value='{"ocr_model_id":"ocr-lite-v1"}', height=55)
                rt = st.text_area("Text", value="Offline captured text", height=80)
                if st.form_submit_button("📴 Create", use_container_width=True):
                    try:
                        out = offline_service.create_offline_provisional(
                            tenant_id=tenant_id, citizen_id=cit.strip(), file_name=fn.strip(),
                            raw_text=rt, officer_id=officer_id,
                            local_model_versions=json.loads(mv) if mv.strip() else {},
                            provisional_decision=pd_sel,
                            metadata={"source": "SERVICE_CENTER", "offline_node_id": "center-01"},
                        )
                        st.markdown(f'<div class="alert-success">Created: <code>{out.get("id")}</code></div>', unsafe_allow_html=True)
                    except Exception as exc:
                        st.error(str(exc))

    with ot2:
        pending = service.repo.list_pending_offline_documents(tenant_id, limit=500)
        if pending:
            st.metric("Pending", len(pending))
            st.dataframe(pd.DataFrame(pending), use_container_width=True, hide_index=True)
        else:
            st.markdown('<div class="alert-success">✅ Queue empty.</div>', unsafe_allow_html=True)

        if can_write and pending:
            cap = st.number_input("Capacity", 1, 500, 50, 1)
            if st.button("🔄 Sync Now", use_container_width=True):
                ids = [str(r.get("id")) for r in pending if r.get("id")]
                try:
                    bp = offline_service.apply_sync_backpressure(tenant_id=tenant_id, officer_id=officer_id, pending_document_ids=ids, sync_capacity_per_minute=int(cap))
                    synced = 0
                    fails: list[dict] = []
                    prog = st.progress(0)
                    for i, did in enumerate(ids[:int(cap)]):
                        try:
                            offline_service.sync_offline_document(tenant_id=tenant_id, document_id=did, officer_id=officer_id)
                            synced += 1
                        except Exception as exc:
                            fails.append({"id": did, "error": str(exc)})
                        prog.progress((i + 1) / max(1, min(len(ids), int(cap))))
                    st.markdown(f'<div class="alert-success">Synced: {synced} · Failed: {len(fails)} · Overflow: {bp.get("queue_overflow")}</div>', unsafe_allow_html=True)
                    if fails:
                        st.dataframe(pd.DataFrame(fails), use_container_width=True, hide_index=True)
                except Exception as exc:
                    st.error(str(exc))


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: ML Training
# ══════════════════════════════════════════════════════════════════════════════

def _render_ml_training(*, role: str, tenant_id: str, officer_id: str) -> None:
    _render_journey("Learning Loop", ["Correction", "Gate", "QA", "Approved", "Retrain", "Deploy"], active_index=0)
    st.caption("Officer corrections feed into ML training through a QA gate.")
    is_read_only = role in AUDIT_ROLES and role not in SENIOR_ROLES
    if is_read_only:
        st.markdown(
            '<div class="alert-info">🔎 Read-only mode. '
            "You can view ML metrics and QA queues but cannot change training config.</div>",
            unsafe_allow_html=True,
        )

    mt1, mt2, mt3 = st.tabs(["🔍 Gate Queue", "📊 Performance", "⚙️ Config"])

    with mt1:
        try:
            pq = service.repo.list_correction_gate_records(tenant_id, status="PENDING_QA")
            if pq:
                st.metric("Pending QA", len(pq))
                st.dataframe(pd.DataFrame(pq), use_container_width=True, hide_index=True)
            else:
                st.markdown('<div class="alert-success">✅ No pending QA.</div>', unsafe_allow_html=True)
        except Exception as exc:
            st.error(str(exc))

        _section("Approved")
        try:
            ap = service.repo.list_correction_gate_records(tenant_id, status="TRAINING_APPROVED")
            if ap:
                st.dataframe(pd.DataFrame(ap), use_container_width=True, hide_index=True)
            else:
                st.caption("None yet.")
        except Exception as exc:
            st.error(str(exc))

    with mt2:
        try:
            metrics = service.repo.list_module_metrics(tenant_id=tenant_id, limit=1000)
            if metrics:
                df_m = pd.DataFrame(metrics)
                st.dataframe(df_m, use_container_width=True, hide_index=True)
                if "status" in df_m.columns:
                    ok_pct = round(df_m["status"].value_counts().get("OK", 0) * 100 / max(1, len(df_m)), 1)
                    _render_kpi_row([{"label": "Total", "value": len(df_m)}, {"label": "OK %", "value": f"{ok_pct}%", "delta_good": ok_pct > 90}])
        except Exception as exc:
            st.error(str(exc))

        _section("OCR Trend")
        try:
            logs = service.repo.list_model_audit_logs(tenant_id=tenant_id)
            rows = []
            for r in logs:
                if str(r.get("module_name")) == "ocr_multi_script":
                    try:
                        rows.append({"ts": str(r.get("created_at", ""))[:19], "conf": float((r.get("output") or {}).get("ocr_confidence", 0))})
                    except Exception:
                        pass
            if rows:
                st.line_chart(pd.DataFrame(rows).tail(100).set_index("ts")["conf"])
            else:
                st.caption("No OCR data yet.")
        except Exception as exc:
            st.error(str(exc))

    with mt3:
        try:
            docs = service.list_documents(tenant_id, officer_id)
            tf = []
            for d in docs[:200]:
                f = ((d.get("metadata") or {}).get("ml_training_flags") or {}).get("eligible_for_training", {})
                if isinstance(f, dict):
                    tf.append({k: bool(f.get(k, False)) for k in ["ocr", "classification", "extraction", "fraud"]})
            if tf:
                elig = {k: sum(1 for f in tf if f.get(k, False)) for k in ["ocr", "classification", "extraction", "fraud"]}
                _render_kpi_row([{"label": k.capitalize(), "value": v} for k, v in elig.items()])
                st.bar_chart(pd.DataFrame([elig]).T.rename(columns={0: "eligible"}))
        except Exception as exc:
            st.error(str(exc))

        if role in SENIOR_ROLES and not is_read_only:
            _section("Pipeline Config")
            st.markdown(
                "- **Gate threshold**: Per rule set in Governance → Rules\n"
                "- **Retrain trigger**: Batch size or drift threshold\n"
                "- **Versioning**: Auto-incremented; tracked in audit logs\n"
                "- **Rollback**: Via DR service"
            )


# ══════════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════════

AUTH_USER_ID_KEY = "auth_user_id"
AUTH_EMAIL_KEY = "auth_email"


def _auth_client() -> tuple[Any | None, str | None]:
    client = service.repo.client
    if client is not None:
        auth = getattr(client, "auth", None)
        if auth is not None:
            return auth, None

    fallback_client, fallback_error = get_supabase_client()
    if fallback_client is None:
        return None, fallback_error
    return getattr(fallback_client, "auth", None), fallback_error


def _extract_auth_identity(response: Any) -> tuple[str | None, str | None]:
    user = getattr(response, "user", None)
    if user is None and isinstance(response, dict):
        user = response.get("user")
    if user is None:
        session = getattr(response, "session", None)
        if session is not None:
            user = getattr(session, "user", None)
            if user is None and isinstance(session, dict):
                user = session.get("user")

    if user is None:
        return None, None

    if isinstance(user, dict):
        return str(user.get("id") or ""), str(user.get("email") or "")
    return str(getattr(user, "id", "") or ""), str(getattr(user, "email", "") or "")


def _role_label(role: str) -> str:
    return str(ROLE_META.get(role, {}).get("label") or role or "User")


def _default_user_name(input_name: str, email: str) -> str:
    cleaned = str(input_name or "").strip()
    if cleaned:
        return cleaned
    local = str(email or "").split("@", 1)[0].replace(".", " ").replace("_", " ").strip()
    return local.title() if local else "User"


def _extract_reset_link(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        candidate = value.strip()
        return candidate if candidate.startswith("http") else None
    if isinstance(value, dict):
        for key in ("action_link", "confirmation_url", "url", "recovery_link"):
            candidate = _extract_reset_link(value.get(key))
            if candidate:
                return candidate
        for nested_key in ("properties", "data"):
            candidate = _extract_reset_link(value.get(nested_key))
            if candidate:
                return candidate
        return None
    for attr in ("action_link", "confirmation_url", "url", "recovery_link", "properties", "data"):
        try:
            candidate = _extract_reset_link(getattr(value, attr, None))
            if candidate:
                return candidate
        except Exception:
            continue
    return None


def _generate_supabase_recovery_link(auth: Any, email: str) -> str | None:
    try:
        admin = getattr(auth, "admin", None)
        if admin is None:
            return None
        payload = {
            "type": "recovery",
            "email": email,
            "options": {"redirect_to": settings.password_reset_redirect_url},
        }
        if hasattr(admin, "generate_link"):
            response = admin.generate_link(payload)
            return _extract_reset_link(response)
    except Exception:
        return None
    return None


def _trigger_supabase_password_reset(auth: Any, email: str) -> tuple[bool, str]:
    if not email.strip():
        return False, "email missing"
    redirect_to = settings.password_reset_redirect_url
    errors: list[str] = []

    calls = [
        lambda: auth.reset_password_for_email(email, {"redirect_to": redirect_to}),
        lambda: auth.reset_password_for_email(email, redirect_to=redirect_to),
        lambda: auth.reset_password_email(email, {"redirect_to": redirect_to}),
        lambda: auth.reset_password_email(email, redirect_to=redirect_to),
    ]
    for call in calls:
        try:
            call()
            return True, "reset triggered"
        except Exception as exc:
            errors.append(str(exc))

    return False, " | ".join(errors[-2:]) if errors else "reset methods unavailable"


def _set_auth_identity(user_id: str, email: str | None) -> None:
    st.session_state[AUTH_USER_ID_KEY] = user_id
    st.session_state[AUTH_EMAIL_KEY] = (email or "").strip()


def _clear_auth_identity() -> None:
    st.session_state.pop(AUTH_USER_ID_KEY, None)
    st.session_state.pop(AUTH_EMAIL_KEY, None)
    st.session_state.pop("nav_page", None)
    st.session_state.pop("_nav_override", None)


def _render_auth_gate() -> bool:
    if st.session_state.get(AUTH_USER_ID_KEY):
        return True

    st.title("🔐 GovDocIQ Access")
    st.caption("Sign in to access your workspace.")

    auth, auth_error = _auth_client()
    if auth is None:
        detail = auth_error or service.repo.error or "unknown configuration error"
        st.error(
            "Supabase authentication is not available. "
            "Set `SUPABASE_URL` and one of `SUPABASE_KEY` / `SUPABASE_ANON_KEY` "
            "in Streamlit secrets. "
            f"Details: {detail}"
        )
        return False

    sign_in_tab, sign_up_tab = st.tabs(["Sign In", "Sign Up"])

    with sign_in_tab:
        with st.form("auth_sign_in_form"):
            email = st.text_input("Email", value="", placeholder="name@example.com")
            password = st.text_input("Password", type="password")
            submit_sign_in = st.form_submit_button("Sign In", use_container_width=True)
        if submit_sign_in:
            try:
                response = auth.sign_in_with_password(
                    {"email": email.strip(), "password": password}
                )
                user_id, user_email = _extract_auth_identity(response)
                if not user_id:
                    st.error("Sign in failed. Check credentials or email verification status.")
                else:
                    _set_auth_identity(user_id, user_email or email)
                    st.rerun()
            except Exception as exc:
                st.error(str(exc))

        st.markdown("### Recovery")
        fp_col, fu_col = st.columns(2)
        with fp_col:
            with st.form("auth_forgot_password_form"):
                fp_email = st.text_input("Forgot password email", value="", placeholder="name@example.com")
                fp_name = st.text_input("Name (optional)", value="")
                fp_role = st.selectbox(
                    "Role",
                    ALL_ROLES,
                    format_func=lambda r: ROLE_META.get(r, {}).get("label", r),
                    key="forgot_password_role",
                )
                submit_forgot_password = st.form_submit_button("Send Password Reset", use_container_width=True)
            if submit_forgot_password:
                target_email = fp_email.strip()
                if not target_email:
                    st.error("Email is required.")
                else:
                    reset_ok, reset_detail = _trigger_supabase_password_reset(auth, target_email)
                    recovery_link = _generate_supabase_recovery_link(auth, target_email)
                    send_result = email_adapter.send_email(
                        to_email=target_email,
                        template_type="forgot",
                        user_name=_default_user_name(fp_name, target_email),
                        role=_role_label(fp_role),
                        user_email=target_email,
                        reset_link=recovery_link or settings.password_reset_redirect_url,
                    )
                    if reset_ok:
                        st.success("Password reset flow triggered. Check your inbox.")
                    else:
                        st.warning(f"Supabase reset trigger returned: {reset_detail}")
                    if not send_result.ok:
                        st.info(f"Custom email not sent: {send_result.detail}")

        with fu_col:
            with st.form("auth_forgot_username_form"):
                fu_email = st.text_input("Forgot username email", value="", placeholder="name@example.com")
                fu_name = st.text_input("Name (optional)", value="", key="forgot_username_name")
                fu_role = st.selectbox(
                    "Role",
                    ALL_ROLES,
                    format_func=lambda r: ROLE_META.get(r, {}).get("label", r),
                    key="forgot_username_role",
                )
                submit_forgot_username = st.form_submit_button("Send Username Reminder", use_container_width=True)
            if submit_forgot_username:
                target_email = fu_email.strip()
                if not target_email:
                    st.error("Email is required.")
                else:
                    send_result = email_adapter.send_email(
                        to_email=target_email,
                        template_type="username",
                        user_name=_default_user_name(fu_name, target_email),
                        role=_role_label(fu_role),
                        user_email=target_email,
                    )
                    if send_result.ok:
                        st.success("Username reminder sent.")
                    else:
                        st.error(f"Username reminder failed: {send_result.detail}")

    with sign_up_tab:
        with st.form("auth_sign_up_form"):
            email_su = st.text_input("Email", value="", placeholder="name@example.com")
            pwd_su = st.text_input("Password", type="password")
            pwd_confirm = st.text_input("Confirm Password", type="password")
            full_name_su = st.text_input("Name (optional)", value="")
            role_su = st.selectbox(
                "Role",
                ALL_ROLES,
                format_func=lambda r: ROLE_META.get(r, {}).get("label", r),
            )
            submit_sign_up = st.form_submit_button("Create Account", use_container_width=True)

        if submit_sign_up:
            workspace_id = settings.default_workspace_id.strip()
            if pwd_su != pwd_confirm:
                st.error("Passwords do not match.")
            elif len(pwd_su) < 8:
                st.error("Password must be at least 8 characters.")
            elif not workspace_id:
                st.error("Workspace is not configured.")
            else:
                try:
                    response = auth.sign_up(
                        {"email": email_su.strip(), "password": pwd_su}
                    )
                    user_id, user_email = _extract_auth_identity(response)
                    if not user_id:
                        st.info(
                            "Sign-up request submitted. Verify your email, then sign in."
                        )
                    else:
                        service.register_officer(
                            officer_id=user_id,
                            tenant_id=workspace_id,
                            role=role_su,
                            display_name=workspace_id,
                        )
                        service.repo.upsert_tenant_membership(
                            user_id=user_id,
                            tenant_id=workspace_id,
                            role=role_su,
                            status="ACTIVE",
                        )
                        _set_auth_identity(user_id, user_email or email_su)
                        st.success("Account created and linked. Signed in.")
                        send_result = email_adapter.send_email(
                            to_email=(user_email or email_su.strip()),
                            template_type="signup",
                            user_name=_default_user_name(full_name_su, user_email or email_su),
                            role=_role_label(role_su),
                            user_email=(user_email or email_su.strip()),
                        )
                        if not send_result.ok:
                            st.info(f"Signup confirmation email not sent: {send_result.detail}")
                        st.rerun()
                except Exception as exc:
                    st.error(str(exc))

    return False


def _resolve_access_context() -> tuple[str, str, str] | None:
    officer_id = str(st.session_state.get(AUTH_USER_ID_KEY) or "").strip()
    if not officer_id:
        return None

    profile = service.get_officer_profile(officer_id)
    if profile and str(profile.get("status", "ACTIVE")).upper() == "ACTIVE":
        return str(profile.get("tenant_id")), str(profile.get("role")), officer_id

    st.warning("Your account is authenticated but not yet linked to an access profile.")
    with st.form("complete_profile_form"):
        workspace_id = settings.default_workspace_id.strip()
        role = st.selectbox(
            "Role",
            ALL_ROLES,
            format_func=lambda r: ROLE_META.get(r, {}).get("label", r),
            key="complete_profile_role",
        )
        submit_profile = st.form_submit_button("Complete Profile", use_container_width=True)
    if submit_profile:
        if not workspace_id:
            st.error("Workspace is not configured.")
        else:
            try:
                service.register_officer(
                    officer_id=officer_id,
                    tenant_id=workspace_id,
                    role=role,
                    display_name=workspace_id,
                )
                service.repo.upsert_tenant_membership(
                    user_id=officer_id,
                    tenant_id=workspace_id,
                    role=role,
                    status="ACTIVE",
                )
                st.success("Profile linked.")
                st.rerun()
            except Exception as exc:
                st.error(str(exc))
    return None


def main() -> None:
    if not _render_auth_gate():
        return

    access = _resolve_access_context()
    if access is None:
        return
    tenant_id, role, officer_id = access

    # ── Sidebar ───────────────────────────────────────────────────────────────
    with st.sidebar:
        st.markdown("### 🔐 Access")
        meta = ROLE_META.get(role, {"icon": "👤", "label": role, "color": "#90a4ae"})
        st.markdown(
            f'<div style="padding:0.3rem 0.7rem;border-radius:6px;background:{meta["color"]}15;'
            f'border-left:3px solid {meta["color"]};font-size:0.8rem;margin:0.4rem 0;color:{meta["color"]}">'
            f'{meta["icon"]} <strong>{meta["label"]}</strong></div>',
            unsafe_allow_html=True,
        )
        if st.session_state.get(AUTH_EMAIL_KEY):
            st.caption(f"Signed in as `{st.session_state[AUTH_EMAIL_KEY]}`")
        st.caption(f"Officer ID: `{officer_id}`")

        if st.button("🚪 Sign Out", use_container_width=True):
            try:
                auth, _ = _auth_client()
                if auth is not None:
                    auth.sign_out()
            except Exception as exc:
                st.error(str(exc))
            _clear_auth_identity()
            st.rerun()

        st.markdown("---")

        # Navigation with persisted state so form submits do not jump back to Dashboard.
        accessible = [p for p in PAGES if role in PAGE_ACCESS[p]]
        nav_override = st.session_state.pop("_nav_override", None)
        if nav_override and nav_override in accessible:
            st.session_state["nav_page"] = nav_override
        elif st.session_state.get("nav_page") not in accessible:
            st.session_state["nav_page"] = accessible[0] if accessible else ""

        page_index = 0
        if accessible and st.session_state.get("nav_page") in accessible:
            page_index = accessible.index(st.session_state["nav_page"])

        page = st.radio(
            "Navigation",
            accessible,
            index=page_index,
            key="nav_page",
            label_visibility="collapsed",
        )

        st.markdown("---")

        # Document selector
        docs, docs_error = _load_docs(tenant_id=tenant_id, officer_id=officer_id, role=role)
        if docs_error:
            st.error(docs_error)

        doc_ids = [str(d.get("id")) for d in docs if d.get("id")]
        selected_doc_id = st.selectbox("📄 Document", ["—"] + doc_ids, index=0)
        if selected_doc_id == "—":
            selected_doc_id = ""

        if selected_doc_id:
            sel = next((d for d in docs if str(d.get("id")) == selected_doc_id), None)
            if sel:
                s = str(sel.get("state", ""))
                dec = str(sel.get("decision") or "PENDING")
                dot = "green" if "APPROVED" in s else "red" if "REJECTED" in s or "FAILED" in s else "yellow" if "REVIEW" in s else "gray"
                st.markdown(f'{_status_dot(dot)} `{s}` · {dec}', unsafe_allow_html=True)

        # System status in sidebar bottom
        with st.expander("⚙️ System", expanded=False):
            using = "Supabase" if service.repo.using_supabase else f"Memory ({service.repo.error})"
            st.caption(f"Persistence: {using}")
            st.caption(f"OCR: {settings.ocr_backend} · Auth: {settings.authenticity_backend}")
            st.caption(f"DR: {service.dr_service.describe()}")
            st.write(
                {
                    "SCHEMA_READY": {
                        "part2": service.repo.part2_schema_ready,
                        "part3": service.repo.part3_schema_ready,
                        "part4": service.repo.part4_schema_ready,
                        "part5": service.repo.part5_schema_ready,
                    }
                }
            )
            if st.button("🧪 Run Supabase Storage Probe", use_container_width=True):
                try:
                    probe = service.supabase_persistence_probe(
                        tenant_id=tenant_id,
                        officer_id=officer_id,
                    )
                    st.json(probe)
                except Exception as exc:
                    st.error(str(exc))

    # ── Resolve selections ────────────────────────────────────────────────────
    policy = service.repo.get_tenant_policy(tenant_id)
    selected_doc = next((d for d in docs if str(d.get("id")) == selected_doc_id), None) if selected_doc_id else None
    latest_record_row = service.repo.get_latest_document_record(tenant_id, selected_doc_id) if selected_doc_id else None
    selected_record = _unwrap_record(latest_record_row)

    # ── Hero + Context bar (NOT on dashboard) ─────────────────────────────────
    _hero_banner(role, tenant_id, officer_id)

    if page != "🏠 Dashboard" and selected_doc:
        _doc_context_bar(selected_doc, role)

    # ── Page routing ──────────────────────────────────────────────────────────
    if page == "🏠 Dashboard":
        _render_dashboard(role=role, tenant_id=tenant_id, officer_id=officer_id, docs=docs)
    elif page == "📥 Intake & Processing":
        _render_intake_processing(role=role, tenant_id=tenant_id, officer_id=officer_id, docs=docs, selected_doc=selected_doc)
    elif page == "🔍 Review Workbench":
        _render_review_workbench(role=role, tenant_id=tenant_id, officer_id=officer_id, selected_doc=selected_doc, selected_record=selected_record)
    elif page == "⚖️ Dispute Desk":
        _render_dispute_desk(role=role, tenant_id=tenant_id, officer_id=officer_id, selected_doc=selected_doc)
    elif page == "🛡️ Fraud & Authenticity":
        _render_fraud_authenticity(selected_doc=selected_doc, selected_record=selected_record)
    elif page == "💬 Citizen Communication":
        _render_citizen_communication(role=role, tenant_id=tenant_id, officer_id=officer_id, selected_doc=selected_doc)
    elif page == "📋 Audit Trail":
        _render_audit_trail(tenant_id=tenant_id, officer_id=officer_id, selected_doc=selected_doc, selected_record=selected_record)
    elif page == "📊 Governance & KPI":
        _render_governance_kpi(role=role, tenant_id=tenant_id, officer_id=officer_id)
    elif page == "🖥️ Ops Monitor":
        _render_ops_monitor(role=role, tenant_id=tenant_id, officer_id=officer_id)
    elif page == "🧩 Integrations":
        _render_integrations(role=role, tenant_id=tenant_id, officer_id=officer_id)
    elif page == "📴 Offline Sync":
        _render_offline_sync(role=role, tenant_id=tenant_id, officer_id=officer_id)
    elif page == "🤖 ML Training":
        _render_ml_training(role=role, tenant_id=tenant_id, officer_id=officer_id)

    st.divider()
    st.caption("Role-gated · Auditable · Explainable AI")


if __name__ == "__main__":
    main()
