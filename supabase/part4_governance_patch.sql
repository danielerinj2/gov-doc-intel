-- Apply this if your DB existed before Part-4 governance layer.
-- Safe to run multiple times.

alter table if exists public.officers drop constraint if exists officers_role_check;
alter table if exists public.officers add constraint officers_role_check check (
    role in (
        'case_worker',
        'reviewer',
        'admin',
        'auditor',
        'tenant_operator',
        'tenant_officer',
        'tenant_senior_officer',
        'tenant_admin',
        'tenant_auditor'
    )
);

create table if not exists public.tenant_data_policies (
    tenant_id text primary key references public.tenants(tenant_id) on delete cascade,
    raw_image_retention_years integer not null default 7 check (raw_image_retention_years > 0),
    structured_data_retention_years integer not null default 10 check (structured_data_retention_years > 0),
    fraud_logs_retention_years integer not null default 10 check (fraud_logs_retention_years > 0),
    archival_strategy text not null default 'COLD_STORAGE_AFTER_RETENTION_WINDOW',
    purge_policy text not null default 'ANONYMIZE_AFTER_EXPIRY',
    training_data_policy text not null default 'PSEUDONYMIZED_AND_GATED',
    legal_basis text not null default 'SERVICE_DELIVERY_FRAUD_PREVENTION_AUDIT',
    updated_at timestamptz not null default now()
);

create table if not exists public.tenant_partition_configs (
    tenant_id text primary key references public.tenants(tenant_id) on delete cascade,
    partition_mode text not null default 'LOGICAL_SHARED' check (partition_mode in ('LOGICAL_SHARED', 'DEDICATED_SCHEMA', 'DEDICATED_CLUSTER', 'DEDICATED_DEPLOYMENT')),
    residency_region text not null default 'default',
    region_cluster text not null default 'region-a',
    physical_isolation_required boolean not null default false,
    sensitivity_tier text not null default 'STANDARD',
    updated_at timestamptz not null default now()
);

create table if not exists public.platform_access_grants (
    id uuid primary key default gen_random_uuid(),
    actor_id text not null,
    platform_role text not null check (platform_role in ('platform_super_admin', 'platform_auditor')),
    justification text not null,
    approved_by text not null,
    status text not null default 'ACTIVE' check (status in ('ACTIVE', 'REVOKED', 'EXPIRED')),
    granted_at timestamptz not null default now(),
    expires_at timestamptz,
    unique (actor_id, platform_role)
);

create table if not exists public.operational_runbooks (
    id uuid primary key default gen_random_uuid(),
    tenant_id text not null references public.tenants(tenant_id) on delete cascade,
    event_type text not null,
    severity text not null check (severity in ('SEV1', 'SEV2', 'SEV3')),
    title text not null,
    steps jsonb not null default '[]'::jsonb,
    owner_role text not null,
    is_active boolean not null default true,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists public.governance_audit_reviews (
    id uuid primary key default gen_random_uuid(),
    tenant_id text not null references public.tenants(tenant_id) on delete cascade,
    review_type text not null,
    status text not null,
    findings jsonb not null default '[]'::jsonb,
    reviewed_by text not null,
    created_at timestamptz not null default now()
);

create index if not exists idx_platform_access_grants_actor on public.platform_access_grants (actor_id, status);
create index if not exists idx_operational_runbooks_tenant_event on public.operational_runbooks (tenant_id, event_type, is_active);
create index if not exists idx_governance_audit_reviews_tenant_created on public.governance_audit_reviews (tenant_id, created_at desc);
