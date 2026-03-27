# Data Architecture

## Purpose

This document explains the recommended storage strategy for the platform and maps major data domains to the right persistence layer.

The goal is not to force one database to do everything. The goal is to put each kind of data in the store that best matches its consistency, query, lifecycle, and cost profile.

## Recommended Storage Model

Use a polyglot persistence model:

- Postgres for operational truth
- MongoDB for flexible denormalized read models where useful
- Redis for cache and coordination
- object storage for uploaded and generated artifacts

This matches the platform shape:

- workflow-heavy control plane
- external microservice integrations
- asynchronous projections
- document-heavy operations
- analytics and AI read paths

## Decision Summary

### Postgres should be the system of record for

- tenant and identity data
- permissions and approvals
- client, household, and account registry
- onboarding and transfer workflows
- order intents and synchronized trading projections
- billing workflows
- audit events and workflow history
- ledger-like balance and cash projection records

### MongoDB should be used for

- denormalized dashboard views
- AI context documents
- flexible report payloads
- search-oriented or aggregated read models
- cached external reference data projections when document shape is fluid

### Redis should be used for

- sessions
- rate limiting
- idempotency keys
- distributed locks
- short-lived workflow coordination
- ephemeral caches

### Object storage should be used for

- uploaded documents
- signed forms
- generated reports
- statements
- exports
- secure file attachments

## Why Postgres For The Core

The platform core is dominated by:

- state transitions
- approvals
- relational business entities
- correctness-sensitive writes
- reconciliation
- auditability

These are naturally relational problems.

Postgres is preferred because it gives:

- transactions
- foreign keys
- unique constraints
- partial indexes
- rich query support
- strong support for append-only and event-like tables
- JSONB when some local flexibility is needed

That makes it a strong fit for the operational core without losing all document flexibility.

## Data Placement By Domain

## 1. Tenant, Identity, and Access Control

### Store of record

Postgres

### Entities

- tenants
- firms
- users
- sessions
- refresh tokens
- MFA factors
- roles
- permissions
- role assignments
- support impersonation sessions

### Why

- strict uniqueness and relational integrity matter
- auth and authorization are correctness-sensitive
- support impersonation must be auditable

### Redis usage

- short-lived sessions or session cache
- rate limiting
- token revocation cache if needed

## 2. Client, Household, and Account Registry

### Store of record

Postgres

### Entities

- households
- client persons
- client entities
- advisor-client relationships
- account registrations
- accounts
- beneficiaries
- trusted contacts
- authorized signers
- external bank accounts metadata

### Why

- these entities are highly relational
- constraints and lifecycle states matter
- joins are frequent and legitimate

### MongoDB usage

- optional denormalized household summary read model
- optional advisor dashboard projections

## 3. Workflow and Case Management

### Store of record

Postgres

### Entities

- onboarding cases
- transfer cases
- approval requests
- operational tasks
- workflow state transitions
- workflow notes
- exception records

### Why

- these are transactional and stateful
- retries and re-entry need durable, queryable state
- there are strong parent-child relationships

### Redis usage

- workflow locks
- dedupe windows
- short-lived worker coordination

## 4. Orders, Trading, and Execution Projections

### Store of record

Postgres for platform-owned truth

### Entities

- order intents
- order submission attempts
- order projections
- execution projections
- order-event ingestion offsets or checkpoints
- cancel requests

### Why

- order intent is platform-owned
- upstream IDs and sync timestamps need disciplined storage
- audit and replay handling are easier in relational tables

### Important note

The OMS remains authoritative for live order and execution truth. Postgres stores:

- local command intent
- synchronized projections
- reconciliation metadata

### MongoDB usage

- optional trading activity timelines for UI
- optional denormalized portfolio activity feeds

## 5. Transfers and Money Movement

### Store of record

Postgres

### Entities

- transfer intents
- rail submission attempts
- transfer projections
- return or reversal records
- verification records
- transfer-case links

### Why

- transfer workflows are transactional and exception-heavy
- idempotency and status history are critical
- links to clients, accounts, workflows, and documents are relational

### Redis usage

- short-lived status cache
- worker dedupe or backoff coordination

## 6. Cash, Ledger, and Balance Projections

### Store of record

Postgres

### Entities

- cash balance projections
- available, pending, settled balance rows
- ledger-style entries
- fee debits
- interest accruals
- reconciliation status

### Why

- deterministic correctness matters
- balance derivation needs strong ordering and auditable history
- compensating records are easier to model than mutable documents

### Important note

If an upstream system owns the authoritative ledger, Postgres still stores the platform-side projected or reconciled view, with:

- source system
- as-of timestamp
- sync status

## 7. Billing

### Store of record

Postgres

### Entities

- fee schedules
- billing groups
- billing runs
- invoices
- posting records
- corrections
- reversals

### Why

- billing is a relational workflow
- approvals and reversals require strong data integrity
- auditability is non-negotiable

### MongoDB usage

- optional denormalized fee analytics views

## 8. Reporting and Snapshots

### Store of record

Split model

- Postgres for report metadata and generation jobs
- object storage for published artifacts
- MongoDB optional for rich structured payload snapshots

### Entities

Postgres:

- report definitions
- report jobs
- artifact references
- publication status

Object storage:

- PDFs
- generated statements
- downloadable reports

MongoDB optional:

- report snapshot documents
- rich narrative payloads
- denormalized performance payloads

### Why

- metadata and publication states are transactional
- artifacts are large binary objects
- some report payloads are easier to store as flexible documents

## 9. Documents and Vault

### Store of record

Split model

- Postgres for document metadata and access linkage
- object storage for the binary artifact

### Entities

Postgres:

- document records
- retention classes
- artifact references
- attachment links
- access classifications

Object storage:

- raw uploads
- signed forms
- generated disclosures
- statements

### Why

- metadata wants joins and permissions
- files want durable blob storage

## 10. External Reference Data and Security Master Projections

### Store of record

Authoritative source is external

### Local storage recommendation

- Redis for hot cache
- MongoDB or Postgres for local projections, depending on access pattern

### Use MongoDB when

- document shape is wide or changes often
- the main need is denormalized lookup or read performance
- full upstream history is not required in SQL joins

Examples:

- security profile documents
- ETF composition documents
- enriched instrument metadata for UI reads

### Use Postgres when

- the data participates in transactional workflows
- strict relational joins are needed
- reference rows are small and stable

Examples:

- allowed trading universe mappings
- internal product eligibility tables
- normalized benchmark mappings

## 11. AI Context and Analytical Read Models

### Store of record

Usually derived, not authoritative

### Recommended store

MongoDB

### Example documents

- household AI context summary
- advisor dashboard aggregates
- client narrative context pack
- search-oriented operational summaries

### Why

- document structure may evolve quickly
- denormalized bundles are useful
- source-of-truth constraints are weaker here

### Guardrail

Every derived document should include:

- source references
- generation time
- freshness metadata
- tenant ID

## 12. Audit and Event History

### Store of record

Postgres

### Entities

- audit events
- privileged action events
- workflow transition logs
- ingestion event checkpoints

### Why

- append-only behavior matters
- queryability by actor, tenant, workflow, and time matters
- compliance and support use cases need structured filtering

### Kafka relationship

Kafka is the transport, not the query store.

Persist important audit and workflow facts after consumption. Do not rely on Kafka alone as the long-term evidence store.

## 13. Redis Usage Guidelines

Redis is important, but it should not become a shadow database.

Safe uses:

- caching
- rate limiting
- locks
- ephemeral workflow coordination
- short-lived token or session support
- short-lived projections

Avoid using Redis as the only store for:

- workflow truth
- approvals
- transfers
- order state
- billing state
- audit events

## 14. Where MongoDB Still Fits Well

MongoDB is still useful in this architecture. It is just not the best choice for the operational core.

Best-fit MongoDB use cases here:

- denormalized dashboard read models
- flexible report snapshot payloads
- AI context bundles
- search-oriented aggregates
- cached or replicated external reference documents

If the team already has strong MongoDB operational expertise, MongoDB can be very effective for read-heavy projection stores.

## 15. Suggested First-Cut Database Split

If you need a pragmatic starting point:

### Postgres first

- tenants
- users
- roles and permissions
- households
- clients
- accounts
- onboarding cases
- transfer cases
- order intents
- order projections
- billing runs
- invoices
- document metadata
- audit events

### Redis first

- sessions
- rate limits
- idempotency keys
- workflow locks

### Object storage first

- raw uploads
- signed artifacts
- statements

### MongoDB later

Add MongoDB once you need:

- denormalized dashboard read models
- AI context documents
- flexible report snapshots
- broad search-oriented projections

## 16. Final Recommendation

Use Postgres for the core system of record because this platform is primarily a workflow, permissions, and correctness problem.

Use MongoDB where flexibility and denormalized reads create real leverage:

- AI context
- projections
- report payloads
- dynamic aggregates

That gives you the best of both:

- strong operational correctness
- flexible product read models
- clean separation between truth and derived views
