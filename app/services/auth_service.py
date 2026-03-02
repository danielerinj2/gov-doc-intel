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
        self.provider = (settings.auth_provider or "supabase").lower()

        self.base_url = settings.supabase_url.rstrip("/")
        self.api_key = (settings.supabase_anon_key or settings.supabase_key or settings.supabase_service_key).strip()
        self.service_key = (settings.supabase_service_key or "").strip()

        self.appwrite_endpoint = settings.appwrite_endpoint.rstrip("/")
        self.appwrite_project_id = settings.appwrite_project_id.strip()
        self.appwrite_api_key = settings.appwrite_api_key.strip()

        self.email_adapter = GovDocIQEmailAdapter()

    def configured(self) -> bool:
        if self.provider == "appwrite":
            return settings.appwrite_configured()
        if self.provider == "supabase":
            return bool(self.base_url and self.api_key and settings.supabase_url_valid())
        return False

    def connection_check(self) -> AuthResponse:
        if not self.configured():
            return AuthResponse(False, f"{self.provider} is not configured.")
        if self.provider == "appwrite":
            try:
                # Public health endpoint for Appwrite Cloud.
                res = requests.get(f"{self.appwrite_endpoint}/health/version", timeout=12)
                if res.status_code < 400:
                    payload = res.json() if res.headers.get("content-type", "").startswith("application/json") else {}
                    return AuthResponse(True, "Appwrite reachable.", {"status_code": res.status_code, "payload": payload})
                return AuthResponse(False, f"Appwrite health check failed: {res.status_code} {self._appwrite_error_message(res)}")
            except Exception as exc:
                return AuthResponse(False, f"Appwrite unreachable: {exc}")
        try:
            res = requests.get(
                f"{self.base_url}/auth/v1/health",
                headers=self._supabase_headers(),
                timeout=12,
            )
            if res.status_code < 400:
                payload = res.json() if res.headers.get("content-type", "").startswith("application/json") else {}
                return AuthResponse(True, "Supabase reachable.", {"status_code": res.status_code, "payload": payload})
            return AuthResponse(False, f"Supabase health check failed: {res.status_code} {res.text[:200]}")
        except Exception as exc:
            return AuthResponse(False, f"Supabase unreachable: {exc}")

    # ──────────────────────────────────────────────────────────────────────
    # Supabase helpers
    # ──────────────────────────────────────────────────────────────────────

    def _supabase_headers(self, use_service: bool = False) -> dict[str, str]:
        key = self.service_key if use_service and self.service_key else self.api_key
        return {
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }

    # ──────────────────────────────────────────────────────────────────────
    # Appwrite helpers
    # ──────────────────────────────────────────────────────────────────────

    def _appwrite_headers(self, use_api_key: bool = False) -> dict[str, str]:
        headers = {
            "X-Appwrite-Project": self.appwrite_project_id,
            "Content-Type": "application/json",
        }
        if use_api_key and self.appwrite_api_key:
            headers["X-Appwrite-Key"] = self.appwrite_api_key
        return headers

    def _appwrite_request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> requests.Response:
        h = headers or self._appwrite_headers()
        return requests.request(
            method,
            f"{self.appwrite_endpoint}{path}",
            headers=h,
            json=payload,
            timeout=20,
        )

    def _appwrite_error_message(self, response: requests.Response) -> str:
        try:
            body = response.json() or {}
            return str(body.get("message") or body.get("error") or response.text or "Request failed")
        except Exception:
            return response.text or "Request failed"

    # ──────────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────────

    def sign_up(self, *, name: str, email: str, password: str, role: str) -> AuthResponse:
        if self.provider == "appwrite":
            return self._sign_up_appwrite(name=name, email=email, password=password, role=role)
        return self._sign_up_supabase(name=name, email=email, password=password, role=role)

    def sign_in(self, *, email: str, password: str) -> AuthResponse:
        if self.provider == "appwrite":
            return self._sign_in_appwrite(email=email, password=password)
        return self._sign_in_supabase(email=email, password=password)

    def send_password_reset(self, *, email: str, role: str = "Verifier", name: str = "User") -> AuthResponse:
        if self.provider == "appwrite":
            return self._send_password_reset_appwrite(email=email, role=role, name=name)
        return self._send_password_reset_supabase(email=email, role=role, name=name)

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

    # ──────────────────────────────────────────────────────────────────────
    # Supabase flows
    # ──────────────────────────────────────────────────────────────────────

    def _sign_up_supabase(self, *, name: str, email: str, password: str, role: str) -> AuthResponse:
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
                headers=self._supabase_headers(),
                json=payload,
                timeout=15,
            )
            if res.status_code >= 400:
                msg = (
                    res.json().get("msg")
                    if res.headers.get("content-type", "").startswith("application/json")
                    else res.text
                ) or "Signup failed"
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

    def _sign_in_supabase(self, *, email: str, password: str) -> AuthResponse:
        if not self.configured():
            return AuthResponse(False, "Supabase authentication is not configured.")

        payload = {"email": email, "password": password}
        try:
            res = requests.post(
                f"{self.base_url}/auth/v1/token?grant_type=password",
                headers=self._supabase_headers(),
                json=payload,
                timeout=15,
            )
            if res.status_code >= 400:
                msg = (
                    res.json().get("msg")
                    if res.headers.get("content-type", "").startswith("application/json")
                    else res.text
                ) or "Invalid credentials"
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

    def _generate_recovery_link_supabase(self, email: str) -> str | None:
        if not self.service_key:
            return None
        try:
            res = requests.post(
                f"{self.base_url}/auth/v1/admin/generate_link",
                headers=self._supabase_headers(use_service=True),
                json={
                    "type": "recovery",
                    "email": email,
                    "options": {"redirect_to": settings.supabase_password_reset_redirect_url},
                },
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

    def _send_password_reset_supabase(self, *, email: str, role: str = "Verifier", name: str = "User") -> AuthResponse:
        if not self.configured():
            return AuthResponse(False, "Supabase authentication is not configured.")

        link = self._generate_recovery_link_supabase(email)
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

        try:
            res = requests.post(
                f"{self.base_url}/auth/v1/recover",
                headers=self._supabase_headers(),
                json={"email": email, "redirect_to": settings.supabase_password_reset_redirect_url},
                timeout=15,
            )
            if res.status_code >= 400:
                msg = (
                    res.json().get("msg")
                    if res.headers.get("content-type", "").startswith("application/json")
                    else res.text
                ) or "Recovery request failed"
                return AuthResponse(False, str(msg))
            return AuthResponse(True, "Password reset request submitted. Check your email.")
        except Exception as exc:
            return AuthResponse(False, f"Recovery failed: {exc}")

    # ──────────────────────────────────────────────────────────────────────
    # Appwrite flows
    # ──────────────────────────────────────────────────────────────────────

    def _sign_up_appwrite(self, *, name: str, email: str, password: str, role: str) -> AuthResponse:
        if not self.configured():
            return AuthResponse(False, "Appwrite authentication is not configured.")
        try:
            res = self._appwrite_request(
                "POST",
                "/account",
                payload={
                    "userId": "unique()",
                    "email": email,
                    "password": password,
                    "name": name,
                },
            )
            if res.status_code >= 400:
                return AuthResponse(False, self._appwrite_error_message(res))
            data = res.json() or {}

            # Best-effort welcome email via SendGrid.
            self.email_adapter.send_govdociq_email(
                to_email=email,
                template_type="signup",
                user_name=name,
                role=role,
                user_email=email,
            )

            return AuthResponse(
                True,
                "Signup successful. You can sign in now.",
                {
                    "user_id": data.get("$id"),
                    "email": data.get("email") or email,
                    "name": data.get("name") or name,
                    "role": role or ROLE_VERIFIER,
                },
            )
        except Exception as exc:
            return AuthResponse(False, f"Signup failed: {exc}")

    def _sign_in_appwrite(self, *, email: str, password: str) -> AuthResponse:
        if not self.configured():
            return AuthResponse(False, "Appwrite authentication is not configured.")
        try:
            # Create email session.
            res = self._appwrite_request(
                "POST",
                "/account/sessions/email",
                payload={"email": email, "password": password},
            )
            if res.status_code >= 400:
                return AuthResponse(False, self._appwrite_error_message(res))

            session = res.json() or {}
            user_id = session.get("userId") or session.get("$id")

            # We do not depend on role persistence from Appwrite here.
            profile = {
                "user_id": user_id,
                "email": email,
                "name": email.split("@")[0],
                "role": ROLE_VERIFIER,
                "session_id": session.get("$id"),
                "auth_provider": "appwrite",
            }
            return AuthResponse(True, "Signed in", profile)
        except Exception as exc:
            return AuthResponse(False, f"Sign in failed: {exc}")

    def _send_password_reset_appwrite(self, *, email: str, role: str = "Verifier", name: str = "User") -> AuthResponse:
        if not self.configured():
            return AuthResponse(False, "Appwrite authentication is not configured.")
        try:
            # Appwrite sends recovery mail using configured templates.
            res = self._appwrite_request(
                "POST",
                "/account/recovery",
                payload={"email": email, "url": settings.appwrite_recovery_redirect_url},
            )
            if res.status_code >= 400:
                return AuthResponse(False, self._appwrite_error_message(res))

            # Optional informational email through SendGrid (no reset link here).
            if self.email_adapter.configured():
                self.email_adapter.send_govdociq_email(
                    to_email=email,
                    template_type="forgot",
                    user_name=name,
                    role=role,
                    reset_link=settings.appwrite_recovery_redirect_url,
                )
            return AuthResponse(True, "Password reset request submitted. Check your email.")
        except Exception as exc:
            return AuthResponse(False, f"Recovery failed: {exc}")
