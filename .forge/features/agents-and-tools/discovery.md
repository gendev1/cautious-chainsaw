# Discovery: Agents and Tools

## Requirements

- **R1: Agent Registry** — `app/agents/registry.py` with `AgentEntry` dataclass (name, agent, tier, description) and `AgentRegistry` class (register, get, list_agents). Module-level singleton `registry`.
- **R2: Agent `__init__`** — `app/agents/__init__.py` imports all 12 agent modules to trigger registration, exports `registry`.
- **R3: Base Dependencies** — `app/agents/base_deps.py` with `AgentDeps` dataclass holding `platform: PlatformClient`, `access_scope: AccessScope`, `tenant_id: str`, `actor_id: str`. Replaces existing `app/agents/deps.py`.
- **R4: 12 Agent Modules** — Each agent follows the pattern: dependency class → `Agent(model=..., result_type=..., tools=[...])` → `@agent.system_prompt` → `registry.register()`. Agents: copilot, digest, email_drafter, email_triager, task_extractor, meeting_prep, meeting_summarizer, tax_planner, portfolio_analyst, firm_reporter, doc_classifier, doc_extractor.
- **R5: Model Tier Definitions** — `app/services/llm_client.py` with `ModelTier` frozen dataclass and `TIERS` dict (copilot, batch, analysis, extraction, transcription). Plus `run_with_fallback_chain()` for multi-level fallback.
- **R6: Tool Modules** — `app/tools/platform.py` with platform read tools (get_household_summary, get_account_summary, get_transfer_case, get_order_projection, get_client_timeline, get_report_snapshot, get_advisor_clients). `app/tools/search.py` with search tools (search_documents, search_emails, search_crm_notes, search_meeting_transcripts). Adapter stubs for calendar, email, CRM.
- **R7: Tool Models** — Pydantic models for tool return types: HouseholdSummary, AccountBrief, ClientBrief, AccountSummary, DocumentMatch, etc.
- **R8: Structured Output Result Types** — `app/models/schemas.py` with all result models: HazelCopilot (Citation, Action), DailyDigest (DigestItem, DigestSection, PriorityItem), TaxPlan (TaxSituation, TaxOpportunity, TaxScenario), MeetingSummary (TopicSection), EmailDraft, TriagedEmail, ExtractedTask, MeetingPrep, PortfolioAnalysis, FirmWideReport, DocClassification, DocExtraction, CRMSyncPayload, ChatRequest.
- **R9: Conversation Memory** — `app/services/conversation_memory.py` with Redis-backed `ConversationMemory` class (load, save, load_state, clear). Keys scoped by tenant/actor/conversation_id. 2-hour TTL, 50-message cap.
- **R10: Message Codec** — `app/services/message_codec.py` with serialize/deserialize for Pydantic AI `ModelMessage`, `ModelRequest`, `ModelResponse` and their parts. Plus `trim_message_history()`, `extract_active_client_id()`, `extract_active_household_id()`.
- **R11: Chat Router with Memory** — Expand `app/routers/chat.py` with `POST /chat` and `POST /chat/stream` using conversation memory, agent registry lookup, and SSE streaming.
- **R12: Tool Safety Tests** — `tests/test_tool_safety.py` with AST-based static analysis checking for forbidden mutation method prefixes and direct httpx calls in tool files.
- **R13: Agent Testing Infrastructure** — Tests using `TestModel` and `FunctionModel` from Pydantic AI, mock `PlatformClient` fixture in `conftest.py`, direct tool function testing with mock `RunContext`.
- **R14: DI Extension** — Add `get_agent_deps` and `get_conversation_memory` to `app/dependencies.py`.

## Decisions Already Made

- **D1:** Pydantic AI is the agent framework. All agents use `Agent(model=..., result_type=..., tools=[...])`.
- **D2:** 12 agents across 4 model tiers (copilot, batch, analysis, extraction) plus transcription.
- **D3:** Tools receive deps via `RunContext[AgentDeps]` — Pydantic AI's built-in dependency injection.
- **D4:** All tools are read-only. No mutation methods on PlatformClient.
- **D5:** Conversation memory stores full Pydantic AI message lists (including tool calls/results), not simplified transcripts.
- **D6:** System prompts are rebuilt on every turn via `@agent.system_prompt` decorator.
- **D7:** The existing `app/agents/deps.py` will be replaced by `app/agents/base_deps.py` with a simpler shape (no redis, no retriever — those are accessed through platform and tools instead).

## Constraints

- **C1:** Builds on core infrastructure from spec 01. Must not break existing tests.
- **C2:** The existing `app/agents/deps.py` (from core infra) has a different shape than the spec's `base_deps.py`. Need to migrate references.
- **C3:** Tool functions call platform client methods that are stubs. Tools themselves will be functional stubs that delegate to the stub platform client.
- **C4:** Agent modules import tool functions that don't exist yet — must create tools before agents.
- **C5:** The spec references `ChatRequest` model with `message`, `conversation_id`, `client_id`, `household_id`, and `history` fields.
- **C6:** `fakeredis` needed as a dev dependency for conversation memory tests.

## Open Questions

- [x] **Q1:** Should all 12 agents have full system prompts and tool lists as shown in the spec, or should some be minimal stubs? **Answer: All 12 agents fully implemented (Option B) — extrapolate system prompts and tool assignments for the 10 agents the spec doesn't fully detail.**
- [x] **Q2:** The existing `app/agents/deps.py` (from core infra) references `redis` and `retriever`. Should it be deleted and replaced by `base_deps.py`? **Answer: Keep both. deps.py stays as the DI bridge (FastAPI → agents). Add base_deps.py as the simpler base that tools type against. Agent-specific deps (e.g. CopilotDeps) extend base_deps.py.**
- [x] **Q3:** Should `fakeredis` be added to dev dependencies? **Answer: Yes.**
- [x] **Q4:** The spec shows many Pydantic models for tool return types in `app/tools/platform.py` and result types in `app/models/schemas.py`. Should all models be created even if their agent is a stub? **Answer: Yes, all models should be created as they define the API contract.**
