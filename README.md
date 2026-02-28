# gov-doc-intel

Streamlit + Supabase + Groq implementation for a DAG-based government document intelligence platform.

## Architecture implemented
- DAG pipeline with parallel branches and merge/decision nodes
- Document state machine + event trail
- Logical multi-tenancy with department-scoped isolation
- Officer-bound tenant authorization (one officer -> one tenant)
- Tenant-scoped templates, rules, fraud scope, logs, and exports
- Review/reject/dispute workflow
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
4. Run app:
   ```bash
   streamlit run streamlit_app.py
   ```

## Important Supabase fix
Use:
- `SUPABASE_URL=https://<project-ref>.supabase.co`
- `SUPABASE_KEY=<publishable-or-service-key>`

Do not use the Postgres connection string in `SUPABASE_URL`.

Default Groq model is `llama-3.3-70b-versatile` (override with `GROQ_MODEL`).
Cloudflare-sensitive runtimes can set `GROQ_USER_AGENT` (already used in SDK calls).

If using publishable key, your app requests must include a signed-in user JWT and that user must exist in `public.tenant_memberships`.

## Multi-Tenancy Guarantees
- Templates and rules are tenant-scoped (`tenant_templates`, `tenant_rules`).
- Fraud dedup scope is tenant-only by default and can be enabled cross-tenant via `tenant_policies.cross_tenant_fraud_enabled`.
- Officers are bound to a single tenant (`officers` table + service authorization checks).
- Audit logs are tenant-filtered (`document_events.tenant_id` + filtered queries/RLS).
- Batch export is tenant-only and policy-gated (`tenant_policies.export_enabled`).
- Tenant storage bucket mapping is isolated (`tenant_storage_buckets` + storage RLS).

## First Run in UI
1. Use sidebar to set `Tenant ID` and `Officer ID`.
2. Click `Register / Bind Officer` once.
3. Then ingest/process documents.

## Verify env quickly
```bash
python3 scripts/check_setup.py
```

## Debug Groq
```bash
./scripts/debug_groq.py
```
