# Epic 3: Workflow and Case Management

## Goal

Create the chassis for long-running onboarding, transfer, approval, and exception workflows. This epic establishes the generic state machine infrastructure and the concrete case models that every downstream domain (onboarding, transfers, billing, trading) depends on. Without this foundation, the platform becomes a collection of CRUD endpoints with no operating model.

## Dependencies

- Epic 1: Tenant, Identity, and Access Control (tenant context, actor identity, role-based permissions)
- Epic 2: Client, Household, and Account Registry (households, clients, accounts referenced by cases)

## Architectural Context

- Workflow state is persisted in Postgres as the system of record.
- Redis is used for workflow locks, SLA timer coordination, and ephemeral deduplication keys.
- Kafka carries domain events (`onboarding.case_submitted`, `transfer.completed`, etc.) for decoupled consumers: notifications, read model updates, audit projections.
- Workflow logic lives in `modules/` service layers and `workflows/` runners, never in Hono route handlers.
- All mutating workflow commands carry request ID, tenant ID, actor ID, idempotency key, and correlation ID.

## Tech Stack

Node.js, TypeScript, Hono, Zod, Postgres, Redis, KafkaJS

---

## Issue 1: Generic Case and Workflow State Machine Infrastructure

### Title

Implement generic workflow state machine engine

### Description

Build a reusable state machine infrastructure that all case types (onboarding, transfer, and future domains like billing and trading) compose over. The engine defines how statuses, transitions, guards, and side effects are declared and executed. It is not a visual workflow designer; it is a typed, code-defined transition graph that enforces valid state changes, records every transition, and guarantees that business logic cannot bypass the state machine.

### Scope

- Define a `WorkflowDefinition<TStatus>` type that declares:
  - an enum of valid statuses
  - a set of allowed transitions (from-status to to-status pairs)
  - optional guard functions per transition (sync checks that must pass before the transition is allowed)
  - optional side-effect hooks per transition (post-commit actions such as event emission)
- Implement a `WorkflowEngine.transition(caseId, targetStatus, context)` function that:
  - loads current case state within a Postgres transaction
  - validates the requested transition against the definition
  - executes guard functions
  - persists the new status and a transition history record atomically
  - emits domain events after commit
- Acquire a Redis-based advisory lock per case during transition to prevent concurrent mutations.
- Provide a `WorkflowDefinition.canTransition(fromStatus, toStatus)` query for UI and validation layers.
- Define shared Postgres schema patterns: `status` column, `status_changed_at`, `status_changed_by`, `workflow_correlation_id`.
- All transitions must be tenant-scoped: the engine must verify `tenant_id` on every operation.

### Acceptance Criteria

- A workflow definition can be declared with typed statuses and a transition map.
- Attempting an invalid transition returns a structured `INVALID_WORKFLOW_STATE` error.
- Guard functions can reject a transition with a reason before any write occurs.
- Every successful transition writes a row to the transition history table within the same database transaction as the status update.
- Concurrent transition attempts on the same case are serialized via Redis lock; the second caller receives a conflict error.
- Domain events are published to Kafka only after the transaction commits.
- Unit tests cover: valid transitions, invalid transitions, guard rejection, concurrent lock contention, and event emission ordering.

### Dependencies

- Postgres connection pool and transaction helper (shared infrastructure)
- Redis client (shared infrastructure)
- Kafka producer (shared infrastructure)
- Tenant context middleware from Epic 1

---

## Issue 2: Onboarding Case Model

### Title

Implement onboarding case entity, statuses, and transitions

### Description

Define the `OnboardingCase` domain model that tracks the full lifecycle of opening one or more accounts for a household. The onboarding case is a workflow envelope around sub-processes: client identity capture, account registration, disclosures, document collection, review, external submission, and activation. It uses the generic state machine from Issue 1 with onboarding-specific statuses, guards, and transition rules.

### Scope

- Define statuses:
  - `draft` -- advisor is assembling the case
  - `pending_client_action` -- waiting for client to complete steps (e-sign, disclosures, identity verification)
  - `submitted` -- advisor has submitted for processing
  - `pending_internal_review` -- operations or compliance is reviewing
  - `pending_external_review` -- submitted to custodian or clearing firm; awaiting external response
  - `exception` -- a durable problem requires human intervention
  - `approved` -- all reviews passed
  - `rejected` -- case was denied
  - `activated` -- accounts are live and operational
- Define allowed transitions:
  - `draft` -> `pending_client_action`, `submitted`
  - `pending_client_action` -> `submitted`, `draft` (advisor recalls)
  - `submitted` -> `pending_internal_review`
  - `pending_internal_review` -> `pending_external_review`, `exception`, `approved`, `rejected`
  - `pending_external_review` -> `exception`, `approved`, `rejected`
  - `exception` -> `pending_internal_review`, `pending_external_review`, `rejected` (after resolution)
  - `approved` -> `activated`, `exception`
  - `rejected` -- terminal, no outbound transitions
  - `activated` -- terminal, no outbound transitions
- Postgres table `onboarding_cases` with columns: `id` (UUID), `tenant_id`, `household_id`, `advisor_id`, `status`, `status_changed_at`, `status_changed_by`, `correlation_id`, `metadata` (JSONB for flexible sub-process state), `created_at`, `updated_at`.
- Repository layer: `OnboardingCaseRepository` with `create`, `findById`, `findByHousehold`, `updateStatus`, `list` (with status and date filters).
- Service layer: `OnboardingCaseService` that uses the workflow engine for all transitions.
- Hono routes matching the API spec:
  - `POST /api/onboarding-cases` -- create draft
  - `GET /api/onboarding-cases/:id` -- read case with current status
  - `POST /api/onboarding-cases/:id/submit`
  - `POST /api/onboarding-cases/:id/request-client-action`
  - `POST /api/onboarding-cases/:id/approve`
  - `POST /api/onboarding-cases/:id/reject`
  - `GET /api/onboarding-cases` -- list with filters (status, household, advisor, date range)
- Emit Kafka events: `onboarding.case_created`, `onboarding.case_submitted`, `onboarding.case_approved`, `onboarding.case_rejected`, `onboarding.case_activated`, `onboarding.case_exceptioned`.
- Zod schemas for all request and response payloads.

### Acceptance Criteria

- An advisor can create a draft onboarding case linked to a household.
- All transitions follow the defined state machine; invalid transitions return `INVALID_WORKFLOW_STATE`.
- Every status change records the actor, timestamp, and reason in the transition history.
- The `exception` status can be entered from review or post-approval states and can be resolved back into the review flow.
- Terminal statuses (`rejected`, `activated`) cannot be transitioned out of.
- List endpoint supports filtering by status, household, advisor, and date range with cursor-based pagination.
- Kafka events are emitted for each status transition.
- All endpoints enforce tenant isolation and require appropriate permissions (`onboarding.read`, `onboarding.submit`, `onboarding.review`).

### Dependencies

- Issue 1 (generic state machine)
- Epic 2: Household and client records

---

## Issue 3: Transfer Case Model

### Title

Implement transfer case entity, statuses, and transitions

### Description

Define the `TransferCase` domain model for tracking money movement workflows across all supported rail types (ACH deposit, ACH withdrawal, ACAT full, ACAT partial, internal journal, wire in, wire out). Transfer cases are independent first-class workflows that can be created from onboarding or from active accounts. The platform persists the transfer intent before calling any external rail service and ingests status updates asynchronously.

### Scope

- Define statuses:
  - `draft` -- transfer details are being assembled
  - `submitted` -- intent persisted and queued for external submission
  - `pending_verification` -- awaiting client or bank verification (e.g., micro-deposit confirmation)
  - `pending_external_review` -- submitted to external rail; awaiting acceptance or review
  - `in_transit` -- funds are moving; external rail confirmed acceptance
  - `completed` -- funds settled successfully
  - `failed` -- rail or platform rejected the transfer
  - `cancelled` -- advisor or system cancelled before completion
  - `reversed` -- completed transfer was reversed (return, chargeback)
  - `exception` -- durable problem requiring manual intervention
- Define allowed transitions:
  - `draft` -> `submitted`, `cancelled`
  - `submitted` -> `pending_verification`, `pending_external_review`, `failed`, `cancelled`, `exception`
  - `pending_verification` -> `pending_external_review`, `failed`, `cancelled`, `exception`
  - `pending_external_review` -> `in_transit`, `failed`, `cancelled`, `exception`
  - `in_transit` -> `completed`, `failed`, `exception`
  - `completed` -> `reversed`
  - `failed` -> `exception` (for investigation)
  - `cancelled` -- terminal
  - `reversed` -- terminal
  - `exception` -> `submitted` (retry), `cancelled`, `failed`
- Postgres table `transfer_cases` with columns: `id` (UUID), `tenant_id`, `account_id`, `type` (enum: `ach_deposit`, `ach_withdrawal`, `acat_full`, `acat_partial`, `journal`, `wire_in`, `wire_out`), `amount`, `currency`, `status`, `status_changed_at`, `status_changed_by`, `correlation_id`, `external_reference_id`, `idempotency_key`, `metadata` (JSONB), `onboarding_case_id` (nullable FK), `created_at`, `updated_at`.
- Repository and service layers following the same module shape as onboarding.
- Hono routes:
  - `POST /api/transfers` -- create transfer intent
  - `GET /api/transfers/:id`
  - `POST /api/transfers/:id/submit`
  - `POST /api/transfers/:id/cancel`
  - `POST /api/transfers/:id/retry-sync` -- re-submit after exception resolution
  - `GET /api/transfers` -- list with filters
- Submission endpoint returns `202 Accepted` with case ID, current status, and polling URL.
- Kafka events: `transfer.created`, `transfer.submitted`, `transfer.in_transit`, `transfer.completed`, `transfer.failed`, `transfer.cancelled`, `transfer.reversed`, `transfer.exceptioned`.

### Acceptance Criteria

- A transfer can be created in `draft` and submitted, persisting the intent record before any external call.
- All transitions follow the defined state machine.
- The `type` field constrains which rail adapter will be invoked downstream (Epic 4 / Epic 7).
- `external_reference_id` is populated when the rail service acknowledges submission.
- The `idempotency_key` prevents duplicate transfer submissions; a second submit with the same key returns the existing record.
- Transfers can be optionally linked to an `onboarding_case_id` for funding-as-part-of-onboarding flows.
- Asynchronous status ingestion (from Kafka consumers) can advance the case through `in_transit`, `completed`, `failed`, and `reversed` without an HTTP request.
- Terminal statuses (`cancelled`, `reversed`) cannot be transitioned out of.
- All endpoints enforce tenant isolation and require `transfer.read` / `transfer.submit` / `transfer.cancel` permissions.

### Dependencies

- Issue 1 (generic state machine)
- Epic 2: Account records
- Epic 4 (integration framework, consumed downstream for rail submission)

---

## Issue 4: Approval Request System

### Title

Implement approval request model and policy evaluation

### Description

Certain high-impact actions require explicit approval from an authorized actor before the platform executes them. The approval system is a cross-cutting mechanism that can be invoked by any workflow when a policy gate determines that approval is required. It is not limited to a single domain; it covers money movement thresholds, trade classes, billing posting, and support impersonation.

### Scope

- Define `ApprovalPolicy` configuration (stored per tenant):
  - `policy_type` enum: `large_money_movement`, `restricted_trade_class`, `billing_posting`, `support_impersonation`
  - `conditions` (JSONB): threshold amounts, trade class lists, or other criteria
  - `required_role`: the role that can grant approval (e.g., `firm_admin`, `operations`)
  - `approval_count`: number of approvals required (default 1)
  - `expiry_hours`: how long the request remains valid
- Define `ApprovalRequest` entity:
  - `id` (UUID), `tenant_id`, `policy_type`, `resource_type`, `resource_id`, `requested_by`, `requested_at`, `status` (enum: `pending`, `approved`, `rejected`, `expired`, `cancelled`), `decided_by`, `decided_at`, `decision_reason`, `expiry_at`, `metadata` (JSONB with action context)
- Postgres tables: `approval_policies`, `approval_requests`.
- Service layer:
  - `ApprovalPolicyService.evaluate(action, context)` -- returns whether approval is required and, if so, which policy applies.
  - `ApprovalRequestService.create(policy, resource, requester)` -- creates a pending request and emits `approval.requested` event.
  - `ApprovalRequestService.decide(requestId, decision, actor)` -- approves or rejects; emits `approval.approved` or `approval.rejected`.
  - Expiry job: a scheduled worker that moves stale `pending` requests to `expired`.
- Hono routes:
  - `GET /api/approvals` -- list pending approvals for the current actor's role
  - `GET /api/approvals/:id`
  - `POST /api/approvals/:id/approve`
  - `POST /api/approvals/:id/reject`
- Integration hook: workflow transitions that trigger approval should move the parent case to a `pending_approval` or equivalent hold state and resume upon approval decision.

### Acceptance Criteria

- Approval policies can be configured per tenant with type-specific conditions.
- When a workflow action matches a policy (e.g., transfer amount exceeds threshold), an `ApprovalRequest` is created and the originating workflow is held.
- Only actors with the `required_role` can approve or reject a request.
- Approved requests allow the originating workflow to resume; rejected requests move the workflow to an appropriate state.
- Expired requests are automatically marked as `expired` by the scheduled job.
- Double-approval (submitting approve twice) is idempotent.
- Kafka events are emitted for `approval.requested`, `approval.approved`, `approval.rejected`, `approval.expired`.
- Approval requests are queryable by tenant, policy type, status, and resource.

### Dependencies

- Issue 1 (generic state machine, for the approval request's own lifecycle)
- Epic 1: Role and permission model (for evaluating `required_role`)

---

## Issue 5: Operational Task Assignment and Tracking

### Title

Implement operational task model with assignment and lifecycle tracking

### Description

Operations teams need a structured way to track discrete work items that arise from workflows: manual reviews, exception investigations, document follow-ups, reconciliation checks, and ad-hoc requests. Operational tasks are lightweight, assignable work units that can be linked to any case or resource. They are distinct from approval requests (which gate specific actions) and from the case state machines themselves.

### Scope

- Define `OperationalTask` entity:
  - `id` (UUID), `tenant_id`, `title`, `description`, `task_type` (enum: `manual_review`, `exception_investigation`, `document_followup`, `reconciliation_check`, `general`), `priority` (enum: `low`, `medium`, `high`, `urgent`), `status` (enum: `open`, `assigned`, `in_progress`, `completed`, `cancelled`), `assigned_to` (nullable user ID), `assigned_by`, `resource_type` (e.g., `onboarding_case`, `transfer_case`), `resource_id`, `due_at` (nullable), `completed_at`, `created_at`, `updated_at`.
- Postgres table: `operational_tasks`.
- Service layer:
  - Create task (manual or system-generated from workflow hooks).
  - Assign / reassign task.
  - Update status (open -> assigned -> in_progress -> completed | cancelled).
  - List tasks with filters: assignee, status, priority, resource, due date.
- Hono routes:
  - `POST /api/tasks`
  - `GET /api/tasks/:id`
  - `PATCH /api/tasks/:id` (assign, update priority, update status)
  - `GET /api/tasks` -- list with filters and cursor pagination
- Workflow integration: when a case enters `exception` or `pending_internal_review`, the system can auto-generate an operational task linked to that case.
- Kafka events: `task.created`, `task.assigned`, `task.completed`.

### Acceptance Criteria

- Tasks can be created manually by operations users or automatically by workflow transition hooks.
- Tasks can be assigned, reassigned, and completed with full actor tracking.
- Tasks are linked to a parent resource (case, account, etc.) for contextual navigation.
- List endpoint supports filtering by assignee, status, priority, resource type, and due date range.
- Overdue tasks (past `due_at` and not completed) are identifiable via query filter.
- All task mutations are tenant-scoped and permission-guarded.
- Kafka events are emitted on creation, assignment, and completion.

### Dependencies

- Issue 1 (for task status lifecycle)
- Epic 1: User records for assignee resolution

---

## Issue 6: Exception State Management

### Title

Implement durable exception state model for workflow cases

### Description

Exception states represent durable problems that require human investigation and resolution -- not transient errors or retryable failures. When a case enters `exception`, the system must capture structured information about the problem, track resolution attempts, and provide a clear path back into the normal workflow. This is a first-class operational concern for a custody-grade platform.

### Scope

- Define `CaseException` entity:
  - `id` (UUID), `tenant_id`, `case_type` (enum: `onboarding_case`, `transfer_case`), `case_id`, `exception_code` (machine-readable, e.g., `CUSTODIAN_REJECTION`, `KYC_MISMATCH`, `RAIL_TIMEOUT`, `DOCUMENT_INVALID`, `COMPLIANCE_HOLD`), `exception_category` (enum: `external_rejection`, `data_quality`, `compliance`, `system_failure`, `manual_hold`), `severity` (enum: `low`, `medium`, `high`, `critical`), `summary`, `detail` (JSONB -- structured payload from the source), `status` (enum: `open`, `investigating`, `resolved`, `escalated`), `raised_at`, `raised_by` (user or `system`), `resolved_at`, `resolved_by`, `resolution_summary`.
- Postgres table: `case_exceptions`.
- Service layer:
  - `ExceptionService.raise(caseType, caseId, code, detail)` -- creates exception record and transitions the parent case to `exception` status atomically.
  - `ExceptionService.resolve(exceptionId, resolution, actor)` -- marks resolved and allows the parent case to transition back to a review or retry state.
  - `ExceptionService.escalate(exceptionId, actor)` -- marks escalated and optionally creates a higher-priority operational task.
  - `ExceptionService.listByCriteria(filters)` -- query by tenant, case type, category, severity, status.
- Hono routes:
  - `POST /api/exceptions` -- raise manually (for operations staff)
  - `GET /api/exceptions/:id`
  - `POST /api/exceptions/:id/resolve`
  - `POST /api/exceptions/:id/escalate`
  - `GET /api/exceptions` -- list with filters
- Automatic exception creation: when a Kafka consumer receives a rejection or failure event from an external service, the consumer calls `ExceptionService.raise` with the structured detail.
- Kafka events: `exception.raised`, `exception.resolved`, `exception.escalated`.

### Acceptance Criteria

- Raising an exception transitions the parent case to `exception` status atomically (same DB transaction).
- Exception records capture machine-readable codes, severity, and structured detail from the source system.
- Resolving an exception allows the parent case to re-enter the normal workflow (e.g., back to `pending_internal_review` or `submitted` for retry).
- Escalation creates or upgrades a linked operational task.
- Multiple exceptions can exist for the same case (e.g., a case may encounter sequential problems).
- Exception history is preserved even after resolution; records are never deleted.
- List endpoint supports filtering by severity, category, status, case type, and date range.
- All operations are tenant-scoped and audited.

### Dependencies

- Issue 1 (generic state machine, for transitioning parent case)
- Issue 2 (onboarding case, as a consumer)
- Issue 3 (transfer case, as a consumer)
- Issue 5 (operational tasks, for escalation)

---

## Issue 7: Notes and Comments

### Title

Implement append-only notes and comments for cases and tasks

### Description

Advisors, operations staff, and system processes need to attach contextual notes to cases, exceptions, and tasks. Notes serve as the institutional memory for why decisions were made, what was tried during exception resolution, and what client communications occurred. Notes are strictly append-only; once written, they cannot be edited or deleted.

### Scope

- Define `CaseNote` entity:
  - `id` (UUID), `tenant_id`, `resource_type` (enum: `onboarding_case`, `transfer_case`, `case_exception`, `operational_task`, `approval_request`), `resource_id`, `author_id`, `author_type` (enum: `user`, `system`), `content` (text), `visibility` (enum: `internal`, `client_visible`), `created_at`.
- Postgres table: `case_notes` with no `updated_at` or `deleted_at` columns (immutable by design).
- Service layer:
  - `NoteService.append(resourceType, resourceId, content, author, visibility)` -- validates resource exists and tenant matches, then inserts.
  - `NoteService.listByResource(resourceType, resourceId)` -- returns notes in chronological order.
- Hono routes:
  - `POST /api/onboarding-cases/:id/notes`
  - `POST /api/transfers/:id/notes`
  - `POST /api/tasks/:id/notes`
  - `POST /api/exceptions/:id/notes`
  - `GET /api/{resource-type}/:id/notes` -- list notes for a resource
- System-generated notes: workflow transitions, exception raises, and approval decisions should auto-append system notes for traceability (e.g., "Status changed from submitted to pending_internal_review by user X").
- Zod validation: `content` must be non-empty, max 10,000 characters.

### Acceptance Criteria

- Notes can be appended to any supported resource type.
- Notes cannot be edited, updated, or deleted after creation.
- The database schema enforces immutability (no update/delete columns; application-level guards).
- Notes include the author identity and whether the author is a user or the system.
- System-generated notes are created automatically on status transitions and key workflow events.
- Notes can be filtered by visibility (`internal` vs `client_visible`) for client portal use cases.
- Notes are returned in chronological order.
- All note operations are tenant-scoped.

### Dependencies

- Issue 2 (onboarding cases as a resource)
- Issue 3 (transfer cases as a resource)
- Issue 5 (operational tasks as a resource)
- Issue 6 (exceptions as a resource)
- Epic 1: Actor identity for author tracking

---

## Issue 8: SLA Timers and Reminder System

### Title

Implement SLA timer tracking and reminder dispatch

### Description

Custody and operations workflows have service-level expectations: onboarding reviews should complete within a defined window, exception investigations should not go stale, approval requests should not sit indefinitely. The SLA system tracks time-in-status for cases and tasks, fires reminder events when deadlines approach, and marks items as breached when deadlines pass. This is infrastructure for operational visibility and accountability, not hard enforcement that blocks workflow progression.

### Scope

- Define `SlaTimer` entity:
  - `id` (UUID), `tenant_id`, `resource_type`, `resource_id`, `sla_type` (enum: `onboarding_review`, `exception_resolution`, `approval_decision`, `task_completion`, `transfer_processing`), `started_at`, `warning_at` (nullable), `deadline_at`, `status` (enum: `running`, `warned`, `breached`, `completed`, `cancelled`), `completed_at`.
- Define `SlaPolicy` configuration per tenant:
  - `sla_type`, `warning_hours`, `deadline_hours`.
- Postgres tables: `sla_policies`, `sla_timers`.
- Service layer:
  - `SlaTimerService.start(resourceType, resourceId, slaType)` -- calculates warning and deadline from policy, inserts timer.
  - `SlaTimerService.complete(resourceType, resourceId, slaType)` -- marks timer as completed.
  - `SlaTimerService.cancel(resourceType, resourceId, slaType)` -- marks timer as cancelled (e.g., case was cancelled).
- Scheduled worker (cron job or recurring Kafka-triggered job):
  - Scans for timers where `warning_at <= now()` and status is `running` -> set to `warned`, emit `sla.warning` event.
  - Scans for timers where `deadline_at <= now()` and status is `running` or `warned` -> set to `breached`, emit `sla.breached` event.
- Workflow integration: case transitions that enter reviewable or actionable states auto-start SLA timers; terminal transitions auto-complete them.
- Kafka events: `sla.started`, `sla.warning`, `sla.breached`, `sla.completed`.

### Acceptance Criteria

- SLA policies can be configured per tenant with warning and deadline thresholds.
- Timers are automatically started when a case enters a monitored status (e.g., `pending_internal_review` starts an `onboarding_review` timer).
- Timers are automatically completed or cancelled when the case exits the monitored status.
- The scheduled worker correctly identifies and transitions `warned` and `breached` timers.
- Warning and breach events are emitted to Kafka for consumption by notification and dashboard systems.
- Timers are queryable by status (especially `breached`) for operational dashboards.
- SLA tracking does not block workflow progression; it is observational and alerting.
- The worker is idempotent: running it multiple times does not duplicate events or corrupt timer state.

### Dependencies

- Issue 1 (state machine hooks to trigger timer start/complete)
- Issue 2, Issue 3 (cases whose transitions trigger timers)
- Issue 5 (tasks can have SLA timers)
- Kafka producer and scheduled job infrastructure

---

## Issue 9: Workflow History and Transition Logging

### Title

Implement immutable workflow transition history

### Description

Every status change across all case types must be recorded in an immutable, queryable history. This history serves three purposes: operational debugging (understanding how a case reached its current state), compliance audit (proving that approvals and reviews happened in the correct order by authorized actors), and analytics (measuring throughput, dwell time, and bottleneck identification). The transition log is written in the same transaction as the status update, making it the authoritative record of what happened.

### Scope

- Define `WorkflowTransition` entity:
  - `id` (UUID), `tenant_id`, `case_type` (enum: `onboarding_case`, `transfer_case`, `approval_request`, `operational_task`), `case_id`, `from_status`, `to_status`, `actor_id`, `actor_type` (enum: `user`, `system`, `external_service`), `reason` (nullable text), `metadata` (JSONB -- guard results, policy references, external response summaries), `correlation_id`, `idempotency_key` (nullable), `transitioned_at` (timestamptz).
- Postgres table: `workflow_transitions` -- append-only, no updates or deletes. Indexed on `(tenant_id, case_type, case_id)` and `(tenant_id, transitioned_at)`.
- The generic workflow engine (Issue 1) writes the transition record in the same DB transaction as the status update.
- Query layer:
  - `TransitionHistoryService.getByCaseId(caseType, caseId)` -- full ordered history for a case.
  - `TransitionHistoryService.query(filters)` -- search by tenant, case type, status, actor, date range.
  - Dwell time calculation: time spent in each status derived from consecutive transition timestamps.
- Hono routes:
  - `GET /api/onboarding-cases/:id/history`
  - `GET /api/transfers/:id/history`
  - `GET /api/approvals/:id/history`
  - `GET /api/tasks/:id/history`

### Acceptance Criteria

- Every status transition across all case types produces exactly one `WorkflowTransition` record.
- Transition records are written atomically with the status update (same Postgres transaction).
- Transition records are immutable: the table has no UPDATE or DELETE application paths.
- Each record captures the actor, timestamp, from/to statuses, reason, and correlation ID.
- History can be retrieved per case in chronological order.
- History can be queried across cases by filters (useful for operations dashboards and compliance reports).
- Dwell time in each status can be computed from the transition log.

### Dependencies

- Issue 1 (generic state machine -- this is the persistence layer for transition records)

---

## Issue 10: Idempotent Workflow Re-entry and Retry Handling

### Title

Implement idempotent re-entry and safe retry semantics for workflow transitions

### Description

In a distributed system with Kafka consumers, async status updates, and potential network failures, the same transition command may arrive more than once. The platform must handle duplicate transition requests, consumer replays, and manual retry attempts safely. A transition that has already been applied must not create duplicate history records, emit duplicate events, or corrupt case state. Additionally, cases in `exception` status must support structured re-entry into the workflow without data loss.

### Scope

- Idempotency key handling in the workflow engine:
  - Every transition command accepts an optional `idempotency_key`.
  - Before executing a transition, check the `workflow_transitions` table for an existing record with the same `idempotency_key` and `case_id`.
  - If found: return the existing transition result without re-executing side effects. This is a successful no-op, not an error.
  - Idempotency keys have a configurable TTL for cleanup of very old entries (default: 90 days).
- Kafka consumer replay safety:
  - Consumers that advance transfer or onboarding case state must include the upstream event ID as the idempotency key.
  - If the case is already at or past the target status, the consumer acknowledges the message without mutation (status-monotonicity check).
- Manual retry from exception:
  - The `exception` -> previous-status transitions must preserve all case data and metadata.
  - The retry action creates a new transition record with the retry actor and reason.
  - External re-submission (e.g., re-sending to a transfer rail) uses the same `external_reference_id` and idempotency key to avoid duplicate external side effects.
- Concurrent safety:
  - Redis advisory locks (from Issue 1) prevent race conditions during retries.
  - If a lock cannot be acquired within a timeout, the caller receives a `409 Conflict` response.
- Error categorization:
  - Distinguish between retryable failures (transient: network timeout, temporary upstream unavailability) and non-retryable failures (durable: validation rejection, compliance block).
  - Retryable failures may be auto-retried by workers with exponential backoff.
  - Non-retryable failures move the case to `exception` for human resolution.

### Acceptance Criteria

- Submitting the same transition with the same idempotency key twice returns the same result without side effects.
- Kafka consumers replaying already-processed events do not create duplicate transitions or emit duplicate domain events.
- A case in `exception` can be retried back into the workflow with a new transition record and preserved case data.
- External re-submissions use the original idempotency key to prevent duplicate rail-side actions.
- Concurrent retry attempts on the same case are serialized; losers receive `409 Conflict`.
- Retryable vs non-retryable failures are categorized, and auto-retry uses exponential backoff with a maximum attempt count.
- All retry and re-entry actions are fully logged in the transition history with actor, reason, and correlation ID.
- Integration tests cover: duplicate HTTP submission, duplicate Kafka event, exception-to-retry flow, concurrent retry race condition.

### Dependencies

- Issue 1 (generic state machine with locking and idempotency key column)
- Issue 6 (exception state management for the re-entry flow)
- Issue 9 (transition history for logging retries)
- Epic 4: Kafka consumer infrastructure for replay-safe ingestion

---

## Summary

| Issue | Title | Priority |
|-------|-------|----------|
| 1 | Generic Case and Workflow State Machine Infrastructure | P0 -- Foundation |
| 2 | Onboarding Case Model | P0 -- Core domain |
| 3 | Transfer Case Model | P0 -- Core domain |
| 4 | Approval Request System | P0 -- Control gate |
| 5 | Operational Task Assignment and Tracking | P1 -- Operations |
| 6 | Exception State Management | P0 -- Custody-grade |
| 7 | Notes and Comments | P1 -- Operational memory |
| 8 | SLA Timers and Reminder System | P1 -- Accountability |
| 9 | Workflow History and Transition Logging | P0 -- Audit and compliance |
| 10 | Idempotent Workflow Re-entry and Retry Handling | P0 -- Distributed safety |

### Recommended Implementation Order

1. Issue 1 (state machine engine) and Issue 9 (transition history -- they are built together)
2. Issue 2 (onboarding case) and Issue 3 (transfer case) in parallel
3. Issue 6 (exception management) -- needed before cases can be fully exercised
4. Issue 10 (idempotency and retry) -- hardens the engine before downstream consumers exist
5. Issue 4 (approval system) -- cross-cutting gate
6. Issue 7 (notes) -- lightweight, high-value addition
7. Issue 5 (operational tasks) and Issue 8 (SLA timers) in parallel
