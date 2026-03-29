# Implementation Context: Agents and Tools

## Chosen Approach

Approach A: Bottom-Up by Layer. Build models → deps → tools → services → agents → registry → memory → router.

## Implementation Order

### Step 1: Result Type Models
- **Files:** `src/app/models/schemas.py` (new)
- **What:** All Pydantic result types: ChatRequest, Citation, Action, HazelCopilot, DigestItem, DigestSection, PriorityItem, DailyDigest, EmailDraft, TriagedEmail, ExtractedTask, MeetingPrep, TopicSection, CRMSyncPayload, MeetingSummary, TaxSituation, TaxOpportunity, TaxScenario, TaxPlan, PortfolioAnalysis, FirmWideReport, DocClassification, DocExtraction.
- **Verify:** All models instantiate with valid data.

### Step 2: Base Agent Dependencies
- **Files:** `src/app/agents/base_deps.py` (new)
- **What:** `AgentDeps` dataclass with platform, access_scope, tenant_id, actor_id. This is the simpler base that tools type against.
- **Verify:** Imports cleanly, doesn't conflict with existing deps.py.

### Step 3: Platform Client Read Methods
- **Files:** `src/app/services/platform_client.py` (edit)
- **What:** Add stub read methods: get_household_summary, get_account_summary, get_client_profile, get_client_timeline, get_advisor_clients, search_documents_text, get_transfer_case, get_order_projection, get_report_snapshot. All return stub data or empty results.
- **Verify:** Methods callable, return correct types.

### Step 4: Tool Modules
- **Files:** `src/app/tools/__init__.py` (new), `src/app/tools/platform.py` (new), `src/app/tools/search.py` (new), `src/app/tools/calendar_adapter.py` (new), `src/app/tools/email_adapter.py` (new), `src/app/tools/crm_adapter.py` (new)
- **What:** Tool functions with `RunContext[AgentDeps]` signatures. Platform tools delegate to ctx.deps.platform. Search tools delegate to ctx.deps.platform search methods. Adapter stubs return empty lists.
- **Verify:** All tool functions importable, typed correctly.

### Step 5: LLM Client
- **Files:** `src/app/services/llm_client.py` (new)
- **What:** ModelTier frozen dataclass, TIERS dict, run_with_fallback_chain() function.
- **Verify:** TIERS has 5 entries, ModelTier instantiates.

### Step 6: Agent Registry
- **Files:** `src/app/agents/registry.py` (new)
- **What:** AgentEntry dataclass, AgentRegistry class with register/get/list_agents. Module-level singleton.
- **Verify:** Register and retrieve works.

### Step 7: All 12 Agent Modules
- **Files:** `src/app/agents/copilot.py`, `digest.py`, `email_drafter.py`, `email_triager.py`, `task_extractor.py`, `meeting_prep.py`, `meeting_summarizer.py`, `tax_planner.py`, `portfolio_analyst.py`, `firm_reporter.py`, `doc_classifier.py`, `doc_extractor.py` (all new)
- **What:** Each follows the pattern: optional extended deps class → Agent(model, result_type, tools, fallback_model, retries) → @agent.system_prompt → registry.register(). Full system prompts and tool assignments for all 12.
- **Verify:** All agents register successfully, correct tiers.

### Step 8: Agent Init
- **Files:** `src/app/agents/__init__.py` (rewrite)
- **What:** Import all 12 agent modules to trigger registration. Export registry.
- **Verify:** `from app.agents import registry` works, registry has 12 entries.

### Step 9: Message Codec
- **Files:** `src/app/services/message_codec.py` (new)
- **What:** serialize_message, deserialize_message, serialize_part, deserialize_part, trim_message_history, extract_active_client_id, extract_active_household_id, helper functions.
- **Verify:** Round-trip serialize/deserialize preserves message structure.

### Step 10: Conversation Memory
- **Files:** `src/app/services/conversation_memory.py` (new)
- **What:** ConversationMemory class with load, save, load_state, clear. Redis-backed with tenant-scoped keys.
- **Verify:** Save and load round-trip works with fakeredis.

### Step 11: DI Extensions
- **Files:** `src/app/dependencies.py` (edit)
- **What:** Add get_agent_deps() and get_conversation_memory() dependency callables.
- **Verify:** Both callables importable.

### Step 12: Chat Router Expansion
- **Files:** `src/app/routers/chat.py` (rewrite)
- **What:** POST /chat with conversation memory integration, POST /chat/stream with SSE streaming.
- **Verify:** Endpoint responds with structured output.

### Step 13: Update Dependencies
- **Files:** `pyproject.toml` (edit)
- **What:** Add fakeredis to dev dependencies.
- **Verify:** uv sync succeeds.

### Step 14: Tests
- **Files:** `tests/test_registry.py`, `tests/test_schemas.py`, `tests/test_tool_safety.py`, `tests/test_message_codec.py`, `tests/test_conversation_memory.py`, `tests/test_llm_client.py` (all new)
- **What:** Tests for all new functionality. Use TestModel for agent tests, fakeredis for memory tests.
- **Verify:** All tests pass.

## External Dependencies

| Package | Purpose | Already in pyproject.toml? |
|---|---|---|
| pydantic-ai | Agent framework | Yes |
| redis | Conversation memory | Yes |
| httpx | Platform client | Yes |
| fakeredis | Test Redis | No — add to dev deps |

## Test Cases

- **T1:** AgentRegistry register and get returns correct AgentEntry
- **T2:** AgentRegistry.get raises KeyError for unknown agent
- **T3:** Registry has exactly 12 agents after all imports
- **T4:** HazelCopilot model validates with all required fields
- **T5:** DailyDigest model validates with sections and priority items
- **T6:** TaxPlan model includes default disclaimer
- **T7:** No tool file calls mutation methods (AST check)
- **T8:** No tool file makes direct httpx calls
- **T9:** Message serialize/deserialize round-trip preserves structure
- **T10:** trim_message_history caps at max_messages
- **T11:** trim_message_history preserves leading system prompt
- **T12:** ConversationMemory save and load round-trip
- **T13:** ConversationMemory tenant isolation
- **T14:** ModelTier TIERS dict has 5 entries with correct models
- **T15:** ChatRequest model validates with required fields

## Scope Boundaries

### In scope
- All files under `src/app/agents/` (new + edit)
- All files under `src/app/tools/` (new)
- `src/app/models/schemas.py` (new)
- `src/app/services/llm_client.py`, `conversation_memory.py`, `message_codec.py` (new)
- `src/app/services/platform_client.py` (edit — add read method stubs)
- `src/app/dependencies.py` (edit — add 2 new callables)
- `src/app/routers/chat.py` (rewrite)
- `pyproject.toml` (edit — add fakeredis)
- All new test files

### Out of scope
- Full PlatformClient method implementations (stubs only)
- Actual LLM calls (TestModel for tests)
- Full RAG/vector search implementation
- Other router implementations beyond chat (digest, email, etc.)
- Docker/deployment changes
- Langfuse instrumentation
