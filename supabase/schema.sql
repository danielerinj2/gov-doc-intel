create extension if not exists "pgcrypto";

create table if not exists public.tenants (
    tenant_id text primary key,
    display_name text not null,
    residency_region text not null default 'default',
    status text not null default 'ACTIVE' check (status in ('ACTIVE', 'INACTIVE')),
    created_at timestamptz not null default now()
);

create table if not exists public.officers (
    officer_id text primary key,
    tenant_id text not null references public.tenants(tenant_id) on delete cascade,
    role text not null check (role in ('case_worker', 'reviewer', 'admin', 'auditor')),
    status text not null default 'ACTIVE' check (status in ('ACTIVE', 'SUSPENDED')),
    created_at timestamptz not null default now()
);
create index if not exists idx_officers_tenant on public.officers (tenant_id, status);

create table if not exists public.tenant_policies (
    tenant_id text primary key references public.tenants(tenant_id) on delete cascade,
    data_retention_days integer not null default 365 check (data_retention_days > 0),
    api_rate_limit_per_minute integer not null default 120 check (api_rate_limit_per_minute > 0),
    max_documents_per_day integer not null default 25000 check (max_documents_per_day > 0),
    cross_tenant_fraud_enabled boolean not null default false,
    export_enabled boolean not null default true,
    sms_enabled boolean not null default true,
    email_enabled boolean not null default true,
    portal_enabled boolean not null default true,
    whatsapp_enabled boolean not null default false,
    review_sla_days integer not null default 3,
    escalation_step_days integer not null default 1,
    residency_region text not null default 'default',
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists public.tenant_templates (
    id uuid primary key default gen_random_uuid(),
    tenant_id text not null references public.tenants(tenant_id) on delete cascade,
    document_type text not null,
    doc_subtype text,
    region_code text,
    description text,
    template_id text not null,
    template_version text not null default '2025.1.0',
    policy_rule_set_id text,
    version integer not null default 1,
    is_active boolean not null default true,
    config jsonb not null default '{}'::jsonb,
    lifecycle_status text not null default 'ACTIVE' check (lifecycle_status in ('ACTIVE', 'DEPRECATED', 'RETIRED')),
    effective_from timestamptz default now(),
    effective_to timestamptz,
    created_at timestamptz not null default now(),
    unique (tenant_id, document_type, version)
);
create index if not exists idx_tenant_templates_active on public.tenant_templates (tenant_id, document_type, is_active, version desc);

create table if not exists public.tenant_rules (
    id uuid primary key default gen_random_uuid(),
    tenant_id text not null references public.tenants(tenant_id) on delete cascade,
    document_type text not null,
    rule_set_id text,
    rule_name text not null,
    version integer not null default 1,
    is_active boolean not null default true,
    min_extract_confidence double precision not null default 0.6,
    min_approval_confidence double precision not null default 0.72,
    max_approval_risk double precision not null default 0.35,
    registry_required boolean not null default true,
    config jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    unique (tenant_id, document_type, version)
);
create index if not exists idx_tenant_rules_active on public.tenant_rules (tenant_id, document_type, is_active, version desc);

create table if not exists public.tenant_storage_buckets (
    id uuid primary key default gen_random_uuid(),
    tenant_id text not null references public.tenants(tenant_id) on delete cascade,
    bucket_name text not null unique,
    encryption_key_ref text,
    is_active boolean not null default true,
    created_at timestamptz not null default now(),
    unique (tenant_id, bucket_name)
);
create index if not exists idx_tenant_storage_buckets_tenant on public.tenant_storage_buckets (tenant_id, is_active);

create table if not exists public.tenant_api_keys (
    id uuid primary key default gen_random_uuid(),
    tenant_id text not null references public.tenants(tenant_id) on delete cascade,
    key_label text not null,
    key_hash text not null unique,
    status text not null default 'ACTIVE' check (status in ('ACTIVE', 'REVOKED')),
    created_at timestamptz not null default now(),
    last_used_at timestamptz
);
create index if not exists idx_tenant_api_keys_tenant_status on public.tenant_api_keys (tenant_id, status);

create table if not exists public.documents (
    id uuid primary key,
    tenant_id text not null references public.tenants(tenant_id) on delete cascade,
    citizen_id text not null,
    file_name text not null,
    raw_text text,
    metadata jsonb not null default '{}'::jsonb,
    state text not null,
    dedup_hash text,
    confidence double precision,
    risk_score double precision,
    decision text,
    template_id text,
    last_job_id text,
    derived jsonb,
    expires_at timestamptz,
    offline_processed boolean not null default false,
    offline_local_model_versions jsonb,
    offline_processed_at timestamptz,
    offline_synced_at timestamptz,
    offline_sync_status text,
    provisional_decision text,
    queue_overflow boolean not null default false,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

alter table public.documents add column if not exists metadata jsonb not null default '{}'::jsonb;
alter table public.documents add column if not exists template_id text;
alter table public.documents add column if not exists last_job_id text;
alter table public.documents add column if not exists expires_at timestamptz;
alter table public.documents add column if not exists offline_processed boolean not null default false;
alter table public.documents add column if not exists offline_local_model_versions jsonb;
alter table public.documents add column if not exists offline_processed_at timestamptz;
alter table public.documents add column if not exists offline_synced_at timestamptz;
alter table public.documents add column if not exists offline_sync_status text;
alter table public.documents add column if not exists provisional_decision text;
alter table public.documents add column if not exists queue_overflow boolean not null default false;

create index if not exists idx_documents_tenant_created on public.documents (tenant_id, created_at desc);
create index if not exists idx_documents_hash on public.documents (tenant_id, dedup_hash);
create index if not exists idx_documents_state on public.documents (tenant_id, state);
create index if not exists idx_documents_expires on public.documents (tenant_id, expires_at);

create table if not exists public.document_events (
    id uuid primary key,
    document_id uuid not null references public.documents(id) on delete cascade,
    tenant_id text not null references public.tenants(tenant_id) on delete cascade,
    actor_type text not null default 'SYSTEM',
    actor_id text,
    event_type text not null,
    payload jsonb not null,
    reason text,
    policy_version integer,
    model_versions jsonb,
    correlation_id text,
    causation_id text,
    created_at timestamptz not null default now()
);

alter table public.document_events add column if not exists actor_type text not null default 'SYSTEM';
alter table public.document_events add column if not exists actor_id text;
alter table public.document_events add column if not exists reason text;
alter table public.document_events add column if not exists policy_version integer;
alter table public.document_events add column if not exists model_versions jsonb;
alter table public.document_events add column if not exists correlation_id text;
alter table public.document_events add column if not exists causation_id text;

create index if not exists idx_events_doc_created on public.document_events (document_id, created_at asc);
create index if not exists idx_events_tenant_created on public.document_events (tenant_id, created_at asc);

create table if not exists public.disputes (
    id uuid primary key,
    document_id uuid not null references public.documents(id) on delete cascade,
    tenant_id text not null references public.tenants(tenant_id) on delete cascade,
    reason text not null,
    evidence_note text,
    status text not null default 'DISPUTE_SUBMITTED',
    created_at timestamptz not null default now()
);
create index if not exists idx_disputes_tenant_created on public.disputes (tenant_id, created_at desc);

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
create index if not exists idx_notifications_tenant_doc on public.citizen_notifications (tenant_id, document_id, created_at desc);

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
create index if not exists idx_review_escalations_tenant_status on public.review_escalations (tenant_id, status, created_at desc);

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
create index if not exists idx_document_records_tenant_doc_created on public.document_records (tenant_id, document_id, created_at desc);

-- Seed for bootstrap.
insert into public.tenants (tenant_id, display_name, residency_region)
values ('dept-education', 'Department of Education', 'default')
on conflict (tenant_id) do nothing;

insert into public.tenant_policies (
    tenant_id,
    data_retention_days,
    api_rate_limit_per_minute,
    max_documents_per_day,
    cross_tenant_fraud_enabled,
    export_enabled,
    sms_enabled,
    email_enabled,
    portal_enabled,
    whatsapp_enabled,
    review_sla_days,
    escalation_step_days,
    residency_region
)
values ('dept-education', 365, 120, 25000, false, true, true, true, true, false, 3, 1, 'default')
on conflict (tenant_id) do nothing;

insert into public.tenant_storage_buckets (tenant_id, bucket_name, encryption_key_ref)
values ('dept-education', 'tenant-dept-education', null)
on conflict (bucket_name) do nothing;

insert into public.officers (officer_id, tenant_id, role, status)
values ('officer-001', 'dept-education', 'case_worker', 'ACTIVE')
on conflict (officer_id) do nothing;
