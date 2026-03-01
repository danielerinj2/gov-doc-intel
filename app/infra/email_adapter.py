from __future__ import annotations

from dataclasses import dataclass

import requests

from app.config import settings


@dataclass
class EmailResult:
    ok: bool
    status_code: int
    message: str


class GovDocIQEmailAdapter:
    def __init__(self) -> None:
        self.api_key = settings.sendgrid_api_key.strip()
        self.from_email = settings.sendgrid_from_email.strip()

    def configured(self) -> bool:
        return bool(self.api_key and self.from_email)

    def send_govdociq_email(
        self,
        *,
        to_email: str,
        template_type: str,
        user_name: str,
        role: str,
        user_email: str | None = None,
        reset_link: str | None = None,
    ) -> EmailResult:
        if not self.configured():
            return EmailResult(False, 0, "SendGrid is not configured.")

        subjects = {
            "signup": f"Welcome to GovDocIQ Access! Your {role} Account is Active",
            "forgot": f"GovDocIQ Access Password Reset - {role}",
            "username": "GovDocIQ Access Username Reminder",
        }

        if template_type == "signup":
            body = (
                f"Hi {user_name},\n\n"
                "Your GovDocIQ Access account is ready!\n\n"
                f"Your Role: {role}\n"
                f"Email: {user_email or to_email}\n"
                f"Login: {settings.app_login_url}\n\n"
                f"Start {role.lower()} government documents now!\n\n"
                "Best,\nGovDocIQ Access Team"
            )
        elif template_type == "forgot":
            body = (
                f"Hi {user_name},\n\n"
                "Reset your GovDocIQ Access password:\n"
                f"{reset_link or settings.app_login_url} (valid 15 mins)\n\n"
                f"Your Role: {role}\n"
                f"Login: {settings.app_login_url}\n\n"
                "Ignore if you didn't request this.\n\n"
                "Best,\nGovDocIQ Access Team"
            )
        else:
            body = (
                f"Hi {user_name},\n\n"
                f"Your GovDocIQ sign-in username is: {user_email or to_email}\n"
                f"Login: {settings.app_login_url}\n\n"
                "Best,\nGovDocIQ Access Team"
            )

        payload = {
            "personalizations": [{"to": [{"email": to_email}]}],
            "from": {"email": self.from_email},
            "subject": subjects.get(template_type, "GovDocIQ Access"),
            "content": [{"type": "text/plain", "value": body}],
        }
        try:
            response = requests.post(
                "https://api.sendgrid.com/v3/mail/send",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=12,
            )
            if response.status_code == 202:
                return EmailResult(True, 202, "Email sent")
            return EmailResult(False, response.status_code, response.text[:300])
        except Exception as exc:
            return EmailResult(False, 0, str(exc))
