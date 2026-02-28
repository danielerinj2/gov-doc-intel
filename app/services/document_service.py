from __future__ import annotations

from typing import Any

from app.domain.models import Dispute, Document, DocumentEvent
from app.domain.state_machine import StateMachine
from app.domain.states import DocumentState
from app.infra.groq_adapter import GroqAdapter
from app.infra.repositories import Repository
from app.pipeline.dag import DAG, Node
from app.pipeline.nodes import PipelineNodes


class DocumentService:
    def __init__(self) -> None:
        self.repo = Repository()
        self.sm = StateMachine()
        self.nodes = PipelineNodes(GroqAdapter())
        self.dag = DAG(
            [
                Node("preprocessing_hashing", self.nodes.preprocessing_hashing, []),
                Node("ocr_multi_script", self.nodes.ocr_multi_script, ["preprocessing_hashing"]),
                Node("dedup_cross_submission", self.nodes.dedup_cross_submission, ["preprocessing_hashing"]),
                Node("classification", self.nodes.classification, ["ocr_multi_script"]),
                Node("stamps_seals", self.nodes.stamps_seals, ["ocr_multi_script"]),
                Node("tamper_forensics", self.nodes.tamper_forensics, ["ocr_multi_script"]),
                Node("template_map", self.nodes.template_map, ["classification"]),
                Node("image_features", self.nodes.image_features, ["tamper_forensics", "preprocessing_hashing"]),
                Node("field_extract", self.nodes.field_extract, ["template_map", "ocr_multi_script", "classification"]),
                Node("fraud_behavioral_engine", self.nodes.fraud_behavioral_engine, ["dedup_cross_submission", "tamper_forensics", "image_features"]),
                Node("issuer_registry_verification", self.nodes.issuer_registry_verification, ["field_extract", "classification"]),
                Node("validation", self.nodes.validation, ["field_extract", "issuer_registry_verification"]),
                Node("merge_node", self.nodes.merge_node, ["validation", "fraud_behavioral_engine", "issuer_registry_verification", "stamps_seals", "tamper_forensics"]),
                Node("decision_explainability", self.nodes.decision_explainability, ["merge_node"]),
                Node("output_notification", self.nodes.output_notification, ["decision_explainability"]),
            ]
        )

    def create_document(
        self,
        tenant_id: str,
        citizen_id: str,
        file_name: str,
        raw_text: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        doc = Document(
            tenant_id=tenant_id,
            citizen_id=citizen_id,
            file_name=file_name,
            raw_text=raw_text,
            metadata=metadata or {},
        )
        row = self.repo.create_document(doc)
        self._event(doc.id, tenant_id, "document.received", {"file_name": file_name})
        return row

    def process_document(self, document_id: str) -> dict[str, Any]:
        doc = self.repo.get_document(document_id)
        if not doc:
            raise ValueError("Document not found")

        # State progression before merge decision.
        self._transition(doc, DocumentState.PREPROCESSED)
        self._transition(doc, DocumentState.OCR_DONE)
        self._transition(doc, DocumentState.CLASSIFIED)
        self._transition(doc, DocumentState.EXTRACTED)
        self._transition(doc, DocumentState.VALIDATED)

        ctx = self.dag.run(
            {
                "raw_text": doc.get("raw_text", ""),
                "tenant_id": doc["tenant_id"],
                "document_id": doc["id"],
                "repo": self.repo,
            }
        )

        dedup_hash = ctx["dedup_cross_submission"]["dedup_hash"]
        decision = ctx["output_notification"]["final_decision"]
        confidence = ctx["decision_explainability"]["confidence"]
        risk = ctx["decision_explainability"]["risk_score"]

        if decision in {"APPROVE", "REJECT"}:
            self._transition(doc, DocumentState.VERIFIED)
            self._transition(doc, DocumentState.APPROVED if decision == "APPROVE" else DocumentState.REJECTED)
        else:
            self._transition(doc, DocumentState.REVIEW_REQUIRED)

        updated = self.repo.update_document(
            doc["id"],
            dedup_hash=dedup_hash,
            confidence=confidence,
            risk_score=risk,
            decision=decision,
            derived=ctx["node_outputs"],
        )
        if not updated:
            raise RuntimeError("Document update failed")

        self._event(
            doc["id"],
            doc["tenant_id"],
            "decision.finalized",
            {
                "decision": decision,
                "confidence": confidence,
                "risk_score": risk,
                "execution_order": ctx["execution_order"],
            },
        )
        return updated

    def notify(self, document_id: str) -> dict[str, Any] | None:
        doc = self.repo.get_document(document_id)
        if not doc:
            return None
        state = DocumentState(doc["state"])
        if state not in {DocumentState.APPROVED, DocumentState.REJECTED}:
            return doc
        self._transition(doc, DocumentState.NOTIFIED)
        self._event(doc["id"], doc["tenant_id"], "notification.sent", {"channel": "PORTAL"})
        return self.repo.get_document(document_id)

    def open_dispute(self, document_id: str, reason: str, evidence_note: str) -> dict[str, Any]:
        doc = self.repo.get_document(document_id)
        if not doc:
            raise ValueError("Document not found")

        state = DocumentState(doc["state"])
        if state == DocumentState.REJECTED:
            self._transition(doc, DocumentState.DISPUTED)
        elif state != DocumentState.NOTIFIED:
            raise ValueError("Dispute allowed after rejection/notification only")

        dispute = Dispute(
            document_id=doc["id"],
            tenant_id=doc["tenant_id"],
            reason=reason,
            evidence_note=evidence_note,
        )
        row = self.repo.create_dispute(dispute)
        self._event(doc["id"], doc["tenant_id"], "dispute.opened", {"reason": reason})
        return row

    def manual_decision(self, document_id: str, decision: str) -> dict[str, Any]:
        doc = self.repo.get_document(document_id)
        if not doc:
            raise ValueError("Document not found")

        if DocumentState(doc["state"]) != DocumentState.REVIEW_REQUIRED:
            raise ValueError("Manual decision is only allowed from REVIEW_REQUIRED")

        if decision == "APPROVE":
            self._transition(doc, DocumentState.APPROVED)
        elif decision == "REJECT":
            self._transition(doc, DocumentState.REJECTED)
        else:
            raise ValueError("Unsupported decision")

        updated = self.repo.update_document(doc["id"], decision=decision)
        if not updated:
            raise RuntimeError("Update failed")
        self._event(doc["id"], doc["tenant_id"], "manual.review.completed", {"decision": decision})
        return updated

    def list_documents(self, tenant_id: str) -> list[dict[str, Any]]:
        return self.repo.list_documents(tenant_id)

    def list_events(self, document_id: str) -> list[dict[str, Any]]:
        return self.repo.list_events(document_id)

    def list_disputes(self, tenant_id: str) -> list[dict[str, Any]]:
        return self.repo.list_disputes(tenant_id)

    def _transition(self, doc: dict[str, Any], target: DocumentState) -> None:
        current = DocumentState(doc["state"])
        nxt = self.sm.transition(current, target)
        updated = self.repo.update_document(doc["id"], state=nxt)
        if not updated:
            raise RuntimeError("State update failed")
        doc.update(updated)
        self._event(doc["id"], doc["tenant_id"], "document.state.changed", {"from": current.value, "to": target.value})

    def _event(self, document_id: str, tenant_id: str, event_type: str, payload: dict[str, Any]) -> None:
        event = DocumentEvent(document_id=document_id, tenant_id=tenant_id, event_type=event_type, payload=payload)
        self.repo.add_event(event)
