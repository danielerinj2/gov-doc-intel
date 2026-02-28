-- Apply this if your DB existed before Part-5 KPI/rollout/risk layer.
-- Safe to run multiple times.

create table if not exists public.tenant_kpi_targets (
    tenant_id text not null references public.tenants(tenant_id) on delete cascade,
    kpi_key text not null,
    target_value double precision not null,
    unit text not null,
    direction text not null check (direction in ('GTE', 'LTE')),
    description text not null,
    updated_at timestamptz not null default now(),
    primary key (tenant_id, kpi_key)
);

create table if not exists public.tenant_kpi_snapshots (
    id uuid primary key default gen_random_uuid(),
    tenant_id text not null references public.tenants(tenant_id) on delete cascade,
    kpi_key text not null,
    measured_value double precision not null,
    source text not null,
    notes text,
    measured_at timestamptz not null default now(),
    created_at timestamptz not null default now()
);

create table if not exists public.tenant_rollout_phases (
    tenant_id text not null references public.tenants(tenant_id) on delete cascade,
    phase_key text not null,
    title text not null,
    duration_months_min integer not null check (duration_months_min >= 0),
    duration_months_max integer not null check (duration_months_max >= duration_months_min),
    status text not null,
    description text not null,
    start_date date,
    end_date date,
    updated_at timestamptz not null default now(),
    primary key (tenant_id, phase_key)
);

create table if not exists public.tenant_risk_register (
    tenant_id text not null references public.tenants(tenant_id) on delete cascade,
    risk_code text not null,
    title text not null,
    mitigation text not null,
    owner_team text not null,
    impact text not null,
    likelihood text not null,
    status text not null,
    updated_at timestamptz not null default now(),
    primary key (tenant_id, risk_code)
);

create index if not exists idx_tenant_kpi_targets_tenant on public.tenant_kpi_targets (tenant_id);
create index if not exists idx_tenant_kpi_snapshots_tenant_measured on public.tenant_kpi_snapshots (tenant_id, measured_at desc);
create index if not exists idx_tenant_kpi_snapshots_tenant_key on public.tenant_kpi_snapshots (tenant_id, kpi_key, measured_at desc);
create index if not exists idx_tenant_rollout_phases_tenant on public.tenant_rollout_phases (tenant_id, phase_key);
create index if not exists idx_tenant_risk_register_tenant on public.tenant_risk_register (tenant_id, risk_code);
