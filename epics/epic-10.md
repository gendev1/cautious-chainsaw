# Epic 10: Portfolio Models and Rebalancing

## Goal

Build model portfolio management, account-to-model assignment, drift monitoring, and rebalance proposal generation with a clear separation between proposal creation and trade execution. A RebalanceProposal is a decision-support artifact. Releasing a proposal emits OrderIntents that flow through the normal order lifecycle defined in Epic 9.

## Dependencies

- Epic 2 (Client, Household, and Account Registry) -- account and household graph
- Epic 4 (External Service Integration Framework) -- security master lookups, Kafka event publishing
- Epic 8 (Advisor Portal Experience) -- advisor-facing views for models and proposals
- Epic 9 (Orders, OMS/EMS Integration, and Trade Status) -- OrderIntent creation and submission pipeline

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/models` | Create a model portfolio |
| GET | `/api/models` | List model portfolios (firm-owned and marketplace) |
| GET | `/api/models/:id` | Get model portfolio detail |
| PUT | `/api/models/:id` | Update a model portfolio |
| DELETE | `/api/models/:id` | Soft-delete a model portfolio |
| POST | `/api/model-assignments` | Assign a model to an account |
| GET | `/api/model-assignments` | List model assignments (filterable by account, model) |
| DELETE | `/api/model-assignments/:id` | Remove a model assignment |
| POST | `/api/rebalance-proposals` | Generate a rebalance proposal |
| GET | `/api/rebalance-proposals` | List proposals (filterable by account, model, status) |
| GET | `/api/rebalance-proposals/:id` | Get proposal detail including generated trades |
| POST | `/api/rebalance-proposals/:id/release` | Release proposal, emitting OrderIntents |
| POST | `/api/rebalance-proposals/:id/cancel` | Cancel a pending proposal |
| GET | `/api/rebalance-rules` | Get rebalance rule configuration for an account or model |
| PUT | `/api/rebalance-rules/:id` | Update rebalance rule configuration |
| GET | `/api/marketplace/models` | Browse marketplace models (read-only) |
| POST | `/api/marketplace/models/:id/subscribe` | Subscribe to a marketplace model |

## Domain Events

- `model_portfolio.created`
- `model_portfolio.updated`
- `model_portfolio.deleted`
- `model_assignment.created`
- `model_assignment.removed`
- `rebalance_proposal.generated`
- `rebalance_proposal.released`
- `rebalance_proposal.cancelled`
- `drift_threshold.breached`
- `marketplace_model.subscribed`

---

## Issue 10-1: ModelPortfolio CRUD

### Title

Implement ModelPortfolio create, read, update, and delete operations

### Description

Build the foundational CRUD layer for firm-owned model portfolios. A ModelPortfolio defines a named set of target allocations (security or asset class to target weight percentage). Models are tenant-scoped and versioned so that historical assignments and proposals can reference the exact model definition that was active at the time.

### Scope

- Postgres schema: `model_portfolios` table with columns for id, tenant_id, name, description, status (active, archived), version, created_by, created_at, updated_at
- Postgres schema: `model_portfolio_allocations` table with columns for id, model_portfolio_id, security_id (nullable), asset_class (nullable), target_weight_pct, min_weight_pct (nullable), max_weight_pct (nullable)
- Zod request/response schemas for model creation and update
- Service layer: create model with allocations (must sum to 100%), update model (creates new version), soft-delete (only if no active assignments), list with pagination and filtering
- Routes: POST /api/models, GET /api/models, GET /api/models/:id, PUT /api/models/:id, DELETE /api/models/:id
- Emit `model_portfolio.created`, `model_portfolio.updated`, `model_portfolio.deleted` domain events
- Permission guard: `model.create`, `model.update`, `model.delete`, `model.read`
- Audit event emission on all mutations

### Acceptance Criteria

- POST /api/models creates a model with target allocations; allocations must sum to exactly 100%
- Validation rejects models with duplicate securities or overlapping asset class entries
- PUT /api/models/:id creates a new version of the model; prior version is retained for historical reference
- DELETE /api/models/:id returns 409 Conflict if active model assignments exist
- DELETE /api/models/:id soft-deletes (sets status to archived) when no active assignments reference the model
- GET /api/models returns paginated list filtered by status, with allocation details included
- GET /api/models/:id returns full model with current allocations and version history
- All mutations emit audit events with actor, tenant, and resource identifiers
- All mutations require appropriate permissions; unauthorized requests return 403

### Dependencies

- Epic 1 (authentication, authorization, tenant context)
- Epic 4 (security master integration for security_id validation)

---

## Issue 10-2: ModelAssignment -- Assign Model to Account

### Title

Implement model-to-account assignment and assignment history tracking

### Description

Allow advisors to assign a ModelPortfolio to an account. An account may have at most one active model assignment at a time. Assigning a new model to an account that already has an assignment replaces the prior assignment (setting its end date) and creates a new assignment record. The full assignment history is retained for audit and compliance.

### Scope

- Postgres schema: `model_assignments` table with columns for id, tenant_id, account_id, model_portfolio_id, model_version, assigned_by, assigned_at, ended_at, end_reason (replaced, removed, account_closed)
- Service layer: create assignment (end any existing active assignment for the account), remove assignment, list assignments by account or model
- Routes: POST /api/model-assignments, GET /api/model-assignments, DELETE /api/model-assignments/:id
- Validate that the referenced account exists and is in an active state
- Validate that the referenced model exists and is active
- Emit `model_assignment.created` and `model_assignment.removed` domain events
- Permission guard: `model_assignment.create`, `model_assignment.remove`, `model_assignment.read`
- Audit event emission on all mutations

### Acceptance Criteria

- POST /api/model-assignments creates a new assignment linking an account to a model at its current version
- If the account already has an active assignment, the existing assignment is ended with reason "replaced" and a new one is created atomically
- DELETE /api/model-assignments/:id sets ended_at and end_reason to "removed"; does not hard-delete
- GET /api/model-assignments supports filtering by account_id and model_portfolio_id and includes both active and historical assignments
- Creating an assignment for a non-existent or non-active account returns 422
- Creating an assignment for an archived model returns 422
- Assignment records reference the specific model version at time of assignment
- All mutations emit audit events

### Dependencies

- Issue 10-1 (ModelPortfolio CRUD)
- Epic 2 (Account registry for account validation)

---

## Issue 10-3: Drift Monitoring

### Title

Implement portfolio drift detection against model targets with configurable thresholds

### Description

Build a drift monitoring capability that compares an account's current holdings projection against its assigned model's target allocations. Drift is calculated as the absolute difference between the current weight and target weight for each allocation. When any position's drift exceeds the configured threshold, a `drift_threshold.breached` event is emitted. Drift checks can be triggered on-demand via the proposal generation flow or run periodically as a background job.

### Scope

- Drift calculation service: accept account holdings (from holdings projection or external source), model target allocations, and compute per-position drift (absolute and relative)
- Drift result type: array of { security_id, asset_class, target_weight_pct, current_weight_pct, drift_pct, exceeds_threshold }
- Integration with holdings projection (or external portfolio data service) to retrieve current account positions and market values
- Background worker (Kafka consumer or scheduled job) to evaluate drift for all accounts with active model assignments
- Redis cache for last-computed drift per account to avoid redundant recomputation within a configurable window
- Emit `drift_threshold.breached` event when any position exceeds the account's or model's configured threshold
- Drift results are ephemeral computations, not persisted as source-of-truth records; they serve as inputs to proposal generation

### Acceptance Criteria

- Drift calculation correctly computes per-position absolute drift given holdings and model targets
- Positions not in the model (overweight) and model allocations with no holding (underweight) are both reported
- Cash position is handled explicitly (either as a model allocation or as residual)
- Drift check uses the account's configured threshold (from rebalance rules, Issue 10-11) or falls back to a system default
- `drift_threshold.breached` event includes account_id, model_id, and the positions that exceeded the threshold
- Background drift evaluation processes accounts in batches without blocking HTTP traffic
- Drift computation is idempotent; running it twice with the same inputs produces the same result
- Stale or unavailable holdings data causes the drift check to skip the account and log a warning, not fail the batch

### Dependencies

- Issue 10-1 (ModelPortfolio for target allocations)
- Issue 10-2 (ModelAssignment to know which model applies)
- Epic 4 (external service framework for holdings/pricing data)
- Epic 11 (cash and balance projections, soft dependency -- can stub initially)

---

## Issue 10-4: RebalanceProposal Generation

### Title

Implement rebalance proposal generation capturing exact assumptions and proposed trades

### Description

Build the internal platform logic that generates a RebalanceProposal for an account. Given an account with an active model assignment, the system computes the set of proposed buy and sell orders needed to bring the account in line with the model's target allocations. The proposal captures a complete snapshot of all assumptions used at generation time (holdings, prices, model targets, cash available, restrictions) so the proposal is reproducible and auditable. A proposal is a decision-support artifact -- it is NOT an executed trade.

### Scope

- Postgres schema: `rebalance_proposals` table with columns for id, tenant_id, account_id, model_portfolio_id, model_version, status (draft, pending_review, released, cancelled, expired), generated_by, generated_at, released_by, released_at, cancelled_by, cancelled_at, expires_at, idempotency_key
- Postgres schema: `rebalance_proposal_assumptions` table capturing the frozen inputs: holdings snapshot (JSON), prices snapshot (JSON), model_targets snapshot (JSON), cash_available, restrictions (JSON), drift_summary (JSON)
- Postgres schema: `rebalance_proposal_trades` table with columns for id, proposal_id, security_id, side (buy/sell), quantity, estimated_amount, rationale
- Service layer: generate proposal from account_id (resolves active model assignment, fetches current holdings, fetches current prices, computes trades, persists proposal with assumptions)
- Route: POST /api/rebalance-proposals (accepts account_id, optional parameters like cash_target)
- Rebalance algorithm: minimize-trade approach -- sell overweight positions, buy underweight positions, respect minimum trade thresholds, respect cash reserve requirements
- Idempotency: accept idempotency_key to prevent duplicate proposal generation
- Emit `rebalance_proposal.generated` domain event
- Permission guard: `rebalance_proposal.create`

### Acceptance Criteria

- POST /api/rebalance-proposals generates a proposal for the given account with status "pending_review"
- Proposal fails with 422 if the account has no active model assignment
- Proposal fails with 422 if current holdings or pricing data is unavailable or stale beyond a configurable threshold
- The assumptions table stores a complete, immutable snapshot of all inputs used at generation time
- Proposed trades correctly move the portfolio toward model targets (sells overweight, buys underweight)
- Proposed trades respect a configurable minimum trade amount to avoid trivial orders
- Cash reserve is respected: the proposal does not deploy cash below the configured cash target
- Positions flagged with restrictions (e.g., do-not-sell) are excluded from sell trades
- The proposal includes a per-trade rationale string (e.g., "Sell: overweight by 3.2% vs target")
- Duplicate requests with the same idempotency_key return the existing proposal
- `rebalance_proposal.generated` event is emitted with proposal_id and account_id
- Proposal generation does NOT create OrderIntents or trigger any trade execution

### Dependencies

- Issue 10-1 (ModelPortfolio)
- Issue 10-2 (ModelAssignment)
- Issue 10-3 (Drift monitoring logic, reused for drift computation)
- Epic 4 (pricing and holdings data from external services)
- Epic 9 (OrderIntent schema awareness, but no direct creation at this stage)

---

## Issue 10-5: Proposal Review and Release Workflow

### Title

Implement proposal release flow that emits OrderIntents through the normal order pipeline

### Description

Build the release workflow for a RebalanceProposal. An advisor reviews a pending proposal and, upon approval, releases it. The release action transitions the proposal to "released" status and emits one OrderIntent per proposed trade. These OrderIntents then follow the standard order lifecycle defined in Epic 9 (validation, OMS submission, execution tracking). The release endpoint is a separate, explicit command -- it is never triggered automatically by proposal generation.

### Scope

- Route: POST /api/rebalance-proposals/:id/release
- Service layer: validate proposal is in "pending_review" status, transition to "released", create OrderIntent records for each proposed trade, emit `rebalance_proposal.released` event
- Each OrderIntent references the proposal_id as its source, linking the trade back to the rebalance decision
- OrderIntent creation follows the schema and conventions from Epic 9 (order-intents module)
- Idempotency: release is idempotent; re-releasing an already-released proposal returns 200 with the existing result
- Permission guard: `rebalance_proposal.release` (may differ from `rebalance_proposal.create`)
- Audit event emission with full context: who released, when, which proposal, how many order intents created

### Acceptance Criteria

- POST /api/rebalance-proposals/:id/release transitions proposal from "pending_review" to "released"
- Release returns 409 if proposal is not in "pending_review" status (already released, cancelled, or expired)
- Release creates one OrderIntent per entry in the proposal's trades list
- Each OrderIntent includes: account_id, security_id, side, quantity, source_type ("rebalance_proposal"), source_id (proposal_id)
- OrderIntents are created in the database but NOT automatically submitted to OMS -- they follow the standard Epic 9 submission flow
- `rebalance_proposal.released` event is emitted with proposal_id, account_id, and count of order intents created
- Re-calling release on an already-released proposal returns the same result without creating duplicate OrderIntents
- Audit event captures the releasing actor, proposal details, and generated order intent IDs
- If OrderIntent creation fails partway through, the entire release is rolled back (transactional)

### Dependencies

- Issue 10-4 (RebalanceProposal generation and schema)
- Epic 9 (OrderIntent creation interface and schema)

---

## Issue 10-6: Proposal Cancel Flow

### Title

Implement proposal cancellation for pending rebalance proposals

### Description

Allow advisors to cancel a RebalanceProposal that has not yet been released. Cancellation transitions the proposal to "cancelled" status and records who cancelled it and when. A cancelled proposal cannot be released. If the proposal has already been released, cancellation is not allowed -- the advisor must cancel the individual OrderIntents through the Epic 9 cancel flow instead.

### Scope

- Route: POST /api/rebalance-proposals/:id/cancel
- Service layer: validate proposal is in "pending_review" status, transition to "cancelled", record cancelled_by and cancelled_at
- Emit `rebalance_proposal.cancelled` domain event
- Permission guard: `rebalance_proposal.cancel`
- Audit event emission

### Acceptance Criteria

- POST /api/rebalance-proposals/:id/cancel transitions proposal from "pending_review" to "cancelled"
- Cancel returns 409 if the proposal is not in "pending_review" status
- Cancel returns 409 with a clear message if the proposal has already been released, directing the user to cancel individual order intents
- Cancelled proposals retain all their data (assumptions, trades) for audit purposes -- nothing is deleted
- `rebalance_proposal.cancelled` event is emitted with proposal_id and account_id
- Audit event records the cancelling actor and timestamp
- A cancelled proposal cannot be released (release returns 409)
- Cancel is idempotent: cancelling an already-cancelled proposal returns 200

### Dependencies

- Issue 10-4 (RebalanceProposal schema and statuses)

---

## Issue 10-7: Proposal History and Audit

### Title

Implement proposal history retrieval with full assumption and trade audit trail

### Description

Build the read APIs that allow advisors and compliance staff to review the full history of rebalance proposals for an account. Each proposal's record includes the exact assumptions used at generation time, the proposed trades, the outcome (released, cancelled, expired), and the actors involved. This supports both day-to-day advisor review and compliance audit requirements.

### Scope

- Route: GET /api/rebalance-proposals (list with filtering and pagination)
- Route: GET /api/rebalance-proposals/:id (detail view)
- Filters: account_id, model_portfolio_id, status, date range (generated_at), generated_by
- Detail response includes: proposal metadata, full assumptions snapshot, proposed trades with rationale, status history (generated, released/cancelled, by whom, when)
- If released, include references to the generated OrderIntent IDs so the advisor can trace through to execution
- Permission guard: `rebalance_proposal.read`
- No mutations in this issue -- read-only

### Acceptance Criteria

- GET /api/rebalance-proposals returns paginated list of proposals filtered by account, model, status, date range
- GET /api/rebalance-proposals/:id returns the complete proposal including assumptions, trades, and status timeline
- Assumptions snapshot is returned exactly as captured at generation time, not recomputed
- Released proposals include the list of OrderIntent IDs created during release
- Response includes actor information for each status transition (generated_by, released_by, cancelled_by)
- Results are tenant-scoped; no cross-tenant data leakage
- Large proposal lists perform acceptably with proper indexing on account_id, status, and generated_at
- Permission enforcement returns 403 for unauthorized users

### Dependencies

- Issue 10-4 (RebalanceProposal schema)
- Issue 10-5 (release data for linking to OrderIntents)
- Issue 10-6 (cancel data)

---

## Issue 10-8: Tax-Sensitive Proposal Logic

### Title

Add tax-aware lot selection preferences to rebalance proposal generation

### Description

Enhance the proposal generation logic to incorporate tax-sensitive heuristics when selecting which lots to sell. This is decision-support logic, not authoritative tax advice. The system should prefer selling loss lots (tax-loss harvesting opportunities) and flag or deprioritize lots that would realize short-term capital gains. The tax-sensitivity analysis is captured in the proposal's assumptions and per-trade rationale so the advisor can review the reasoning.

### Scope

- Extend proposal generation service to accept a tax_sensitivity option (none, basic, aggressive)
- Lot-level analysis: for each sell candidate, evaluate available lots by cost basis, acquisition date, and unrealized gain/loss
- Lot selection heuristics:
  - Prefer lots with unrealized losses (harvest losses)
  - Deprioritize lots held less than one year (avoid short-term gains)
  - Among long-term lots, prefer highest-cost-basis lots (minimize taxable gain)
- Wash sale awareness: flag (do not block) sells where a repurchase of the same or substantially identical security is in the proposal's buy list
- Extend `rebalance_proposal_trades` with optional fields: selected_lot_ids (JSON), estimated_gain_loss, gain_loss_term (short_term, long_term), wash_sale_warning (boolean)
- Extend assumptions snapshot to include lot-level data used for tax analysis
- Tax sensitivity rationale included in per-trade rationale string
- This logic is advisory. The platform does not guarantee tax outcomes and does not replace a tax professional.

### Acceptance Criteria

- When tax_sensitivity is "basic" or "aggressive", sell trades include lot selection preferences
- Loss lots are preferred over gain lots for sell candidates
- Short-term gain lots are deprioritized relative to long-term gain lots
- In "aggressive" mode, the system will prefer harvesting losses even if it slightly over-trades relative to drift targets
- Wash sale warnings are flagged when a sell-and-buy of the same security appears in the same proposal
- Per-trade rationale explains the tax reasoning (e.g., "Sell lot acquired 2024-03-15: harvests $2,300 long-term loss")
- Assumptions snapshot includes the lot-level data used for tax evaluation
- When tax_sensitivity is "none", proposal generation behaves identically to Issue 10-4 (no lot-level analysis)
- Tax logic does not block proposal generation -- if lot data is unavailable, the proposal is generated without tax preferences and a warning is included

### Dependencies

- Issue 10-4 (RebalanceProposal generation base)
- Epic 9 or external integration (lot-level position data with cost basis and acquisition dates)

---

## Issue 10-9: Model Marketplace Browsing

### Title

Implement read-only browsing of marketplace model portfolios

### Description

Allow advisors to browse a catalog of model portfolios published by third-party strategists or the platform itself. Marketplace models are read-only from the advisor's perspective -- they cannot be edited, only viewed and subscribed to. This issue covers the browsing and detail view; subscription is handled in Issue 10-10.

### Scope

- Postgres schema: `marketplace_models` table (or a flag/source_type on `model_portfolios` distinguishing firm-owned from marketplace) with additional fields: provider_name, strategy_description, inception_date, benchmark, fee_rate (if applicable), category tags
- Route: GET /api/marketplace/models (list with filtering by category, provider, search term)
- Route: GET /api/marketplace/models/:id (detail view with allocations and strategy description)
- Marketplace models are not tenant-scoped in the same way as firm models; they are visible across tenants based on entitlements
- Permission guard: `marketplace.browse` (available to all advisor roles by default)
- No mutation endpoints in this issue

### Acceptance Criteria

- GET /api/marketplace/models returns a paginated list of available marketplace models
- Filtering supports category, provider_name, and free-text search on name and description
- GET /api/marketplace/models/:id returns full model detail including allocations, provider info, and strategy description
- Marketplace models cannot be edited or deleted through advisor-facing APIs
- Results respect tenant entitlements (if marketplace access is a paid feature, unenrolled tenants see an empty list or 403)
- Response clearly distinguishes marketplace models from firm-owned models

### Dependencies

- Issue 10-1 (ModelPortfolio schema, potentially extended)
- Epic 1 (tenant entitlements)

---

## Issue 10-10: Model Marketplace Subscription

### Title

Implement subscription to marketplace models for use in account assignments

### Description

Allow advisors to subscribe their firm to a marketplace model. Subscribing creates a local reference that enables the marketplace model to be used in model assignments (Issue 10-2) and rebalance proposals (Issue 10-4). The subscription tracks the firm's adoption of the model and allows the platform to notify the firm when the marketplace model is updated by its provider.

### Scope

- Postgres schema: `marketplace_subscriptions` table with columns for id, tenant_id, marketplace_model_id, subscribed_by, subscribed_at, status (active, cancelled), cancelled_at
- Route: POST /api/marketplace/models/:id/subscribe
- Route: DELETE /api/marketplace/subscriptions/:id (cancel subscription)
- Service layer: create subscription, validate tenant entitlements, check for duplicate active subscription
- When a marketplace model is updated by its provider, subscribed firms receive a `marketplace_model.updated` event (consumption of this event is a notification concern, not a blocking requirement for this issue)
- Emit `marketplace_model.subscribed` domain event
- Permission guard: `marketplace.subscribe`
- Audit event emission

### Acceptance Criteria

- POST /api/marketplace/models/:id/subscribe creates a subscription linking the tenant to the marketplace model
- Duplicate active subscriptions for the same tenant and model return 409
- After subscription, the marketplace model appears in GET /api/models results (marked as marketplace-sourced) and can be used in model assignments
- DELETE /api/marketplace/subscriptions/:id cancels the subscription; existing assignments using the model are NOT automatically removed (advisor must reassign manually)
- Cancelling a subscription with active model assignments emits a warning in the response but does not block cancellation
- `marketplace_model.subscribed` event is emitted with tenant_id and model_id
- Audit event records the subscribing actor
- Subscription respects tenant entitlement checks

### Dependencies

- Issue 10-9 (Marketplace browsing)
- Issue 10-2 (ModelAssignment for using subscribed models)
- Epic 1 (tenant entitlements)

---

## Issue 10-11: Rebalance Rule Configuration

### Title

Implement per-account and per-model rebalance rule configuration

### Description

Allow advisors to configure rebalance rules that control when and how rebalancing is triggered for an account or model. Rules include drift thresholds, rebalance frequency, and whether the system should auto-generate proposals or wait for manual initiation. These rules feed into the drift monitoring (Issue 10-3) and can be used by a scheduled rebalance job to auto-generate proposals for accounts whose drift exceeds the threshold.

### Scope

- Postgres schema: `rebalance_rules` table with columns for id, tenant_id, scope_type (account, model), scope_id, drift_threshold_pct, rebalance_frequency (daily, weekly, monthly, quarterly, manual), auto_propose (boolean), cash_target_pct, min_trade_amount, tax_sensitivity (none, basic, aggressive), created_by, updated_at
- Routes: GET /api/rebalance-rules (filtered by scope_type and scope_id), PUT /api/rebalance-rules/:id
- Service layer: upsert rules for account or model scope, apply inheritance (account-level rules override model-level rules which override system defaults)
- Background worker integration: scheduled job reads rebalance rules, identifies accounts due for rebalance evaluation, triggers drift check and optionally auto-generates proposals
- Permission guard: `rebalance_rules.read`, `rebalance_rules.update`
- Audit event emission on rule changes

### Acceptance Criteria

- PUT /api/rebalance-rules/:id creates or updates rebalance rules for the given scope (account or model)
- Rules support drift_threshold_pct (e.g., 5.0 means rebalance when any position drifts more than 5%)
- Rules support rebalance_frequency with valid values: daily, weekly, monthly, quarterly, manual
- When auto_propose is true and the scheduled job detects drift exceeding the threshold, a RebalanceProposal is auto-generated with status "pending_review" (never auto-released)
- When auto_propose is false, drift breach emits an event/notification but does not generate a proposal
- Account-level rules take precedence over model-level rules; model-level rules take precedence over system defaults
- GET /api/rebalance-rules returns the effective rules for the given scope, showing which level (account, model, default) each setting comes from
- cash_target_pct and min_trade_amount feed into proposal generation logic (Issue 10-4)
- tax_sensitivity setting feeds into tax-sensitive proposal logic (Issue 10-8)
- Rule changes emit audit events with before/after values
- Auto-propose NEVER auto-releases; human review is always required before OrderIntents are created

### Dependencies

- Issue 10-3 (Drift monitoring for threshold evaluation)
- Issue 10-4 (Proposal generation for auto-propose flow)
- Issue 10-8 (Tax sensitivity configuration feeds into proposal logic)

---

## Module Structure

Following the conventions from the API server spec:

```text
modules/portfolios/
  routes.ts          -- Hono route definitions for models, assignments, proposals, rules, marketplace
  schemas.ts         -- Zod schemas for all request/response types
  service.ts         -- Orchestration: model CRUD, assignment, proposal generation, release, cancel
  repository.ts      -- Postgres queries for all portfolio domain tables
  types.ts           -- TypeScript types for ModelPortfolio, ModelAssignment, RebalanceProposal, etc.
  events.ts          -- Domain event names and Kafka publishing helpers
  drift.ts           -- Drift calculation logic (pure function, no I/O)
  rebalancer.ts      -- Rebalance algorithm (pure function: given holdings + model + rules, produce trades)
  tax-analyzer.ts    -- Tax-sensitive lot selection logic (pure function)
```

## Key Design Principles

1. **Proposal is not execution.** A RebalanceProposal is an inert artifact until explicitly released. Release emits OrderIntents. OrderIntents follow the Epic 9 lifecycle. There is no shortcut from proposal to execution.

2. **Assumptions are frozen.** Every proposal stores the exact holdings, prices, model targets, and rules that were used to generate it. The proposal is reproducible and auditable without re-fetching live data.

3. **Auto-propose never auto-releases.** Even when the system automatically generates proposals based on drift thresholds and rebalance schedules, a human must explicitly release the proposal before any OrderIntents are created.

4. **Tax logic is advisory.** Tax-sensitive lot selection is decision support. The platform flags opportunities and preferences but does not guarantee tax outcomes or replace professional tax advice.

5. **Marketplace models are read-only.** Advisors consume marketplace models through subscription; they cannot modify them. Firm-owned models are fully editable.
