-- Apply this after schema.sql if your DB was created before Part-3 modules.
-- Safe to run multiple times.

create extension if not exists "pgcrypto";

create table if not exists public.model_audit_logs (
    id uuid primary key default gen_random_uuid(),
    tenant_id text not null references public.tenants(tenant_id) on delete cascade,
    document_id uuid not null references public.documents(id) on delete cascade,
    job_id text not null,
    module_name text not null,
    model_id text not null,
    model_version text not null,
    input_ref jsonb not null default '{}'::jsonb,
    output jsonb not null default '{}'::jsonb,
    reason_codes jsonb not null default '[]'::jsonb,
    actor_type text not null default 'SYSTEM',
    actor_id text,
    created_at timestamptz not null default now()
);

create table if not exists public.module_metrics (
    id uuid primary key default gen_random_uuid(),
    tenant_id text not null references public.tenants(tenant_id) on delete cascade,
    document_id uuid not null references public.documents(id) on delete cascade,
    job_id text not null,
    module_name text not null,
    latency_ms double precision not null check (latency_ms >= 0),
    status text not null default 'OK',
    metric_payload jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);

create table if not exists public.human_review_assignments (
    id uuid primary key default gen_random_uuid(),
    tenant_id text not null references public.tenants(tenant_id) on delete cascade,
    document_id uuid not null references public.documents(id) on delete cascade,
    queue_name text not null,
    assignment_policy text not null default 'LEAST_LOADED',
    priority integer not null default 50,
    status text not null default 'WAITING_FOR_REVIEW',
    assigned_officer_id text,
    claimed_at timestamptz,
    resolved_at timestamptz,
    created_at timestamptz not null default now()
);

create table if not exists public.webhook_outbox (
    id uuid primary key default gen_random_uuid(),
    tenant_id text not null references public.tenants(tenant_id) on delete cascade,
    document_id uuid not null references public.documents(id) on delete cascade,
    event_type text not null,
    payload jsonb not null default '{}'::jsonb,
    status text not null default 'PENDING',
    attempt_count integer not null default 0,
    last_error text,
    created_at timestamptz not null default now(),
    dispatched_at timestamptz
);

create table if not exists public.correction_events (
    id uuid primary key default gen_random_uuid(),
    tenant_id text not null references public.tenants(tenant_id) on delete cascade,
    document_id uuid not null references public.documents(id) on delete cascade,
    field_name text not null,
    old_value text,
    new_value text,
    officer_id text not null,
    reason text not null,
    created_at timestamptz not null default now()
);

create table if not exists public.correction_validation_gate (
    id uuid primary key default gen_random_uuid(),
    tenant_id text not null references public.tenants(tenant_id) on delete cascade,
    document_id uuid not null references public.documents(id) on delete cascade,
    correction_event_id uuid not null references public.correction_events(id) on delete cascade,
    status text not null default 'PENDING_QA',
    qa_required boolean not null default true,
    notes jsonb not null default '[]'::jsonb,
    created_at timestamptz not null default now(),
    validated_at timestamptz
);

create index if not exists idx_model_audit_logs_tenant_doc on public.model_audit_logs (tenant_id, document_id, created_at desc);
create index if not exists idx_model_audit_logs_job on public.model_audit_logs (job_id, module_name);

create index if not exists idx_module_metrics_tenant_created on public.module_metrics (tenant_id, created_at desc);
create index if not exists idx_module_metrics_doc_job on public.module_metrics (document_id, job_id);

create index if not exists idx_human_review_assignments_tenant_status on public.human_review_assignments (tenant_id, status, priority desc, created_at asc);
create index if not exists idx_human_review_assignments_doc on public.human_review_assignments (document_id, created_at desc);

create index if not exists idx_webhook_outbox_tenant_status_created on public.webhook_outbox (tenant_id, status, created_at asc);

create index if not exists idx_correction_events_tenant_doc on public.correction_events (tenant_id, document_id, created_at desc);
create index if not exists idx_correction_events_field on public.correction_events (tenant_id, field_name, created_at desc);

create index if not exists idx_correction_validation_gate_tenant_status on public.correction_validation_gate (tenant_id, status, created_at desc);
