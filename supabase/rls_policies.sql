-- Tenant-safe RLS for publishable-key usage.
-- IMPORTANT:
-- 1) Run supabase/schema.sql first.
-- 2) Publishable key alone is not enough; requests must include a signed-in user JWT (role=authenticated).
-- 3) Manage tenant memberships with service role / SQL editor.

begin;

-- 1) Membership model used by RLS predicates
create table if not exists public.tenant_memberships (
    user_id uuid not null references auth.users(id) on delete cascade,
    tenant_id text not null,
    role text not null check (role in ('case_worker', 'reviewer', 'admin', 'auditor')),
    status text not null default 'ACTIVE' check (status in ('ACTIVE', 'SUSPENDED')),
    created_at timestamptz not null default now(),
    primary key (user_id, tenant_id)
);

create index if not exists idx_tenant_memberships_tenant on public.tenant_memberships (tenant_id);

alter table public.tenant_memberships enable row level security;

-- Allow users to see their own memberships only.
drop policy if exists tm_select_own on public.tenant_memberships;
create policy tm_select_own
on public.tenant_memberships
for select
to authenticated
using (user_id = auth.uid());

-- No direct client writes to memberships.
revoke all on table public.tenant_memberships from anon;
revoke all on table public.tenant_memberships from authenticated;
grant select on table public.tenant_memberships to authenticated;

-- 2) Helper functions for policy reuse
create or replace function public.is_tenant_member(p_tenant_id text)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
    select exists (
        select 1
        from public.tenant_memberships tm
        where tm.user_id = auth.uid()
          and tm.tenant_id = p_tenant_id
          and tm.status = 'ACTIVE'
    );
$$;

create or replace function public.has_tenant_role(p_tenant_id text, p_roles text[])
returns boolean
language sql
stable
security definer
set search_path = public
as $$
    select exists (
        select 1
        from public.tenant_memberships tm
        where tm.user_id = auth.uid()
          and tm.tenant_id = p_tenant_id
          and tm.status = 'ACTIVE'
          and tm.role = any (p_roles)
    );
$$;

revoke all on function public.is_tenant_member(text) from public;
revoke all on function public.has_tenant_role(text, text[]) from public;
grant execute on function public.is_tenant_member(text) to authenticated;
grant execute on function public.has_tenant_role(text, text[]) to authenticated;

-- 3) Enable RLS on core tables
alter table public.documents enable row level security;
alter table public.document_events enable row level security;
alter table public.disputes enable row level security;

-- 4) documents policies

drop policy if exists documents_select_tenant on public.documents;
create policy documents_select_tenant
on public.documents
for select
to authenticated
using (
    public.is_tenant_member(tenant_id)
);

drop policy if exists documents_insert_tenant_writer on public.documents;
create policy documents_insert_tenant_writer
on public.documents
for insert
to authenticated
with check (
    public.has_tenant_role(tenant_id, array['case_worker', 'reviewer', 'admin'])
);

drop policy if exists documents_update_tenant_writer on public.documents;
create policy documents_update_tenant_writer
on public.documents
for update
to authenticated
using (
    public.has_tenant_role(tenant_id, array['case_worker', 'reviewer', 'admin'])
)
with check (
    public.has_tenant_role(tenant_id, array['case_worker', 'reviewer', 'admin'])
);

drop policy if exists documents_delete_tenant_admin on public.documents;
create policy documents_delete_tenant_admin
on public.documents
for delete
to authenticated
using (
    public.has_tenant_role(tenant_id, array['admin'])
);

-- 5) document_events policies (append-only for clients)

drop policy if exists document_events_select_tenant on public.document_events;
create policy document_events_select_tenant
on public.document_events
for select
to authenticated
using (
    public.is_tenant_member(tenant_id)
    and exists (
        select 1
        from public.documents d
        where d.id = document_events.document_id
          and d.tenant_id = document_events.tenant_id
    )
);

drop policy if exists document_events_insert_tenant_writer on public.document_events;
create policy document_events_insert_tenant_writer
on public.document_events
for insert
to authenticated
with check (
    public.has_tenant_role(tenant_id, array['case_worker', 'reviewer', 'admin'])
    and exists (
        select 1
        from public.documents d
        where d.id = document_events.document_id
          and d.tenant_id = document_events.tenant_id
    )
);

-- No update/delete policies for document_events => denied by default.

-- 6) disputes policies

drop policy if exists disputes_select_tenant on public.disputes;
create policy disputes_select_tenant
on public.disputes
for select
to authenticated
using (
    public.is_tenant_member(tenant_id)
    and exists (
        select 1
        from public.documents d
        where d.id = disputes.document_id
          and d.tenant_id = disputes.tenant_id
    )
);

drop policy if exists disputes_insert_tenant_writer on public.disputes;
create policy disputes_insert_tenant_writer
on public.disputes
for insert
to authenticated
with check (
    public.has_tenant_role(tenant_id, array['case_worker', 'reviewer', 'admin'])
    and exists (
        select 1
        from public.documents d
        where d.id = disputes.document_id
          and d.tenant_id = disputes.tenant_id
    )
);

drop policy if exists disputes_update_tenant_reviewer on public.disputes;
create policy disputes_update_tenant_reviewer
on public.disputes
for update
to authenticated
using (
    public.has_tenant_role(tenant_id, array['reviewer', 'admin'])
)
with check (
    public.has_tenant_role(tenant_id, array['reviewer', 'admin'])
);

drop policy if exists disputes_delete_tenant_admin on public.disputes;
create policy disputes_delete_tenant_admin
on public.disputes
for delete
to authenticated
using (
    public.has_tenant_role(tenant_id, array['admin'])
);

-- 7) Explicit grants: authenticated only, no anon table access
revoke all on table public.documents from anon;
revoke all on table public.document_events from anon;
revoke all on table public.disputes from anon;

revoke all on table public.documents from authenticated;
revoke all on table public.document_events from authenticated;
revoke all on table public.disputes from authenticated;

grant select, insert, update, delete on table public.documents to authenticated;
grant select, insert on table public.document_events to authenticated;
grant select, insert, update, delete on table public.disputes to authenticated;

commit;
