# gov-doc-intel

Streamlit + Supabase + Groq implementation for a DAG-based government document intelligence platform.

## Architecture implemented
- DAG pipeline with parallel branches and merge/decision nodes
- Document state machine + event trail
- Tenant-scoped document processing
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

If using publishable key, your app requests must include a signed-in user JWT and that user must exist in `public.tenant_memberships`.

## Verify env quickly
```bash
python3 scripts/check_setup.py
```
