# Implementation Manifest: Agents and Tools

## Files Created

| File | Purpose |
|---|---|
| `src/app/models/schemas.py` | All structured output types (22 models) |
| `src/app/agents/base_deps.py` | Simpler AgentDeps base for tools |
| `src/app/agents/registry.py` | AgentRegistry with register/get/list |
| `src/app/agents/copilot.py` | Copilot agent (Sonnet, 10 tools) |
| `src/app/agents/digest.py` | Daily digest agent (Haiku, 5 tools) |
| `src/app/agents/email_drafter.py` | Email drafter agent (Sonnet) |
| `src/app/agents/email_triager.py` | Email triager agent (Haiku) |
| `src/app/agents/task_extractor.py` | Task extractor agent (Haiku) |
| `src/app/agents/meeting_prep.py` | Meeting prep agent (Sonnet) |
| `src/app/agents/meeting_summarizer.py` | Meeting summarizer agent (Sonnet) |
| `src/app/agents/tax_planner.py` | Tax planner agent (Opus, no fallback) |
| `src/app/agents/portfolio_analyst.py` | Portfolio analyst agent (Sonnet) |
| `src/app/agents/firm_reporter.py` | Firm reporter agent (Opus, no fallback) |
| `src/app/agents/doc_classifier.py` | Document classifier agent (Haiku) |
| `src/app/agents/doc_extractor.py` | Document extractor agent (Haiku) |
| `src/app/tools/__init__.py` | Tools package |
| `src/app/tools/platform.py` | 7 platform read tools |
| `src/app/tools/search.py` | 4 search tools |
| `src/app/tools/calendar_adapter.py` | Calendar adapter stub |
| `src/app/tools/email_adapter.py` | Email adapter stub |
| `src/app/tools/crm_adapter.py` | CRM adapter stub |
| `src/app/services/llm_client.py` | ModelTier definitions, 5 tiers, fallback chain |
| `src/app/services/message_codec.py` | Pydantic AI message serialization |
| `src/app/services/conversation_memory.py` | Redis-backed conversation memory |
| `conftest.py` | Root conftest with dummy API keys for tests |

## Files Modified

| File | Change |
|---|---|
| `src/app/agents/__init__.py` | Imports all 12 agents, exports registry |
| `src/app/services/platform_client.py` | Added 12 read method stubs |
| `src/app/dependencies.py` | Added get_agent_deps, get_conversation_memory |
| `src/app/routers/chat.py` | Implemented POST /chat and POST /chat/stream |
| `pyproject.toml` | Added fakeredis to dev deps |

## Patterns Followed

- Agent pattern: deps class → Agent(model, output_type, tools, retries, defer_model_check) → @system_prompt → registry.register()
- Tool pattern: RunContext[AgentDeps] first param, delegates to platform client, read-only
- Memory pattern: Redis-backed with tenant-scoped keys, full message serialization including tool traces
- Model tier: frozen dataclass with primary + optional fallback
- Adapted to pydantic-ai v1.73: output_type (not result_type), defer_model_check=True, no fallback_model param

## Test Results

```
58 passed in 1.48s
Ruff: All checks passed!
```
