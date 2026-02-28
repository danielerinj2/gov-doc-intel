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
    residency_region text not null default 'default',
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists public.tenant_templates (
    id uuid primary key default gen_random_uuid(),
    tenant_id text not null references public.tenants(tenant_id) on delete cascade,
    document_type text not null,
    template_id text not null,
    version integer not null default 1,
    is_active boolean not null default true,
    config jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    unique (tenant_id, document_type, version)
);

create index if not exists idx_tenant_templates_active on public.tenant_templates (tenant_id, document_type, is_active, version desc);

create table if not exists public.tenant_rules (
    id uuid primary key default gen_random_uuid(),
    tenant_id text not null references public.tenants(tenant_id) on delete cascade,
    document_type text not null,
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
    derived jsonb,
    expires_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

alter table public.documents add column if not exists template_id text;
alter table public.documents add column if not exists expires_at timestamptz;
alter table public.documents add column if not exists metadata jsonb not null default '{}'::jsonb;

create index if not exists idx_documents_tenant_created on public.documents (tenant_id, created_at desc);
create index if not exists idx_documents_hash on public.documents (tenant_id, dedup_hash);
create index if not exists idx_documents_state on public.documents (tenant_id, state);
create index if not exists idx_documents_expires on public.documents (tenant_id, expires_at);

create table if not exists public.document_events (
    id uuid primary key,
    document_id uuid not null references public.documents(id) on delete cascade,
    tenant_id text not null references public.tenants(tenant_id) on delete cascade,
    officer_id text,
    event_type text not null,
    payload jsonb not null,
    created_at timestamptz not null default now()
);

alter table public.document_events add column if not exists officer_id text;

create index if not exists idx_events_doc_created on public.document_events (document_id, created_at asc);
create index if not exists idx_events_tenant_created on public.document_events (tenant_id, created_at asc);

create table if not exists public.disputes (
    id uuid primary key,
    document_id uuid not null references public.documents(id) on delete cascade,
    tenant_id text not null references public.tenants(tenant_id) on delete cascade,
    reason text not null,
    evidence_note text,
    status text not null default 'OPEN',
    created_at timestamptz not null default now()
);

create index if not exists idx_disputes_tenant_created on public.disputes (tenant_id, created_at desc);

-- Seed for local/bootstrap convenience.
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
    residency_region
)
values ('dept-education', 365, 120, 25000, false, true, 'default')
on conflict (tenant_id) do nothing;

insert into public.tenant_storage_buckets (tenant_id, bucket_name, encryption_key_ref)
values ('dept-education', 'tenant-dept-education', null)
on conflict (bucket_name) do nothing;

insert into public.officers (officer_id, tenant_id, role, status)
values ('officer-001', 'dept-education', 'case_worker', 'ACTIVE')
on conflict (officer_id) do nothing;
