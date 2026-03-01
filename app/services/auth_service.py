from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests

from app.config import settings
from app.infra.email_adapter import GovDocIQEmailAdapter
from app.infra.repositories import ROLE_VERIFIER


@dataclass
class AuthResponse:
    ok: bool
    message: str
    data: dict[str, Any] | None = None


class AuthService:
    def __init__(self) -> None:
        self.base_url = settings.supabase_url.rstrip("/")
        self.api_key = (settings.supabase_anon_key or settings.supabase_key or settings.supabase_service_key).strip()
        self.service_key = (settings.supabase_service_key or "").strip()
        self.email_adapter = GovDocIQEmailAdapter()

    def configured(self) -> bool:
        return bool(self.base_url and self.api_key and settings.supabase_url_valid())

    def _headers(self, use_service: bool = False) -> dict[str, str]:
        key = self.service_key if use_service and self.service_key else self.api_key
        return {
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }

    def sign_up(self, *, name: str, email: str, password: str, role: str) -> AuthResponse:
        if not self.configured():
            return AuthResponse(False, "Supabase authentication is not configured.")

        payload = {
            "email": email,
            "password": password,
            "data": {"name": name, "role": role},
        }
        try:
            res = requests.post(
                f"{self.base_url}/auth/v1/signup",
                headers=self._headers(),
                json=payload,
                timeout=15,
            )
            if res.status_code >= 400:
                msg = (res.json().get("msg") if res.headers.get("content-type", "").startswith("application/json") else res.text) or "Signup failed"
                return AuthResponse(False, str(msg))

            data = res.json()
            self.email_adapter.send_govdociq_email(
                to_email=email,
                template_type="signup",
                user_name=name,
                role=role,
                user_email=email,
            )
            return AuthResponse(True, "Signup successful. You can sign in now.", data)
        except Exception as exc:
            return AuthResponse(False, f"Signup failed: {exc}")

    def sign_in(self, *, email: str, password: str) -> AuthResponse:
        if not self.configured():
            return AuthResponse(False, "Supabase authentication is not configured.")

        payload = {"email": email, "password": password}
        try:
            res = requests.post(
                f"{self.base_url}/auth/v1/token?grant_type=password",
                headers=self._headers(),
                json=payload,
                timeout=15,
            )
            if res.status_code >= 400:
                msg = (res.json().get("msg") if res.headers.get("content-type", "").startswith("application/json") else res.text) or "Invalid credentials"
                return AuthResponse(False, str(msg))

            data = res.json()
            user = data.get("user") or {}
            user_meta = user.get("user_metadata") or {}
            profile = {
                "user_id": user.get("id"),
                "email": user.get("email") or email,
                "name": user_meta.get("name") or "User",
                "role": user_meta.get("role") or ROLE_VERIFIER,
                "access_token": data.get("access_token"),
                "refresh_token": data.get("refresh_token"),
            }
            return AuthResponse(True, "Signed in", profile)
        except Exception as exc:
            return AuthResponse(False, f"Sign in failed: {exc}")

    def _generate_recovery_link(self, email: str) -> str | None:
        if not self.service_key:
            return None
        try:
            # Supabase admin endpoint for generating action links.
            res = requests.post(
                f"{self.base_url}/auth/v1/admin/generate_link",
                headers=self._headers(use_service=True),
                json={"type": "recovery", "email": email, "options": {"redirect_to": settings.supabase_password_reset_redirect_url}},
                timeout=15,
            )
            if res.status_code < 400:
                out = res.json() or {}
                props = out.get("properties") or {}
                link = props.get("action_link") or out.get("action_link")
                return str(link) if link else None
        except Exception:
            return None
        return None

    def send_password_reset(self, *, email: str, role: str = "Verifier", name: str = "User") -> AuthResponse:
        if not self.configured():
            return AuthResponse(False, "Supabase authentication is not configured.")

        link = self._generate_recovery_link(email)
        if link:
            email_res = self.email_adapter.send_govdociq_email(
                to_email=email,
                template_type="forgot",
                user_name=name,
                role=role,
                reset_link=link,
            )
            if email_res.ok:
                return AuthResponse(True, "Password reset email sent.")
            return AuthResponse(False, f"Reset link generated but email failed: {email_res.message}")

        # Fallback to Supabase-built email flow.
        try:
            res = requests.post(
                f"{self.base_url}/auth/v1/recover",
                headers=self._headers(),
                json={"email": email, "redirect_to": settings.supabase_password_reset_redirect_url},
                timeout=15,
            )
            if res.status_code >= 400:
                msg = (res.json().get("msg") if res.headers.get("content-type", "").startswith("application/json") else res.text) or "Recovery request failed"
                return AuthResponse(False, str(msg))
            return AuthResponse(True, "Password reset request submitted. Check your email.")
        except Exception as exc:
            return AuthResponse(False, f"Recovery failed: {exc}")

    def send_username_reminder(self, *, email: str, name: str = "User") -> AuthResponse:
        res = self.email_adapter.send_govdociq_email(
            to_email=email,
            template_type="username",
            user_name=name,
            role="Verifier",
            user_email=email,
        )
        if res.ok:
            return AuthResponse(True, "Username reminder email sent.")
        return AuthResponse(False, f"Username reminder failed: {res.message}")
