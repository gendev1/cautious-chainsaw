# Epic 9: Orders, OMS/EMS Integration, and Trade Status

## Goal

Build trading workflows using platform-owned order intents and upstream OMS/EMS projections. The platform is NOT the OMS. It creates `OrderIntent` records (platform-owned), submits them to the external OMS synchronously, and then ingests order state and execution fills asynchronously via Kafka as `OrderProjection` and `ExecutionProjection` records.

## Submission Flow (per spec)

1. Validate actor permission (`order.submit` capability)
2. Validate account and policy constraints (restrictions, cash/position checks, approval policies)
3. Create `OrderIntent` (platform-owned, persisted before any external call)
4. Submit to OMS via synchronous client
5. Persist upstream ID and accepted status on the `OrderIntent`
6. Await subsequent fill/reject/cancel events asynchronously via Kafka

## Dependencies

- Epic 1: Tenant, Identity, and Access Control (permission model, actor resolution)
- Epic 2: Client, Household, and Account Registry (account records, restrictions)
- Epic 3: Workflow and Case Management (approval requests)
- Epic 4: External Service Integration Framework (OMS client adapter, Kafka consumers, idempotency keys, correlation IDs, retry policies, dead-letter handling)
- Epic 8: Advisor Portal Experience (advisor-facing order workspace)

---

## Issue 1: OrderIntent Model and Creation

### Description

Define and implement the `OrderIntent` entity as the platform-owned record that captures the advisor's or system's intent to place an order. This record is persisted locally before any external OMS call is made. It serves as the platform's durable record of what was requested, regardless of what the OMS ultimately does.

### Scope

- Postgres table `order_intents` with columns: `id` (UUID), `tenant_id`, `account_id`, `symbol`, `side` (buy/sell), `quantity`, `order_type` (market/limit/stop/stop_limit), `limit_price` (nullable), `stop_price` (nullable), `time_in_force` (day/gtc/ioc/fok), `idempotency_key`, `source` (manual/rebalance/model_release), `source_id` (nullable, e.g. rebalance_proposal_id), `upstream_order_id` (nullable, populated after OMS accepts), `status` (draft/pending_validation/validated/submitted/accepted/rejected/cancel_requested/cancelled/failed), `submitted_by` (actor user ID), `submitted_at`, `created_at`, `updated_at`
- Zod schemas for creation input validation (`OrderIntentCreateSchema`)
- Repository layer: `OrderIntentRepository` with `create`, `findById`, `findByIdempotencyKey`, `updateStatus`, `setUpstreamOrderId`
- Service layer: `OrderIntentService.create()` that persists the intent in `draft` status
- Domain types in `modules/orders/types.ts`
- Database migration for the `order_intents` table
- Unique constraint on `(tenant_id, idempotency_key)` to support duplicate prevention at the DB level

### Acceptance Criteria

- [ ] `order_intents` table exists with all specified columns and appropriate indexes (tenant_id, account_id, status, idempotency_key)
- [ ] Zod schema validates all required fields and rejects invalid side/type/time_in_force values
- [ ] `OrderIntentRepository.create()` persists a new intent and returns the full record
- [ ] `OrderIntentRepository.findById()` is tenant-scoped
- [ ] Unique constraint on `(tenant_id, idempotency_key)` is enforced at the database level
- [ ] `status` column uses a Postgres enum or check constraint matching the defined state set
- [ ] Unit tests cover creation, retrieval, and constraint violations

### Dependencies

- Epic 1 (tenant_id, actor model)
- Epic 2 (account_id foreign key)

---

## Issue 2: Pre-Trade Validation

### Description

Implement the validation layer that runs before an `OrderIntent` is submitted to the OMS. This is step 2 of the submission flow. Validation must check account-level restrictions, basic cash and position availability, actor permissions, and policy constraints. Validation failures must block submission and record the reason on the intent.

### Scope

- `OrderValidationService` with a `validate(intent, actor, account)` method that returns a structured result (pass/fail with reasons)
- Account restriction checks: account must be in `active` status, not restricted or closed, not frozen for trading
- Cash availability check for buy orders: available cash >= estimated cost (quantity * last price or limit price). This is a soft check -- the OMS is authoritative, but the platform should catch obvious failures early
- Position availability check for sell orders: current position quantity >= order quantity (based on local position projection if available)
- Duplicate prevention: reject if an identical `OrderIntent` (same account, symbol, side, quantity, type) is already in `submitted` or `accepted` status within a configurable time window
- Permission check: actor must have `order.submit` capability (delegated to permission evaluator from Epic 1)
- Validation result is persisted as `validation_result` (jsonb) on the `OrderIntent` and status transitions to `validated` or `rejected`
- Emit `order_intent.validation_failed` event on rejection

### Acceptance Criteria

- [ ] Orders against restricted, closed, or frozen accounts are rejected with a clear error code
- [ ] Buy orders that exceed available cash projection are rejected with `INSUFFICIENT_CASH` reason
- [ ] Sell orders that exceed position projection are rejected with `INSUFFICIENT_POSITION` reason
- [ ] Duplicate orders (same account/symbol/side/quantity/type within time window) are rejected with `DUPLICATE_ORDER` reason
- [ ] Actors without `order.submit` capability receive `FORBIDDEN` before validation runs
- [ ] Validation reasons are persisted on the `OrderIntent` record
- [ ] Validation is a discrete step that can be invoked independently of submission (supports dry-run / pre-check)
- [ ] Unit tests cover each validation rule independently and in combination

### Dependencies

- Issue 1 (OrderIntent model)
- Epic 1 (permission evaluator)
- Epic 2 (account status, restrictions)
- Epic 11 (cash/position projections -- can stub initially)

---

## Issue 3: OMS Submission Flow

### Description

Implement the end-to-end synchronous submission flow that takes a validated `OrderIntent`, submits it to the external OMS, and persists the upstream order ID. This is steps 3-5 of the submission flow. The OMS call is synchronous; subsequent state transitions (fills, rejects, cancels) arrive asynchronously.

### Scope

- `POST /api/order-intents` route that accepts order parameters and idempotency key
- `POST /api/order-intents/:id/submit` route that triggers submission of a created intent
- Alternatively, a single `POST /api/order-intents` that creates and submits in one request (configurable)
- `OrderIntentService.submit(intentId, actor)` orchestration:
  1. Load the `OrderIntent` and verify it is in `draft` or `validated` status
  2. Run pre-trade validation (Issue 2)
  3. Transition status to `submitted`
  4. Call `OmsClient.submitOrder(intent)` synchronously
  5. On OMS acceptance: persist `upstream_order_id`, transition to `accepted`, emit `order_intent.accepted`
  6. On OMS rejection: transition to `rejected`, persist rejection reason, emit `order_intent.rejected`
  7. On OMS client error (timeout, network failure): transition to `failed`, persist error details, emit `order_intent.submission_failed`
- `OmsClient` adapter in `external/oms/` with typed request/response, correlation ID propagation, timeout configuration, and retry policy (per Epic 4 framework)
- HTTP response: `202 Accepted` with the `OrderIntent` record including current status and polling URL
- Audit event emission for `order.submitted` (per spec section 12)
- Correlation ID and request ID propagated to OMS call

### Acceptance Criteria

- [ ] `POST /api/order-intents` creates an intent and optionally submits it, returning `202 Accepted`
- [ ] `POST /api/order-intents/:id/submit` submits an existing draft/validated intent
- [ ] Submission to OMS uses the `OmsClient` adapter with correlation ID, tenant ID, and idempotency key
- [ ] On OMS acceptance, `upstream_order_id` is persisted and status is `accepted`
- [ ] On OMS synchronous rejection, status is `rejected` with reason persisted
- [ ] On OMS client error (timeout/network), status is `failed` with error details; no silent data loss
- [ ] Submitting an intent that is not in `draft` or `validated` status returns `INVALID_WORKFLOW_STATE`
- [ ] Audit event `order.submitted` is emitted with actor, tenant, account, and intent details
- [ ] Response includes `OrderIntent` record with current status and a polling URL for async updates
- [ ] Integration test covers the happy path with a mocked OMS client

### Dependencies

- Issue 1 (OrderIntent model)
- Issue 2 (pre-trade validation)
- Epic 4 (OmsClient adapter pattern, retry policies)
- Epic 1 (authentication, actor resolution, audit emission)

---

## Issue 4: Order Cancel Flow

### Description

Implement the order cancellation flow. The platform receives a cancel request from the advisor, forwards it to the OMS synchronously, and then awaits async confirmation. Cancellation is a request, not a guarantee -- the OMS may reject the cancel if the order is already filled or in a terminal state.

### Scope

- `POST /api/order-intents/:id/cancel` route
- `OrderIntentService.requestCancel(intentId, actor)` orchestration:
  1. Load the `OrderIntent` and verify it is in a cancellable state (`accepted` or `partially_filled` on the projection)
  2. Verify actor has `order.submit` capability (same permission governs cancel)
  3. Transition intent status to `cancel_requested`
  4. Call `OmsClient.cancelOrder(upstreamOrderId)` synchronously
  5. On OMS acknowledgment: remain in `cancel_requested`, await async cancel confirmation via Kafka
  6. On OMS rejection (e.g., already filled): transition back to previous state, return error
  7. On OMS client error: persist error, keep `cancel_requested` status for retry
- Response: `202 Accepted` with current intent status
- Audit event emission for `order.cancel_requested`
- Final `cancelled` status is set by the Kafka consumer (Issue 5), not by this flow

### Acceptance Criteria

- [ ] `POST /api/order-intents/:id/cancel` returns `202 Accepted` on successful cancel request
- [ ] Cancel is rejected with `INVALID_WORKFLOW_STATE` if the intent is in a terminal state (filled, rejected, cancelled, failed)
- [ ] Cancel is rejected if the actor lacks `order.submit` capability
- [ ] `OmsClient.cancelOrder()` is called with the `upstream_order_id`
- [ ] Intent status transitions to `cancel_requested` before the OMS call
- [ ] If OMS rejects the cancel (already filled), the intent status reverts and the response includes the rejection reason
- [ ] Audit event `order.cancel_requested` is emitted
- [ ] Final `cancelled` status is NOT set by this endpoint -- it is set by the async Kafka consumer
- [ ] Unit tests cover cancellable states, non-cancellable states, and OMS error scenarios

### Dependencies

- Issue 1 (OrderIntent model, status transitions)
- Issue 3 (OmsClient adapter, upstream_order_id)
- Epic 1 (permission check)

---

## Issue 5: OrderProjection Sync via Kafka Consumer

### Description

Implement a Kafka consumer that ingests order state change events from the external OMS/EMS and writes them as `OrderProjection` records in the platform database. These projections are NOT platform-owned truth -- they are cached views of the OMS's authoritative state, tagged with source metadata and sync timestamps.

### Scope

- Postgres table `order_projections` with columns: `id` (UUID), `tenant_id`, `order_intent_id` (nullable FK, linked when platform initiated the order), `upstream_order_id`, `upstream_source` (e.g., "oms-v1"), `account_id`, `symbol`, `side`, `quantity`, `order_type`, `limit_price`, `stop_price`, `time_in_force`, `status` (accepted/rejected/partially_filled/filled/cancelled/expired), `rejection_reason` (nullable), `filled_quantity`, `average_fill_price`, `last_synced_at`, `upstream_event_timestamp`, `created_at`, `updated_at`
- Database migration for the `order_projections` table
- Kafka consumer in `workers/src/consumers/order-projection-consumer.ts` subscribed to the OMS order state topic (e.g., `oms.orders.state`)
- Consumer logic:
  1. Deserialize and validate event payload with Zod
  2. Resolve `tenant_id` from event metadata
  3. Upsert `OrderProjection` by `upstream_order_id` -- insert on first event, update on subsequent
  4. If `order_intent_id` can be resolved (via `upstream_order_id` lookup on `order_intents`), link the projection and update the intent's status to match
  5. Emit platform domain events: `order.accepted`, `order.rejected`, `order.partially_filled`, `order.filled`, `order.cancelled`
- Consumer must be idempotent: processing the same event twice produces the same result
- Consumer must be replay-safe: older events do not overwrite newer state (compare `upstream_event_timestamp`)
- Dead-letter handling for malformed or unprocessable events (per Epic 4 DLQ framework)

### Acceptance Criteria

- [ ] `order_projections` table exists with all specified columns and indexes on `upstream_order_id`, `order_intent_id`, `tenant_id`, `account_id`
- [ ] Kafka consumer deserializes OMS order state events and upserts projections
- [ ] Projections are tagged with `upstream_source` and `last_synced_at`
- [ ] When an `OrderIntent` exists for the upstream order, the intent status is updated to reflect OMS state
- [ ] Consumer is idempotent: duplicate events do not create duplicate projections or corrupt state
- [ ] Consumer is replay-safe: an older event does not overwrite a newer projection state
- [ ] Platform domain events are emitted on each state transition
- [ ] Malformed events are routed to the dead-letter topic with error metadata
- [ ] Consumer runs in the worker process, not in the HTTP server runtime
- [ ] Integration test covers the full consumer flow with a mocked Kafka message

### Dependencies

- Issue 1 (OrderIntent model, status field for back-linking)
- Epic 4 (Kafka consumer framework, DLQ, idempotency)
- Epic 1 (tenant resolution from event metadata)

---

## Issue 6: ExecutionProjection Sync via Kafka Consumer

### Description

Implement a Kafka consumer that ingests execution fill events from the external OMS/EMS and writes them as `ExecutionProjection` records. Each execution represents a single fill (or partial fill) against an order. These are projections of the OMS/EMS's authoritative execution data.

### Scope

- Postgres table `execution_projections` with columns: `id` (UUID), `tenant_id`, `order_projection_id` (FK to `order_projections`), `upstream_execution_id`, `upstream_source`, `upstream_order_id`, `account_id`, `symbol`, `side`, `fill_quantity`, `fill_price`, `gross_amount`, `commission` (nullable), `fees` (nullable), `net_amount`, `executed_at` (upstream execution timestamp), `settlement_date` (nullable), `settlement_status` (nullable: pending/settled/failed), `last_synced_at`, `created_at`
- Database migration for the `execution_projections` table
- Kafka consumer in `workers/src/consumers/execution-projection-consumer.ts` subscribed to the OMS execution topic (e.g., `oms.executions`)
- Consumer logic:
  1. Deserialize and validate event payload with Zod
  2. Resolve `tenant_id` from event metadata
  3. Look up or create the associated `OrderProjection` by `upstream_order_id`
  4. Insert `ExecutionProjection` (executions are append-only -- fills are not updated, only new fills arrive)
  5. Update the parent `OrderProjection`'s `filled_quantity` and `average_fill_price`
  6. Emit platform domain event: `execution.fill_received`
- Idempotency: deduplicate on `upstream_execution_id` to prevent double-counting fills
- Dead-letter handling for malformed events

### Acceptance Criteria

- [ ] `execution_projections` table exists with all specified columns and indexes on `upstream_execution_id`, `order_projection_id`, `tenant_id`, `account_id`
- [ ] Kafka consumer ingests fill events and inserts execution projection records
- [ ] Each execution is linked to its parent `OrderProjection`
- [ ] Duplicate fills (same `upstream_execution_id`) are ignored, not double-inserted
- [ ] Parent `OrderProjection` is updated with cumulative `filled_quantity` and `average_fill_price`
- [ ] Executions are append-only: no updates to existing execution records
- [ ] Projections are tagged with `upstream_source` and `last_synced_at`
- [ ] Platform event `execution.fill_received` is emitted per fill
- [ ] Malformed events are routed to the dead-letter topic
- [ ] Consumer runs in the worker process
- [ ] Integration test covers fill ingestion and parent order update

### Dependencies

- Issue 5 (OrderProjection model and consumer)
- Epic 4 (Kafka consumer framework, DLQ)

---

## Issue 7: Order and Execution Query APIs

### Description

Implement read APIs for order intents, order projections, and execution projections. These endpoints serve the advisor portal and any downstream consumers that need to display order and execution status.

### Scope

- `GET /api/order-intents/:id` -- returns the platform-owned intent record with current status, upstream ID, and validation result
- `GET /api/order-intents` -- list intents for an account or across the tenant, with filters: `account_id`, `status`, `symbol`, `date_range`, `source`; paginated
- `GET /api/orders/:id` -- returns an `OrderProjection` by ID, including linked execution count and fill summary
- `GET /api/orders` -- list order projections with filters: `account_id`, `status`, `symbol`, `date_range`; paginated
- `GET /api/executions/:id` -- returns an `ExecutionProjection` by ID
- `GET /api/executions` -- list executions with filters: `order_id`, `account_id`, `symbol`, `date_range`; paginated
- All endpoints are tenant-scoped (middleware enforces `tenant_id`)
- All endpoints require `order.read` or equivalent read capability (or fall back to `order.submit` for actors who can trade)
- Response presenters: `OrderIntentPresenter`, `OrderProjectionPresenter`, `ExecutionProjectionPresenter` -- database models do not leak directly into HTTP responses
- Include `last_synced_at` and `upstream_source` on projection responses so consumers understand data freshness
- Zod schemas for query parameter validation

### Acceptance Criteria

- [ ] `GET /api/order-intents/:id` returns the intent with status, upstream ID, and validation metadata
- [ ] `GET /api/order-intents` supports filtering by account, status, symbol, source, and date range with pagination
- [ ] `GET /api/orders/:id` returns the order projection with fill summary
- [ ] `GET /api/orders` supports filtering by account, status, symbol, and date range with pagination
- [ ] `GET /api/executions/:id` returns the execution projection
- [ ] `GET /api/executions` supports filtering by order, account, symbol, and date range with pagination
- [ ] All responses use presenters; no raw database rows in HTTP responses
- [ ] Projection responses include `last_synced_at` and `upstream_source` for freshness awareness
- [ ] All endpoints are tenant-scoped and require appropriate read permission
- [ ] 404 is returned for resources outside the actor's tenant
- [ ] Query parameter schemas reject invalid filter values

### Dependencies

- Issue 1 (OrderIntent model)
- Issue 5 (OrderProjection model)
- Issue 6 (ExecutionProjection model)
- Epic 1 (authentication, tenant scoping, permission enforcement)

---

## Issue 8: Settlement State Ingestion

### Description

Ingest settlement status updates from the trading stack where available. Settlement data may arrive as part of execution events or as separate settlement lifecycle events. The platform stores settlement state on execution projections and optionally as standalone settlement records for downstream cash/ledger projections.

### Scope

- Extend `execution_projections` table: ensure `settlement_date` and `settlement_status` columns support updates (these may arrive after the initial fill event)
- If the trading stack emits separate settlement events, implement a Kafka consumer in `workers/src/consumers/settlement-consumer.ts` subscribed to the settlement topic (e.g., `oms.settlements`)
- Consumer logic:
  1. Deserialize and validate settlement event
  2. Look up `ExecutionProjection` by `upstream_execution_id`
  3. Update `settlement_date` and `settlement_status` (pending/settled/failed_to_settle)
  4. Emit platform event: `execution.settlement_updated`
- If settlement data arrives embedded in execution fill events, handle it in the execution consumer (Issue 6) instead
- Optional: `settlement_events` table for a full settlement audit trail if the trading stack provides detailed settlement lifecycle data
- Graceful handling when settlement data is not available: fields remain null, no errors

### Acceptance Criteria

- [ ] `settlement_date` and `settlement_status` on `execution_projections` can be updated after initial fill insertion
- [ ] If a separate settlement Kafka topic exists, the consumer processes events and updates execution projections
- [ ] Settlement status transitions are tracked: `pending` -> `settled` or `pending` -> `failed_to_settle`
- [ ] Platform event `execution.settlement_updated` is emitted on status change
- [ ] Consumer is idempotent and replay-safe
- [ ] When settlement data is unavailable, execution projections function normally with null settlement fields
- [ ] Settlement updates do not overwrite or corrupt fill data on the execution projection
- [ ] Dead-letter handling for malformed settlement events

### Dependencies

- Issue 6 (ExecutionProjection model)
- Epic 4 (Kafka consumer framework)
- Epic 11 (cash/ledger projections will consume settlement state downstream)

---

## Issue 9: Idempotency and Duplicate Submission Prevention

### Description

Implement robust idempotency for order submission to prevent duplicate orders from reaching the OMS. This covers both the HTTP idempotency key mechanism and the business-level duplicate detection logic.

### Scope

- HTTP-level idempotency:
  - `POST /api/order-intents` requires an `Idempotency-Key` header
  - Redis-backed idempotency store: on first request, store `(tenant_id, idempotency_key) -> response` with a configurable TTL (e.g., 24 hours)
  - On duplicate request with the same key: return the stored response without re-executing the operation
  - On conflicting request (same key, different payload): return `409 IDEMPOTENCY_CONFLICT`
- Database-level idempotency:
  - Unique constraint on `(tenant_id, idempotency_key)` in `order_intents` table (from Issue 1)
  - Catch constraint violations and map to `IDEMPOTENCY_CONFLICT` error
- Business-level duplicate detection (from Issue 2):
  - Configurable time window (e.g., 5 minutes) within which identical orders (same account, symbol, side, quantity, type) are flagged as potential duplicates
  - Return `DUPLICATE_ORDER` warning or rejection depending on configuration
  - Allow explicit override via a `force` flag for intentional duplicate submissions
- Idempotency key propagation to OMS: the platform's idempotency key (or a derived key) is included in the OMS submission to prevent double-submission at the OMS level as well

### Acceptance Criteria

- [ ] `POST /api/order-intents` requires an `Idempotency-Key` header; requests without it receive `400`
- [ ] Repeated requests with the same idempotency key return the original response without re-executing
- [ ] Requests with the same key but different payload return `409 IDEMPOTENCY_CONFLICT`
- [ ] Redis idempotency entries expire after the configured TTL
- [ ] Database unique constraint on `(tenant_id, idempotency_key)` catches any race conditions the Redis check misses
- [ ] Business-level duplicate detection flags identical in-flight orders within the time window
- [ ] The `force` flag allows intentional duplicate submissions when explicitly provided
- [ ] Idempotency key is propagated to the OMS client call
- [ ] Unit tests cover: first request, duplicate request, conflicting request, expired key, business duplicate, forced duplicate

### Dependencies

- Issue 1 (OrderIntent model, unique constraint)
- Issue 2 (business-level duplicate detection)
- Issue 3 (OMS submission flow)
- Epic 4 (Redis-backed idempotency store pattern)

---

## Issue 10: Permission Enforcement for Order Submission

### Description

Enforce the `order.submit` capability at the route level and within the order service. Only actors with the `order.submit` permission may create, submit, or cancel order intents. Read access to orders and executions requires `order.read` or an equivalent capability.

### Scope

- Route-level permission guard on `POST /api/order-intents` requiring `order.submit`
- Route-level permission guard on `POST /api/order-intents/:id/submit` requiring `order.submit`
- Route-level permission guard on `POST /api/order-intents/:id/cancel` requiring `order.submit`
- Route-level permission guard on `GET /api/order-intents/**`, `GET /api/orders/**`, `GET /api/executions/**` requiring `order.read` (or `order.submit` implying read)
- Permission checks use the permission evaluator from Epic 1 with the resolved actor and tenant context
- Minimum roles that should have `order.submit`: `trader`, `advisor` (configurable per firm)
- Minimum roles that should have `order.read`: `trader`, `advisor`, `operations`, `viewer`
- Service-level defensive check: `OrderIntentService` methods verify permission even if the route guard is bypassed (defense in depth)
- Audit event includes the actor and permission context for every order submission and cancel

### Acceptance Criteria

- [ ] Actors without `order.submit` receive `403 FORBIDDEN` on create, submit, and cancel endpoints
- [ ] Actors without `order.read` receive `403 FORBIDDEN` on query endpoints
- [ ] `trader` and `advisor` roles have `order.submit` by default
- [ ] `operations` and `viewer` roles have `order.read` but not `order.submit` by default
- [ ] Service-level permission checks exist as defense-in-depth alongside route guards
- [ ] Audit events for order submission and cancel include the actor ID and resolved permissions
- [ ] Permission enforcement is tenant-scoped: an advisor in Tenant A cannot submit orders in Tenant B
- [ ] Unit tests cover permitted and denied access for each role and endpoint

### Dependencies

- Epic 1 (permission evaluator, role model, capability definitions)
- Issue 3 (submission route)
- Issue 4 (cancel route)
- Issue 7 (query routes)

---

## Issue 11: Approval Policies for Certain Trade Classes

### Description

Implement approval policies that require a second actor to approve certain order intents before they are submitted to the OMS. Per the spec, "certain trade classes" require approval. This integrates with the `ApprovalRequest` workflow resource from Epic 3.

### Scope

- Approval policy configuration: define which trade characteristics require approval. Initial policy dimensions:
  - Order value exceeding a configurable threshold (e.g., > $100,000 notional)
  - Specific asset classes or security types (e.g., options, alternatives, fixed income)
  - Specific account types (e.g., ERISA accounts, trust accounts)
  - Firm-level toggle to enable/disable trade approval policies
- Policy evaluation: `ApprovalPolicyService.evaluateOrderIntent(intent)` returns whether approval is required and which policy triggered it
- When approval is required:
  1. `OrderIntent` status transitions to `pending_approval` (new status added to the enum)
  2. An `ApprovalRequest` record is created (per Epic 3 model) linked to the `OrderIntent`
  3. The intent is NOT submitted to the OMS
  4. A notification or task is created for the approver
- Approval flow:
  - `POST /api/order-intents/:id/approve` -- approver with `order.approve` capability approves the intent, which then proceeds through the normal submission flow (Issue 3)
  - `POST /api/order-intents/:id/reject` -- approver rejects the intent, status transitions to `rejected`
- `order.approve` capability: separate from `order.submit` to enforce segregation of duties
- Audit events: `order_intent.approval_required`, `order_intent.approved`, `order_intent.approval_rejected`

### Acceptance Criteria

- [ ] Approval policies can be configured per firm with trade value thresholds and asset class rules
- [ ] `OrderIntent` creation evaluates approval policies before proceeding to OMS submission
- [ ] When approval is required, intent transitions to `pending_approval` and an `ApprovalRequest` is created
- [ ] Intents in `pending_approval` status are NOT submitted to the OMS
- [ ] `POST /api/order-intents/:id/approve` requires `order.approve` capability and triggers the submission flow
- [ ] `POST /api/order-intents/:id/reject` requires `order.approve` capability and transitions to `rejected`
- [ ] `order.approve` is a separate capability from `order.submit` (segregation of duties)
- [ ] Audit events are emitted for approval required, approved, and rejected transitions
- [ ] Policy evaluation is a discrete, testable service -- not embedded in route handlers
- [ ] Unit tests cover: policy triggering, approval flow, rejection flow, policy bypass when not configured

### Dependencies

- Issue 1 (OrderIntent model, status enum extension)
- Issue 3 (submission flow -- approval gates into this flow)
- Epic 1 (permission model, `order.approve` capability)
- Epic 3 (ApprovalRequest model and workflow)
