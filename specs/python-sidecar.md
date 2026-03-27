# Python Sidecar Spec v3 — AI Operating Layer

## 1. Purpose

The Python sidecar is the AI operating layer for the wealth platform. It powers Hazel — the advisor-facing AI assistant that spans custodial data, documents, emails, CRM, meetings, and tax planning.

The sidecar exists to:

- answer advisor questions across all firm data (documents, emails, notes, CRM, custodial records)
- generate daily briefings and meeting prep
- draft emails in each advisor's writing style
- transcribe and summarize meeting artifacts with action items
- triage email inboxes and surface priority messages
- classify and extract uploaded documents
- generate report narratives and firm-wide analytics
- run tax planning analysis and what-if scenarios
- provide real-time portfolio analytics (concentration, RMD, loss harvesting)
- generate task recommendations and CRM sync payloads for platform execution

The sidecar does not own:

- tenant identity or permissions
- workflow state or approval decisions
- order lifecycle or transfer lifecycle
- balances or ledger truth
- security master truth
- mobile or browser capture UX
- mailbox, CRM, or custodial connector execution
- report artifact publication or system-of-record storage

Any action it recommends must be executed through normal platform command endpoints after advisor confirmation.

## 2. Design Principle

The sidecar is **read-oriented and recommendation-oriented**. It may read broadly, compute aggressively, and suggest confidently — but it never mutates regulated records directly.

This means the sidecar owns:

- AI reasoning
- summarization
- drafting
- retrieval over approved context
- classification
- extraction
- scoring and prioritization
- scenario analysis

This means the sidecar does not own:

- authentication
- authorization
- system integrations as systems of record
- external API write execution
- workflow advancement
- operational evidence storage
- client application behavior

## 3. Agent Framework

### 3.1 Primary framework: Pydantic AI

Pydantic AI is the agent framework for all Hazel capabilities.

Why:

- **Typed structured output** — recommendations, tax plans, meeting summaries all return validated Pydantic models
- **Provider flexibility** — swap Anthropic/OpenAI/Together/Groq with one-line changes; fallback chains for redundancy
- **Dependency injection for tools** — read-only platform client methods injected as typed tool dependencies, enforced at the type level
- **Testable** — agents can be unit tested with mock dependencies, no LLM calls needed
- **No mutation risk** — tools are typed Python functions, not arbitrary code execution

### 3.2 Supporting infrastructure

| Concern | Technology |
|---------|-----------|
| Agent framework | Pydantic AI |
| HTTP framework | FastAPI |
| Conversation memory | Redis (session) + PostgreSQL via platform API (long-term) |
| Vector search / RAG | pgvector via platform API, or dedicated vector store |
| Audio transcription | Whisper API (OpenAI) or Deepgram |
| Email integration | Microsoft Graph API / Google Workspace API via platform adapters |
| CRM integration | Platform integration adapters (Salesforce, Wealthbox, Redtail) |
| Document parsing | pdfplumber, pymupdf, LLM vision |
| Observability | Langfuse (token tracking, cost, latency per agent) |
| Task queue | Redis + ARQ (for async jobs: daily digest, transcription) |

### 3.3 Provider routing

```python
from pydantic_ai import Agent

# Primary provider with fallback chain
copilot_agent = Agent(
    model='anthropic:claude-sonnet-4-6',
    fallback_model='openai:gpt-4o',
)

# Cost-optimized for batch jobs (daily digest, transcription summary)
batch_agent = Agent(
    model='anthropic:claude-haiku-4-5',
    fallback_model='together:meta-llama/Llama-3.3-70B',
)

# High-capability for complex analysis (tax planning, firm-wide reporting)
analysis_agent = Agent(
    model='anthropic:claude-opus-4-6',
)
```

## 4. Feature Architecture

### Feature 1: Ask Hazel Anything — Firmwide Knowledge Assistant

**Endpoint:** `POST /ai/chat`

**What it does:**
Answers advisor questions by searching across all firm data — recorded conversations, emails, documents, CRM data, notes, tax returns, estate plans, market/regulatory info. Delivers tailored responses specific to the advisor's clients and prior interactions.

**Agent design:**

```python
class HazelCopilot(BaseModel):
    """Result type for copilot responses."""
    answer: str                          # Markdown response
    citations: list[Citation]            # Source references
    confidence: float
    as_of: str                           # Freshness timestamp
    recommended_actions: list[Action]    # Structured recommendations
    follow_up_questions: list[str]

class Citation(BaseModel):
    source_type: str                     # "document", "email", "crm_note", "meeting_transcript", "account_data"
    source_id: str
    title: str
    excerpt: str
    relevance_score: float

class Action(BaseModel):
    type: str                            # "CREATE_REBALANCE_PROPOSAL", "SCHEDULE_MEETING", "DRAFT_EMAIL", etc.
    target_id: str | None
    reason: str
```

**Tools (read-only):**

| Tool | Source | Description |
|------|--------|-------------|
| `search_documents` | Platform API | Full-text + vector search across uploaded documents, tax returns, estate plans |
| `search_emails` | Email adapter | Search advisor's email history by client, date, topic |
| `search_crm_notes` | CRM adapter | Search CRM notes, activities, tasks |
| `search_meeting_transcripts` | Platform API | Search past meeting transcripts and summaries |
| `get_household_summary` | Platform API | Household overview with accounts, AUM, performance |
| `get_account_summary` | Platform API | Account detail with holdings, activity, status |
| `get_transfer_case` | Platform API | Transfer status and history |
| `get_order_projection` | Platform API | Recent order/execution status |
| `get_client_timeline` | Platform API | Aggregated activity feed for a client |

**RAG pipeline:**
1. Advisor query → embedding via model
2. Vector search across document embeddings, email embeddings, CRM note embeddings, meeting transcript embeddings
3. Top-K results enriched with metadata (source, date, client association)
4. Results + live platform data fed to LLM as context
5. LLM generates answer with citations

**Conversation memory:** Redis-backed per advisor per conversation, 2-hour TTL, max 50 messages. System prompt rebuilt on each turn with fresh context.

---

### Feature 2: Daily Snapshot / Daily Digest

**Endpoint:** `POST /ai/digest/generate`

**What it does:**
Generates a personalized daily briefing for each advisor: upcoming meetings, pending tasks, priority emails, CRM updates, account alerts (drift, RMD deadlines, large cash movements), and suggested actions.

**Agent design:**

```python
class DailyDigest(BaseModel):
    advisor_id: str
    generated_at: str
    greeting: str
    sections: list[DigestSection]
    priority_items: list[PriorityItem]
    suggested_actions: list[Action]

class DigestSection(BaseModel):
    title: str                           # "Today's Meetings", "Pending Tasks", "Account Alerts"
    items: list[DigestItem]

class DigestItem(BaseModel):
    type: str                            # "meeting", "task", "email", "alert", "crm_update"
    title: str
    summary: str
    client_id: str | None
    urgency: str                         # "high", "medium", "low"
    action_url: str | None
```

**Execution:** Async job via ARQ, scheduled per advisor (default 6:00 AM local time). Pulls data from platform API + CRM + email, generates digest via batch agent (Haiku-tier for cost).

**Data sources:**
- Calendar (meetings today/tomorrow)
- Platform tasks and exceptions assigned to advisor
- Email inbox (unread priority messages)
- CRM activity feed (new notes, tasks due)
- Account alerts (drift breaches, RMD approaching, large pending transfers)

---

### Feature 3: Email Drafting in Advisor's Tone

**Endpoint:** `POST /ai/email/draft`

**What it does:**
Drafts client-facing emails that match each advisor's writing style. Uses the advisor's sent email history to learn tone, formality, signature patterns, and common phrases.

**Agent design:**

```python
class EmailDraftRequest(BaseModel):
    advisor_id: str
    client_id: str
    intent: str                          # "quarterly_review_followup", "account_update", "meeting_request", "custom"
    context: str                         # Advisor's notes on what to communicate
    reply_to_email_id: str | None        # If replying to an existing thread

class EmailDraft(BaseModel):
    subject: str
    body: str                            # HTML or plain text
    tone_confidence: float               # How well it matches advisor's style
    suggestions: list[str]               # Alternative phrasings
    warnings: list[str]                  # "Contains specific return numbers - verify before sending"
```

**Style learning:**
- On first use, index advisor's last 100 sent emails via embedding
- Extract style profile: formality level, greeting patterns, sign-off style, average length, vocabulary preferences
- Store style profile per advisor in Redis (refresh weekly)
- Inject style profile into system prompt when generating drafts

**Safety:** Drafts are returned to the advisor for review — never auto-sent. Warnings flag any specific numbers, compliance-sensitive language, or forward-looking statements.

---

### Feature 4: Task Extraction + CRM Sync Payloads

**Endpoint:** `POST /ai/tasks/extract` and `POST /ai/crm/sync`

**What it does:**
Extracts candidate tasks from meeting transcripts, email threads, and advisor notes. Generates CRM sync payloads for tasks, notes, and activities that the platform may execute against CRM systems.

**Agent design:**

```python
class ExtractedTask(BaseModel):
    title: str
    description: str
    assigned_to: str | None              # Advisor ID or name
    due_date: str | None
    priority: str
    client_id: str | None
    source_type: str                     # "meeting_transcript", "email", "note"
    source_id: str
    confidence: float

class CRMSyncPayload(BaseModel):
    provider: str                        # "salesforce", "wealthbox", "redtail"
    operation: str                       # "create_task", "create_note", "update_contact", "log_activity"
    data: dict
    idempotency_key: str
```

**CRM adapter pattern:** The sidecar generates extracted tasks and CRM sync payloads but does NOT call CRM APIs directly. Payloads are returned to the platform API server, which handles CRM integration through its own adapter framework. This preserves the sidecar's role as an intelligence layer rather than an integration system of record.

---

### Feature 5: Meeting Prep Assistant

**Endpoint:** `POST /ai/meetings/prep`

**What it does:**
Before each meeting, generates a prep packet: summary of past interactions, recent account activity, relevant documents, open action items, talking points, and suggested discussion topics.

**Agent design:**

```python
class MeetingPrepRequest(BaseModel):
    advisor_id: str
    client_id: str
    meeting_date: str
    meeting_type: str                    # "quarterly_review", "annual_review", "ad_hoc", "prospect"

class MeetingPrep(BaseModel):
    client_summary: str                  # 2-3 sentence overview
    relationship_history: str            # Key milestones, how long a client
    recent_activity: list[ActivityItem]  # Last 90 days
    account_snapshot: dict               # AUM, performance, allocation summary
    open_items: list[str]                # Pending tasks, unresolved issues
    past_meeting_highlights: list[str]   # Key points from last 2-3 meetings
    talking_points: list[TalkingPoint]
    suggested_topics: list[str]
    relevant_documents: list[Citation]
    warnings: list[str]                  # "RMD deadline approaching", "Large unrealized loss in XLE"
```

**Data sources:** Platform API (account data, transfer history, order history), CRM (notes, activities), email (recent correspondence), meeting transcripts (past meetings with this client), document vault (relevant documents).

---

### Feature 6: Meeting Transcription & Summaries

**Endpoint:** `POST /ai/meetings/transcribe` and `POST /ai/meetings/summarize`

**What it does:**
Processes meeting audio or transcript artifacts provided by platform clients, transcribes audio when needed, generates structured summaries with action items, and returns follow-up recommendations for the advisor workspace.

**Architecture:**

```text
Audio Input (browser/mobile)
    → captured by browser/mobile client
    → chunked upload to platform-managed object storage
    → async transcription job (Whisper/Deepgram)
    → transcript stored in platform document vault
    → summarization agent generates:
        - structured summary
        - action items with assignees
        - follow-up email drafts
        - CRM sync payloads
    → results returned to advisor workspace
```

**Agent design:**

```python
class MeetingSummary(BaseModel):
    meeting_id: str
    duration_minutes: int
    participants: list[str]
    executive_summary: str               # 3-5 sentences
    key_topics: list[TopicSection]
    action_items: list[ExtractedTask]
    follow_up_drafts: list[EmailDraft]   # Suggested follow-up emails
    client_sentiment: str | None         # "positive", "neutral", "concerned"
    next_steps: list[str]
    crm_sync_payloads: list[CRMSyncPayload]

class TopicSection(BaseModel):
    topic: str
    summary: str
    speaker_attribution: dict[str, str]  # Who said what (if diarization available)
    decisions_made: list[str]
```

**Transcription pipeline:**
1. Audio uploaded by a platform client to platform-managed object storage (chunked, max 2 hours)
2. ARQ job dispatched for transcription
3. Whisper API or Deepgram processes audio → raw transcript
4. Speaker diarization (if supported by provider)
5. Raw transcript stored in document vault
6. Summarization agent processes transcript → MeetingSummary
7. Action items and CRM payloads returned as recommendations

---

### Feature 7: Mobile Meeting Intelligence

Not a sidecar concern — this is a mobile app feature that uses the same transcription and summarization endpoints (Feature 6). The mobile app captures audio and uploads to the same pipeline.

---

### Feature 8: Email Triage & Reply Suggestions

**Endpoint:** `POST /ai/email/triage`

**What it does:**
Analyzes advisor inbox inputs, identifies priority messages, categorizes emails, and drafts thoughtful reply suggestions using firm data context.

**Agent design:**

```python
class EmailTriageRequest(BaseModel):
    advisor_id: str
    emails: list[IncomingEmail]          # Batch of new emails to triage

class IncomingEmail(BaseModel):
    email_id: str
    from_address: str
    subject: str
    body_preview: str                    # First 500 chars
    received_at: str
    thread_id: str | None
    has_attachments: bool

class TriagedEmail(BaseModel):
    email_id: str
    priority: str                        # "urgent", "high", "normal", "low", "informational"
    category: str                        # "client_request", "compliance", "prospect", "vendor", "internal", "newsletter"
    client_id: str | None                # Matched to a platform client if recognized
    summary: str                         # One-sentence summary
    suggested_action: str                # "reply_now", "reply_today", "delegate", "archive", "review_later"
    draft_reply: EmailDraft | None       # Auto-drafted reply for urgent/high
    reasoning: str                       # Why this priority/category
```

**Execution:** Can run as a scheduled job (every 15 min) or on-demand when advisor opens inbox view. Uses batch agent for cost efficiency. Mailbox connectivity and webhook delivery are owned by platform adapters, not by the sidecar itself.

**Client matching:** Cross-references sender email against platform client records and CRM contacts to associate emails with clients.

---

### Feature 9: SSO Integration

Not a sidecar concern — this is a platform identity feature (Epic 1). The sidecar benefits from SSO via the platform's auth layer.

---

### Feature 10: AI Tax Planning

**Endpoint:** `POST /ai/tax/plan`

**What it does:**
Analyzes tax documents (1040s, pay stubs, statements), custodial data, CRM notes, and emails to generate personalized tax plans with what-if scenario modeling.

**Agent design:**

```python
class TaxPlanRequest(BaseModel):
    client_id: str
    tax_year: int
    documents: list[str]                 # Document IDs (1040s, pay stubs, K-1s)
    include_scenarios: list[str]         # ["roth_conversion", "tax_loss_harvest", "charitable_giving", "rmd_strategy"]

class TaxPlan(BaseModel):
    client_id: str
    tax_year: int
    current_situation: TaxSituation
    opportunities: list[TaxOpportunity]
    scenarios: list[TaxScenario]
    warnings: list[str]
    disclaimer: str                      # Always present: "This is decision support, not tax advice"

class TaxSituation(BaseModel):
    filing_status: str
    estimated_income: float
    estimated_tax_bracket: float
    capital_gains_summary: dict
    rmd_status: dict | None
    loss_harvesting_potential: float

class TaxOpportunity(BaseModel):
    type: str                            # "tax_loss_harvest", "roth_conversion", "charitable_qcd", "gain_deferral"
    description: str
    estimated_impact: float              # Dollars saved/deferred
    confidence: str                      # "high", "medium", "low"
    action: Action                       # Structured recommendation
    assumptions: list[str]

class TaxScenario(BaseModel):
    name: str                            # "Harvest all losses", "Convert $50K to Roth"
    inputs: dict
    projected_tax_liability: float
    compared_to_baseline: float          # Delta
    trade_offs: list[str]
```

**Data pipeline:**
1. Extract data from uploaded tax documents (document extraction agent)
2. Pull custodial data from platform API (holdings, cost basis, realized gains)
3. Pull client financial profile (income, filing status, tax bracket)
4. Analysis agent computes opportunities and scenarios
5. What-if modeling: vary inputs (Roth conversion amount, harvest timing) and project outcomes

**Safety:** Every tax plan includes a disclaimer. Results are tagged as decision support. Specific dollar amounts reference their data source and freshness.

---

### Feature 11: Custodial Data Analytics

**Endpoint:** `POST /ai/portfolio/analyze`

**What it does:**
Provides near-real-time analysis of custodial or platform-exposed account data: concentration risk, sector exposure, RMD status, loss harvesting opportunities, beneficiary completeness, and balance trends.

**Agent design:**

```python
class PortfolioAnalysis(BaseModel):
    client_id: str
    as_of: str
    analyses: list[AnalysisResult]
    alerts: list[Alert]
    recommended_actions: list[Action]

class AnalysisResult(BaseModel):
    type: str                            # "concentration", "exposure", "rmd_status", "loss_harvest", "drift"
    title: str
    summary: str
    data: dict                           # Type-specific structured data
    severity: str                        # "info", "warning", "action_needed"

class Alert(BaseModel):
    type: str
    title: str
    description: str
    client_id: str
    account_id: str | None
    urgency: str
```

**Analysis types:**
- **Concentration risk** — single stock >10%, sector >30%, geographic overweight
- **Sector/asset class exposure** — vs benchmark, vs model target
- **RMD status** — clients approaching 73, calculated RMD amounts, deadline tracking
- **Loss harvesting** — unrealized losses above threshold, replacement candidates, wash sale windows
- **Beneficiary audit** — accounts missing beneficiaries, outdated designations
- **Cash drag** — excessive uninvested cash across accounts

**Boundary note:** The sidecar does not own custodial integrations themselves. It consumes approved platform reads or integration projections and computes analytics on top of them.

---

### Feature 12: Firm-Wide Analytical Reporting

**Endpoint:** `POST /ai/reports/firm-wide`

**What it does:**
Automated firm-level reporting: clients behind on RMDs, unrealized loss opportunities across all accounts, concentration risk heatmap, billing anomalies, stale documents — with interactive AI-powered analysis.

**Agent design:**

```python
class FirmWideReport(BaseModel):
    firm_id: str
    generated_at: str
    report_type: str                     # "rmd_audit", "loss_harvest_sweep", "concentration_scan", "compliance_review"
    summary: str
    sections: list[ReportSection]
    flagged_items: list[FlaggedItem]
    total_opportunity: float | None      # Dollar value of identified opportunities

class FlaggedItem(BaseModel):
    client_id: str
    client_name: str
    account_id: str | None
    issue: str
    severity: str
    recommended_action: Action
    estimated_impact: float | None
```

**Execution:** Async job via ARQ. Scans all clients/accounts in the tenant, runs analysis agents per account, aggregates results into firm-level report. Stored as report artifact in platform.

**Boundary note:** The sidecar owns the analytical scan, summarization, and explanation. The platform owns report definitions, report publication, access control, and durable report artifact storage.

---

## 5. Expanded Project Structure

```text
sidecar/
├── app/
│   ├── main.py                          # FastAPI app, lifespan, middleware
│   ├── config.py                        # Pydantic Settings
│   ├── dependencies.py                  # DI container
│   ├── middleware/
│   │   ├── tenant.py                    # X-Tenant-ID / X-Actor-ID extraction
│   │   ├── request_id.py
│   │   └── logging.py
│   ├── routers/
│   │   ├── chat.py                      # Feature 1: Ask Hazel Anything
│   │   ├── digest.py                    # Feature 2: Daily Digest
│   │   ├── email.py                     # Feature 3: Email Draft + Feature 8: Triage
│   │   ├── tasks.py                     # Feature 4: Task Extraction
│   │   ├── meetings.py                  # Feature 5: Meeting Prep + Feature 6: Transcription
│   │   ├── tax.py                       # Feature 10: Tax Planning
│   │   ├── portfolio.py                 # Feature 11: Portfolio Analytics
│   │   ├── reports.py                   # Feature 12: Firm-Wide Reports + Narratives
│   │   ├── documents.py                 # Document classify/extract
│   │   └── health.py
│   ├── agents/
│   │   ├── copilot.py                   # Hazel copilot agent (Pydantic AI)
│   │   ├── digest.py                    # Daily digest agent
│   │   ├── email_drafter.py             # Email drafting agent
│   │   ├── email_triager.py             # Email triage agent
│   │   ├── task_extractor.py            # Task extraction agent
│   │   ├── meeting_prep.py              # Meeting prep agent
│   │   ├── meeting_summarizer.py        # Transcript summarization agent
│   │   ├── tax_planner.py              # Tax planning agent
│   │   ├── portfolio_analyst.py         # Portfolio analytics agent
│   │   ├── firm_reporter.py             # Firm-wide reporting agent
│   │   ├── doc_classifier.py            # Document classification agent
│   │   └── doc_extractor.py             # Document field extraction agent
│   ├── tools/
│   │   ├── platform.py                  # Read-only platform API tools
│   │   ├── search.py                    # RAG / vector search tools
│   │   ├── email_adapter.py             # Email read tools (Graph API / Gmail)
│   │   ├── crm_adapter.py              # CRM read tools
│   │   ├── calendar_adapter.py          # Calendar read tools
│   │   └── transcription.py             # Whisper/Deepgram tools
│   ├── rag/
│   │   ├── embeddings.py                # Embedding generation
│   │   ├── indexer.py                   # Document/email/note indexing
│   │   ├── retriever.py                 # Vector search retrieval
│   │   └── chunker.py                   # Text chunking strategies
│   ├── jobs/
│   │   ├── worker.py                    # ARQ worker entry point
│   │   ├── daily_digest.py              # Scheduled digest generation
│   │   ├── email_triage.py              # Scheduled inbox triage
│   │   ├── transcription.py             # Async transcription job
│   │   ├── firm_report.py               # Async firm-wide report generation
│   │   └── style_profile.py             # Advisor email style learning
│   ├── models/
│   │   ├── schemas.py                   # Shared Pydantic models
│   │   ├── prompts.py                   # System prompt templates
│   │   └── enums.py
│   ├── services/
│   │   ├── platform_client.py           # Narrow typed platform API client
│   │   ├── llm_client.py                # Pydantic AI provider config
│   │   └── token_tracker.py             # Langfuse integration
│   └── utils/
│       ├── redis.py
│       ├── errors.py
│       └── freshness.py                 # as_of metadata helpers
├── tests/
└── pyproject.toml
```

## 6. Endpoint Surface

| Endpoint | Feature | Method |
|----------|---------|--------|
| `POST /ai/chat` | Ask Hazel Anything | Copilot |
| `POST /ai/chat/stream` | Ask Hazel Anything (SSE) | Copilot |
| `POST /ai/digest/generate` | Daily Digest | Batch job |
| `GET /ai/digest/latest` | Get latest digest | Read |
| `POST /ai/email/draft` | Email Drafting | Agent |
| `POST /ai/email/triage` | Email Triage | Agent |
| `POST /ai/tasks/extract` | Task Extraction | Agent |
| `POST /ai/crm/sync-payload` | CRM Sync Payload Generation (returns payload, doesn't execute) | Agent |
| `POST /ai/meetings/prep` | Meeting Prep | Agent |
| `POST /ai/meetings/transcribe` | Meeting Transcription (async, returns 202) | Job |
| `POST /ai/meetings/summarize` | Meeting Summary | Agent |
| `GET /ai/meetings/:id/summary` | Get meeting summary | Read |
| `POST /ai/tax/plan` | Tax Planning | Agent |
| `POST /ai/portfolio/analyze` | Custodial Data Analytics | Agent |
| `POST /ai/reports/firm-wide` | Firm-Wide Analytical Report (async, returns 202) | Job |
| `POST /ai/reports/narrative` | Report Narrative | Agent |
| `POST /ai/documents/classify` | Document Classification | Agent |
| `POST /ai/documents/extract` | Document Extraction | Agent |
| `GET /health` | Health check | Infra |
| `GET /ready` | Readiness check | Infra |

## 7. Agent Tier Model

Not all features need the same model capability or cost profile.

| Tier | Model | Use Cases | Cost Profile |
|------|-------|-----------|-------------|
| **Copilot** | Claude Sonnet 4.6 (fallback: GPT-4o) | Chat, meeting prep, portfolio analysis, tax planning | Medium — interactive latency |
| **Batch** | Claude Haiku 4.5 (fallback: Llama 3.3 70B) | Daily digest, email triage, task extraction, CRM sync | Low — throughput optimized |
| **Analysis** | Claude Opus 4.6 | Firm-wide reports, complex tax scenarios, multi-document reasoning | High — accuracy critical |
| **Extraction** | Claude Haiku 4.5 | Document classification, field extraction, email categorization | Low — high volume |
| **Transcription** | Whisper large-v3 / Deepgram Nova-3 | Meeting audio → text | Specialized — audio model |

## 8. Tool Safety Model

### 8.1 Read-only tool allowlist

Every Pydantic AI agent declares its tools explicitly. Only read operations are permitted:

```python
copilot = Agent(
    model='anthropic:claude-sonnet-4-6',
    result_type=HazelCopilot,
    tools=[
        search_documents,           # Vector search
        search_emails,              # Email history search
        search_crm_notes,           # CRM search
        search_meeting_transcripts, # Transcript search
        get_household_summary,      # Platform read
        get_account_summary,        # Platform read
        get_transfer_case,          # Platform read
        get_order_projection,       # Platform read
        get_client_timeline,        # Platform read
        get_report_snapshot,        # Platform read
    ],
)
```

### 8.2 Explicitly forbidden tools

No agent may have tools that:

- Submit orders to OMS
- Initiate transfers
- Approve onboarding cases
- Post billing
- Modify workflow state
- Send emails (draft only)
- Create CRM records directly (generate sync payloads only)
- Publish report artifacts directly
- Persist authoritative meeting records outside approved platform APIs
- Maintain direct long-lived connectivity to mailbox, CRM, or custodial systems for write execution

### 8.3 Output safety

All agent outputs that contain financial numbers must include:

- `as_of` timestamp
- `source` reference
- `confidence` score where applicable
- Disclaimer text for tax and compliance-adjacent content

## 9. Isolation And Access Scoping

The sidecar must preserve data silos across firms and respect advisor-level visibility within a firm.

### 9.1 Isolation model

There are two layers of scope:

1. tenant isolation
2. actor access scope

Tenant isolation is mandatory in every storage and retrieval path. Data from one tenant must never be retrievable in another tenant's context.

Actor access scope is applied within a tenant. If the platform allows only certain advisors or roles to view certain households, clients, accounts, emails, notes, or documents, the sidecar must receive and honor that scope. The sidecar does not decide these permissions independently. The platform provides the scope, and the sidecar enforces it in retrieval.

### 9.2 Required request context

Every sidecar request must include at minimum:

- `tenant_id`
- `actor_id`
- `actor_type`
- `request_id`
- `conversation_id` when applicable
- `access_scope`

The `access_scope` should be explicit and structured, for example:

```json
{
  "visibility_mode": "full_tenant" ,
  "household_ids": ["hh_123", "hh_456"],
  "client_ids": ["cl_123"],
  "account_ids": ["acc_123", "acc_456"],
  "document_ids": [],
  "advisor_ids": ["adv_123"]
}
```

If the platform has full-firm admins, it may send a broad tenant scope. If the platform enforces advisor-scoped visibility, it should send only the allowed resource set or a constrained scope token that resolves to that set.

### 9.3 Retrieval-time enforcement

Filtering must happen before context reaches the model.

Allowed:

- tenant-scoped vector search
- tenant + advisor/client/household/account/document filtered search
- platform read methods that already enforce actor scope

Not allowed:

- retrieving broad tenant data and asking the model to ignore unauthorized records
- retrieving all matching chunks and filtering only after reranking
- relying on prompt instructions as the primary isolation mechanism

The LLM should only ever see context that has already passed retrieval-time scope checks.

### 9.4 Document and communication visibility

Every indexed or cached artifact should carry enough metadata to enforce visibility. At minimum:

- `tenant_id`
- `source_type`
- `source_id`
- `household_id` when applicable
- `client_id` when applicable
- `account_id` when applicable
- `advisor_id` when applicable
- `visibility_tags`
- `created_at`

Examples:

- a tax return linked to one client should be retrievable only for actors who can access that client
- a meeting transcript tied to one household should not appear in search for unrelated households
- an advisor's email history should not be mixed into another advisor's style profile or retrieval context unless the platform explicitly allows shared visibility

### 9.5 Conversation and cache isolation

Conversation memory, digest caches, style profiles, and intermediate enriched reads must be scoped at least by tenant and actor.

Recommended cache key patterns:

- `chat:{tenant_id}:{actor_id}:{conversation_id}`
- `digest:{tenant_id}:{actor_id}:{date}`
- `style_profile:{tenant_id}:{actor_id}`
- `retrieval:{tenant_id}:{actor_id}:{hash}`

No cache entry should be reusable across tenants. Advisor-specific caches should not be shared across advisors unless the data is explicitly tenant-wide and non-sensitive.

### 9.6 Safe retrieval flow

The safe flow is:

1. platform authenticates the user
2. platform resolves tenant and actor permissions
3. platform computes or resolves allowed access scope
4. sidecar applies scope filters during retrieval
5. only authorized chunks and records are passed to the model
6. citations in the response reference only authorized sources

This prevents cross-advisor or cross-client leakage even when embeddings, caches, and async jobs exist in the same service.

## 10. RAG Architecture

For "Ask Hazel Anything" to work across all firm data, the sidecar needs a retrieval-augmented generation pipeline.

### 10.1 What gets indexed

| Source | Indexing Trigger | Embedding Model |
|--------|-----------------|-----------------|
| Uploaded documents | On upload (via platform event) | text-embedding-3-small |
| Email history | On sync (periodic or webhook) | text-embedding-3-small |
| CRM notes | On sync | text-embedding-3-small |
| Meeting transcripts | After transcription completes | text-embedding-3-small |
| Client activity feed | On event (trade, transfer, etc.) | text-embedding-3-small |

### 10.2 Index structure

Each indexed chunk stores:

- `chunk_id`
- `tenant_id` (hard isolation)
- `source_type` (document, email, crm_note, transcript, activity)
- `source_id`
- `household_id` (if associated)
- `client_id` (if associated)
- `account_id` (if associated)
- `advisor_id` (if associated)
- `visibility_tags`
- `text` (chunk content)
- `embedding` (vector)
- `created_at`
- `metadata` (title, sender, date, etc.)

### 10.3 Search flow

```text
query + access_scope → embed → vector search (top 20, tenant + scope filtered)
     → rerank by relevance + recency + client association within allowed scope
     → top 5-8 chunks fed to LLM as context
     → LLM generates answer with citations
```

## 11. Async Jobs

Several features run as background jobs, not synchronous request handlers.

| Job | Trigger | Frequency | Agent Tier |
|-----|---------|-----------|-----------|
| Daily digest | Cron | Per advisor, daily at configured time | Batch |
| Email triage | Cron or webhook-fed email batch | Every 15 min or on new email batch | Batch |
| Transcription | Event (audio uploaded by platform client) | On demand | Transcription |
| Meeting summary | Event (transcription complete) | On demand | Copilot |
| Firm-wide analytical report | API request | On demand (async, returns 202) | Analysis |
| Style profile refresh | Cron | Per advisor, weekly | Batch |
| RAG index update | Event (new document/email/note) | On event | Extraction |

Worker process: ARQ (async Redis queue) running in a separate process from the FastAPI app.

## 12. Platform Client — Narrow, Typed, Read-Only

```python
class PlatformClient:
    """Narrow typed client for platform API reads. Single approved data access path."""

    async def get_household_summary(self, household_id: str, access_scope: AccessScope) -> HouseholdSummary: ...
    async def get_account_summary(self, account_id: str, access_scope: AccessScope) -> AccountSummary: ...
    async def get_client_profile(self, client_id: str, access_scope: AccessScope) -> ClientProfile: ...
    async def get_transfer_case(self, transfer_id: str, access_scope: AccessScope) -> TransferCase: ...
    async def get_order_projection(self, order_id: str, access_scope: AccessScope) -> OrderProjection: ...
    async def get_execution_projection(self, execution_id: str, access_scope: AccessScope) -> ExecutionProjection: ...
    async def get_report_snapshot(self, report_id: str, access_scope: AccessScope) -> ReportSnapshot: ...
    async def get_document_metadata(self, document_id: str, access_scope: AccessScope) -> DocumentMetadata: ...
    async def get_client_timeline(self, client_id: str, access_scope: AccessScope, days: int = 90) -> list[TimelineEvent]: ...
    async def get_advisor_clients(self, advisor_id: str, access_scope: AccessScope) -> list[ClientSummary]: ...
    async def get_firm_accounts(self, filters: dict, access_scope: AccessScope) -> list[AccountSummary]: ...
    async def search_documents_text(self, query: str, filters: dict, access_scope: AccessScope) -> list[DocumentMatch]: ...
```

No mutation methods. No generic data fetch. Each method is typed and bounded.

The narrow client is intentional. It prevents the sidecar from becoming an unbounded hidden backend with implicit ownership over operational domains. If a capability needs new context, the platform should expose a new explicit read method rather than letting the sidecar roam through internal systems.

## 13. Observability

Track via Langfuse:

- Token usage per agent, per tenant, per advisor
- Cost per request, per agent type, per day
- Latency percentiles per endpoint
- Tool call frequency and latency
- RAG retrieval quality (relevance scores)
- Cache hit rates
- Failure classification (LLM error, platform read error, transcription error)

## 14. Reliability

The platform must work without the sidecar. If Hazel is down:

- Onboarding, transfers, trading, billing all continue
- Advisors lose: copilot chat, daily digest, email drafting, meeting transcription, tax planning, firm reports
- These are productivity features, not operational dependencies

This boundary is necessary because the platform is still responsible for correctness, permissions, workflow control, and durable records even when AI features are unavailable.

## 15. Dependencies

```
# pyproject.toml core dependencies
pydantic-ai>=0.1
fastapi>=0.115
uvicorn[standard]>=0.34
pydantic>=2.10
pydantic-settings>=2.7
httpx>=0.28
redis[hiredis]>=5.2
arq>=0.26                    # Async job queue
langfuse>=3.0                # Observability
pdfplumber>=0.11             # PDF parsing
pymupdf>=1.25                # PDF parsing
numpy>=2.2
scipy>=1.15
```

## 16. Key Design Decisions

1. **Pydantic AI over LangChain/LangGraph** — type-safe structured output, provider flexibility, clean DI for tools, testable agents without LLM calls.

2. **One agent per feature, not one mega-agent** — each Hazel capability is a separate Pydantic AI agent with its own tool set, result type, and model tier. Easier to test, deploy, and cost-optimize independently.

3. **RAG for cross-data search** — "Ask Hazel Anything" requires searching across documents, emails, CRM, and transcripts. Vector embeddings with tenant-scoped isolation.

4. **Sync payloads, not direct mutations** — CRM sync, email sending, and task creation all return structured payloads that the platform API server executes after advisor confirmation.

5. **ARQ for async jobs** — daily digest, transcription, firm reports run in a separate worker process, not blocking the FastAPI event loop.

6. **Three-tier model routing** — Copilot (Sonnet), Batch (Haiku), Analysis (Opus) based on latency/cost/accuracy trade-offs per feature.

## 17. Boundary Rationale

The sidecar is designed this way because these features mix two very different responsibilities:

1. intelligence work
2. operational system ownership

The intelligence work includes summarization, drafting, extraction, ranking, scenario modeling, and explanation. Those tasks fit the sidecar well because they benefit from flexible prompting, model routing, async enrichment, and rapid iteration.

Operational ownership is different. It includes permissions, workflow transitions, durable evidence, integration correctness, external API retries, auditability, and source-of-truth storage. Those responsibilities belong in the platform core because they must remain deterministic, governable, and available even if the AI layer fails.

That is why the spec draws these boundaries:

- email triage belongs in the sidecar, but mailbox connectivity and sending do not
- CRM sync payload generation belongs in the sidecar, but CRM writes do not
- transcription and meeting summarization belong in the sidecar, but capture UX and authoritative transcript storage do not
- report narratives and firm-wide analytical scans belong in the sidecar, but report publication and official report records do not
- portfolio and tax analysis belong in the sidecar, but trading, transfer execution, approvals, and tax authority do not

This structure keeps the sidecar useful without letting it become a second uncontrolled backend. It also makes failures safer: if the sidecar is degraded, advisors lose assistive features, but the wealth platform still operates correctly.
