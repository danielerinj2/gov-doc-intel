-- Apply this if your database was created before Part-2 contracts were added.
-- Safe to run multiple times.

alter table if exists public.documents add column if not exists last_job_id text;
alter table if exists public.documents add column if not exists offline_processed boolean not null default false;
alter table if exists public.documents add column if not exists offline_local_model_versions jsonb;
alter table if exists public.documents add column if not exists offline_processed_at timestamptz;
alter table if exists public.documents add column if not exists offline_synced_at timestamptz;
alter table if exists public.documents add column if not exists offline_sync_status text;
alter table if exists public.documents add column if not exists provisional_decision text;
alter table if exists public.documents add column if not exists queue_overflow boolean not null default false;

alter table if exists public.document_events add column if not exists actor_type text not null default 'SYSTEM';
alter table if exists public.document_events add column if not exists actor_id text;
alter table if exists public.document_events add column if not exists reason text;
alter table if exists public.document_events add column if not exists policy_version integer;
alter table if exists public.document_events add column if not exists model_versions jsonb;
alter table if exists public.document_events add column if not exists correlation_id text;
alter table if exists public.document_events add column if not exists causation_id text;

create table if not exists public.citizen_notifications (
    id uuid primary key default gen_random_uuid(),
    tenant_id text not null references public.tenants(tenant_id) on delete cascade,
    document_id uuid not null references public.documents(id) on delete cascade,
    citizen_id text not null,
    channel text not null,
    event_type text not null,
    message text not null,
    status text not null default 'SENT',
    metadata jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    sent_at timestamptz
);

create table if not exists public.review_escalations (
    id uuid primary key default gen_random_uuid(),
    tenant_id text not null references public.tenants(tenant_id) on delete cascade,
    document_id uuid not null references public.documents(id) on delete cascade,
    escalation_level integer not null,
    assignee_role text not null,
    reason text not null,
    status text not null default 'OPEN',
    created_at timestamptz not null default now(),
    resolved_at timestamptz
);

create table if not exists public.document_records (
    id uuid primary key default gen_random_uuid(),
    tenant_id text not null references public.tenants(tenant_id) on delete cascade,
    document_id uuid not null references public.documents(id) on delete cascade,
    job_id text not null,
    schema_version text not null default '1.0',
    record jsonb not null,
    created_at timestamptz not null default now(),
    unique (document_id, job_id)
);

create index if not exists idx_notifications_tenant_doc on public.citizen_notifications (tenant_id, document_id, created_at desc);
create index if not exists idx_review_escalations_tenant_status on public.review_escalations (tenant_id, status, created_at desc);
create index if not exists idx_document_records_tenant_doc_created on public.document_records (tenant_id, document_id, created_at desc);
