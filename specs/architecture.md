# Architecture Spec v2

## 1. System Intent

The platform is a multi-tenant advisor operating system for RIAs. It is not the owner of every financial capability. Some critical capabilities already exist as separate microservices, including market and trading infrastructure such as:

- security master
- order management system (OMS)
- execution management system (EMS)
- market and reference data services
- potentially custody, clearing, and reconciliation adapters

This platform acts as the advisor-facing control plane and workflow orchestrator over those systems.

## 2. Core Architectural Principle

The system should be designed around three classes of services:

1. Platform-owned domains
   Tenant, identity, permissions, households, clients, onboarding cases, documents, workflow state, billing configuration, reporting orchestration, advisor/client experience, and AI orchestration.

2. External financial infrastructure services
   Security master, OMS, EMS, pricing, reference data, transfer rails, and other market or operational infrastructure. These are authoritative within their domains.

3. Derived intelligence services
   AI and analytics services that consume platform and external data but do not become the source of truth for regulated actions.

## 3. High-Level Topology

```text
                        ┌───────────────────────────────┐
                        │       Web / Mobile Apps       │
                        │ advisor portal, client portal │
                        └───────────────┬───────────────┘
                                        │
                        ┌───────────────▼───────────────┐
                        │        API Gateway Layer       │
                        │ host routing, auth, throttling │
                        └───────────────┬───────────────┘
                                        │
                ┌───────────────────────┼────────────────────────┐
                │                       │                        │
    ┌───────────▼───────────┐ ┌────────▼────────┐ ┌─────────────▼────────────┐
    │   Core Platform API   │ │ Workflow/Jobs   │ │      AI Sidecar          │
    │ tenant + business API │ │ orchestrations  │ │ augmentation only        │
    └───────────┬───────────┘ └────────┬────────┘ └─────────────┬────────────┘
                │                      │                         │
                ├──────────────┬───────┴─────────────┬──────────┤
                │              │                     │          │
    ┌───────────▼───────┐ ┌────▼────────────┐ ┌─────▼───────┐ ┌▼────────────────┐
    │ Relational Store  │ │ Object Storage  │ │ Redis/Cache │ │ Event Bus/Kafka │
    │ system of record  │ │ docs/artifacts  │ │ sessions    │ │ async backbone  │
    └───────────────────┘ └─────────────────┘ └─────────────┘ └─────┬──────────┘
                                                                      │
                                 ┌────────────────────────────────────┼─────────────────────────────────┐
                                 │                                    │                                 │
                      ┌──────────▼──────────┐             ┌───────────▼───────────┐          ┌──────────▼──────────┐
                      │ Security Master Svc │             │ OMS / EMS / Routing   │          │ Money Movement Svc  │
                      │ ref + market data   │             │ orders/executions      │          │ ACH/ACAT/wire/etc   │
                      └─────────────────────┘             └────────────────────────┘          └─────────────────────┘
```

## 4. Deployment Model

### 4.1 Multi-tenancy

Each RIA firm is a logical tenant. Isolation is enforced through:

- tenant-scoped authentication claims
- tenant-scoped data partitioning
- tenant-aware workflow execution
- tenant-aware event metadata
- role and permission evaluation inside the tenant

Subdomain routing remains valid:

- `{slug}.wealthadvisor.com`

But tenant isolation alone is insufficient. The platform must also support within-tenant permissions for:

- firm admin
- advisor
- trader
- operations
- billing admin
- read-only or support roles

### 4.2 Storage model

Recommended storage split:

- relational DB for operational truth
  Users, permissions, clients, households, accounts, onboarding cases, workflow states, billing runs, transfer intents, order intents, audit metadata.

- object storage for immutable artifacts
  Signed forms, uploaded documents, generated statements, exports, secure attachments.

- Redis for cache and ephemeral coordination
  Session state, rate limiting, workflow locks, small computed caches.

- analytics/read models as separate projections
  Reporting snapshots, AI context summaries, performance aggregates.

MongoDB-only is not recommended for the operational core because onboarding, money movement, billing, and order workflows require stronger transactional guarantees and clearer relational modeling.

## 5. Domain Boundaries

## 5.1 Platform-owned bounded contexts

### Tenant and Identity

Owns:

- tenant lifecycle
- user accounts
- invitations
- MFA
- sessions
- roles and permissions
- support access controls

### Client and Account Registry

Owns:

- households
- people and entity records
- advisor relationships
- account registrations
- beneficiaries
- trusted contacts
- document associations

### Workflow and Case Management

Owns:

- onboarding cases
- transfer cases
- approval queues
- exception states
- operational tasks
- SLAs and reminders

### Documents and Records

Owns:

- document metadata
- retention class
- versioning metadata
- vault access rules
- immutable signed artifact references

### Billing and Reporting Orchestration

Owns:

- fee schedule definitions
- billing calendars
- invoice generation orchestration
- report generation orchestration
- reporting access controls

### Experience APIs

Owns:

- advisor UI APIs
- client UI APIs
- notifications
- dashboard read models

## 5.2 External authoritative services

These are not reimplemented in the platform. They are integrated.

### Security Master / Reference Data

Authoritative for:

- security metadata
- classifications
- benchmark and market reference attributes
- instrument eligibility data

Integration styles:

- synchronous API/gRPC/proto request for point lookup
- Kafka-fed local projection for hot read paths

### OMS / EMS / Trading Infrastructure

Authoritative for:

- order acceptance
- route state
- execution fills
- order rejections
- settlement status if emitted by trading stack

Integration styles:

- synchronous request for order submission and cancellation
- event-driven updates over Kafka for state transitions

### Money Movement / Transfer Infrastructure

Authoritative for:

- external rail submission
- verification state
- rail status changes
- return and reversal events

Integration styles:

- synchronous initiation where needed
- asynchronous status ingestion over webhooks, API polling, or Kafka

## 6. Integration Rules

### 6.1 Kafka vs synchronous calls

Use synchronous API/gRPC/proto calls when:

- the user action requires immediate validation or acceptance feedback
- the request is bounded and low-latency
- the platform is requesting the latest authoritative state

Examples:

- validate an instrument before presenting an order ticket
- submit an order intent to OMS
- fetch a specific security record

Use Kafka or event ingestion when:

- the upstream domain is long-running or stateful
- status transitions happen outside the request lifecycle
- the platform needs projections or read models
- retries and eventual consistency are expected

Examples:

- order fills and cancellations
- ACAT lifecycle updates
- ACH return events
- end-of-day price or position projections

### 6.2 Local projections

The platform may maintain local read models for external services, but those projections are:

- cached or replicated views
- not the source of truth for the upstream domain
- tagged with source, version, and as-of timestamps

### 6.3 Idempotency and correlation

Every cross-service command must carry:

- request ID
- tenant ID
- actor ID
- idempotency key where the action mutates state
- correlation ID for workflow tracing

## 7. Workflow Architecture

The platform must not model onboarding, transfers, trading, and billing as direct CRUD writes. They are workflows.

### 7.1 Onboarding workflow

Stages:

- draft capture
- client completion
- internal review
- external submission where required
- exception resolution
- approval
- activation

### 7.2 Transfer workflow

Stages:

- intent created
- validation
- submission
- verification or review
- in-flight monitoring
- completion, failure, or reversal

### 7.3 Trading workflow

Stages:

- proposal generation
- advisor approval if required
- order submission to OMS
- execution ingestion
- allocation and position update
- settlement projection and reconciliation

### 7.4 Billing workflow

Stages:

- schedule freeze
- calculation
- review
- posting
- collection
- exception or reversal

These workflows should run in a dedicated orchestration layer or job framework and persist durable workflow state.

## 8. Event Model

Recommended event families:

- tenant events
- user and permission events
- onboarding events
- transfer events
- order and execution events
- cash and billing events
- reporting events
- document events
- AI audit events

Examples:

- `onboarding.case_submitted`
- `onboarding.case_exceptioned`
- `transfer.submitted`
- `transfer.completed`
- `order.submitted`
- `order.rejected`
- `execution.fill_received`
- `billing.run_posted`
- `statement.generated`

Events support:

- workflow progression
- audit trails
- notifications
- read model updates

Events do not replace transactional writes to platform-owned records.

## 9. AI Sidecar Position

The AI sidecar remains a separate service, but its contract is narrower than the current draft suggests.

The sidecar may:

- summarize platform data
- classify and extract documents
- generate narratives
- answer advisor questions using current context
- suggest actions

The sidecar may not:

- be the source of truth for holdings, balances, orders, or transfer status
- make permission decisions
- silently execute regulated actions
- bypass platform workflow controls

The sidecar reads via the platform API and read models. It should not call OMS, EMS, or financial infrastructure services directly unless the platform explicitly brokers those read permissions.

## 10. Security and Control Model

Required controls:

- MFA for all advisors
- support impersonation with explicit approval and audit
- role-based permissions within tenant
- scoped service-to-service auth
- secret rotation
- immutable privileged-action audit
- document access logging
- least-privilege access for workers and integration adapters

## 11. Observability

Required identifiers across all services:

- request ID
- correlation ID
- workflow ID
- tenant ID
- actor ID

Required telemetry:

- API latency and error rates
- workflow success/failure/stuck counts
- consumer lag for Kafka-fed projections
- external dependency latency
- order/transfer/billing exception dashboards

## 12. Architectural Decisions Replacing Prior Assumptions

1. The platform is not the owner of security master or OMS/EMS concerns.

2. The platform is an orchestrating control plane with durable workflow state.

3. AI is additive, not foundational to regulated write paths.

4. Within-tenant permissions are required.

5. External service integration must support both synchronous request paths and asynchronous event ingestion.

6. Local projections of external systems are allowed, but they do not replace upstream authority.

## 13. Implementation Baseline

The recommended implementation baseline for the platform control plane is:

- Node.js
- TypeScript
- Hono for HTTP transport
- Postgres for operational truth
- Redis for cache and coordination
- Kafka for async event ingestion and projection updates

This stack choice does not change the architectural rules above. Hono should remain a thin transport layer over explicit domain modules, workflows, and integration adapters.

### 13.1 Why Hono is acceptable here

Hono fits the platform if the team prefers:

- explicit request handling
- minimal framework magic
- low decorator usage
- transport-layer simplicity

Hono is a good fit for the control plane as long as:

- business logic does not live directly in route handlers
- validation is explicit at module boundaries
- workflows and consumers are not coupled to HTTP transport
- integration clients are isolated behind adapters

### 13.2 Architectural guardrail for Hono

Do not let the codebase collapse into:

- routes calling database code directly
- route-local validation and ad hoc orchestration
- cross-module imports without boundaries
- Kafka consumers sharing business logic through handler files

The desired shape is:

- Hono for routing and HTTP concerns
- services/use cases for orchestration
- repositories for persistence
- workflow runners for long-running process state
- external adapters for OMS, security master, and transfer services
