# gov-doc-intel

Streamlit + Supabase + Groq implementation for a DAG-based government document intelligence platform.

## Architecture implemented
- DAG pipeline with parallel branches and merge/decision nodes
- Formal governance state machine + event trail
- Logical multi-tenancy with department-scoped isolation
- Officer-bound tenant authorization (one officer -> one tenant)
- Tenant-scoped templates, rules, fraud scope, logs, and exports
- Offline conflict framework (provisional local results, central source-of-truth)
- Citizen notifications + dispute + review SLA escalation workflow
- Unified versioned `document_record` persistence contract
- Level-2 module boundaries across OCR, classification, template/rules, extraction, validation, authenticity, fraud, issuer verification, explainability/audit, human review workload, output integration, offline sync, and monitoring/MLOps
- AI audit logs, module metrics, webhook outbox, human review assignment queue, and correction validation gate
- Governance/Ops/Tenancy layer with tenant data policies, partition config, platform oversight grants, operational runbooks, and governance audit reviews
- KPI/SLA target management, rollout phase planning, and risk register tracking (Part 5)
- Supabase persistence with in-memory fallback

## Setup
1. Create venv and install requirements:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```
2. Configure `.env` using `.env.example`.
3. Apply SQL schema in Supabase SQL editor:
   - `supabase/schema.sql`
   - `supabase/rls_policies.sql` (for publishable-key + authenticated JWT access)
   - If your DB was created earlier, run `supabase/part2_contracts_patch.sql` once
   - If your DB predates Part-3 module tables, run `supabase/part3_operational_patch.sql` once
   - If your DB predates Part-4 governance tables, run `supabase/part4_governance_patch.sql` once
   - If your DB predates Part-5 KPI/rollout/risk tables, run `supabase/part5_kpi_rollout_patch.sql` once
4. Run app:
   ```bash
   streamlit run streamlit_app.py
   ```

## Important Supabase fix
Use:
- `SUPABASE_URL=https://<project-ref>.supabase.co`
- `SUPABASE_KEY=<publishable-or-service-key>`

Do not use the Postgres connection string in `SUPABASE_URL`.

Default Groq model is `llama-3.1-70b-versatile` (override with `GROQ_MODEL`).
Cloudflare-sensitive runtimes can set `GROQ_USER_AGENT` (already used in SDK calls).

If using publishable key, your app requests must include a signed-in user JWT and that user must exist in `public.tenant_memberships`.

## Multi-Tenancy Guarantees
- Templates and rules are tenant-scoped (`tenant_templates`, `tenant_rules`).
- Fraud dedup scope is tenant-only by default and can be enabled cross-tenant via `tenant_policies.cross_tenant_fraud_enabled`.
- Officers are bound to a single tenant (`officers` table + service authorization checks).
- Audit logs are tenant-filtered (`document_events.tenant_id` + filtered queries/RLS).
- Batch export is tenant-only and policy-gated (`tenant_policies.export_enabled`).
- Tenant storage bucket mapping is isolated (`tenant_storage_buckets` + storage RLS).

## Formal State Machine
Implemented document states:
- `RECEIVED`
- `PREPROCESSING`
- `OCR_COMPLETE`
- `BRANCHED`
- `MERGED`
- `WAITING_FOR_REVIEW`
- `REVIEW_IN_PROGRESS`
- `APPROVED`
- `REJECTED`
- `DISPUTED`
- `EXPIRED`
- `FAILED`
- `ARCHIVED`

Core transitions implemented include:
- `RECEIVED -> PREPROCESSING -> OCR_COMPLETE -> BRANCHED -> MERGED`
- `MERGED -> WAITING_FOR_REVIEW | APPROVED | REJECTED`
- `WAITING_FOR_REVIEW -> REVIEW_IN_PROGRESS`
- `REVIEW_IN_PROGRESS -> APPROVED | REJECTED`
- `REJECTED -> DISPUTED -> REVIEW_IN_PROGRESS`
- `APPROVED/REJECTED/EXPIRED/FAILED -> ARCHIVED`
- `ANY(non-archived) -> FAILED` on pipeline exception

Every transition is persisted with actor, reason, policy version, model versions, and timestamp in `document_events`.

## Event-Driven Contracts
Event contracts and validation are in:
- `app/events/contracts.py`
- `app/events/bus.py`

Implemented core events:
- `document.received`
- `document.preprocessed`
- `ocr.completed`
- `branch.started`
- `branch.completed.<module>`
- `document.merged`
- `document.flagged.for_review`
- `review.started`
- `review.completed`
- `document.approved`
- `document.rejected`
- `document.disputed`
- `document.fraud_flagged`
- `document.requires_reupload`
- `document.archived`
- `document.failed`
- `offline.conflict.detected`
- `offline.queue_overflow`
- `notification.sent`
- `review.escalated`
- `review.assignment.created`
- `webhook.queued`
- `correction.logged`

## Part-3 Operational Tables
Implemented tenant-scoped operational persistence:
- `model_audit_logs` (AI audit trail)
- `module_metrics` (latency/status monitoring)
- `human_review_assignments` (assignment + workload balancing)
- `webhook_outbox` (integration fan-out)
- `correction_events` + `correction_validation_gate` (correction validation gate)

## Part-4 Governance Tables
- `tenant_data_policies` (retention/archival/purge/training governance per tenant)
- `tenant_partition_configs` (logical vs physical partitioning and residency controls)
- `platform_access_grants` (explicitly justified cross-tenant access grants)
- `operational_runbooks` (incident and operations playbooks per tenant)
- `governance_audit_reviews` (oversight findings and review evidence)

Detailed governance notes: `docs/PART4_GOVERNANCE.md`.

## Part-5 Program and KPI Tables
- `tenant_kpi_targets` (target values for program/operational KPIs)
- `tenant_kpi_snapshots` (time-series measured KPI values)
- `tenant_rollout_phases` (phase-wise rollout tracking)
- `tenant_risk_register` (risk and mitigation registry)

Executive program/tender summary: `docs/PART5_EXECUTIVE_PRD.md`.

## Part-2 Data Contracts
Strict Pydantic schemas are implemented in:
- `app/contracts/schemas.py`

This includes versioned contracts for:
- OCR output
- Classification output
- Template definition
- Extraction output
- Validation output
- Visual authenticity output
- Fraud risk output
- Issuer verification output
- Unified `document_record`

Unified records are persisted per `document_id + job_id` in `document_records.record` (JSONB).

## Offline Conflict Resolution
- Local offline output is marked provisional (`provisional_legal_standing = NONE`).
- Central pipeline output is authoritative on sync.
- Conflicts emit `offline.conflict.detected`.
- Backlog pressure emits `offline.queue_overflow` and sets `offline_sync_status=QUEUE_OVERFLOW`.

## DR / Failover
- DR targets and failover config are exposed by `app/services/dr_service.py`:
  - `RPO <= 5 min`
  - `RTO <= 15 min`
  - Resume from last committed event

## First Run in UI
1. Use sidebar to set `Tenant ID` and `Officer ID`.
2. Click `Register / Bind Officer` once.
3. Then ingest/process documents.

## Verify env quickly
```bash
python3 scripts/check_setup.py
```

If app status shows `PART2_SCHEMA_READY=false`, apply:
- `supabase/part2_contracts_patch.sql`
- then rerun `supabase/rls_policies.sql`

If app status shows `PART3_SCHEMA_READY=false`, apply:
- `supabase/part3_operational_patch.sql`
- then rerun `supabase/rls_policies.sql`

If app status shows `PART4_SCHEMA_READY=false`, apply:
- `supabase/part4_governance_patch.sql`
- then rerun `supabase/rls_policies.sql`

If app status shows `PART5_SCHEMA_READY=false`, apply:
- `supabase/part5_kpi_rollout_patch.sql`
- then rerun `supabase/rls_policies.sql`

## Debug Groq
```bash
./scripts/debug_groq.py
```
