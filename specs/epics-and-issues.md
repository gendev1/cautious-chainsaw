# Platform Epics and Issues

## Purpose

This document breaks the platform into delivery epics and captures the major issues, risks, and unresolved design questions that must be tracked while building an Altruist-like advisor platform.

It is meant to be used as a planning artifact alongside:

- `specs/platform-chassis-v2.md`
- `specs/architecture.md`
- `specs/api-server.md`
- `specs/python-sidecar.md`
- `specs/data-architecture.md`

This version keeps the original 18-epic roadmap structure. Missing chassis concerns are folded into existing epics rather than split into new epics.

## Delivery Order

Recommended implementation order:

1. Tenant, Identity, and Access Control
2. Client, Household, and Account Registry
3. Workflow and Case Management
4. External Service Integration Framework
5. Document Vault and Records Management
6. Onboarding and Account Opening
7. Money Movement and Transfer Operations
8. Advisor Portal Experience
9. Orders, OMS/EMS Integration, and Trade Status
10. Portfolio Models and Rebalancing
11. Cash, Ledger, and Balance Projections
12. Billing and Fee Operations
13. Reporting, Statements, and Snapshots
14. Client Portal Experience
15. Notifications and Operational Visibility
16. Audit, Compliance, and Support Tooling
17. Platform Reliability and Observability
18. AI Copilot and Document Intelligence

## Important Execution Note

The epic numbering stays the same, but some foundational work must begin earlier than the epic numbers suggest:

- audit baselines start during Epics 1, 3, and 4
- observability and replay safety start during Epics 4 and 6
- schema governance, outbox/inbox, and projection conventions live inside Epic 4
- reconciliation and break-handling foundations begin inside Epics 7, 11, 12, and 17

This preserves the 18-epic roadmap without losing the chassis requirements.

## Epic 1: Tenant, Identity, and Access Control

### Goal

Establish tenant isolation, authentication, within-tenant authorization, session security, and privileged access controls.

### Scope

- tenant provisioning
- subdomain resolution
- user lifecycle
- MFA
- JWT/session model
- role and permission model
- service-to-service authentication
- support impersonation
- baseline schema/versioning rules for auth and identity tables

### Dependencies

- none

### Issues

- The earlier assumption that all advisors in a tenant can do everything is not acceptable.
- Support impersonation is high-risk and must be fully audited.
- Service auth is underspecified if the platform will call multiple external microservices over mixed protocols.
- Tenant identity must propagate cleanly across HTTP, gRPC/proto, and Kafka.
- Foundational audit for auth, role changes, and impersonation must start here even though broader audit tooling is Epic 16.

## Epic 2: Client, Household, and Account Registry

### Goal

Create the canonical business graph for households, clients, legal parties, and account registrations.

### Scope

- households
- client person records
- client entity or trust records
- advisor-client relationships
- account registration types
- beneficiaries
- trusted contacts
- external bank accounts

### Dependencies

- Epic 1

### Issues

- The current docs overload "client" and "account" semantics too heavily.
- Trust, IRA, joint, and entity registrations need distinct modeling.
- Beneficiaries, trusted contacts, and signers have different legal meanings and should not be flattened into one contact model.
- External account identifiers and masked data need careful storage rules.
- Registry identifiers will become external mapping keys, so stability matters early.

## Epic 3: Workflow and Case Management

### Goal

Create the chassis for long-running onboarding, transfer, approval, and exception workflows.

### Scope

- onboarding cases
- transfer cases
- approval requests
- operational tasks
- exception states
- notes and comments
- SLA and reminder timers
- workflow history

### Dependencies

- Epic 1
- Epic 2

### Issues

- The previous onboarding session state machine is too linear and happy-path oriented.
- A custody-grade platform needs durable exception states, not transient handler errors.
- Workflow re-entry and retries need to be idempotent.
- There must be a clear boundary between business state and orchestration state.
- Baseline workflow audit trails must start here, even though advanced support tooling comes later.

## Epic 4: External Service Integration Framework

### Goal

Standardize how the platform talks to security master, OMS/EMS, money movement rails, and other external microservices.

### Scope

- outbound API/gRPC/proto clients
- Kafka producers and consumers
- idempotency keys
- correlation IDs
- retry policies
- dead-letter handling
- projection sync jobs
- upstream health handling
- schema governance for integration payloads
- outbox/inbox processing
- projection table conventions
- replay and backfill strategy
- event contract versioning

### Dependencies

- Epic 1
- Epic 2
- Epic 3

### Issues

- Different services may support different interaction styles, which can fragment platform behavior.
- Sync submission plus async lifecycle is the right pattern, but it adds projection complexity.
- Kafka event contracts must be versioned early or they will become brittle.
- External service outages must degrade workflows safely rather than causing partial writes.
- This epic is where most schema-governance and projection-layer concerns must live if the roadmap stays at 18 epics.
- Reliability and replay tooling must begin here in practice even though Epic 17 is later.

## Epic 5: Document Vault and Records Management

### Goal

Manage uploaded, generated, and signed documents with retention and access controls.

### Scope

- upload intake
- object storage integration
- document metadata
- artifact types
- retention classes
- version references
- secure retrieval
- case and account attachment model

### Dependencies

- Epic 1
- Epic 2
- Epic 3

### Issues

- Signed artifacts and raw uploads are not the same thing and must not be mixed.
- Sensitive documents need access logging and least-privilege retrieval.
- Retention and deletion policies will differ by artifact type.
- Generated statements and signed onboarding records require immutability semantics.
- Document access logging should align with the platform-wide audit model, not a separate local pattern.

## Epic 6: Onboarding and Account Opening

### Goal

Build digital onboarding on top of the case engine with explicit review, approval, and exception handling.

### Scope

- advisor-initiated onboarding
- client action collection
- disclosures and consents
- legal party capture
- beneficiary and trusted contact workflows
- document collection
- review and approval
- activation handoff

### Dependencies

- Epics 1 through 5

### Issues

- "Paperless" should be treated as digital-first, not universal truth.
- Account opening is not complete just because a form was filled.
- There will be manual review and exception scenarios from day one.
- Activation state must respect external approvals or downstream provisioning.
- Client-actor onboarding flows require a minimal client auth/session slice even before the fuller client portal epic.

## Epic 7: Money Movement and Transfer Operations

### Goal

Handle transfer intent creation, submission, lifecycle tracking, and exception handling across funding rails.

### Scope

- ACH transfers
- ACAT full and partial transfers
- wire workflows
- journals
- transfer intents
- status ingestion
- reversals and returns
- reconciliation hooks

### Dependencies

- Epics 1 through 6

### Issues

- Transfers must be treated as first-class workflows, not onboarding sub-fields.
- Upstream rail status can lag or conflict with platform expectations.
- Reversals and returns are core states, not edge cases.
- Cash availability and transfer lifecycle must eventually reconcile.
- Break detection starts here even if broader reconciliation tooling matures later.

## Epic 8: Advisor Portal Experience

### Goal

Provide the advisor-facing product surface over the underlying workflow and records platform.

### Scope

- dashboard
- household and client views
- onboarding workspace
- transfer workspace
- documents
- tasks and exceptions
- portfolio and proposal views only after backend support exists

### Dependencies

- Epics 1 through 7

### Issues

- UI needs to represent in-flight and exception states clearly.
- Advisors need visibility into upstream statuses without exposing raw infrastructure complexity.
- Workflow actions must map to explicit commands, not generic save buttons.
- This epic should not outrun backend capabilities and force placeholder APIs for orders or rebalancing.

## Epic 9: Orders, OMS/EMS Integration, and Trade Status

### Goal

Build trading workflows using platform-owned order intents and upstream OMS/EMS projections.

### Scope

- order intents
- pre-trade validation
- OMS submission
- cancel flows
- execution ingestion
- order and fill projections
- settlement state ingestion where available

### Dependencies

- Epic 4
- Epic 8
- Epic 11

### Issues

- The platform must not masquerade as the OMS.
- Immediate submission responses and delayed fill events create consistency gaps that need explicit handling.
- Duplicate submission and replay protection are critical.
- Permissioning around who can submit, release, or cancel orders must be explicit.
- Pre-trade checks rely on a clear cash and position availability contract from Epic 11.

## Epic 10: Portfolio Models and Rebalancing

### Goal

Build model management and rebalance proposal generation without collapsing proposal and execution together.

### Scope

- model portfolios
- model assignments
- drift monitoring
- rebalance proposal generation
- review and release
- proposal history and rationale

### Dependencies

- Epic 2
- Epic 9
- Epic 11

### Issues

- Rebalance logic depends on reliable holdings and account state projections.
- A generated proposal is not an executed trade.
- Proposal reproducibility matters for audit and advisor trust.
- Personalized indexing and tax-aware logic should not overcomplicate early delivery.
- The advisor portal should consume this epic, not define it.

## Epic 11: Cash, Ledger, and Balance Projections

### Goal

Provide deterministic cash and balance representations for the product even when authoritative cash movement is upstream.

### Scope

- cash projections
- available vs pending vs settled balances
- ledger-style entries
- interest accrual support
- fee debit support
- reconciliation inputs

### Dependencies

- Epic 7
- Epic 9

### Issues

- Ad hoc balance computation will create inconsistent UI and operational errors.
- The platform needs a clear contract between projected balances and authoritative balances.
- Cash movement, trade settlement, and fee debits can overlap in hard-to-debug ways.
- If upstream ledgers exist, local projection rules must be explicit and timestamped.
- Append-only semantics should stay strict; avoid drifting into in-place adjustment patterns.

## Epic 12: Billing and Fee Operations

### Goal

Build fee configuration, billing runs, approvals, posting, and correction flows.

### Scope

- fee schedules
- billing scopes
- billing calendars
- billing run generation
- review and approval
- invoice creation
- posting
- reversal and correction

### Dependencies

- Epic 2
- Epic 3
- Epic 11

### Issues

- Billing is a lifecycle, not a single calculation function.
- Reversals should create compensating records, not destructive edits.
- Household, client, and account scope billing all need explicit support.
- Billing and cash availability may become coupled if fee collection is automated.
- Valuation inputs for billing need explicit projection freshness semantics; AUM is not a cash-only concept.
- Use the platform-wide audit model instead of isolated billing-local audit semantics.

## Epic 13: Reporting, Statements, and Snapshots

### Goal

Generate client-ready reports and statements from frozen inputs and versioned artifacts.

### Scope

- reporting snapshots
- statement generation
- artifact publication
- versioned report definitions
- advisor and client retrieval

### Dependencies

- Epic 4
- Epic 5
- Epic 11
- Epic 12

### Issues

- Published reporting cannot depend on mutable live reads.
- Reports need clear as-of semantics.
- Performance and activity data may come from multiple upstream sources and need normalization.
- Statement artifacts must be immutable once published.

## Epic 14: Client Portal Experience

### Goal

Deliver client-facing views and actions under a separate permission and workflow model.

### Scope

- client activation
- MFA
- account overviews
- transfer visibility
- statement and document vault
- limited client actions

### Dependencies

- Epics 1, 5, 6, 7, and 13

### Issues

- Client permissions should not mirror advisor permissions.
- Client-visible statuses must be simpler than internal operational statuses.
- Document visibility may vary by artifact type and workflow stage.
- Notifications and operational signals will likely be needed for invitations and status updates, even if the epic remains later.

## Epic 15: Notifications and Operational Visibility

### Goal

Give users and operations teams timely visibility into important events and stuck work.

### Scope

- notification routing
- in-app inbox
- workflow alerts
- exception alerts
- reminder timers
- operational dashboards

### Dependencies

- Epic 3
- Epic 4
- Epic 8

### Issues

- Event volume can become noisy without routing rules and deduplication.
- Workflow notifications need actor-aware targeting.
- Operational dashboards need reliable event and workflow correlations.
- Notifications and operational signals should share plumbing with observability rather than duplicating it.

## Epic 16: Audit, Compliance, and Support Tooling

### Goal

Create append-only auditability and internal support workflows for privileged actions and investigations.

### Scope

- audit event store
- actor/resource/workflow search
- support tooling
- impersonation audit
- sensitive document access log
- export tooling

### Dependencies

- Epic 1
- Epic 3
- Epic 5
- Epic 7
- Epic 9
- Epic 12

### Issues

- Audit is not a bolt-on concern.
- Support tooling can become a security liability if it bypasses normal permission controls.
- Sensitive resource access must be queryable after the fact.
- This epic extends the foundational audit work already started in Epics 1, 3, and 4.

## Epic 17: Platform Reliability and Observability

### Goal

Make the system operable under failure, lag, retries, and upstream instability.

### Scope

- distributed tracing
- metrics
- workflow dashboards
- Kafka lag visibility
- replay-safe consumers
- dependency health views
- operational recovery tooling
- runbooks
- backup and restore planning

### Dependencies

- cross-cutting, should start early

### Issues

- The system depends on multiple external services with mixed consistency models.
- Projection lag and stale reads must be visible.
- Workflow failures need durable repair paths, not only logs.
- This work must begin with integration and workflow epics even if the numbered epic is later.
- Reconciliation and break-management tooling should be built partly here if it is not separated into its own epic.

## Epic 18: AI Copilot and Document Intelligence

### Goal

Add advisor productivity and document intelligence features without putting AI on the regulated write path.

### Scope

- copilot chat
- portfolio explanations
- operational summaries
- document classification
- document extraction
- report narratives
- recommendation payloads

### Dependencies

- Epic 8
- Epic 13
- Epic 17
- stable internal read contracts and projection freshness policy

### Issues

- AI must not become a hidden dependency for operational truth.
- Sidecar reads need narrow contracts, not open-ended internal data sprawl.
- AI recommendations must not bypass permissions or workflow approvals.
- Narrative generation should prefer frozen snapshots for client-visible output.
- Stable platform read contracts and projection freshness semantics are prerequisites, even if they are implemented inside earlier epics rather than as separate roadmap items.

## Cross-Epic Issues

These issues cut across nearly every epic:

### 1. Role model and approvals

The platform needs a simple but real authorization model from the start. Delaying this will contaminate every workflow with assumptions that are hard to unwind.

### 2. Sync commands vs async truth

Many user actions will receive immediate submission feedback while final truth arrives later through Kafka or other async channels. This needs explicit modeling everywhere.

### 3. Projection freshness

If the product shows local projections of OMS, transfer rails, or ledger-adjacent state, every view must have clear freshness semantics.

### 4. Idempotency

Orders, transfers, billing posts, and workflow transitions all need idempotent command handling.

### 5. Auditability

Operational, privileged, and customer-impacting actions must leave append-only audit trails.

### 6. Event contract governance

Kafka schemas and consumer compatibility need versioning and testing discipline, even if this work is folded into Epic 4.

### 7. PII and retention controls

Field-level protection, masking, key management, and data retention enforcement need explicit design.

### 8. AI boundary

AI should remain a read and recommendation layer. If it starts mutating workflow state directly, the architecture will become unsafe quickly.

## Suggested MVP Cut

If the goal is to get to a credible first platform slice, the best MVP is:

- Epic 1
- Epic 2
- Epic 3
- Epic 4
- Epic 5
- Epic 6
- Epic 7
- Epic 8
- Epic 16
- Epic 17

That gives you:

- secure tenancy and permissions
- a real client/account graph
- durable onboarding and transfer workflows
- document handling
- integration boundaries to external financial systems
- advisor-facing happy paths
- audit and operational visibility

Trading, billing, reporting, and AI can then be layered onto a stable chassis instead of forcing redesign later.
