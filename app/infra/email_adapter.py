from __future__ import annotations

import json
from dataclasses import dataclass
from urllib import request
from urllib.error import HTTPError, URLError

from app.config import settings


@dataclass(frozen=True)
class EmailSendResult:
    ok: bool
    detail: str


def _safe_name(value: str | None, fallback: str = "User") -> str:
    cleaned = str(value or "").strip()
    return cleaned or fallback


class GovDocIQEmailAdapter:
    def __init__(self) -> None:
        # Backward-compatible: do not crash if an older settings object is loaded.
        self.api_key = str(getattr(settings, "sendgrid_api_key", "") or "").strip()
        self.from_email = str(getattr(settings, "sendgrid_from_email", "") or "").strip()
        self.login_url = str(getattr(settings, "app_login_url", "") or "").strip() or "https://govdociq.streamlit.app"

    def _build_subject(self, template_type: str, role: str) -> str:
        subjects = {
            "signup": f"Welcome to GovDocIQ Access! Your {role} Account is Active",
            "forgot": f"GovDocIQ Access Password Reset - {role}",
            "username": f"GovDocIQ Access Username Reminder - {role}",
        }
        return subjects.get(template_type, f"GovDocIQ Access Notification - {role}")

    def _build_body(
        self,
        *,
        template_type: str,
        user_name: str,
        role: str,
        user_email: str | None,
        reset_link: str | None,
    ) -> str:
        if template_type == "signup":
            return (
                f"Hi {user_name},\n\n"
                "Your GovDocIQ Access account is ready.\n\n"
                f"Your Role: {role}\n"
                f"Email: {user_email or '-'}\n"
                f"Login: {self.login_url}\n\n"
                f"Start {role.lower()} government documents now.\n\n"
                "Best,\n"
                "GovDocIQ Access Team"
            )
        if template_type == "forgot":
            link = (reset_link or "").strip() or self.login_url
            return (
                f"Hi {user_name},\n\n"
                "Reset your GovDocIQ Access password:\n"
                f"{link} (valid 15 mins)\n\n"
                f"Your Role: {role}\n"
                f"Login: {self.login_url}\n\n"
                "Ignore if you did not request this.\n\n"
                "Best,\n"
                "GovDocIQ Access Team"
            )
        if template_type == "username":
            return (
                f"Hi {user_name},\n\n"
                "Your GovDocIQ Access username is your email address.\n\n"
                f"Username: {user_email or '-'}\n"
                f"Your Role: {role}\n"
                f"Login: {self.login_url}\n\n"
                "Best,\n"
                "GovDocIQ Access Team"
            )
        return (
            f"Hi {user_name},\n\n"
            "GovDocIQ Access notification.\n\n"
            f"Role: {role}\n"
            f"Login: {self.login_url}\n\n"
            "Best,\n"
            "GovDocIQ Access Team"
        )

    def send_govdociq_email(
        self,
        to_email: str,
        template_type: str,
        user_name: str,
        role: str,
        user_email: str | None = None,
        reset_link: str | None = None,
    ) -> bool:
        result = self.send_email(
            to_email=to_email,
            template_type=template_type,
            user_name=user_name,
            role=role,
            user_email=user_email,
            reset_link=reset_link,
        )
        return result.ok

    def send_email(
        self,
        *,
        to_email: str,
        template_type: str,
        user_name: str,
        role: str,
        user_email: str | None = None,
        reset_link: str | None = None,
    ) -> EmailSendResult:
        target = str(to_email or "").strip()
        if not target:
            return EmailSendResult(ok=False, detail="recipient email missing")
        if not self.api_key:
            return EmailSendResult(ok=False, detail="SENDGRID_API_KEY missing")
        if not self.from_email:
            return EmailSendResult(ok=False, detail="SENDGRID_FROM_EMAIL missing")

        safe_name = _safe_name(user_name)
        role_label = str(role or "User").strip() or "User"
        subject = self._build_subject(template_type, role_label)
        body = self._build_body(
            template_type=template_type,
            user_name=safe_name,
            role=role_label,
            user_email=user_email or target,
            reset_link=reset_link,
        )
        payload = {
            "personalizations": [{"to": [{"email": target}]}],
            "from": {"email": self.from_email},
            "subject": subject,
            "content": [{"type": "text/plain", "value": body}],
        }
        req = request.Request(
            "https://api.sendgrid.com/v3/mail/send",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=10) as resp:
                if int(getattr(resp, "status", 0)) == 202:
                    return EmailSendResult(ok=True, detail="sent")
                return EmailSendResult(ok=False, detail=f"unexpected status {getattr(resp, 'status', 'unknown')}")
        except HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="ignore")
            except Exception:
                detail = str(exc)
            return EmailSendResult(ok=False, detail=f"http_error:{exc.code}:{detail}")
        except URLError as exc:
            return EmailSendResult(ok=False, detail=f"url_error:{exc}")
        except Exception as exc:  # pragma: no cover
            return EmailSendResult(ok=False, detail=str(exc))
