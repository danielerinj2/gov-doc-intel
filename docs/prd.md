# Product Requirements Document (PRD)

## Contents
- Abstract
- Business Objectives
- KPI
- Success Criteria
- User Journeys
- Scenarios
- User Flow
- Functional Requirements
- Model Requirements
- Data Requirements
- Prompt Requirements
- Testing and Measurement
- Risks and Mitigations
- Costs
- Assumptions and Dependencies
- Compliance, Privacy, and Legal
- GTM and Rollout Plan

## Abstract
An AI-powered, multi-tenant Document Intelligence Platform for government that:
- Ingests citizen documents from online portals and service centers.
- Runs OCR (multi-script), classification, extraction, validation, authenticity, fraud, and issuer checks through a DAG-based, event-driven pipeline.
- Outputs structured, explainable decisions and flags to officers, with full auditability and offline support.

The system acts as a shared verification backbone across departments, improving speed, accuracy, and fraud detection while keeping humans as the final decision-makers.

## Business Objectives
- Reduce manual effort and turnaround time for document verification across departments.
- Increase detection of forged and tampered documents without overwhelming teams with false positives.
- Standardize verification logic, templates, and audit practices across schemes and departments.
- Provide a scalable, governance-grade platform that can onboard 20+ departments over time.
- Enable controlled, safe continuous improvement using officer corrections and MLOps.

## KPI
| Goal | Metric | Question |
| --- | --- | --- |
| Faster verification | Avg. processing time per document < 5 seconds (P95, online) | Is the platform materially faster than manual checks? |
| Higher automation | % documents auto-cleared without human review (target 30-40% in first 12 weeks) | How much work are we taking off officers? |
| OCR quality | OCR character accuracy >= 90% on major scripts | Are we extracting reliable text from regional docs? |
| Classification accuracy | Template classification accuracy >= 95% for in-scope types | Are documents correctly routed to templates? |
| Fraud and authenticity quality | Tamper detection recall >= 85%; fraud flag precision >= 80% | Are we catching fraud without flooding queues? |
| Governance and reliability | 100% decisions audit-traceable; availability >= 99.9% | Is this fit for legal scrutiny and daily operations? |

First 8-12 weeks focus KPIs:
- Processing time
- Automation percentage
- OCR and classification accuracy

## Success Criteria
- v1 running in production for at least one anchor tenant (assumption: Scholarship Board or similar welfare scheme).
- Minimum 30% of documents for that tenant auto-cleared end-to-end with no officer touch, with no material increase in fraud or error incidents.
- All approved and rejected documents have an explainable, reconstructible decision trail (models, rules, and human actions).
- No cross-tenant data leakage incidents; DR drills completed successfully with RPO/RTO within target.
- Officers and tenant admins can use the system with minimal training.

## User Journeys
### Primary Journeys (v1)
1. Citizen via online portal
- Uploads documents as part of an application.
- Receives confirmation and can later check status.
- If rejected, sees human-readable reason and next steps, and can raise dispute.

2. Service center operator (assisted mode)
- Logs into operator console for a specific tenant and center.
- Captures citizen application and scans/uploads documents.
- Sees real-time provisional OCR and extraction suggestions.
- Submits case; offline nodes store and sync later if connectivity is poor.

3. Backoffice verification officer
- Logs into review console (tenant-scoped).
- Sees prioritized queue of flagged or pending documents.
- Reviews structured fields, document images, authenticity and fraud signals, issuer checks, and explanations.
- Edits fields if needed, approves or rejects, and adds comments.

4. Senior officer and dispute handling
- Accesses disputed and escalated queue.
- Reviews prior decisions, system signals, and citizen evidence.
- Confirms or overturns decisions with full trail.

5. Tenant admin
- Manages templates, rules, and officer accounts.
- Updates rules via controlled UI.
- Reviews tenant analytics and SLAs.

6. Fraud and risk analyst (Phase 2)
- Uses fraud workspace to review suspicious patterns and anomalies.

## Scenarios
1. Standard structured document auto-clears
- OCR -> classification -> extraction -> validation -> authenticity/fraud -> issuer checks pass thresholds.
- System auto-approves and notifies citizen.

2. Missing signature or low-quality scan
- Visual authenticity detects missing/uncertain signature.
- Case routes to WAITING_FOR_REVIEW.

3. Fraud-suspected duplicate document
- Behavioral engine detects duplicate reuse.
- Case flagged HIGH and routed to specialized queue.

4. Offline service center bulk sync
- Local offline capture uses provisional OCR.
- On sync, central pipeline reprocesses and may override provisional output.

5. Citizen dispute after rejection
- Citizen receives rejection reason.
- Raises dispute; case moves to DISPUTED and senior review.

## User Flow
### Happy Path (Online, Auto-Approval)
1. Citizen submits application and uploads document.
2. System ingests document -> RECEIVED.
3. Preprocessing and hashing -> PREPROCESSING.
4. OCR and dedup run -> OCR_COMPLETE then BRANCHED.
5. Parallel branches run classification/template, visual authenticity, and dedup analytics.
6. Field extraction and validation execute.
7. Fraud and issuer modules compute risk/authenticity.
8. Merge node aggregates outputs -> MERGED.
9. Decision module auto-approves -> APPROVED.
10. Notifications and downstream integration events are emitted.

### Happy Path (Service Center Assisted)
1. Operator logs in and chooses tenant/scheme.
2. Captures application and uploads scans.
3. Provisional OCR assists form fill.
4. Operator corrects key fields and submits.
5. Central pipeline runs and returns final outcome.

### Alternative Flows
- Flagged review: MERGED -> WAITING_FOR_REVIEW -> REVIEW_IN_PROGRESS -> APPROVED or REJECTED.
- Dispute: REJECTED -> DISPUTED -> REVIEW_IN_PROGRESS (senior) -> APPROVED or REJECTED -> ARCHIVED.
- Offline overflow: backlog exceeds capacity -> QUEUE_OVERFLOW and alerts.

## Functional Requirements
### Key applications and pages (v1)
1. Citizen portal (v1-lite)
- C1: application and document upload
- C2: application status and notifications

2. Operator console
- O1: login and tenant selection
- O2: new application capture with upload
- O3: application list and center status

3. Officer review console
- R1: review queue with filters
- R2: case detail with evidence and actions

4. Tenant admin console
- A1: template and rule management
- A2: user and role management
- A3: tenant analytics and SLA dashboard

5. Platform admin and audit console
- P1: platform monitoring and tenant overview
- P2: audit and AI decision logs

Total v1 pages: approximately 11-13.

### Main inputs
- Document files (images/PDFs)
- Metadata: tenant_id, application id, citizen id, source channel
- Officer actions: corrections, decisions, comments
- Tenant config: templates, rules, retention and issuer settings

### Main outputs
- Structured document_record (per job_id)
- Status and decision states
- Field values, confidence, and validation statuses
- Authenticity/fraud risk scores and reason codes
- Citizen notifications
- API/webhook responses and CSV exports

### Auth and core flows
- Signup and invite flow for internal users (email; SSO in later phase)
- Login route to role-specific home
- Password reset flow for internal users

### Core functionality highlights
Operator capture:
- Validate tenant/scheme before upload
- Show quality score and re-scan prompts
- Save draft for offline/low-connectivity conditions

Officer review:
- Show image and extracted field evidence
- Show PASS/FAIL/WARN statuses and reason codes
- Allow correction, re-upload request, approve/reject with reason
- Persist all actions to audit trail

Tenant admin rules:
- CRUD template/rules with versioning and lifecycle
- Stage and validate before activation
- Prevent destructive edits on active configs

Audit reconstruction:
- Search by document_id/citizen/time
- Display full state history, model/rule versions, and human actions
- Export redacted legal audit report

## Model Requirements
| Specification | Requirement | Rationale |
| --- | --- | --- |
| Open vs proprietary | Hybrid approach; configurable providers | Flexibility with compliance |
| Context window | >= 8k tokens for explanation chains | Enough context for reasons |
| Modalities | Vision and text | Document-first workflow |
| Fine-tuning | Required for OCR/classification/extraction/fraud | Domain adaptation |
| Latency | OCR < 1.5s/page (P95); E2E < 5s (P95) online | UX and throughput |
| Parameters | Configurable per tenant/module | Performance-cost tuning |

## Data Requirements
- OCR, classification, extraction, and fraud datasets with tenant-specific coverage.
- document_record and ml_training_flags as central curation source.
- Pseudonymization/minimization for training where possible.
- Ongoing collection via correction validation gate.
- Quarterly retraining with canary and rollback controls.

## Prompt Requirements
- LLM outputs are advisory; business rules and human decisions remain authoritative.
- Citizen text must be simple and non-technical.
- Internal explanation outputs must be structured and schema-valid.
- Fallback to static templates when model confidence is low.

## Testing and Measurement
- Module-wise offline golden set evaluation.
- Regression tests for model/rule updates.
- Controlled online rollouts and A/B where needed.
- Canary + rollback for production safety.
- Live dashboards for latency, throughput, error rate, fraud rate, queue SLA, and DR status.

## Risks and Mitigations
| Risk | Mitigation |
| --- | --- |
| Low-quality scans | Preprocess + quality score + human fallback |
| Missing marker false negatives | Conservative thresholds + manual escalation |
| Over-reliance on model output | Explicit human-in-loop controls |
| Noisy corrections poison training | Validation gate + QA sampling |
| Offline contradiction | Central truth policy + clear citizen messaging |
| Cross-tenant leakage | RLS + RBAC + storage isolation + audits |
| Sync surges | Rate limiting + autoscaling + overflow queues |
| Rule inconsistency | Versioned policy with staged rollout |
| Legal challenge on AI decisions | Explainability + full audit + human final decision |

## Costs
High-level categories:
- Development: platform engineering, model integration, governance tooling.
- Operations: compute, storage, event infrastructure, observability.
- People: SRE/Ops, ML/Data, tenant onboarding/support.

## Assumptions and Dependencies
Assumptions:
- v1 starts with 1-3 anchor tenants.
- Web-first internal UX; no native mobile in phase 1.
- Citizen portal initially limited to upload/status/dispute initiation.
- Handwriting automation limited in phase 1.

Dependencies:
- Multi-zone hosting readiness.
- Issuer API availability for integrated doc types.
- Department ownership of template/rule policy lifecycle.
- Secure secret management and key rotation.

## Compliance, Privacy, and Legal
- Align with data protection law principles: purpose limitation, minimization, retention control.
- Tenant-specific data retention and archival rules.
- Strong RBAC and tenant isolation controls.
- Full auditability for model/rule/human decisions.
- Formal policy: AI assists verification; legal decisions remain with officers.
- Bias/fairness monitoring for risk modules where legally and operationally allowed.

## GTM and Rollout Plan
Phase 0 (2-3 months):
- Core ingestion, OCR/classification/extraction, baseline review UI, tenancy and monitoring.

Phase 1 (3-4 months):
- Validation, authenticity, basic fraud, event backbone, offline MVP, onboarding 3-5 departments.

Phase 2 (3 months):
- Issuer integrations, advanced fraud, explainability v2, DR/failover, compliance dashboards, correction validation gate.

Phase 3 (6-12 months):
- 20+ departments, stronger offline-first architecture, selective handwriting OCR, fraud workspace, multi-region scale.

Launch strategy:
- Start with 1-2 high-volume predictable schemes, then expand using measured wins.
