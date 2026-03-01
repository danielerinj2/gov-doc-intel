# GovDocIQ MVP

GovDocIQ is an MVP for government document digitization and verification:

- Ingestion: upload + preview + basic file validation
- Preprocessing: deskew, resize, contrast enhancement
- Classification: hybrid keyword/layout heuristic for 3 doc types
- OCR: PaddleOCR primary (v5 `predict` API supported, fallback-safe)
- Field extraction: template-style regex extraction
- Validation: regex + basic cross-field checks
- Fraud signals: stamp/signature presence + layout consistency
- Human review: editable grid + approve/reject
- Audit logging: structured event log and review decisions

## Run

```bash
pip install -r requirements.txt
streamlit run streamlit_app.py
```

## Supabase

If `SUPABASE_URL` and `SUPABASE_SERVICE_KEY` (or `SUPABASE_KEY`) are configured, the app uses Supabase tables.
Otherwise it runs with in-memory persistence.

Auth uses Supabase Auth (email/password) and recovery endpoints.

## SendGrid

Set `SENDGRID_API_KEY` and `SENDGRID_FROM_EMAIL` to send signup, reset, and username reminder emails from your sender.

Apply schema:

```sql
-- run supabase/schema.sql
```

## Environment

Use `.env.example` as reference. Primary keys used by app:

- `SUPABASE_URL`
- `SUPABASE_SERVICE_KEY` or `SUPABASE_KEY`
- `OCR_BACKEND`
- `OCR_DEFAULT_LANG`
- `OCR_DEVICE`
- `OCR_MIN_CONFIDENCE`
- `CLASSIFIER_BACKEND` (`heuristic`/`layoutlm`/`fusion`)
- `APP_ENV`

Advanced classifiers are optional. If `layoutlm` or `fusion` models/artifacts are missing, the app automatically falls back to the heuristic classifier.
