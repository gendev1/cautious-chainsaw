# Agents and Tools -- Pydantic AI Implementation Guide

This document specifies the implementation architecture for the Python sidecar's Pydantic AI agent system. It covers agent registration and routing, model tier configuration, tool definitions with dependency injection, structured output validation, conversation memory, and testing patterns.

All code examples use Pydantic AI's actual API surface.

---

## 1. Agent Registry

The sidecar runs 12 agents, each owning a single feature. The registry maps agent names to their configured `Agent` instances and provides a single lookup point for routers and job handlers.

### 1.1 Registry Module

```python
# app/agents/registry.py

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic_ai import Agent


@dataclass
class AgentEntry:
    """Metadata wrapper around a registered agent."""

    name: str
    agent: Agent[Any, Any]
    tier: str                    # "copilot", "batch", "analysis", "extraction"
    description: str


class AgentRegistry:
    """Central registry of all Pydantic AI agents.

    Agents are registered at import time and looked up by name
    at request time.
    """

    def __init__(self) -> None:
        self._agents: dict[str, AgentEntry] = {}

    def register(
        self,
        name: str,
        agent: Agent[Any, Any],
        *,
        tier: str,
        description: str = "",
    ) -> None:
        if name in self._agents:
            raise ValueError(f"Agent '{name}' is already registered")
        self._agents[name] = AgentEntry(
            name=name,
            agent=agent,
            tier=tier,
            description=description,
        )

    def get(self, name: str) -> AgentEntry:
        try:
            return self._agents[name]
        except KeyError:
            raise KeyError(
                f"Unknown agent '{name}'. Registered: {list(self._agents)}"
            )

    def list_agents(self) -> list[AgentEntry]:
        return list(self._agents.values())


# Module-level singleton
registry = AgentRegistry()
```

### 1.2 Agent Registration

Each agent module registers itself when imported. The application startup imports all agent modules to populate the registry.

```python
# app/agents/__init__.py

from app.agents.registry import registry

# Importing each module triggers its register() call.
from app.agents import (  # noqa: F401
    copilot,
    digest,
    email_drafter,
    email_triager,
    task_extractor,
    meeting_prep,
    meeting_summarizer,
    tax_planner,
    portfolio_analyst,
    firm_reporter,
    doc_classifier,
    doc_extractor,
)

__all__ = ["registry"]
```

The 12 agents and their tiers:

| Agent               | Tier        | Result Type         | Primary Endpoint                |
|---------------------|-------------|---------------------|---------------------------------|
| `copilot`           | Copilot     | `HazelCopilot`     | `POST /ai/chat`                |
| `digest`            | Batch       | `DailyDigest`      | `POST /ai/digest/generate`     |
| `email_drafter`     | Copilot     | `EmailDraft`       | `POST /ai/email/draft`         |
| `email_triager`     | Batch       | `list[TriagedEmail]`| `POST /ai/email/triage`       |
| `task_extractor`    | Batch       | `list[ExtractedTask]`| `POST /ai/tasks/extract`     |
| `meeting_prep`      | Copilot     | `MeetingPrep`      | `POST /ai/meetings/prep`       |
| `meeting_summarizer`| Copilot     | `MeetingSummary`   | `POST /ai/meetings/summarize`  |
| `tax_planner`       | Analysis    | `TaxPlan`          | `POST /ai/tax/plan`            |
| `portfolio_analyst` | Copilot     | `PortfolioAnalysis`| `POST /ai/portfolio/analyze`   |
| `firm_reporter`     | Analysis    | `FirmWideReport`   | `POST /ai/reports/firm-wide`   |
| `doc_classifier`    | Extraction  | `DocClassification`| `POST /ai/documents/classify`  |
| `doc_extractor`     | Extraction  | `DocExtraction`    | `POST /ai/documents/extract`   |

### 1.3 Router Lookup

Routers resolve agents through the registry, never by importing agent instances directly.

```python
# app/routers/chat.py

from uuid import uuid4

from fastapi import APIRouter, Depends
from pydantic_ai import Agent

from app.agents import registry
from app.dependencies import get_agent_deps
from app.models.schemas import ChatRequest, HazelCopilot

router = APIRouter(prefix="/ai")


@router.post("/chat", response_model=HazelCopilot)
async def chat(request: ChatRequest, deps=Depends(get_agent_deps)):
    entry = registry.get("copilot")
    result = await entry.agent.run(
        request.message,
        deps=deps,
        message_history=request.history,
    )
    return result.data
```

---

## 2. Agent Definition Pattern

Every agent follows the same structural pattern: a Pydantic AI `Agent` configured with a model, result type, tool list, system prompt, and optional fallback model.

### 2.1 Complete Copilot Agent Example

```python
# app/agents/copilot.py

from __future__ import annotations

from dataclasses import dataclass

from pydantic_ai import Agent, RunContext

from app.agents.base_deps import AgentDeps
from app.agents.registry import registry
from app.models.schemas import HazelCopilot
from app.tools.platform import (
    get_account_summary,
    get_client_timeline,
    get_household_summary,
    get_order_projection,
    get_report_snapshot,
    get_transfer_case,
)
from app.tools.search import (
    search_crm_notes,
    search_documents,
    search_emails,
    search_meeting_transcripts,
)


# -- Dependency container for this agent --

@dataclass
class CopilotDeps(AgentDeps):
    """Copilot extends the shared dependency shape with active conversation scope."""

    active_client_id: str | None = None
    active_household_id: str | None = None


# -- Agent definition --

copilot_agent: Agent[CopilotDeps, HazelCopilot] = Agent(
    model="anthropic:claude-sonnet-4-6",
    result_type=HazelCopilot,
    tools=[
        search_documents,
        search_emails,
        search_crm_notes,
        search_meeting_transcripts,
        get_household_summary,
        get_account_summary,
        get_transfer_case,
        get_order_projection,
        get_client_timeline,
        get_report_snapshot,
    ],
    fallback_model="openai:gpt-4o",
    retries=2,
)


# -- System prompt, rebuilt per turn --

@copilot_agent.system_prompt
async def build_system_prompt(ctx: RunContext[CopilotDeps]) -> str:
    """Rebuilt on every turn so live platform context stays fresh."""
    parts = [
        "You are Hazel, an AI assistant for wealth advisors.",
        f"Tenant: {ctx.deps.tenant_id}",
        f"Advisor: {ctx.deps.actor_id}",
        "",
        "Guidelines:",
        "- Answer questions using the tools available to you.",
        "- Always cite your sources with source_type, source_id, and a short excerpt.",
        "- Include an as_of timestamp reflecting the freshness of the data you used.",
        "- If you are unsure, say so and assign a low confidence score.",
        "- Never fabricate financial numbers. If data is missing, state that explicitly.",
        "- When recommending actions, return them as structured Action objects.",
        "- Suggest follow-up questions the advisor might want to ask.",
        "- Every financial figure must reference its data source.",
    ]

    if getattr(ctx.deps, "active_client_id", None):
        client = await ctx.deps.platform.get_client_profile(
            ctx.deps.active_client_id,
            ctx.deps.access_scope,
        )
        timeline = await ctx.deps.platform.get_client_timeline(
            ctx.deps.active_client_id,
            ctx.deps.access_scope,
            days=30,
        )
        parts.extend(
            [
                "",
                "Active client context:",
                f"- Client: {client.name} ({client.client_id})",
                f"- Recent activity items: {len(timeline)}",
            ]
        )

    if getattr(ctx.deps, "active_household_id", None):
        household = await ctx.deps.platform.get_household_summary(
            ctx.deps.active_household_id,
            ctx.deps.access_scope,
        )
        parts.extend(
            [
                "",
                "Active household context:",
                f"- Household: {household.household_name} ({household.household_id})",
                f"- AUM: {household.total_aum}",
            ]
        )

    return "\n".join(parts)


# -- Register --

registry.register(
    "copilot",
    copilot_agent,
    tier="copilot",
    description="Hazel copilot -- firmwide knowledge assistant",
)
```

### 2.2 Pattern Breakdown

Every agent module contains these sections in order:

1. **Dependency class** -- a plain Python class holding the `PlatformClient`, `AccessScope`, and any agent-specific state the tools need.
2. **Agent instantiation** -- `Agent(model=..., result_type=..., tools=[...], fallback_model=..., retries=...)`.
3. **System prompt function** -- decorated with `@agent.system_prompt`, receives `RunContext[Deps]`, returns a string. Rebuilt on every `run()` call so it can include fresh context.
4. **Registry call** -- `registry.register(name, agent, tier=..., description=...)`.

### 2.3 Batch Agent Example (Daily Digest)

```python
# app/agents/digest.py

from pydantic_ai import Agent, RunContext

from app.agents.registry import registry
from app.models.schemas import DailyDigest
from app.tools.platform import (
    get_advisor_clients,
    get_household_summary,
)
from app.tools.calendar_adapter import get_todays_meetings
from app.tools.email_adapter import get_unread_priority_emails
from app.tools.crm_adapter import get_pending_tasks


class DigestDeps:
    def __init__(self, platform, access_scope, tenant_id, actor_id):
        self.platform = platform
        self.access_scope = access_scope
        self.tenant_id = tenant_id
        self.actor_id = actor_id


digest_agent: Agent[DigestDeps, DailyDigest] = Agent(
    model="anthropic:claude-haiku-4-5",
    result_type=DailyDigest,
    tools=[
        get_advisor_clients,
        get_household_summary,
        get_todays_meetings,
        get_unread_priority_emails,
        get_pending_tasks,
    ],
    fallback_model="together:meta-llama/Llama-3.3-70B",
    retries=2,
)


@digest_agent.system_prompt
async def build_digest_prompt(ctx: RunContext[DigestDeps]) -> str:
    return f"""\
You are Hazel generating a daily briefing for advisor {ctx.deps.actor_id}.

Generate a personalized daily digest with:
- Today's meetings and prep notes
- Priority emails requiring attention
- Pending tasks and deadlines
- Account alerts (drift, RMD, large cash movements)
- Suggested actions for the day

Organize into clear sections. Prioritize by urgency.
"""


registry.register(
    "digest",
    digest_agent,
    tier="batch",
    description="Daily digest generator",
)
```

---

## 3. Three-Tier Model Routing

Model selection is driven by the cost/latency/accuracy profile of each feature. The sidecar uses five tiers.

### 3.1 Tier Definitions

```python
# app/services/llm_client.py

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelTier:
    primary: str
    fallback: str | None


TIERS: dict[str, ModelTier] = {
    "copilot": ModelTier(
        primary="anthropic:claude-sonnet-4-6",
        fallback="openai:gpt-4o",
    ),
    "batch": ModelTier(
        primary="anthropic:claude-haiku-4-5",
        fallback="together:meta-llama/Llama-3.3-70B",
    ),
    "analysis": ModelTier(
        primary="anthropic:claude-opus-4-6",
        fallback=None,  # No fallback -- accuracy-critical, fail rather than degrade
    ),
    "extraction": ModelTier(
        primary="anthropic:claude-haiku-4-5",
        fallback=None,  # High volume, low cost
    ),
    "transcription": ModelTier(
        primary="whisper:large-v3",
        fallback="deepgram:nova-3",
    ),
}
```

### 3.2 Tier-to-Agent Mapping

| Tier           | Primary Model            | Fallback Model                    | Agents                                                    |
|----------------|--------------------------|-----------------------------------|-----------------------------------------------------------|
| **Copilot**    | `claude-sonnet-4-6`     | `gpt-4o`                          | copilot, email_drafter, meeting_prep, meeting_summarizer, portfolio_analyst |
| **Batch**      | `claude-haiku-4-5`      | `Llama-3.3-70B` (Together)        | digest, email_triager, task_extractor                     |
| **Analysis**   | `claude-opus-4-6`       | None                              | tax_planner, firm_reporter                                |
| **Extraction** | `claude-haiku-4-5`      | None                              | doc_classifier, doc_extractor                             |
| **Transcription** | Whisper `large-v3`   | Deepgram `nova-3`                 | (audio pipeline, not a Pydantic AI agent)                 |

### 3.3 Provider Fallback Chains

Pydantic AI's `fallback_model` handles single-level fallback natively. For multi-level fallback chains or custom retry logic, wrap the agent run:

```python
# app/services/llm_client.py

from pydantic_ai import Agent
from pydantic_ai.exceptions import UnexpectedModelBehavior
import httpx


async def run_with_fallback_chain(
    agent: Agent,
    prompt: str,
    deps,
    *,
    message_history=None,
    fallback_models: list[str] | None = None,
):
    """Run an agent with a multi-level fallback chain.

    The agent's own primary and fallback_model are tried first.
    If both fail and fallback_models is provided, each model in the
    list is tried in order.
    """
    # First attempt: uses agent's configured model + fallback_model
    try:
        return await agent.run(
            prompt,
            deps=deps,
            message_history=message_history,
        )
    except (UnexpectedModelBehavior, httpx.HTTPStatusError) as first_err:
        if not fallback_models:
            raise

        last_err = first_err
        for model in fallback_models:
            try:
                return await agent.run(
                    prompt,
                    deps=deps,
                    message_history=message_history,
                    model=model,  # Override model for this run
                )
            except (UnexpectedModelBehavior, httpx.HTTPStatusError) as e:
                last_err = e
                continue

        raise last_err
```

### 3.4 Provider Configuration

API keys and base URLs are loaded from environment via Pydantic Settings, never hardcoded.

```python
# app/config.py

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Anthropic
    anthropic_api_key: str
    # OpenAI (fallback + embeddings + whisper)
    openai_api_key: str
    # Together (batch fallback)
    together_api_key: str | None = None
    # Deepgram (transcription fallback)
    deepgram_api_key: str | None = None

    # Model overrides (for staging/testing)
    copilot_model: str = "anthropic:claude-sonnet-4-6"
    batch_model: str = "anthropic:claude-haiku-4-5"
    analysis_model: str = "anthropic:claude-opus-4-6"
    extraction_model: str = "anthropic:claude-haiku-4-5"

    model_config = {
        "env_file": ".env",
    }
```

### 3.5 Dynamic Model Override Per Run

For testing or staged rollouts, any agent can use a different model on a single invocation without changing its definition:

```python
result = await copilot_agent.run(
    "What is the Smith household AUM?",
    deps=deps,
    model="openai:gpt-4o",  # Override just this run
)
```

---

## 4. Tool Definition Pattern

Tools are typed Python functions that receive dependencies through Pydantic AI's `RunContext` dependency injection. Each tool reads data and returns a typed result. No tool mutates state.

### 4.1 Full Pattern: `get_household_summary`

```python
# app/tools/platform.py

from __future__ import annotations

from pydantic import BaseModel
from pydantic_ai import RunContext

from app.agents.base_deps import AgentDeps


class HouseholdSummary(BaseModel):
    household_id: str
    name: str
    total_aum: float
    accounts: list[AccountBrief]
    members: list[ClientBrief]
    as_of: str


class AccountBrief(BaseModel):
    account_id: str
    account_name: str
    account_type: str
    balance: float


class ClientBrief(BaseModel):
    client_id: str
    name: str
    relationship: str


async def get_household_summary(
    ctx: RunContext[AgentDeps],
    household_id: str,
) -> HouseholdSummary:
    """Retrieve a summary of a household including AUM, accounts, and members.

    Use this when the advisor asks about a household's overall financial
    picture, total assets, or account composition.
    """
    result = await ctx.deps.platform.get_household_summary(
        household_id=household_id,
        access_scope=ctx.deps.access_scope,
    )
    return result
```

Key points about the tool definition:

- The first parameter is always `ctx: RunContext[DepsType]`. Pydantic AI injects this automatically.
- Subsequent parameters (`household_id`) become the tool's input schema, visible to the LLM.
- The docstring becomes the tool description the LLM sees. Write it to explain when and why to use the tool.
- The return type annotation tells Pydantic AI (and the LLM) what shape of data to expect.
- The function body calls the injected `PlatformClient` through `ctx.deps`.

### 4.2 Full Pattern: `search_documents`

```python
# app/tools/search.py

from __future__ import annotations

from pydantic import BaseModel
from pydantic_ai import RunContext

from app.agents.base_deps import AgentDeps


class DocumentMatch(BaseModel):
    document_id: str
    title: str
    source_type: str
    excerpt: str
    relevance_score: float
    client_id: str | None
    household_id: str | None
    created_at: str


async def search_documents(
    ctx: RunContext[AgentDeps],
    query: str,
    *,
    client_id: str | None = None,
    document_type: str | None = None,
    max_results: int = 8,
) -> list[DocumentMatch]:
    """Search across uploaded documents, tax returns, estate plans, and statements.

    Use this when the advisor asks about document contents, uploaded files,
    tax returns, or estate planning documents. Optionally filter by client
    or document type.
    """
    results = await ctx.deps.platform.search_documents_text(
        query=query,
        filters={
            "client_id": client_id,
            "document_type": document_type,
            "limit": max_results,
        },
        access_scope=ctx.deps.access_scope,
    )
    return [
        DocumentMatch(
            document_id=r.document_id,
            title=r.title,
            source_type=r.source_type,
            excerpt=r.excerpt,
            relevance_score=r.relevance_score,
            client_id=r.client_id,
            household_id=r.household_id,
            created_at=r.created_at,
        )
        for r in results
    ]
```

### 4.3 Full Pattern: `get_account_summary`

```python
# app/tools/platform.py  (continued)

from pydantic import BaseModel
from pydantic_ai import RunContext


class AccountSummary(BaseModel):
    account_id: str
    account_name: str
    account_type: str
    custodian: str
    balance: float
    cash_balance: float
    holdings_count: int
    performance_ytd: float | None
    drift_from_model: float | None
    unrealized_gain_loss: float | None
    as_of: str


async def get_account_summary(
    ctx: RunContext[CopilotDeps],
    account_id: str,
) -> AccountSummary:
    """Retrieve detailed summary for a single account.

    Use this when the advisor asks about a specific account's balance,
    holdings, performance, drift, or unrealized gains/losses.
    """
    result = await ctx.deps.platform.get_account_summary(
        account_id=account_id,
        access_scope=ctx.deps.access_scope,
    )
    return result
```

### 4.4 Shared Dependency Type

When multiple agents share the same dependency shape, define a common base:

```python
# app/agents/base_deps.py

from __future__ import annotations

from dataclasses import dataclass

from app.models.access_scope import AccessScope
from app.services.platform_client import PlatformClient


@dataclass
class AgentDeps:
    """Base dependencies shared across all agents."""

    platform: PlatformClient
    access_scope: AccessScope
    tenant_id: str
    actor_id: str
```

All tool functions can then use `RunContext[AgentDeps]` as their context type, making tools reusable across agents.

---

## 5. Tool Safety Enforcement

The sidecar is read-oriented by design. Tool safety is enforced at multiple levels to ensure no agent can mutate regulated records.

### 5.1 Allowlist at the Agent Level

Every agent declares its tools as an explicit list. There is no dynamic tool discovery or plugin loading. If a tool is not in the list, the agent cannot call it.

```python
copilot_agent = Agent(
    model="anthropic:claude-sonnet-4-6",
    result_type=HazelCopilot,
    tools=[
        # Explicitly enumerated -- nothing else is available
        search_documents,
        search_emails,
        search_crm_notes,
        search_meeting_transcripts,
        get_household_summary,
        get_account_summary,
        get_transfer_case,
        get_order_projection,
        get_client_timeline,
        get_report_snapshot,
    ],
)
```

### 5.2 Read-Only PlatformClient

The `PlatformClient` exposes only read methods. There are no mutation methods to call even if a tool tried.

```python
# app/services/platform_client.py

class PlatformClient:
    """Narrow typed client for platform API reads.

    This class deliberately has NO methods for creating, updating,
    or deleting any resource. It is the single approved data access
    path for the sidecar.
    """

    def __init__(self, config: PlatformClientConfig, http_client: httpx.AsyncClient) -> None:
        self._config = config
        self._http = http_client

    async def get_household_summary(
        self, household_id: str, access_scope: AccessScope
    ) -> HouseholdSummary:
        ...

    async def get_account_summary(
        self, account_id: str, access_scope: AccessScope
    ) -> AccountSummary:
        ...

    async def get_client_profile(
        self, client_id: str, access_scope: AccessScope
    ) -> ClientProfile:
        ...

    # ... additional read methods ...

    # NO create_*, update_*, delete_*, submit_*, approve_*, send_* methods.
```

This is an interface excerpt only. For a runnable implementation, use the platform-client document as the source of truth.

### 5.3 Type-Level Enforcement

Tools receive `RunContext[AgentDeps]` where `AgentDeps` holds a `PlatformClient`. Since `PlatformClient` has no mutation methods, a tool function literally cannot call a write operation through the injected dependency. This is enforced by Python's type system and verified by mypy/pyright in CI.

```python
# This tool can only call read methods -- the type checker ensures it.
async def get_household_summary(
    ctx: RunContext[AgentDeps],  # AgentDeps.platform is PlatformClient (read-only)
    household_id: str,
) -> HouseholdSummary:
    # ctx.deps.platform.submit_order(...)  # AttributeError -- method does not exist
    # ctx.deps.platform.send_email(...)    # AttributeError -- method does not exist
    return await ctx.deps.platform.get_household_summary(
        household_id, ctx.deps.access_scope
    )
```

### 5.4 CI Enforcement with Static Analysis

Add a static analysis check that scans tool files for forbidden patterns:

```python
# tests/test_tool_safety.py

import ast
import pathlib


FORBIDDEN_PREFIXES = [
    "create_", "update_", "delete_", "submit_",
    "approve_", "send_", "post_", "put_", "patch_",
    "execute_", "mutate_", "write_",
]

TOOLS_DIR = pathlib.Path("app/tools")


def test_no_mutation_methods_in_tools():
    """Verify no tool file calls a mutation method on any dependency."""
    for py_file in TOOLS_DIR.glob("*.py"):
        tree = ast.parse(py_file.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                for prefix in FORBIDDEN_PREFIXES:
                    assert not node.attr.startswith(prefix), (
                        f"{py_file.name} calls '{node.attr}' which looks like "
                        f"a mutation method (prefix '{prefix}'). Tools must be "
                        f"read-only."
                    )


def test_no_httpx_direct_calls_in_tools():
    """Verify tools do not make raw HTTP calls bypassing PlatformClient."""
    for py_file in TOOLS_DIR.glob("*.py"):
        source = py_file.read_text()
        assert "httpx.post(" not in source, f"{py_file.name} has direct httpx.post"
        assert "httpx.put(" not in source, f"{py_file.name} has direct httpx.put"
        assert "httpx.patch(" not in source, f"{py_file.name} has direct httpx.patch"
        assert "httpx.delete(" not in source, f"{py_file.name} has direct httpx.delete"
```

### 5.5 Forbidden Tool Categories

No agent may register tools that:

- Submit orders to OMS
- Initiate transfers
- Approve onboarding cases
- Post billing
- Modify workflow state
- Send emails (drafting is allowed; sending is not)
- Create CRM records directly (generating sync payloads is allowed)
- Publish report artifacts directly
- Persist authoritative meeting records

---

## 6. Structured Output

Pydantic AI validates every agent response against the declared `result_type`. If the LLM's output does not conform to the Pydantic model, Pydantic AI retries automatically (up to the configured `retries` count) with a validation error message fed back to the LLM.

### 6.1 How It Works

When an agent is defined with `result_type=SomeModel`, Pydantic AI:

1. Generates a JSON schema from `SomeModel` and includes it in the LLM request (as a tool definition for structured output).
2. Parses the LLM's response as JSON.
3. Validates the JSON against `SomeModel` using Pydantic's validator.
4. If validation fails, sends the validation errors back to the LLM and retries.
5. Returns a `RunResult[SomeModel]` where `result.data` is a fully validated instance.

```python
# The agent ensures every response is a valid HazelCopilot instance.
copilot_agent = Agent(
    model="anthropic:claude-sonnet-4-6",
    result_type=HazelCopilot,
    retries=2,  # Up to 2 retries on validation failure
    ...
)

result = await copilot_agent.run("What is the Smith AUM?", deps=deps)
# result.data is guaranteed to be a valid HazelCopilot instance.
# Accessing result.data.answer, result.data.citations, etc. is type-safe.
```

### 6.2 Result Type: `HazelCopilot`

```python
# app/models/schemas.py

from pydantic import BaseModel, Field


class Citation(BaseModel):
    source_type: str = Field(
        description="One of: document, email, crm_note, meeting_transcript, account_data"
    )
    source_id: str
    title: str
    excerpt: str = Field(description="Relevant excerpt from the source, max 200 chars")
    relevance_score: float = Field(ge=0.0, le=1.0)


class Action(BaseModel):
    type: str = Field(
        description=(
            "Action type: CREATE_REBALANCE_PROPOSAL, SCHEDULE_MEETING, "
            "DRAFT_EMAIL, CREATE_TASK, REVIEW_DOCUMENT, etc."
        )
    )
    target_id: str | None = None
    reason: str


class HazelCopilot(BaseModel):
    """Structured response from the Hazel copilot agent."""

    answer: str = Field(description="Markdown-formatted response to the advisor's question")
    citations: list[Citation] = Field(
        default_factory=list,
        description="Sources referenced in the answer",
    )
    confidence: float = Field(
        ge=0.0, le=1.0,
        description="Confidence in the answer's accuracy",
    )
    as_of: str = Field(
        description="ISO 8601 timestamp of the freshest data used in the answer"
    )
    recommended_actions: list[Action] = Field(
        default_factory=list,
        description="Structured recommendations the advisor can act on",
    )
    follow_up_questions: list[str] = Field(
        default_factory=list,
        description="Suggested follow-up questions",
    )
```

### 6.3 Result Type: `DailyDigest`

```python
class DigestItem(BaseModel):
    type: str = Field(description="One of: meeting, task, email, alert, crm_update")
    title: str
    summary: str
    client_id: str | None = None
    urgency: str = Field(description="One of: high, medium, low")
    action_url: str | None = None


class DigestSection(BaseModel):
    title: str = Field(
        description="Section header: Today's Meetings, Pending Tasks, Account Alerts, etc."
    )
    items: list[DigestItem]


class PriorityItem(BaseModel):
    title: str
    reason: str
    urgency: str


class DailyDigest(BaseModel):
    """Structured daily briefing for an advisor."""

    advisor_id: str
    generated_at: str
    greeting: str = Field(description="Personalized greeting for the advisor")
    sections: list[DigestSection]
    priority_items: list[PriorityItem] = Field(
        description="Top 3-5 items requiring immediate attention"
    )
    suggested_actions: list[Action]
```

### 6.4 Result Type: `TaxPlan`

```python
class TaxSituation(BaseModel):
    filing_status: str
    estimated_income: float
    estimated_tax_bracket: float
    capital_gains_summary: dict
    rmd_status: dict | None = None
    loss_harvesting_potential: float


class TaxOpportunity(BaseModel):
    type: str = Field(
        description="One of: tax_loss_harvest, roth_conversion, charitable_qcd, gain_deferral"
    )
    description: str
    estimated_impact: float = Field(description="Estimated dollars saved or deferred")
    confidence: str = Field(description="One of: high, medium, low")
    action: Action
    assumptions: list[str]


class TaxScenario(BaseModel):
    name: str = Field(description="Scenario label: Harvest all losses, Convert $50K to Roth")
    inputs: dict
    projected_tax_liability: float
    compared_to_baseline: float = Field(description="Delta from baseline scenario")
    trade_offs: list[str]


class TaxPlan(BaseModel):
    """Structured tax planning analysis."""

    client_id: str
    tax_year: int
    current_situation: TaxSituation
    opportunities: list[TaxOpportunity]
    scenarios: list[TaxScenario]
    warnings: list[str]
    disclaimer: str = Field(
        default="This is decision support, not tax advice. "
        "Consult a qualified tax professional before taking action.",
        description="Always-present disclaimer",
    )
```

### 6.5 Result Type: `MeetingSummary`

```python
class TopicSection(BaseModel):
    topic: str
    summary: str
    speaker_attribution: dict[str, str] = Field(
        default_factory=dict,
        description="Speaker name to their key points",
    )
    decisions_made: list[str]


class MeetingSummary(BaseModel):
    """Structured meeting summary with action items."""

    meeting_id: str
    duration_minutes: int
    participants: list[str]
    executive_summary: str = Field(
        description="3-5 sentence executive summary of the meeting"
    )
    key_topics: list[TopicSection]
    action_items: list[ExtractedTask]
    follow_up_drafts: list[EmailDraft] = Field(
        default_factory=list,
        description="Suggested follow-up email drafts",
    )
    client_sentiment: str | None = Field(
        default=None,
        description="One of: positive, neutral, concerned. Null if not determinable.",
    )
    next_steps: list[str]
    crm_sync_payloads: list[CRMSyncPayload] = Field(
        default_factory=list,
        description="CRM payloads for platform to execute",
    )
```

### 6.6 Validation Retry Flow

When the LLM produces output that does not match the result type, Pydantic AI automatically retries:

```text
Run 1:
  LLM returns JSON missing required field "as_of"
  -> Pydantic raises ValidationError
  -> Pydantic AI feeds error back: "Field 'as_of' is required"

Run 2 (retry 1):
  LLM returns valid JSON with all required fields
  -> Pydantic validates successfully
  -> result.data is a HazelCopilot instance
```

If all retries are exhausted, `agent.run()` raises `UnexpectedModelBehavior` with the last validation error attached. The router catches this and returns a structured error response to the platform.

---

## 7. Conversation Memory Integration

The copilot agent maintains multi-turn conversation state. Memory is stored in Redis with a 2-hour TTL and a 50-message cap. The stored payload is the serialized Pydantic AI message list, not a simplified `{role, content}` transcript, so tool calls and tool results survive into the next turn.

### 7.1 Memory Storage

```python
# app/services/conversation_memory.py

from __future__ import annotations

import json
from datetime import timedelta
from uuid import uuid4

import redis.asyncio as redis
from pydantic_ai.messages import ModelMessage

from app.services.message_codec import (
    deserialize_message,
    extract_active_client_id,
    extract_active_household_id,
    serialize_message,
    trim_message_history,
)


CONVERSATION_TTL = timedelta(hours=2)
MAX_MESSAGES = 50


class ConversationMemory:
    """Redis-backed conversation memory for multi-turn agent interactions."""

    def __init__(self, redis_client: redis.Redis) -> None:
        self._redis = redis_client

    def _key(self, tenant_id: str, actor_id: str, conversation_id: str) -> str:
        return f"chat:{tenant_id}:{actor_id}:{conversation_id}"

    async def load(
        self,
        tenant_id: str,
        actor_id: str,
        conversation_id: str | None,
    ) -> list[ModelMessage]:
        """Load structured message history suitable for agent.run()."""
        conversation_id = conversation_id or str(uuid4())
        key = self._key(tenant_id, actor_id, conversation_id)
        raw = await self._redis.get(key)
        if raw is None:
            return []
        payload = json.loads(raw)
        return [deserialize_message(item) for item in payload["messages"]]

    async def save(
        self,
        tenant_id: str,
        actor_id: str,
        conversation_id: str,
        messages: list[ModelMessage],
    ) -> None:
        """Persist a full structured history with turn-aware trimming."""
        key = self._key(tenant_id, actor_id, conversation_id)
        trimmed = trim_message_history(messages, max_messages=MAX_MESSAGES)
        await self._redis.set(
            key,
            json.dumps(
                {
                    "messages": [serialize_message(m) for m in trimmed],
                    "active_client_id": extract_active_client_id(trimmed),
                    "active_household_id": extract_active_household_id(trimmed),
                }
            ),
            ex=int(CONVERSATION_TTL.total_seconds()),
        )

    async def load_state(
        self,
        tenant_id: str,
        actor_id: str,
        conversation_id: str,
    ) -> dict[str, str | None]:
        key = self._key(tenant_id, actor_id, conversation_id)
        raw = await self._redis.get(key)
        if raw is None:
            return {"active_client_id": None, "active_household_id": None}
        payload = json.loads(raw)
        return {
            "active_client_id": payload.get("active_client_id"),
            "active_household_id": payload.get("active_household_id"),
        }

    async def clear(
        self,
        tenant_id: str,
        actor_id: str,
        conversation_id: str,
    ) -> None:
        key = self._key(tenant_id, actor_id, conversation_id)
        await self._redis.delete(key)
```

### 7.1A Message Codec

`ConversationMemory` relies on a dedicated codec module so routers and tests do not need to understand the internal Pydantic AI message shape. This module is responsible for:

- serializing `ModelMessage` objects into Redis-safe JSON
- deserializing them back into `ModelMessage` instances for `message_history`
- extracting active client and household IDs from recent turns
- trimming history without breaking tool-call/tool-result continuity

```python
# app/services/message_codec.py

from __future__ import annotations

from typing import Any

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)


def serialize_message(message: ModelMessage) -> dict[str, Any]:
    """Convert a Pydantic AI message into a Redis-safe dict."""
    if isinstance(message, ModelRequest):
        role = "request"
    elif isinstance(message, ModelResponse):
        role = "response"
    else:
        raise TypeError(f"Unsupported message type: {type(message)!r}")

    return {
        "role": role,
        "parts": [serialize_part(part) for part in message.parts],
    }


def deserialize_message(payload: dict[str, Any]) -> ModelMessage:
    """Rehydrate a Redis payload back into a Pydantic AI message."""
    parts = [deserialize_part(part) for part in payload["parts"]]
    if payload["role"] == "request":
        return ModelRequest(parts=parts)
    if payload["role"] == "response":
        return ModelResponse(parts=parts)
    raise ValueError(f"Unknown message role: {payload['role']}")


def serialize_part(part: Any) -> dict[str, Any]:
    if isinstance(part, UserPromptPart):
        return {"type": "user_prompt", "content": part.content}
    if isinstance(part, SystemPromptPart):
        return {"type": "system_prompt", "content": part.content}
    if isinstance(part, TextPart):
        return {"type": "text", "content": part.content}
    if isinstance(part, ToolCallPart):
        return {
            "type": "tool_call",
            "tool_name": part.tool_name,
            "args": part.args,
            "tool_call_id": getattr(part, "tool_call_id", None),
        }
    if isinstance(part, ToolReturnPart):
        return {
            "type": "tool_return",
            "tool_name": part.tool_name,
            "content": part.content,
            "tool_call_id": getattr(part, "tool_call_id", None),
        }
    raise TypeError(f"Unsupported message part: {type(part)!r}")


def deserialize_part(payload: dict[str, Any]) -> Any:
    part_type = payload["type"]
    if part_type == "user_prompt":
        return UserPromptPart(content=payload["content"])
    if part_type == "system_prompt":
        return SystemPromptPart(content=payload["content"])
    if part_type == "text":
        return TextPart(content=payload["content"])
    if part_type == "tool_call":
        return ToolCallPart(
            tool_name=payload["tool_name"],
            args=payload["args"],
            tool_call_id=payload.get("tool_call_id"),
        )
    if part_type == "tool_return":
        return ToolReturnPart(
            tool_name=payload["tool_name"],
            content=payload["content"],
            tool_call_id=payload.get("tool_call_id"),
        )
    raise ValueError(f"Unknown message part type: {part_type}")


def trim_message_history(
    messages: list[ModelMessage],
    *,
    max_messages: int,
) -> list[ModelMessage]:
    """
    Keep the newest complete turns while preserving any leading system prompt.

    Strategy:
    - keep the first system prompt message if present
    - keep the newest max_messages-1 additional messages
    - do not trim the tail only by assistant text; preserve tool trace messages
    """
    if len(messages) <= max_messages:
        return messages

    first = messages[0]
    if _is_system_prompt_message(first):
        tail = messages[-(max_messages - 1):]
        return [first, *tail]

    return messages[-max_messages:]


def extract_active_client_id(messages: list[ModelMessage]) -> str | None:
    """Best-effort extraction of the last active client ID from message history."""
    for message in reversed(messages):
        for part in getattr(message, "parts", []):
            client_id = _extract_id_from_part(part, keys=("client_id",))
            if client_id:
                return client_id
    return None


def extract_active_household_id(messages: list[ModelMessage]) -> str | None:
    """Best-effort extraction of the last active household ID from message history."""
    for message in reversed(messages):
        for part in getattr(message, "parts", []):
            household_id = _extract_id_from_part(part, keys=("household_id",))
            if household_id:
                return household_id
    return None


def _is_system_prompt_message(message: ModelMessage) -> bool:
    return any(isinstance(part, SystemPromptPart) for part in message.parts)


def _extract_id_from_part(part: Any, *, keys: tuple[str, ...]) -> str | None:
    if isinstance(part, ToolCallPart) and isinstance(part.args, dict):
        for key in keys:
            value = part.args.get(key)
            if isinstance(value, str) and value:
                return value
    if isinstance(part, ToolReturnPart) and isinstance(part.content, dict):
        for key in keys:
            value = part.content.get(key)
            if isinstance(value, str) and value:
                return value
    return None
```

The important behavioral rules of `message_codec.py` are:

- it preserves tool traces, not just visible assistant prose
- it extracts conversation scope from structured tool traffic when possible
- it trims by message object boundaries so a tool call is not separated from its result
- it treats the first system prompt specially so persona and runtime instructions survive long chats

### 7.2 Passing History to the Agent

The router loads structured history from Redis and passes it as `message_history`. The system prompt is rebuilt on every turn via `@agent.system_prompt`, so it always reflects fresh platform data rather than stale context from the first turn.

```python
# app/routers/chat.py

from fastapi import APIRouter, Depends

from app.agents import registry
from app.dependencies import get_agent_deps, get_conversation_memory
from app.models.schemas import ChatRequest, HazelCopilot
from app.services.conversation_memory import ConversationMemory

router = APIRouter(prefix="/ai")


@router.post("/chat", response_model=HazelCopilot)
async def chat(
    request: ChatRequest,
    deps=Depends(get_agent_deps),
    memory: ConversationMemory = Depends(get_conversation_memory),
):
    entry = registry.get("copilot")
    conversation_id = request.conversation_id or str(uuid4())

    # Load structured conversation history from Redis
    history = await memory.load(
        tenant_id=deps.tenant_id,
        actor_id=deps.actor_id,
        conversation_id=conversation_id,
    )
    state = await memory.load_state(
        deps.tenant_id, deps.actor_id, conversation_id,
    )

    deps.active_client_id = request.client_id or state["active_client_id"]
    deps.active_household_id = request.household_id or state["active_household_id"]

    # Run agent with history. System prompt is rebuilt automatically.
    result = await entry.agent.run(
        request.message,
        deps=deps,
        message_history=history,
    )

    # Persist the full structured transcript, including tool calls/results.
    await memory.save(
        deps.tenant_id,
        deps.actor_id,
        conversation_id,
        result.all_messages(),
    )

    return result.data
```

### 7.3 Memory Lifecycle

- **Creation**: First message in a conversation creates the Redis key.
- **Growth**: Each turn stores the full structured transcript including user prompts, assistant replies, tool calls, and tool results.
- **Cap**: Trim by complete recent turns, not by raw text lines. Keep the newest exchanges and preserve tool traces that belong to those exchanges.
- **Expiry**: Redis TTL of 2 hours. Conversations that go idle expire automatically.
- **Isolation**: Cache key is `chat:{tenant_id}:{actor_id}:{conversation_id}`. Cross-tenant or cross-advisor leakage is structurally impossible.
- **System prompt**: Rebuilt from scratch on every `run()` call via the `@agent.system_prompt` decorator. It is never stored in the message history.
- **Freshness**: Active client and household IDs can be carried forward from conversation state, but client profile, household summary, and recent activity are refreshed from platform reads on every turn.

### 7.4 Streaming with Memory

For the SSE streaming endpoint, history is loaded the same way but `run_stream` is used. After the stream finishes, persist `result.all_messages()` rather than only the final assistant text.

```python
# app/routers/chat.py

from uuid import uuid4

from fastapi.responses import StreamingResponse


@router.post("/chat/stream")
async def chat_stream(
    request: ChatRequest,
    deps=Depends(get_agent_deps),
    memory: ConversationMemory = Depends(get_conversation_memory),
):
    entry = registry.get("copilot")
    conversation_id = request.conversation_id or str(uuid4())
    history = await memory.load(
        deps.tenant_id, deps.actor_id, conversation_id,
    )
    state = await memory.load_state(
        deps.tenant_id, deps.actor_id, conversation_id,
    )
    deps.active_client_id = request.client_id or state["active_client_id"]
    deps.active_household_id = request.household_id or state["active_household_id"]

    async def event_stream():
        async with entry.agent.run_stream(
            request.message,
            deps=deps,
            message_history=history,
        ) as result:
            async for chunk in result.stream_text():
                yield f"data: {chunk}\n\n"

            # After stream completes, get the validated structured result
            final = await result.get_data()
            await memory.save(
                deps.tenant_id,
                deps.actor_id,
                conversation_id,
                result.all_messages(),
            )

            yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
```

---

## 8. Agent Testing

Agents are tested without making real LLM calls. Pydantic AI provides `TestModel` and `FunctionModel` for deterministic testing.

### 8.1 Testing with `TestModel`

`TestModel` is a built-in mock that returns structured data matching the agent's `result_type` without calling any LLM provider.

```python
# tests/agents/test_copilot.py

import pytest
from pydantic_ai import models as pai_models

from app.agents.copilot import copilot_agent, CopilotDeps
from app.models.access_scope import AccessScope


@pytest.fixture
def mock_deps(mock_platform_client):
    """Build CopilotDeps with a mocked PlatformClient."""
    return CopilotDeps(
        platform=mock_platform_client,
        access_scope=AccessScope(
            visibility_mode="full_tenant",
            household_ids=[],
            client_ids=[],
            account_ids=[],
            document_ids=[],
            advisor_ids=["adv_test"],
        ),
        tenant_id="tenant_test",
        actor_id="adv_test",
    )


@pytest.mark.anyio
async def test_copilot_returns_structured_output(mock_deps):
    """Verify the copilot agent returns a valid HazelCopilot."""
    with pai_models.override(copilot_agent, model=pai_models.TestModel()):
        result = await copilot_agent.run(
            "What is the Smith household AUM?",
            deps=mock_deps,
        )

    # TestModel returns a synthetic valid instance of the result_type
    assert result.data is not None
    assert isinstance(result.data.answer, str)
    assert isinstance(result.data.citations, list)
    assert 0.0 <= result.data.confidence <= 1.0
```

### 8.2 Testing with `FunctionModel`

For more control, `FunctionModel` lets you define exactly what the mock LLM returns:

```python
# tests/agents/test_copilot_function_model.py

import pytest
from pydantic_ai import models as pai_models
from pydantic_ai.messages import (
    ModelResponse,
    ToolCallPart,
    TextPart,
)
import json

from app.agents.copilot import copilot_agent, CopilotDeps


def mock_llm_handler(messages, model_settings):
    """Simulate the LLM calling get_household_summary then returning a result."""
    # Check if this is the first call (should trigger tool use)
    # or the second call (should return final structured output)
    last_msg = messages[-1]

    # If we haven't called a tool yet, call one
    if not any("tool" in str(m).lower() for m in messages[1:]):
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="get_household_summary",
                    args={"household_id": "hh_smith_001"},
                )
            ]
        )

    # After tool result is available, return the structured answer
    return ModelResponse(
        parts=[
            TextPart(
                content=json.dumps({
                    "answer": "The Smith household has $2.4M in total AUM.",
                    "citations": [{
                        "source_type": "account_data",
                        "source_id": "hh_smith_001",
                        "title": "Smith Household Summary",
                        "excerpt": "Total AUM: $2,400,000",
                        "relevance_score": 1.0,
                    }],
                    "confidence": 0.95,
                    "as_of": "2026-03-26T08:00:00Z",
                    "recommended_actions": [],
                    "follow_up_questions": [
                        "How is the Smith portfolio allocated?",
                        "Are there any tax-loss harvesting opportunities?",
                    ],
                })
            )
        ]
    )


@pytest.mark.anyio
async def test_copilot_tool_call_and_response(mock_deps):
    with pai_models.override(
        copilot_agent,
        model=pai_models.FunctionModel(mock_llm_handler),
    ):
        result = await copilot_agent.run(
            "What is the Smith household AUM?",
            deps=mock_deps,
        )

    assert "2.4M" in result.data.answer
    assert result.data.confidence == 0.95
    assert len(result.data.citations) == 1
    assert result.data.citations[0].source_id == "hh_smith_001"
```

### 8.3 Mocking the PlatformClient

The `PlatformClient` is mocked so tools return deterministic data without hitting a real API:

```python
# tests/conftest.py

from unittest.mock import AsyncMock

import pytest

from app.models.schemas import HouseholdSummary, AccountSummary, AccountBrief, ClientBrief


@pytest.fixture
def mock_platform_client():
    client = AsyncMock()

    client.get_household_summary.return_value = HouseholdSummary(
        household_id="hh_smith_001",
        name="Smith Household",
        total_aum=2_400_000.0,
        accounts=[
            AccountBrief(
                account_id="acc_001",
                account_name="Smith Joint Brokerage",
                account_type="brokerage",
                balance=1_200_000.0,
            ),
            AccountBrief(
                account_id="acc_002",
                account_name="Smith IRA",
                account_type="ira",
                balance=1_200_000.0,
            ),
        ],
        members=[
            ClientBrief(
                client_id="cl_001",
                name="John Smith",
                relationship="primary",
            ),
            ClientBrief(
                client_id="cl_002",
                name="Jane Smith",
                relationship="spouse",
            ),
        ],
        as_of="2026-03-26T08:00:00Z",
    )

    client.get_account_summary.return_value = AccountSummary(
        account_id="acc_001",
        account_name="Smith Joint Brokerage",
        account_type="brokerage",
        custodian="Schwab",
        balance=1_200_000.0,
        cash_balance=45_000.0,
        holdings_count=28,
        performance_ytd=0.067,
        drift_from_model=0.02,
        unrealized_gain_loss=85_000.0,
        as_of="2026-03-26T08:00:00Z",
    )

    client.search_documents_text.return_value = []

    return client
```

### 8.4 Testing Tool Functions Directly

Tool functions can be tested in isolation by constructing a `RunContext` manually:

```python
# tests/tools/test_platform_tools.py

import pytest
from unittest.mock import AsyncMock, MagicMock

from pydantic_ai import RunContext

from app.tools.platform import get_household_summary
from app.agents.base_deps import AgentDeps
from app.models.access_scope import AccessScope


@pytest.fixture
def mock_ctx(mock_platform_client):
    deps = AgentDeps(
        platform=mock_platform_client,
        access_scope=AccessScope(
            visibility_mode="full_tenant",
            household_ids=[],
            client_ids=[],
            account_ids=[],
            document_ids=[],
            advisor_ids=["adv_test"],
        ),
        tenant_id="tenant_test",
        actor_id="adv_test",
    )
    ctx = RunContext(
        deps=deps,
        retry=0,
        tool_name="get_household_summary",
    )
    return ctx


@pytest.mark.anyio
async def test_get_household_summary_calls_platform(mock_ctx):
    result = await get_household_summary(mock_ctx, household_id="hh_smith_001")

    assert result.household_id == "hh_smith_001"
    assert result.total_aum == 2_400_000.0
    assert len(result.accounts) == 2

    # Verify the platform client was called with correct args
    mock_ctx.deps.platform.get_household_summary.assert_called_once_with(
        household_id="hh_smith_001",
        access_scope=mock_ctx.deps.access_scope,
    )
```

### 8.5 Testing Conversation Memory

```python
# tests/services/test_conversation_memory.py

import pytest
import fakeredis.aioredis

from app.services.conversation_memory import ConversationMemory
from tests.factories.messages import make_assistant_message, make_user_message


@pytest.fixture
async def memory():
    redis_client = fakeredis.aioredis.FakeRedis()
    return ConversationMemory(redis_client)


@pytest.mark.anyio
async def test_append_and_retrieve(memory):
    messages = [
        make_user_message("Hello"),
        make_assistant_message("Hi there"),
    ]
    await memory.save("t1", "a1", "conv1", messages)

    history = await memory.load("t1", "a1", "conv1")
    assert len(history) == 2


@pytest.mark.anyio
async def test_max_messages_cap(memory):
    messages = [make_user_message(f"msg {i}") for i in range(60)]
    await memory.save("t1", "a1", "conv1", messages)

    history = await memory.load("t1", "a1", "conv1")
    assert len(history) == 50  # Capped at MAX_MESSAGES


@pytest.mark.anyio
async def test_tenant_isolation(memory):
    await memory.save("tenant_a", "a1", "conv1", [make_user_message("secret A")])
    await memory.save("tenant_b", "a1", "conv1", [make_user_message("secret B")])

    history_a = await memory.load("tenant_a", "a1", "conv1")
    history_b = await memory.load("tenant_b", "a1", "conv1")

    assert len(history_a) == 1
    assert len(history_b) == 1
    # Tenant A cannot see tenant B's messages
```

### 8.6 Integration Test: Full Agent Round Trip

```python
# tests/agents/test_copilot_integration.py

import pytest
from pydantic_ai import models as pai_models

from app.agents.copilot import copilot_agent, CopilotDeps
from app.services.conversation_memory import ConversationMemory
from app.models.access_scope import AccessScope


@pytest.mark.anyio
async def test_copilot_full_round_trip(mock_platform_client):
    """Test a full round trip with structured history and tool trace continuity."""
    import fakeredis.aioredis

    redis_client = fakeredis.aioredis.FakeRedis()
    memory = ConversationMemory(redis_client)

    deps = CopilotDeps(
        platform=mock_platform_client,
        access_scope=AccessScope(
            visibility_mode="full_tenant",
            household_ids=[],
            client_ids=[],
            account_ids=[],
            document_ids=[],
            advisor_ids=["adv_test"],
        ),
        tenant_id="tenant_test",
        actor_id="adv_test",
    )

    # Turn 1
    with pai_models.override(copilot_agent, model=pai_models.TestModel()):
        result = await copilot_agent.run("Tell me about the Smith household", deps=deps)

    await memory.save(
        "tenant_test",
        "adv_test",
        "conv_1",
        result.all_messages(),
    )

    # Turn 2 -- with history
    history = await memory.load("tenant_test", "adv_test", "conv_1")
    assert len(history) >= 2

    with pai_models.override(copilot_agent, model=pai_models.TestModel()):
        result2 = await copilot_agent.run(
            "What about their tax situation?",
            deps=deps,
            message_history=history,
        )

    assert result2.data is not None
```

---

## Appendix A: Agent Definition Checklist

For every new agent:

1. Define a `result_type` as a Pydantic `BaseModel` with field descriptions and constraints.
2. Define a dependency class (or reuse `AgentDeps`) holding `PlatformClient` and `AccessScope`.
3. Instantiate `Agent(model=..., result_type=..., tools=[...], fallback_model=..., retries=2)`.
4. Add a `@agent.system_prompt` function that receives `RunContext[Deps]` and returns the prompt string.
5. Call `registry.register(name, agent, tier=..., description=...)`.
6. Write tests using `TestModel` for smoke tests and `FunctionModel` for behavioral tests.
7. Run the tool safety CI check to confirm no mutation methods leaked in.

## Appendix B: Tool Definition Checklist

For every new tool:

1. First parameter is `ctx: RunContext[DepsType]`.
2. Remaining parameters become the LLM-visible input schema.
3. Return type is a Pydantic `BaseModel` or a simple type.
4. Docstring explains when and why to use the tool (the LLM reads this).
5. Implementation calls only read methods on `ctx.deps.platform` or other read-only adapters.
6. No direct HTTP calls, no filesystem writes, no external mutations.
7. Add a unit test that mocks `ctx.deps` and verifies the tool calls the correct platform method.
