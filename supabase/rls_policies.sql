-- Tenant-safe RLS for publishable-key usage.
-- IMPORTANT:
-- 1) Run supabase/schema.sql first.
-- 2) Publishable key requires signed-in user JWT (role=authenticated).
-- 3) This policy model enforces one ACTIVE tenant membership per user.

begin;

create table if not exists public.tenant_memberships (
    user_id uuid not null references auth.users(id) on delete cascade,
    tenant_id text not null references public.tenants(tenant_id) on delete cascade,
    role text not null check (role in ('case_worker', 'reviewer', 'admin', 'auditor')),
    status text not null default 'ACTIVE' check (status in ('ACTIVE', 'SUSPENDED')),
    created_at timestamptz not null default now(),
    primary key (user_id, tenant_id)
);

create index if not exists idx_tenant_memberships_tenant on public.tenant_memberships (tenant_id);
create unique index if not exists uq_tenant_memberships_one_active_per_user
    on public.tenant_memberships (user_id)
    where status = 'ACTIVE';

alter table public.tenant_memberships enable row level security;

-- Membership visibility: own memberships only.
drop policy if exists tm_select_own on public.tenant_memberships;
create policy tm_select_own
on public.tenant_memberships
for select
to authenticated
using (user_id = auth.uid());

revoke all on table public.tenant_memberships from anon;
revoke all on table public.tenant_memberships from authenticated;
grant select on table public.tenant_memberships to authenticated;

-- Helper functions
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

create or replace function public.member_can_access_bucket(p_bucket_name text)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
    select exists (
        select 1
        from public.tenant_storage_buckets b
        join public.tenant_memberships tm on tm.tenant_id = b.tenant_id
        where tm.user_id = auth.uid()
          and tm.status = 'ACTIVE'
          and b.is_active = true
          and b.bucket_name = p_bucket_name
    );
$$;

revoke all on function public.is_tenant_member(text) from public;
revoke all on function public.has_tenant_role(text, text[]) from public;
revoke all on function public.member_can_access_bucket(text) from public;
grant execute on function public.is_tenant_member(text) to authenticated;
grant execute on function public.has_tenant_role(text, text[]) to authenticated;
grant execute on function public.member_can_access_bucket(text) to authenticated;

-- Enable RLS on all tenancy-governed tables
alter table public.tenants enable row level security;
alter table public.officers enable row level security;
alter table public.tenant_policies enable row level security;
alter table public.tenant_templates enable row level security;
alter table public.tenant_rules enable row level security;
alter table public.tenant_storage_buckets enable row level security;
alter table public.tenant_api_keys enable row level security;
alter table public.documents enable row level security;
alter table public.document_events enable row level security;
alter table public.disputes enable row level security;
alter table public.citizen_notifications enable row level security;
alter table public.review_escalations enable row level security;
alter table public.document_records enable row level security;
alter table public.model_audit_logs enable row level security;
alter table public.module_metrics enable row level security;
alter table public.human_review_assignments enable row level security;
alter table public.webhook_outbox enable row level security;
alter table public.correction_events enable row level security;
alter table public.correction_validation_gate enable row level security;

-- tenants
 drop policy if exists tenants_select_tenant on public.tenants;
create policy tenants_select_tenant
on public.tenants
for select
to authenticated
using (public.is_tenant_member(tenant_id));

 drop policy if exists tenants_update_admin on public.tenants;
create policy tenants_update_admin
on public.tenants
for update
to authenticated
using (public.has_tenant_role(tenant_id, array['admin']))
with check (public.has_tenant_role(tenant_id, array['admin']));

-- officers
 drop policy if exists officers_select_tenant on public.officers;
create policy officers_select_tenant
on public.officers
for select
to authenticated
using (public.is_tenant_member(tenant_id));

 drop policy if exists officers_write_admin on public.officers;
create policy officers_write_admin
on public.officers
for all
to authenticated
using (public.has_tenant_role(tenant_id, array['admin']))
with check (public.has_tenant_role(tenant_id, array['admin']));

-- tenant policies/templates/rules/storage/api keys
 drop policy if exists tenant_policies_select_tenant on public.tenant_policies;
create policy tenant_policies_select_tenant
on public.tenant_policies
for select
to authenticated
using (public.is_tenant_member(tenant_id));

 drop policy if exists tenant_policies_write_admin on public.tenant_policies;
create policy tenant_policies_write_admin
on public.tenant_policies
for all
to authenticated
using (public.has_tenant_role(tenant_id, array['admin']))
with check (public.has_tenant_role(tenant_id, array['admin']));

 drop policy if exists tenant_templates_select_tenant on public.tenant_templates;
create policy tenant_templates_select_tenant
on public.tenant_templates
for select
to authenticated
using (public.is_tenant_member(tenant_id));

 drop policy if exists tenant_templates_write_admin on public.tenant_templates;
create policy tenant_templates_write_admin
on public.tenant_templates
for all
to authenticated
using (public.has_tenant_role(tenant_id, array['admin']))
with check (public.has_tenant_role(tenant_id, array['admin']));

 drop policy if exists tenant_rules_select_tenant on public.tenant_rules;
create policy tenant_rules_select_tenant
on public.tenant_rules
for select
to authenticated
using (public.is_tenant_member(tenant_id));

 drop policy if exists tenant_rules_write_admin on public.tenant_rules;
create policy tenant_rules_write_admin
on public.tenant_rules
for all
to authenticated
using (public.has_tenant_role(tenant_id, array['admin']))
with check (public.has_tenant_role(tenant_id, array['admin']));

 drop policy if exists tenant_storage_buckets_select_tenant on public.tenant_storage_buckets;
create policy tenant_storage_buckets_select_tenant
on public.tenant_storage_buckets
for select
to authenticated
using (public.is_tenant_member(tenant_id));

 drop policy if exists tenant_storage_buckets_write_admin on public.tenant_storage_buckets;
create policy tenant_storage_buckets_write_admin
on public.tenant_storage_buckets
for all
to authenticated
using (public.has_tenant_role(tenant_id, array['admin']))
with check (public.has_tenant_role(tenant_id, array['admin']));

 drop policy if exists tenant_api_keys_select_admin on public.tenant_api_keys;
create policy tenant_api_keys_select_admin
on public.tenant_api_keys
for select
to authenticated
using (public.has_tenant_role(tenant_id, array['admin']));

 drop policy if exists tenant_api_keys_write_admin on public.tenant_api_keys;
create policy tenant_api_keys_write_admin
on public.tenant_api_keys
for all
to authenticated
using (public.has_tenant_role(tenant_id, array['admin']))
with check (public.has_tenant_role(tenant_id, array['admin']));

-- documents/events/disputes
 drop policy if exists documents_select_tenant on public.documents;
create policy documents_select_tenant
on public.documents
for select
to authenticated
using (public.is_tenant_member(tenant_id));

 drop policy if exists documents_insert_tenant_writer on public.documents;
create policy documents_insert_tenant_writer
on public.documents
for insert
to authenticated
with check (public.has_tenant_role(tenant_id, array['case_worker', 'reviewer', 'admin']));

 drop policy if exists documents_update_tenant_writer on public.documents;
create policy documents_update_tenant_writer
on public.documents
for update
to authenticated
using (public.has_tenant_role(tenant_id, array['case_worker', 'reviewer', 'admin']))
with check (public.has_tenant_role(tenant_id, array['case_worker', 'reviewer', 'admin']));

 drop policy if exists documents_delete_tenant_admin on public.documents;
create policy documents_delete_tenant_admin
on public.documents
for delete
to authenticated
using (public.has_tenant_role(tenant_id, array['admin']));

 drop policy if exists document_events_select_tenant on public.document_events;
create policy document_events_select_tenant
on public.document_events
for select
to authenticated
using (
    public.is_tenant_member(tenant_id)
    and exists (
        select 1 from public.documents d
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
        select 1 from public.documents d
        where d.id = document_events.document_id
          and d.tenant_id = document_events.tenant_id
    )
);

 drop policy if exists disputes_select_tenant on public.disputes;
create policy disputes_select_tenant
on public.disputes
for select
to authenticated
using (
    public.is_tenant_member(tenant_id)
    and exists (
        select 1 from public.documents d
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
        select 1 from public.documents d
        where d.id = disputes.document_id
          and d.tenant_id = disputes.tenant_id
    )
);

 drop policy if exists disputes_update_tenant_reviewer on public.disputes;
create policy disputes_update_tenant_reviewer
on public.disputes
for update
to authenticated
using (public.has_tenant_role(tenant_id, array['reviewer', 'admin']))
with check (public.has_tenant_role(tenant_id, array['reviewer', 'admin']));

 drop policy if exists disputes_delete_tenant_admin on public.disputes;
create policy disputes_delete_tenant_admin
on public.disputes
for delete
to authenticated
using (public.has_tenant_role(tenant_id, array['admin']));

-- notifications / escalations / document record
 drop policy if exists citizen_notifications_select_tenant on public.citizen_notifications;
create policy citizen_notifications_select_tenant
on public.citizen_notifications
for select
to authenticated
using (public.is_tenant_member(tenant_id));

 drop policy if exists citizen_notifications_insert_writer on public.citizen_notifications;
create policy citizen_notifications_insert_writer
on public.citizen_notifications
for insert
to authenticated
with check (public.has_tenant_role(tenant_id, array['case_worker', 'reviewer', 'admin']));

 drop policy if exists review_escalations_select_tenant on public.review_escalations;
create policy review_escalations_select_tenant
on public.review_escalations
for select
to authenticated
using (public.is_tenant_member(tenant_id));

 drop policy if exists review_escalations_write_reviewer on public.review_escalations;
create policy review_escalations_write_reviewer
on public.review_escalations
for all
to authenticated
using (public.has_tenant_role(tenant_id, array['reviewer', 'admin']))
with check (public.has_tenant_role(tenant_id, array['reviewer', 'admin']));

 drop policy if exists document_records_select_tenant on public.document_records;
create policy document_records_select_tenant
on public.document_records
for select
to authenticated
using (public.is_tenant_member(tenant_id));

 drop policy if exists document_records_write_writer on public.document_records;
create policy document_records_write_writer
on public.document_records
for all
to authenticated
using (public.has_tenant_role(tenant_id, array['case_worker', 'reviewer', 'admin']))
with check (public.has_tenant_role(tenant_id, array['case_worker', 'reviewer', 'admin']));

 drop policy if exists model_audit_logs_select_tenant on public.model_audit_logs;
create policy model_audit_logs_select_tenant
on public.model_audit_logs
for select
to authenticated
using (public.is_tenant_member(tenant_id));

 drop policy if exists model_audit_logs_insert_writer on public.model_audit_logs;
create policy model_audit_logs_insert_writer
on public.model_audit_logs
for insert
to authenticated
with check (public.has_tenant_role(tenant_id, array['case_worker', 'reviewer', 'admin']));

 drop policy if exists module_metrics_select_tenant on public.module_metrics;
create policy module_metrics_select_tenant
on public.module_metrics
for select
to authenticated
using (public.is_tenant_member(tenant_id));

 drop policy if exists module_metrics_insert_writer on public.module_metrics;
create policy module_metrics_insert_writer
on public.module_metrics
for insert
to authenticated
with check (public.has_tenant_role(tenant_id, array['case_worker', 'reviewer', 'admin']));

 drop policy if exists human_review_assignments_select_tenant on public.human_review_assignments;
create policy human_review_assignments_select_tenant
on public.human_review_assignments
for select
to authenticated
using (public.is_tenant_member(tenant_id));

 drop policy if exists human_review_assignments_write_reviewer on public.human_review_assignments;
create policy human_review_assignments_write_reviewer
on public.human_review_assignments
for all
to authenticated
using (public.has_tenant_role(tenant_id, array['reviewer', 'admin']))
with check (public.has_tenant_role(tenant_id, array['reviewer', 'admin']));

 drop policy if exists webhook_outbox_select_tenant on public.webhook_outbox;
create policy webhook_outbox_select_tenant
on public.webhook_outbox
for select
to authenticated
using (public.is_tenant_member(tenant_id));

 drop policy if exists webhook_outbox_write_writer on public.webhook_outbox;
create policy webhook_outbox_write_writer
on public.webhook_outbox
for all
to authenticated
using (public.has_tenant_role(tenant_id, array['case_worker', 'reviewer', 'admin']))
with check (public.has_tenant_role(tenant_id, array['case_worker', 'reviewer', 'admin']));

 drop policy if exists correction_events_select_tenant on public.correction_events;
create policy correction_events_select_tenant
on public.correction_events
for select
to authenticated
using (public.is_tenant_member(tenant_id));

 drop policy if exists correction_events_insert_reviewer on public.correction_events;
create policy correction_events_insert_reviewer
on public.correction_events
for insert
to authenticated
with check (public.has_tenant_role(tenant_id, array['reviewer', 'admin']));

 drop policy if exists correction_validation_gate_select_tenant on public.correction_validation_gate;
create policy correction_validation_gate_select_tenant
on public.correction_validation_gate
for select
to authenticated
using (public.is_tenant_member(tenant_id));

 drop policy if exists correction_validation_gate_write_reviewer on public.correction_validation_gate;
create policy correction_validation_gate_write_reviewer
on public.correction_validation_gate
for all
to authenticated
using (public.has_tenant_role(tenant_id, array['reviewer', 'admin']))
with check (public.has_tenant_role(tenant_id, array['reviewer', 'admin']));

-- Grants
revoke all on table public.tenants from anon;
revoke all on table public.officers from anon;
revoke all on table public.tenant_policies from anon;
revoke all on table public.tenant_templates from anon;
revoke all on table public.tenant_rules from anon;
revoke all on table public.tenant_storage_buckets from anon;
revoke all on table public.tenant_api_keys from anon;
revoke all on table public.documents from anon;
revoke all on table public.document_events from anon;
revoke all on table public.disputes from anon;
revoke all on table public.citizen_notifications from anon;
revoke all on table public.review_escalations from anon;
revoke all on table public.document_records from anon;
revoke all on table public.model_audit_logs from anon;
revoke all on table public.module_metrics from anon;
revoke all on table public.human_review_assignments from anon;
revoke all on table public.webhook_outbox from anon;
revoke all on table public.correction_events from anon;
revoke all on table public.correction_validation_gate from anon;

revoke all on table public.tenants from authenticated;
revoke all on table public.officers from authenticated;
revoke all on table public.tenant_policies from authenticated;
revoke all on table public.tenant_templates from authenticated;
revoke all on table public.tenant_rules from authenticated;
revoke all on table public.tenant_storage_buckets from authenticated;
revoke all on table public.tenant_api_keys from authenticated;
revoke all on table public.documents from authenticated;
revoke all on table public.document_events from authenticated;
revoke all on table public.disputes from authenticated;
revoke all on table public.citizen_notifications from authenticated;
revoke all on table public.review_escalations from authenticated;
revoke all on table public.document_records from authenticated;
revoke all on table public.model_audit_logs from authenticated;
revoke all on table public.module_metrics from authenticated;
revoke all on table public.human_review_assignments from authenticated;
revoke all on table public.webhook_outbox from authenticated;
revoke all on table public.correction_events from authenticated;
revoke all on table public.correction_validation_gate from authenticated;

grant select on table public.tenants to authenticated;
grant select, insert, update, delete on table public.officers to authenticated;
grant select, insert, update, delete on table public.tenant_policies to authenticated;
grant select, insert, update, delete on table public.tenant_templates to authenticated;
grant select, insert, update, delete on table public.tenant_rules to authenticated;
grant select, insert, update, delete on table public.tenant_storage_buckets to authenticated;
grant select, insert, update, delete on table public.tenant_api_keys to authenticated;
grant select, insert, update, delete on table public.documents to authenticated;
grant select, insert on table public.document_events to authenticated;
grant select, insert, update, delete on table public.disputes to authenticated;
grant select, insert on table public.citizen_notifications to authenticated;
grant select, insert, update, delete on table public.review_escalations to authenticated;
grant select, insert, update, delete on table public.document_records to authenticated;
grant select, insert on table public.model_audit_logs to authenticated;
grant select, insert on table public.module_metrics to authenticated;
grant select, insert, update, delete on table public.human_review_assignments to authenticated;
grant select, insert, update, delete on table public.webhook_outbox to authenticated;
grant select, insert on table public.correction_events to authenticated;
grant select, insert, update, delete on table public.correction_validation_gate to authenticated;

-- Storage bucket isolation
 do $$
begin
    begin
        execute 'drop policy if exists storage_objects_tenant_select on storage.objects';
        execute 'drop policy if exists storage_objects_tenant_insert on storage.objects';
        execute 'drop policy if exists storage_objects_tenant_update on storage.objects';
        execute 'drop policy if exists storage_objects_tenant_delete on storage.objects';

        execute '
            create policy storage_objects_tenant_select
            on storage.objects
            for select
            to authenticated
            using (public.member_can_access_bucket(bucket_id))
        ';

        execute '
            create policy storage_objects_tenant_insert
            on storage.objects
            for insert
            to authenticated
            with check (public.member_can_access_bucket(bucket_id))
        ';

        execute '
            create policy storage_objects_tenant_update
            on storage.objects
            for update
            to authenticated
            using (public.member_can_access_bucket(bucket_id))
            with check (public.member_can_access_bucket(bucket_id))
        ';

        execute '
            create policy storage_objects_tenant_delete
            on storage.objects
            for delete
            to authenticated
            using (public.member_can_access_bucket(bucket_id))
        ';
    exception
        when undefined_table then
            null;
    end;
end
$$;

commit;
