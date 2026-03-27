# Epic 7: Money Movement and Transfer Operations

## Goal

Handle transfer intent creation, submission, lifecycle tracking, and exception handling across all funding rails. The platform persists every transfer intent locally before calling any external rail, ingests status updates asynchronously, and maintains a durable state machine that supports reversals, returns, retries, and reconciliation with the cash ledger.

## Dependencies

- Epic 1: Tenant, Identity, and Access Control
- Epic 2: Client, Household, and Account Registry
- Epic 3: Workflow and Case Management
- Epic 4: External Service Integration Framework
- Epic 5: Document Vault and Records Management
- Epic 6: Onboarding and Account Opening

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/transfers` | Create a transfer intent in `draft` status |
| GET | `/api/transfers/:id` | Retrieve transfer detail including current status and history |
| POST | `/api/transfers/:id/submit` | Submit the transfer to the appropriate external rail |
| POST | `/api/transfers/:id/cancel` | Request cancellation of an in-flight or draft transfer |
| POST | `/api/transfers/:id/retry-sync` | Re-attempt synchronization with the external rail after a transient failure |

## Transfer Types

- ACH deposit
- ACH withdrawal
- ACAT full transfer
- ACAT partial transfer
- Wire in
- Wire out
- Internal journal

## Transfer Lifecycle State Machine

```
draft
  --> submitted
        --> pending_verification
        --> pending_external_review
        --> in_transit
              --> completed
              --> failed
              --> reversed
        --> failed
        --> exception
  --> cancelled

pending_verification
  --> in_transit
  --> failed
  --> cancelled
  --> exception

pending_external_review
  --> in_transit
  --> failed
  --> cancelled
  --> exception

completed
  --> reversed

failed
  --> draft  (via retry-sync, creates new attempt linked to original intent)

exception
  --> submitted  (after manual resolution)
  --> cancelled
```

---

## Issue 7.1: Transfer Intent Creation and Persistence

### Title

Implement transfer intent creation with local persistence before rail submission

### Description

Build the `POST /api/transfers` endpoint and the underlying `transfers` module (routes, schemas, service, repository). When a caller creates a transfer, the platform must persist a complete intent record in Postgres in `draft` status before any external rail is contacted. This is the foundational safety invariant for all money movement: the platform always knows what it intended to do, regardless of whether the rail accepted or failed.

The intent record captures transfer type, direction, amount, currency, source and destination account references, external bank account reference (where applicable), originating case ID (if linked to onboarding), and metadata such as memo and expected settlement date.

### Scope

- `transfers` table schema: `id`, `tenant_id`, `type`, `direction`, `amount`, `currency`, `source_account_id`, `destination_account_id`, `external_bank_account_id`, `onboarding_case_id` (nullable), `status`, `status_reason`, `idempotency_key`, `metadata`, `created_by`, `created_at`, `updated_at`
- `transfer_status_history` table: `id`, `transfer_id`, `from_status`, `to_status`, `reason`, `actor_id`, `occurred_at`
- Zod request schema for `POST /api/transfers` covering all seven transfer types
- Service-layer validation: account ownership within tenant, valid external bank account reference, valid transfer type for the account pair
- Repository insert within a transaction that writes both the intent row and the initial status history entry
- Response returns the created transfer with `status: draft` and a polling URL

### Acceptance Criteria

- POST /api/transfers creates a `draft` transfer and returns 201 with the transfer ID and polling URL.
- The `transfers` row and a `transfer_status_history` row are written atomically in one Postgres transaction.
- Validation rejects unknown transfer types, missing required fields per type, and cross-tenant account references.
- No external rail is contacted during creation.
- Transfer type-specific required fields are enforced (e.g., ACAT requires contra-firm details; wire requires wire instructions).
- Audit event `transfer_intent_created` is emitted.

### Dependencies

- Epic 2: Account and ExternalBankAccount records must exist.
- Epic 3: Case management for optional `onboarding_case_id` linkage.

---

## Issue 7.2: ACH Deposit and Withdrawal Flows

### Title

Implement ACH deposit and withdrawal transfer logic

### Description

ACH deposits pull funds from a verified external bank account into a platform account. ACH withdrawals push funds from a platform account to a verified external bank account. Both flows require the external bank account to have a verified status before the transfer can be submitted.

The service layer must enforce direction-specific rules: deposits require a verified source bank account; withdrawals require sufficient available cash (or a soft hold) and a verified destination bank account. Amount limits and frequency limits should be configurable per tenant.

### Scope

- ACH-specific validation in transfer service: verified bank account check, amount floor/ceiling enforcement, duplicate detection within configurable time window
- Tenant-level ACH configuration: daily/monthly limits, per-transfer ceiling
- Direction enum: `deposit` or `withdrawal`
- Integration point: upon submission (Issue 7.7), the platform calls the ACH rail adapter
- ACH-specific metadata: originator name, trace number (populated after submission), expected settlement date (T+1 to T+3)

### Acceptance Criteria

- ACH deposit transfer can be created and submitted when the linked external bank account is verified.
- ACH withdrawal transfer enforces available cash check before submission.
- Transfers against unverified bank accounts are rejected with a clear error.
- Tenant-configurable amount limits are enforced at submission time.
- Duplicate ACH transfer detection flags potential duplicates (same amount, same bank account, within configurable window) and returns a warning or blocks based on tenant policy.

### Dependencies

- Issue 7.1: Transfer intent creation.
- Issue 7.11: Bank account verification.

---

## Issue 7.3: ACAT Full Transfer Flow

### Title

Implement ACAT full account transfer workflow

### Description

An ACAT full transfer moves all assets and cash from an account at a contra firm to the platform. This is the most common transfer type during advisor transitions. The platform must capture the contra firm identifier, the account number at the contra firm, and the account type. ACAT transfers have a longer lifecycle than ACH (typically 5-8 business days) and involve NSCC/ACATS system interactions via the clearing integration.

ACAT full transfers can originate from an onboarding case or be created standalone for an existing active account.

### Scope

- ACAT-specific schema fields: `contra_firm_dtc_number`, `contra_account_number`, `contra_account_type`, `contra_account_title`
- Validation: contra firm DTC number format, required contra account fields
- ACAT-specific statuses map to the common state machine: `submitted` maps to TIF submitted, `pending_external_review` maps to contra firm review period, `in_transit` maps to assets in transfer
- Document attachment support: clients may need to sign a TIF (Transfer Initiation Form)
- ACAT rejection handling: contra firm can reject the transfer with reason codes that map to `failed` or `exception`

### Acceptance Criteria

- ACAT full transfer can be created with contra firm details and persisted in `draft`.
- Submission sends the TIF to the clearing rail adapter and transitions to `submitted`.
- Contra firm rejection events transition the transfer to `failed` with the upstream reason code preserved.
- The transfer can be linked to an onboarding case via `onboarding_case_id`.
- Document attachments (TIF) can be linked to the transfer record.

### Dependencies

- Issue 7.1: Transfer intent creation.
- Issue 7.7: Transfer submission to external rails.
- Issue 7.8: Status ingestion from external rails.
- Epic 5: Document vault for TIF attachment.

---

## Issue 7.4: ACAT Partial Transfer Flow

### Title

Implement ACAT partial transfer workflow for selected positions

### Description

An ACAT partial transfer moves specific positions (and optionally a cash amount) from a contra firm account rather than the full account. The platform must capture a line-item manifest of assets to transfer, including CUSIP/symbol, quantity or full position flag per line, and an optional cash amount.

Partial ACATs are more complex operationally because the contra firm may reject individual line items while accepting others. The platform must handle partial acceptance and surface per-line-item status when available.

### Scope

- `transfer_line_items` table: `id`, `transfer_id`, `cusip`, `symbol`, `description`, `requested_quantity`, `transfer_full_position`, `status`, `rejection_reason`, `actual_quantity_transferred`
- Zod schema for partial ACAT: array of line items plus optional cash amount
- Per-line-item status tracking: `pending`, `accepted`, `rejected`, `transferred`
- Overall transfer status reflects the aggregate: if all lines rejected, transfer is `failed`; if some rejected, transfer may complete with exceptions noted
- Cash component handling as a separate line item type

### Acceptance Criteria

- Partial ACAT transfer can be created with one or more line items and optional cash amount.
- Each line item has independent status tracking.
- Partial acceptance from the contra firm updates individual line statuses and the overall transfer status appropriately.
- GET /api/transfers/:id for a partial ACAT returns line-item details with per-item status.
- Full rejection of all line items transitions the transfer to `failed`.

### Dependencies

- Issue 7.1: Transfer intent creation.
- Issue 7.3: Shared ACAT infrastructure (contra firm fields, clearing rail adapter).
- Issue 7.8: Status ingestion for per-line-item updates.

---

## Issue 7.5: Wire In/Out Flows

### Title

Implement wire transfer in and out workflows

### Description

Wire transfers are higher-value, lower-frequency transfers that require explicit wire instructions (receiving bank ABA/routing, account number, beneficiary details, reference/memo). Wire-out transfers typically require additional approval due to fraud risk and higher dollar amounts.

Wire-in transfers are often initiated externally; the platform may receive notification of an incoming wire and must match it to an account. Wire-out transfers are initiated by the advisor through the platform.

### Scope

- Wire instruction fields: `receiving_bank_name`, `receiving_bank_aba`, `receiving_account_number`, `beneficiary_name`, `beneficiary_address`, `reference_memo`, `intermediary_bank` (optional)
- Wire-out approval policy: transfers above a configurable threshold require explicit approval before submission (integrates with Epic 3 approval requests)
- Wire-in matching: service attempts to match incoming wire notifications to a platform account based on account number and reference
- Wire-in unmatched handling: creates a transfer in `exception` status for manual resolution
- Wire-specific validation: ABA routing number format, required beneficiary fields

### Acceptance Criteria

- Wire-out transfer can be created with full wire instructions and persisted in `draft`.
- Wire-out transfers above the tenant-configurable threshold require an approval request before submission proceeds.
- Wire-in notifications create or update transfer records with matched account.
- Unmatched wire-in notifications create transfers in `exception` status for operations review.
- Wire instructions are validated for format correctness at creation time.

### Dependencies

- Issue 7.1: Transfer intent creation.
- Issue 7.7: Transfer submission to external rails.
- Epic 3: Approval request for wire-out threshold enforcement.

---

## Issue 7.6: Internal Journal Transfers

### Title

Implement internal journal transfers between accounts within the platform

### Description

Journal transfers move cash between two accounts on the platform without involving an external rail. These are used for rebalancing across accounts in a household, moving cash to cover fees, or consolidating positions. Because no external system is involved, journals can complete synchronously (or near-synchronously) after validation.

Journals still follow the same intent-first pattern: a `draft` is created, then submitted. Submission validates both accounts, checks available cash on the source, debits the source, credits the destination, and completes the transfer -- all within a single Postgres transaction.

### Scope

- Validation: both accounts must belong to the same tenant, source must have sufficient available cash
- Transactional completion: submission creates ledger entries (debit on source, credit on destination) and transitions the transfer to `completed` atomically
- No external rail call; the platform is authoritative
- Journal transfers return 200 (not 202) on submission since they complete synchronously
- Household-scoping rule: journals across households may require additional authorization

### Acceptance Criteria

- Journal transfer can be created between two platform accounts in the same tenant.
- Submission validates available cash and completes atomically with ledger entry creation.
- Submission returns 200 with `status: completed` (not 202).
- Insufficient cash returns a validation error without partial state change.
- Cross-household journal transfers enforce additional permission checks.
- Transfer status history records the full lifecycle: `draft` -> `submitted` -> `completed`.

### Dependencies

- Issue 7.1: Transfer intent creation.
- Epic 2: Account registry for ownership validation.
- Epic 11 (future): Cash ledger for debit/credit entries. Initially, journal completion updates a balance projection; full ledger integration comes in Epic 11.

---

## Issue 7.7: Transfer Submission to External Rails

### Title

Implement async transfer submission to external rails returning 202 Accepted

### Description

The `POST /api/transfers/:id/submit` endpoint transitions a `draft` transfer to `submitted` and dispatches the transfer to the appropriate external rail adapter. For all external transfer types (ACH, ACAT, wire), the endpoint returns `202 Accepted` with the current status and a polling URL, because the rail will not confirm completion synchronously.

The submission flow is:
1. Validate the transfer is in a submittable state (`draft` or re-entry from `exception` after manual resolution).
2. Validate the idempotency key to prevent duplicate submissions.
3. Perform type-specific pre-submission checks (verified bank account, cash availability, approval completion for wires).
4. Transition status to `submitted` in Postgres.
5. Call the external rail adapter (ACH service, clearing/ACAT service, or wire service).
6. If the rail returns an upstream reference ID, persist it on the transfer record.
7. If the rail call fails transiently, transition to `exception` with a retryable flag.
8. Return 202 with transfer ID, current status, and polling URL.

Rail adapter calls must happen outside the Hono request handler's critical path. The HTTP handler persists the `submitted` status and enqueues the rail dispatch; a worker or async process performs the actual external call.

### Scope

- `POST /api/transfers/:id/submit` route, schema, and service method
- Idempotency key enforcement: reject duplicate submissions with `409 IDEMPOTENCY_CONFLICT`
- Rail adapter dispatch: enqueue to a job queue or Kafka topic for the appropriate rail worker
- Rail adapter interface: `submitTransfer(transfer): Promise<{ upstreamId: string; status: string }>`
- Upstream reference ID persistence: `upstream_rail_id`, `upstream_rail_name`, `upstream_submitted_at`
- Transient failure handling: if dispatch fails, mark transfer as `exception` with `retryable: true`
- Permission check: caller must have `transfer.submit` capability

### Acceptance Criteria

- POST /api/transfers/:id/submit on a `draft` transfer returns 202 with status `submitted`.
- Duplicate submission with the same idempotency key returns 409.
- Submission of a transfer not in `draft` or resolved `exception` returns 409 `INVALID_WORKFLOW_STATE`.
- The external rail adapter is called asynchronously (not blocking the HTTP response).
- Upstream reference ID is persisted when the rail acknowledges receipt.
- Transient rail failures transition the transfer to `exception` with retryable metadata.
- Audit event `transfer_submitted` is emitted with actor, transfer ID, and rail name.
- Internal journal transfers are excluded from this flow (they use synchronous completion per Issue 7.6).

### Dependencies

- Issue 7.1: Transfer intent record.
- Issue 7.12: Idempotency key infrastructure.
- Epic 4: External service integration framework (retry policies, dead-letter handling).

---

## Issue 7.8: Status Ingestion from External Rails

### Title

Implement Kafka consumers and webhook handlers for external rail status updates

### Description

External rails (ACH processor, NSCC/ACATS, wire service) emit lifecycle events as transfers progress. The platform must ingest these events and update the local transfer record's status accordingly. Events arrive via Kafka topics (preferred) or inbound webhooks, depending on the rail.

Each consumer or handler must:
1. Resolve the transfer by upstream rail ID.
2. Validate the incoming status transition against the state machine.
3. Update the transfer status and append to the status history.
4. Emit a domain event (`transfer.status_changed`) for downstream consumers (notifications, case updates, ledger hooks).

Consumers must be idempotent: processing the same event twice must not corrupt state or create duplicate history entries. Consumers must be replay-safe: re-processing a batch of events must converge to the correct final state.

### Scope

- Kafka consumer group for transfer rail events (separate worker process, not in the Hono HTTP runtime)
- Webhook handler route for rails that push via HTTP (with signature verification)
- Event schema: `{ railName, upstreamId, upstreamStatus, timestamp, details }`
- Rail-to-platform status mapping per transfer type (e.g., ACH `settled` -> platform `completed`)
- Idempotent processing: deduplicate by `(upstream_rail_id, upstream_status, timestamp)` tuple
- Dead-letter handling for events that fail processing after retries
- Domain event emission: `transfer.status_changed` published to internal Kafka topic
- ACAT-specific: per-line-item status updates for partial transfers (Issue 7.4)

### Acceptance Criteria

- Kafka consumer processes rail status events and updates the transfer record.
- Webhook handler accepts inbound status pushes with signature verification and updates the transfer record.
- Duplicate events are detected and skipped without error.
- Invalid status transitions are logged and routed to dead-letter for investigation.
- Domain event `transfer.status_changed` is emitted after every successful status update.
- Consumer runs in a separate worker process from the HTTP server.
- Consumer lag is observable via metrics.

### Dependencies

- Issue 7.1: Transfer records with upstream rail ID.
- Issue 7.9: State machine validation.
- Epic 4: Kafka consumer framework, dead-letter handling.

---

## Issue 7.9: Transfer Lifecycle State Machine

### Title

Implement the transfer status state machine with enforced transitions

### Description

All transfer status changes must pass through a state machine that defines valid transitions. The state machine is the single authority on whether a given `(current_status, target_status)` pair is allowed. Invalid transitions must be rejected, not silently applied.

The state machine is:

- `draft` -> `submitted`, `cancelled`
- `submitted` -> `pending_verification`, `pending_external_review`, `in_transit`, `failed`, `exception`
- `pending_verification` -> `in_transit`, `failed`, `cancelled`, `exception`
- `pending_external_review` -> `in_transit`, `failed`, `cancelled`, `exception`
- `in_transit` -> `completed`, `failed`, `reversed`
- `completed` -> `reversed`
- `failed` -> `draft` (retry creates new attempt linked to original)
- `exception` -> `submitted`, `cancelled`

Every transition must record the previous status, new status, reason, acting user or system actor, and timestamp in the `transfer_status_history` table.

### Scope

- `TransferStateMachine` module: pure function `canTransition(currentStatus, targetStatus): boolean` and `assertTransition(currentStatus, targetStatus): void` (throws on invalid)
- Status enum type shared across all transfer code
- Transition recording in `transfer_status_history` within the same transaction as the status update
- Machine is type-agnostic (same transitions for ACH, ACAT, wire, journal) but individual flows may only use a subset of paths
- Unit tests for every valid and invalid transition pair

### Acceptance Criteria

- `TransferStateMachine.canTransition` returns correct boolean for all defined pairs.
- `assertTransition` throws `INVALID_WORKFLOW_STATE` error for undefined transitions.
- Every status update in the codebase goes through the state machine -- no direct status overwrites.
- `transfer_status_history` is appended atomically with every status change.
- State machine is covered by exhaustive unit tests including all valid transitions and representative invalid transitions.

### Dependencies

- Issue 7.1: Transfer table and status history table.

---

## Issue 7.10: Reversal and Return Handling

### Title

Implement transfer reversal and return processing

### Description

Transfers that have reached `completed` or `in_transit` status can be reversed due to ACH returns, ACAT reclaims, or wire recalls. Reversals are not destructive edits; they create compensating records and transition the original transfer to `reversed`.

ACH returns are the most common case: an ACH deposit may be returned by the originating bank for reasons such as insufficient funds (R01), account closed (R02), or unauthorized (R10). The platform must ingest the return event, transition the transfer to `reversed`, create a compensating ledger entry, and flag the transfer for operational review.

For ACAT reversals, the contra firm may reclaim assets after initial transfer. For wires, a recall may be initiated. Each scenario must be handled with the correct compensating action.

### Scope

- `transfer_reversals` table: `id`, `transfer_id`, `reason_code`, `reason_description`, `source` (rail name), `reversed_amount`, `reversed_at`, `created_at`
- ACH return code mapping: R01-R29 codes mapped to platform reason categories
- Reversal processing: update transfer status to `reversed`, create reversal record, emit `transfer.reversed` domain event
- Compensating ledger action: create a debit entry reversing the original credit (or vice versa) -- initially as a balance adjustment, full ledger integration in Epic 11
- Operational task creation: reversals generate an operational task for review (integrates with Epic 3)
- Partial reversal support: for partial ACAT reclaims, individual line items can be reversed

### Acceptance Criteria

- Completed transfers can be transitioned to `reversed` via the status ingestion pipeline.
- A `transfer_reversals` record is created with the upstream reason code.
- ACH return codes (R01, R02, R10, etc.) are mapped to platform reason categories.
- A compensating balance adjustment is created.
- An operational task is generated for operations team review.
- Partial ACAT reversals update individual line-item statuses.
- Domain event `transfer.reversed` is emitted.
- Reversal of an already-reversed transfer is rejected by the state machine.

### Dependencies

- Issue 7.8: Status ingestion pipeline delivers reversal events.
- Issue 7.9: State machine permits `completed` -> `reversed` and `in_transit` -> `reversed`.
- Epic 3: Operational task creation for review.

---

## Issue 7.11: Bank Account Verification

### Title

Implement external bank account verification via micro-deposits and instant verification

### Description

Before ACH transfers can be submitted, the linked external bank account must be verified. The platform must support two verification methods:

1. **Micro-deposit verification**: The platform initiates two small deposits (e.g., $0.01-$0.99) to the external account. The account holder confirms the amounts, which proves ownership. This is the fallback method.

2. **Instant verification**: Via a third-party service (e.g., Plaid, MX, or similar), the account holder authenticates with their bank and the platform receives a verified account token. This is the preferred method for speed and conversion.

The `ExternalBankAccount` record (from Epic 2) must track verification status: `unverified`, `pending_micro_deposit`, `micro_deposit_sent`, `verified`, `failed`.

### Scope

- `POST /api/bank-accounts/:id/verify/initiate` -- starts micro-deposit or instant verification flow
- `POST /api/bank-accounts/:id/verify/confirm` -- confirms micro-deposit amounts
- `POST /api/bank-accounts/:id/verify/instant` -- handles callback/token from instant verification provider
- Verification status on `ExternalBankAccount`: `unverified` -> `pending_micro_deposit` -> `micro_deposit_sent` -> `verified` (or `failed`)
- Instant verification path: `unverified` -> `verified` (single step on success)
- Micro-deposit amount storage (encrypted or hashed) for confirmation matching
- Rate limiting on confirmation attempts (max 3 failures before lockout)
- Verification expiry: micro-deposits must be confirmed within configurable window (e.g., 5 business days)

### Acceptance Criteria

- Micro-deposit initiation sends two small deposits and transitions bank account to `micro_deposit_sent`.
- Confirmation endpoint validates the amounts and transitions to `verified` on match.
- Three failed confirmation attempts lock the verification and transition to `failed`.
- Instant verification endpoint accepts a verified token from the provider and transitions directly to `verified`.
- ACH transfer submission rejects transfers linked to unverified bank accounts.
- Verification status is visible on the bank account record via existing GET endpoints.

### Dependencies

- Epic 2: `ExternalBankAccount` record.
- Epic 4: Integration framework for micro-deposit rail and instant verification provider.

---

## Issue 7.12: Idempotent Transfer Submission

### Title

Implement idempotency key enforcement for transfer creation and submission

### Description

Transfer creation and submission are operations with external side effects and must be idempotent. The platform must accept an `Idempotency-Key` header on `POST /api/transfers` and `POST /api/transfers/:id/submit`. If the same key is reused:

- If the original request succeeded, return the same response (same status code, same body).
- If the original request is still in flight, return `409 IDEMPOTENCY_CONFLICT`.
- If the original request failed with a retryable error, allow retry with the same key.

Keys must be tenant-scoped to prevent cross-tenant collisions.

### Scope

- `idempotency_keys` table: `key`, `tenant_id`, `endpoint`, `request_hash`, `response_status`, `response_body`, `created_at`, `expires_at`
- Middleware or service-layer check before processing: look up key, return cached response if found
- Key expiry: keys expire after a configurable TTL (e.g., 24 hours) to prevent unbounded growth
- Key scoping: `(tenant_id, key)` is the uniqueness constraint
- Redis fast-path: optionally check Redis first for recently used keys before hitting Postgres
- Cleanup job: periodic deletion of expired keys

### Acceptance Criteria

- `POST /api/transfers` with an `Idempotency-Key` header stores the key and response on success.
- Repeating the same request with the same key returns the original 201 response without creating a duplicate.
- `POST /api/transfers/:id/submit` with an `Idempotency-Key` header prevents duplicate rail submissions.
- Different tenants can use the same key string without collision.
- Expired keys are cleaned up and no longer block new requests with the same key.
- Missing `Idempotency-Key` on `POST /api/transfers/:id/submit` returns 400.

### Dependencies

- Epic 4: Integration framework conventions for idempotency.
- Epic 1: Tenant context for key scoping.

---

## Issue 7.13: Transfer-to-Case Linking

### Title

Implement linking between transfers and onboarding or standalone cases

### Description

Transfers can originate from two contexts:

1. **Onboarding-linked**: During account opening, the advisor selects a funding method and creates a transfer as part of the onboarding case. The transfer's lifecycle is visible within the onboarding case view, and onboarding completion may depend on the initial funding transfer reaching a certain state.

2. **Standalone**: An advisor creates a transfer for an already-active account. These transfers have no parent case but may optionally be tracked as standalone transfer cases (from Epic 3).

The transfer record carries an optional `onboarding_case_id` foreign key. When present, transfer status changes emit events that the onboarding case workflow can consume to update its own progress tracking. Standalone transfers may be associated with a `transfer_case_id` for operational tracking.

### Scope

- `onboarding_case_id` nullable FK on `transfers` table (from Issue 7.1)
- `transfer_case_id` nullable FK on `transfers` table for standalone operational tracking
- Event bridge: `transfer.status_changed` events include the case ID so the case workflow can react
- Onboarding case progress: the case tracks whether the initial funding transfer has reached `in_transit` or `completed`
- Query support: `GET /api/onboarding-cases/:id/transfers` returns all transfers linked to the case
- Query support: `GET /api/transfers?account_id=X` returns transfers for a given account regardless of case linkage
- Transfer creation from onboarding: `POST /api/transfers` with `onboarding_case_id` validates the case exists and belongs to the same tenant

### Acceptance Criteria

- Transfers can be created with an `onboarding_case_id` that links them to an active onboarding case.
- Transfers can be created standalone with an optional `transfer_case_id`.
- `GET /api/onboarding-cases/:id/transfers` returns linked transfers.
- Transfer status change events include case IDs for downstream consumption.
- Onboarding case status reflects funding progress when a linked transfer reaches `in_transit` or `completed`.
- Transfers cannot be linked to cases belonging to a different tenant.

### Dependencies

- Issue 7.1: Transfer intent record with case FK columns.
- Epic 3: Case management for onboarding and transfer cases.
- Epic 6: Onboarding case workflow.

---

## Issue 7.14: Reconciliation Hooks with Cash Ledger

### Title

Implement reconciliation integration points between transfers and the cash ledger

### Description

Every completed transfer must produce a corresponding ledger effect, and every reversed transfer must produce a compensating entry. This issue establishes the hooks and contracts -- not the full ledger implementation (that is Epic 11) -- so that transfer completion and reversal events reliably trigger ledger updates.

The reconciliation contract is:

- `transfer.completed` -> create a credit (deposit/transfer-in) or debit (withdrawal/transfer-out) ledger entry with the transfer ID as the source reference.
- `transfer.reversed` -> create a compensating entry that offsets the original.
- Journal transfers create both debit and credit entries atomically at submission time (Issue 7.6).

Until Epic 11 is built, the platform maintains a simplified `balance_adjustments` table that records these effects. When the full ledger is implemented, balance adjustments are migrated to proper ledger entries.

### Scope

- `balance_adjustments` table: `id`, `tenant_id`, `account_id`, `transfer_id`, `type` (credit/debit), `amount`, `currency`, `effective_date`, `created_at`
- Domain event handler: listens for `transfer.completed` and `transfer.reversed` events, creates balance adjustment records
- Reconciliation query: `GET /api/accounts/:id/balance-adjustments?from=&to=` for operations visibility
- Daily reconciliation check: a scheduled job that compares the sum of balance adjustments against expected transfer outcomes and flags mismatches as reconciliation breaks
- Break records: `reconciliation_breaks` table with transfer ID, expected amount, actual amount, and resolution status

### Acceptance Criteria

- Every `transfer.completed` event produces a balance adjustment record.
- Every `transfer.reversed` event produces a compensating balance adjustment.
- Journal transfers produce debit and credit adjustments atomically.
- Duplicate event processing does not create duplicate adjustments (idempotent handler).
- Daily reconciliation job identifies and records mismatches.
- Reconciliation breaks are queryable by operations.
- Balance adjustment records include the transfer ID for traceability.

### Dependencies

- Issue 7.8: Status ingestion emits `transfer.completed` and `transfer.reversed` events.
- Issue 7.10: Reversal handling emits `transfer.reversed` events.
- Issue 7.6: Journal transfers create adjustments at submission.
- Epic 11 (future): Full ledger replaces balance adjustments.

---

## Issue 7.15: Transfer Cancellation Flow

### Title

Implement transfer cancellation via POST /api/transfers/:id/cancel

### Description

Advisors or operations staff may cancel a transfer that has not yet reached a terminal state. Cancellation behavior depends on the current status:

- `draft`: Immediate cancellation. No external action needed.
- `submitted`, `pending_verification`, `pending_external_review`: Platform sends a cancellation request to the external rail. Cancellation may be accepted or rejected by the rail. If rejected (e.g., transfer already in transit), the transfer remains in its current status and the caller is informed.
- `in_transit`, `completed`, `reversed`, `failed`: Cannot be cancelled. Returns `INVALID_WORKFLOW_STATE`.

The endpoint must be idempotent: cancelling an already-cancelled transfer returns success.

### Scope

- `POST /api/transfers/:id/cancel` route, schema, and service method
- Reason field: caller must provide a cancellation reason
- Status-dependent logic: immediate local cancellation for `draft`; async cancellation request for `submitted`/`pending_*` states
- Rail cancellation adapter: sends cancel request to the appropriate rail and handles accept/reject response
- Idempotent: cancelling a `cancelled` transfer returns 200
- Permission check: caller must have `transfer.cancel` capability
- Audit event: `transfer_cancellation_requested` and `transfer_cancelled` (or `transfer_cancellation_rejected`)

### Acceptance Criteria

- Draft transfers are cancelled immediately and return 200 with `status: cancelled`.
- Submitted or pending transfers trigger an async cancellation request to the rail and return 202.
- Rail cancellation rejection is surfaced to the caller and the transfer remains in its current status.
- Transfers in `in_transit`, `completed`, `reversed`, or `failed` cannot be cancelled.
- Cancelling an already-cancelled transfer returns 200 (idempotent).
- Cancellation reason is recorded in the status history.
- Audit events are emitted for cancellation requests and outcomes.

### Dependencies

- Issue 7.9: State machine defines valid cancellation transitions.
- Issue 7.7: Rail adapter interface extended with cancel capability.
- Epic 4: Async dispatch for rail cancellation requests.

---

## Issue 7.16: Transfer Retry-Sync Flow

### Title

Implement POST /api/transfers/:id/retry-sync for re-synchronization with external rail

### Description

When a transfer is in `exception` status due to a transient rail failure (network timeout, upstream unavailability), the operations team or an automated recovery job can trigger a re-synchronization attempt. The `retry-sync` endpoint re-dispatches the transfer to the rail adapter.

This is distinct from cancelling and re-creating a transfer. Retry-sync preserves the original transfer ID and idempotency key, and the rail adapter uses the same upstream reference if one was obtained before the failure.

### Scope

- `POST /api/transfers/:id/retry-sync` route, schema, and service method
- Precondition: transfer must be in `exception` status with `retryable: true` metadata
- Behavior: transitions from `exception` back to `submitted` and re-dispatches to the rail adapter
- Max retry count: configurable per tenant, with a hard ceiling (e.g., 5 attempts)
- Retry history: each attempt is logged in `transfer_status_history`
- Automated retry: optionally, a scheduled job can auto-retry eligible transfers (configurable delay between attempts)
- Permission check: caller must have `transfer.submit` capability

### Acceptance Criteria

- `retry-sync` on an `exception` transfer with `retryable: true` returns 202 and re-dispatches.
- `retry-sync` on a non-exception transfer returns `INVALID_WORKFLOW_STATE`.
- `retry-sync` on an exception transfer with `retryable: false` returns 400 with an explanation.
- Retry count is tracked and enforced; exceeding the limit transitions the transfer to `failed`.
- Each retry attempt is recorded in the status history.
- Automated retry job picks up eligible transfers and processes them within configured delay.

### Dependencies

- Issue 7.7: Rail adapter dispatch for re-submission.
- Issue 7.9: State machine permits `exception` -> `submitted`.
- Epic 4: Retry policies and dead-letter handling.

---

## Issue 7.17: Transfer Query and Detail Endpoint

### Title

Implement GET /api/transfers/:id with full status history and related records

### Description

The `GET /api/transfers/:id` endpoint returns the complete transfer record including current status, status history, linked case references, line items (for partial ACATs), reversal records, and upstream rail identifiers. This is the primary polling endpoint referenced in 202 responses.

Additionally, support list queries for transfers filtered by account, case, status, type, and date range.

### Scope

- `GET /api/transfers/:id` route and presenter
- Response includes: transfer fields, current status, `status_history[]`, `line_items[]` (if ACAT partial), `reversals[]` (if any), `onboarding_case_id`, `transfer_case_id`, `upstream_rail_id`, `upstream_rail_name`
- `GET /api/transfers` with query parameters: `account_id`, `status`, `type`, `onboarding_case_id`, `created_after`, `created_before`, `limit`, `offset`
- Pagination: offset-based with configurable page size and hard maximum
- Permission check: caller must have `transfer.read` capability and transfers are scoped to the caller's tenant
- Presenter layer: maps internal DB fields to API response shape, redacts sensitive fields

### Acceptance Criteria

- GET /api/transfers/:id returns the full transfer record with status history.
- ACAT partial transfers include line items in the response.
- Reversed transfers include reversal records in the response.
- GET /api/transfers supports filtering by account, status, type, case, and date range.
- Results are paginated with offset and limit.
- Transfers are tenant-scoped; cross-tenant access is impossible.
- Response shape is consistent across all transfer types.

### Dependencies

- Issue 7.1: Transfer records.
- Issue 7.4: Line items for partial ACATs.
- Issue 7.10: Reversal records.
- Epic 1: Tenant scoping and permission enforcement.
