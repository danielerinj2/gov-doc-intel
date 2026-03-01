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
   - For Streamlit Cloud, use `Secrets` TOML format:
     ```toml
     SUPABASE_URL="https://<project-ref>.supabase.co"
     SUPABASE_SERVICE_KEY="..."
     SUPABASE_ANON_KEY="..."
     GROQ_API_KEY="..."
     GROQ_MODEL="llama-3.1-70b-versatile"
     GROQ_USER_AGENT="Mozilla/5.0 ..."
     APP_ENV="prod"
     ```
   - Do not use `.env` style with spaces around `=` in Streamlit Secrets.
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
5. Optional: run API service (for integrations/webhooks/offline workers):
   ```bash
   uvicorn app.api.main:app --host 0.0.0.0 --port 8000 --reload
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
- `app/events/backends.py`

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
Product-facing PRD: `docs/prd.md`.

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
3. Choose your role and navigate role-aware sections:
   - `Citizen Portal (Upload & Status)` (public journey simulation)
   - `Intake & Processing`
   - `Review Workbench`
   - `Dispute Desk`
   - `Fraud & Authenticity`
   - `Citizen Communication`
   - `Audit Trail & Explainability`
   - `Governance & KPI`
   - `Ops & DR Monitor`
   - `Integrations (API/Webhook/Export)`
   - `Offline Sync Console`
4. Select a document in sidebar to load shared `Document Header` and role-specific fields.
5. Tenant admin users can manage:
   - `A1` Template and Rule Management (versioned)
   - `A2` User and Role Management (officer accounts)
   - `A3` Governance/KPI controls and policy updates

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

## External API Surface (FastAPI)
- Entry point: `app/api/main.py`
- Required headers for tenant-safe access:
  - `X-Tenant-ID`
  - `X-Officer-ID`
  - `X-API-Key` (optional but recommended for app-to-app calls)
- Core endpoints:
  - `POST /documents`
  - `POST /documents/{document_id}/process`
  - `GET /documents/{document_id}/status`
  - `GET /documents/{document_id}/result`
  - `GET /documents/{document_id}/events`
  - `POST /documents/{document_id}/review/start`
  - `POST /documents/{document_id}/review/decision`
  - `POST /documents/{document_id}/dispute`
  - `GET /tenants/{tenant_id}/dashboard`
  - `GET /tenants/{tenant_id}/governance`
  - `GET /tenants/{tenant_id}/kpis`
  - `POST /tenants/{tenant_id}/offline/sync`
  - `POST /tenants/{tenant_id}/api-keys`

## Offline Worker (Rate-Controlled Sync)
Run a sync batch for provisional offline documents:
```bash
python3 scripts/offline_sync_worker.py \
  --tenant-id TENANT_A \
  --officer-id officer-sync \
  --capacity-per-minute 50
```
This applies backlog pressure handling (`QUEUE_OVERFLOW`) and then syncs up to configured capacity.

## MLOps Curation Script (Correction Validation Gate)
Build a curated JSONL dataset from gated corrections:
```bash
python3 scripts/mlops_curate_training_data.py \
  --tenant-id TENANT_A \
  --output artifacts/training/tenant_a_curated.jsonl \
  --approve-gates
```

## Config for Backends
Environment flags in `.env`:
- Event bus:
  - `EVENT_BUS_BACKEND=inmemory|kafka|pulsar`
  - `KAFKA_BOOTSTRAP_SERVERS`, `KAFKA_TOPIC`
  - `PULSAR_SERVICE_URL`, `PULSAR_TOPIC`
- OCR:
  - `OCR_BACKEND=heuristic|tesseract|easyocr`
  - `OCR_DEFAULT_LANG=eng`
- Authenticity/fraud:
  - `AUTHENTICITY_BACKEND=heuristic`
  - `FRAUD_CALIBRATION_WEIGHTS={"image":0.35,"behavioral":0.35,"issuer":0.30}`
- Issuer verification:
  - `ISSUER_REGISTRY_BASE_URL`
  - `ISSUER_REGISTRY_TOKEN`
