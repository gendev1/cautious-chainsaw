# Epic 8: Advisor Portal Experience

## Goal

Provide the advisor-facing API surface over the underlying workflow and records platform. This epic builds the experience layer that advisors interact with daily -- dashboards, workspaces, search, notifications, and settings. Every endpoint in this epic is a composition or projection of data owned by Epics 1 through 7; it does not introduce new authoritative records, but it defines the API contracts that shape the advisor product.

All endpoints are scoped to the authenticated advisor's tenant and respect the role and permission model from Epic 1. The focus is on API contracts (request/response schemas, pagination, filtering, command mappings), not frontend UI implementation.

## Dependencies

- Epic 1: Tenant, Identity, and Access Control (authentication, roles, permissions)
- Epic 2: Client, Household, and Account Registry (households, clients, accounts, beneficiaries)
- Epic 3: Workflow and Case Management (onboarding cases, transfer cases, tasks, approvals, exceptions)
- Epic 4: External Service Integration Framework (projection sync, upstream status freshness)
- Epic 5: Document Vault and Records Management (document metadata, upload, retrieval, classification)
- Epic 6: Onboarding and Account Opening (onboarding case lifecycle, sub-resources)
- Epic 7: Money Movement and Transfer Operations (transfer intents, status history, lifecycle)

---

## Issue 1: Advisor Dashboard API

### Title

Implement advisor dashboard API with aggregated firm-level statistics

### Description

Build a dedicated dashboard endpoint that returns a composite view of the advisor's firm-level operating state. This is the landing page data source -- it must be fast, cached where appropriate, and give the advisor immediate situational awareness without requiring multiple round-trips.

The dashboard aggregates data across households, clients, accounts, workflows, and recent activity. It should use Redis-cached projections for expensive aggregations (total AUM, client count) and real-time queries only for time-sensitive items (pending tasks, recent activity).

### Scope

- `GET /api/advisor/dashboard` -- returns the composite dashboard payload
- Response sections:
  - `firmSummary`: total AUM, total client count, total household count, total account count
  - `pendingWork`: pending onboarding cases count, pending transfer cases count, pending approval requests count, pending operational tasks count, exceptions needing attention count
  - `recentActivity`: last N activity events across the firm (onboarding submitted, transfer completed, account activated, etc.), each with timestamp, actor, resource type, resource ID, and summary text
  - `alerts`: system-level alerts (stale projections, integration health warnings) if the advisor has appropriate permissions
- Zod schema for the full response envelope
- Redis cache layer for AUM and count aggregations with configurable TTL (suggest 60s default)
- Cache invalidation on material state changes (account activation, transfer completion, etc.)
- Presenter layer that transforms internal aggregation results into the stable API contract
- Permission: requires `advisor` role or higher

### Acceptance Criteria

- [ ] `GET /api/advisor/dashboard` returns 200 with the full composite payload
- [ ] AUM and client/household/account counts are served from Redis cache when available, with fallback to direct query
- [ ] Pending work counts reflect current workflow states from the case and task tables
- [ ] Recent activity returns the 20 most recent events, ordered by timestamp descending
- [ ] Response conforms to the Zod schema and is validated at the presenter boundary
- [ ] Endpoint returns within 200ms under normal cache conditions
- [ ] Unauthorized users receive 403
- [ ] All counts are tenant-scoped -- no cross-tenant data leakage

### Dependencies

- Epic 1: authentication middleware, role resolution
- Epic 2: household, client, account counts and AUM aggregation queries
- Epic 3: pending task, approval, and exception counts
- Epic 7: pending transfer counts

---

## Issue 2: Household and Client Views

### Title

Implement household summary, client detail, and account list APIs for advisor consumption

### Description

Build the advisor-facing read APIs for browsing and inspecting households, clients, and their associated accounts. These endpoints compose data from the registry (Epic 2) with workflow status summaries (Epic 3) and balance projections to give the advisor a complete picture of each household and client.

These are read-only projection endpoints. They do not duplicate the CRUD endpoints from Epic 2 but instead provide richer, advisor-optimized response shapes that include cross-domain data (e.g., a client detail view that includes account summaries, active cases, and pending tasks).

### Scope

- `GET /api/advisor/households` -- paginated list of households with summary stats (member count, total AUM, account count, active case count)
- `GET /api/advisor/households/:householdId` -- household detail with member list, account roll-up, active onboarding/transfer cases, and recent activity
- `GET /api/advisor/clients/:clientId` -- client detail with personal/entity info, household membership, account list, active cases, document count, and pending tasks
- `GET /api/advisor/clients/:clientId/accounts` -- paginated account list for a client with account type, status, balance summary, and model assignment
- Zod schemas for all request params (path, query) and response payloads
- Cursor-based pagination on list endpoints (see Issue 8 for shared pagination contract)
- Sorting options: by name, by AUM, by account count, by last activity date
- Filter options on household list: advisor assignment, has active cases, AUM range
- Permission: requires `client.read` capability

### Acceptance Criteria

- [ ] `GET /api/advisor/households` returns paginated household summaries with cursor-based pagination
- [ ] `GET /api/advisor/households/:householdId` returns full household detail including members, accounts, active cases, and recent activity
- [ ] `GET /api/advisor/clients/:clientId` returns client detail with cross-domain summaries
- [ ] `GET /api/advisor/clients/:clientId/accounts` returns paginated account list with balance and status
- [ ] All responses conform to Zod schemas
- [ ] Sorting and filtering work correctly on the households list
- [ ] Non-existent resources return 404 with the standard error envelope
- [ ] All data is tenant-scoped; advisors cannot access clients outside their tenant
- [ ] Permission enforcement: users without `client.read` receive 403

### Dependencies

- Epic 2: household, client, account repositories and relationship queries
- Epic 3: active case and task counts per client/household
- Epic 11 (future): balance projections (stub or omit until available)

---

## Issue 3: Onboarding Workspace API

### Title

Implement onboarding workspace APIs for case listing, detail, sub-resources, and workflow actions

### Description

Build the advisor-facing workspace for managing onboarding cases. This workspace surfaces the onboarding case list, individual case detail with all sub-resources (client data, account registrations, disclosures, documents, notes, approval history), and maps action buttons to the underlying workflow commands defined in Epic 6.

The workspace does not own the onboarding business logic -- it delegates to the onboarding case service. Its job is to present the right data shape for the advisor UI and expose the correct command endpoints with clear precondition feedback.

### Scope

- `GET /api/advisor/onboarding-cases` -- paginated list with filters (status, advisor, date range, client name search)
- `GET /api/advisor/onboarding-cases/:caseId` -- full case detail including:
  - case metadata (status, created date, last updated, assigned advisor)
  - client and household references with inline summaries
  - account registrations associated with the case
  - disclosures and consent status
  - beneficiary and trusted contact status
  - attached documents with classification
  - notes and comments (append-only timeline)
  - approval history (who approved/rejected, when, with reason)
  - available actions (computed from current case status and actor permissions)
- Command endpoints (delegating to Epic 6 service layer):
  - `POST /api/advisor/onboarding-cases/:caseId/submit`
  - `POST /api/advisor/onboarding-cases/:caseId/request-client-action`
  - `POST /api/advisor/onboarding-cases/:caseId/approve`
  - `POST /api/advisor/onboarding-cases/:caseId/reject`
  - `POST /api/advisor/onboarding-cases/:caseId/add-note`
- `availableActions` field on the detail response: an array of action descriptors (action name, label, enabled flag, reason if disabled) computed from the case state machine and the requesting actor's permissions
- Zod schemas for all inputs and outputs

### Acceptance Criteria

- [ ] `GET /api/advisor/onboarding-cases` returns paginated, filterable case list
- [ ] `GET /api/advisor/onboarding-cases/:caseId` returns full case detail with all sub-resources
- [ ] `availableActions` correctly reflects the case status and actor permissions (e.g., `approve` is only available in `pending_internal_review` status for users with the appropriate permission)
- [ ] Each command endpoint delegates to the onboarding case service and returns the updated case state
- [ ] Invalid workflow transitions return 409 with `INVALID_WORKFLOW_STATE` error code
- [ ] `add-note` accepts text content and records the actor and timestamp
- [ ] All command endpoints accept an idempotency key header
- [ ] Audit events are emitted for all command actions
- [ ] Permission enforcement: `account.open` required for submit/approve/reject actions

### Dependencies

- Epic 3: workflow and case management engine
- Epic 5: document attachment queries
- Epic 6: onboarding case service, state machine, command handlers

---

## Issue 4: Transfer Workspace API

### Title

Implement transfer workspace APIs for listing, detail, status history, and workflow actions

### Description

Build the advisor-facing workspace for managing money movement transfers. This workspace surfaces the transfer list, individual transfer detail with full status history, and maps action buttons to transfer workflow commands from Epic 7.

Transfers have complex lifecycle states driven by both platform actions and asynchronous upstream events. The workspace must present the current status clearly, show the full status history timeline, and indicate which actions are available given the current state.

### Scope

- `GET /api/advisor/transfers` -- paginated list with filters (status, transfer type, client, account, date range, amount range)
- `GET /api/advisor/transfers/:transferId` -- full transfer detail including:
  - transfer metadata (type, amount, source, destination, status, created date)
  - client and account references with inline summaries
  - status history timeline (each status transition with timestamp, actor or system source, and notes)
  - linked onboarding case reference (if transfer originated from onboarding)
  - attached documents
  - upstream sync status and last synced timestamp
  - available actions
- Command endpoints (delegating to Epic 7 service layer):
  - `POST /api/advisor/transfers/:transferId/submit`
  - `POST /api/advisor/transfers/:transferId/cancel`
  - `POST /api/advisor/transfers/:transferId/retry-sync`
- `availableActions` field computed from transfer status, upstream sync state, and actor permissions
- Zod schemas for all inputs and outputs

### Acceptance Criteria

- [ ] `GET /api/advisor/transfers` returns paginated, filterable transfer list
- [ ] `GET /api/advisor/transfers/:transferId` returns full detail with status history timeline
- [ ] Status history includes all transitions with timestamps and actors
- [ ] `availableActions` correctly reflects transfer status (e.g., `cancel` only available for cancellable states, `retry-sync` only when sync has failed)
- [ ] Each command endpoint delegates to the transfer service and returns updated state
- [ ] Invalid transitions return 409 with `INVALID_WORKFLOW_STATE`
- [ ] All command endpoints accept an idempotency key header
- [ ] Upstream sync freshness is visible in the response (`lastSyncedAt`, `syncStatus`)
- [ ] Audit events are emitted for all command actions
- [ ] Permission enforcement: `transfer.submit` required for submit/cancel actions

### Dependencies

- Epic 3: workflow engine for status transitions
- Epic 7: transfer case service, state machine, command handlers
- Epic 4: upstream sync status metadata

---

## Issue 5: Document Workspace API

### Title

Implement document workspace APIs for browsing, uploading, classifying, and attaching documents

### Description

Build the advisor-facing document workspace that allows advisors to browse documents by client, account, or case context, upload new documents, classify them, and attach them to relevant resources. This workspace composes the document vault from Epic 5 with contextual navigation from Epic 2 (clients/accounts) and Epic 3 (cases).

Document operations are security-sensitive. Access to sensitive documents must be permissioned and audited. The upload flow should use pre-signed URLs for direct-to-storage upload with metadata registration in the API server.

### Scope

- `GET /api/advisor/documents` -- paginated list with filters (client ID, account ID, case ID, case type, document classification, date range, upload source)
- `GET /api/advisor/documents/:documentId` -- document metadata detail including classification, attached resources, upload source, created date, and a time-limited signed retrieval URL
- `POST /api/advisor/documents` -- initiate document upload; returns a pre-signed upload URL and a document record ID in `pending_upload` status
  - Request body: client ID (optional), account ID (optional), case ID (optional), filename, content type, classification hint (optional)
- `POST /api/advisor/documents/:documentId/confirm-upload` -- confirm that the upload completed; transitions document to `uploaded` status
- `POST /api/advisor/documents/:documentId/classify` -- set or update document classification (e.g., `identity_document`, `account_statement`, `transfer_form`, `tax_document`, `correspondence`, `other`)
  - Request body: classification enum value, notes (optional)
- `POST /api/advisor/documents/:documentId/attach` -- attach document to a resource
  - Request body: resource type (`client`, `account`, `onboarding_case`, `transfer_case`), resource ID
- Zod schemas for all inputs and outputs
- Signed URL generation with configurable expiry (suggest 15 minutes for upload, 5 minutes for retrieval)

### Acceptance Criteria

- [ ] `GET /api/advisor/documents` returns paginated, filterable document list
- [ ] Documents are filterable by client, account, case, classification, and date range
- [ ] `GET /api/advisor/documents/:documentId` returns metadata and a time-limited signed retrieval URL
- [ ] `POST /api/advisor/documents` creates a document record and returns a pre-signed upload URL
- [ ] `POST /api/advisor/documents/:documentId/confirm-upload` transitions the document to `uploaded` status
- [ ] `POST /api/advisor/documents/:documentId/classify` updates classification and records the actor
- [ ] `POST /api/advisor/documents/:documentId/attach` creates an association between the document and the target resource
- [ ] Access to documents classified as sensitive (e.g., `identity_document`) emits an audit event
- [ ] Signed URLs expire after the configured TTL
- [ ] Permission enforcement: `document.read_sensitive` required for sensitive document retrieval
- [ ] All mutations emit audit events

### Dependencies

- Epic 5: document vault service, object storage integration, metadata repository
- Epic 2: client and account validation for attachment targets
- Epic 3: case validation for attachment targets

---

## Issue 6: Task and Exception Queue API

### Title

Implement task queue and exception queue APIs for pending work, approvals, and exception resolution

### Description

Build the advisor-facing APIs for managing pending operational work. This includes pending tasks (items requiring advisor action), pending approvals (items awaiting sign-off from an authorized actor), and exceptions (workflow items that have entered an error or exception state and require manual resolution).

This workspace is critical for operational reliability. Advisors and operations staff need a single place to see all work that requires their attention, prioritized and filterable. The task queue pulls from operational tasks, approval requests, and exception states across onboarding, transfers, and other workflow domains.

### Scope

- `GET /api/advisor/tasks` -- unified pending work queue with filters:
  - task type (`operational_task`, `approval_request`, `exception`)
  - status (`pending`, `in_progress`, `escalated`)
  - domain (`onboarding`, `transfer`, `billing`, `account`)
  - assigned to (advisor ID, or `unassigned`)
  - priority (`high`, `normal`, `low`)
  - date range (created, due date)
  - Sorted by priority then due date by default
- `GET /api/advisor/tasks/:taskId` -- task detail including:
  - task metadata (type, status, priority, domain, created date, due date, SLA status)
  - linked resource (case ID, transfer ID, account ID, etc.) with inline summary
  - assignment info (assigned to, assigned at, assigned by)
  - notes and resolution history
  - available actions
- Command endpoints:
  - `POST /api/advisor/tasks/:taskId/assign` -- assign or reassign task (body: assignee user ID)
  - `POST /api/advisor/tasks/:taskId/resolve` -- mark task as resolved (body: resolution notes)
  - `POST /api/advisor/tasks/:taskId/escalate` -- escalate task (body: reason, target role or user)
  - `POST /api/advisor/tasks/:taskId/add-note` -- add a note to the task timeline
- `GET /api/advisor/tasks/counts` -- summary counts by type, domain, and priority for badge/indicator rendering
- Zod schemas for all inputs and outputs

### Acceptance Criteria

- [ ] `GET /api/advisor/tasks` returns paginated, filterable unified work queue
- [ ] Tasks from onboarding cases, transfer cases, and approval requests all appear in the unified queue
- [ ] `GET /api/advisor/tasks/:taskId` returns full task detail with linked resource summary
- [ ] `GET /api/advisor/tasks/counts` returns grouped counts suitable for rendering badges
- [ ] `assign` correctly updates assignment and emits an audit event
- [ ] `resolve` transitions the task to resolved status with resolution notes
- [ ] `escalate` changes priority/assignment and emits an audit event
- [ ] SLA status is computed (e.g., `on_track`, `at_risk`, `breached`) based on due date and current time
- [ ] Permission enforcement: operations staff and advisors can view; resolve and escalate require appropriate role
- [ ] Resolved tasks no longer appear in default pending queries (but are retrievable with status filter)

### Dependencies

- Epic 3: operational tasks, approval requests, exception states, SLA timers
- Epic 6: onboarding-related tasks and exceptions
- Epic 7: transfer-related tasks and exceptions

---

## Issue 7: Portfolio and Proposal Views

### Title

Implement portfolio and proposal view APIs for model assignments, drift, proposals, and release actions

### Description

Build the advisor-facing read and action APIs for portfolio management. This includes viewing model assignments per account, portfolio drift summaries, rebalance proposal detail, and the release action that sends a proposal to execution.

These endpoints compose data from the portfolio engine (Epic 10, when available) and present it in the advisor workspace. For Phase 1, some data (drift calculations, proposal generation) may be stubbed or simplified, but the API contract should be designed for the full workflow.

### Scope

- `GET /api/advisor/accounts/:accountId/portfolio` -- portfolio summary for an account:
  - assigned model (if any) with model name, target allocations
  - current holdings summary (positions with current weights)
  - drift summary (per-position drift from target, overall drift score, drift status: `in_band`, `approaching_threshold`, `out_of_band`)
  - last rebalance date
- `GET /api/advisor/models` -- list available model portfolios with name, description, target allocations, and assignment count
- `GET /api/advisor/models/:modelId` -- model detail with full target allocation breakdown and list of assigned accounts
- `GET /api/advisor/proposals` -- paginated list of rebalance proposals with filters (status, model, account, date range)
- `GET /api/advisor/proposals/:proposalId` -- proposal detail including:
  - proposal metadata (status, generated date, model snapshot, account snapshot)
  - proposed trades (security, direction, quantity, estimated amount, rationale)
  - tax impact summary (estimated short-term gains, long-term gains, wash sale warnings)
  - assumptions used for generation (prices as of, holdings as of, model version)
  - available actions
- Command endpoints:
  - `POST /api/advisor/proposals/:proposalId/release` -- release proposal for execution (creates order intents via Epic 9)
  - `POST /api/advisor/proposals/:proposalId/cancel` -- cancel proposal
- Zod schemas for all inputs and outputs

### Acceptance Criteria

- [ ] `GET /api/advisor/accounts/:accountId/portfolio` returns portfolio summary with drift information
- [ ] `GET /api/advisor/models` returns paginated model list
- [ ] `GET /api/advisor/models/:modelId` returns model detail with assigned accounts
- [ ] `GET /api/advisor/proposals` returns paginated, filterable proposal list
- [ ] `GET /api/advisor/proposals/:proposalId` returns full proposal detail with proposed trades and tax impact
- [ ] Proposal detail includes the exact assumptions (price timestamps, holdings timestamps, model version) used for generation
- [ ] `release` transitions proposal to `released` status and returns 202 with the proposal ID and polling URL
- [ ] `cancel` transitions proposal to `cancelled` status
- [ ] Invalid transitions return 409 with `INVALID_WORKFLOW_STATE`
- [ ] Release requires `order.submit` permission
- [ ] All command endpoints accept an idempotency key header

### Dependencies

- Epic 2: account registry for portfolio context
- Epic 9: order intent creation upon proposal release (future)
- Epic 10: model portfolio and rebalance proposal engine (future; stub as needed)

---

## Issue 8: Search and Filtering Infrastructure

### Title

Implement cross-domain search and cursor-based pagination for advisor workspace

### Description

Build a shared search and filtering infrastructure that supports all advisor workspace list endpoints. This includes a unified search endpoint for cross-domain queries and a standardized cursor-based pagination contract used by all list endpoints in this epic.

Search must be fast enough for typeahead use cases (under 150ms) and support filtering across clients, accounts, transfers, and cases from a single query. Individual domain list endpoints (Issues 2-7) use the same pagination primitives.

### Scope

- `GET /api/advisor/search` -- cross-domain search endpoint
  - Query parameter: `q` (search term, minimum 2 characters)
  - Query parameter: `domain` (optional filter: `client`, `account`, `household`, `transfer`, `onboarding_case`, or omit for all)
  - Query parameter: `limit` (default 10, max 50)
  - Returns grouped results by domain, each with resource ID, display name, resource type, status, and relevance metadata
  - Searches across: client name, client email, account number, household name, transfer reference, case reference
- Shared pagination contract (used by all list endpoints):
  - Cursor-based using opaque encoded cursors (not offset-based)
  - Standard query parameters: `cursor` (opaque string), `limit` (default 25, max 100), `sort` (field name), `order` (`asc` or `desc`)
  - Standard response envelope: `{ data: T[], pagination: { cursor: string | null, hasMore: boolean, totalCount: number } }`
  - `totalCount` is optional and may be omitted on expensive queries (indicated by `totalCount: null`)
- Shared filter primitives:
  - Date range filters: `createdAfter`, `createdBefore`, `updatedAfter`, `updatedBefore`
  - Status filters: `status` (single or comma-separated list)
  - Zod schemas for pagination params, filter params, and pagination response envelope
- Postgres-backed search using `ILIKE` or `tsvector` for full-text search depending on scale needs
- Redis cache for frequent search patterns (optional, with short TTL)

### Acceptance Criteria

- [ ] `GET /api/advisor/search?q=...` returns grouped cross-domain results
- [ ] Search results are scoped to the advisor's tenant
- [ ] Search responds within 150ms for typical queries
- [ ] Domain filter narrows results to a single resource type
- [ ] All list endpoints across Issues 2-7 use the shared cursor-based pagination contract
- [ ] Cursor values are opaque and tamper-resistant (base64-encoded, not raw IDs)
- [ ] Pagination works correctly at boundaries (first page, last page, empty results)
- [ ] `hasMore` is accurate; requesting the next cursor after the last page returns empty data
- [ ] Shared Zod schemas are importable by all workspace route modules
- [ ] Sort and order parameters work correctly on all list endpoints

### Dependencies

- Epic 2: client, household, account search indices
- Epic 3: case reference search
- Epic 7: transfer reference search

---

## Issue 9: Notification Inbox API

### Title

Implement in-app notification inbox with read/unread state and mark-as-read actions

### Description

Build the advisor-facing notification inbox API. Notifications are generated by platform events (workflow transitions, task assignments, approval requests, transfer completions, exceptions) and delivered to the appropriate advisor's inbox. This issue covers the inbox read and management APIs; notification generation and routing logic belongs to Epic 15.

The inbox must support efficient polling or cursor-based retrieval for new notifications, bulk mark-as-read, and individual dismissal.

### Scope

- `GET /api/advisor/notifications` -- paginated notification list with filters:
  - `read` (boolean: `true`, `false`, or omit for all)
  - `category` (e.g., `task`, `approval`, `transfer`, `onboarding`, `system`)
  - `priority` (`high`, `normal`, `low`)
  - Sorted by created date descending by default
- `GET /api/advisor/notifications/unread-count` -- returns the count of unread notifications (optimized for polling; should use Redis counter)
- `GET /api/advisor/notifications/:notificationId` -- notification detail with full message, linked resource reference, and metadata
- `POST /api/advisor/notifications/:notificationId/read` -- mark a single notification as read
- `POST /api/advisor/notifications/mark-read` -- bulk mark-as-read
  - Request body: `{ notificationIds: string[] }` (max 100 per request)
  - Alternative: `{ before: ISO8601 timestamp }` to mark all notifications before a date as read
- `POST /api/advisor/notifications/:notificationId/dismiss` -- dismiss (soft-delete) a notification; dismissed notifications do not appear in default queries
- Notification record shape:
  - `id`, `category`, `priority`, `title`, `body`, `resourceType`, `resourceId`, `read`, `dismissed`, `createdAt`
- Zod schemas for all inputs and outputs

### Acceptance Criteria

- [ ] `GET /api/advisor/notifications` returns paginated notification list with cursor-based pagination
- [ ] Filtering by `read`, `category`, and `priority` works correctly
- [ ] `GET /api/advisor/notifications/unread-count` returns accurate count, optimized for frequent polling
- [ ] `POST .../read` marks a single notification as read and returns 204
- [ ] `POST .../mark-read` supports both ID-based and timestamp-based bulk marking
- [ ] Bulk mark-as-read is limited to 100 IDs per request; exceeding returns 400
- [ ] `POST .../dismiss` soft-deletes the notification; dismissed notifications excluded from default list
- [ ] Notifications are scoped to the authenticated advisor (not shared across the tenant)
- [ ] Unread count is served from Redis with write-through updates
- [ ] All responses conform to Zod schemas

### Dependencies

- Epic 15: notification generation and routing (this issue provides the inbox API; Epic 15 provides the event-to-notification pipeline)
- Epic 3: workflow events that trigger notifications
- Epic 1: user identity for inbox scoping

---

## Issue 10: Advisor Settings and Preferences API

### Title

Implement advisor settings and preferences APIs for personal configuration

### Description

Build the APIs that allow advisors to manage their personal settings and preferences within the platform. These settings control the advisor's experience (default views, notification preferences, display options) and are distinct from firm-level configuration managed by `firm_admin` users.

Settings are per-user and tenant-scoped. They should be lightweight to read (cached in Redis) and infrequently written.

### Scope

- `GET /api/advisor/settings` -- returns the advisor's full settings object
- `PATCH /api/advisor/settings` -- partial update to settings; merges with existing settings
- Settings schema:
  - `notifications`: per-category toggle for in-app and email notifications (e.g., `{ onboarding: { inApp: true, email: true }, transfers: { inApp: true, email: false }, tasks: { inApp: true, email: true }, system: { inApp: true, email: false } }`)
  - `dashboard`: default date range, preferred sort for recent activity, preferred AUM display currency
  - `display`: timezone (IANA), date format preference, number format (locale), items per page default
  - `workspaces`: per-workspace default filters and sort preferences (e.g., onboarding workspace default status filter)
- `GET /api/advisor/profile` -- returns the advisor's profile information (name, email, role, firm, avatar URL, last login)
- `PATCH /api/advisor/profile` -- update limited profile fields (display name, avatar URL, phone number)
- Zod schemas for settings and profile with strict validation (e.g., timezone must be a valid IANA zone, date format from allowed set)
- Redis cache for settings reads with write-through invalidation on updates
- Default settings factory: new users get a sensible default settings object on first access

### Acceptance Criteria

- [ ] `GET /api/advisor/settings` returns the full settings object; first-time access returns defaults
- [ ] `PATCH /api/advisor/settings` performs a partial merge and returns the updated settings
- [ ] Invalid setting values (bad timezone, unknown category) return 400 with `VALIDATION_ERROR`
- [ ] Settings are per-user and tenant-scoped; advisors cannot read or write other users' settings
- [ ] Settings reads are served from Redis cache; cache is invalidated on write
- [ ] `GET /api/advisor/profile` returns profile information including role and firm context
- [ ] `PATCH /api/advisor/profile` allows updating display name, avatar URL, and phone number only
- [ ] Profile updates emit an audit event
- [ ] Attempting to update restricted profile fields (email, role) returns 400
- [ ] All responses conform to Zod schemas

### Dependencies

- Epic 1: user identity, role resolution, tenant scoping
- Epic 15: notification preferences are consumed by the notification routing engine (future)
