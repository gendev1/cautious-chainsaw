# Epic 17: Platform Reliability and Observability

## Goal

Make the system operable under failure, lag, retries, and upstream instability. Establish consistent observability primitives across all platform services so that every request, workflow step, Kafka message, and external dependency call can be traced, measured, and alerted on. Provide operational recovery tooling so that stuck workflows, failed messages, and projection staleness are visible and actionable without ad-hoc database queries.

## Context

The platform depends on multiple external services (security master, OMS/EMS, money movement rails) with mixed consistency models. User-facing actions often receive immediate submission feedback while final truth arrives later through Kafka. Projection lag, stale reads, and upstream degradation must be visible to operations teams in real time. Workflow failures need durable repair paths, not only logs.

Every cross-service interaction must carry the five required identifiers:

- **request ID** -- unique per inbound HTTP request or Kafka message processing cycle
- **correlation ID** -- ties together a chain of related requests and messages across service boundaries
- **workflow ID** -- ties all activity to a specific long-running workflow instance (onboarding case, transfer, billing run, etc.)
- **tenant ID** -- scopes all telemetry to the originating RIA firm
- **actor ID** -- identifies the user or service principal that initiated the action

## Dependencies

- **Epic 1: Tenant, Identity, and Access Control** -- tenant ID and actor ID must be available in the auth context
- **Epic 3: Workflow and Case Management** -- workflow IDs and state definitions must exist before workflow observability can be built
- **Epic 4: External Service Integration Framework** -- adapter patterns, Kafka consumer framework, and circuit breaker primitives must be in place

This epic is cross-cutting and should start early. Issues 1-3 (tracing, structured logging, request/correlation ID middleware) should be implemented alongside Epic 1. Remaining issues layer on as their dependent epics deliver.

---

## Issue 17-1: Distributed Tracing Setup

### Title

Implement OpenTelemetry distributed tracing with propagation across HTTP, Kafka, and sidecar calls

### Description

Set up OpenTelemetry SDK for Node.js/TypeScript across all platform services. Configure automatic instrumentation for Hono HTTP handlers, Kafka producers/consumers, outbound HTTP/gRPC calls to external services (security master, OMS, transfer rails), and calls to the AI sidecar. Ensure trace context (W3C Trace Context headers) propagates correctly across all transport boundaries so that a single advisor action can be followed from API request through workflow execution, Kafka message processing, and external service calls.

### Scope

- Install and configure `@opentelemetry/sdk-node`, `@opentelemetry/sdk-trace-node`, and relevant auto-instrumentation packages
- Create a shared tracing initialization module used by all service entry points (api-core, workflow-engine, integration-workers, reporting-jobs)
- Instrument Hono middleware to extract incoming trace context and create root spans for new requests
- Instrument Kafka producer to inject trace context into message headers
- Instrument Kafka consumer to extract trace context from message headers and create child spans
- Instrument outbound HTTP clients (security master, OMS, transfer rails, AI sidecar) to propagate trace context
- Add custom span attributes for tenant ID, actor ID, workflow ID, and correlation ID on every span
- Configure trace exporter (OTLP to a collector endpoint; specific backend is a deployment decision)
- Ensure sampling strategy is configurable per environment (100% in dev/staging, head-based sampling in production)

### Acceptance Criteria

- A single user request that triggers a workflow step, produces a Kafka message, and calls an external service generates a connected trace visible in the tracing backend
- Every span includes `tenant.id`, `actor.id`, `correlation.id`, and `workflow.id` (when applicable) as span attributes
- Kafka-produced messages carry W3C traceparent headers; consumers continue the trace
- Outbound calls to external services include traceparent headers
- Trace sampling rate is configurable via environment variable without code changes
- No tracing code leaks into business logic; all instrumentation is in middleware or shared infrastructure modules

### Dependencies

- Epic 1 (tenant and actor context available in request)
- Epic 4 (Kafka producer/consumer framework, outbound HTTP client abstractions)

---

## Issue 17-2: Structured Logging Standard

### Title

Establish JSON structured logging standard with required identifiers on every log line

### Description

Define and implement a platform-wide structured logging standard. Every log line emitted by any service must be a JSON object containing the five required identifiers (request ID, correlation ID, workflow ID, tenant ID, actor ID) when they are available in the current execution context. Standardize log levels, field names, and sensitive data handling rules. Replace any existing console.log usage with the structured logger.

### Scope

- Select and configure a structured logging library (e.g., pino) with JSON output format
- Define the canonical log schema:
  - `timestamp` (ISO 8601)
  - `level` (trace, debug, info, warn, error, fatal)
  - `message` (human-readable string)
  - `requestId`
  - `correlationId`
  - `workflowId` (when in workflow context)
  - `tenantId`
  - `actorId`
  - `service` (service name)
  - `module` (domain module name)
  - `traceId` (OpenTelemetry trace ID)
  - `spanId` (OpenTelemetry span ID)
  - `error` (serialized error object when applicable)
  - `duration` (milliseconds, for timed operations)
- Create a logger factory that accepts context and returns a child logger with context fields pre-bound
- Integrate the logger with Hono request context so that any code executing within a request handler has access to a context-aware logger
- Integrate the logger with Kafka consumer context
- Integrate the logger with workflow execution context
- Define redaction rules for sensitive fields (SSN, account numbers, tokens) -- these must never appear in logs
- Create a lint rule or code review checklist item that flags raw console.log usage

### Acceptance Criteria

- Every log line is valid JSON and includes all available context identifiers
- Logs within an HTTP request context include requestId, correlationId, tenantId, and actorId without the developer manually passing them
- Logs within a Kafka consumer context include the message's correlationId and tenantId
- Logs within a workflow step include workflowId
- Sensitive fields defined in the redaction list are replaced with `[REDACTED]` in all log output
- No raw console.log calls remain in the codebase
- Log level is configurable per environment via environment variable

### Dependencies

- Issue 17-3 (request ID and correlation ID middleware must generate the IDs that the logger consumes)

---

## Issue 17-3: Request ID and Correlation ID Middleware

### Title

Implement middleware to generate and propagate request ID and correlation ID on every request and message

### Description

Create Hono middleware that generates a unique request ID for every inbound HTTP request and either extracts an existing correlation ID from the incoming headers or generates a new one if absent. Ensure both IDs are stored in the request context and included in all outbound calls (HTTP responses, Kafka messages, external service requests). Create an equivalent context-propagation mechanism for Kafka consumers.

### Scope

- Hono middleware that:
  - Generates a UUID v4 (or ULID) request ID for each inbound request
  - Reads `X-Correlation-ID` from incoming headers; generates a new one if missing
  - Reads `X-Workflow-ID` from incoming headers if present
  - Stores request ID, correlation ID, and workflow ID in the Hono request context
  - Sets `X-Request-ID` and `X-Correlation-ID` response headers on every response
- Kafka consumer wrapper that:
  - Extracts correlation ID, tenant ID, actor ID, and workflow ID from Kafka message headers
  - Generates a new request ID for each message processing cycle
  - Makes all IDs available through a context object passed to the consumer handler
- Outbound HTTP client interceptor that:
  - Injects request ID, correlation ID, tenant ID, actor ID, and workflow ID into outbound request headers
- Kafka producer wrapper that:
  - Injects correlation ID, tenant ID, actor ID, and workflow ID into message headers
- AsyncLocalStorage-based context propagation so that IDs are available anywhere in the call stack without explicit parameter threading

### Acceptance Criteria

- Every HTTP response includes `X-Request-ID` and `X-Correlation-ID` headers
- If a client sends `X-Correlation-ID`, the same value propagates through all downstream calls and appears in the response
- If no `X-Correlation-ID` is sent, a new one is generated and propagated
- A Kafka message produced during an HTTP request carries the originating correlation ID, tenant ID, and actor ID in its headers
- A Kafka consumer processing that message can access the correlation ID, tenant ID, and actor ID from context
- Outbound HTTP calls to external services include all five identifiers in headers
- Context is available via AsyncLocalStorage without requiring explicit parameter passing through the business logic layer

### Dependencies

- Epic 1 (tenant ID and actor ID extracted from auth middleware)

---

## Issue 17-4: API Metrics Collection

### Title

Instrument API endpoints with latency, error rate, and request count metrics per endpoint and per tenant

### Description

Implement Prometheus-compatible metrics collection for all Hono API endpoints. Capture request count, latency distribution (histogram with percentile support), and error rates. All metrics must be labeled with endpoint, HTTP method, response status code, and tenant ID. These metrics form the foundation for SLO tracking and alerting.

### Scope

- Create Hono middleware that records for every request:
  - `http_request_duration_seconds` histogram (labels: method, route, status_code, tenant_id)
  - `http_requests_total` counter (labels: method, route, status_code, tenant_id)
  - `http_request_errors_total` counter (labels: method, route, error_type, tenant_id) for 4xx and 5xx responses
- Expose a `/metrics` endpoint in Prometheus exposition format
- Configure histogram buckets appropriate for API latency (e.g., 5ms, 10ms, 25ms, 50ms, 100ms, 250ms, 500ms, 1s, 2.5s, 5s, 10s)
- Add request body size and response body size histograms
- Ensure high-cardinality labels are controlled -- use route patterns (e.g., `/api/v1/accounts/:id`) not actual paths
- Add metrics for in-flight request count (`http_requests_in_flight` gauge, labels: method, route)

### Acceptance Criteria

- Every API request increments the request counter and records a latency observation
- Metrics are labeled with tenant ID so per-tenant latency and error rates can be queried
- The `/metrics` endpoint returns valid Prometheus exposition format
- p50, p90, p95, and p99 latency can be computed from the histogram data
- Error rate per endpoint and per tenant can be computed from the counters
- Route labels use parameterized patterns, not raw URLs (no unbounded cardinality)
- Metrics middleware adds less than 1ms overhead to request processing

### Dependencies

- Issue 17-3 (tenant ID must be available in request context)

---

## Issue 17-5: Workflow Observability

### Title

Instrument workflow execution with success, failure, stuck, and time-in-state metrics and queryable status views

### Description

Add observability into the workflow engine so that operations teams can see how many workflows of each type are active, completed, failed, or stuck, and how long workflows spend in each state. This covers onboarding cases, transfer cases, billing runs, and any other workflow type managed by the case management engine.

### Scope

- Emit metrics on workflow state transitions:
  - `workflow_transitions_total` counter (labels: workflow_type, from_state, to_state, tenant_id)
  - `workflow_active_count` gauge (labels: workflow_type, current_state, tenant_id)
  - `workflow_completed_total` counter (labels: workflow_type, outcome [success, failure, cancelled], tenant_id)
  - `workflow_stuck_count` gauge (labels: workflow_type, current_state, tenant_id) -- workflows that have not transitioned within their expected SLA
  - `workflow_state_duration_seconds` histogram (labels: workflow_type, state, tenant_id)
- Create a periodic job (configurable interval, default 60 seconds) that scans workflow state and updates stuck counts based on configurable time-in-state thresholds per workflow type and state
- Log structured events on every workflow state transition with workflow ID, type, from/to state, duration in previous state, tenant ID, and actor ID
- Provide a query endpoint for operations: list workflows by type, state, tenant, and stuck status with pagination

### Acceptance Criteria

- Every workflow state transition emits a metric increment and a structured log entry
- A workflow that has been in a state longer than its configured SLA threshold is counted as stuck
- Time-in-state histograms allow computing p50, p90, p99 durations for each workflow state
- Operations can query active, stuck, and recently failed workflows via the API
- Metrics are labeled by workflow type and tenant, enabling per-tenant operational views
- Stuck detection thresholds are configurable per workflow type and state without code changes

### Dependencies

- Epic 3 (workflow and case management engine must exist)
- Issue 17-2 (structured logging)
- Issue 17-4 (metrics infrastructure)

---

## Issue 17-6: Kafka Consumer Lag Monitoring

### Title

Implement consumer lag monitoring per topic and partition with alerting on growing lag

### Description

Monitor Kafka consumer lag for all platform consumer groups. Consumer lag is the difference between the latest offset on a partition and the consumer group's committed offset. Growing lag indicates that consumers are falling behind, which directly impacts projection freshness and event processing timeliness. This is critical because the platform relies on Kafka for OMS state updates, transfer lifecycle events, billing events, and other projections.

### Scope

- Expose consumer lag metrics:
  - `kafka_consumer_lag` gauge (labels: consumer_group, topic, partition)
  - `kafka_consumer_lag_total` gauge (labels: consumer_group, topic) -- sum across partitions
  - `kafka_consumer_last_poll_timestamp` gauge (labels: consumer_group, topic) -- detect stalled consumers
  - `kafka_consumer_messages_processed_total` counter (labels: consumer_group, topic, status [success, error, dead_lettered])
  - `kafka_consumer_processing_duration_seconds` histogram (labels: consumer_group, topic)
- Implement lag collection either via the Kafka admin client API (querying offsets periodically) or by instrumenting the consumer framework to report lag on each poll
- Detect stalled consumers: if last poll timestamp exceeds a threshold (configurable, default 5 minutes), flag the consumer as stalled
- Log a warning when lag on any consumer group exceeds a configurable threshold
- Emit a structured log entry for every dead-lettered message with topic, partition, offset, error reason, and all context identifiers

### Acceptance Criteria

- Consumer lag per topic and partition is available as a Prometheus metric
- A consumer group that stops polling is detectable via the last-poll-timestamp metric
- Dead-lettered messages are logged with full context (correlation ID, tenant ID, error details)
- Consumer message processing latency is captured as a histogram
- Lag thresholds and stall detection intervals are configurable without code changes

### Dependencies

- Epic 4 (Kafka consumer framework and dead-letter handling)
- Issue 17-4 (metrics infrastructure)

---

## Issue 17-7: External Dependency Health Monitoring

### Title

Monitor latency, error rates, and circuit breaker state for all external service dependencies

### Description

The platform makes synchronous calls to security master, OMS/EMS, and transfer/money movement services, as well as the AI sidecar. Each external dependency must be instrumented to track call latency, error rates, and circuit breaker state. This enables operations to detect upstream degradation before it cascades into workflow failures.

### Scope

- Instrument all outbound HTTP/gRPC client adapters with metrics:
  - `external_dependency_request_duration_seconds` histogram (labels: dependency, operation, status_code)
  - `external_dependency_requests_total` counter (labels: dependency, operation, status_code)
  - `external_dependency_errors_total` counter (labels: dependency, operation, error_type)
  - `external_dependency_circuit_breaker_state` gauge (labels: dependency) -- 0=closed, 1=half-open, 2=open
  - `external_dependency_circuit_breaker_trips_total` counter (labels: dependency)
- Log circuit breaker state transitions (closed->open, open->half-open, half-open->closed) as structured warning/info events
- Track per-dependency availability as a rolling window metric (requests succeeded / total requests over the last N minutes)
- Ensure all outbound calls include timeouts (configurable per dependency) and the five required identifiers in headers
- Named dependencies to instrument from day one:
  - Security master (lookups, projections)
  - OMS (order submission, cancellation, status queries)
  - Transfer/money movement rails (initiation, status queries)
  - AI sidecar (all calls)

### Acceptance Criteria

- Every outbound call to an external dependency records latency and status in metrics
- Circuit breaker state is visible as a metric and logged on every transition
- Per-dependency error rate and latency percentiles can be queried from the metrics backend
- All outbound calls enforce configurable timeouts
- All outbound calls propagate request ID, correlation ID, tenant ID, actor ID, and workflow ID

### Dependencies

- Epic 4 (external service adapter framework, circuit breaker implementation)
- Issue 17-3 (context propagation for outbound calls)
- Issue 17-4 (metrics infrastructure)

---

## Issue 17-8: Operational Recovery Tooling

### Title

Build tooling to retry stuck workflows, replay failed Kafka messages, and manually resolve operational breaks

### Description

Provide operations teams with safe, audited tooling to recover from common failure scenarios: workflows stuck in intermediate states, Kafka messages that failed processing and landed in dead-letter topics, and reconciliation breaks between platform state and external system state. These tools must enforce authorization (operations or admin role), log all recovery actions to the audit trail, and be idempotent.

### Scope

- **Stuck workflow retry**
  - API endpoint to retry a stuck workflow from its current state
  - Validate that the workflow is genuinely stuck (past its SLA threshold)
  - Re-execute the current step with idempotency protections
  - Log the retry action with actor ID, workflow ID, reason, and outcome
  - Support bulk retry by workflow type and state (with confirmation safeguards)

- **Dead-letter message replay**
  - API endpoint to list messages in dead-letter topics with filtering by topic, time range, and error type
  - API endpoint to replay a specific dead-letter message back to its original topic
  - API endpoint to bulk replay messages matching filter criteria
  - Log every replay action with actor ID, message metadata, and outcome
  - Track replay success/failure metrics

- **Manual break resolution**
  - API endpoint to list reconciliation breaks (platform state vs external system state discrepancies)
  - API endpoint to mark a break as investigated with notes
  - API endpoint to force-resolve a break by updating platform state to match external truth (with mandatory reason and audit entry)
  - All resolution actions require operations or admin role

- **Safeguards**
  - All recovery endpoints require explicit confirmation (not just a single POST)
  - All actions are idempotent
  - Rate limiting on bulk operations
  - Full audit trail for every recovery action

### Acceptance Criteria

- Operations can retry a stuck workflow via API and the retry is logged to the audit trail
- Operations can list, inspect, and replay dead-letter messages via API
- Bulk operations require confirmation and are rate-limited
- Every recovery action records actor ID, timestamp, action type, target resource, reason, and outcome in the audit log
- Recovery endpoints are restricted to operations and admin roles
- Replayed Kafka messages are processed with the same idempotency guarantees as original messages

### Dependencies

- Epic 3 (workflow engine with state management)
- Epic 4 (Kafka dead-letter topic infrastructure)
- Epic 16 (audit event store for recording recovery actions)
- Issue 17-5 (stuck workflow detection)

---

## Issue 17-9: Health and Readiness Endpoints

### Title

Implement liveness, readiness, and dependency health check endpoints for all services

### Description

Each platform service must expose health check endpoints that container orchestrators and load balancers can use to determine whether the service is alive, ready to accept traffic, and whether its critical dependencies are reachable. These endpoints enable zero-downtime deployments, automatic restart of unhealthy instances, and traffic draining during dependency outages.

### Scope

- **Liveness endpoint** (`GET /health/live`)
  - Returns 200 if the process is running and not deadlocked
  - Performs no external dependency checks
  - Must respond within 100ms

- **Readiness endpoint** (`GET /health/ready`)
  - Returns 200 only if the service can serve requests
  - Checks critical dependencies:
    - Postgres connection pool has available connections
    - Redis is reachable
    - Kafka producer is connected (for services that produce)
    - Kafka consumer is polling (for services that consume)
  - Returns 503 with a JSON body listing which dependencies are unhealthy
  - Must respond within 2 seconds

- **Detailed dependency health endpoint** (`GET /health/dependencies`) -- restricted to internal/operations callers
  - Returns status, latency, and last-check timestamp for each dependency:
    - Postgres
    - Redis
    - Kafka broker connectivity
    - Security master
    - OMS
    - Transfer rails
    - AI sidecar
  - Includes circuit breaker state for each external dependency

- Health check results should be cached briefly (e.g., 5 seconds) to avoid overwhelming dependencies with health check traffic

### Acceptance Criteria

- `/health/live` returns 200 when the process is running, with less than 100ms latency
- `/health/ready` returns 503 with a specific failure reason when any critical dependency is unreachable
- `/health/dependencies` returns per-dependency status including circuit breaker state
- Health endpoints do not require authentication (liveness/readiness) or require only internal auth (dependencies)
- Health check results are cached for a configurable duration to prevent dependency load
- Each service (api-core, workflow-engine, integration-workers, reporting-jobs) exposes all three endpoints

### Dependencies

- Issue 17-7 (external dependency health monitoring provides the health state consumed by these endpoints)

---

## Issue 17-10: Alerting Rules

### Title

Define and implement alerting rules for error rates, stuck workflows, Kafka lag, and external dependency degradation

### Description

Define the alerting rule set that turns raw metrics into actionable notifications for the operations team. Alerts should be tiered (warning vs critical), minimize false positives through appropriate thresholds and evaluation windows, and route to the correct on-call channels. All thresholds must be configurable.

### Scope

- **API error rate alerts**
  - Warning: 5xx error rate exceeds 1% over 5 minutes for any endpoint
  - Critical: 5xx error rate exceeds 5% over 5 minutes for any endpoint
  - Warning: p99 latency exceeds 2s for any endpoint over 5 minutes
  - Critical: p99 latency exceeds 5s for any endpoint over 5 minutes

- **Workflow stuck alerts**
  - Warning: any workflow in a state longer than 1x its SLA threshold
  - Critical: any workflow in a state longer than 2x its SLA threshold
  - Critical: more than N stuck workflows of the same type (configurable per type)

- **Kafka consumer lag alerts**
  - Warning: consumer lag exceeds N messages for more than 5 minutes (threshold configurable per topic)
  - Critical: consumer lag is growing continuously over 15 minutes
  - Critical: consumer has not polled in more than 5 minutes (stalled consumer)

- **External dependency degradation alerts**
  - Warning: error rate for any dependency exceeds 5% over 5 minutes
  - Critical: circuit breaker opens for any dependency
  - Warning: dependency p99 latency exceeds 2x its normal baseline over 5 minutes

- **Projection staleness alerts**
  - Warning: any projection is stale beyond its configured freshness SLA
  - Critical: any projection is stale beyond 2x its configured freshness SLA

- **Infrastructure alerts**
  - Critical: any service readiness endpoint returns 503 for more than 2 minutes
  - Warning: Postgres connection pool utilization exceeds 80%
  - Warning: Redis memory utilization exceeds 80%

- Alert definitions should be expressed as configuration (e.g., Prometheus alerting rules YAML or equivalent) so they can be version-controlled and reviewed

### Acceptance Criteria

- All alert rules are defined in version-controlled configuration files
- Each alert has a severity level (warning or critical), evaluation window, and threshold
- All thresholds are configurable without code changes
- Alert rules cover: API errors, API latency, stuck workflows, Kafka lag, stalled consumers, external dependency errors, circuit breaker trips, projection staleness, and infrastructure health
- Alert definitions include runbook links or descriptions for each alert
- Alert routing distinguishes between warning (Slack/email) and critical (pager) severity

### Dependencies

- Issue 17-4 (API metrics)
- Issue 17-5 (workflow metrics)
- Issue 17-6 (Kafka lag metrics)
- Issue 17-7 (external dependency metrics)
- Issue 17-12 (projection staleness metrics)

---

## Issue 17-11: Dashboard Definitions

### Title

Define operational dashboards for exception queue, transfer pipeline, order pipeline, and billing pipeline

### Description

Create dashboard definitions (Grafana or equivalent) that give operations teams real-time visibility into the health and throughput of the platform's critical pipelines. Each dashboard should combine metrics, workflow state counts, and error indicators into a single view that supports both routine monitoring and incident investigation.

### Scope

- **Exception Queue Dashboard**
  - Active exception count by type (onboarding, transfer, billing, reconciliation)
  - Exception age distribution (how long items have been in exception state)
  - Exception resolution rate (resolved per hour/day)
  - Exceptions by tenant (top tenants with most exceptions)
  - Recent exception events feed

- **Transfer Pipeline Dashboard**
  - Transfers in each lifecycle state (initiated, submitted, in_transit, completed, failed, reversed)
  - Transfer volume by type (ACH, ACAT, wire, journal) over time
  - Transfer failure rate by type and rail
  - Mean time from initiation to completion by type
  - Stuck transfers (past SLA threshold)
  - Transfer-related dead-letter message count

- **Order Pipeline Dashboard**
  - Orders in each lifecycle state (created, validated, routed, partially_filled, filled, cancelled, rejected, settled)
  - Order submission rate and fill rate over time
  - Rejection rate by reason
  - Mean time from submission to fill
  - OMS dependency health (latency, error rate, circuit breaker state)
  - Order-related dead-letter message count

- **Billing Pipeline Dashboard**
  - Billing runs in each state (scheduled, calculated, pending_review, approved, posted, collected, failed, reversed)
  - Billing run volume and completion rate over time
  - Fee calculation exceptions count
  - Mean time from schedule to posting
  - Billing-related dead-letter message count

- All dashboards should support tenant filtering and time range selection
- Dashboard definitions should be version-controlled as JSON/YAML

### Acceptance Criteria

- Four dashboards are defined and version-controlled as exportable configuration files
- Each dashboard provides at-a-glance health status plus drill-down capability
- All dashboards support filtering by tenant ID and time range
- Exception queue dashboard shows counts, age distribution, and resolution rates
- Pipeline dashboards show items in each lifecycle state with throughput and error rates
- Dashboards reference the metrics defined in Issues 17-4, 17-5, 17-6, and 17-7

### Dependencies

- Issue 17-4 (API metrics)
- Issue 17-5 (workflow observability metrics)
- Issue 17-6 (Kafka consumer lag metrics)
- Issue 17-7 (external dependency metrics)

---

## Issue 17-12: Projection Staleness Monitoring

### Title

Detect and surface when local projections of OMS, transfer, and other external state are lagging

### Description

The platform maintains local read projections of external system state (OMS order/fill status, transfer rail status, security master data, cash positions). These projections are updated via Kafka event ingestion. If the event stream falls behind or a consumer fails, projections become stale and the platform may show outdated information to advisors or make decisions on stale data. This issue adds monitoring that detects projection staleness and makes it visible to operations teams and, where appropriate, to the product UI.

### Scope

- Define a `projection_registry` that tracks each projection with:
  - Projection name (e.g., `oms_order_status`, `transfer_status`, `security_master`, `cash_positions`)
  - Source topic or feed
  - Consumer group
  - Last successfully processed event timestamp
  - Expected maximum staleness (freshness SLA, configurable per projection)

- Emit metrics:
  - `projection_staleness_seconds` gauge (labels: projection_name) -- seconds since last successfully processed event
  - `projection_freshness_sla_breached` gauge (labels: projection_name) -- 1 if stale beyond SLA, 0 otherwise
  - `projection_last_event_timestamp` gauge (labels: projection_name) -- epoch timestamp of last processed event

- Update the projection registry on every successfully processed event in each consumer

- Create a periodic staleness checker (configurable interval, default 30 seconds) that:
  - Reads the projection registry
  - Compares last-event timestamps against freshness SLAs
  - Updates staleness metrics
  - Logs a warning when any projection breaches its SLA

- Expose a `GET /api/v1/projections/status` endpoint that returns the freshness status of all projections (for operations use and potentially for UI staleness indicators)

- Add an `as_of` or `last_updated` timestamp to API responses that return projected data, so that consumers (UI, AI sidecar) can assess freshness

### Acceptance Criteria

- Every projection has a registered freshness SLA
- Staleness is computed and emitted as a metric every 30 seconds (configurable)
- A projection whose last event is older than its freshness SLA is flagged in metrics and logs
- The projections status endpoint returns the current freshness of each projection
- API responses for projected data include an `as_of` timestamp
- Staleness thresholds are configurable per projection without code changes
- Adding a new projection requires only a registry entry and consumer instrumentation, not changes to the staleness checker

### Dependencies

- Epic 4 (Kafka consumer framework and projection sync infrastructure)
- Issue 17-6 (consumer lag monitoring provides complementary data)

---

## Implementation Notes

### Sequencing

The recommended implementation order within this epic:

1. **Issue 17-3** (Request ID / Correlation ID middleware) -- foundational; everything else depends on context propagation
2. **Issue 17-2** (Structured logging) -- depends on 17-3 for context IDs
3. **Issue 17-1** (Distributed tracing) -- depends on 17-3 for context propagation; can parallel with 17-2
4. **Issue 17-4** (API metrics) -- can start as soon as 17-3 is in place
5. **Issue 17-9** (Health endpoints) -- independent of metrics; can start early
6. **Issue 17-7** (External dependency health) -- depends on 17-4 for metrics patterns
7. **Issue 17-6** (Kafka lag monitoring) -- depends on 17-4
8. **Issue 17-5** (Workflow observability) -- depends on Epic 3, 17-2, 17-4
9. **Issue 17-12** (Projection staleness) -- depends on Epic 4, 17-6
10. **Issue 17-10** (Alerting rules) -- depends on all metric-producing issues
11. **Issue 17-11** (Dashboard definitions) -- depends on all metric-producing issues
12. **Issue 17-8** (Operational recovery tooling) -- depends on 17-5, Epic 3, Epic 4, Epic 16

### Technology Choices

- **Tracing**: OpenTelemetry SDK for Node.js with OTLP exporter
- **Metrics**: Prometheus client for Node.js (`prom-client`) with OTLP export as an alternative
- **Logging**: pino with JSON serialization
- **Dashboards**: Grafana (dashboard JSON definitions version-controlled)
- **Alerting**: Prometheus Alertmanager rules (YAML definitions version-controlled)
- **Context propagation**: Node.js AsyncLocalStorage for in-process propagation; W3C Trace Context headers for cross-process propagation
