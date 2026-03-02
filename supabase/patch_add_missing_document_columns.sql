-- Safe patch for older deployments missing newer columns used by the app.
-- Run this in Supabase SQL Editor.

alter table if exists public.documents
  add column if not exists file_path text,
  add column if not exists ocr_engine text,
  add column if not exists preprocess_output jsonb,
  add column if not exists classification_output jsonb,
  add column if not exists extraction_output jsonb,
  add column if not exists validation_output jsonb,
  add column if not exists fraud_output jsonb,
  add column if not exists confidence double precision,
  add column if not exists risk_score double precision,
  add column if not exists decision text,
  add column if not exists metadata jsonb,
  add column if not exists review_notes text,
  add column if not exists reviewed_at timestamptz,
  add column if not exists processed_at timestamptz,
  add column if not exists last_actor text,
  add column if not exists last_actor_role text,
  add column if not exists created_at timestamptz default now(),
  add column if not exists updated_at timestamptz default now();

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
