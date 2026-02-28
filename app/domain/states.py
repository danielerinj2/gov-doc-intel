from __future__ import annotations

from enum import Enum


class DocumentState(str, Enum):
    RECEIVED = "RECEIVED"
    PREPROCESSED = "PREPROCESSED"
    OCR_DONE = "OCR_DONE"
    CLASSIFIED = "CLASSIFIED"
    EXTRACTED = "EXTRACTED"
    VALIDATED = "VALIDATED"
    VERIFIED = "VERIFIED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    DISPUTED = "DISPUTED"
    REOPENED = "REOPENED"
    NOTIFIED = "NOTIFIED"


ALLOWED_TRANSITIONS: dict[DocumentState, set[DocumentState]] = {
    DocumentState.RECEIVED: {DocumentState.PREPROCESSED},
    DocumentState.PREPROCESSED: {DocumentState.OCR_DONE},
    DocumentState.OCR_DONE: {DocumentState.CLASSIFIED},
    DocumentState.CLASSIFIED: {DocumentState.EXTRACTED},
    DocumentState.EXTRACTED: {DocumentState.VALIDATED},
    DocumentState.VALIDATED: {DocumentState.VERIFIED, DocumentState.REVIEW_REQUIRED},
    DocumentState.VERIFIED: {DocumentState.APPROVED, DocumentState.REJECTED},
    DocumentState.REVIEW_REQUIRED: {DocumentState.APPROVED, DocumentState.REJECTED},
    DocumentState.REJECTED: {DocumentState.DISPUTED, DocumentState.NOTIFIED},
    DocumentState.DISPUTED: {DocumentState.REOPENED},
    DocumentState.REOPENED: {DocumentState.REVIEW_REQUIRED},
    DocumentState.APPROVED: {DocumentState.NOTIFIED},
    DocumentState.NOTIFIED: set(),
}
