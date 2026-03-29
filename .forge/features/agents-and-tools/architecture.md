# Architecture: Agents and Tools

## Approach A: Bottom-Up by Layer

Build each layer from foundation to consumer: models → deps → tools → services → agents → registry → router expansion → tests.

**Implementation order:**
1. Result type models (`app/models/schemas.py`) — no deps
2. Tool return type models (inside `app/tools/platform.py`, `search.py`) — no deps
3. Base deps (`app/agents/base_deps.py`) — depends on AccessScope, PlatformClient
4. Platform client expansion (`app/services/platform_client.py`) — add read method stubs
5. Tool modules (`app/tools/`) — depends on base_deps, platform client
6. LLM client (`app/services/llm_client.py`) — model tiers, fallback chain
7. Agent registry (`app/agents/registry.py`) — no deps
8. All 12 agent modules — depends on tools, registry, models
9. Agent `__init__.py` — imports all agents to trigger registration
10. Message codec (`app/services/message_codec.py`) — depends on pydantic_ai.messages
11. Conversation memory (`app/services/conversation_memory.py`) — depends on codec
12. DI extensions (`app/dependencies.py`) — add get_agent_deps, get_conversation_memory
13. Chat router expansion (`app/routers/chat.py`) — depends on everything above
14. Update pyproject.toml (fakeredis)
15. Tests

**Trade-offs:**
- (+) Each layer independently testable
- (+) Clean dependency order — no forward references
- (+) Models can be validated before agents use them
- (-) Many files in sequence

## Approach B: Agent-First Vertical Slices

Build one complete agent (copilot) end-to-end first, then replicate the pattern.

**Trade-offs:**
- (+) Get a working vertical slice fast
- (-) Creates forward references — tools need models, agents need tools
- (-) Registry and memory can't be tested until the first agent exists
- (-) Harder to parallelize the 12 agents

## Recommendation

**Approach A: Bottom-Up by Layer** is the right choice.

The spec defines clear layer boundaries. Building bottom-up means each file compiles and can be tested as soon as it's written. The 12 agents all follow the same pattern, so once tools and registry exist, agents can be written rapidly. The conversation memory and message codec are self-contained services that don't need agents to exist.

## Task Breakdown (recommended approach)

| Step | Files | Depends on |
|---|---|---|
| 1. Result models | `app/models/schemas.py` | — |
| 2. Base deps | `app/agents/base_deps.py` | AccessScope, PlatformClient |
| 3. Platform client expansion | `app/services/platform_client.py` | — |
| 4. Tool modules | `app/tools/__init__.py`, `platform.py`, `search.py`, `calendar_adapter.py`, `email_adapter.py`, `crm_adapter.py` | steps 1-3 |
| 5. LLM client | `app/services/llm_client.py` | config |
| 6. Agent registry | `app/agents/registry.py` | — |
| 7. All 12 agents | `app/agents/copilot.py` ... `doc_extractor.py` | steps 1-6 |
| 8. Agent init | `app/agents/__init__.py` | step 7 |
| 9. Message codec | `app/services/message_codec.py` | pydantic_ai |
| 10. Conversation memory | `app/services/conversation_memory.py` | step 9 |
| 11. DI extensions | `app/dependencies.py` | steps 8, 10 |
| 12. Chat router | `app/routers/chat.py` | steps 8, 10, 11 |
| 13. Update deps | `pyproject.toml` | — |
| 14. Tests | all test files | all above |
