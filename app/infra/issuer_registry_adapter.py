from __future__ import annotations

import json
import ssl
from typing import Any
from urllib import error, request

from app.config import settings


class IssuerRegistryAdapter:
    """External issuer verification adapter.

    Expected endpoint:
    POST {ISSUER_REGISTRY_BASE_URL}/verify
    payload: {tenant_id, doc_type, fields}
    response: {status, confidence, issuer_reference_id, fields_compared}
    """

    def __init__(self) -> None:
        self.base_url = settings.issuer_registry_base_url
        self.token = settings.issuer_registry_token

    def verify(self, *, tenant_id: str, doc_type: str, fields: dict[str, Any]) -> dict[str, Any] | None:
        if not self.base_url:
            return None

        url = f"{self.base_url}/verify"
        payload = {"tenant_id": tenant_id, "doc_type": doc_type, "fields": fields}
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        req = request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
        ctx = ssl._create_unverified_context()
        try:
            with request.urlopen(req, timeout=8, context=ctx) as resp:
                body = resp.read().decode("utf-8", "ignore")
                parsed = json.loads(body) if body else {}
                if isinstance(parsed, dict):
                    return parsed
                return None
        except error.HTTPError:
            return None
        except Exception:
            return None
