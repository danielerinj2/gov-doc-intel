from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import settings

try:
    import numpy as np  # type: ignore
except Exception:  # pragma: no cover
    np = None  # type: ignore[assignment]

try:
    import cv2  # type: ignore
except Exception:  # pragma: no cover
    cv2 = None  # type: ignore[assignment]


DOC_TYPES = ["AADHAAR_CARD", "PAN_CARD", "INCOME_CERTIFICATE", "OTHER"]


def preprocess_image(input_path: str, output_path: str) -> dict[str, Any]:
    if cv2 is None or np is None:
        return {"output_path": input_path, "steps": ["opencv_unavailable"], "quality_score": 0.5}

    image = cv2.imread(input_path)
    if image is None:
        return {"output_path": input_path, "steps": ["decode_failed"], "quality_score": 0.0}

    steps: list[str] = []

    # Resize to reasonable inference size for speed.
    h, w = image.shape[:2]
    max_w = 1400
    if w > max_w:
        ratio = max_w / float(w)
        image = cv2.resize(image, (max_w, int(h * ratio)), interpolation=cv2.INTER_AREA)
        steps.append("resize")

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    # Basic deskew using min-area rectangle around non-zero pixels.
    coords = np.column_stack(np.where(gray > 0))
    if coords.size:
        angle = cv2.minAreaRect(coords)[-1]
        if angle < -45:
            angle = -(90 + angle)
        else:
            angle = -angle

        if abs(angle) > 0.4:
            (hh, ww) = image.shape[:2]
            center = (ww // 2, hh // 2)
            m = cv2.getRotationMatrix2D(center, angle, 1.0)
            image = cv2.warpAffine(image, m, (ww, hh), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
            steps.append("deskew")

    # Contrast enhancement via CLAHE on L channel.
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    l2 = clahe.apply(l)
    out = cv2.cvtColor(cv2.merge((l2, a, b)), cv2.COLOR_LAB2BGR)
    steps.append("contrast_enhance")

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(output_path, out)

    quality_score = float(min(1.0, max(0.0, 0.65 + (0.1 * len(steps)))))
    return {"output_path": output_path, "steps": steps, "quality_score": round(quality_score, 3)}


def _heuristic_classifier(text: str, file_name: str = "") -> dict[str, Any]:
    t = (text or "").lower()
    fn = file_name.lower()

    aadhaar_terms = ["aadhaar", "government of india", "uidai", "आधार", "जन्म तिथि", "dob"]
    pan_terms = ["permanent account number", "income tax department", "pan", "father", "dob"]
    income_terms = ["income certificate", "annual income", "issuing authority", "certificate no"]

    scores = {
        "AADHAAR_CARD": 0.0,
        "PAN_CARD": 0.0,
        "INCOME_CERTIFICATE": 0.0,
    }

    for term in aadhaar_terms:
        if term in t:
            scores["AADHAAR_CARD"] += 0.18
    for term in pan_terms:
        if term in t:
            scores["PAN_CARD"] += 0.18
    for term in income_terms:
        if term in t:
            scores["INCOME_CERTIFICATE"] += 0.2

    if "aadhaar" in fn:
        scores["AADHAAR_CARD"] += 0.2
    if "pan" in fn:
        scores["PAN_CARD"] += 0.2
    if "income" in fn:
        scores["INCOME_CERTIFICATE"] += 0.2

    winner = max(scores, key=scores.get)
    confidence = min(scores[winner], 0.99)
    if confidence < 0.35:
        winner = "OTHER"
        confidence = 0.35 if t else 0.1

    return {"doc_type": winner, "confidence": round(confidence, 3), "scores": scores, "backend": "heuristic"}


def _layoutlm_classifier(
    *,
    image_path: str | None,
    words: list[str] | None,
    bbox: list[list[float]] | None,
) -> dict[str, Any] | None:
    if not image_path or not words or not bbox:
        return None
    if not os.path.isdir(settings.layoutlm_model_dir):
        return None

    try:
        import torch
        from PIL import Image
        from transformers import AutoProcessor, LayoutLMv3ForSequenceClassification
    except Exception:
        return None

    try:
        processor = AutoProcessor.from_pretrained(settings.layoutlm_model_dir, apply_ocr=False)
        model = LayoutLMv3ForSequenceClassification.from_pretrained(settings.layoutlm_model_dir)
        model.eval()
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        model.to(device)

        image = Image.open(image_path).convert("RGB")
        encoding = processor(
            image,
            words,
            boxes=[[int(v) for v in box] for box in bbox],
            return_tensors="pt",
            truncation=True,
            padding="max_length",
            max_length=512,
        )
        encoding = {k: v.to(device) for k, v in encoding.items()}

        with torch.no_grad():
            outputs = model(**encoding)
            probs = torch.softmax(outputs.logits, dim=-1).squeeze(0)
            pred_id = int(torch.argmax(probs).item())
            conf = float(probs[pred_id].item())

        id2label = model.config.id2label or {}
        raw_label = str(id2label.get(pred_id, "OTHER")).upper()
        mapped = raw_label if raw_label in DOC_TYPES else "OTHER"
        return {"doc_type": mapped, "confidence": round(conf, 3), "backend": "layoutlmv3"}
    except Exception:
        return None


def _fusion_classifier(*, text: str, image_path: str | None) -> dict[str, Any] | None:
    if not image_path:
        return None
    if not os.path.isfile(settings.fusion_model_path):
        return None
    if not os.path.isfile(settings.fusion_label_map_path):
        return None

    try:
        import torch
        from PIL import Image
        from torchvision import models, transforms
        from torchvision.models import ResNet50_Weights
        from transformers import BertModel, BertTokenizer
    except Exception:
        return None

    try:
        with open(settings.fusion_label_map_path, "r", encoding="utf-8") as f:
            raw_map = json.load(f)
        label_map = {str(k).lower(): int(v) for k, v in raw_map.items()}
        id2label = {v: k.upper() for k, v in label_map.items()}
        num_classes = max(id2label.keys()) + 1 if id2label else 0
        if num_classes <= 0:
            return None

        # Lightweight fallback architecture: concat BERT CLS + ResNet embedding + MLP head.
        class _FusionHead(torch.nn.Module):
            def __init__(self, out_dim: int) -> None:
                super().__init__()
                self.fc = torch.nn.Sequential(
                    torch.nn.Linear(768 + 2048, 768),
                    torch.nn.ReLU(),
                    torch.nn.Dropout(0.2),
                    torch.nn.Linear(768, out_dim),
                )

            def forward(self, text_emb: torch.Tensor, image_emb: torch.Tensor) -> torch.Tensor:
                x = torch.cat([text_emb, image_emb], dim=-1)
                return self.fc(x)

        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        tokenizer = BertTokenizer.from_pretrained("bert-base-uncased")
        bert = BertModel.from_pretrained("bert-base-uncased").eval().to(device)
        resnet = models.resnet50(weights=ResNet50_Weights.IMAGENET1K_V1)
        resnet = torch.nn.Sequential(*list(resnet.children())[:-1]).eval().to(device)
        classifier = _FusionHead(num_classes).to(device)
        classifier.load_state_dict(torch.load(settings.fusion_model_path, map_location=device))
        classifier.eval()

        t = tokenizer(text or "", return_tensors="pt", truncation=True, padding=True, max_length=128)
        t = {k: v.to(device) for k, v in t.items()}
        with torch.no_grad():
            text_emb = bert(**t).last_hidden_state[:, 0, :]

        transform = transforms.Compose(
            [
                transforms.Resize((224, 224)),
                transforms.ToTensor(),
                transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
            ]
        )
        img = Image.open(image_path).convert("RGB")
        x = transform(img).unsqueeze(0).to(device)
        with torch.no_grad():
            image_emb = resnet(x).squeeze(-1).squeeze(-1)
            logits = classifier(text_emb, image_emb)
            probs = torch.softmax(logits, dim=-1).squeeze(0)
            pred_id = int(torch.argmax(probs).item())
            conf = float(probs[pred_id].item())
        mapped = id2label.get(pred_id, "OTHER")
        if mapped not in DOC_TYPES:
            mapped = "OTHER"
        return {"doc_type": mapped, "confidence": round(conf, 3), "backend": "fusion"}
    except Exception:
        return None


def classify_document(
    text: str,
    file_name: str = "",
    *,
    image_path: str | None = None,
    words: list[str] | None = None,
    bbox: list[list[float]] | None = None,
) -> dict[str, Any]:
    backend = settings.classifier_backend
    if backend == "layoutlm":
        out = _layoutlm_classifier(image_path=image_path, words=words, bbox=bbox)
        if out:
            return out
    elif backend == "fusion":
        out = _fusion_classifier(text=text, image_path=image_path)
        if out:
            return out

    return _heuristic_classifier(text=text, file_name=file_name)


def _extract_aadhaar(text: str) -> dict[str, Any]:
    normalized = " ".join(text.split())
    aadhaar_number = None
    m_num = re.search(r"\b\d{4}\s?\d{4}\s?\d{4}\b", normalized)
    if m_num:
        aadhaar_number = m_num.group(0).replace(" ", "")
    if not aadhaar_number:
        for m in re.finditer(r"(?:\d[\s\-]*){12,16}", normalized):
            digits = re.sub(r"\D", "", m.group(0))
            if len(digits) == 12:
                aadhaar_number = digits
                break

    dob = None
    m_dob = re.search(r"\b(\d{2}[-/.]\d{2}[-/.]\d{4})\b", normalized)
    if m_dob:
        dob = m_dob.group(1).replace("-", "/").replace(".", "/")

    gender = None
    low = normalized.lower()
    if re.search(r"\bmale\b", low) or "पुरुष" in normalized:
        gender = "MALE"
    elif re.search(r"\bfemale\b", low) or "महिला" in normalized:
        gender = "FEMALE"
    elif re.search(r"\btransgender\b", low):
        gender = "TRANSGENDER"

    name = None
    m_name = re.search(r"नाम[:\s]+([A-Za-z\s]{3,40})", text)
    if m_name:
        name = m_name.group(1).strip()
    if not name:
        m_name2 = re.search(
            r"(?:name)\s*[:\-]?\s*([A-Za-z\u0900-\u097F\s]{3,80})",
            text,
            flags=re.IGNORECASE,
        )
        if m_name2:
            name = " ".join(m_name2.group(1).split())
    if not name:
        m_name3 = re.search(
            r"([A-Za-z\u0900-\u097F][A-Za-z\u0900-\u097F\s]{4,80})\s+(?:dob|date of birth|जन्म)",
            text,
            flags=re.IGNORECASE,
        )
        if m_name3:
            name = " ".join(m_name3.group(1).split())

    address = None
    m_addr = re.search(r"(?:address|पता)\s*[:\-]?\s*([^\n]{8,220})", text, flags=re.IGNORECASE)
    if m_addr:
        address = " ".join(m_addr.group(1).split())
    if not address and aadhaar_number:
        idx = normalized.find(aadhaar_number[:4])
        if idx >= 0:
            tail = normalized[idx + len(aadhaar_number[:4]) :]
            # Remove immediate number remainder and separators.
            tail = re.sub(r"^[\s\-:|,./\d]+", "", tail)
            tail = re.sub(
                r"\b(?:name|dob|date of birth|gender|aadhaar|uid)\b.*$",
                "",
                tail,
                flags=re.IGNORECASE,
            ).strip(" ,;")
            if len(tail) >= 12:
                address = tail[:180]

    return {
        "name": name,
        "aadhaar_number": aadhaar_number,
        "dob": dob,
        "gender": gender,
        "address": address,
    }


def _extract_pan(text: str) -> dict[str, Any]:
    normalized = " ".join(text.split())
    pan = None
    m_pan = re.search(r"\b[A-Z]{5}\d{4}[A-Z]\b", normalized)
    if m_pan:
        pan = m_pan.group(0)

    dob = None
    m_dob = re.search(r"\b(\d{2}[-/]\d{2}[-/]\d{4})\b", normalized)
    if m_dob:
        dob = m_dob.group(1).replace("-", "/")

    return {"pan_number": pan, "dob": dob}


def _extract_income(text: str) -> dict[str, Any]:
    normalized = " ".join(text.split())

    cert_no = None
    m_cert = re.search(r"(?:cert(?:ificate)?\s*(?:no|number)[:\s]*)([A-Za-z0-9\-/]+)", normalized, re.IGNORECASE)
    if m_cert:
        cert_no = m_cert.group(1)

    annual_income = None
    m_income = re.search(r"(?:rs\.?|inr)\s*([0-9,]{3,})", normalized, re.IGNORECASE)
    if m_income:
        annual_income = m_income.group(1).replace(",", "")

    return {"certificate_number": cert_no, "annual_income": annual_income}


def extract_fields(doc_type: str, text: str) -> dict[str, Any]:
    if doc_type == "AADHAAR_CARD":
        fields = _extract_aadhaar(text)
    elif doc_type == "PAN_CARD":
        fields = _extract_pan(text)
    elif doc_type == "INCOME_CERTIFICATE":
        fields = _extract_income(text)
    else:
        fields = {}

    output = []
    for k, v in fields.items():
        if v in (None, ""):
            continue
        output.append({"field_name": k, "normalized_value": str(v), "confidence": 0.9})

    return {"fields": output}


def validate_fields(doc_type: str, extracted_fields: list[dict[str, Any]]) -> dict[str, Any]:
    value_map = {str(f.get("field_name")): str(f.get("normalized_value", "")) for f in extracted_fields}
    results: list[dict[str, Any]] = []

    def add(name: str, status: str, reason: str) -> None:
        results.append({"field_name": name, "status": status, "reason": reason})

    if doc_type == "AADHAAR_CARD":
        aadhaar = value_map.get("aadhaar_number", "")
        if not aadhaar:
            add("aadhaar_number", "FAIL", "Missing Aadhaar number")
        elif re.fullmatch(r"\d{12}", aadhaar):
            add("aadhaar_number", "PASS", "Valid Aadhaar format")
        else:
            add("aadhaar_number", "FAIL", "Aadhaar format invalid")

    if doc_type == "PAN_CARD":
        pan = value_map.get("pan_number", "")
        if not pan:
            add("pan_number", "FAIL", "Missing PAN number")
        elif re.fullmatch(r"[A-Z]{5}\d{4}[A-Z]", pan):
            add("pan_number", "PASS", "Valid PAN format")
        else:
            add("pan_number", "FAIL", "PAN format invalid")

    dob = value_map.get("dob")
    if dob:
        try:
            parsed = datetime.strptime(dob, "%d/%m/%Y")
            if parsed.date() <= datetime.utcnow().date():
                add("dob", "PASS", "DOB plausible")
            else:
                add("dob", "FAIL", "DOB cannot be in the future")
        except Exception:
            add("dob", "FAIL", "DOB format invalid")

    # Cross-field check: if name exists and is too short, flag.
    name = value_map.get("name")
    if name:
        if len(name.strip()) >= 3:
            add("name", "PASS", "Name length plausible")
        else:
            add("name", "FAIL", "Name too short")

    fail_count = len([r for r in results if r["status"] == "FAIL"])
    overall = "PASS" if fail_count == 0 else "REVIEW"
    return {
        "overall_status": overall,
        "field_results": results,
        "failed_count": fail_count,
        "passed_count": len(results) - fail_count,
    }


def fraud_signals(text: str, classification_confidence: float, validation_output: dict[str, Any]) -> dict[str, Any]:
    t = (text or "").lower()
    stamp_present = any(token in t for token in ["government", "govt", "seal", "department"])
    signature_present = "signature" in t or "signed" in t

    fail_count = int(validation_output.get("failed_count", 0))
    layout_consistency = "PASS" if classification_confidence >= 0.55 else "FAIL"

    risk = 0.15
    if not stamp_present:
        risk += 0.15
    if not signature_present:
        risk += 0.1
    if layout_consistency == "FAIL":
        risk += 0.2
    risk += min(0.3, fail_count * 0.12)
    risk = float(min(0.95, max(0.0, risk)))

    level = "LOW"
    if risk >= 0.75:
        level = "HIGH"
    elif risk >= 0.45:
        level = "MEDIUM"

    return {
        "stamp_present": stamp_present,
        "signature_present": signature_present,
        "layout_consistency": layout_consistency,
        "aggregate_fraud_risk_score": round(risk, 3),
        "risk_level": level,
    }


def overall_confidence(ocr_confidence: float, classification_confidence: float, validation_output: dict[str, Any], fraud_output: dict[str, Any]) -> float:
    total = len(validation_output.get("field_results", []))
    fails = int(validation_output.get("failed_count", 0))
    validation_score = 1.0 if total == 0 else max(0.0, 1.0 - (fails / total))
    safety_score = 1.0 - float(fraud_output.get("aggregate_fraud_risk_score", 0.5))

    combined = (
        0.4 * max(0.0, min(1.0, ocr_confidence))
        + 0.3 * max(0.0, min(1.0, classification_confidence))
        + 0.2 * validation_score
        + 0.1 * safety_score
    )
    return round(max(0.0, min(1.0, combined)), 3)
