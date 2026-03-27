# API Server Spec v2

## 1. Purpose

The API server is the control plane for the wealth platform. It exposes advisor and client APIs, owns tenant-scoped business logic, enforces permissions, persists workflow state, and orchestrates external microservices.

Recommended implementation stack:

- Node.js
- TypeScript
- Hono
- Zod for request/response validation
- Postgres for operational truth
- Redis for cache and coordination
- KafkaJS for Kafka integration

It is not:

- the security master
- the OMS or EMS
- the execution venue
- the sole source of market/reference data
- the AI system

## 2. Responsibilities

The API server owns:

- tenant resolution and authentication
- within-tenant authorization
- household, client, and account registry
- onboarding and transfer case management
- billing and reporting orchestration
- document metadata and vault access rules
- advisor/client experience APIs
- workflow commands and status projections
- service orchestration to external microservices

The API server integrates with:

- security master and reference data services
- OMS/EMS and trading microservices
- money movement rails
- notification services
- AI sidecar

## 3. Recommended Structure

```text
apps/
├── api/
│   └── src/
│       ├── main.ts
│       ├── app.ts
│       ├── http/
│       │   ├── middleware/
│       │   ├── routes/
│       │   ├── presenters/
│       │   └── errors/
│       ├── modules/
│       │   ├── firms/
│       │   ├── users/
│       │   ├── households/
│       │   ├── clients/
│       │   ├── accounts/
│       │   ├── onboarding-cases/
│       │   ├── transfer-cases/
│       │   ├── portfolios/
│       │   ├── orders/
│       │   ├── billing/
│       │   ├── reports/
│       │   ├── documents/
│       │   ├── notifications/
│       │   ├── integrations/
│       │   ├── ai/
│       │   └── support/
│       ├── workflows/
│       ├── external/
│       │   ├── security-master/
│       │   ├── oms/
│       │   ├── transfers/
│       │   └── pricing/
│       ├── events/
│       ├── audit/
│       ├── db/
│       └── shared/
├── workers/
│   └── src/
│       ├── consumers/
│       ├── jobs/
│       └── workflows/
└── package.json
```

Module boundaries should match domain ownership, not UI menu names.

### 3.1 Module shape

Each module should be explicit and transport-agnostic:

```text
modules/<domain>/
├── routes.ts
├── schemas.ts
├── service.ts
├── repository.ts
├── types.ts
└── events.ts
```

Recommended rule:

- `routes.ts` only handles Hono request/response concerns
- `service.ts` owns orchestration and business rules
- `repository.ts` owns database reads and writes
- `events.ts` owns event names and publishing helpers

### 3.2 Validation and typing

Use Zod at the HTTP boundary and at selected internal integration boundaries.

Recommended pattern:

- route parses request with Zod
- route invokes service with typed input
- service returns typed result
- presenter serializes response

Avoid:

- inline ad hoc validation in handlers
- database models leaking directly into HTTP responses
- framework-specific types in business services

## 4. Request Pipeline

Recommended middleware order:

1. request ID
2. host and tenant resolution
3. authentication
4. actor and role resolution
5. permission enforcement
6. rate limiting
7. handler
8. audit emission

In Hono, these should be implemented as composed middleware plus route-level permission guards, not as hidden framework magic.

## 5. Authentication and Authorization

### 5.1 Authentication

Use tenant-scoped JWT access tokens and rotating refresh tokens.

Claims must include:

- subject user ID
- tenant ID
- actor type
- session ID
- issued and expiry timestamps

### 5.2 Authorization

The prior "no RBAC/ABAC" stance is removed.

Minimum role set:

- `firm_admin`
- `advisor`
- `trader`
- `operations`
- `billing_admin`
- `viewer`
- `support_impersonator`

Permissions should be capability-based, for example:

- `client.read`
- `account.open`
- `transfer.submit`
- `order.submit`
- `billing.post`
- `report.publish`
- `document.read_sensitive`
- `support.impersonate`

Some actions should also support approval policies:

- large money movement
- certain trade classes
- billing posting
- support impersonation

### 5.3 Service-to-service auth

Internal service calls must use service credentials, mTLS, signed service tokens, or equivalent. Shared-secret-only designs are acceptable only as an interim local-dev simplification.

## 5.4 Dependency wiring

Because Hono is lightweight, dependency wiring must be explicit.

Use one of:

- a simple composition root in `app.ts`
- a lightweight container with explicit registration

Do not rely on implicit global singletons for:

- database connections
- Kafka producers or consumers
- external service clients
- audit emitters
- permission evaluators

## 6. Data Ownership Model

### 6.1 API-owned records

The API server is authoritative for:

- tenant metadata
- users and roles
- households, clients, and account registration records
- onboarding and transfer cases
- workflow states
- document metadata
- billing configuration and run metadata
- audit trails

### 6.2 External authoritative records

The API server is not authoritative for:

- security definitions
- order routing state
- execution fills
- external money movement rail status

The API server stores local intent records and synchronized projections, each with:

- upstream source name
- upstream ID
- last synced timestamp
- sync status

## 7. Core Resource Model

### 7.1 Tenant and firm resources

- `Firm`
- `FirmBranding`
- `User`
- `UserRoleAssignment`
- `IntegrationConnection`

### 7.2 Client and account resources

- `Household`
- `ClientPerson`
- `ClientEntity`
- `AccountRegistration`
- `Account`
- `Beneficiary`
- `TrustedContact`
- `ExternalBankAccount`

### 7.3 Workflow resources

- `OnboardingCase`
- `TransferCase`
- `OperationalTask`
- `ApprovalRequest`

### 7.4 Portfolio and order resources

- `ModelPortfolio`
- `ModelAssignment`
- `RebalanceProposal`
- `OrderIntent`
- `OrderProjection`
- `ExecutionProjection`

### 7.5 Billing and reporting resources

- `FeeSchedule`
- `BillingRun`
- `Invoice`
- `ReportDefinition`
- `ReportArtifact`

### 7.6 Records and audit resources

- `DocumentRecord`
- `VaultArtifact`
- `AuditEvent`

## 8. API Design Rules

### 8.1 Commands vs queries

Use command endpoints for state transitions and external orchestration:

- `POST /.../submit`
- `POST /.../approve`
- `POST /.../cancel`
- `POST /.../release`

Use standard reads for projections and current status:

- `GET /...`
- `GET /.../:id`

Avoid overloading a single `PATCH` endpoint with workflow semantics.

### 8.2 Idempotency

All mutating endpoints that can create external side effects must accept an idempotency key, especially:

- transfer submission
- order submission
- billing posting
- invitation resend
- support impersonation sessions

### 8.3 Async responses

Long-running commands should return `202 Accepted` with:

- local workflow or case ID
- current status
- polling URL

## 9. Major API Domains

## 9.1 Firms and Users

Example endpoints:

- `POST /api/firms`
- `GET /api/firms/current`
- `POST /api/users/invitations`
- `GET /api/users`
- `PUT /api/users/:id/roles`
- `POST /api/support/impersonation`

Rules:

- only `firm_admin` may manage roles
- impersonation requires explicit permission and audit event emission

## 9.2 Households, Clients, and Accounts

Example endpoints:

- `POST /api/households`
- `POST /api/clients/persons`
- `POST /api/clients/entities`
- `POST /api/accounts`
- `GET /api/accounts/:id`
- `GET /api/accounts/:id/summary`

Rules:

- account creation creates registration intent records, not necessarily an active account
- accounts remain lifecycle-managed through onboarding or internal operations

## 9.3 Onboarding Cases

Replace the old linear session model with a case model.

Example endpoints:

- `POST /api/onboarding-cases`
- `GET /api/onboarding-cases/:id`
- `POST /api/onboarding-cases/:id/submit`
- `POST /api/onboarding-cases/:id/request-client-action`
- `POST /api/onboarding-cases/:id/approve`
- `POST /api/onboarding-cases/:id/reject`
- `POST /api/onboarding-cases/:id/add-note`

Suggested statuses:

- `draft`
- `pending_client_action`
- `submitted`
- `pending_internal_review`
- `pending_external_review`
- `exception`
- `approved`
- `rejected`
- `activated`

Rules:

- approval is explicit
- exception states are durable
- audit notes are append-only
- documents and disclosures are part of the case

## 9.4 Transfers

Transfers are independent cases and can be created from onboarding or from active accounts.

Example endpoints:

- `POST /api/transfers`
- `GET /api/transfers/:id`
- `POST /api/transfers/:id/submit`
- `POST /api/transfers/:id/cancel`
- `POST /api/transfers/:id/retry-sync`

Supported types:

- ACH deposit
- ACH withdrawal
- ACAT full
- ACAT partial
- internal journal
- wire in
- wire out

Transfer statuses:

- `draft`
- `submitted`
- `pending_verification`
- `pending_external_review`
- `in_transit`
- `completed`
- `failed`
- `cancelled`
- `reversed`
- `exception`

Rules:

- external submission returns `202`
- platform persists the transfer intent before calling the rail service
- platform ingests status updates asynchronously

## 9.5 Portfolio Models and Rebalancing

Example endpoints:

- `POST /api/models`
- `GET /api/models`
- `POST /api/model-assignments`
- `POST /api/rebalance-proposals`
- `GET /api/rebalance-proposals/:id`
- `POST /api/rebalance-proposals/:id/release`
- `POST /api/rebalance-proposals/:id/cancel`

Rules:

- proposal generation is internal platform logic
- release is a separate command
- release may emit one or more order intents
- proposal records must retain the exact assumptions used to generate them

## 9.6 Orders and Trading

The API server should expose order intent and projection APIs, not pretend to be the OMS.

Example endpoints:

- `POST /api/order-intents`
- `GET /api/order-intents/:id`
- `POST /api/order-intents/:id/submit`
- `POST /api/order-intents/:id/cancel`
- `GET /api/orders/:id`
- `GET /api/executions/:id`

Rules:

- `order-intents` are platform-owned records
- `orders` and `executions` are synchronized projections from OMS/EMS or trading services
- submit path performs local checks before calling OMS
- duplicate submissions are blocked via idempotency and workflow state

Submission flow:

1. validate actor permission
2. validate account and policy constraints
3. create `OrderIntent`
4. submit to OMS via synchronous client
5. persist upstream ID and accepted status
6. await subsequent fill/reject/cancel events asynchronously

## 9.7 Billing

Example endpoints:

- `POST /api/fee-schedules`
- `POST /api/billing-runs`
- `GET /api/billing-runs/:id`
- `POST /api/billing-runs/:id/approve`
- `POST /api/billing-runs/:id/post`
- `POST /api/invoices/:id/reverse`

Rules:

- calculation, approval, and posting are separate steps
- billing posting may create money movement or ledger-affecting commands downstream
- reversals create new records, not destructive edits

## 9.8 Reports and Statements

Example endpoints:

- `POST /api/reports/generate`
- `GET /api/reports/:id`
- `GET /api/reports/:id/artifacts`
- `POST /api/statements/generate`

Rules:

- report generation is asynchronous
- published artifacts are immutable
- generation inputs should be versioned or snapshot-based

## 9.9 Documents

Example endpoints:

- `POST /api/documents`
- `GET /api/documents/:id`
- `POST /api/documents/:id/classify`
- `POST /api/documents/:id/attach`
- `GET /api/vault/artifacts/:id`

Rules:

- raw uploads and signed artifacts are separate concepts
- access to sensitive documents must be permissioned and audited

## 9.10 AI

Example endpoints:

- `POST /api/ai/chat`
- `POST /api/ai/reports/narrative`
- `POST /api/ai/documents/extract`

Rules:

- AI endpoints are read-mostly and assistive
- AI-generated actions must come back as recommendations or drafts
- any execution requires normal platform command paths and permissions

## 10. External Service Integration Patterns

## 10.1 Security master

Use:

- sync calls for point reads
- local cache or projection for repeated UI access

Do not:

- duplicate ownership of security reference records without source metadata

## 10.2 OMS/EMS

Use:

- sync submission and cancel requests
- async Kafka ingestion for order state and fills

Persist locally:

- intent records
- upstream identifiers
- state snapshots
- sync timestamps

## 10.3 Transfer rails

Use:

- sync initiation where available
- async lifecycle consumption for status changes

Required support:

- retries
- dead-letter handling
- manual repair tooling

In Node.js, keep these integrations out of Hono route handlers. Submission may start from HTTP, but retries, event ingestion, and repair tooling belong in workers or workflow executors.

## 11. Event Ingestion

The API server needs consumers for:

- order accepted/rejected/fill events
- transfer lifecycle events
- price and security projection refresh events
- billing or statement completion events from workers

Consumers must be:

- idempotent
- tenant-aware
- replay-safe

Recommended implementation:

- Hono app for synchronous APIs
- separate worker process for Kafka consumers and background jobs

Do not run heavy Kafka consumption inside the same runtime path as latency-sensitive HTTP traffic unless throughput is trivially small.

## 12. Audit and Compliance Requirements

Emit audit events for:

- authentication events
- role changes
- onboarding approval or rejection
- transfer submission and cancellation
- order submission and cancellation
- billing approval and posting
- document access to sensitive artifacts
- support impersonation

Audit events must be append-only and queryable by:

- tenant
- actor
- resource
- workflow
- date range

## 13. Error Model

Use a stable machine-readable error envelope.

Representative codes:

- `TENANT_NOT_FOUND`
- `UNAUTHORIZED`
- `FORBIDDEN`
- `VALIDATION_ERROR`
- `APPROVAL_REQUIRED`
- `INVALID_WORKFLOW_STATE`
- `IDEMPOTENCY_CONFLICT`
- `UPSTREAM_SERVICE_UNAVAILABLE`
- `UPSTREAM_REJECTED`
- `PROJECTION_STALE`
- `RATE_LIMITED`

Differentiate:

- local validation failure
- upstream rejection
- async in-flight state

## 14. Key Replacements To Prior API Spec

1. Replace "all advisors have equal access" with explicit roles and permissions.

2. Replace the onboarding session happy path with onboarding cases and approval flows.

3. Replace direct trade assumptions with order intent plus OMS projection.

4. Replace synchronous side effects with async workflow commands where the operation is long-running.

5. Replace "Go owns all market/reference data" with platform-owned orchestration over external services.

6. Keep AI behind controlled platform APIs instead of making it part of the operational write path.

7. Replace language-specific server assumptions with a Node.js + TypeScript + Hono modular control-plane implementation.
