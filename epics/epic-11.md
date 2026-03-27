# Epic 11: Cash, Ledger, and Balance Projections

## Goal

Provide deterministic, auditable cash and balance representations for every account on the platform. Balances must be derived from an append-only ledger of entries, never computed ad hoc from mutable state. When an upstream system owns the authoritative ledger, Postgres stores projected and reconciled views tagged with source, as-of timestamp, and sync status.

## Context

The platform operates in a space where advisors, clients, billing systems, and reporting pipelines all consume balance data. If balances are inconsistent, computed on the fly from scattered mutable records, or silently stale, the downstream consequences compound: incorrect fee calculations, misleading client statements, failed pre-trade checks, and reconciliation breaks that are impossible to diagnose.

This epic establishes the internal books-and-records layer for cash. It does not replace an upstream clearing firm or custodian ledger. It creates the platform-side projection that all internal consumers depend on, with explicit provenance and reconciliation contracts.

## Architecture Principles

- **Append-only ledger entries.** Every cash-affecting event produces an immutable ledger row. Balances are derived by summing entries, not by mutating a running total.
- **Deterministic derivation.** Given the same set of ledger entries, the same balance must result. No ambient state, no runtime branching based on external calls.
- **Source provenance.** Every entry and every synced balance carries a source system identifier and an as-of timestamp.
- **Reconciliation as a first-class concern.** The gap between local projections and upstream truth is expected, measured, and surfaced, not hidden.
- **Tenant isolation.** All tables include `tenant_id`. All queries enforce tenant scoping.

## Dependencies

- **Epic 7 (Money Movement and Transfer Operations):** Transfer lifecycle events produce ledger entries for deposits, withdrawals, and holds.
- **Epic 9 (Orders, OMS/EMS Integration, and Trade Status):** Settlement events produce settlement adjustment entries.
- **Epic 4 (External Service Integration Framework):** Upstream balance sync relies on the integration framework for polling, retry, and dead-letter handling.
- **Epic 12 (Billing and Fee Operations):** Billing runs post fee debit entries into this ledger. Epic 12 depends on this epic; the integration point is defined here.

---

## Issue 11-1: Cash Balance Projection Model

### Title

Design and implement the cash balance projection schema

### Description

Create the core Postgres table that stores the current projected cash balance state per account. This table holds the derived balance breakdown (available, pending, settled) and serves as the queryable snapshot that downstream consumers read. It is not the source of truth itself -- the ledger entries are -- but it is the materialized view that gets updated deterministically whenever the ledger changes.

Each row also carries metadata about when the balance was last derived, which ledger entry it was derived through, and whether it has been reconciled against an upstream source.

### Scope

- `cash_balance_projections` table in Postgres with columns:
  - `id` (UUID, primary key)
  - `tenant_id` (UUID, NOT NULL, foreign key to tenants)
  - `account_id` (UUID, NOT NULL, foreign key to accounts)
  - `available_balance` (NUMERIC(18,4), NOT NULL)
  - `pending_balance` (NUMERIC(18,4), NOT NULL)
  - `settled_balance` (NUMERIC(18,4), NOT NULL)
  - `currency` (VARCHAR(3), NOT NULL, default 'USD')
  - `derived_through_entry_id` (UUID, references the last ledger entry included in derivation)
  - `derived_at` (TIMESTAMPTZ, NOT NULL)
  - `reconciliation_status` (ENUM: 'unreconciled', 'reconciled', 'break_detected')
  - `last_reconciled_at` (TIMESTAMPTZ, nullable)
  - `upstream_source` (VARCHAR, nullable, identifies authoritative system)
  - `upstream_as_of` (TIMESTAMPTZ, nullable)
  - `created_at` (TIMESTAMPTZ, NOT NULL)
  - `updated_at` (TIMESTAMPTZ, NOT NULL)
- Unique constraint on `(tenant_id, account_id, currency)`
- Index on `(tenant_id, account_id)`
- Index on `reconciliation_status` for operational queries
- Migration script and rollback script
- TypeScript type definitions and Zod validation schemas

### Acceptance Criteria

- Table is created via a versioned migration.
- One projection row exists per account per currency; the unique constraint enforces this.
- `available_balance`, `pending_balance`, and `settled_balance` are always present and default to zero for new accounts.
- `derived_through_entry_id` links to the last processed ledger entry, enabling idempotent re-derivation.
- `reconciliation_status`, `upstream_source`, and `upstream_as_of` fields are present and queryable.
- TypeScript types and Zod schemas are exported from the domain module.
- Migration can be applied and rolled back cleanly.

### Dependencies

- Epic 2 (accounts table must exist)
- Epic 1 (tenants table must exist)

---

## Issue 11-2: Ledger-Style Entry Table

### Title

Implement the append-only cash ledger entry table

### Description

Create the foundational ledger table that records every cash-affecting event for every account. This table is append-only: rows are never updated or deleted. Corrections and reversals are modeled as new compensating entries, not mutations to existing rows.

Each entry has a type that classifies the cash event (deposit, withdrawal, fee_debit, interest_accrual, settlement_adjustment, transfer_hold, transfer_hold_release, correction). Entries carry the amount (positive for inflows, negative for outflows), an effective date, and provenance metadata identifying the source system and the upstream event that triggered the entry.

### Scope

- `cash_ledger_entries` table in Postgres with columns:
  - `id` (UUID, primary key)
  - `tenant_id` (UUID, NOT NULL, foreign key to tenants)
  - `account_id` (UUID, NOT NULL, foreign key to accounts)
  - `entry_type` (ENUM: 'deposit', 'withdrawal', 'fee_debit', 'interest_accrual', 'settlement_adjustment', 'transfer_hold', 'transfer_hold_release', 'correction')
  - `amount` (NUMERIC(18,4), NOT NULL, positive for credits, negative for debits)
  - `currency` (VARCHAR(3), NOT NULL, default 'USD')
  - `effective_date` (DATE, NOT NULL)
  - `settlement_date` (DATE, nullable)
  - `status` (ENUM: 'pending', 'settled', 'reversed')
  - `description` (TEXT, nullable)
  - `reference_type` (VARCHAR, nullable, e.g., 'transfer_intent', 'billing_run', 'order_execution', 'interest_batch')
  - `reference_id` (UUID, nullable, links to the originating entity)
  - `source_system` (VARCHAR, NOT NULL, e.g., 'platform', 'custodian', 'clearing')
  - `source_event_id` (VARCHAR, nullable, upstream event correlation)
  - `idempotency_key` (VARCHAR, NOT NULL, UNIQUE within tenant)
  - `created_at` (TIMESTAMPTZ, NOT NULL, immutable)
- Unique constraint on `(tenant_id, idempotency_key)`
- Index on `(tenant_id, account_id, effective_date)`
- Index on `(tenant_id, account_id, status)` for balance derivation queries
- Index on `(reference_type, reference_id)` for reverse lookups
- Append-only enforcement: no UPDATE or DELETE permissions granted to the application role; consider a Postgres rule or trigger to reject mutations
- TypeScript type definitions, Zod schemas, and a repository module with insert-only methods

### Acceptance Criteria

- Table is created via a versioned migration.
- Rows are immutable after insert. The application layer exposes only insert operations. A database-level rule or trigger rejects UPDATE and DELETE on this table.
- Every entry has an `idempotency_key` enforced unique per tenant, preventing duplicate postings.
- Entry types cover all required cash events: deposits, withdrawals, fee debits, interest accruals, settlement adjustments, transfer holds, transfer hold releases, and corrections.
- `source_system` and `source_event_id` are always populated, providing full provenance.
- `reference_type` and `reference_id` enable tracing any entry back to the originating business entity.
- Indexes support the balance derivation query pattern (all entries for an account, filtered by status).
- TypeScript types and insert-only repository are exported.

### Dependencies

- Epic 2 (accounts table)
- Epic 1 (tenants table)

---

## Issue 11-3: Balance Derivation Logic

### Title

Implement deterministic balance derivation from ledger entries

### Description

Build the service that computes available, pending, and settled balances for an account by summing its ledger entries. This is the core correctness guarantee: balances are always derived from the append-only ledger, never from ad hoc calculations or mutable counters.

The derivation logic must be deterministic: given the same set of entries, the same balances result. The derived result is written to the `cash_balance_projections` table, and the `derived_through_entry_id` is set to the most recent entry processed, enabling incremental re-derivation.

### Scope

- Balance derivation service/function in TypeScript:
  - `settled_balance` = SUM of all entries where `status = 'settled'`
  - `pending_balance` = SUM of all entries where `status = 'pending'`
  - `available_balance` = `settled_balance` + `pending_balance` (pending deposits count toward available; transfer holds are negative pending entries that reduce availability)
  - The derivation runs within a database transaction that reads entries and writes the projection atomically
- Incremental derivation: process only entries newer than `derived_through_entry_id` when possible, but support full re-derivation from scratch for reconciliation or recovery
- Triggered on new ledger entry insertion (synchronous within the posting transaction, or via a reliable async mechanism with at-least-once delivery)
- Unit tests with deterministic entry sets verifying exact balance outputs
- Edge case handling: zero entries, mixed currencies (reject or handle per-currency), concurrent entry insertion

### Acceptance Criteria

- Given a set of ledger entries for an account, the derivation produces identical balances on every invocation.
- `settled_balance`, `pending_balance`, and `available_balance` are computed using the documented formulas and match expected values in unit tests.
- The projection row is updated atomically with the ledger read within a single Postgres transaction.
- `derived_through_entry_id` and `derived_at` are set correctly after each derivation.
- Full re-derivation from entry zero produces the same result as incremental derivation.
- Concurrent entry inserts for the same account do not produce incorrect balances (serializable isolation or explicit locking).
- Test coverage includes: zero entries, single entry, mixed pending/settled, negative entries (holds, withdrawals), correction entries, and entries across multiple currencies.

### Dependencies

- Issue 11-1 (cash_balance_projections table)
- Issue 11-2 (cash_ledger_entries table)

---

## Issue 11-4: Fee Debit Posting Integration

### Title

Integrate billing fee debits into the cash ledger

### Description

When a billing run is approved and posted (Epic 12), it must create ledger entries of type `fee_debit` in the cash ledger for each affected account. This issue defines the contract between the billing system and the ledger, and implements the posting side.

Fee debits are negative-amount entries. Each fee debit entry references the billing run and invoice that originated it. The idempotency key is derived from the billing run ID and account ID to prevent double-posting if the billing workflow retries.

### Scope

- A `postFeeDebits` function/service that accepts a billing run result (list of account-level fee amounts with billing_run_id and invoice_id) and inserts corresponding `fee_debit` entries into `cash_ledger_entries`
- Idempotency key format: `fee_debit:{billing_run_id}:{account_id}`
- Each entry sets:
  - `entry_type` = 'fee_debit'
  - `amount` = negative fee amount
  - `status` = 'settled' (fees are immediately effective)
  - `reference_type` = 'billing_run'
  - `reference_id` = billing_run_id
  - `source_system` = 'platform'
- Runs within a transaction: all fee debits for a billing run post atomically, or none do
- Triggers balance re-derivation for all affected accounts after posting
- Reversal support: if a billing run is reversed (Epic 12), a corresponding correction entry is posted with `entry_type = 'correction'` and a reference back to the original fee_debit entry

### Acceptance Criteria

- A completed billing run produces one `fee_debit` ledger entry per affected account.
- All entries for a billing run are inserted atomically within a single transaction.
- Idempotency keys prevent double-posting; re-invoking the same billing run does not create duplicate entries.
- Balance derivation runs after fee debits are posted, and `available_balance` reflects the deducted fees.
- Fee debit reversal creates a correction entry that offsets the original debit.
- The billing system can call this integration without knowledge of ledger internals (clean interface contract).

### Dependencies

- Issue 11-2 (cash_ledger_entries table)
- Issue 11-3 (balance derivation logic)
- Epic 12 (billing run output, consumed but not built here)

---

## Issue 11-5: Interest Accrual Support

### Title

Implement interest accrual entry posting

### Description

Support periodic interest accrual postings to the cash ledger. Interest accrual entries represent earned interest on cash balances. These are posted by an accrual batch job (or received from an upstream system) and recorded as `interest_accrual` entries in the ledger.

Interest calculation logic itself is out of scope for this issue -- the platform may receive accrual amounts from an upstream cash management or sweep program. This issue covers the posting mechanism, the entry format, and the integration contract.

### Scope

- An `postInterestAccruals` function/service that accepts a batch of interest accrual amounts (account_id, amount, accrual_period_start, accrual_period_end, source) and inserts `interest_accrual` entries
- Idempotency key format: `interest_accrual:{source}:{account_id}:{accrual_period_end}`
- Each entry sets:
  - `entry_type` = 'interest_accrual'
  - `amount` = positive (interest credit)
  - `status` = 'pending' initially, moved to 'settled' when confirmed by upstream
  - `reference_type` = 'interest_batch'
  - `source_system` = value from the accrual source (e.g., 'sweep_program', 'custodian')
  - `description` includes accrual period
- Batch insert within a transaction
- Balance re-derivation triggered after posting
- Status transition from 'pending' to 'settled' handled via a new compensating entry or a documented exception to the append-only rule (settled status update on the same entry, with audit)

### Acceptance Criteria

- Interest accrual entries can be posted in batch for multiple accounts.
- Idempotency keys prevent duplicate accrual postings for the same period and account.
- Pending accruals are reflected in `pending_balance`; settled accruals move to `settled_balance`.
- The posting interface accepts accruals from multiple sources (platform-computed or upstream-provided).
- Balance derivation correctly incorporates interest entries.
- Accrual period metadata is stored in the entry description or a JSONB metadata column for auditability.

### Dependencies

- Issue 11-2 (cash_ledger_entries table)
- Issue 11-3 (balance derivation logic)

---

## Issue 11-6: Transfer Hold Management

### Title

Implement transfer hold and release ledger entries

### Description

When a transfer is initiated (e.g., an ACH withdrawal or a wire out), the platform must place a hold on the corresponding cash amount to prevent double-spending. This hold reduces the available balance without affecting the settled balance until the transfer completes or is cancelled.

Holds are modeled as two ledger entry types: `transfer_hold` (negative pending entry placed when transfer is initiated) and `transfer_hold_release` (compensating entry when the transfer settles, fails, or is cancelled). When a transfer settles, the hold is released and a `withdrawal` entry is posted for the actual cash movement.

### Scope

- `placeTransferHold` function: inserts a `transfer_hold` entry when a transfer intent is submitted
  - `entry_type` = 'transfer_hold'
  - `amount` = negative (reduces available cash)
  - `status` = 'pending'
  - `reference_type` = 'transfer_intent'
  - `reference_id` = transfer_intent_id
  - Idempotency key: `transfer_hold:{transfer_intent_id}`
- `releaseTransferHold` function: inserts a `transfer_hold_release` entry when the transfer completes, fails, or is cancelled
  - `entry_type` = 'transfer_hold_release'
  - `amount` = positive (restores the held amount)
  - `status` = 'settled'
  - `reference_type` = 'transfer_intent'
  - `reference_id` = transfer_intent_id
  - Idempotency key: `transfer_hold_release:{transfer_intent_id}`
- On transfer completion: release hold, then post a `withdrawal` entry for the settled amount
- On transfer failure/cancellation: release hold only (no withdrawal entry)
- Integration with Epic 7 transfer lifecycle events via domain events or direct service calls
- Balance derivation triggered after each operation

### Acceptance Criteria

- Initiating a transfer places a hold that reduces `available_balance` by the transfer amount.
- `settled_balance` is not affected by pending holds.
- A completed transfer releases the hold and posts a withdrawal, resulting in the correct final `settled_balance` and `available_balance`.
- A failed or cancelled transfer releases the hold without a withdrawal, fully restoring `available_balance`.
- Double-hold prevention: the idempotency key prevents placing the same hold twice.
- Double-release prevention: the idempotency key prevents releasing the same hold twice.
- All hold and release entries reference the originating `transfer_intent_id`.

### Dependencies

- Issue 11-2 (cash_ledger_entries table)
- Issue 11-3 (balance derivation logic)
- Epic 7 (transfer intent lifecycle events)

---

## Issue 11-7: Settlement Adjustment Entries

### Title

Post settlement adjustment entries from trade settlement events

### Description

When a trade settles (via the OMS/EMS integration in Epic 9), a corresponding cash impact must be recorded in the ledger. Buy orders produce a negative settlement adjustment (cash out); sell orders produce a positive settlement adjustment (cash in). These entries link the trade lifecycle to the cash balance.

Settlement adjustments arrive from upstream settlement events, which may be asynchronous and delayed relative to the original order execution.

### Scope

- `postSettlementAdjustment` function/service that accepts a settlement event (account_id, order_id, execution_id, settlement_amount, settlement_date, direction) and inserts a `settlement_adjustment` entry
- Entry details:
  - `entry_type` = 'settlement_adjustment'
  - `amount` = positive for sell settlements, negative for buy settlements
  - `status` = 'settled'
  - `effective_date` = settlement_date from the event
  - `settlement_date` = settlement_date
  - `reference_type` = 'order_execution'
  - `reference_id` = execution_id
  - `source_system` = 'oms' or the upstream system identifier
  - `source_event_id` = upstream settlement event ID
  - Idempotency key: `settlement_adj:{execution_id}:{settlement_date}`
- Handles partial fills: each execution produces its own settlement entry
- Balance re-derivation triggered after posting
- Error handling: if a settlement event references an unknown account, log the error and route to a dead-letter/exception queue (Epic 4 integration framework patterns)

### Acceptance Criteria

- Each trade settlement event produces exactly one ledger entry per execution.
- Buy settlements reduce the cash balance; sell settlements increase it.
- Idempotency keys prevent duplicate settlement postings for the same execution.
- Settlement entries carry full provenance: execution_id, order_id, upstream event ID, settlement date.
- Balance derivation reflects settlement adjustments immediately after posting.
- Unknown account references are handled gracefully without crashing the ingestion pipeline.
- Partial fills are handled correctly with separate entries per execution.

### Dependencies

- Issue 11-2 (cash_ledger_entries table)
- Issue 11-3 (balance derivation logic)
- Epic 9 (settlement event ingestion)
- Epic 4 (integration framework for upstream event consumption)

---

## Issue 11-8: Reconciliation Framework

### Title

Build the reconciliation framework for comparing local projections against upstream balances

### Description

The platform's local balance projections may diverge from upstream authoritative balances due to timing, missed events, or processing errors. This issue builds the reconciliation framework that periodically compares the local derived balance against the upstream authoritative balance and records the result.

Reconciliation runs on a per-account basis. For each account, the framework fetches the upstream balance (via the integration framework), compares it against the locally derived balance as of the same timestamp, and records whether the balances match within a configurable tolerance.

### Scope

- `reconciliation_runs` table in Postgres:
  - `id` (UUID, primary key)
  - `tenant_id` (UUID, NOT NULL)
  - `run_type` (VARCHAR, e.g., 'daily_cash', 'on_demand')
  - `status` (ENUM: 'pending', 'in_progress', 'completed', 'failed')
  - `started_at` (TIMESTAMPTZ)
  - `completed_at` (TIMESTAMPTZ)
  - `total_accounts` (INTEGER)
  - `matched_count` (INTEGER)
  - `break_count` (INTEGER)
  - `skipped_count` (INTEGER)
  - `created_at` (TIMESTAMPTZ)
- `reconciliation_results` table in Postgres:
  - `id` (UUID, primary key)
  - `tenant_id` (UUID, NOT NULL)
  - `reconciliation_run_id` (UUID, foreign key)
  - `account_id` (UUID, NOT NULL)
  - `local_available_balance` (NUMERIC(18,4))
  - `local_pending_balance` (NUMERIC(18,4))
  - `local_settled_balance` (NUMERIC(18,4))
  - `upstream_available_balance` (NUMERIC(18,4), nullable)
  - `upstream_settled_balance` (NUMERIC(18,4), nullable)
  - `upstream_source` (VARCHAR, NOT NULL)
  - `upstream_as_of` (TIMESTAMPTZ, NOT NULL)
  - `local_as_of` (TIMESTAMPTZ, NOT NULL)
  - `match_status` (ENUM: 'matched', 'break', 'skipped')
  - `variance_amount` (NUMERIC(18,4), nullable)
  - `notes` (TEXT, nullable)
  - `created_at` (TIMESTAMPTZ)
- Reconciliation service that:
  - Iterates over active accounts for a tenant
  - Fetches upstream balance via integration adapter (Epic 4)
  - Derives local balance as of the upstream as-of timestamp
  - Compares with configurable tolerance (e.g., 0.01)
  - Records the result
  - Updates `cash_balance_projections.reconciliation_status` based on the result
- Configurable scheduling (daily, on-demand)
- TypeScript types, Zod schemas, repository, and service module

### Acceptance Criteria

- A reconciliation run compares local and upstream balances for all active accounts in a tenant.
- Results are recorded per-account with full balance details from both sides.
- Matching accounts are marked 'matched'; mismatches are marked 'break'.
- Accounts where upstream balance is unavailable are marked 'skipped'.
- The `cash_balance_projections` table is updated with the reconciliation outcome.
- Tolerance is configurable and defaults to 0.01.
- Reconciliation runs are idempotent: re-running for the same period does not create duplicate result rows.
- Run-level summary (total, matched, breaks, skipped) is stored for operational dashboards.

### Dependencies

- Issue 11-1 (cash_balance_projections table)
- Issue 11-3 (balance derivation logic)
- Epic 4 (integration framework for upstream balance fetching)

---

## Issue 11-9: Reconciliation Break Detection and Reporting

### Title

Implement reconciliation break detection, alerting, and operational reporting

### Description

When a reconciliation run detects breaks (mismatches between local and upstream balances), the platform must surface these breaks for operational investigation. This issue builds the break detection pipeline, operational query APIs, and alerting hooks.

Breaks are categorized by severity based on variance amount and duration (how many consecutive runs have shown a break for the same account). Persistent breaks escalate in severity.

### Scope

- Break severity classification:
  - `minor`: variance within a second tolerance tier (e.g., < $10)
  - `major`: variance exceeds the minor threshold
  - `critical`: break has persisted across N consecutive reconciliation runs (configurable, default 3)
- `reconciliation_breaks` table in Postgres:
  - `id` (UUID, primary key)
  - `tenant_id` (UUID, NOT NULL)
  - `account_id` (UUID, NOT NULL)
  - `first_detected_run_id` (UUID, references reconciliation_runs)
  - `latest_detected_run_id` (UUID, references reconciliation_runs)
  - `consecutive_break_count` (INTEGER, NOT NULL, default 1)
  - `severity` (ENUM: 'minor', 'major', 'critical')
  - `variance_amount` (NUMERIC(18,4))
  - `status` (ENUM: 'open', 'investigating', 'resolved', 'accepted')
  - `resolution_notes` (TEXT, nullable)
  - `resolved_at` (TIMESTAMPTZ, nullable)
  - `resolved_by` (UUID, nullable, references users)
  - `created_at` (TIMESTAMPTZ)
  - `updated_at` (TIMESTAMPTZ)
- Break lifecycle management:
  - New break created on first detection
  - Consecutive count incremented on subsequent detections
  - Severity escalated when thresholds are crossed
  - Break resolved when a subsequent reconciliation run shows a match
  - Manual resolution with notes for accepted variances
- Operational query endpoints:
  - `GET /api/reconciliation/breaks` -- list open breaks with severity filtering
  - `GET /api/reconciliation/breaks/:id` -- break detail with history
  - `PATCH /api/reconciliation/breaks/:id` -- update status (investigating, resolved, accepted)
- Alerting hooks: emit domain events (`reconciliation_break_detected`, `reconciliation_break_escalated`) for consumption by Epic 15 (Notifications) or operational monitoring
- TypeScript types, Zod schemas, repository, service, and Hono route handlers

### Acceptance Criteria

- Breaks are automatically created when a reconciliation run detects a mismatch.
- Consecutive break count is incremented on subsequent detections; severity escalates at the configured threshold.
- Breaks are automatically resolved when a subsequent run shows a match for the same account.
- Operators can query open breaks filtered by severity, tenant, and status.
- Operators can manually update break status to 'investigating', 'resolved', or 'accepted' with notes.
- Domain events are emitted on break detection and escalation for downstream alerting.
- Break history (first detected, latest detected, consecutive count) is available for each break.

### Dependencies

- Issue 11-8 (reconciliation framework and results)
- Epic 1 (user identity for resolved_by)
- Epic 15 (notifications, optional consumer of break events)

---

## Issue 11-10: Balance Query APIs

### Title

Implement balance query API endpoints

### Description

Expose RESTful API endpoints that return the current balance breakdown for an account. These endpoints read from the `cash_balance_projections` table (the deterministically derived snapshot), not from ad hoc calculations. The response includes available, pending, and settled balances along with freshness metadata (when the balance was last derived, reconciliation status, and upstream sync information).

### Scope

- `GET /api/accounts/:id/balances` endpoint via Hono:
  - Path parameter: `id` (account UUID)
  - Response body:
    ```json
    {
      "account_id": "uuid",
      "currency": "USD",
      "available_balance": "1234.5600",
      "pending_balance": "500.0000",
      "settled_balance": "734.5600",
      "derived_at": "2026-03-26T12:00:00Z",
      "derived_through_entry_id": "uuid",
      "reconciliation_status": "reconciled",
      "last_reconciled_at": "2026-03-26T06:00:00Z",
      "upstream_source": "custodian_x",
      "upstream_as_of": "2026-03-26T05:55:00Z"
    }
    ```
  - Returns 404 if the account has no balance projection row
  - Enforces tenant isolation via middleware
  - Enforces role-based access (advisor can see their clients' accounts; operations can see all within tenant)
- `GET /api/accounts/:id/balances/ledger` endpoint:
  - Returns paginated ledger entries for the account
  - Query parameters: `entry_type` (filter), `status` (filter), `from_date`, `to_date`, `limit`, `offset`
  - Ordered by `created_at` descending
  - Enforces same tenant and role scoping
- `GET /api/households/:id/balances` endpoint:
  - Aggregates balance projections across all accounts in a household
  - Returns per-account breakdown plus household-level totals
- Request validation with Zod
- Response serialization with consistent decimal string formatting (no floating point)
- OpenAPI/route documentation annotations

### Acceptance Criteria

- `GET /api/accounts/:id/balances` returns the current derived balance with all three balance types and freshness metadata.
- Balances are returned as string-formatted decimals, never floating point numbers.
- Tenant isolation is enforced: an account belonging to a different tenant returns 404.
- Role-based access is enforced per Epic 1 permission model.
- `GET /api/accounts/:id/balances/ledger` returns paginated, filterable ledger entries.
- `GET /api/households/:id/balances` aggregates across household accounts correctly.
- All endpoints return appropriate HTTP status codes (200, 404, 403).
- Request parameters are validated; invalid inputs return 400 with descriptive errors.
- Response includes `reconciliation_status` and upstream sync metadata so consumers know the freshness and trustworthiness of the data.

### Dependencies

- Issue 11-1 (cash_balance_projections table)
- Issue 11-2 (cash_ledger_entries table)
- Epic 1 (authentication, authorization middleware)
- Epic 2 (account and household lookups)

---

## Issue 11-11: Upstream Balance Sync

### Title

Implement upstream authoritative balance synchronization

### Description

When the authoritative ledger is owned by an external system (e.g., a clearing firm or custodian), the platform must periodically sync balance snapshots from that upstream source. This issue implements the sync mechanism that fetches upstream balances, stores them with full provenance, and updates the local projection metadata.

The sync does not replace the local ledger. The local ledger remains the platform's internal books-and-records projection. The upstream balance is stored alongside the local projection to enable reconciliation (Issue 11-8) and to provide the most authoritative balance when the platform's local projection is known to be incomplete.

### Scope

- `upstream_balance_snapshots` table in Postgres:
  - `id` (UUID, primary key)
  - `tenant_id` (UUID, NOT NULL)
  - `account_id` (UUID, NOT NULL)
  - `upstream_source` (VARCHAR, NOT NULL, identifies the external system)
  - `available_balance` (NUMERIC(18,4), nullable)
  - `pending_balance` (NUMERIC(18,4), nullable)
  - `settled_balance` (NUMERIC(18,4), nullable)
  - `currency` (VARCHAR(3), NOT NULL)
  - `as_of_timestamp` (TIMESTAMPTZ, NOT NULL, the upstream system's reported time)
  - `sync_status` (ENUM: 'synced', 'stale', 'error')
  - `sync_error_message` (TEXT, nullable)
  - `synced_at` (TIMESTAMPTZ, NOT NULL, when the platform fetched this)
  - `raw_response` (JSONB, nullable, optional storage of the upstream payload for debugging)
  - `created_at` (TIMESTAMPTZ, NOT NULL)
- Index on `(tenant_id, account_id, upstream_source, as_of_timestamp)`
- Unique constraint on `(tenant_id, account_id, upstream_source, as_of_timestamp)` to prevent duplicate snapshots
- Upstream balance sync service:
  - Uses the integration framework (Epic 4) to call the upstream balance API
  - Handles retries, timeouts, and error recording
  - Stores the fetched balance as a new snapshot row
  - Updates `cash_balance_projections` with `upstream_source`, `upstream_as_of`, and refreshes `reconciliation_status` if the sync reveals a match or mismatch
  - Marks previous snapshots as superseded or relies on the as_of ordering
- Sync scheduling: configurable frequency per upstream source (e.g., every 15 minutes, hourly, daily)
- Staleness detection: if a sync has not completed within a configurable window, the snapshot is marked 'stale' and the projection's `reconciliation_status` is updated accordingly
- TypeScript types, Zod schemas, repository, service, and integration adapter interface

### Acceptance Criteria

- Upstream balances are fetched and stored with `upstream_source`, `as_of_timestamp`, and `sync_status`.
- Each sync creates a new snapshot row; historical snapshots are preserved for audit.
- Duplicate snapshots (same source, account, as_of) are rejected by the unique constraint.
- `cash_balance_projections` is updated with the latest upstream metadata after each successful sync.
- Sync errors are recorded with `sync_status = 'error'` and an error message, without crashing the sync job.
- Staleness detection marks snapshots as 'stale' when the sync interval is exceeded.
- The sync service uses the Epic 4 integration framework patterns (retry, dead-letter, correlation IDs).
- Balance query APIs (Issue 11-10) reflect the upstream sync metadata in their responses.
- Sync frequency is configurable per upstream source without code changes.

### Dependencies

- Issue 11-1 (cash_balance_projections table)
- Issue 11-8 (reconciliation framework, consumer of sync data)
- Epic 4 (integration framework for upstream API calls)

---

## Summary

| Issue | Title | Key Deliverable |
|-------|-------|----------------|
| 11-1 | Cash Balance Projection Model | `cash_balance_projections` table and types |
| 11-2 | Ledger-Style Entry Table | Append-only `cash_ledger_entries` table |
| 11-3 | Balance Derivation Logic | Deterministic derivation service |
| 11-4 | Fee Debit Posting Integration | Billing-to-ledger posting contract |
| 11-5 | Interest Accrual Support | Accrual entry posting service |
| 11-6 | Transfer Hold Management | Hold/release entry lifecycle |
| 11-7 | Settlement Adjustment Entries | Trade settlement to cash posting |
| 11-8 | Reconciliation Framework | Local vs upstream comparison engine |
| 11-9 | Reconciliation Break Detection and Reporting | Break tracking, alerting, and ops APIs |
| 11-10 | Balance Query APIs | REST endpoints for balance reads |
| 11-11 | Upstream Balance Sync | External balance fetch, storage, and staleness |

## Implementation Order

1. **Issue 11-1** and **Issue 11-2** (schema foundations, can be done in parallel)
2. **Issue 11-3** (balance derivation, depends on both tables)
3. **Issue 11-4**, **Issue 11-5**, **Issue 11-6**, **Issue 11-7** (entry producers, can be done in parallel after derivation logic exists)
4. **Issue 11-10** (query APIs, can begin once projection table and derivation exist)
5. **Issue 11-11** (upstream sync, can begin once projection table exists)
6. **Issue 11-8** (reconciliation, depends on derivation and upstream sync)
7. **Issue 11-9** (break detection, depends on reconciliation framework)
