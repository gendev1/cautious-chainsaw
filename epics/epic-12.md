# Epic 12: Billing and Fee Operations

## Goal

Build the complete billing lifecycle: fee schedule configuration, billing scope assignment, calendar management, billing run generation with schedule freeze and calculation, review and approval workflow, invoice creation, fee posting to the ledger, reversal and correction via compensating records, exception handling, pro-ration, fee caps, and a full audit trail.

Billing is a lifecycle, not a single calculation. Calculation, approval, and posting are separate steps. Reversals create compensating records, not destructive edits.

## Dependencies

- Epic 2: Client, Household, and Account Registry (billing scope targets)
- Epic 3: Workflow and Case Management (approval workflow primitives)
- Epic 11: Cash, Ledger, and Balance Projections (fee debit posting, AUM valuation source)

## Module Location

```
apps/api/src/modules/billing/
├── routes.ts
├── schemas.ts
├── service.ts
├── repository.ts
├── types.ts
└── events.ts
```

## Core Data Model

```
FeeSchedule
  id, firm_id, name, fee_type (aum_percentage | flat | tiered | per_account),
  frequency (monthly | quarterly | semi_annually | annually),
  tiers (jsonb), flat_amount, per_account_amount, aum_bps,
  min_fee, max_fee, effective_date, end_date, status, created_at, updated_at

BillingScopeAssignment
  id, fee_schedule_id, scope_type (account | client | household),
  scope_entity_id, override_fee_schedule_id (nullable), exclusion (boolean),
  effective_date, end_date, created_at

BillingCalendar
  id, firm_id, frequency, period_start, period_end, billing_date,
  status (upcoming | open | frozen | closed), created_at

BillingRun
  id, firm_id, billing_calendar_id, status (calculated | pending_review |
  approved | posted | failed | reversed), initiated_by, approved_by,
  posted_by, schedule_snapshot (jsonb), total_fees, invoice_count,
  idempotency_key, created_at, approved_at, posted_at

Invoice
  id, billing_run_id, scope_type, scope_entity_id, fee_schedule_id,
  period_start, period_end, aum_value, calculated_fee, adjustments,
  final_fee, pro_rata_factor, min_fee_applied, max_fee_applied,
  status (calculated | approved | posted | reversed), reversal_of_id (nullable),
  reversed_by_id (nullable), created_at

InvoiceLineItem
  id, invoice_id, account_id, aum_value, calculated_fee, tier_detail (jsonb),
  pro_rata_factor, notes

BillingException
  id, billing_run_id, invoice_id (nullable), account_id (nullable),
  exception_type, description, resolution_status (open | resolved | waived),
  resolved_by, resolved_at, notes, created_at

BillingAuditEntry
  id, firm_id, entity_type, entity_id, action, actor_id, detail (jsonb),
  created_at
```

## API Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/fee-schedules` | Create fee schedule |
| GET | `/api/fee-schedules` | List fee schedules for firm |
| GET | `/api/fee-schedules/:id` | Get fee schedule detail |
| PUT | `/api/fee-schedules/:id` | Update fee schedule |
| POST | `/api/fee-schedules/:id/archive` | Archive fee schedule |
| POST | `/api/billing-scope-assignments` | Assign billing scope |
| GET | `/api/billing-scope-assignments` | List scope assignments |
| DELETE | `/api/billing-scope-assignments/:id` | Remove scope assignment |
| POST | `/api/billing-calendars` | Create billing calendar periods |
| GET | `/api/billing-calendars` | List billing calendar periods |
| POST | `/api/billing-runs` | Initiate billing run (freeze + calculate) |
| GET | `/api/billing-runs/:id` | Get billing run detail with invoices |
| POST | `/api/billing-runs/:id/approve` | Approve billing run |
| POST | `/api/billing-runs/:id/post` | Post billing run (creates ledger entries) |
| GET | `/api/invoices` | List invoices (filterable) |
| GET | `/api/invoices/:id` | Get invoice detail with line items |
| POST | `/api/invoices/:id/reverse` | Reverse posted invoice (compensating record) |
| GET | `/api/billing-exceptions` | List billing exceptions |
| POST | `/api/billing-exceptions/:id/resolve` | Resolve a billing exception |

## Billing Run Lifecycle

```
Schedule Freeze
  └─→ Calculation (snapshot schedules, compute fees per scope)
       └─→ Status: calculated
            └─→ Review (advisor/billing_admin inspects invoices)
                 └─→ Status: pending_review
                      └─→ Approve (billing_admin confirms)
                           └─→ Status: approved
                                └─→ Post (creates ledger debit entries in Epic 11)
                                     └─→ Status: posted
```

Each transition is an explicit command endpoint. No auto-advancement.

## Domain Events

- `billing_run.calculated`
- `billing_run.approved`
- `billing_run.posted`
- `billing_run.failed`
- `invoice.reversed`
- `billing_exception.created`
- `billing_exception.resolved`

---

## Issues

---

### Issue 12.1: Fee Schedule Definition

**Description**

Implement the `FeeSchedule` entity and CRUD endpoints. Fee schedules define how fees are calculated for a given assignment. The system must support multiple fee types (AUM percentage, flat fee, tiered, per-account) and multiple billing frequencies (monthly, quarterly, semi-annually, annually). Fee schedules are versioned by effective date and can be archived but never hard-deleted.

**Scope**

- `FeeSchedule` table with columns: id, firm_id, name, description, fee_type enum (aum_percentage, flat, tiered, per_account), frequency enum (monthly, quarterly, semi_annually, annually), aum_bps (nullable), flat_amount (nullable), per_account_amount (nullable), tiers jsonb (nullable, array of `{lower_bound, upper_bound, bps}`), min_fee (nullable), max_fee (nullable), effective_date, end_date (nullable), status (active, archived), created_at, updated_at.
- Zod schemas for creation and update validation, including tier structure validation (no gaps, no overlaps, ascending bounds).
- `POST /api/fee-schedules` -- create a new fee schedule; requires `billing.manage` permission.
- `GET /api/fee-schedules` -- list fee schedules for the current firm, filterable by status and fee_type.
- `GET /api/fee-schedules/:id` -- return full fee schedule detail including tier breakdown.
- `PUT /api/fee-schedules/:id` -- update a fee schedule; only allowed if no in-progress billing run references it.
- `POST /api/fee-schedules/:id/archive` -- soft archive; prevents future assignment but preserves historical reference.
- Repository layer with tenant-scoped queries.
- Audit entry on every create, update, and archive action.

**Acceptance Criteria**

- [ ] Fee schedules can be created with any supported fee_type and frequency combination.
- [ ] Tiered schedules validate that tiers have no gaps and no overlapping bounds.
- [ ] Fee schedules with fee_type `aum_percentage` require aum_bps; `flat` requires flat_amount; `per_account` requires per_account_amount; `tiered` requires a non-empty tiers array.
- [ ] Archived fee schedules cannot be assigned to new billing scope assignments.
- [ ] Fee schedules referenced by an in-progress billing run cannot be updated.
- [ ] All mutations emit a BillingAuditEntry.
- [ ] Endpoints enforce `billing.manage` permission.
- [ ] All responses follow the platform error envelope for validation failures.

**Dependencies**

- Epic 1 (tenant context, permissions)

---

### Issue 12.2: Billing Scope Configuration

**Description**

Implement billing scope assignments that link fee schedules to billable entities at the account, client, or household level. Scoping determines what gets billed during a billing run and which fee schedule applies. Support per-entity overrides and exclusions.

**Scope**

- `BillingScopeAssignment` table: id, fee_schedule_id (FK), scope_type enum (account, client, household), scope_entity_id (polymorphic reference), override_fee_schedule_id (nullable FK, allows per-entity override), exclusion boolean (default false), effective_date, end_date (nullable), created_at.
- `POST /api/billing-scope-assignments` -- assign a fee schedule to a scope entity; requires `billing.manage`.
- `GET /api/billing-scope-assignments` -- list assignments, filterable by scope_type, fee_schedule_id, and scope_entity_id.
- `DELETE /api/billing-scope-assignments/:id` -- remove an assignment (soft delete via end_date).
- Validate that scope_entity_id references a valid account, client, or household in the firm.
- Validate that overlapping assignments for the same scope entity do not create ambiguous billing (one active assignment per entity per period).
- When scope_type is `household`, the billing run must aggregate all accounts in the household. When `client`, aggregate all accounts for the client.
- Audit entry on every assignment change.

**Acceptance Criteria**

- [ ] Assignments can be created at account, client, or household level.
- [ ] An exclusion flag on an assignment prevents the entity from being billed even if a parent scope would include it.
- [ ] Override fee schedule takes precedence over the default schedule for that entity.
- [ ] Duplicate active assignments for the same scope entity and overlapping date range are rejected with a validation error.
- [ ] Deletion sets end_date rather than removing the row.
- [ ] scope_entity_id is validated against the corresponding registry table (accounts, clients, households).
- [ ] All mutations emit a BillingAuditEntry.

**Dependencies**

- Issue 12.1 (fee schedules must exist)
- Epic 2 (account, client, household entities must exist)

---

### Issue 12.3: Billing Calendar and Period Management

**Description**

Implement billing calendar management. Billing calendars define the periods over which fees are calculated. Each firm has calendar periods auto-generated or manually created based on their fee schedule frequencies. Calendar periods have a lifecycle: upcoming, open, frozen (locked for billing run), closed (run completed).

**Scope**

- `BillingCalendar` table: id, firm_id, frequency enum, period_start date, period_end date, billing_date date, status enum (upcoming, open, frozen, closed), created_at.
- `POST /api/billing-calendars` -- create calendar periods (bulk creation for a given frequency and date range). Requires `billing.manage`.
- `GET /api/billing-calendars` -- list calendar periods for the firm, filterable by frequency, status, and date range.
- Service logic to auto-generate upcoming calendar periods based on configured frequencies.
- Status transitions: upcoming -> open (when period_start is reached), open -> frozen (when billing run is initiated), frozen -> closed (when billing run is posted).
- Frozen calendars cannot be modified. This ensures the billing run operates on a stable period definition.
- Prevent creation of overlapping calendar periods for the same frequency.

**Acceptance Criteria**

- [ ] Calendar periods can be created for monthly, quarterly, semi-annual, and annual frequencies.
- [ ] Overlapping periods for the same frequency within a firm are rejected.
- [ ] A calendar period transitions to `frozen` when a billing run is initiated against it.
- [ ] Frozen and closed calendar periods cannot be modified or deleted.
- [ ] Calendar periods can be listed and filtered by frequency, status, and date range.
- [ ] Auto-generation service can create upcoming periods up to a configurable horizon.

**Dependencies**

- Epic 1 (tenant context)

---

### Issue 12.4: Billing Run Generation

**Description**

Implement the billing run initiation flow: freeze the calendar period, snapshot all active fee schedules and scope assignments, calculate fees for every in-scope entity, and produce invoices with line items. The billing run is the central orchestration record for a billing cycle.

**Scope**

- `BillingRun` table: id, firm_id, billing_calendar_id (FK), status enum (calculated, pending_review, approved, posted, failed, reversed), initiated_by (FK to user), approved_by (nullable), posted_by (nullable), schedule_snapshot jsonb (frozen copy of fee schedules and scope assignments used), total_fees decimal, invoice_count integer, idempotency_key (unique), error_detail (nullable), created_at, approved_at (nullable), posted_at (nullable).
- `POST /api/billing-runs` -- initiate a billing run. Accepts billing_calendar_id and idempotency_key. Requires `billing.manage`.
- Processing pipeline:
  1. Validate that the calendar period is `open` or `upcoming`; transition to `frozen`.
  2. Snapshot all active fee schedules and scope assignments as of the period end date; store in schedule_snapshot.
  3. Resolve all billable entities (expand household and client scopes to their constituent accounts).
  4. For each billable entity, retrieve AUM values as of period end from the ledger/balance projections (Epic 11).
  5. Calculate fee for each entity using the applicable fee schedule (applying tiers, pro-ration, min/max caps as needed).
  6. Create Invoice and InvoiceLineItem records for each entity.
  7. Aggregate totals on the BillingRun record.
  8. Set status to `calculated`.
  9. If any entity produces an exception (missing AUM, invalid schedule, etc.), create a BillingException and continue processing remaining entities.
- Idempotency: reject duplicate billing run for the same calendar period unless the prior run is in `failed` or `reversed` status.
- Emit `billing_run.calculated` event on success.
- Emit `billing_run.failed` event on catastrophic failure.

**Acceptance Criteria**

- [ ] A billing run freezes the calendar period on initiation.
- [ ] Fee schedules and scope assignments are snapshotted and stored on the run record.
- [ ] Invoices are generated for all in-scope entities with correct fee calculations.
- [ ] AUM values are sourced from Epic 11 balance projections as of period end.
- [ ] Tiered fee calculations correctly apply breakpoint tiers.
- [ ] A billing run for an already-frozen or closed calendar period is rejected.
- [ ] Duplicate billing runs for the same period are blocked by idempotency_key.
- [ ] Entities that produce calculation exceptions result in BillingException records, not run failure.
- [ ] The billing run record includes total_fees and invoice_count aggregates.
- [ ] `billing_run.calculated` event is emitted on success.
- [ ] The endpoint requires `billing.manage` permission.

**Dependencies**

- Issue 12.1 (fee schedules)
- Issue 12.2 (scope assignments)
- Issue 12.3 (calendar periods)
- Epic 11 (AUM balance values)

---

### Issue 12.5: Billing Run Review and Approval Workflow

**Description**

Implement the review and approval workflow for billing runs. After calculation, a billing run must be explicitly reviewed and approved before it can be posted. This is a compliance-critical gate that prevents incorrect fees from being debited.

**Scope**

- `GET /api/billing-runs/:id` -- return full billing run detail including status, schedule snapshot, invoices summary, exceptions summary, and totals.
- `POST /api/billing-runs/:id/approve` -- transition from `calculated` or `pending_review` to `approved`. Requires `billing.approve` permission. Records approved_by and approved_at.
- Add a `pending_review` status transition: the billing run moves to `pending_review` when a reviewer first opens or explicitly marks it for review (optional intermediate step; approval can also happen directly from `calculated`).
- Approval is only valid if all blocking exceptions are resolved (non-blocking exceptions may remain).
- If the firm has configured an approval policy (from Epic 3), validate against it (e.g., require a different user than the initiator, require billing_admin role).
- Emit `billing_run.approved` event.
- Reject approval if the billing run status is not `calculated` or `pending_review`.
- Return `INVALID_WORKFLOW_STATE` error for invalid transitions.

**Acceptance Criteria**

- [ ] A billing run can be approved from `calculated` or `pending_review` status.
- [ ] Approval from any other status returns `INVALID_WORKFLOW_STATE`.
- [ ] The approve endpoint requires `billing.approve` permission.
- [ ] approved_by and approved_at are recorded on the billing run.
- [ ] If an approval policy is configured, it is enforced (e.g., four-eyes principle: approver differs from initiator).
- [ ] Billing runs with unresolved blocking exceptions cannot be approved.
- [ ] `billing_run.approved` event is emitted.
- [ ] The GET endpoint returns sufficient detail for a reviewer to make an informed decision (invoices, line items, exceptions, totals, schedule snapshot).

**Dependencies**

- Issue 12.4 (billing run must be calculated first)
- Epic 3 (approval policy primitives)

---

### Issue 12.6: Invoice Creation and Detail

**Description**

Implement the Invoice and InvoiceLineItem entities and their read endpoints. Invoices are created during billing run generation (Issue 12.4) but need dedicated query and detail endpoints for review, audit, and client communication.

**Scope**

- `Invoice` table: id, billing_run_id (FK), scope_type, scope_entity_id, fee_schedule_id (FK), period_start, period_end, aum_value decimal, calculated_fee decimal, adjustments decimal (default 0), final_fee decimal, pro_rata_factor decimal (default 1.0), min_fee_applied boolean (default false), max_fee_applied boolean (default false), status enum (calculated, approved, posted, reversed), reversal_of_id (nullable FK self-ref), reversed_by_id (nullable FK self-ref), created_at.
- `InvoiceLineItem` table: id, invoice_id (FK), account_id (FK), aum_value decimal, calculated_fee decimal, tier_detail jsonb (nullable), pro_rata_factor decimal, notes text (nullable).
- `GET /api/invoices` -- list invoices filterable by billing_run_id, scope_type, scope_entity_id, status, and date range. Requires `billing.read`.
- `GET /api/invoices/:id` -- return full invoice detail including all line items. Requires `billing.read`.
- Invoice status mirrors the billing run lifecycle: when the run is approved, all its invoices transition to `approved`; when posted, all transition to `posted`.
- Invoices are immutable after creation. Corrections are handled by reversal (Issue 12.8), not by editing existing invoices.

**Acceptance Criteria**

- [ ] Invoices are created as part of billing run generation with all required fields populated.
- [ ] Each invoice has one or more line items breaking down fees by account.
- [ ] Household-scope and client-scope invoices aggregate line items from all constituent accounts.
- [ ] Invoice status transitions follow the parent billing run's lifecycle.
- [ ] Invoices are queryable by billing run, scope entity, status, and date range.
- [ ] Invoice detail endpoint returns complete line item breakdown including tier detail.
- [ ] Invoices cannot be edited after creation; the only mutation path is reversal.
- [ ] Endpoints enforce `billing.read` permission.

**Dependencies**

- Issue 12.4 (invoices are created during billing run generation)

---

### Issue 12.7: Fee Posting

**Description**

Implement the posting step that transitions an approved billing run to `posted` and creates corresponding fee debit ledger entries in Epic 11. Posting is the point at which billing affects cash balances. This is an irreversible forward action; corrections after posting require reversal (Issue 12.8).

**Scope**

- `POST /api/billing-runs/:id/post` -- transition from `approved` to `posted`. Requires `billing.post` permission. Accepts an idempotency_key.
- For each invoice in the billing run:
  1. Create a fee debit ledger entry in the cash ledger (Epic 11) for each account referenced in the invoice line items.
  2. The ledger entry must reference the invoice_id and billing_run_id for traceability.
  3. Transition the invoice status to `posted`.
- If any ledger debit fails (e.g., insufficient information, ledger service unavailable), the entire posting operation must fail atomically -- no partial posts.
- Record posted_by and posted_at on the billing run.
- Transition the billing calendar period to `closed`.
- Emit `billing_run.posted` event.
- Posting is idempotent: re-posting an already-posted run with the same idempotency_key returns success without creating duplicate ledger entries.

**Acceptance Criteria**

- [ ] Posting is only allowed from `approved` status; other statuses return `INVALID_WORKFLOW_STATE`.
- [ ] A fee debit ledger entry is created in Epic 11 for each account line item in the billing run.
- [ ] Ledger entries reference the invoice_id and billing_run_id.
- [ ] Posting is atomic: if any ledger debit fails, the entire run remains in `approved` and no partial debits are created.
- [ ] posted_by and posted_at are recorded.
- [ ] The billing calendar period transitions to `closed` on successful posting.
- [ ] `billing_run.posted` event is emitted.
- [ ] Idempotent re-posting returns success without duplicate ledger entries.
- [ ] The endpoint requires `billing.post` permission and an idempotency_key.
- [ ] A BillingAuditEntry is created for the posting action.

**Dependencies**

- Issue 12.5 (billing run must be approved)
- Issue 12.6 (invoices with line items must exist)
- Epic 11 (ledger entry creation API for fee debits)

---

### Issue 12.8: Billing Reversal and Correction

**Description**

Implement invoice reversal via compensating records. Reversals never delete or mutate existing invoice or ledger records. Instead, a reversal creates a new compensating invoice with negated amounts and a corresponding credit ledger entry. This preserves the full audit trail and maintains ledger integrity.

**Scope**

- `POST /api/invoices/:id/reverse` -- reverse a posted invoice. Requires `billing.post` permission. Accepts a reason (required) and idempotency_key.
- Reversal process:
  1. Validate the invoice status is `posted`. Reject if already reversed or not yet posted.
  2. Create a new Invoice record with all the same fields but negated fee amounts. Set `reversal_of_id` to the original invoice id.
  3. Set `reversed_by_id` on the original invoice to point to the new compensating invoice.
  4. Transition the original invoice status to `reversed`.
  5. Create compensating (credit) ledger entries in Epic 11 for each account, referencing the reversal invoice.
  6. Create a BillingAuditEntry capturing the reversal reason, actor, and both invoice IDs.
- Corrections after reversal: to bill the correct amount, a new billing run or manual adjustment invoice must be created. Reversed invoices are not re-opened.
- Emit `invoice.reversed` event.
- If the billing run has all invoices reversed, the billing run status transitions to `reversed` and the calendar period can be re-opened for a corrective run.

**Acceptance Criteria**

- [ ] Only posted invoices can be reversed; other statuses return a validation error.
- [ ] Reversal creates a new compensating invoice with negated amounts, not a mutation of the original.
- [ ] The original invoice is marked `reversed` with a reference to the compensating invoice.
- [ ] The compensating invoice references the original via `reversal_of_id`.
- [ ] Compensating credit ledger entries are created in Epic 11 for each account.
- [ ] A reason is required for every reversal.
- [ ] Reversal is idempotent: reversing an already-reversed invoice returns the existing compensating invoice.
- [ ] If all invoices in a billing run are reversed, the run status becomes `reversed`.
- [ ] When a billing run is fully reversed, the calendar period can be re-opened.
- [ ] `invoice.reversed` event is emitted.
- [ ] A BillingAuditEntry records the reversal with full detail.
- [ ] No existing invoice or ledger records are modified (only status flags and reference pointers are set on the original).

**Dependencies**

- Issue 12.6 (invoice records)
- Issue 12.7 (posting must have occurred)
- Epic 11 (credit ledger entries)

---

### Issue 12.9: Billing Exception Handling

**Description**

Implement billing exception capture, surfacing, and resolution. Exceptions arise during billing run generation when an entity cannot be billed correctly (missing AUM data, invalid fee schedule configuration, account in restricted status, etc.). Exceptions must be durable, visible, and resolvable without requiring a full re-run.

**Scope**

- `BillingException` table: id, billing_run_id (FK), invoice_id (nullable FK), account_id (nullable FK), exception_type enum (missing_aum, invalid_schedule, restricted_account, calculation_error, other), severity enum (blocking, non_blocking), description text, resolution_status enum (open, resolved, waived), resolved_by (nullable FK), resolved_at (nullable), resolution_notes text (nullable), created_at.
- Exceptions are created during billing run generation (Issue 12.4) when an entity cannot be processed.
- `GET /api/billing-exceptions` -- list exceptions filterable by billing_run_id, exception_type, severity, and resolution_status. Requires `billing.read`.
- `POST /api/billing-exceptions/:id/resolve` -- resolve or waive an exception. Accepts resolution_status (resolved or waived) and resolution_notes. Requires `billing.manage`.
- Blocking exceptions prevent billing run approval (Issue 12.5). Non-blocking exceptions are informational and do not block approval.
- Resolved exceptions record the resolving user and timestamp.
- Audit entry on exception creation and resolution.

**Acceptance Criteria**

- [ ] Exceptions are automatically created during billing run generation for entities that cannot be billed.
- [ ] Each exception has a type, severity, and description.
- [ ] Blocking exceptions prevent billing run approval until resolved or waived.
- [ ] Non-blocking exceptions do not prevent approval.
- [ ] Exceptions can be resolved or waived with notes.
- [ ] Resolution records the actor and timestamp.
- [ ] Exceptions are queryable by billing run, type, severity, and resolution status.
- [ ] All exception lifecycle changes are audit-logged.

**Dependencies**

- Issue 12.4 (exceptions are generated during billing runs)
- Issue 12.5 (blocking exceptions gate approval)

---

### Issue 12.10: Pro-Ration for Mid-Period Account Additions

**Description**

Implement pro-rata fee calculation for accounts that are added or activated mid-way through a billing period. An account that was active for only a portion of the billing period should be billed proportionally based on the number of days active relative to the total period length.

**Scope**

- During billing run calculation (Issue 12.4), for each account, determine the account activation date.
- If the activation date falls after the billing period start date, calculate a pro_rata_factor = (days_active_in_period / total_days_in_period).
- Apply the pro_rata_factor to the calculated fee for that account's InvoiceLineItem.
- Store the pro_rata_factor on the InvoiceLineItem record.
- If the account was active for the entire period, pro_rata_factor is 1.0.
- Similarly handle accounts closed mid-period: pro-rate based on days active before closure.
- The pro_rata_factor rolls up to the Invoice level as a weighted factor when aggregating across accounts.
- Document the pro-ration method (daily basis, actual/actual day count).

**Acceptance Criteria**

- [ ] Accounts activated mid-period are billed only for the portion of the period they were active.
- [ ] Accounts closed mid-period are billed only for the portion of the period they were active.
- [ ] pro_rata_factor is correctly calculated as days_active / total_period_days.
- [ ] pro_rata_factor is stored on each InvoiceLineItem.
- [ ] A full-period account has pro_rata_factor = 1.0.
- [ ] Pro-rated fees interact correctly with tiered schedules (pro-rate the final fee, not the tier boundaries).
- [ ] Pro-rated fees interact correctly with min/max caps (caps are applied to the pro-rated fee).
- [ ] The calculation uses actual/actual day count convention.

**Dependencies**

- Issue 12.4 (integrated into billing run calculation)
- Epic 2 (account activation and closure dates)

---

### Issue 12.11: Minimum and Maximum Fee Caps

**Description**

Implement minimum and maximum fee enforcement on fee schedules. After the base fee is calculated (including any tiered computation and pro-ration), the system must clamp the result to the configured min_fee and max_fee bounds if they are set on the fee schedule.

**Scope**

- During billing run calculation (Issue 12.4), after computing the base fee for an entity:
  1. If the fee schedule has a min_fee and the calculated fee is below it, set the final fee to min_fee and flag `min_fee_applied = true` on the Invoice.
  2. If the fee schedule has a max_fee and the calculated fee is above it, set the final fee to max_fee and flag `max_fee_applied = true` on the Invoice.
  3. If both min and max are set, min is evaluated first, then max (min cannot exceed max -- validate at fee schedule creation).
- Caps apply at the invoice scope level (i.e., household-level billing applies the cap to the household aggregate, not per-account).
- For pro-rated periods, caps are also pro-rated: effective_min = min_fee * pro_rata_factor; effective_max = max_fee * pro_rata_factor.
- Fee schedule validation (Issue 12.1) must enforce that min_fee <= max_fee when both are provided.

**Acceptance Criteria**

- [ ] Fees below the min_fee are raised to the min_fee value.
- [ ] Fees above the max_fee are reduced to the max_fee value.
- [ ] min_fee_applied and max_fee_applied flags are set on the Invoice when caps are triggered.
- [ ] Fee schedule creation rejects min_fee > max_fee.
- [ ] Caps are applied at the invoice scope level (household/client/account).
- [ ] For pro-rated periods, min and max caps are proportionally adjusted.
- [ ] Cap application is reflected in the InvoiceLineItem and Invoice records for transparency.

**Dependencies**

- Issue 12.1 (min_fee, max_fee on fee schedule)
- Issue 12.4 (integrated into billing run calculation)
- Issue 12.10 (pro-ration interaction)

---

### Issue 12.12: Billing Audit Trail

**Description**

Implement a comprehensive, append-only audit trail for all billing operations. Every mutation across the billing lifecycle must be recorded with the actor, action, timestamp, and relevant detail. This is a compliance requirement for fee billing in an RIA platform.

**Scope**

- `BillingAuditEntry` table: id, firm_id, entity_type enum (fee_schedule, scope_assignment, calendar, billing_run, invoice, exception), entity_id uuid, action enum (created, updated, archived, assigned, unassigned, calculated, approved, posted, reversed, exception_created, exception_resolved, exception_waived), actor_id uuid, detail jsonb (action-specific payload), created_at.
- Audit entries are append-only. No updates or deletes.
- Every service method across Issues 12.1 through 12.11 must emit audit entries for mutations.
- `GET /api/billing-audit` -- query audit entries by firm_id, entity_type, entity_id, action, actor_id, and date range. Requires `billing.read` permission. Paginated.
- Audit entries must also be emittable to the platform-wide AuditEvent system (Epic 16) for cross-domain querying.
- Include sufficient detail in the jsonb payload to reconstruct what changed (e.g., for fee schedule update: old and new values; for approval: the approver and run totals; for reversal: the reason and compensating invoice ID).

**Acceptance Criteria**

- [ ] Every billing mutation (create, update, archive, assign, calculate, approve, post, reverse, exception resolve/waive) produces an audit entry.
- [ ] Audit entries are append-only; the table does not support UPDATE or DELETE operations.
- [ ] Each entry records actor_id, timestamp, entity reference, action, and a detail payload.
- [ ] Audit entries are queryable by entity_type, entity_id, action, actor_id, and date range.
- [ ] The detail payload contains enough information to understand the change without querying the source record.
- [ ] Audit entries integrate with the platform-wide audit system (Epic 16).
- [ ] The query endpoint is paginated and requires `billing.read` permission.
- [ ] No billing operation across Issues 12.1 through 12.11 can succeed without producing its corresponding audit entry (transactional guarantee).

**Dependencies**

- All prior issues in Epic 12 (audit is cross-cutting)
- Epic 16 (platform audit event integration)
