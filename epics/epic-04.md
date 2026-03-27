# Epic 4: External Service Integration Framework

## Goal

Standardize how the platform communicates with security master, OMS/EMS, money movement rails, and other external microservices. Establish typed client infrastructure, Kafka-based event publishing and consumption, projection synchronization, resilience patterns, and cross-service observability so that all downstream epics (trading, transfers, billing) inherit a consistent and operationally safe integration surface.

## Dependencies

- Epic 1: Tenant, Identity, and Access Control (tenant ID propagation, service-to-service auth)
- Epic 3: Workflow and Case Management (workflow state machines that depend on external lifecycle events)

## Architectural Context

The platform is not the owner of security master, OMS/EMS, or money movement rail state. It acts as an orchestrating control plane that issues synchronous commands to those systems and ingests asynchronous lifecycle events back through Kafka. Every integration must carry tenant ID, actor ID, correlation ID, request ID, and idempotency keys where mutations occur. Local projections of external state are permitted but must be tagged with upstream source, upstream ID, last synced timestamp, and sync status.

---

## Issue 1: Outbound HTTP/gRPC Client Infrastructure

### Title

Build typed outbound HTTP and gRPC client foundation with timeouts, retries, and circuit breakers

### Description

Create a base client abstraction in `apps/api/src/external/` that all upstream service adapters extend. The base client must enforce consistent timeout configuration, automatic retry with backoff, circuit breaker state tracking, request/response logging, and propagation of tenant context and correlation headers. Both HTTP (via `fetch` or `undici`) and gRPC (via `@grpc/grpc-js` or equivalent) transports must be supported behind a unified interface so that adapter consumers do not depend on transport choice.

### Scope

- Abstract `ExternalClient` base class or factory with configurable timeout, retry, and circuit breaker options
- HTTP client wrapper with typed request/response generics, header injection, and response deserialization
- gRPC client wrapper with channel management, metadata injection, deadline propagation, and proto-based typing
- Automatic injection of `x-request-id`, `x-correlation-id`, `x-tenant-id`, and service auth credentials into every outbound call
- Configurable per-client timeout defaults (connect timeout, request timeout, idle timeout)
- Structured error normalization: map upstream HTTP status codes and gRPC status codes to a platform `UpstreamError` type
- Request/response logging at debug level with PII redaction hooks
- Integration with the dependency wiring composition root in `app.ts`

### Acceptance Criteria

- [ ] A typed HTTP client exists that accepts a base URL, default headers, timeout config, and retry policy
- [ ] A typed gRPC client exists that accepts a service definition, channel target, metadata defaults, and deadline config
- [ ] Both clients automatically inject tenant ID, correlation ID, request ID, and service credentials from the current request context
- [ ] Both clients normalize upstream errors into a consistent `UpstreamError` shape with status, code, message, and upstream response metadata
- [ ] Timeout, retry, and circuit breaker configuration is per-client and overridable per-request
- [ ] All outbound calls are logged with method, URL/service, duration, and status at structured debug level
- [ ] Unit tests verify timeout enforcement, header injection, and error normalization
- [ ] Clients are registered through the composition root, not imported as global singletons

### Dependencies

- Epic 1 (service-to-service auth credentials, tenant context middleware)

---

## Issue 2: Kafka Producer Setup

### Title

Implement tenant-aware Kafka producer with idempotency keys, correlation IDs, and schema-safe publishing

### Description

Set up a KafkaJS producer within the API server process for publishing platform-originated domain events to Kafka. The producer must enforce a standard event envelope that includes tenant ID, actor ID, correlation ID, request ID, idempotency key, event type, event version, and ISO-8601 timestamp. Publishing must be transactional where possible (Kafka idempotent producer) and must not block HTTP request latency beyond a reasonable threshold.

### Scope

- KafkaJS producer initialization with idempotent producer configuration (`enableIdempotence: true`, `maxInFlightRequests: 1` or 5 as appropriate)
- Standard `PlatformEvent<T>` envelope type:
  - `eventId` (UUID)
  - `eventType` (e.g., `transfer.submitted`)
  - `eventVersion` (e.g., `1`)
  - `tenantId`
  - `actorId`
  - `correlationId`
  - `requestId`
  - `idempotencyKey` (optional, for mutation events)
  - `timestamp` (ISO-8601)
  - `payload: T`
- `EventPublisher` service that accepts typed events, serializes with the envelope, and publishes to the correct topic
- Topic naming convention: `platform.<domain>.<event-class>` (e.g., `platform.transfers.lifecycle`)
- Partition key strategy: tenant ID or entity ID depending on ordering requirements
- Publish timeout and error handling: failed publishes must be logged and optionally buffered for retry
- Graceful shutdown: flush pending messages before process exit
- Zod schemas for each event envelope to validate outbound events in development and test

### Acceptance Criteria

- [ ] KafkaJS producer is initialized at startup with idempotent configuration and connected to the configured broker list
- [ ] `EventPublisher` service exists with a `publish<T>(topic, event: PlatformEvent<T>)` method
- [ ] Every published message includes the full envelope (eventId, eventType, eventVersion, tenantId, actorId, correlationId, requestId, timestamp)
- [ ] Partition key is set per topic configuration (tenant ID by default, overridable to entity ID)
- [ ] Publish failures are caught, logged with full context, and do not crash the API process
- [ ] Graceful shutdown flushes the producer before disconnecting
- [ ] Integration test confirms a message published to a test topic is retrievable by a test consumer with correct envelope fields
- [ ] Event envelope Zod schema exists and is enforced in tests

### Dependencies

- Epic 1 (tenant and actor context)
- Kafka cluster provisioned and accessible

---

## Issue 3: Kafka Consumer Framework

### Title

Build idempotent, tenant-aware, replay-safe Kafka consumer framework in a separate worker process

### Description

Create the consumer infrastructure in `apps/workers/src/consumers/` that runs as a separate Node.js process from the Hono API server. The framework must support registering topic handlers that are idempotent, tenant-aware, and safe to replay without duplicating side effects. Each consumer group must track processed event IDs (or offsets with deduplication) so that reprocessing after rebalance or restart does not corrupt platform state.

### Scope

- Separate `apps/workers/` entry point with its own KafkaJS consumer group configuration
- `ConsumerRegistry` that maps topics to typed handler functions
- Idempotency enforcement: before processing, check a `processed_events` table (or equivalent) keyed on `eventId`; skip if already processed; mark as processed after successful handling within the same database transaction as the side effect
- Tenant-aware context injection: extract `tenantId` from the event envelope and set it in the handler context so that all downstream repository calls are tenant-scoped
- Replay safety: handlers must be written to tolerate out-of-order and duplicate delivery
- Concurrency control: configurable `partitionsConsumedConcurrently` and `eachBatchAutoResolve` settings
- Heartbeat and session timeout configuration to avoid unnecessary rebalances during slow processing
- Structured logging per consumed message: topic, partition, offset, eventType, tenantId, correlationId, processing duration, outcome
- Graceful shutdown: commit offsets, stop fetching, and allow in-flight handlers to complete

### Acceptance Criteria

- [ ] Worker process starts independently from the API server and connects its own consumer group(s)
- [ ] `ConsumerRegistry` allows registering a handler for a topic with a typed event payload
- [ ] Each handler receives a typed context with tenantId, correlationId, actorId, and the deserialized payload
- [ ] Duplicate eventIds are detected and skipped without re-executing side effects
- [ ] processed_events deduplication record is written in the same transaction as the handler's database side effect
- [ ] Rebalance and restart do not cause duplicate processing of already-handled events
- [ ] Consumer logs every processed message with topic, partition, offset, eventType, tenantId, duration, and outcome
- [ ] Graceful shutdown completes in-flight handlers and commits final offsets
- [ ] Integration tests confirm idempotent skip behavior on duplicate eventId delivery

### Dependencies

- Issue 2 (event envelope contract)
- Epic 1 (tenant-scoped database access)

---

## Issue 4: Dead-Letter Queue Handling

### Title

Implement dead-letter queue routing for permanently failed consumer messages

### Description

When a Kafka consumer handler fails after exhausting its retry budget, the message must be routed to a dead-letter topic rather than blocking the partition or being silently dropped. The DLQ entry must preserve the original message, envelope metadata, failure reason, retry count, and a timestamp. An administrative API or CLI tool must allow operators to inspect, replay, or discard DLQ entries.

### Scope

- DLQ topic naming convention: `platform.<original-topic>.dlq`
- `DeadLetterProducer` that publishes failed messages to the DLQ topic with additional metadata headers:
  - `x-original-topic`
  - `x-original-partition`
  - `x-original-offset`
  - `x-failure-reason`
  - `x-retry-count`
  - `x-failed-at` (ISO-8601)
  - `x-correlation-id`
  - `x-tenant-id`
- Integration with the consumer framework: after max retries, hand off to `DeadLetterProducer` and commit the offset
- DLQ consumer or admin endpoint that lists DLQ entries filtered by tenant, topic, time range, and failure reason
- Replay mechanism: re-publish a DLQ entry back to the original topic for reprocessing
- Discard mechanism: mark a DLQ entry as acknowledged/discarded
- Alerting hook: emit a metric or log event when a message is routed to DLQ so monitoring can trigger alerts

### Acceptance Criteria

- [ ] Messages that fail after max retries are published to the corresponding `.dlq` topic with all required metadata headers
- [ ] The original message payload and envelope are preserved exactly in the DLQ entry
- [ ] Consumer does not block the partition after routing to DLQ; offset is committed
- [ ] An admin endpoint or CLI can list DLQ entries filtered by tenant, original topic, and time range
- [ ] An admin endpoint or CLI can replay a specific DLQ entry back to its original topic
- [ ] An admin endpoint or CLI can discard/acknowledge a DLQ entry
- [ ] A structured log event or metric is emitted on every DLQ routing for alerting integration
- [ ] Integration test confirms a poison message is routed to DLQ after retry exhaustion and can be replayed

### Dependencies

- Issue 3 (consumer framework)
- Issue 9 (retry policies)

---

## Issue 5: Security Master Client Adapter

### Title

Implement security master client adapter for synchronous lookups and local projection sync

### Description

Create a typed adapter in `apps/api/src/external/security-master/` that wraps the security master service for point lookups (by CUSIP, ISIN, ticker, or internal security ID) and supports a local projection table for hot read paths. The adapter uses the base HTTP/gRPC client from Issue 1 and follows the local projection sync pattern from Issue 8.

### Scope

- `SecurityMasterClient` extending the base external client with methods:
  - `getSecurityById(id: string): Promise<SecurityRecord>`
  - `searchSecurities(query: SecuritySearchQuery): Promise<SecurityRecord[]>`
  - `getSecurityByIdentifier(type: 'cusip' | 'isin' | 'ticker', value: string): Promise<SecurityRecord>`
- `SecurityRecord` type covering: internal ID, CUSIP, ISIN, ticker, name, asset class, sector, exchange, status, and upstream metadata
- Local projection table `security_projections` with columns: `id`, `upstream_source`, `upstream_id`, `data` (JSONB), `last_synced_at`, `sync_status`, `tenant_id` (if security data is tenant-partitioned) or global scope
- `SecurityProjectionRepository` for reading from and writing to the local projection
- Read-through cache pattern: check local projection first; if stale or missing, fetch from upstream and update projection
- Kafka consumer handler for security update events (if the security master publishes change events)
- Zod schema for `SecurityRecord` to validate upstream responses
- Redis cache layer for ultra-hot lookups (instrument validation during order ticket entry) with short TTL

### Acceptance Criteria

- [ ] `SecurityMasterClient` provides typed methods for point lookup and search against the upstream service
- [ ] Upstream responses are validated against the `SecurityRecord` Zod schema
- [ ] Local projection table exists with upstream_source, upstream_id, last_synced_at, and sync_status columns
- [ ] Read-through pattern falls back to upstream when local projection is missing or stale
- [ ] Redis cache is checked before the projection table for hot lookups, with a configurable TTL
- [ ] Consumer handler updates the local projection when security change events arrive on Kafka
- [ ] Client uses the base external client with timeout, retry, and circuit breaker configuration
- [ ] Integration tests verify lookup, cache miss fallback, and projection update from a Kafka event

### Dependencies

- Issue 1 (base client)
- Issue 3 (consumer framework, for event-driven projection updates)
- Issue 8 (local projection sync pattern)

---

## Issue 6: OMS/EMS Client Adapter

### Title

Implement OMS/EMS client adapter for order submission, cancellation, and execution event ingestion

### Description

Create a typed adapter in `apps/api/src/external/oms/` that handles synchronous order submission and cancellation against the OMS, plus Kafka consumer handlers for asynchronous order state transitions and execution fill events. The adapter bridges the platform's `OrderIntent` records with the upstream OMS's order lifecycle.

### Scope

- `OmsClient` extending the base external client with methods:
  - `submitOrder(request: OmsSubmitRequest): Promise<OmsSubmitResponse>` (returns upstream order ID and accepted status)
  - `cancelOrder(upstreamOrderId: string, reason: string): Promise<OmsCancelResponse>`
  - `getOrderStatus(upstreamOrderId: string): Promise<OmsOrderStatus>`
- `OmsSubmitRequest` type: instrument ID, side, quantity, order type, time-in-force, account ID, idempotency key, tenant ID, correlation ID
- `OmsSubmitResponse` type: upstream order ID, accepted/rejected status, rejection reason if applicable
- Kafka consumer handlers in the worker process:
  - `order.accepted` -- update `OrderProjection` with upstream ID and accepted timestamp
  - `order.rejected` -- update `OrderProjection` with rejection reason and terminal status
  - `execution.fill_received` -- create `ExecutionProjection` record with fill price, quantity, venue, and timestamp
  - `order.cancelled` -- update `OrderProjection` with cancellation status
- `OrderProjection` and `ExecutionProjection` tables following the local projection sync pattern (upstream_source, upstream_id, last_synced_at, sync_status)
- Idempotency: submission uses the platform `OrderIntent` ID as the idempotency key; consumer handlers check eventId deduplication
- Error mapping: upstream OMS rejection codes mapped to platform error codes (`UPSTREAM_REJECTED`, `UPSTREAM_SERVICE_UNAVAILABLE`)

### Acceptance Criteria

- [ ] `OmsClient` provides typed submission, cancellation, and status methods using the base external client
- [ ] Submission sends idempotency key, tenant ID, and correlation ID to the OMS
- [ ] Upstream rejection is mapped to a typed `UpstreamError` with the OMS rejection reason
- [ ] Kafka consumer handlers exist for order.accepted, order.rejected, execution.fill_received, and order.cancelled
- [ ] Each consumer handler updates the corresponding projection table idempotently
- [ ] `OrderProjection` and `ExecutionProjection` tables include upstream_source, upstream_id, last_synced_at, and sync_status
- [ ] Integration tests verify submission round-trip and projection update from simulated Kafka events
- [ ] Circuit breaker trips after repeated OMS failures and recovers after the configured window

### Dependencies

- Issue 1 (base client)
- Issue 3 (consumer framework)
- Issue 8 (local projection sync pattern)
- Issue 10 (circuit breaker)

---

## Issue 7: Money Movement / Transfer Rail Client Adapter

### Title

Implement money movement client adapter for transfer initiation and lifecycle event ingestion

### Description

Create a typed adapter in `apps/api/src/external/transfers/` that handles synchronous transfer initiation (ACH, ACAT, wire, journal) against the money movement service, plus Kafka consumer handlers for asynchronous transfer lifecycle events (verification, in-transit, completed, failed, reversed). The adapter bridges the platform's `TransferCase` records with the upstream rail service's lifecycle.

### Scope

- `TransferRailClient` extending the base external client with methods:
  - `initiateTransfer(request: TransferInitiateRequest): Promise<TransferInitiateResponse>`
  - `cancelTransfer(upstreamTransferId: string): Promise<TransferCancelResponse>`
  - `getTransferStatus(upstreamTransferId: string): Promise<TransferStatus>`
- `TransferInitiateRequest` type: transfer type (ACH, ACAT_FULL, ACAT_PARTIAL, WIRE, JOURNAL), source account, destination account, amount, currency, idempotency key, tenant ID, correlation ID, and type-specific metadata
- `TransferInitiateResponse` type: upstream transfer ID, accepted status, estimated completion date if available
- Kafka consumer handlers in the worker process:
  - `transfer.pending_verification` -- update transfer case status
  - `transfer.in_transit` -- update transfer case status with tracking metadata
  - `transfer.completed` -- update transfer case to terminal completed status
  - `transfer.failed` -- update transfer case with failure reason
  - `transfer.reversed` -- update transfer case with reversal metadata
  - `transfer.returned` -- handle return events (especially ACH returns)
- Transfer projection following local projection sync pattern
- Error mapping for rail-specific rejection codes (insufficient funds, invalid routing, compliance hold)
- Support for both synchronous initiation and webhook/polling fallback for rails that do not support Kafka

### Acceptance Criteria

- [ ] `TransferRailClient` provides typed initiation, cancellation, and status methods
- [ ] Initiation sends idempotency key, tenant ID, and correlation ID to the rail service
- [ ] All supported transfer types (ACH, ACAT, wire, journal) are handled with type-specific request validation
- [ ] Kafka consumer handlers exist for each transfer lifecycle event and update the transfer case idempotently
- [ ] Return and reversal events are handled as first-class transitions, not error conditions
- [ ] Transfer projection table includes upstream_source, upstream_id, last_synced_at, and sync_status
- [ ] Rail-specific error codes are mapped to platform error codes
- [ ] Integration tests verify initiation round-trip and lifecycle event processing from simulated Kafka events

### Dependencies

- Issue 1 (base client)
- Issue 3 (consumer framework)
- Issue 8 (local projection sync pattern)
- Epic 3 (transfer case workflow state machine)

---

## Issue 8: Local Projection Sync Framework

### Title

Build reusable local projection sync framework with upstream source tracking and staleness detection

### Description

Create a generic framework for maintaining local read-model projections of external authoritative data. Every projection record must track its upstream source, upstream ID, last synced timestamp, and sync status. The framework provides base repository methods, staleness detection, bulk sync job support, and a consistent schema pattern that all service-specific projections (security master, OMS, transfers) extend.

### Scope

- Base `projection` table schema pattern (to be applied per domain):
  - `id` (platform UUID)
  - `upstream_source` (string, e.g., `security-master`, `oms`, `transfer-rail`)
  - `upstream_id` (string, the external system's identifier)
  - `tenant_id` (UUID, nullable for global projections)
  - `data` (JSONB, the projection payload)
  - `last_synced_at` (timestamptz)
  - `sync_status` (enum: `synced`, `stale`, `error`, `pending`)
  - `sync_error` (text, nullable)
  - `created_at` (timestamptz)
  - `updated_at` (timestamptz)
  - Unique constraint on `(upstream_source, upstream_id)` or `(upstream_source, upstream_id, tenant_id)` as appropriate
- `ProjectionRepository<T>` generic base class with methods:
  - `upsertFromUpstream(upstreamSource, upstreamId, tenantId, data, syncedAt)`
  - `findByUpstreamId(upstreamSource, upstreamId)`
  - `findStale(upstreamSource, staleBefore: Date, limit: number)`
  - `markStale(upstreamSource, upstreamId)`
  - `markError(upstreamSource, upstreamId, error: string)`
- `ProjectionSyncJob` base class for scheduled bulk refresh:
  - Queries for stale or errored projections
  - Fetches fresh data from upstream via the corresponding client adapter
  - Updates the projection within a transaction
  - Logs sync outcomes with counts and durations
- Staleness threshold configuration per upstream source
- Projection freshness metadata exposed in API responses (e.g., `_meta.lastSyncedAt`, `_meta.syncStatus`) so consumers know the data age

### Acceptance Criteria

- [ ] Base projection table schema is documented and applied via migration for at least one domain (security projections)
- [ ] `ProjectionRepository<T>` provides generic upsert, find, staleness query, and error marking methods
- [ ] Upsert is idempotent: re-syncing with the same upstream ID and data does not create duplicate records
- [ ] `findStale` returns projections older than the configured staleness threshold
- [ ] `ProjectionSyncJob` base class can be extended per domain to refresh stale projections in batch
- [ ] Projection freshness metadata (lastSyncedAt, syncStatus) is available for API response enrichment
- [ ] Unit tests verify upsert idempotency, staleness detection, and error marking
- [ ] Migration creates the projection table with proper indexes on upstream_source, upstream_id, and sync_status

### Dependencies

- Epic 1 (tenant-scoped data access)
- Database migration infrastructure from platform chassis

---

## Issue 9: Retry Policies and Exponential Backoff

### Title

Implement configurable retry policies with exponential backoff, jitter, and budget limits

### Description

Create a reusable retry framework that is used by both the outbound HTTP/gRPC clients (Issue 1) and the Kafka consumer framework (Issue 3). The framework must support configurable max attempts, exponential backoff with jitter, per-error-type retry classification (transient vs permanent), and total retry budget (time-bounded or count-bounded). It must integrate cleanly with the circuit breaker from Issue 10.

### Scope

- `RetryPolicy` configuration type:
  - `maxAttempts` (number)
  - `baseDelayMs` (number)
  - `maxDelayMs` (number, cap for exponential growth)
  - `backoffMultiplier` (number, default 2)
  - `jitterMode` (`full` | `equal` | `none`)
  - `retryableErrors` (predicate function or error code allowlist)
  - `totalTimeoutMs` (optional, total time budget across all retries)
- `retry<T>(fn: () => Promise<T>, policy: RetryPolicy): Promise<T>` function
- Error classification: distinguish transient errors (network timeout, 502, 503, gRPC UNAVAILABLE) from permanent errors (400, 404, gRPC INVALID_ARGUMENT) to avoid retrying non-recoverable failures
- Retry attempt logging: log each retry with attempt number, delay, error summary, and correlation ID
- Integration points:
  - Base external client uses `RetryPolicy` for outbound calls
  - Kafka consumer handler wrapper uses `RetryPolicy` before routing to DLQ
- Pre-built policy presets: `defaultHttpRetry`, `defaultGrpcRetry`, `defaultConsumerRetry`, `aggressiveRetry`, `noRetry`

### Acceptance Criteria

- [ ] `RetryPolicy` type is defined with all configuration fields
- [ ] `retry()` function correctly retries transient errors up to `maxAttempts` with exponential backoff and jitter
- [ ] Permanent errors are not retried and propagate immediately
- [ ] Backoff delay is capped at `maxDelayMs` and respects `totalTimeoutMs` budget
- [ ] Each retry attempt is logged with attempt number, delay, error class, and correlation ID
- [ ] Pre-built policy presets exist for HTTP, gRPC, consumer, and no-retry scenarios
- [ ] Unit tests verify backoff timing, jitter distribution, max attempt enforcement, and permanent error passthrough
- [ ] The retry function integrates with the circuit breaker (does not retry when circuit is open)

### Dependencies

- None (foundational utility; consumed by Issues 1, 3, 4, and 10)

---

## Issue 10: Upstream Health Monitoring and Circuit Breakers

### Title

Implement circuit breaker pattern for upstream service dependencies with health state tracking

### Description

Create a circuit breaker implementation that wraps outbound calls to each upstream service. The circuit breaker tracks failure rates per upstream dependency and transitions between closed (healthy), open (tripped), and half-open (probing) states. When open, calls fail fast without reaching the upstream service. Health state must be observable via metrics and a health endpoint so that operators can see which dependencies are degraded.

### Scope

- `CircuitBreaker` class with configuration:
  - `failureThreshold` (number of failures before opening)
  - `failureWindowMs` (sliding window for failure counting)
  - `openDurationMs` (time to stay open before transitioning to half-open)
  - `halfOpenMaxAttempts` (number of probe requests in half-open state)
  - `successThreshold` (consecutive successes in half-open to close)
- State transitions: CLOSED -> OPEN (on threshold breach), OPEN -> HALF_OPEN (on timer expiry), HALF_OPEN -> CLOSED (on success threshold) or HALF_OPEN -> OPEN (on probe failure)
- `CircuitBreakerRegistry` that maintains one circuit breaker per named upstream dependency (e.g., `security-master`, `oms`, `transfer-rail`)
- Integration with the base external client: wrap each outbound call with the corresponding circuit breaker
- When circuit is open, immediately throw a typed `CircuitOpenError` with the upstream name and estimated recovery time
- Health endpoint contribution: expose circuit breaker states via `GET /api/health/dependencies` or equivalent
- Metrics emission: state transitions, failure counts, open duration, and fast-fail counts
- Optional Redis-backed state sharing for multi-instance deployments (so all API instances respect the same circuit state)

### Acceptance Criteria

- [ ] `CircuitBreaker` class implements closed, open, and half-open states with configurable thresholds
- [ ] Circuit opens after the configured failure threshold within the sliding window
- [ ] Open circuit fails fast with `CircuitOpenError` without making upstream calls
- [ ] Half-open state allows a limited number of probe requests and transitions to closed on success
- [ ] `CircuitBreakerRegistry` maintains per-dependency circuit breakers
- [ ] Base external client integrates with the circuit breaker for all outbound calls
- [ ] Health endpoint exposes current circuit breaker states for all registered upstream dependencies
- [ ] State transitions emit structured log events and metrics
- [ ] Unit tests verify state transitions, fast-fail behavior, and recovery after half-open success

### Dependencies

- Issue 1 (base external client integration)
- Issue 9 (retry integration -- retries should not fire when circuit is open)

---

## Issue 11: Event Contract Versioning

### Title

Establish event contract versioning strategy with schema registry and backward-compatible evolution rules

### Description

Define and enforce a versioning strategy for all Kafka event contracts so that producers and consumers can evolve independently without breaking each other. Every event must carry an `eventVersion` field. The platform must support reading older versions of events and must define rules for backward-compatible schema evolution.

### Scope

- Event version field is mandatory in the `PlatformEvent<T>` envelope (established in Issue 2)
- Versioning rules:
  - Adding optional fields is backward-compatible and does not require a version bump
  - Removing fields, renaming fields, or changing field types requires a new version
  - Consumers must handle all supported versions of an event type
- Per-event-type Zod schemas organized by version:
  - `events/schemas/transfer.submitted.v1.ts`
  - `events/schemas/order.accepted.v1.ts`
  - Pattern: `events/schemas/<domain>.<event>.v<N>.ts`
- `EventSchemaRegistry` that maps `(eventType, eventVersion)` to the corresponding Zod schema
- Consumer-side version dispatch: when a consumer receives an event, it looks up the schema by type and version, validates, and optionally transforms older versions to the latest internal representation
- Producer-side validation: `EventPublisher` validates the payload against the registered schema before publishing (in development/test; optional in production for performance)
- Documentation convention: each event schema file includes a changelog comment block describing changes from the previous version
- CI check: event schema files must not contain breaking changes to existing versions (enforced via snapshot tests or schema compatibility checks)

### Acceptance Criteria

- [ ] Every event type has a versioned Zod schema file following the naming convention
- [ ] `EventSchemaRegistry` resolves the correct schema for a given (eventType, eventVersion) pair
- [ ] Consumers validate incoming events against the registered schema and handle version dispatch
- [ ] Producers validate outbound events against the schema in development and test environments
- [ ] Adding an optional field to an existing version does not break existing consumers (verified by test)
- [ ] A breaking change to an event schema requires a new version number and a new schema file
- [ ] Schema files include changelog documentation
- [ ] Snapshot tests or compatibility checks exist to detect accidental breaking changes to published schemas

### Dependencies

- Issue 2 (event envelope and publisher)
- Issue 3 (consumer framework)

---

## Issue 12: Correlation ID and Request ID Propagation Across Services

### Title

Implement end-to-end correlation ID and request ID propagation across HTTP, gRPC, and Kafka boundaries

### Description

Ensure that every request entering the platform receives a unique request ID and is associated with a correlation ID that follows the entire operation across synchronous outbound calls, Kafka event publishing, and Kafka consumer processing. This enables distributed tracing and operational debugging across the full request lifecycle, including async branches.

### Scope

- Hono middleware that:
  - Generates a UUID `requestId` for each inbound HTTP request (or accepts `x-request-id` from the gateway)
  - Reads or generates a `correlationId` (from `x-correlation-id` header, or creates a new one for top-level requests)
  - Stores both in an AsyncLocalStorage context accessible throughout the request lifecycle
- `RequestContext` via Node.js `AsyncLocalStorage`:
  - `requestId`
  - `correlationId`
  - `tenantId`
  - `actorId`
  - `workflowId` (optional, set by workflow handlers)
- Outbound HTTP client: automatically reads from `RequestContext` and sets `x-request-id`, `x-correlation-id`, `x-tenant-id` headers on every outbound call
- Outbound gRPC client: automatically sets correlation and tenant metadata on every outbound call
- Kafka producer: automatically includes `correlationId`, `requestId`, `tenantId`, and `actorId` in the event envelope from the current `RequestContext`
- Kafka consumer: extracts `correlationId` from the consumed event envelope and establishes a new `RequestContext` with a new `requestId` (for the consumer's processing) but the original `correlationId` (for tracing continuity)
- HTTP response: include `x-request-id` and `x-correlation-id` in response headers for client-side debugging
- Structured logging: all log statements automatically include `requestId`, `correlationId`, `tenantId` from the current `RequestContext`

### Acceptance Criteria

- [ ] Every inbound HTTP request has a `requestId` and `correlationId` available via `AsyncLocalStorage`
- [ ] `x-request-id` and `x-correlation-id` are included in all HTTP responses
- [ ] Outbound HTTP calls carry `x-request-id`, `x-correlation-id`, and `x-tenant-id` headers automatically
- [ ] Outbound gRPC calls carry correlation and tenant metadata automatically
- [ ] Published Kafka events include the `correlationId` and `requestId` from the originating HTTP request
- [ ] Kafka consumers establish a `RequestContext` with the original `correlationId` and a new `requestId`
- [ ] All structured log entries include `requestId`, `correlationId`, and `tenantId` from the current context
- [ ] An end-to-end integration test traces a correlation ID from an HTTP request through a Kafka publish and back through a consumer handler
- [ ] `RequestContext` is accessible from services, repositories, and adapters without explicit parameter passing

### Dependencies

- Epic 1 (tenant and actor context middleware)
- Issue 1 (base external client)
- Issue 2 (Kafka producer)
- Issue 3 (Kafka consumer framework)

---

## Implementation Notes

### Suggested Issue Order

The recommended implementation sequence within this epic:

1. Issue 12 (Correlation/Request ID propagation) and Issue 9 (Retry policies) -- foundational utilities with no internal dependencies
2. Issue 10 (Circuit breakers) -- depends on Issue 9
3. Issue 1 (Outbound client infrastructure) -- depends on Issues 9, 10, 12
4. Issue 2 (Kafka producer) -- depends on Issue 12
5. Issue 11 (Event contract versioning) -- depends on Issue 2
6. Issue 3 (Kafka consumer framework) -- depends on Issues 2, 9, 12
7. Issue 4 (Dead-letter queue) -- depends on Issues 3, 9
8. Issue 8 (Local projection sync framework) -- depends on Issue 3
9. Issues 5, 6, 7 (Service-specific adapters) -- depend on Issues 1, 3, 8; can be parallelized

### Key Architectural Constraints

- Kafka consumers must run in a separate worker process (`apps/workers/`), not inside the Hono HTTP server process.
- All external clients must be registered through the composition root, not imported as global singletons.
- Local projections are never the source of truth -- they are cached views tagged with upstream provenance.
- Every cross-service call must carry tenant ID, correlation ID, request ID, and actor ID.
- The integration layer must not leak into Hono route handlers. Routes call services; services call adapters.

### Estimated Scope

This epic establishes the integration chassis. It does not implement full business logic for trading, transfers, or security data. Domain-specific workflow logic is covered in Epics 6, 7, and 9. The adapters built here provide the typed client surface and event ingestion plumbing that those epics consume.
