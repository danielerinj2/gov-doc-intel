create extension if not exists pgcrypto;

create table if not exists public.documents (
  id uuid primary key default gen_random_uuid(),
  citizen_id text not null,
  file_name text not null,
  file_path text,
  raw_text text,
  ocr_text text,
  ocr_confidence double precision,
  ocr_engine text,
  preprocess_output jsonb,
  classification_output jsonb,
  extraction_output jsonb,
  validation_output jsonb,
  fraud_output jsonb,
  confidence double precision,
  risk_score double precision,
  state text,
  decision text,
  metadata jsonb,
  review_notes text,
  reviewed_at timestamptz,
  processed_at timestamptz,
  last_actor text,
  last_actor_role text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.reviews (
  id uuid primary key default gen_random_uuid(),
  document_id uuid not null references public.documents(id) on delete cascade,
  actor_id text not null,
  actor_role text,
  decision text not null,
  notes text,
  payload jsonb,
  created_at timestamptz not null default now()
);

create table if not exists public.audit_events (
  id uuid primary key default gen_random_uuid(),
  document_id uuid not null references public.documents(id) on delete cascade,
  actor_id text,
  actor_role text,
  event_type text not null,
  payload jsonb,
  created_at timestamptz not null default now()
);

create index if not exists idx_documents_state on public.documents(state);
create index if not exists idx_documents_updated_at on public.documents(updated_at desc);
create index if not exists idx_reviews_document_id on public.reviews(document_id);
create index if not exists idx_audit_events_document_id on public.audit_events(document_id);

alter table public.documents enable row level security;
alter table public.reviews enable row level security;
alter table public.audit_events enable row level security;

-- Service-role backend can bypass RLS.
-- For anon/authenticated clients, add policies as needed for your auth model.
