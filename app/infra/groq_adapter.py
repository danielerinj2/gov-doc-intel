from __future__ import annotations

import json
import re
from typing import Any

from groq import Groq

from app.config import settings


class GroqAdapter:
    def __init__(self) -> None:
        self.client = Groq(api_key=settings.groq_api_key) if settings.groq_api_key else None
        self.model = settings.groq_model

    @property
    def enabled(self) -> bool:
        return self.client is not None

    def classify(self, text: str) -> dict[str, Any]:
        if not self.client:
            return self._fallback_classify(text)

        prompt = (
            "Classify this government document. Return strict JSON with keys: "
            "document_type, confidence, reasoning_short."
        )
        data = self._chat_json(prompt, text)
        return data if data else self._fallback_classify(text)

    def extract(self, text: str, document_type: str) -> dict[str, Any]:
        if not self.client:
            return self._fallback_extract(text, document_type)

        prompt = (
            "Extract useful key fields from this document. Return strict JSON with keys: "
            "fields (object), required_missing (array), confidence."
        )
        data = self._chat_json(prompt, f"Document type: {document_type}\n\n{text}")
        return data if data else self._fallback_extract(text, document_type)

    def _chat_json(self, system_prompt: str, user_text: str) -> dict[str, Any] | None:
        try:
            completion = self.client.chat.completions.create(
                model=self.model,
                temperature=0,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_text[:12000]},
                ],
            )
            content = completion.choices[0].message.content
            return json.loads(content) if content else None
        except Exception:
            return None

    def _fallback_classify(self, text: str) -> dict[str, Any]:
        t = text.lower()
        if "passport" in t:
            return {"document_type": "PASSPORT", "confidence": 0.85, "reasoning_short": "Keyword match: passport"}
        if "license" in t:
            return {"document_type": "LICENSE", "confidence": 0.82, "reasoning_short": "Keyword match: license"}
        if "marks" in t or "transcript" in t:
            return {"document_type": "MARKSHEET", "confidence": 0.79, "reasoning_short": "Keyword match: marksheet"}
        return {"document_type": "UNKNOWN", "confidence": 0.56, "reasoning_short": "No strong keyword"}

    def _fallback_extract(self, text: str, document_type: str) -> dict[str, Any]:
        fields: dict[str, Any] = {"document_type": document_type}
        missing: list[str] = []

        name_match = re.search(r"name\s*[:\-]\s*([A-Za-z .]{3,})", text, re.IGNORECASE)
        num_match = re.search(r"(id|no\.?|number|reg no)\s*[:\-]?\s*([A-Z0-9\-]{5,})", text, re.IGNORECASE)
        issuer_match = re.search(r"issuer\s*[:\-]\s*([A-Za-z .]{3,})", text, re.IGNORECASE)

        if name_match:
            fields["name"] = name_match.group(1).strip()
        else:
            missing.append("name")

        if num_match:
            fields["document_number"] = num_match.group(2).strip()
        else:
            missing.append("document_number")

        if issuer_match:
            fields["issuer"] = issuer_match.group(1).strip()
        else:
            missing.append("issuer")

        conf = 0.7 if not missing else 0.5
        return {"fields": fields, "required_missing": missing, "confidence": conf}
