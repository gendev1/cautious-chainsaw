# Exploration: Agents and Tools

## Most Similar Feature

No existing agent implementation to reference — the codebase has only stubs. The closest reference is the `app/agents/deps.py` AgentDeps dataclass pattern (frozen dataclass with properties).

**What to reuse:** Frozen dataclass pattern for deps, existing DI wiring in dependencies.py, router stub pattern.
**What to extend:** PlatformClient needs read methods, agents/__init__.py needs registry imports, router stubs need endpoint implementations.

## Architecture Map

```
Request → Router → Registry.get(agent_name) → Agent.run(prompt, deps, history)
                        │                              │
                        │                     ┌────────┤
                        │                     ↓        ↓
                        │              System Prompt   Tools (via RunContext)
                        │                              │
                        │                     ┌────────┤
                        │                     ↓        ↓
                        │              Platform reads  Search (RAG)
                        │
                  ConversationMemory ← Redis (load/save history)

Agent modules register at import time → registry singleton
Tools receive AgentDeps via RunContext[AgentDeps] → read-only platform access
```

## Structural Patterns

### Frozen dataclass for deps [grep-fallback]
- `@dataclass(frozen=True, slots=True)` with typed fields
- Example: `src/app/agents/deps.py` lines 15-35
- Match count: 2 (AgentDeps, RequestContext) [insufficient-sample: 2 matches]

### Router stub pattern [grep-fallback]
- `router = APIRouter(tags=[...])` with no endpoints
- Example: `src/app/routers/chat.py` (4 lines)
- Match count: 9 [sufficient sample]

### Settings access via get_settings() [grep-fallback]
- `@lru_cache(maxsize=1)` singleton
- Example: `src/app/config.py` line 112
- Match count: 1 [insufficient-sample]

## Key Files

### Reference reading
- `src/app/agents/deps.py` — existing AgentDeps shape (keep, add base_deps alongside)
- `src/app/dependencies.py` — DI wiring (needs get_agent_deps, get_conversation_memory)
- `src/app/services/platform_client.py` — stub (needs read methods for tools)
- `src/app/models/access_scope.py` — existing model (tools depend on this)
- `src/app/config.py` — model tier settings already defined

### Expected new files
- `src/app/agents/registry.py` — AgentRegistry class
- `src/app/agents/base_deps.py` — simpler base deps for tools
- `src/app/agents/copilot.py` — copilot agent
- `src/app/agents/digest.py` — digest agent
- `src/app/agents/email_drafter.py` — email drafter agent
- `src/app/agents/email_triager.py` — email triager agent
- `src/app/agents/task_extractor.py` — task extractor agent
- `src/app/agents/meeting_prep.py` — meeting prep agent
- `src/app/agents/meeting_summarizer.py` — meeting summarizer agent
- `src/app/agents/tax_planner.py` — tax planner agent
- `src/app/agents/portfolio_analyst.py` — portfolio analyst agent
- `src/app/agents/firm_reporter.py` — firm reporter agent
- `src/app/agents/doc_classifier.py` — document classifier agent
- `src/app/agents/doc_extractor.py` — document extractor agent
- `src/app/tools/__init__.py` — tools package
- `src/app/tools/platform.py` — platform read tools
- `src/app/tools/search.py` — search tools
- `src/app/tools/calendar_adapter.py` — calendar adapter stub
- `src/app/tools/email_adapter.py` — email adapter stub
- `src/app/tools/crm_adapter.py` — CRM adapter stub
- `src/app/models/schemas.py` — all result types and request models
- `src/app/services/llm_client.py` — model tier definitions + fallback chain
- `src/app/services/conversation_memory.py` — Redis-backed conversation memory
- `src/app/services/message_codec.py` — Pydantic AI message serialization

### Expected edits
- `src/app/agents/__init__.py` — import all agent modules, export registry
- `src/app/routers/chat.py` — implement POST /chat and POST /chat/stream
- `src/app/dependencies.py` — add get_agent_deps, get_conversation_memory
- `src/app/services/platform_client.py` — add read method stubs

### Expected tests
- `tests/test_registry.py` — registry register/get/list
- `tests/test_schemas.py` — result type model validation
- `tests/test_tool_safety.py` — AST-based mutation check
- `tests/test_message_codec.py` — serialize/deserialize round-trip
- `tests/test_conversation_memory.py` — Redis memory load/save/trim
- `tests/test_llm_client.py` — model tier definitions
