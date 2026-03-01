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
        self.user_agent = settings.groq_user_agent

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
                extra_headers={"User-Agent": self.user_agent} if self.user_agent else None,
            )
            content = completion.choices[0].message.content
            return json.loads(content) if content else None
        except Exception:
            return None

    def _fallback_classify(self, text: str) -> dict[str, Any]:
        t = text.lower()
        if any(k in t for k in ["aadhaar", "aadhar", "uidai", "government of india"]) or re.search(r"\b\d{4}\s?\d{4}\s?\d{4}\b", t):
            return {"document_type": "AADHAAR_CARD", "confidence": 0.9, "reasoning_short": "Keyword/pattern match: Aadhaar"}
        if "passport" in t:
            return {"document_type": "PASSPORT", "confidence": 0.85, "reasoning_short": "Keyword match: passport"}
        if re.search(r"\b[a-z]{5}\d{4}[a-z]\b", t):
            return {"document_type": "PAN_CARD", "confidence": 0.88, "reasoning_short": "Pattern match: PAN"}
        if any(k in t for k in ["driving licence", "driving license", "dl no", "driving licence no"]):
            return {"document_type": "DRIVING_LICENSE", "confidence": 0.83, "reasoning_short": "Keyword match: driving license"}
        if any(k in t for k in ["voter id", "election commission", "epic"]):
            return {"document_type": "VOTER_ID", "confidence": 0.82, "reasoning_short": "Keyword match: voter id"}
        if any(k in t for k in ["birth certificate", "date of birth certificate"]):
            return {"document_type": "BIRTH_CERTIFICATE", "confidence": 0.8, "reasoning_short": "Keyword match: birth certificate"}
        if any(k in t for k in ["income certificate"]):
            return {"document_type": "INCOME_CERTIFICATE", "confidence": 0.8, "reasoning_short": "Keyword match: income certificate"}
        if any(k in t for k in ["caste certificate"]):
            return {"document_type": "CASTE_CERTIFICATE", "confidence": 0.8, "reasoning_short": "Keyword match: caste certificate"}
        if any(k in t for k in ["ration card"]):
            return {"document_type": "RATION_CARD", "confidence": 0.8, "reasoning_short": "Keyword match: ration card"}
        if any(k in t for k in ["land record", "khasra", "khata", "mutation"]):
            return {"document_type": "LAND_RECORD", "confidence": 0.78, "reasoning_short": "Keyword match: land record"}
        if "marks" in t or "transcript" in t or "mark sheet" in t:
            return {"document_type": "MARKSHEET", "confidence": 0.79, "reasoning_short": "Keyword match: marksheet"}
        return {"document_type": "UNKNOWN", "confidence": 0.56, "reasoning_short": "No strong keyword"}

    def _fallback_extract(self, text: str, document_type: str) -> dict[str, Any]:
        fields: dict[str, Any] = {"document_type": document_type}
        missing: list[str] = []
        txt = text or ""

        name_match = re.search(
            r"(?:name|नाम)\s*[:\-]?\s*([A-Za-z\u0900-\u097F .]{3,})",
            txt,
            re.IGNORECASE,
        )
        dob_match = re.search(
            r"(?:dob|date of birth|जन्म\s*तिथि)\s*[:\-]?\s*(\d{2}[\/\-]\d{2}[\/\-]\d{4})",
            txt,
            re.IGNORECASE,
        )
        gender_match = re.search(
            r"(?:gender|लिंग)\s*[:\-]?\s*(male|female|m|f|पुरुष|महिला)",
            txt,
            re.IGNORECASE,
        )
        aadhaar_match = re.search(r"\b(\d{4}\s?\d{4}\s?\d{4})\b", txt)
        pan_match = re.search(r"\b([A-Z]{5}[0-9]{4}[A-Z])\b", txt)
        num_match = re.search(r"(?:id|no\.?|number|reg(?:istration)? no|ref(?:erence)?)\s*[:\-]?\s*([A-Z0-9\-\/]{5,})", txt, re.IGNORECASE)
        issuer_match = re.search(r"(?:issuer|issuing authority|जारीकर्ता)\s*[:\-]?\s*([A-Za-z\u0900-\u097F .]{3,})", txt, re.IGNORECASE)
        address_match = re.search(r"(?:address|पता)\s*[:\-]?\s*([A-Za-z0-9,\-\/\u0900-\u097F .]{8,})", txt, re.IGNORECASE)

        if name_match:
            fields["name"] = name_match.group(1).strip()
        else:
            missing.append("name")

        if dob_match:
            fields["date_of_birth"] = dob_match.group(1).strip()
        else:
            missing.append("date_of_birth")

        if gender_match:
            fields["gender"] = gender_match.group(1).strip().upper()

        if aadhaar_match:
            fields["document_number"] = re.sub(r"\s+", "", aadhaar_match.group(1))
        elif pan_match:
            fields["document_number"] = pan_match.group(1).strip()
        elif num_match:
            fields["document_number"] = num_match.group(1).strip()
        else:
            missing.append("document_number")

        if issuer_match:
            fields["issuer"] = issuer_match.group(1).strip()
        elif any(k in txt.lower() for k in ["government of india", "भारत सरकार"]):
            fields["issuer"] = "GOVERNMENT OF INDIA"
        else:
            missing.append("issuer")

        if address_match:
            fields["address"] = address_match.group(1).strip()

        matched = max(0, len(fields) - 1)  # excluding document_type
        conf = min(0.95, 0.45 + (matched * 0.08))
        if missing:
            conf = max(0.4, conf - 0.1)
        return {"fields": fields, "required_missing": missing, "confidence": conf}
