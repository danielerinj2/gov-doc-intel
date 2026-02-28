from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


ScriptType = Literal["DEVANAGARI", "LATIN", "TAMIL", "MIXED", "UNKNOWN"]
FieldType = Literal["STRING", "DATE", "NUMBER", "ID", "ADDRESS", "ENUM"]
ValidationStatus = Literal["PASS", "FAIL", "WARN", "UNKNOWN"]
LifecycleStatus = Literal["ACTIVE", "DEPRECATED", "RETIRED"]
RiskLevel = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]


class BBox(BaseModel):
    page_number: int = Field(ge=1)
    x_min: float = Field(ge=0.0, le=1.0)
    y_min: float = Field(ge=0.0, le=1.0)
    x_max: float = Field(ge=0.0, le=1.0)
    y_max: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _validate_bounds(self) -> "BBox":
        if self.x_max < self.x_min or self.y_max < self.y_min:
            raise ValueError("bbox max coordinates must be >= min coordinates")
        return self


class OCRWord(BaseModel):
    word_id: str
    text: str
    confidence: float = Field(ge=0.0, le=1.0)
    bbox: BBox
    is_uncertain: bool = False


class OCRLine(BaseModel):
    line_id: str
    text: str
    confidence: float = Field(ge=0.0, le=1.0)
    bbox: BBox
    words: list[OCRWord] = Field(default_factory=list)


class OCRPage(BaseModel):
    page_number: int = Field(ge=1)
    width_px: int = Field(gt=0)
    height_px: int = Field(gt=0)
    script: ScriptType = "UNKNOWN"
    lines: list[OCRLine] = Field(default_factory=list)


class PreprocessingMetadata(BaseModel):
    steps_applied: list[str] = Field(default_factory=list)
    original_dpi: int | None = None
    estimated_quality_score: float = Field(ge=0.0, le=1.0)


class ModelMetadata(BaseModel):
    model_id: str
    model_version: str


class OCROutput(BaseModel):
    schema_version: str = "1.0"
    document_id: str
    job_id: str
    tenant_id: str
    pages: list[OCRPage] = Field(default_factory=list)
    preprocessing_metadata: PreprocessingMetadata
    model_metadata: ModelMetadata


class ClassificationOutput(BaseModel):
    schema_version: str = "1.0"
    document_id: str
    job_id: str
    tenant_id: str
    doc_type: str
    doc_subtype: str | None = None
    region_code: str | None = None
    template_id: str
    template_version: str
    confidence: float = Field(ge=0.0, le=1.0)
    model_metadata: ModelMetadata
    low_confidence: bool = False
    reasons: list[str] = Field(default_factory=list)


class Anchor(BaseModel):
    type: str
    pattern: str
    proximity_radius: float | None = None


class TemplateField(BaseModel):
    field_name: str
    field_label: dict[str, str] = Field(default_factory=dict)
    field_type: FieldType
    required: bool = True
    expected_bbox: BBox
    anchors: list[Anchor] = Field(default_factory=list)
    validation_rule_refs: list[str] = Field(default_factory=list)


class VisualMarkerDefinition(BaseModel):
    marker_type: str
    marker_name: str
    expected_bbox: BBox
    required: bool = False


class TemplatePage(BaseModel):
    page_number: int = Field(ge=1)
    fields: list[TemplateField] = Field(default_factory=list)
    visual_markers: list[VisualMarkerDefinition] = Field(default_factory=list)


class TemplateLifecycle(BaseModel):
    status: LifecycleStatus = "ACTIVE"
    effective_from: str
    effective_to: str | None = None


class TemplateDefinition(BaseModel):
    schema_version: str = "1.0"
    tenant_id: str
    template_id: str
    template_version: str
    doc_type: str
    doc_subtype: str | None = None
    region_code: str | None = None
    description: str | None = None
    pages: list[TemplatePage] = Field(default_factory=list)
    policy_rule_set_id: str
    lifecycle: TemplateLifecycle


class ExtractedField(BaseModel):
    field_name: str
    raw_text: str
    normalized_value: str | None = None
    bbox: BBox | None = None
    source: Literal["OCR", "DERIVED", "MANUAL"] = "OCR"
    confidence: float = Field(ge=0.0, le=1.0)
    page_number: int | None = None
    line_ids: list[str] = Field(default_factory=list)
    word_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ExtractionOutput(BaseModel):
    schema_version: str = "1.0"
    document_id: str
    job_id: str
    tenant_id: str
    template_id: str
    template_version: str
    fields: list[ExtractedField] = Field(default_factory=list)
    model_metadata: ModelMetadata


class RuleResult(BaseModel):
    rule_id: str
    status: ValidationStatus
    reason_code: str
    message: str


class MLCheckResult(BaseModel):
    check_id: str
    status: ValidationStatus
    score: float = Field(ge=0.0, le=1.0)
    reason_code: str
    message: str


class FieldValidationResult(BaseModel):
    field_name: str
    status: ValidationStatus
    rule_results: list[RuleResult] = Field(default_factory=list)
    ml_checks: list[MLCheckResult] = Field(default_factory=list)
    final_status: ValidationStatus
    final_reason_code: str


class DocumentCheckResult(BaseModel):
    check_id: str
    status: ValidationStatus
    reason_code: str
    message: str


class ValidationModelMetadata(BaseModel):
    rule_engine_version: str
    ml_validator_model_id: str
    ml_validator_model_version: str


class ValidationOutput(BaseModel):
    schema_version: str = "1.0"
    document_id: str
    job_id: str
    tenant_id: str
    template_id: str
    template_version: str
    rule_set_id: str
    field_results: list[FieldValidationResult] = Field(default_factory=list)
    document_level_results: list[DocumentCheckResult] = Field(default_factory=list)
    overall_status: ValidationStatus
    model_metadata: ValidationModelMetadata


class VisualMarkerResult(BaseModel):
    marker_type: str
    marker_name: str
    expected: bool
    detected: bool
    confidence: float = Field(ge=0.0, le=1.0)
    bbox: BBox | None = None
    reason_code: str


class ForensicsSignal(BaseModel):
    signal_id: str
    severity: Literal["LOW", "MEDIUM", "HIGH"]
    message: str
    bbox: BBox | None = None


class ImageForensics(BaseModel):
    tamper_signals: list[ForensicsSignal] = Field(default_factory=list)
    global_image_score: float = Field(ge=0.0, le=1.0)


class VisualAuthenticityOutput(BaseModel):
    schema_version: str = "1.0"
    document_id: str
    job_id: str
    tenant_id: str
    template_id: str
    template_version: str
    markers: list[VisualMarkerResult] = Field(default_factory=list)
    image_forensics: ImageForensics
    visual_authenticity_score: float = Field(ge=0.0, le=1.0)
    model_metadata: dict[str, str] = Field(default_factory=dict)


class FraudComponent(BaseModel):
    score: float = Field(ge=0.0, le=1.0)
    signals: list[str] = Field(default_factory=list)
    related_job_ids: list[str] = Field(default_factory=list)


class FraudRiskComponents(BaseModel):
    image_forensics_component: FraudComponent
    behavioral_component: FraudComponent
    issuer_mismatch_component: FraudComponent


class FraudRiskOutput(BaseModel):
    schema_version: str = "1.0"
    document_id: str
    job_id: str
    tenant_id: str
    components: FraudRiskComponents
    aggregate_fraud_risk_score: float = Field(ge=0.0, le=1.0)
    risk_level: RiskLevel
    disclaimer: str
    model_metadata: dict[str, str] = Field(default_factory=dict)


class FieldComparison(BaseModel):
    field_name: str
    local_value: str | None = None
    issuer_value: str | None = None
    match: bool


class IssuerResponseMetadata(BaseModel):
    registry_request_id: str | None = None
    response_time_ms: int | None = None
    error_code: str | None = None


class IssuerVerificationOutput(BaseModel):
    schema_version: str = "1.0"
    document_id: str
    job_id: str
    tenant_id: str
    doc_type: str
    issuer_name: str | None = None
    verification_method: Literal["REGISTRY_API", "DIGITAL_SIGNATURE", "VC", "NOT_AVAILABLE"]
    status: Literal["CONFIRMED", "MISMATCH", "NOT_FOUND", "ERROR", "NOT_AVAILABLE"]
    issuer_reference_id: str | None = None
    fields_compared: list[FieldComparison] = Field(default_factory=list)
    issuer_authenticity_score: float = Field(ge=0.0, le=1.0)
    response_metadata: IssuerResponseMetadata


class IngestionSubmittedBy(BaseModel):
    actor_type: Literal["CITIZEN", "OPERATOR", "SYSTEM"]
    actor_id: str


class IngestionRecord(BaseModel):
    source: Literal["ONLINE_PORTAL", "SERVICE_CENTER", "BATCH_UPLOAD", "API"]
    submitted_by: IngestionSubmittedBy
    received_at: str
    original_file_uri: str | None = None
    perceptual_hash: str
    dedup_matches: list[str] = Field(default_factory=list)


class StateHistoryEvent(BaseModel):
    from_state: str | None = None
    to_state: str
    at: str
    by: str
    reason: str


class StateMachineRecord(BaseModel):
    current_state: str
    history: list[StateHistoryEvent] = Field(default_factory=list)


class TemplateReference(BaseModel):
    template_id: str
    template_version: str


class ExplanationEntry(BaseModel):
    field_name: str | None = None
    messages: list[str] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)


class ExplainabilityRecord(BaseModel):
    field_explanations: list[ExplanationEntry] = Field(default_factory=list)
    document_explanations: list[dict[str, str]] = Field(default_factory=list)


class ScoreRecord(BaseModel):
    validation_status: ValidationStatus
    visual_authenticity_score: float = Field(ge=0.0, le=1.0)
    issuer_authenticity_score: float = Field(ge=0.0, le=1.0)
    aggregate_fraud_risk_score: float = Field(ge=0.0, le=1.0)


class HumanReviewEvent(BaseModel):
    officer_id: str
    action: str
    field_name: str | None = None
    old_value: str | None = None
    new_value: str | None = None
    decision: str | None = None
    reason: str
    at: str


class HumanReviewRecord(BaseModel):
    assigned_to_officer_id: str | None = None
    assigned_at: str | None = None
    review_events: list[HumanReviewEvent] = Field(default_factory=list)


class CitizenCommEvent(BaseModel):
    event_type: str
    channel: str
    message_template_id: str
    sent_at: str


class CitizenCommunicationRecord(BaseModel):
    preferred_channels: list[str] = Field(default_factory=list)
    events: list[CitizenCommEvent] = Field(default_factory=list)


class OfflineMetadataRecord(BaseModel):
    processed_offline: bool = False
    offline_node_id: str | None = None
    offline_model_versions: dict[str, str] = Field(default_factory=dict)
    first_seen_offline_at: str | None = None
    synced_to_central_at: str | None = None


class MLTrainingFlagsRecord(BaseModel):
    eligible_for_training: dict[str, bool] = Field(default_factory=dict)
    data_quality_notes: list[str] = Field(default_factory=list)


class RetentionPolicyRecord(BaseModel):
    policy_id: str
    retention_until: str
    archival_status: Literal["ACTIVE", "ARCHIVED", "PURGED"]


class DocumentRecord(BaseModel):
    schema_version: str = "1.0"
    document_id: str
    job_id: str
    tenant_id: str
    ingestion: IngestionRecord
    state_machine: StateMachineRecord
    ocr_output: OCROutput
    classification_output: ClassificationOutput
    template_definition_ref: TemplateReference
    extraction_output: ExtractionOutput
    validation_output: ValidationOutput
    visual_authenticity_output: VisualAuthenticityOutput
    fraud_risk_output: FraudRiskOutput
    issuer_verification_output: IssuerVerificationOutput
    explainability: ExplainabilityRecord
    scores: ScoreRecord
    human_review: HumanReviewRecord
    citizen_communication: CitizenCommunicationRecord
    offline_metadata: OfflineMetadataRecord
    ml_training_flags: MLTrainingFlagsRecord
    retention_policy: RetentionPolicyRecord


class DocumentRecordEnvelope(BaseModel):
    document_record: DocumentRecord
