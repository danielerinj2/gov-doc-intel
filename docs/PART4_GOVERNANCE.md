# Part 4 - Governance, Ops and Tenancy Layer

## Tenancy and Access Isolation
- Tenant = one department/scheme authority.
- All officer actions are scoped by `tenant_id`.
- Cross-tenant access requires explicit `platform_access_grants` entry.
- Data-path isolation is enforced in app authorization and DB RLS.

## Role Model
Tenant-scoped roles:
- `verifier`
- `senior_verifier`
- `auditor`

Platform-scoped role:
- `platform_admin`

Legacy aliases are no longer accepted.

## Department Partitioning Model
Configured via `tenant_partition_configs`:
- `partition_mode`: `LOGICAL_SHARED`, `DEDICATED_SCHEMA`, `DEDICATED_CLUSTER`, `DEDICATED_DEPLOYMENT`
- `residency_region`, `region_cluster`
- `physical_isolation_required`, `sensitivity_tier`

## Data Governance and Retention
Configured via `tenant_data_policies`:
- Raw image retention
- Structured data retention
- Fraud logs retention
- Archival strategy
- Purge policy
- Training-data governance and legal basis

## Compliance Position
Policy statement used in UI/API governance snapshot:

> The platform assists officers by automating document analysis and risk flagging. Final legal decisions remain with human officers acting under applicable laws and policies.

## Audit and Oversight
- AI decision audit logs: `model_audit_logs`
- Governance review records: `governance_audit_reviews`
- Platform oversight grants and cross-tenant coverage reports: `platform_access_grants`

## Operational Runbooks
Runbook artifacts are stored in `operational_runbooks` with:
- Event type
- Severity
- Step list
- Owner role

These are used for:
- Pipeline failure handling
- DR/failover steps
- Security incident response
- Model rollback and fraud-spike triage
