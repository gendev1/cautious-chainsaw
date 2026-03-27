# Wealth Platform Chassis Spec v2

## 1. Purpose

This document turns the current rough specs into a more rigorous product and systems baseline for building a platform in the same category as Altruist: a digital, advisor-focused wealth management and custody platform.

It is intentionally stricter than the current docs. The existing documents describe visible features well enough, but they underspecify the operating chassis required to make those features safe, auditable, and reliable.

This spec separates:

- marketing-level feature claims
- product requirements
- regulated operational requirements
- architecture and data ownership rules

## 2. What Altruist Appears To Be

Based on current public Altruist materials as of March 26, 2026, Altruist presents itself as an RIA-focused, self-clearing, digital custody platform with these main product pillars:

- advisor onboarding and transfers, including digital ACATs and bulk transitions
- account opening across 30+ account types
- portfolio management, including trading, automated rebalancing, tax management, direct indexing, fixed income, and model marketplace access
- built-in billing, reporting, integrations, and client portal/mobile experience
- high-yield cash and securities-based lending
- an AI product layer, Hazel, spanning custodial and non-custodial firm data

This matters because the platform category is not "CRM with investing features." It is a custody-and-operations platform whose advisor UX is built on top of operational rails.

## 3. Source Material Assessment

### 3.1 What the current docs get right

- The major customer-visible product areas are directionally correct.
- Multi-account onboarding and household workflows are correctly emphasized.
- Rebalancing, tax-aware portfolio management, reporting, billing, and integrations are core modules.
- A separate AI service is a reasonable implementation choice if AI is additive rather than authoritative.

### 3.2 What the current docs get wrong or leave too loose

- They treat website copy as implementation truth.
- They overfocus on advisor-facing UX and underfocus on custody operations.
- They assume digital workflows remove the need for compliance-heavy control points.
- They collapse transactional, analytical, and AI workloads into a design that is too simple for money movement and trading.
- They assume full within-tenant access and no role model, which is too weak for a real firm platform.
- They treat ACATs, banking, trading, billing, and reporting as CRUD features when each is actually a lifecycle with status, reconciliation, exceptions, and audit requirements.

## 4. Product Definition

The target product should be defined as:

"A multi-tenant advisor platform for RIAs that combines account onboarding, custody operations, portfolio management, billing, reporting, client experience, and advisor copilot workflows on a common operational backbone."

That definition implies four layers:

1. Firm layer
   Manages the RIA tenant, branding, entitlements, subscriptions, integrations, staff, and permissions.

2. Client and account layer
   Manages households, clients, legal parties, accounts, account registrations, documents, beneficiaries, trusted contacts, and account lifecycle.

3. Operations layer
   Manages onboarding review, KYC/AML checkpoints, transfers, bank links, cash movement, trading, settlements, billing runs, reporting runs, reconciliation, and exception handling.

4. Intelligence layer
   AI assistant, analysis, summaries, drafting, and detection workflows. This layer may assist decisions but cannot be the system of record for them.

## 5. Product Pillars

### 5.1 Firm Administration

Required capabilities:

- tenant provisioning and lifecycle
- subscription and plan management
- firm profile and branding
- office and staff directory
- advisor invitations and account recovery
- permissions and approval policies
- integration configuration
- audit visibility for admins and operations staff

Key correction:
The current "no RBAC/ABAC" assumption should be removed. Even small RIAs need at least role-based entitlements for:

- firm admin
- advisor
- trader
- operations associate
- billing admin
- read-only support or compliance user

Phase 1 can keep the model simple, but "every advisor can do everything" is not acceptable for trading, billing, integrations, or support actions.

### 5.2 Client, Household, and Legal Party Model

Required entities:

- household
- client person
- client organization or trust
- advisor-to-client relationship
- account
- account registration
- beneficiary
- trusted contact
- authorized signer
- external bank account
- transfer instruction
- document bundle

Key rule:
Client identity, legal party data, and account registration data must be modeled separately. Do not overload a generic "client" record to carry trust, IRA, entity, and beneficiary semantics.

### 5.3 Onboarding and Account Opening

Onboarding is not one state machine. It is a workflow envelope around several sub-processes:

- household creation
- client identity capture
- account registration selection
- disclosures and consents
- beneficiaries and trusted contacts
- funding method selection
- transfer initiation
- approval and exception handling
- account activation

Required statuses:

- draft
- submitted
- pending_client_action
- pending_internal_review
- pending_custodian_or_clearing
- approved
- rejected
- active
- restricted
- closed

Key correction:
Do not auto-advance from "authorized" to "active" based only on local form completion. A real platform needs explicit approval gates and exception states.

### 5.4 Transfers and Funding

Treat transfers as first-class workflows, not line items on an onboarding session.

Required transfer types:

- ACAT full transfer
- ACAT partial transfer
- ACH deposit
- ACH withdrawal
- journal between internal accounts
- wire in
- wire out
- check and legacy/manual rails where required

Required transfer lifecycle:

- draft
- initiated
- submitted
- pending_external_review
- pending_client_verification
- in_transit
- partially_completed
- completed
- failed
- cancelled
- reversed

Required operational behaviors:

- idempotent initiation
- status history
- exception notes
- document attachment support
- webhook or polling adapter model
- reconciliation with cash ledger

Key correction:
"No paperwork" cannot be a systems requirement. The real requirement is "digital-first with manual exception support."

### 5.5 Portfolio Management

Required capabilities:

- manual trade entry
- model assignment
- rebalance proposal generation
- review and release workflow
- tax-sensitive trade proposal logic
- direct indexing or personalized indexing rules
- fixed income inventory and execution support
- model marketplace subscription and entitlements
- portfolio drift monitoring

Key correction:
Proposal generation and trade execution must be separated. A rebalance proposal is not an executed order.

### 5.6 Order Management and Trade Lifecycle

This is missing from the current docs and must be explicit.

Required entities:

- order
- execution
- allocation
- lot selection decision
- trade confirmation
- settlement event
- corporate action adjustment

Required order statuses:

- created
- validated
- queued
- routed
- partially_filled
- filled
- cancelled
- rejected
- settled
- failed_to_settle

Required controls:

- pre-trade validation
- account restrictions
- cash and position checks
- duplicate order prevention
- order idempotency
- approval policy for certain trade classes
- immutable execution history

### 5.7 Cash, Ledger, and Balances

A platform in this category needs an internal books-and-records representation even if a clearing firm or bank is upstream.

Minimum required ledger concepts:

- account cash balance
- available cash
- pending cash
- settled cash
- ledger entry
- transfer hold
- settlement adjustment
- fee debit
- interest accrual

Non-negotiable rule:
Balances shown to users must come from a deterministic ledger or a reconciled authoritative balance source. Do not compute balances ad hoc from mutable documents.

### 5.8 Reporting and Documents

Required reporting outputs:

- performance reports
- holdings reports
- activity statements
- billing statements
- realized gain/loss tax views
- onboarding and transfer status reports

Required document capabilities:

- document upload
- document classification
- document retention policy
- immutable storage for signed artifacts
- client-visible document vault
- generated document versions

Key correction:
Interactive reporting is a product feature. Source calculations, historical snapshots, and generated statements are platform infrastructure.

### 5.9 Billing

Billing is a lifecycle, not a single calculation run.

Required capabilities:

- fee schedule definition
- account, client, and household billing scopes
- tiered schedules
- exclusions and overrides
- billing period close
- invoice generation
- fee debit posting
- exception handling
- reversal and correction
- audit trail

Required statuses:

- scheduled
- calculated
- pending_review
- approved
- posted
- collected
- partially_collected
- failed
- reversed

### 5.10 Client Experience

Required client-facing functions:

- invitation and activation
- MFA and device/session management
- account overview
- funding and transfer visibility
- document vault
- activity and statements
- beneficiary and trusted contact review where allowed
- co-branded desktop and mobile support

Key rule:
Client-facing permissions and workflows must be defined separately from advisor-facing workflows. A shared data model is acceptable; a shared permission model is not.

### 5.11 Integrations

Integration design should be connector-based.

Required connector classes:

- CRM
- financial planning
- compliance
- market data or reference data
- document and e-sign
- banking and account verification
- clearing and custody rails if externalized
- communication systems for AI context, if supported

Required integration primitives:

- OAuth or API credential storage
- sync cursors
- mapping tables
- retry queue
- dead-letter handling
- sync audit log

### 5.12 AI and Advisor Copilot

AI should be positioned as an augmentation layer, not a control layer.

Good AI use cases:

- summarize client context
- draft communications
- explain portfolio events
- highlight tax or RMD opportunities
- answer operational questions using current system data
- classify and extract documents

Bad AI use cases unless tightly controlled:

- direct order execution without approval
- authoritative compliance determinations
- authoritative tax advice
- source-of-truth record storage

Key correction:
The current specs make the AI sidecar too central in the narrative. AI is important, but it is not part of the chassis that makes custody, money movement, and reporting trustworthy.

## 6. Required Chassis Domains

To build a credible Altruist-like platform, the following domains must exist explicitly, even if Phase 1 combines them into a modular monolith:

### 6.1 Identity, Entitlements, and Tenant Context

- tenant resolution
- user identity
- session and token management
- MFA
- role and permission enforcement
- impersonation and support access controls
- audit of privileged actions

### 6.2 Household, Client, and Account Registry

- household model
- legal parties
- account registrations
- account lifecycle
- relationship graph
- documents and attestations

### 6.3 Workflow and Case Management

- onboarding cases
- transfer cases
- review queues
- exception states
- operational task assignment
- SLA timers and reminders

If this domain is omitted, the product becomes a collection of endpoints with no operating model.

### 6.4 Money Movement Rails

- ACH and bank-link orchestration
- ACAT lifecycle
- wires and journals
- cash ledger integration
- returns, reversals, and holds

### 6.5 Order Management System

- order creation
- validation
- routing integration
- execution updates
- settlement updates
- position and lot updates

### 6.6 Portfolio Engine

- model storage
- account-model mapping
- rebalance rule evaluation
- proposal generation
- tax-aware lot selection support
- direct indexing rule application

### 6.7 Performance and Billing Engine

- holdings snapshots
- cashflow-normalized performance calculation
- benchmark comparison
- fee calculation runs
- invoice and debit generation

### 6.8 Document and Records Platform

- immutable signed artifacts
- generated statements and confirms
- retention controls
- search and retrieval
- export support

### 6.9 Audit, Compliance, and Reconciliation

- append-only audit events
- surveillance and exception events
- daily reconciliations
- break management
- operational notes

This is a hard requirement for the chassis. It is not optional polish.

### 6.10 AI and Analytics

- advisor copilot
- narrative generation
- document extraction
- opportunity detection
- analytics read models

## 7. Recommended Architecture Direction

## 7.1 Product architecture

Use a modular monolith first, with explicit domain modules and asynchronous jobs, unless there is already proven team capacity for distributed systems.

Recommended top-level services:

- `api-core`
  Firm, client, account, portfolio, billing, reporting, client portal APIs.

- `workflow-engine`
  Long-running onboarding, transfer, billing, and reconciliation workflows.

- `integration-workers`
  Adapters for banking, clearing, market data, CRM, and document systems.

- `ai-sidecar`
  Copilot, extraction, narratives, and analytics assistance.

- `reporting-jobs`
  Snapshotting, statement generation, and performance materialization.

This can still be deployed as a small number of runtimes, but the domain boundaries should be explicit from day one.

## 7.2 Data architecture

The current "MongoDB for everything" approach is too risky for transaction-heavy financial operations.

Recommended data split:

- relational database for transactional truth
  Accounts, transfers, orders, executions, billing runs, permissions, workflow states.

- append-only ledger or ledger-style transactional tables
  Cash movements, fee debits, accruals, settlement adjustments.

- document or object storage
  Signed forms, statements, uploads, generated reports.

- cache
  Sessions, rate limiting, ephemeral computed views.

- analytical read models
  Reporting snapshots, portfolio analytics inputs, AI context summaries.

MongoDB can still be used for flexible documents or read models, but it should not be the only persistence layer for ledger-like and workflow-critical state.

## 7.3 Eventing and workflow

Introduce explicit domain events and long-running workflow orchestration.

Examples:

- `account_opening_submitted`
- `account_opening_approved`
- `bank_link_verified`
- `transfer_initiated`
- `transfer_completed`
- `rebalance_proposal_generated`
- `order_filled`
- `billing_run_posted`
- `statement_generated`

Use events for decoupling and audit, not as a substitute for a transactional source of truth.

## 7.4 AI service posture

Keep the AI service off the authoritative write path for:

- order execution
- ledger mutation
- transfer settlement
- account approval
- billing posting

AI may recommend, summarize, classify, and explain. It should not silently commit regulated actions.

## 8. Non-Functional Requirements

Required NFRs for this category:

- strict tenant isolation
- role-based permissions
- idempotent writes
- immutable audit records
- deterministic balance calculations
- workflow retry safety
- attachment and document durability
- observability with request and workflow correlation IDs
- operational dashboards for exceptions and stuck work
- support tooling with scoped access and full audit

## 9. Phase Structure

### Phase 1: Advisor Platform Core

Build:

- tenant, users, roles, households, clients, accounts
- onboarding workflow with review queue
- ACH and ACAT abstractions with manual exception support
- manual trading plus rebalance proposal and review/release
- billing basics
- reporting basics
- client portal basics
- audit, permissions, and document vault

Do not build yet:

- direct indexing optimization
- SBLOC origination engine
- advanced AI autonomy
- broad marketplace ecosystem

### Phase 2: Operational Depth

Build:

- fixed income workflows
- tax-aware rebalancing depth
- reconciliation and break tooling
- richer integration framework
- household-level tax automation
- richer reporting packs

### Phase 3: Differentiation

Build:

- model marketplace economics and subscriptions
- personalized indexing sophistication
- AI across CRM, email, meetings, and custodial data
- advanced lending and cash optimization

## 10. Immediate Changes Required In The Existing Specs

1. Remove the "no RBAC/ABAC" assumption from the API spec.

2. Reframe onboarding as a case workflow with review and exception states, not a single linear happy path.

3. Add an explicit order management and settlement lifecycle to portfolio management.

4. Add an explicit cash ledger and balance model.

5. Add workflow/case management as a first-class platform domain.

6. Add document retention, generated statements, and immutable signed artifact handling.

7. Separate proposal generation from execution across rebalancing, tax actions, and transfers.

8. Treat external rails and integrations as asynchronous adapters with retries and reconciliation, not synchronous CRUD side effects.

9. Demote AI from "core operating dependency" to "augmenting service with strict write boundaries."

10. Replace "paperless always" requirements with "digital-first plus manual exception support."

## 11. Decision Summary

If the goal is to build something meaningfully similar to Altruist, the chassis is not just:

- onboarding
- portfolio management
- operations
- AI

The real chassis is:

- firm and permission model
- household and account registry
- workflow and case management
- money movement rails
- order management and settlement lifecycle
- ledger and balances
- reporting and records
- audit, reconciliation, and compliance controls
- advisor and client experience layers
- AI on top, not underneath

Without those foundations, the product may demo well, but it will not behave like a credible advisor custody platform.
