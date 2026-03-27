# Python Sidecar Spec v2

## 1. Purpose

The Python sidecar is an augmentation service for AI and analytics. It improves advisor productivity and document workflows, but it is not part of the authoritative operational control path.

The sidecar exists to:

- answer advisor questions with current platform context
- generate narratives and summaries
- classify and extract documents
- detect opportunities and anomalies
- compute selected analytics that do not need to live in the transactional core

The sidecar does not own:

- tenant identity
- permissions
- workflow state
- order lifecycle state
- transfer lifecycle state
- balances or ledger truth
- security master truth

## 2. Design Principle

The sidecar is read-oriented and recommendation-oriented.

It may:

- read from platform APIs and approved read models
- call LLM providers
- compute analytics
- return drafts, suggestions, classifications, and summaries

It may not:

- directly mutate regulated records
- directly submit orders to OMS/EMS
- directly initiate ACH, ACAT, or wires
- directly alter workflow states
- become the source of truth for current holdings, balances, or transfer status

Any action it recommends must be executed through normal platform command endpoints after permissions, policy, and workflow checks.

## 3. Placement In The Overall System

```text
advisor request
    -> API server
        -> enriches request with tenant and actor context
        -> calls sidecar for assistive work
            -> sidecar reads platform APIs and projections
            -> sidecar may call LLM providers
        -> API server returns AI result

recommendation selected by advisor
    -> API server command path
        -> normal workflow and external integrations
```

The sidecar is intentionally not on the direct write path to financial infrastructure.

## 4. Service Responsibilities

### 4.1 Supported capabilities

- advisor copilot chat
- narrative performance summaries
- document classification
- document extraction
- portfolio explanation and commentary
- tax opportunity explanation
- onboarding assistant guidance
- operational status summarization

### 4.2 Explicitly unsupported responsibilities

- order creation or routing
- transfer creation or submission
- account approval decisions
- billing posting
- audit authority
- permission evaluation

## 5. Inputs And Data Access

## 5.1 Source of context

The sidecar reads context from:

- platform-owned APIs
- platform-owned analytical read models
- document artifact references
- explicitly approved projections of external systems exposed through the platform

The sidecar should not call security master, OMS/EMS, or transfer-rail microservices directly unless the platform team later decides to expose tightly-scoped read adapters for that purpose. Default rule: access those domains through the platform boundary.

## 5.2 Required request context

Every request from the API layer must include:

- tenant ID
- actor ID
- actor role set or effective permissions summary
- request ID
- conversation ID when applicable

This context is for isolation and traceability, not for sidecar-side authorization decisions.

## 5.3 Read patterns

Use synchronous read calls when:

- the assistant needs fresh status for a user-facing answer
- the data payload is bounded

Use prebuilt read models when:

- the answer relies on aggregated dashboard data
- the same context is used repeatedly
- latency matters more than raw freshness

Examples:

- fresh account summary during copilot chat: sync platform read
- monthly report narrative using a frozen snapshot: read model or artifact input

## 6. Recommended Structure

```text
sidecar/
├── app/
│   ├── main.py
│   ├── config.py
│   ├── middleware/
│   ├── routers/
│   │   ├── chat.py
│   │   ├── reports.py
│   │   ├── documents.py
│   │   ├── portfolio.py
│   │   └── health.py
│   ├── services/
│   │   ├── platform_client/
│   │   ├── llm/
│   │   ├── chat/
│   │   ├── reports/
│   │   ├── documents/
│   │   └── analytics/
│   ├── models/
│   └── utils/
└── tests/
```

The `platform_client` is the single approved internal data access path.

## 7. Endpoint Surface

Suggested sidecar endpoints:

- `POST /ai/chat`
- `POST /ai/reports/narrative`
- `POST /ai/documents/classify`
- `POST /ai/documents/extract`
- `POST /ai/portfolio/explain`
- `POST /ai/operations/summarize`

These endpoints should remain narrow. If an AI flow needs to trigger a real action, it should return a structured recommendation payload that the API server can convert into a standard command flow after user confirmation.

## 8. Chat And Copilot Model

## 8.1 Copilot purpose

The copilot helps advisors:

- understand current client situations
- summarize holdings, performance, and activity
- identify candidate next steps
- explain operational statuses
- draft messages or meeting prep notes

## 8.2 Copilot limits

The copilot must not:

- claim execution occurred unless authoritative status confirms it
- imply a transfer is complete if it is merely submitted
- provide authoritative legal, tax, or compliance advice
- represent stale projections as live truth without disclosing freshness

## 8.3 Tooling model

Tool invocation is allowed for bounded reads and computations, such as:

- get household summary
- get account summary
- get transfer case status
- get order/execution status projection
- get report snapshot
- run portfolio analytics explanation

Tool invocation is not allowed for:

- submit order
- cancel order
- initiate transfer
- approve onboarding

## 9. Report Narrative Generation

Narrative generation should operate on frozen inputs:

- report snapshot ID
- performance period
- benchmark snapshot
- holdings snapshot
- billing/fee snapshot if shown

The sidecar should never narrate directly from mutable live data when generating a published client-facing artifact.

## 10. Document Intelligence

Supported document flows:

- classify uploaded document type
- extract structured fields
- compare extracted fields to platform records
- flag mismatches and confidence

Document outputs should include:

- classification label
- extracted fields
- confidence scores
- validation warnings
- recommended next action

The sidecar may recommend:

- request clearer upload
- route to operations review
- attach to onboarding case

The sidecar may not self-approve a document for a regulated workflow.

## 11. Portfolio And Tax Analytics

Analytics in the sidecar are assistive, not authoritative.

Suitable examples:

- explain portfolio concentration
- summarize drift
- draft tax-loss harvesting opportunities
- compare model intent to current allocation

Guardrails:

- any recommended trade must be tagged as a proposal
- wash-sale or tax logic should be treated as decision support unless the platform later formalizes a fully governed tax engine
- sidecar outputs should carry data freshness metadata

## 12. Output Contract

AI responses should support two formats:

1. Human-readable answer
2. Structured metadata for the product

Suggested metadata fields:

- `citations` or source references
- `as_of`
- `confidence`
- `warnings`
- `recommended_actions`
- `follow_up_questions`

Where relevant, `recommended_actions` should be declarative, for example:

```json
[
  {
    "type": "CREATE_REBALANCE_PROPOSAL",
    "targetAccountId": "acc_123",
    "reason": "Allocation drift exceeded configured threshold"
  }
]
```

The sidecar returns the recommendation only. The API server decides whether the user can act on it.

## 13. Caching

The sidecar may cache:

- conversation context
- intermediate enriched reads
- non-authoritative analytics outputs
- token/accounting data

The sidecar may not cache and present as authoritative:

- live balances without freshness metadata
- live order status beyond a short TTL
- live transfer completion state beyond a short TTL

All cached responses should include:

- source
- generated timestamp
- freshness or TTL metadata

## 14. Safety And Compliance Constraints

Required behavioral constraints:

- identify uncertain or stale data clearly
- avoid definitive tax, legal, or compliance advice
- refuse direct action execution
- log tool usage and major prompts for audit where permitted
- redact sensitive data from logs

For client-visible generated content:

- prefer snapshot-based inputs
- enforce review before publication where policy requires it

## 15. Reliability Model

The sidecar should degrade gracefully.

If unavailable:

- advisor workflows for real platform operations continue
- only assistive AI features fail

This is a hard design goal. The platform cannot depend on the sidecar to complete:

- onboarding approvals
- transfer submissions
- order submissions
- billing posts
- statement generation pipelines, except optional narrative sections

## 16. Observability

Log and meter:

- tenant ID
- actor ID
- request ID
- conversation ID
- prompt and tool latency
- provider/model used
- token counts
- tool call counts
- cache hit rates
- failure classifications

Track separate failure classes:

- platform read failure
- LLM provider failure
- document parse failure
- context-too-large failure

## 17. Interface To Platform API

The sidecar should use a versioned internal client with explicit methods such as:

- `get_household_summary`
- `get_account_summary`
- `get_transfer_case`
- `get_order_projection`
- `get_execution_projection`
- `get_report_snapshot`
- `get_document_metadata`

Avoid a generic unbounded data callback surface. Narrow clients are safer, easier to audit, and less likely to turn into hidden dependency sprawl.

## 18. Key Replacements To Prior Sidecar Spec

1. Replace "pure compute but central to operational flow" with "assistive service outside the authoritative write path."

2. Replace direct dependence on wide internal data APIs with narrower platform-owned read contracts.

3. Remove any implication that the sidecar should directly broker fresh data from OMS/EMS or money movement systems.

4. Keep all regulated actions behind normal product workflows and permissions.

5. Make snapshot-based generation the default for published narratives and client-facing artifacts.
