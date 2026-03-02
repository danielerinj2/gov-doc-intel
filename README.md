# GovDocIQ MVP

GovDocIQ is an MVP for government document digitization and verification:

- Ingestion: upload + preview + basic file validation
- Preprocessing: deskew, resize, contrast enhancement
- Classification: hybrid keyword/layout heuristic for 3 doc types
- OCR: Tesseract fallback by default; PaddleOCR optional (`requirements-paddle.txt`)
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

Optional (PaddleOCR acceleration where supported):

```bash
pip install -r requirements-paddle.txt
```

## Supabase

If `SUPABASE_URL` and `SUPABASE_SERVICE_KEY` (or `SUPABASE_KEY`) are configured, the app uses Supabase tables.
Otherwise it runs with in-memory persistence.

Auth uses Supabase Auth (email/password) and recovery endpoints.
You can switch auth provider with `AUTH_PROVIDER`:

- `supabase` (default)
- `appwrite`

When `AUTH_PROVIDER=appwrite`, persistence can also use Appwrite Database collections (`documents`, `reviews`, `audit_events`) if `APPWRITE_API_KEY` and collection setup are present.

## SendGrid

Set `SENDGRID_API_KEY` and `SENDGRID_FROM_EMAIL` to send signup, reset, and username reminder emails from your sender.

Apply schema:

```sql
-- run supabase/schema.sql
```

If you see `PGRST204` for missing columns (for example `classification_output`), run:

```sql
-- run supabase/patch_add_missing_document_columns.sql
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
- `AUTH_PROVIDER`

If using Appwrite auth:

- `APPWRITE_ENDPOINT`
- `APPWRITE_PROJECT_ID`
- `APPWRITE_API_KEY` (optional for auth flows)
- `APPWRITE_DATABASE_ID`
- `APPWRITE_COLLECTION_DOCUMENTS`
- `APPWRITE_COLLECTION_REVIEWS`
- `APPWRITE_COLLECTION_AUDIT_EVENTS`

## Appwrite Database Setup

To create required Appwrite database/collections/attributes:

```bash
python scripts/setup_appwrite.py
```

Advanced classifiers are optional. If `layoutlm` or `fusion` models/artifacts are missing, the app automatically falls back to the heuristic classifier.
