# Epic 18: AI Copilot and Document Intelligence

## Goal

Add advisor productivity and document intelligence features through a Python/FastAPI sidecar service without putting AI on the regulated write path. The sidecar is augmentation only: it reads platform data through narrow typed methods, calls LLM providers, and returns recommendations, drafts, classifications, and summaries. It never directly mutates regulated records. The platform must function fully without the sidecar; only assistive AI features degrade when it is unavailable.

## Architecture Context

```text
advisor request
    -> API server (Node.js / TypeScript / Hono)
        -> enriches request with tenant and actor context
        -> calls sidecar for assistive work
            -> sidecar reads platform APIs via platform_client
            -> sidecar may call LLM providers
        -> API server returns AI result to advisor

recommendation selected by advisor
    -> API server command path
        -> normal workflow, permissions, and external integrations
```

The sidecar is intentionally not on the direct write path to financial infrastructure. Any action it recommends must be executed through normal platform command endpoints after permissions, policy, and workflow checks.

## Dependencies

- Epic 8: Advisor Portal Experience (provides the advisor-facing surface that consumes AI outputs)
- Epic 13: Reporting, Statements, and Snapshots (provides frozen snapshot inputs for narrative generation)
- Epic 17: Platform Reliability and Observability (provides tracing, metrics, and health infrastructure)
- Epic 5: Document Vault and Records Management (provides document metadata and artifact storage)
- Epic 16: Audit, Compliance, and Support Tooling (provides audit event infrastructure for AI compliance logging)

## Design Constraints

1. AI is augmentation only, NOT on the regulated write path.
2. Sidecar returns recommendations and drafts, never directly mutates regulated records.
3. Tool invocation is allowed for reads only (get_household_summary, get_account_summary, etc.), NOT for mutations (submit order, initiate transfer).
4. Narrative generation uses frozen snapshot inputs, not mutable live data.
5. Platform must work without the sidecar (graceful degradation).
6. Sidecar reads via platform_client with narrow, versioned methods, not wide-open data APIs.
7. AI recommendations are returned as structured payloads (type, targetAccountId, reason) for the API server to convert into normal command flows.
8. All cached responses must include source, generated timestamp, and freshness/TTL metadata.
9. Client-visible generated content must prefer snapshot-based inputs and enforce review before publication where policy requires it.

---

## Issue 18.1: Sidecar Service Setup

**Title:** Set up Python/FastAPI sidecar service with config, health/ready endpoints, and graceful degradation

**Description:**
Bootstrap the AI sidecar as a standalone Python service using FastAPI. Establish the foundational project structure, configuration management, health and readiness endpoints, and the graceful degradation contract that ensures the platform operates normally when the sidecar is unavailable.

**Scope:**
- Initialize the sidecar project structure following the recommended layout:
  ```
  sidecar/
  ├── app/
  │   ├── main.py
  │   ├── config.py
  │   ├── middleware/
  │   ├── routers/
  │   │   └── health.py
  │   ├── services/
  │   ├── models/
  │   └── utils/
  └── tests/
  ```
- Implement `config.py` using pydantic-settings for environment-based configuration (LLM provider URLs, platform API base URL, timeouts, feature flags, log levels).
- Implement `GET /health` (liveness) and `GET /ready` (readiness) endpoints. Readiness must verify that the platform API is reachable and the LLM provider is configured.
- Define the startup and shutdown lifecycle (connection pool initialization, graceful shutdown of in-flight requests).
- Document the graceful degradation contract: when the sidecar is down, the API server must return appropriate fallback responses (e.g., 503 with a machine-readable code like `AI_SERVICE_UNAVAILABLE`) so the advisor portal can hide or disable AI features without breaking core workflows.
- Add Dockerfile and docker-compose fragment for local development.
- Add dependency management (pyproject.toml or requirements.txt with pinned versions).

**Acceptance Criteria:**
- [ ] FastAPI application starts and serves `GET /health` returning `200 OK` with `{"status": "healthy"}`.
- [ ] `GET /ready` returns `200` when platform API and LLM provider are reachable, `503` otherwise with a structured body indicating which dependency is unavailable.
- [ ] All configuration values are loaded from environment variables with sensible defaults and validated at startup via pydantic-settings.
- [ ] Graceful shutdown completes in-flight requests within a configurable timeout before exiting.
- [ ] The API server's AI module handles sidecar unavailability by returning a structured error response, not an unhandled exception.
- [ ] Project structure matches the recommended layout from the sidecar spec.
- [ ] Unit tests cover health and readiness endpoint logic.

**Dependencies:** None (foundational issue).

---

## Issue 18.2: Platform Client in Sidecar

**Title:** Implement narrow typed platform_client with versioned read methods

**Description:**
Build the `platform_client` module as the single approved internal data access path for the sidecar. This client exposes a small set of explicitly typed read methods that call the API server's internal endpoints. It must not provide generic or unbounded data access. Each method returns a typed response model and includes freshness metadata.

**Scope:**
- Implement `app/services/platform_client/` with the following methods:
  - `get_household_summary(tenant_id, household_id) -> HouseholdSummary`
  - `get_account_summary(tenant_id, account_id) -> AccountSummary`
  - `get_transfer_case(tenant_id, transfer_id) -> TransferCase`
  - `get_order_projection(tenant_id, order_id) -> OrderProjection`
  - `get_report_snapshot(tenant_id, report_id) -> ReportSnapshot`
  - `get_document_metadata(tenant_id, document_id) -> DocumentMetadata`
- Each method must propagate request context headers (tenant ID, actor ID, request ID, conversation ID) from the incoming sidecar request.
- Define typed Pydantic response models for each method with `as_of` timestamp fields.
- Use `httpx.AsyncClient` with connection pooling and configurable timeouts.
- Include structured error handling: distinguish platform read failures from network errors, and surface both as typed exceptions.
- Version the client interface so that breaking changes in platform API responses are caught at the client boundary, not deep in LLM prompt construction.
- The client must never expose write endpoints (POST to submit, approve, cancel, etc.).

**Acceptance Criteria:**
- [ ] All six read methods are implemented with typed request parameters and Pydantic response models.
- [ ] Each response model includes an `as_of` field indicating data freshness.
- [ ] Request context (tenant ID, actor ID, request ID) is propagated as headers on every outbound call.
- [ ] Connection pooling and per-request timeouts are configurable via `config.py`.
- [ ] Platform read failures raise a typed `PlatformReadError` with upstream status code and error body.
- [ ] Network/timeout errors raise a typed `PlatformUnavailableError`.
- [ ] No write/mutation methods exist on the client.
- [ ] Unit tests cover success, 4xx, 5xx, and timeout scenarios using mocked HTTP responses.

**Dependencies:** Issue 18.1 (sidecar service setup).

---

## Issue 18.3: Request Context Propagation

**Title:** Implement request context propagation middleware for tenant, actor, request, conversation, and role context

**Description:**
Every request from the API server to the sidecar must carry tenant ID, actor ID, request ID, conversation ID (when applicable), and the actor's role set. This context is used for isolation, traceability, audit logging, and data scoping -- not for sidecar-side authorization decisions. Implement middleware that extracts, validates, and makes this context available throughout the request lifecycle.

**Scope:**
- Define a `RequestContext` model:
  ```python
  class RequestContext(BaseModel):
      tenant_id: str
      actor_id: str
      request_id: str
      conversation_id: str | None
      role_set: list[str]
  ```
- Implement FastAPI middleware (`app/middleware/context.py`) that:
  - Extracts context from well-defined request headers (`X-Tenant-ID`, `X-Actor-ID`, `X-Request-ID`, `X-Conversation-ID`, `X-Role-Set`).
  - Validates that required fields (tenant_id, actor_id, request_id) are present; returns `400` if missing.
  - Stores context in a request-scoped dependency so all downstream services, the platform_client, and logging can access it without explicit parameter threading.
- Ensure all structured log entries automatically include tenant_id, actor_id, and request_id.
- Ensure all outbound platform_client calls propagate these headers.
- Generate a request_id if not provided (fallback for direct testing), but log a warning.

**Acceptance Criteria:**
- [ ] Middleware extracts and validates context from headers on every request.
- [ ] Requests missing required context fields (tenant_id, actor_id, request_id) receive a `400` response with a clear error message.
- [ ] `RequestContext` is accessible as a FastAPI dependency in all route handlers.
- [ ] All structured log lines include tenant_id, actor_id, and request_id.
- [ ] All outbound platform_client HTTP calls include context headers.
- [ ] conversation_id is optional and correctly handled when absent.
- [ ] role_set is parsed from a comma-separated header or JSON array header.
- [ ] Unit tests cover valid context, missing required fields, and optional field absence.

**Dependencies:** Issue 18.1, Issue 18.2.

---

## Issue 18.4: LLM Client

**Title:** Implement configurable LLM client with OpenAI-compatible interface, retry, circuit breaker, and token tracking

**Description:**
Build the LLM client module that abstracts interaction with language model providers. The client must use an OpenAI-compatible API interface, support configurable provider switching, implement retry logic with exponential backoff, include a circuit breaker to prevent cascading failures when the provider is down, and track token usage per request for cost monitoring and audit.

**Scope:**
- Implement `app/services/llm/client.py` with:
  - OpenAI-compatible chat completion interface (messages in, completion out).
  - Configurable provider base URL and API key (supports OpenAI, Azure OpenAI, or any compatible provider).
  - Configurable model selection per request type (e.g., different models for chat vs. document extraction).
  - Retry with configurable max retries, exponential backoff, and jitter for transient failures (429, 500, 502, 503).
  - Circuit breaker: after N consecutive failures within a time window, short-circuit requests for a cooldown period and return a structured `LLMUnavailableError`.
  - Token tracking: capture prompt_tokens, completion_tokens, and total_tokens from the provider response and attach to a per-request metrics object.
  - Request timeout configuration.
  - Structured error types: `LLMRateLimitError`, `LLMUnavailableError`, `LLMResponseError`.
- Implement `app/services/llm/models.py` with typed request/response models (messages, completions, token usage).
- Ensure the client logs provider, model, token counts, and latency per call (with request context from Issue 18.3).

**Acceptance Criteria:**
- [ ] LLM client sends chat completion requests to a configurable OpenAI-compatible endpoint.
- [ ] Provider base URL, API key, and default model are configurable via environment variables.
- [ ] Retry logic handles 429 and 5xx responses with exponential backoff and jitter, up to a configurable max retries.
- [ ] Circuit breaker opens after a configurable number of consecutive failures and rejects requests for a configurable cooldown period.
- [ ] Token usage (prompt, completion, total) is captured from every successful response and attached to request-scoped metrics.
- [ ] Structured errors distinguish rate limiting, unavailability, and response parsing failures.
- [ ] All LLM calls log provider, model, token counts, latency, and request context fields.
- [ ] Unit tests cover success, retry on transient error, circuit breaker open/close, and timeout scenarios.

**Dependencies:** Issue 18.1, Issue 18.3.

---

## Issue 18.5: Advisor Copilot Chat

**Title:** Implement POST /ai/chat with context enrichment, conversation memory, and markdown responses

**Description:**
Build the core copilot chat endpoint that allows advisors to ask questions about client situations, holdings, performance, activity, and operational status. The endpoint enriches the advisor's question with current platform context, maintains conversation memory for multi-turn interactions, and returns markdown-formatted responses with structured metadata.

**Scope:**
- Implement `POST /ai/chat` in `app/routers/chat.py`:
  - Request body: `{ message: string, conversation_id: string | null, household_id: string | null, account_id: string | null }`.
  - Response body:
    ```json
    {
      "message": "markdown-formatted response",
      "conversation_id": "conv_abc",
      "metadata": {
        "citations": [],
        "as_of": "2026-03-26T...",
        "confidence": "high | medium | low",
        "warnings": [],
        "recommended_actions": [],
        "follow_up_questions": []
      },
      "token_usage": { "prompt": 0, "completion": 0, "total": 0 }
    }
    ```
- Implement `app/services/chat/service.py`:
  - Context enrichment: if household_id or account_id is provided, fetch relevant summaries via platform_client before prompting the LLM.
  - Conversation memory: maintain message history per conversation_id. Store in Redis with a configurable TTL (e.g., 1 hour). Trim history to a configurable max token budget.
  - System prompt construction: include advisor role context, behavioral constraints (from Issue 18.7), and enriched platform data.
  - LLM call via the LLM client (Issue 18.4).
  - Parse and return the response with metadata extraction.
- The endpoint must handle platform_client failures gracefully: if context enrichment fails, the copilot should still respond with a disclaimer about limited context rather than failing entirely.

**Acceptance Criteria:**
- [ ] `POST /ai/chat` accepts a message and optional conversation_id, household_id, and account_id.
- [ ] When household_id or account_id is provided, the service fetches the relevant summary from the platform_client and includes it in the LLM prompt context.
- [ ] Conversation history is maintained per conversation_id in Redis with configurable TTL.
- [ ] Conversation history is trimmed to stay within a configurable max token budget.
- [ ] The response includes a markdown-formatted message and structured metadata (citations, as_of, confidence, warnings, recommended_actions, follow_up_questions).
- [ ] If platform_client enrichment fails, the endpoint still returns a response with a warning indicating limited or stale context.
- [ ] Token usage is included in the response.
- [ ] Integration tests cover: new conversation, multi-turn conversation, enrichment with household context, enrichment failure fallback.

**Dependencies:** Issue 18.2, Issue 18.3, Issue 18.4.

---

## Issue 18.6: Copilot Tool Invocation Framework

**Title:** Implement read-only tool invocation framework with max calls per turn and result feedback to LLM

**Description:**
Enable the copilot LLM to invoke read-only tools during a conversation turn to fetch live platform data. The framework defines a registry of allowed tools, enforces that only read operations are permitted, caps the number of tool calls per turn, and feeds tool results back into the LLM for final response synthesis.

**Scope:**
- Implement `app/services/chat/tools.py`:
  - Define a tool registry mapping tool names to platform_client methods:
    - `get_household_summary` -> `platform_client.get_household_summary`
    - `get_account_summary` -> `platform_client.get_account_summary`
    - `get_transfer_case` -> `platform_client.get_transfer_case`
    - `get_order_projection` -> `platform_client.get_order_projection`
    - `get_report_snapshot` -> `platform_client.get_report_snapshot`
    - `get_document_metadata` -> `platform_client.get_document_metadata`
  - Each tool definition includes a name, description (for the LLM), parameter schema, and the bound read method.
  - Tool invocations are formatted as OpenAI-compatible function calls in the LLM request.
- Implement the tool execution loop in the chat service:
  - After receiving an LLM response with tool_calls, execute each tool call against the platform_client.
  - Feed tool results back into the conversation as tool messages and re-prompt the LLM.
  - Enforce a configurable max tool calls per turn (default: 5). If the LLM requests more, stop and synthesize a response with available data plus a warning.
- Explicitly block any tool that would perform a mutation. The tool registry must be an allowlist, not a denylist.
- Tool execution failures should be reported to the LLM as error results so it can respond appropriately (e.g., "I was unable to retrieve the transfer status").

**Acceptance Criteria:**
- [ ] Tool registry contains exactly the six approved read-only tools with typed parameter schemas.
- [ ] The LLM can request tool calls via OpenAI-compatible function calling, and the framework executes them.
- [ ] Tool results are fed back into the LLM conversation for response synthesis.
- [ ] A configurable max tool calls per turn is enforced; exceeding it triggers a synthesized response with a warning.
- [ ] No mutation tools (submit, approve, cancel, initiate) can be registered or invoked.
- [ ] Tool execution failures are returned to the LLM as structured error results, not exceptions.
- [ ] Each tool invocation is logged with tool name, parameters (excluding sensitive values), latency, and success/failure.
- [ ] Unit tests cover: single tool call, multi-tool call, max tool limit enforcement, tool execution failure, and attempted registration of a mutation tool.

**Dependencies:** Issue 18.2, Issue 18.4, Issue 18.5.

---

## Issue 18.7: Copilot Behavioral Constraints

**Title:** Implement and enforce copilot behavioral constraints for safety, accuracy, and compliance

**Description:**
Define and enforce the behavioral guardrails that ensure the copilot does not make execution claims, provide authoritative regulated advice, or misrepresent stale data as current truth. These constraints are encoded in system prompts, output validation, and response post-processing.

**Scope:**
- Define the behavioral constraint set as a structured configuration (`app/services/chat/constraints.py`):
  1. **No execution claims:** The copilot must not claim that an action has been executed (e.g., "I've submitted the order") unless authoritative platform status confirms it. The copilot may say "I recommend submitting..." or "The current status is..."
  2. **No authoritative advice:** The copilot must not provide definitive legal, tax, or compliance advice. It must frame tax and compliance observations as decision support (e.g., "This may be worth discussing with a tax advisor").
  3. **Freshness disclosure:** When presenting platform data, the copilot must disclose the as_of timestamp. Stale projections (beyond a configurable threshold) must carry an explicit staleness warning.
  4. **Recommendation framing:** Suggested actions must be framed as recommendations, not completed actions. Use phrasing like "You may want to consider..." or "A rebalance proposal could address..."
  5. **Scope limitation:** The copilot must decline requests to perform actions it cannot perform (e.g., "I can't submit orders, but I can help you prepare one").
- Encode constraints in the system prompt template used by the chat service.
- Implement a response post-processor (`app/services/chat/post_processor.py`) that:
  - Scans LLM output for phrases that violate constraints (e.g., "I have submitted", "I've transferred", "You should definitely").
  - Flags violations in response metadata `warnings` array.
  - Optionally rewrites or annotates flagged phrases (configurable: flag-only vs. rewrite mode).
- Ensure all constraint definitions are centralized and reusable across chat, portfolio explanation, and operations summarization endpoints.

**Acceptance Criteria:**
- [ ] System prompt template includes all five behavioral constraints in clear, machine-interpretable instruction format.
- [ ] Post-processor detects phrases that claim execution of regulated actions and adds warnings to metadata.
- [ ] Post-processor detects phrases that provide authoritative tax/legal/compliance advice and flags them.
- [ ] Responses that include platform data carry as_of timestamps; data older than the configured staleness threshold triggers a freshness warning.
- [ ] Constraint configuration is centralized in a single module and reused across all AI endpoints.
- [ ] Flag-only and rewrite modes are both supported and configurable.
- [ ] Unit tests cover: execution claim detection, authoritative advice detection, freshness warning injection, and clean pass-through for compliant responses.

**Dependencies:** Issue 18.5.

---

## Issue 18.8: Document Classification

**Title:** Implement POST /ai/documents/classify for uploaded document type classification with confidence scoring

**Description:**
Build the document classification endpoint that accepts a reference to an uploaded document and returns a classification label (e.g., account application, transfer form, tax document, identity verification) along with a confidence score. This enables downstream routing of documents to the correct workflow without manual triage.

**Scope:**
- Implement `POST /ai/documents/classify` in `app/routers/documents.py`:
  - Request body: `{ document_id: string, document_url: string | null }` (document_url is a signed URL for the artifact; document_id is used to fetch metadata from platform_client).
  - Response body:
    ```json
    {
      "document_id": "doc_123",
      "classification": {
        "label": "ACCOUNT_APPLICATION",
        "confidence": 0.94,
        "alternative_labels": [
          { "label": "TRANSFER_FORM", "confidence": 0.04 }
        ]
      },
      "recommended_action": {
        "type": "ROUTE_TO_ONBOARDING",
        "reason": "Document classified as account application with high confidence"
      },
      "warnings": [],
      "as_of": "2026-03-26T..."
    }
    ```
- Implement `app/services/documents/classifier.py`:
  - Fetch document metadata via platform_client.get_document_metadata if needed.
  - Construct a classification prompt with the document content (text extracted from PDF/image or raw text) and the defined label taxonomy.
  - Call the LLM via the LLM client.
  - Parse the structured classification response.
- Define the label taxonomy as a configurable enum:
  - `ACCOUNT_APPLICATION`, `TRANSFER_FORM`, `TAX_DOCUMENT`, `IDENTITY_VERIFICATION`, `BANK_STATEMENT`, `TRUST_DOCUMENT`, `POWER_OF_ATTORNEY`, `BENEFICIARY_DESIGNATION`, `CORRESPONDENCE`, `OTHER`.
- Include recommended_action in the response (e.g., route to onboarding, route to operations review, request clearer upload).
- The sidecar must not self-approve a document for a regulated workflow.

**Acceptance Criteria:**
- [ ] `POST /ai/documents/classify` accepts a document_id and optional document_url.
- [ ] Response includes a primary classification label with confidence score and alternative labels.
- [ ] Response includes a recommended_action payload (type + reason).
- [ ] Classification labels come from a defined, configurable taxonomy.
- [ ] Confidence scores are between 0 and 1.
- [ ] Low-confidence classifications (below a configurable threshold, e.g., 0.7) include a warning recommending manual review.
- [ ] The endpoint does not approve, attach, or otherwise mutate the document's workflow state.
- [ ] Unit tests cover: high-confidence classification, low-confidence with warning, and document metadata fetch failure.

**Dependencies:** Issue 18.2, Issue 18.3, Issue 18.4.

---

## Issue 18.9: Document Field Extraction

**Title:** Implement POST /ai/documents/extract for structured field extraction with per-field confidence

**Description:**
Build the document extraction endpoint that takes a classified document and extracts structured fields (e.g., name, SSN, account number, date of birth, address) with per-field confidence scores. The output is a structured payload that downstream systems can use to pre-populate onboarding forms or validate against platform records.

**Scope:**
- Implement `POST /ai/documents/extract` in `app/routers/documents.py`:
  - Request body: `{ document_id: string, document_url: string | null, document_type: string | null }` (document_type hints which extraction schema to use; if absent, classify first).
  - Response body:
    ```json
    {
      "document_id": "doc_123",
      "document_type": "ACCOUNT_APPLICATION",
      "extracted_fields": [
        { "field": "full_name", "value": "Jane Doe", "confidence": 0.97 },
        { "field": "ssn_last_four", "value": "1234", "confidence": 0.91 },
        { "field": "date_of_birth", "value": "1985-03-15", "confidence": 0.88 },
        { "field": "account_type", "value": "Individual", "confidence": 0.82 }
      ],
      "warnings": ["Field 'account_type' has moderate confidence; manual review recommended"],
      "recommended_action": {
        "type": "ATTACH_TO_ONBOARDING",
        "reason": "Extracted fields match account application schema"
      },
      "as_of": "2026-03-26T..."
    }
    ```
- Implement `app/services/documents/extractor.py`:
  - Define extraction schemas per document type (which fields to extract, expected formats, validation patterns).
  - Construct an extraction prompt that includes the document content and the target field schema.
  - Call the LLM via the LLM client.
  - Parse and validate extracted fields against expected formats (e.g., date format, numeric patterns).
  - Assign per-field confidence based on LLM output and format validation.
- Fields below a configurable confidence threshold generate warnings.
- If document_type is not provided, call the classification service first.
- Sensitive extracted values (SSN, etc.) must be handled according to the platform's data sensitivity rules; full SSNs should not be logged.

**Acceptance Criteria:**
- [ ] `POST /ai/documents/extract` accepts a document_id with optional document_url and document_type.
- [ ] Response includes an array of extracted fields, each with field name, value, and confidence score.
- [ ] Extraction schemas are defined per document type and determine which fields are extracted.
- [ ] Per-field confidence reflects both LLM confidence and format validation.
- [ ] Fields below the configurable confidence threshold include warnings in the response.
- [ ] If document_type is absent, the service classifies the document first before extracting.
- [ ] Sensitive field values (SSN, TIN) are redacted in logs.
- [ ] The endpoint does not mutate any platform records.
- [ ] Unit tests cover: successful extraction with high confidence, mixed confidence with warnings, missing document_type triggering classification, and format validation failures.

**Dependencies:** Issue 18.4, Issue 18.8.

---

## Issue 18.10: Document Validation

**Title:** Implement document validation to compare extracted fields against platform records and flag mismatches

**Description:**
Build a validation layer that compares fields extracted from a document (Issue 18.9) against existing platform records (e.g., client person records, account registrations) and flags mismatches. This helps operations teams identify discrepancies before approving documents into regulated workflows.

**Scope:**
- Implement `app/services/documents/validator.py`:
  - Accept extracted fields and a reference to the platform record to validate against (e.g., client_id, account_id, or household_id).
  - Fetch the reference record via platform_client (get_household_summary, get_account_summary, or a dedicated client profile method).
  - Compare extracted fields to platform record fields:
    - Exact match, fuzzy match (for names with minor variations), and mismatch categories.
    - Date comparison with format normalization.
    - Numeric comparison for account numbers and identifiers.
  - Return a validation result:
    ```json
    {
      "document_id": "doc_123",
      "reference_record_id": "client_456",
      "validation_results": [
        { "field": "full_name", "extracted": "Jane Doe", "platform": "Jane A. Doe", "status": "fuzzy_match", "confidence": 0.85 },
        { "field": "date_of_birth", "extracted": "1985-03-15", "platform": "1985-03-15", "status": "exact_match", "confidence": 1.0 },
        { "field": "ssn_last_four", "extracted": "1234", "platform": "1234", "status": "exact_match", "confidence": 1.0 },
        { "field": "address", "extracted": "123 Main St", "platform": "123 Main Street, Apt 4", "status": "mismatch", "confidence": 0.4 }
      ],
      "overall_status": "review_required",
      "warnings": ["Address mismatch detected; manual review recommended"],
      "recommended_action": {
        "type": "ROUTE_TO_OPERATIONS_REVIEW",
        "reason": "One or more field mismatches require human review"
      }
    }
    ```
  - Overall status is one of: `validated`, `review_required`, `rejected`.
  - Mismatches above a configurable threshold trigger `review_required`.
- The validator does not approve or reject documents; it provides decision support for operations teams.
- Integrate validation as an optional step callable after extraction, or as a combined extract-and-validate flow.

**Acceptance Criteria:**
- [ ] Validator compares extracted fields against platform records fetched via platform_client.
- [ ] Comparison supports exact match, fuzzy match (for name variations), and mismatch statuses.
- [ ] Each field comparison includes a confidence score.
- [ ] Overall status reflects the worst-case field result (any mismatch triggers review_required).
- [ ] Warnings are generated for each mismatched field.
- [ ] recommended_action is included based on overall status.
- [ ] The validator does not mutate any platform records or approve documents.
- [ ] Sensitive field values are redacted in logs.
- [ ] Unit tests cover: all fields match, fuzzy name match, date format normalization, address mismatch, and platform record fetch failure.

**Dependencies:** Issue 18.2, Issue 18.9.

---

## Issue 18.11: Report Narrative Generation

**Title:** Implement POST /ai/reports/narrative for frozen-snapshot narrative generation with tone control and structured sections

**Description:**
Build the report narrative generation endpoint that creates human-readable narrative text for client-facing reports. Narratives are generated from frozen snapshot inputs (not mutable live data) and support configurable tone and structured sections. This ensures that published report narratives are deterministic and reproducible from their inputs.

**Scope:**
- Implement `POST /ai/reports/narrative` in `app/routers/reports.py`:
  - Request body:
    ```json
    {
      "report_snapshot_id": "snap_123",
      "performance_period": { "start": "2025-10-01", "end": "2025-12-31" },
      "tone": "professional | conversational | concise",
      "sections": ["performance_summary", "holdings_overview", "market_commentary", "outlook"],
      "household_id": "hh_456",
      "include_benchmark_comparison": true
    }
    ```
  - Response body:
    ```json
    {
      "report_snapshot_id": "snap_123",
      "narrative": {
        "sections": [
          { "title": "Performance Summary", "content": "markdown text...", "as_of": "2025-12-31" },
          { "title": "Holdings Overview", "content": "markdown text...", "as_of": "2025-12-31" }
        ]
      },
      "metadata": {
        "tone": "professional",
        "generated_at": "2026-03-26T...",
        "snapshot_as_of": "2025-12-31",
        "token_usage": { "prompt": 0, "completion": 0, "total": 0 },
        "warnings": []
      }
    }
    ```
- Implement `app/services/reports/narrative.py`:
  - Fetch the report snapshot via platform_client.get_report_snapshot. The snapshot must contain all data needed for narrative generation (performance numbers, holdings, benchmarks, fees if applicable).
  - Construct a narrative prompt using the frozen snapshot data, requested tone, and requested sections.
  - Call the LLM via the LLM client.
  - Parse the response into structured sections.
- The service must refuse to generate narratives from mutable live data. If the report_snapshot_id does not resolve to a frozen snapshot, return a 422 error.
- Tone control maps to system prompt variations: professional (formal, third person), conversational (approachable, second person), concise (bullet-point heavy, minimal prose).
- Generated narratives must carry the snapshot's as_of date, not the generation timestamp, as the data reference point.

**Acceptance Criteria:**
- [ ] `POST /ai/reports/narrative` accepts a report_snapshot_id, performance_period, tone, and sections list.
- [ ] Narratives are generated exclusively from frozen snapshot data fetched via platform_client.
- [ ] The endpoint returns 422 if the report_snapshot_id does not resolve to a valid frozen snapshot.
- [ ] Response includes structured sections with markdown content, each carrying the snapshot's as_of date.
- [ ] Tone control produces measurably different output styles (professional, conversational, concise).
- [ ] Token usage is included in response metadata.
- [ ] The service does not fetch or incorporate mutable live data.
- [ ] Unit tests cover: successful narrative generation, invalid snapshot ID, each tone variation, and section filtering.

**Dependencies:** Issue 18.2, Issue 18.3, Issue 18.4. Requires Epic 13 to provide frozen report snapshots.

---

## Issue 18.12: Portfolio Explanation and Commentary

**Title:** Implement POST /ai/portfolio/explain for drift, risk, and concentration explanation

**Description:**
Build the portfolio explanation endpoint that generates advisor-facing commentary on portfolio drift, risk exposure, concentration, and allocation relative to model targets. The output helps advisors understand the current state of a portfolio and identify candidate actions, framed as decision support rather than authoritative advice.

**Scope:**
- Implement `POST /ai/portfolio/explain` in `app/routers/portfolio.py`:
  - Request body:
    ```json
    {
      "account_id": "acc_123",
      "household_id": "hh_456",
      "explanation_types": ["drift", "risk", "concentration", "allocation_vs_model"],
      "include_recommendations": true
    }
    ```
  - Response body:
    ```json
    {
      "account_id": "acc_123",
      "explanations": [
        {
          "type": "drift",
          "summary": "markdown explanation of current drift from model...",
          "data_points": { "max_drift_pct": 4.2, "drifted_asset_classes": ["US Large Cap"] },
          "as_of": "2026-03-26T..."
        }
      ],
      "recommended_actions": [
        {
          "type": "CREATE_REBALANCE_PROPOSAL",
          "targetAccountId": "acc_123",
          "reason": "Allocation drift exceeded configured threshold"
        }
      ],
      "metadata": {
        "confidence": "medium",
        "warnings": ["Holdings data is 4 hours old"],
        "as_of": "2026-03-26T...",
        "token_usage": { "prompt": 0, "completion": 0, "total": 0 }
      }
    }
    ```
- Implement `app/services/portfolio/explainer.py`:
  - Fetch account summary and household summary via platform_client.
  - Construct explanation prompts for each requested explanation type.
  - Call the LLM to generate natural language explanations with supporting data points.
  - If include_recommendations is true, generate structured recommendation payloads (see Issue 18.13).
- All explanations must carry as_of timestamps from the underlying data.
- Recommendations must be tagged as proposals, not executed actions.
- Data freshness warnings must be included if the underlying data exceeds a configurable staleness threshold.

**Acceptance Criteria:**
- [ ] `POST /ai/portfolio/explain` accepts account_id or household_id and a list of explanation types.
- [ ] Each explanation includes a type, markdown summary, supporting data points, and as_of timestamp.
- [ ] When include_recommendations is true, structured recommended_actions are included in the response.
- [ ] Recommendations use the structured payload format (type, targetAccountId, reason).
- [ ] Data freshness warnings are included when underlying data exceeds the staleness threshold.
- [ ] Explanations are framed as decision support, not authoritative investment advice.
- [ ] Unit tests cover: each explanation type individually, combined explanation types, recommendation inclusion, and stale data warning.

**Dependencies:** Issue 18.2, Issue 18.3, Issue 18.4.

---

## Issue 18.13: Structured Recommendation Payloads

**Title:** Define and implement the structured recommendation payload contract between sidecar and API server

**Description:**
Formalize the contract for `recommended_actions` payloads that the sidecar returns. These payloads are declarative suggestions (not commands) that the API server can convert into normal platform command flows after user confirmation and permission checks. The sidecar returns the recommendation only; the API server decides whether the user can act on it.

**Scope:**
- Define the recommendation payload schema in `app/models/recommendations.py`:
  ```python
  class RecommendedAction(BaseModel):
      type: RecommendationTypeEnum  # e.g., CREATE_REBALANCE_PROPOSAL, ROUTE_TO_OPERATIONS_REVIEW, REQUEST_CLEARER_UPLOAD, ATTACH_TO_ONBOARDING, INITIATE_TAX_LOSS_HARVEST_REVIEW, SCHEDULE_CLIENT_REVIEW
      target_account_id: str | None
      target_household_id: str | None
      target_document_id: str | None
      target_case_id: str | None
      reason: str
      confidence: float  # 0.0 to 1.0
      metadata: dict | None  # additional context for the API server
  ```
- Define the `RecommendationTypeEnum` with all supported recommendation types. Each type must map to a known platform command path on the API server side.
- Implement a recommendation builder utility that standardizes recommendation construction across all sidecar endpoints (chat, portfolio, documents, reports).
- Document the API server's responsibility: when it receives a recommended_action, it must:
  1. Verify that the actor has permission to perform the mapped command.
  2. Present the recommendation to the advisor for confirmation.
  3. Execute through the normal command path if confirmed.
  4. Never auto-execute recommendations without user confirmation.
- Ensure recommendations are included in AI audit logs (Issue 18.15).

**Acceptance Criteria:**
- [ ] RecommendedAction Pydantic model is defined with type, target IDs, reason, confidence, and metadata.
- [ ] RecommendationTypeEnum contains all supported types, each documented with the corresponding platform command path.
- [ ] A recommendation builder utility is available and used consistently across chat, portfolio, document, and report endpoints.
- [ ] Recommendations include a confidence score between 0.0 and 1.0.
- [ ] The sidecar never includes execution confirmation or status in recommendations; they are purely declarative.
- [ ] API server integration documentation specifies the permission check, user confirmation, and command path mapping responsibilities.
- [ ] Unit tests validate recommendation construction, serialization, and type enum coverage.

**Dependencies:** Issue 18.1. Used by Issues 18.5, 18.8, 18.9, 18.10, 18.12, 18.14.

---

## Issue 18.14: Operational Status Summarization

**Title:** Implement POST /ai/operations/summarize for operational status summarization

**Description:**
Build the operational summarization endpoint that generates a concise, advisor-friendly summary of operational activity and status across a household or set of accounts. This covers pending transfers, onboarding cases, open tasks, recent activity, and exception states -- synthesized into a readable briefing rather than raw status lists.

**Scope:**
- Implement `POST /ai/operations/summarize` in `app/routers/operations.py`:
  - Request body:
    ```json
    {
      "household_id": "hh_456",
      "account_ids": ["acc_123", "acc_789"],
      "include_categories": ["transfers", "onboarding", "tasks", "recent_activity", "exceptions"],
      "time_window_days": 30
    }
    ```
  - Response body:
    ```json
    {
      "household_id": "hh_456",
      "summary": "markdown-formatted operational briefing...",
      "sections": [
        { "category": "transfers", "summary": "Two ACH deposits completed. One ACAT in transit (est. 3-5 days).", "item_count": 3, "as_of": "2026-03-26T..." },
        { "category": "exceptions", "summary": "No open exceptions.", "item_count": 0, "as_of": "2026-03-26T..." }
      ],
      "recommended_actions": [],
      "metadata": {
        "as_of": "2026-03-26T...",
        "warnings": [],
        "token_usage": { "prompt": 0, "completion": 0, "total": 0 }
      }
    }
    ```
- Implement `app/services/operations/summarizer.py`:
  - Fetch household summary and account summaries via platform_client.
  - For each requested category, gather relevant data (transfer cases, onboarding cases, etc.).
  - Construct a summarization prompt with the gathered data.
  - Call the LLM to generate a consolidated, advisor-friendly briefing.
  - Parse into structured sections.
- The summarizer must not claim that in-progress operations have completed unless authoritative status confirms it.
- Include freshness metadata for each section based on the underlying data's as_of timestamps.

**Acceptance Criteria:**
- [ ] `POST /ai/operations/summarize` accepts household_id, optional account_ids, category filter, and time window.
- [ ] Response includes a consolidated markdown summary and per-category structured sections.
- [ ] Each section includes item count and as_of timestamp from the underlying data.
- [ ] The summarizer does not claim completion of in-progress operations.
- [ ] Recommended actions are included where the LLM identifies actionable items (using the structured payload format).
- [ ] The endpoint handles partial data availability gracefully (e.g., if transfer data is unavailable, the transfers section is omitted with a warning).
- [ ] Unit tests cover: full category summary, filtered categories, partial data availability, and stale data warnings.

**Dependencies:** Issue 18.2, Issue 18.3, Issue 18.4, Issue 18.13.

---

## Issue 18.15: AI Audit Logging

**Title:** Implement AI audit logging for tool usage, prompts, recommendations, and token counts

**Description:**
Build a structured audit logging system for all AI sidecar activity to support compliance review. Every meaningful AI interaction must produce an audit record that captures what was asked, what data was accessed, what was recommended, and how many tokens were consumed. Logs must be queryable by tenant, actor, conversation, and time range.

**Scope:**
- Implement `app/services/audit/logger.py`:
  - Define an AI audit event schema:
    ```python
    class AIAuditEvent(BaseModel):
        event_id: str
        event_type: str  # "chat", "tool_invocation", "document_classify", "document_extract", "narrative_generate", "portfolio_explain", "operations_summarize"
        tenant_id: str
        actor_id: str
        request_id: str
        conversation_id: str | None
        timestamp: datetime
        # What was asked
        endpoint: str
        input_summary: str  # truncated/redacted summary of the input
        # What data was accessed
        tools_invoked: list[ToolInvocationRecord]  # tool name, parameters (redacted), latency, success
        platform_reads: list[str]  # list of platform_client methods called
        # What was produced
        output_summary: str  # truncated summary of the output
        recommended_actions: list[dict]  # serialized recommended_actions
        # Cost tracking
        token_usage: TokenUsage
        llm_provider: str
        llm_model: str
        llm_latency_ms: int
        # Constraints
        behavioral_warnings: list[str]  # any post-processor warnings triggered
    ```
  - Emit audit events at the end of each AI endpoint request.
  - Sensitive data (SSN, full document content, PII) must be redacted from audit logs. Log summaries and metadata, not raw prompts containing client data.
  - Store audit events to a structured log sink (initially structured JSON logs; later, these can be forwarded to the platform's audit event store from Epic 16).
- Implement audit middleware or a decorator that automatically captures endpoint, latency, and request context for every AI request.
- Ensure tool invocation records include the tool name, parameter keys (not values for sensitive params), latency, and success/failure.

**Acceptance Criteria:**
- [ ] Every AI endpoint request produces a structured AIAuditEvent at completion.
- [ ] Audit events include event_type, tenant_id, actor_id, request_id, conversation_id, endpoint, tool invocations, token usage, LLM provider/model, and latency.
- [ ] Sensitive data (PII, SSN, full document content) is redacted from audit log entries.
- [ ] Tool invocation records capture tool name, parameter keys, latency, and success/failure.
- [ ] Recommended actions are included in audit events.
- [ ] Behavioral constraint warnings are included in audit events.
- [ ] Audit events are emitted as structured JSON logs with a consistent schema.
- [ ] Audit events are queryable by tenant_id, actor_id, conversation_id, and timestamp range (at the log infrastructure level).
- [ ] Unit tests verify audit event emission, redaction of sensitive fields, and schema completeness.

**Dependencies:** Issue 18.3, Issue 18.4. Integrates with Epic 16 audit infrastructure.

---

## Issue 18.16: Caching Strategy

**Title:** Implement caching for conversation context, intermediate reads, and non-authoritative outputs with freshness metadata

**Description:**
Define and implement the caching strategy for the sidecar to reduce latency, minimize redundant platform reads, and control LLM costs. All cached data must carry source attribution, generation timestamp, and freshness/TTL metadata. Cached data must never be presented as authoritative for live balances, order status, or transfer completion state beyond a short TTL.

**Scope:**
- Implement `app/services/cache/manager.py` using Redis as the backing store:
  - **Conversation context cache:** Store conversation message history per conversation_id with a configurable TTL (default: 1 hour). Used by the chat service (Issue 18.5). Key format: `conv:{tenant_id}:{conversation_id}`.
  - **Platform read cache:** Cache responses from platform_client read methods with per-method configurable TTLs:
    - Household summary: 5 minutes
    - Account summary: 5 minutes
    - Transfer case: 60 seconds (short TTL due to active lifecycle)
    - Order projection: 60 seconds (short TTL due to active lifecycle)
    - Report snapshot: 30 minutes (frozen data, longer TTL)
    - Document metadata: 10 minutes
  - **Non-authoritative output cache:** Cache AI-generated outputs (portfolio explanations, operational summaries) with configurable TTLs (default: 15 minutes). Cache key includes input parameters hash.
- Every cached value must include:
  - `source`: the origin of the data (e.g., "platform_client.get_account_summary")
  - `cached_at`: when the value was stored
  - `ttl_seconds`: the configured TTL
  - `expires_at`: when the cache entry expires
- Implement cache-aside pattern: check cache before calling platform_client or LLM; populate cache on miss.
- Implement forced cache bypass: allow individual requests to include a `force_refresh: true` flag that skips the cache.
- The cache must never store or return as authoritative:
  - Live balances without freshness metadata
  - Live order status beyond the configured short TTL
  - Live transfer completion state beyond the configured short TTL

**Acceptance Criteria:**
- [ ] Conversation context is cached in Redis per conversation_id with configurable TTL.
- [ ] Platform read responses are cached with per-method configurable TTLs.
- [ ] Non-authoritative AI outputs are cached with configurable TTLs and input-hash-based cache keys.
- [ ] Every cached value includes source, cached_at, ttl_seconds, and expires_at metadata.
- [ ] Cache-aside pattern is implemented: cache hit returns immediately, cache miss triggers fetch and populates cache.
- [ ] `force_refresh: true` on a request bypasses the cache and fetches fresh data.
- [ ] Transfer case and order projection caches use short TTLs (configurable, default 60 seconds).
- [ ] Cache hit/miss metrics are emitted for observability.
- [ ] Unit tests cover: cache hit, cache miss with population, TTL expiration, forced refresh bypass, and metadata presence on cached values.

**Dependencies:** Issue 18.1, Issue 18.2, Issue 18.5.

---

## Issue Dependency Graph

```
18.1  Sidecar Setup
 ├── 18.2  Platform Client
 │    ├── 18.5  Copilot Chat ──── 18.6  Tool Framework
 │    │    └── 18.7  Behavioral Constraints
 │    ├── 18.8  Document Classification ──── 18.9  Document Extraction
 │    │                                        └── 18.10  Document Validation
 │    ├── 18.11  Report Narrative
 │    ├── 18.12  Portfolio Explanation
 │    └── 18.14  Operational Summarization
 ├── 18.3  Request Context
 ├── 18.4  LLM Client
 ├── 18.13  Structured Recommendations (used across 18.5, 18.8-18.10, 18.12, 18.14)
 ├── 18.15  AI Audit Logging (cross-cutting)
 └── 18.16  Caching Strategy (cross-cutting)
```

## Suggested Implementation Order

1. **Foundation (parallel):** 18.1, 18.13
2. **Infrastructure (parallel after 18.1):** 18.2, 18.3, 18.4
3. **Core features (parallel after infrastructure):** 18.5, 18.8, 18.11
4. **Feature extensions:** 18.6 (after 18.5), 18.9 (after 18.8), 18.12, 18.14
5. **Refinements:** 18.7 (after 18.5), 18.10 (after 18.9)
6. **Cross-cutting (can start early, mature throughout):** 18.15, 18.16
