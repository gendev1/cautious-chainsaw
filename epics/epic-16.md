# Epic 16: Audit, Compliance, and Support Tooling

## Goal

Create append-only auditability, privileged action tracking, internal support workflows, and compliance export capabilities. Audit events must be immutable and queryable by tenant, actor, resource, workflow, and date range. Support tooling must operate under strict permission controls with full audit trails.

## Background

The platform handles regulated financial operations including money movement, order execution, billing posting, and sensitive document access. Every privileged or customer-impacting action must leave an append-only audit trail. Support staff require investigation and intervention tools that are themselves fully audited. Regulatory retention requirements (minimum 7 years) must be enforceable per tenant.

Per the architecture spec, this is a hard requirement for the chassis -- not optional polish. The audit domain is listed as a platform-owned bounded context alongside reconciliation and compliance controls.

## Dependencies

- Epic 1: Tenant, Identity, and Access Control (tenant resolution, roles, permissions, impersonation model)
- Epic 3: Workflow and Case Management (workflow states, case IDs, operational tasks)
- Epic 5: Document Vault and Records Management (document metadata, vault access rules, sensitive artifact classification)
- Epic 7: Money Movement and Transfer Operations (transfer intents, lifecycle states, retry mechanisms)
- Epic 9: Orders, OMS/EMS Integration, and Trade Status (order intents, submission, cancellation flows)
- Epic 12: Billing and Fee Operations (billing runs, posting, reversal flows)

---

## Issue 16-1: Audit Event Data Model in Postgres

### Title

Define append-only audit event table and domain types

### Description

Create the foundational Postgres schema for audit events. The table must be append-only (no UPDATE or DELETE operations permitted at the application layer). Each event captures the full context of a platform action: who did what, to which resource, within which workflow, and when.

### Scope

- Create `audit_events` table with columns:
  - `id` (UUID, primary key)
  - `tenant_id` (UUID, NOT NULL, foreign key to firms)
  - `actor_id` (UUID, NOT NULL -- user or service principal)
  - `actor_type` (enum: `user`, `service`, `system`, `support_impersonator`)
  - `action` (text, NOT NULL -- e.g., `order.submitted`, `role.changed`, `document.accessed`)
  - `resource` (text, NOT NULL -- e.g., `order_intent`, `transfer_case`, `user_role_assignment`)
  - `resource_id` (UUID, NOT NULL)
  - `workflow` (text, nullable -- e.g., `onboarding`, `transfer`, `billing`)
  - `workflow_id` (UUID, nullable -- case or workflow run ID)
  - `timestamp` (timestamptz, NOT NULL, default NOW())
  - `metadata` (JSONB, nullable -- structured payload specific to the action)
  - `request_id` (UUID, nullable -- correlation to originating HTTP request)
  - `correlation_id` (UUID, nullable -- cross-service workflow correlation)
  - `ip_address` (inet, nullable)
  - `user_agent` (text, nullable)
- Add composite indexes for query patterns: `(tenant_id, timestamp)`, `(tenant_id, actor_id, timestamp)`, `(tenant_id, resource, resource_id)`, `(tenant_id, workflow, workflow_id)`
- Define TypeScript domain types: `AuditEvent`, `AuditAction`, `AuditActorType`, `AuditResource`
- Define Zod schemas for audit event creation
- Create repository module at `src/audit/repository.ts` with insert-only methods (no update, no delete)
- Add database migration

### Acceptance Criteria

- [ ] `audit_events` table exists with all specified columns and indexes
- [ ] No UPDATE or DELETE methods are exposed in the audit repository
- [ ] TypeScript types and Zod schemas cover all fields
- [ ] Insertion of an audit event succeeds and returns the created record
- [ ] Composite indexes support efficient queries by tenant+time, tenant+actor, tenant+resource, and tenant+workflow
- [ ] Migration is reversible for development but the drop direction is clearly documented as destructive
- [ ] Table uses `timestamptz` for all time fields

### Dependencies

- Epic 1 (tenant and user tables must exist for foreign key references)

---

## Issue 16-2: Audit Event Emission Middleware

### Title

Implement Hono middleware for automatic audit event capture on write operations

### Description

Create a Hono after-middleware that automatically emits audit events for mutating HTTP operations. The middleware fires after the route handler completes successfully, capturing the action context from the request pipeline (tenant, actor, resource, action) and persisting it to the audit event store. This ensures consistent audit coverage without requiring each handler to manually emit events.

### Scope

- Create audit emission middleware at `src/http/middleware/audit-emission.ts`
- Middleware reads from Hono context variables set by upstream middleware and route handlers:
  - `tenantId`, `actorId`, `actorType` (set by auth middleware)
  - `auditAction`, `auditResource`, `auditResourceId`, `auditWorkflow`, `auditWorkflowId`, `auditMetadata` (set by route handler or route-level decorator config)
- Middleware fires only on successful responses (2xx status codes) for mutating methods (POST, PUT, PATCH, DELETE)
- Audit event insertion must not block the HTTP response -- use fire-and-forget with error logging
- Provide a route-level helper `withAudit(config)` that pre-configures audit context for a route group
- Support opt-out for routes that handle their own audit emission (e.g., bulk operations)
- Include `request_id`, `ip_address`, and `user_agent` from request headers

### Acceptance Criteria

- [ ] Mutating requests that complete with 2xx automatically produce an audit event
- [ ] Non-mutating requests (GET, HEAD, OPTIONS) do not emit audit events
- [ ] Failed requests (4xx, 5xx) do not emit audit events
- [ ] Audit emission failure does not cause the HTTP response to fail
- [ ] Audit emission errors are logged at error level with request context
- [ ] Routes can opt out of automatic audit emission
- [ ] `withAudit` helper correctly pre-populates audit context for route groups
- [ ] Request ID, IP address, and user agent are captured in the audit event

### Dependencies

- Issue 16-1 (audit event model and repository)
- Epic 1 (auth middleware providing tenant and actor context)

---

## Issue 16-3: Privileged Action Auditing

### Title

Emit explicit audit events for privileged actions: role changes, billing posting, order submission/cancellation, and support impersonation

### Description

Certain actions carry elevated regulatory or operational significance and must always produce detailed audit records regardless of the automatic middleware. These privileged actions must emit audit events with enriched metadata specific to the action type. This issue covers adding explicit audit emission calls within the service layer for each privileged action category.

### Scope

- **Role changes** (`user.role_assigned`, `user.role_revoked`): metadata includes old roles, new roles, target user ID, assigning actor
- **Billing posting** (`billing.run_posted`, `invoice.reversed`): metadata includes billing run ID, total amount, number of invoices, affected accounts
- **Order submission** (`order.submitted`, `order.cancelled`): metadata includes order intent ID, instrument, side, quantity, account ID, submission method (manual vs rebalance release)
- **Support impersonation** (`support.impersonation_started`, `support.impersonation_ended`): metadata includes impersonated user ID, impersonated tenant ID, reason, approval reference
- **Transfer submission/cancellation** (`transfer.submitted`, `transfer.cancelled`): metadata includes transfer type, amount, source, destination, rail type
- **Approval actions** (`approval.granted`, `approval.denied`): metadata includes approval request ID, action being approved, requester
- Each privileged action audit call is made in the corresponding service layer (not in the route handler)
- Privileged audit events are emitted synchronously within the same database transaction where possible, to guarantee the audit record exists if the action succeeds

### Acceptance Criteria

- [ ] Role assignment and revocation produce audit events with before/after role state
- [ ] Billing posting and invoice reversal produce audit events with financial summary metadata
- [ ] Order submission and cancellation produce audit events with order details
- [ ] Support impersonation start and end produce paired audit events
- [ ] Transfer submission and cancellation produce audit events with transfer details
- [ ] Approval grant and denial produce audit events with approval context
- [ ] Privileged audit events are written within the same transaction as the action when feasible
- [ ] All privileged events include the full actor chain (e.g., if impersonating, both the support user and impersonated user are recorded)

### Dependencies

- Issue 16-1 (audit event model)
- Issue 16-2 (audit middleware for non-privileged actions)
- Epic 1 (role management service)
- Epic 7 (transfer service)
- Epic 9 (order service)
- Epic 12 (billing service)

---

## Issue 16-4: Sensitive Document Access Logging

### Title

Log all access to sensitive documents with audit events

### Description

Access to documents classified as sensitive (e.g., tax documents, signed legal agreements, identity verification artifacts, financial statements) must be recorded in the audit trail. This applies to both direct retrieval and presigned URL generation. The logging must capture who accessed what document, when, and through which path (API, support tool, export).

### Scope

- Identify document sensitivity classifications from the document vault model (Epic 5): `sensitive`, `restricted`, `confidential`
- Add audit event emission in the document retrieval service for any document with a sensitive classification
- Audit events use action `document.accessed_sensitive` with metadata:
  - `document_id`
  - `document_type` (e.g., `tax_form`, `signed_agreement`, `identity_document`)
  - `classification` (sensitivity level)
  - `access_method` (`api_download`, `presigned_url`, `support_tool`, `export`)
  - `associated_client_id` and `associated_account_id` where applicable
- Add audit event for presigned URL generation: `document.presigned_url_generated`
- Ensure access logging works for both advisor and support user access paths
- Include client portal document access (client accessing own documents) at a lower audit level

### Acceptance Criteria

- [ ] Every retrieval of a sensitive-classified document produces an audit event
- [ ] Presigned URL generation for sensitive documents produces an audit event
- [ ] Audit metadata includes document type, classification, access method, and associated entity references
- [ ] Support tool document access is logged with the support user as actor
- [ ] Client portal access to own sensitive documents is logged
- [ ] Non-sensitive document access does not produce individual audit events (to avoid log flooding)
- [ ] Audit events for document access are queryable by document ID, actor, and date range

### Dependencies

- Issue 16-1 (audit event model)
- Epic 5 (document vault, classification model, retrieval service)

---

## Issue 16-5: Audit Query API

### Title

Build audit event query API with filtering by tenant, actor, resource, workflow, and date range

### Description

Expose a read API for querying audit events. This API powers the admin audit log UI, support investigation workflows, and compliance review. All queries are implicitly scoped to the requesting user's tenant. Firm admins and compliance roles can query the full tenant audit trail; support users with cross-tenant access can query across tenants with additional audit logging of that access.

### Scope

- Create route group at `GET /api/audit/events`
- Supported query parameters:
  - `actor_id` (UUID, optional)
  - `actor_type` (enum, optional)
  - `action` (text, optional -- supports prefix matching, e.g., `order.*`)
  - `resource` (text, optional)
  - `resource_id` (UUID, optional)
  - `workflow` (text, optional)
  - `workflow_id` (UUID, optional)
  - `start_date` (ISO 8601 datetime, required)
  - `end_date` (ISO 8601 datetime, required)
  - `cursor` (opaque pagination token, optional)
  - `limit` (integer, default 50, max 200)
- Validate all inputs with Zod schemas
- Enforce maximum date range of 90 days per query to prevent unbounded scans
- Results ordered by timestamp descending
- Cursor-based pagination using `(timestamp, id)` composite cursor
- Permission: requires `audit.read` capability; `firm_admin` and `operations` roles have this by default
- Create presenter at `src/audit/presenters.ts` to serialize audit events for API response
- Add `GET /api/audit/events/:id` for single event retrieval

### Acceptance Criteria

- [ ] Audit events are queryable by tenant (implicit), actor, action, resource, workflow, and date range
- [ ] Action filter supports prefix matching (e.g., `order.*` matches `order.submitted` and `order.cancelled`)
- [ ] Date range is required and capped at 90 days
- [ ] Results are paginated with cursor-based pagination
- [ ] Response includes total count estimate and next cursor
- [ ] Single event retrieval by ID works and is tenant-scoped
- [ ] Queries are tenant-scoped automatically from auth context
- [ ] Only users with `audit.read` permission can access the endpoint
- [ ] Query performance is acceptable (under 500ms) for typical filter combinations using existing indexes

### Dependencies

- Issue 16-1 (audit event model and indexes)
- Epic 1 (permission enforcement middleware)

---

## Issue 16-6: Support Tooling -- Impersonation Session Management

### Title

Implement support impersonation session lifecycle with explicit permission, audit, and time-bound controls

### Description

Support staff need the ability to impersonate firm users for investigation and resolution. Impersonation is a high-risk privileged action that requires explicit permission (`support.impersonate`), an approval reference, a time-bound session, and comprehensive audit logging. Every action taken during an impersonation session must be traceable to the support actor.

### Scope

- Create impersonation session model:
  - `id` (UUID)
  - `support_user_id` (UUID -- the support staff member)
  - `target_tenant_id` (UUID)
  - `target_user_id` (UUID -- the firm user being impersonated)
  - `reason` (text, required)
  - `approval_reference` (text, nullable -- ticket ID or approval request ID)
  - `started_at` (timestamptz)
  - `ended_at` (timestamptz, nullable)
  - `max_duration_minutes` (integer, default 60)
  - `status` (enum: `active`, `ended`, `expired`)
- Create endpoints:
  - `POST /api/support/impersonation` -- start session (requires `support.impersonate` permission, idempotency key)
  - `GET /api/support/impersonation/:id` -- get session details
  - `POST /api/support/impersonation/:id/end` -- explicitly end session
  - `GET /api/support/impersonation/active` -- list active sessions
- Impersonation session issues a scoped JWT or session token that includes:
  - original `support_user_id` as the true actor
  - `impersonated_user_id` and `impersonated_tenant_id` as the assumed context
  - `impersonation_session_id` for correlation
  - reduced permission set (read-heavy, limited write actions)
- All actions during impersonation carry `actor_type: support_impersonator` in audit events
- Sessions auto-expire after `max_duration_minutes`
- Background job or middleware check to enforce expiration
- Emit audit events: `support.impersonation_started`, `support.impersonation_ended`, `support.impersonation_expired`

### Acceptance Criteria

- [ ] Impersonation requires `support.impersonate` permission
- [ ] Session creation requires a reason and produces an audit event
- [ ] Session is time-bound with configurable max duration (default 60 minutes)
- [ ] Impersonation token carries both support user and impersonated user identifiers
- [ ] All actions during impersonation are attributed to `actor_type: support_impersonator` with both user IDs
- [ ] Sessions can be explicitly ended and produce an end audit event
- [ ] Expired sessions are automatically invalidated
- [ ] Active sessions are listable for operational oversight
- [ ] Impersonation sessions support idempotency keys to prevent duplicate session creation

### Dependencies

- Issue 16-1 (audit event model)
- Issue 16-3 (privileged action auditing)
- Epic 1 (authentication, JWT issuance, role model with `support_impersonator` role)

---

## Issue 16-7: Support Tooling -- Investigation Search

### Title

Build cross-entity investigation search for support staff to look up clients, accounts, transfers, and orders

### Description

Support staff need a unified search capability to investigate issues across the platform. This search allows looking up a client, account, transfer, or order by various identifiers and then drilling into the full history and audit trail for that entity. The search is cross-entity but always tenant-scoped (or cross-tenant only for impersonation sessions).

### Scope

- Create route group at `GET /api/support/search`
- Supported search types:
  - `client` -- search by name, email, SSN last 4, client ID
  - `account` -- search by account number, account ID, registration type
  - `transfer` -- search by transfer ID, status, date range, amount range
  - `order` -- search by order intent ID, instrument symbol, status, date range
  - `household` -- search by household ID, primary client name
- Each search result returns a summary with links to detail endpoints
- Create detail endpoints:
  - `GET /api/support/clients/:id/history` -- client activity timeline
  - `GET /api/support/accounts/:id/history` -- account activity timeline
  - `GET /api/support/transfers/:id/history` -- transfer lifecycle with status transitions
  - `GET /api/support/orders/:id/history` -- order lifecycle with execution events
- History endpoints aggregate data from the relevant domain modules and audit events
- All support search and detail access is itself audit-logged
- Permission: requires `support.investigate` capability
- Results are paginated and respect tenant boundaries

### Acceptance Criteria

- [ ] Support staff can search clients by name, email, SSN last 4, or client ID
- [ ] Support staff can search accounts by account number or ID
- [ ] Support staff can search transfers by ID, status, date range, or amount
- [ ] Support staff can search orders by ID, symbol, status, or date range
- [ ] Each search result includes a summary and link to detail view
- [ ] Detail history endpoints show a complete timeline of state transitions and related audit events
- [ ] All search and detail access produces audit events
- [ ] Access requires `support.investigate` permission
- [ ] Searches respect tenant scoping (cross-tenant only during impersonation)
- [ ] Search results are paginated

### Dependencies

- Issue 16-1 (audit event model for history aggregation)
- Issue 16-6 (impersonation for cross-tenant access)
- Epic 2 (client, account models)
- Epic 7 (transfer models)
- Epic 9 (order models)

---

## Issue 16-8: Support Tooling -- Manual Intervention Capabilities

### Title

Build support tools for retrying failed transfers and forcing workflow transitions with audit notes

### Description

When automated workflows fail or get stuck, support staff need controlled manual intervention capabilities. These tools allow retrying failed operations and forcing workflow state transitions. Every intervention must require an audit note explaining the reason and must be logged as a privileged action. Interventions operate through the existing workflow and service layers, not through direct database manipulation.

### Scope

- **Transfer retry**:
  - `POST /api/support/transfers/:id/retry` -- retry a failed transfer
  - Validates transfer is in `failed` or `exception` status
  - Calls existing transfer service retry logic
  - Requires `support.intervene` permission
  - Requires `reason` (text) in request body
  - Produces `support.transfer_retried` audit event with transfer details and reason
- **Workflow force transition**:
  - `POST /api/support/workflows/:type/:id/force-transition` -- force a workflow state change
  - Supported workflow types: `onboarding_case`, `transfer_case`, `billing_run`
  - Request body: `target_status` (must be a valid status for the workflow type), `reason` (required text)
  - Validates the transition is at least structurally valid (cannot force to a status that has no meaning)
  - Requires `support.intervene` permission
  - Produces `support.workflow_forced` audit event with before/after states and reason
- **Bulk retry** (limited):
  - `POST /api/support/transfers/bulk-retry` -- retry multiple failed transfers
  - Accepts array of transfer IDs (max 50)
  - Returns per-transfer success/failure results
  - Each individual retry produces its own audit event
- All intervention endpoints require idempotency keys
- All interventions are logged with the support user as actor and the intervention reason in metadata

### Acceptance Criteria

- [ ] Failed transfers can be retried through the support API
- [ ] Transfer retry validates the transfer is in a retryable state before proceeding
- [ ] Workflow transitions can be forced with a required reason
- [ ] Forced transitions validate structural validity of the target status
- [ ] Bulk retry accepts up to 50 transfers and returns per-item results
- [ ] All interventions require `support.intervene` permission
- [ ] All interventions require a reason in the request body
- [ ] All interventions produce audit events with full context and reason
- [ ] Interventions use idempotency keys to prevent duplicate operations
- [ ] Interventions route through existing service layer logic (no direct database updates)

### Dependencies

- Issue 16-1 (audit event model)
- Issue 16-3 (privileged action auditing)
- Issue 16-6 (impersonation sessions for tenant-scoped access)
- Epic 3 (workflow state machine, case management)
- Epic 7 (transfer retry logic)

---

## Issue 16-9: Audit Event Export for Compliance

### Title

Build compliance export capability for audit events in CSV and JSON formats

### Description

Regulators and compliance officers need the ability to export audit records for review periods. The export must support filtered extraction (by date range, actor, resource type, workflow) and produce machine-readable output in CSV and JSON formats. Exports may be large and must be handled asynchronously with the result stored as a downloadable artifact.

### Scope

- Create export request endpoint: `POST /api/audit/exports`
  - Request body: filter criteria (same filters as query API) plus `format` (`csv` or `json`)
  - Returns `202 Accepted` with export job ID and polling URL
- Create export status endpoint: `GET /api/audit/exports/:id`
  - Returns export job status: `pending`, `processing`, `completed`, `failed`
  - When completed, includes a time-limited download URL
- Export job runs as a background worker (not in the HTTP request path)
- Worker streams audit events matching the filter criteria into the output format
- Output is written to object storage (same infrastructure as document vault)
- Download URLs are presigned with a configurable expiration (default 24 hours)
- CSV format includes all audit event fields with JSONB metadata flattened to a string column
- JSON format uses newline-delimited JSON (NDJSON) for streaming compatibility
- Export artifacts are themselves audit-logged: `audit.export_requested`, `audit.export_downloaded`
- Permission: requires `audit.export` capability (typically `firm_admin` or `compliance` role)
- Maximum export date range: 1 year per request

### Acceptance Criteria

- [ ] Export requests are accepted asynchronously and return a job ID
- [ ] Export jobs run in a background worker, not in the HTTP request path
- [ ] CSV export includes all audit event fields with readable headers
- [ ] JSON export uses NDJSON format
- [ ] Completed exports are stored in object storage with presigned download URLs
- [ ] Download URLs expire after a configurable period
- [ ] Export requests and downloads are themselves audit-logged
- [ ] Only users with `audit.export` permission can create and download exports
- [ ] Maximum date range per export is 1 year
- [ ] Large exports (millions of events) complete without memory exhaustion (streaming)

### Dependencies

- Issue 16-1 (audit event model)
- Issue 16-5 (audit query logic for filter reuse)
- Epic 5 (object storage infrastructure for export artifacts)

---

## Issue 16-10: Audit Retention Policy

### Title

Implement configurable per-tenant audit retention with regulatory minimum enforcement

### Description

Audit records must be retained for a configurable period per tenant, with a platform-enforced minimum of 7 years to satisfy SEC, FINRA, and state regulatory requirements. Retention policy management includes configuration, enforcement through scheduled cleanup of expired records (archive or delete), and protection against accidental or premature deletion.

### Scope

- Create `audit_retention_policies` table:
  - `id` (UUID)
  - `tenant_id` (UUID, unique -- one policy per tenant)
  - `retention_years` (integer, NOT NULL, minimum 7)
  - `archive_strategy` (enum: `delete`, `archive_to_cold_storage`)
  - `created_at` (timestamptz)
  - `updated_at` (timestamptz)
  - `updated_by` (UUID -- actor who last modified the policy)
- Default policy: 7 years retention, archive to cold storage
- Create admin endpoints:
  - `GET /api/admin/audit/retention-policy` -- get current tenant policy
  - `PUT /api/admin/audit/retention-policy` -- update tenant policy (minimum 7 years enforced)
- Application-level constraint: `retention_years` cannot be set below 7
- Create scheduled background job (daily) that:
  - For each tenant, checks retention policy
  - Identifies audit events older than the retention period
  - If strategy is `archive_to_cold_storage`: exports events to object storage in NDJSON format, then marks as archived
  - If strategy is `delete`: removes archived events that have been in cold storage for an additional grace period (30 days)
  - Logs the retention job execution as an audit event itself
- Add `archived_at` column to `audit_events` (nullable) to track archival status
- Partitioning consideration: document recommendation for table partitioning by month for production deployments to support efficient range-based cleanup

### Acceptance Criteria

- [ ] Each tenant has a configurable retention policy with a minimum of 7 years
- [ ] Attempts to set retention below 7 years are rejected with a clear error
- [ ] Default policy is created automatically for new tenants
- [ ] Daily background job processes retention for all tenants
- [ ] Archive-to-cold-storage strategy exports events to object storage before removal
- [ ] Delete strategy only removes events that have already been archived and past the grace period
- [ ] Retention job execution is itself logged as an audit event
- [ ] Policy changes are logged as audit events with before/after values
- [ ] Only `firm_admin` can modify the retention policy
- [ ] Documentation includes partitioning recommendation for production Postgres deployments

### Dependencies

- Issue 16-1 (audit event model)
- Issue 16-9 (export infrastructure reuse for archival)
- Epic 5 (object storage for cold storage archives)

---

## Issue 16-11: Surveillance and Exception Event Tracking

### Title

Track and surface unusual patterns, failed reconciliations, and repeated failures as surveillance events

### Description

Beyond action-by-action auditing, the platform needs surveillance-level event tracking that identifies patterns indicating operational risk, compliance concern, or system degradation. This includes repeated authentication failures, unusual transfer patterns, failed reconciliations, repeated workflow exceptions, and other anomaly indicators. Surveillance events are stored in the audit event store with a distinct category and surfaced through dedicated query capabilities.

### Scope

- Define surveillance event types:
  - `surveillance.repeated_auth_failures` -- N failed login attempts for a user within a time window
  - `surveillance.unusual_transfer_pattern` -- transfers exceeding configurable thresholds (amount, frequency)
  - `surveillance.failed_reconciliation` -- reconciliation job detected a break
  - `surveillance.repeated_workflow_failures` -- workflow step failed N times for the same case
  - `surveillance.bulk_order_anomaly` -- unusual volume of order submissions from a single actor
  - `surveillance.permission_escalation` -- role change that grants elevated permissions
  - `surveillance.after_hours_activity` -- privileged actions outside configured business hours
- Create a surveillance event emitter service at `src/audit/surveillance.ts`
- Surveillance events use the same `audit_events` table with `action` prefixed by `surveillance.`
- Surveillance metadata includes:
  - `threshold_config` (what threshold was breached)
  - `observed_value` (actual count or amount)
  - `window_start` and `window_end` (observation period)
  - `affected_entities` (list of entity IDs involved)
- Create threshold configuration:
  - Default thresholds defined in application config
  - Per-tenant overrides stored in `surveillance_thresholds` table
- Evaluation triggers:
  - Some surveillance checks run as after-effects of normal audit events (e.g., auth failure counting)
  - Some run as scheduled background jobs (e.g., reconciliation break detection, pattern analysis)
- Create query endpoint: `GET /api/audit/surveillance` with same filter capabilities as audit query plus `severity` filter (`info`, `warning`, `critical`)
- Surveillance events can trigger notification events (integration point with Epic 15)

### Acceptance Criteria

- [ ] Repeated authentication failures beyond threshold generate a surveillance event
- [ ] Transfer amounts or frequencies exceeding thresholds generate surveillance events
- [ ] Failed reconciliation jobs generate surveillance events
- [ ] Repeated workflow failures for the same case generate surveillance events
- [ ] Unusual order volume generates surveillance events
- [ ] Role escalations generate surveillance events
- [ ] After-hours privileged actions generate surveillance events
- [ ] Surveillance events are stored in the audit event table with `surveillance.*` action prefix
- [ ] Thresholds are configurable with sensible defaults
- [ ] Per-tenant threshold overrides are supported
- [ ] Surveillance events are queryable through a dedicated API endpoint with severity filtering
- [ ] Surveillance event metadata includes threshold, observed value, and time window
- [ ] Surveillance checks run both reactively (after audit events) and on schedule (background jobs)

### Dependencies

- Issue 16-1 (audit event model -- shared table)
- Issue 16-2 (audit middleware -- surveillance checks triggered by audit events)
- Issue 16-5 (audit query infrastructure for surveillance query endpoint)
- Epic 1 (authentication events for failure tracking)
- Epic 15 (notification integration for alerting on critical surveillance events)
