from __future__ import annotations

from enum import Enum


class DocumentState(str, Enum):
    RECEIVED = "RECEIVED"
    PREPROCESSING = "PREPROCESSING"
    OCR_COMPLETE = "OCR_COMPLETE"
    BRANCHED = "BRANCHED"
    MERGED = "MERGED"
    WAITING_FOR_REVIEW = "WAITING_FOR_REVIEW"
    REVIEW_IN_PROGRESS = "REVIEW_IN_PROGRESS"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    DISPUTED = "DISPUTED"
    EXPIRED = "EXPIRED"
    FAILED = "FAILED"
    ARCHIVED = "ARCHIVED"


ALLOWED_TRANSITIONS: dict[DocumentState, set[DocumentState]] = {
    DocumentState.RECEIVED: {DocumentState.PREPROCESSING},
    DocumentState.PREPROCESSING: {DocumentState.OCR_COMPLETE},
    DocumentState.OCR_COMPLETE: {DocumentState.BRANCHED},
    DocumentState.BRANCHED: {DocumentState.MERGED},
    DocumentState.MERGED: {
        DocumentState.WAITING_FOR_REVIEW,
        DocumentState.APPROVED,
        DocumentState.REJECTED,
    },
    DocumentState.WAITING_FOR_REVIEW: {DocumentState.REVIEW_IN_PROGRESS, DocumentState.EXPIRED},
    DocumentState.REVIEW_IN_PROGRESS: {DocumentState.APPROVED, DocumentState.REJECTED},
    DocumentState.REJECTED: {DocumentState.DISPUTED, DocumentState.ARCHIVED},
    DocumentState.DISPUTED: {DocumentState.REVIEW_IN_PROGRESS},
    DocumentState.APPROVED: {DocumentState.ARCHIVED},
    DocumentState.EXPIRED: {DocumentState.ARCHIVED},
    DocumentState.FAILED: {DocumentState.ARCHIVED},
    DocumentState.ARCHIVED: set(),
}
