create extension if not exists "pgcrypto";

create table if not exists public.documents (
    id uuid primary key,
    tenant_id text not null,
    citizen_id text not null,
    file_name text not null,
    raw_text text,
    metadata jsonb not null default '{}'::jsonb,
    state text not null,
    dedup_hash text,
    confidence double precision,
    risk_score double precision,
    decision text,
    derived jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists idx_documents_tenant_created on public.documents (tenant_id, created_at desc);
create index if not exists idx_documents_hash on public.documents (tenant_id, dedup_hash);
create index if not exists idx_documents_state on public.documents (state);

create table if not exists public.document_events (
    id uuid primary key,
    document_id uuid not null references public.documents(id) on delete cascade,
    tenant_id text not null,
    event_type text not null,
    payload jsonb not null,
    created_at timestamptz not null default now()
);

create index if not exists idx_events_doc_created on public.document_events (document_id, created_at asc);

create table if not exists public.disputes (
    id uuid primary key,
    document_id uuid not null references public.documents(id) on delete cascade,
    tenant_id text not null,
    reason text not null,
    evidence_note text,
    status text not null default 'OPEN',
    created_at timestamptz not null default now()
);

create index if not exists idx_disputes_tenant_created on public.disputes (tenant_id, created_at desc);
