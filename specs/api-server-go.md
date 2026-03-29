# API Server Spec v2 (Go Edition)

## 1. Purpose

The API server is the control plane for the wealth platform. It exposes advisor and client APIs, owns tenant-scoped business logic, enforces permissions, persists workflow state, and orchestrates external microservices.

Implementation stack:

- Go 1.23+
- Gin for HTTP routing and middleware
- MongoDB for operational truth, caching, and coordination
- go-playground/validator for request validation
- mongo-go-driver for MongoDB access
- confluent-kafka-go or segmentio/kafka-go for Kafka integration

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
│   ├── cmd/
│   │   └── server/
│   │       └── main.go
│   ├── internal/
│   │   ├── app/
│   │   │   ├── app.go              # composition root, DI wiring
│   │   │   └── config.go           # env-based configuration
│   │   ├── http/
│   │   │   ├── middleware/
│   │   │   │   ├── requestid.go
│   │   │   │   ├── tenant.go
│   │   │   │   ├── auth.go
│   │   │   │   ├── actor.go
│   │   │   │   ├── permission.go
│   │   │   │   ├── ratelimit.go
│   │   │   │   └── audit.go
│   │   │   ├── routes/
│   │   │   ├── presenters/
│   │   │   └── errors/
│   │   ├── modules/
│   │   │   ├── firms/
│   │   │   ├── users/
│   │   │   ├── households/
│   │   │   ├── clients/
│   │   │   ├── accounts/
│   │   │   ├── onboarding/
│   │   │   ├── transfers/
│   │   │   ├── portfolios/
│   │   │   ├── orders/
│   │   │   ├── billing/
│   │   │   ├── reports/
│   │   │   ├── documents/
│   │   │   ├── notifications/
│   │   │   ├── integrations/
│   │   │   ├── ai/
│   │   │   └── support/
│   │   ├── workflows/
│   │   ├── external/
│   │   │   ├── secmaster/
│   │   │   ├── oms/
│   │   │   ├── transfers/
│   │   │   └── pricing/
│   │   ├── events/
│   │   ├── audit/
│   │   ├── db/
│   │   │   └── mongo.go            # MongoDB client, indexes, migrations
│   │   └── shared/
│   ├── pkg/                         # reusable packages (errors, pagination, etc.)
│   └── go.mod
├── workers/
│   ├── cmd/
│   │   └── worker/
│   │       └── main.go
│   ├── internal/
│   │   ├── consumers/
│   │   ├── jobs/
│   │   └── workflows/
│   └── go.mod
└── README.md
```

Module boundaries should match domain ownership, not UI menu names.

### 3.1 Module shape

Each module should be explicit and transport-agnostic:

```text
modules/<domain>/
├── handler.go          # Gin handler functions (HTTP concerns only)
├── service.go          # business logic and orchestration
├── repository.go       # MongoDB reads and writes
├── models.go           # domain types and validation tags
└── events.go           # event names and publishing helpers
```

Recommended rule:

- `handler.go` only handles Gin request/response concerns
- `service.go` owns orchestration and business rules
- `repository.go` owns database reads and writes via mongo-go-driver
- `events.go` owns event names and publishing helpers

### 3.2 Validation and typing

Use struct tags with `go-playground/validator` at the HTTP boundary.

Recommended pattern:

```go
// handler.go
func (h *Handler) CreateHousehold(c *gin.Context) {
    var req CreateHouseholdRequest
    if err := c.ShouldBindJSON(&req); err != nil {
        c.JSON(400, NewValidationError(err))
        return
    }
    result, err := h.service.CreateHousehold(c.Request.Context(), req.ToDomain())
    if err != nil {
        HandleError(c, err)
        return
    }
    c.JSON(201, PresentHousehold(result))
}

// models.go
type CreateHouseholdRequest struct {
    Name            string `json:"name" binding:"required,min=1,max=200"`
    PrimaryAdvisorID string `json:"primary_advisor_id" binding:"required"`
}
```

Avoid:

- inline ad hoc validation in handlers
- MongoDB documents leaking directly into HTTP responses
- Gin-specific types in business services

### 3.3 MongoDB document design

Each aggregate root maps to a MongoDB collection. Use `tenant_id` as a required field on every tenant-scoped document for hard multi-tenant isolation.

Do not transliterate every relational table into its own collection. In the MongoDB variant of this platform:

- use one collection per aggregate root
- embed bounded child arrays when the child data is read with the parent and shares the parent's lifecycle
- keep append-only history, many-to-many links, auth/session material, and high-fanout projections in separate collections
- prefer application-assigned UUID strings for business-resource `_id` values so IDs are stable across HTTP APIs, Kafka events, and external systems
- keep regulated resources soft-deletable only through status fields such as `revoked_at`, `ended_at`, `archived_at`, `closed_at`, or `is_current_version`

```go
// db/mongo.go
type BaseDocument struct {
    ID        string    `bson:"_id" json:"id"` // UUID
    TenantID  string    `bson:"tenant_id" json:"tenant_id"`
    CreatedAt time.Time `bson:"created_at" json:"created_at"`
    UpdatedAt time.Time `bson:"updated_at" json:"updated_at"`
    Version   int64     `bson:"version" json:"version"`
}
```

Index strategy:

- Every tenant-scoped collection gets a compound index beginning with `{tenant_id: 1, ...}`
- Use unique compound indexes for idempotency (e.g., `{tenant_id: 1, idempotency_key: 1}`)
- Use TTL indexes for session, token, cache, and dead-letter documents
- Use partial indexes for status-filtered queries (e.g., active transfers only)
- Use multikey indexes sparingly for embedded arrays that must be queried directly
- Use `version` for optimistic concurrency on mutable aggregates
- Encrypt sensitive-at-rest fields (`ssn`, `tin`, bank account numbers, MFA secrets) before persistence and also persist masked or last-four derivatives for read paths

### 3.4 Dependency wiring

Use explicit constructor injection via a composition root in `app.go`:

```go
// internal/app/app.go
type App struct {
    Router          *gin.Engine
    DB              *mongo.Database
    HouseholdSvc    *households.Service
    AccountSvc      *accounts.Service
    TransferSvc     *transfers.Service
    // ... all services
    AuditEmitter    *audit.Emitter
    KafkaProducer   *kafka.Producer
}

func New(cfg Config) (*App, error) {
    db, err := connectMongo(cfg.MongoURI, cfg.MongoDB)
    if err != nil {
        return nil, err
    }

    auditEmitter := audit.NewEmitter(db)
    householdRepo := households.NewRepository(db)
    householdSvc := households.NewService(householdRepo, auditEmitter)
    // ... wire all dependencies explicitly

    router := gin.New()
    registerMiddleware(router, cfg, db)
    registerRoutes(router, householdSvc, accountSvc, ...)

    return &App{Router: router, DB: db, ...}, nil
}
```

Do not rely on implicit global singletons for:

- database connections
- Kafka producers or consumers
- external service clients
- audit emitters
- permission evaluators

## 4. Request Pipeline

Recommended Gin middleware order:

1. request ID (`X-Request-ID` generation/propagation)
2. host and tenant resolution
3. authentication (JWT validation)
4. actor and role resolution
5. permission enforcement
6. rate limiting
7. handler
8. audit emission

In Gin, these are composed as `gin.HandlerFunc` middleware plus route-group-level permission guards:

```go
func SetupRouter(app *App) *gin.Engine {
    r := gin.New()
    r.Use(middleware.RequestID())
    r.Use(middleware.TenantResolver(app.DB))
    r.Use(middleware.Auth(app.JWTVerifier))
    r.Use(middleware.ActorResolver(app.DB))
    r.Use(middleware.RateLimit(app.RateLimiter))

    api := r.Group("/api")
    {
        admin := api.Group("", middleware.RequirePermission("firm.admin"))
        admin.POST("/firms", app.FirmHandler.Create)

        advisor := api.Group("", middleware.RequirePermission("client.read"))
        advisor.GET("/households", app.HouseholdHandler.List)
        advisor.GET("/households/:id", app.HouseholdHandler.Get)
    }
    return r
}
```

## 5. Authentication and Authorization

### 5.1 Authentication

Use tenant-scoped JWT access tokens and rotating refresh tokens.

Claims must include:

- subject user ID
- tenant ID
- actor type
- session ID
- issued and expiry timestamps

```go
type JWTClaims struct {
    jwt.RegisteredClaims
    TenantID  string `json:"tenant_id"`
    ActorType string `json:"actor_type"` // "advisor", "admin", "trader", etc.
    SessionID string `json:"session_id"`
}
```

### 5.2 Authorization

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

```go
// internal/http/middleware/permission.go
func RequirePermission(perm string) gin.HandlerFunc {
    return func(c *gin.Context) {
        actor := GetActor(c)
        if !actor.HasPermission(perm) {
            c.AbortWithStatusJSON(403, ErrorResponse{
                Code:    "FORBIDDEN",
                Message: "Missing required permission: " + perm,
            })
            return
        }
        c.Next()
    }
}
```

### 5.3 Service-to-service auth

Internal service calls must use service credentials, mTLS, signed service tokens, or equivalent. Shared-secret-only designs are acceptable only as an interim local-dev simplification.

## 6. Data Ownership Model

### 6.1 API-owned records (MongoDB collections)

The API server is authoritative for:

- `firms`
- `users`, `invitations`, `roles`, `permissions`, `role_permissions`, `user_role_assignments`
- `sessions`, `refresh_tokens`, `mfa_factors`, `mfa_recovery_codes`, `service_accounts`, `impersonation_sessions`
- `households`, `client_persons`, `client_entities`, `advisor_relationships`
- `accounts`, `account_status_transitions`, `external_bank_accounts`
- `onboarding_cases`, `transfer_cases`, `approval_policies`, `approval_requests`, `operational_tasks`
- `case_exceptions`, `case_notes`, `workflow_transitions`, `sla_policies`, `sla_timers`
- `document_records`, `retention_classes`, `document_attachments`, `document_access_log`
- `model_portfolios`, `marketplace_models`, `marketplace_subscriptions`, `model_assignments`, `rebalance_rules`, `rebalance_proposals`
- `order_intents`, `order_projections`, `execution_projections`
- `fee_schedules`, `billing_scope_assignments`, `billing_calendars`, `billing_runs`, `invoices`, `billing_exceptions`
- `report_definitions`, `report_definition_versions`, `report_snapshots`, `report_jobs`, `report_artifacts`
- `notifications`, `notification_preferences`
- `audit_events` — audit trails

### 6.2 External authoritative records

The API server is not authoritative for:

- security definitions
- order routing state
- execution fills
- external money movement rail status

The API server stores local intent records and synchronized projections, each with:

```go
type SyncMetadata struct {
    UpstreamSource string    `bson:"upstream_source"`
    UpstreamID     string    `bson:"upstream_id"`
    LastSyncedAt   time.Time `bson:"last_synced_at"`
    SyncStatus     string    `bson:"sync_status"` // "synced", "stale", "failed"
}
```

### 6.3 MongoDB aggregate strategy

The broader architecture docs prefer Postgres for the operational core. This document is the Mongo-backed edition of the platform, so the collection model must be intentional rather than a table-for-table port.

Detailed aggregate rules, collection schemas, indexes, and Mermaid diagrams live in [specs/api-server-go-data-model.md](/Users/eswar/Desktop/wealth-advisor/specs/api-server-go-data-model.md).

Use these summary rules in the main API spec:

- use aggregate-root collections with explicit names such as `client_persons`, `document_records`, and `order_intents`
- embed bounded child state when it is read with the parent and shares the parent lifecycle
- keep append-only history, auth/session material, many-to-many links, and high-fanout projections in separate collections
- require `tenant_id` on every tenant-scoped document and use UUID string `_id` values
- keep the sidecar storage model separate: the sidecar can continue using `pgvector` plus Redis without being forced into the API server's MongoDB model

## 7. Core Resource Model

### 7.1 Tenant and firm resources

- `Firm`
- `FirmBranding`
- `User`
- `Invitation`
- `Role`
- `Permission`
- `UserRoleAssignment`
- `Session`
- `RefreshToken`
- `MFAFactor`
- `ServiceAccount`
- `ImpersonationSession`
- `IntegrationConnection`

### 7.2 Client and account resources

- `Household`
- `ClientPerson`
- `ClientEntity`
- `AdvisorRelationship`
- `AccountRegistration`
- `Account`
- `Beneficiary`
- `TrustedContact`
- `AuthorizedSigner`
- `ExternalBankAccount`

### 7.3 Workflow resources

- `OnboardingCase`
- `TransferCase`
- `OperationalTask`
- `ApprovalRequest`
- `CaseException`
- `CaseNote`
- `WorkflowTransition`
- `SlaTimer`

### 7.4 Portfolio and order resources

- `ModelPortfolio`
- `MarketplaceModel`
- `MarketplaceSubscription`
- `ModelAssignment`
- `RebalanceRule`
- `RebalanceProposal`
- `OrderIntent`
- `OrderProjection`
- `ExecutionProjection`

### 7.5 Billing, reporting, and experience resources

- `FeeSchedule`
- `BillingScopeAssignment`
- `BillingCalendar`
- `BillingRun`
- `BillingException`
- `Invoice`
- `ReportDefinition`
- `ReportSnapshot`
- `ReportJob`
- `ReportArtifact`
- `Notification`
- `NotificationPreference`

### 7.6 Records and audit resources

- `DocumentRecord`
- `DocumentAttachment`
- `RetentionClass`
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

All mutating endpoints that can create external side effects must accept an idempotency key via the `Idempotency-Key` header, especially:

- transfer submission
- order submission
- billing posting
- invitation resend
- support impersonation sessions

MongoDB implementation:

```go
// Check idempotency before processing
filter := bson.M{
    "tenant_id":       tenantID,
    "idempotency_key": idempotencyKey,
}
var existing IdempotencyRecord
err := coll.FindOne(ctx, filter).Decode(&existing)
if err == nil {
    // Return cached response
    c.JSON(existing.StatusCode, existing.Response)
    return
}
// Process request, then store result
```

### 8.3 Async responses

Long-running commands should return `202 Accepted` with:

- local workflow or case ID
- current status
- polling URL

## 9. Major API Domains

### 9.1 Firms and Users

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

### 9.2 Households, Clients, and Accounts

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

### 9.3 Onboarding Cases

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

### 9.4 Transfers

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

### 9.5 Portfolio Models and Rebalancing

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

### 9.6 Orders and Trading

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
3. create `OrderIntent` document in MongoDB
4. submit to OMS via synchronous HTTP client
5. persist upstream ID and accepted status
6. await subsequent fill/reject/cancel events asynchronously via Kafka consumer

### 9.7 Billing

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

### 9.8 Reports and Statements

Example endpoints:

- `POST /api/reports/generate`
- `GET /api/reports/:id`
- `GET /api/reports/:id/artifacts`
- `POST /api/statements/generate`

Rules:

- report generation is asynchronous
- published artifacts are immutable
- generation inputs should be versioned or snapshot-based

### 9.9 Documents

Example endpoints:

- `POST /api/documents`
- `GET /api/documents/:id`
- `POST /api/documents/:id/classify`
- `POST /api/documents/:id/attach`
- `GET /api/vault/artifacts/:id`

Rules:

- raw uploads and signed artifacts are separate concepts
- access to sensitive documents must be permissioned and audited

### 9.10 AI

Example endpoints:

- `POST /api/ai/chat`
- `POST /api/ai/reports/narrative`
- `POST /api/ai/documents/extract`

Rules:

- AI endpoints are read-mostly and assistive
- AI-generated actions must come back as recommendations or drafts
- any execution requires normal platform command paths and permissions
- the API server proxies AI requests to the Python sidecar with tenant context headers (`X-Tenant-ID`, `X-Actor-ID`, `X-Access-Scope`)

## 10. External Service Integration Patterns

### 10.1 Security master

Use:

- sync calls for point reads
- MongoDB cache collection with TTL index for repeated UI access

Do not:

- duplicate ownership of security reference records without source metadata

### 10.2 OMS/EMS

Use:

- sync submission and cancel requests via HTTP client
- async Kafka ingestion for order state and fills

Persist locally in MongoDB:

- intent records
- upstream identifiers
- state snapshots
- sync timestamps

### 10.3 Transfer rails

Use:

- sync initiation where available
- async lifecycle consumption for status changes

Required support:

- retries
- dead-letter handling (MongoDB collection with TTL)
- manual repair tooling

In Go, keep these integrations out of Gin route handlers. Submission may start from HTTP, but retries, event ingestion, and repair tooling belong in the worker process.

## 11. Event Ingestion

The API server needs Kafka consumers for:

- order accepted/rejected/fill events
- transfer lifecycle events
- price and security projection refresh events
- billing or statement completion events from workers

Consumers must be:

- idempotent (use MongoDB upserts with upstream event IDs)
- tenant-aware
- replay-safe

Recommended implementation:

- Gin server for synchronous APIs
- separate Go worker binary for Kafka consumers and background jobs

Do not run heavy Kafka consumption inside the same process as latency-sensitive HTTP traffic unless throughput is trivially small.

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

Audit events are stored in a MongoDB `audit_events` collection and must be:

- append-only (no updates or deletes)
- indexed by: tenant_id, actor_id, resource_type, resource_id, created_at
- queryable by tenant, actor, resource, workflow, and date range

```go
type AuditEvent struct {
    ID            string    `bson:"_id"` // UUID
    TenantID      string    `bson:"tenant_id"`
    ActorID       string    `bson:"actor_id"`
    ActorType     string    `bson:"actor_type"`
    Action        string    `bson:"action"`
    ResourceType  string    `bson:"resource_type"`
    ResourceID    string    `bson:"resource_id"`
    WorkflowID    string    `bson:"workflow_id,omitempty"`
    CorrelationID string    `bson:"correlation_id,omitempty"`
    Metadata      bson.M    `bson:"metadata,omitempty"`
    CreatedAt     time.Time `bson:"created_at"`
}
```

## 13. Error Model

Use a stable machine-readable error envelope:

```go
type ErrorResponse struct {
    Code      string      `json:"code"`
    Message   string      `json:"message"`
    Details   interface{} `json:"details,omitempty"`
    RequestID string      `json:"request_id,omitempty"`
}
```

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

5. Replace "single database owns all data" with platform-owned orchestration over external services, using MongoDB for operational truth and synchronized projections.

6. Keep AI behind controlled platform APIs instead of making it part of the operational write path.

7. Use Go + Gin + MongoDB as the implementation stack for type safety, performance, and operational simplicity.
