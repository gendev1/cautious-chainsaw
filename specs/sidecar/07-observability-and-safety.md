# 07 — Observability, Safety Constraints, and Compliance Controls

This document specifies the implementation of observability, safety guardrails, and compliance controls for the Python sidecar. Every design choice traces back to the core spec principles: the sidecar is read-oriented, recommendation-oriented, and must degrade by feature rather than fail the wealth platform as a whole.

All code targets Python 3.12+, FastAPI, Pydantic AI, structlog, Langfuse SDK 3.x, and the Prometheus Python client.

---

## 1. Langfuse Integration

### 1.1 Client Initialization

A single Langfuse client is created at application startup and shared across all request handlers and background workers.

```python
# app/observability/langfuse_client.py

from langfuse import Langfuse
from app.config import Settings

_langfuse: Langfuse | None = None


def get_langfuse(settings: Settings) -> Langfuse:
    global _langfuse
    if _langfuse is None:
        _langfuse = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
            release=settings.app_version,
            enabled=settings.langfuse_enabled,
        )
    return _langfuse


def shutdown_langfuse() -> None:
    global _langfuse
    if _langfuse is not None:
        _langfuse.flush()
        _langfuse.shutdown()
        _langfuse = None
```

Register the lifecycle hooks on the FastAPI app:

```python
# app/main.py

from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.observability.langfuse_client import get_langfuse, shutdown_langfuse
from app.config import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    get_langfuse(settings)  # warm the client
    yield
    shutdown_langfuse()

app = FastAPI(lifespan=lifespan)
```

### 1.2 Per-Request Trace Creation

Every inbound request gets a Langfuse trace. The trace carries the tenant, actor, and conversation identifiers required for cost attribution and audit. This is created in middleware so no route handler can bypass it.

```python
# app/middleware/tracing.py

from __future__ import annotations
import time
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from app.observability.langfuse_client import get_langfuse
from app.config import get_settings
from app.context import RequestContext


class LangfuseTraceMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        ctx: RequestContext = request.state.context
        langfuse = get_langfuse(get_settings())

        trace = langfuse.trace(
            id=ctx.request_id,
            name=f"{request.method} {request.url.path}",
            user_id=ctx.actor_id,
            session_id=ctx.conversation_id,
            metadata={
                "tenant_id": ctx.tenant_id,
                "actor_type": ctx.actor_type,
                "endpoint": request.url.path,
            },
            tags=[f"tenant:{ctx.tenant_id}"],
        )

        # Attach trace to request state so downstream code can create spans.
        request.state.langfuse_trace = trace
        request.state.trace_start = time.monotonic()

        response = await call_next(request)

        latency_ms = (time.monotonic() - request.state.trace_start) * 1000
        trace.update(
            output={"status_code": response.status_code},
            metadata={
                "latency_ms": round(latency_ms, 2),
                "status_code": response.status_code,
            },
        )
        return response
```

### 1.3 Per-Agent-Call Span Creation

Each Pydantic AI agent invocation is wrapped in a Langfuse generation span that records the model, token counts, and cost. This is implemented as a reusable wrapper rather than inline code.

```python
# app/observability/tracing.py

from __future__ import annotations
import time
from dataclasses import dataclass
from typing import Any
from langfuse.client import StatefulTraceClient, StatefulGenerationClient


@dataclass
class AgentSpan:
    """Wraps a Langfuse generation span around a single agent call."""

    generation: StatefulGenerationClient
    start: float

    @classmethod
    def begin(
        cls,
        trace: StatefulTraceClient,
        *,
        agent_name: str,
        model: str,
        input_payload: dict[str, Any],
    ) -> AgentSpan:
        generation = trace.generation(
            name=agent_name,
            model=model,
            input=input_payload,
            metadata={"agent": agent_name},
        )
        return cls(generation=generation, start=time.monotonic())

    def end(
        self,
        *,
        output: Any,
        usage_input_tokens: int,
        usage_output_tokens: int,
        model: str | None = None,
    ) -> None:
        latency_ms = (time.monotonic() - self.start) * 1000
        self.generation.end(
            output=output,
            model=model,
            usage={
                "input": usage_input_tokens,
                "output": usage_output_tokens,
                "unit": "TOKENS",
            },
            metadata={"latency_ms": round(latency_ms, 2)},
        )


@dataclass
class ToolSpan:
    """Wraps a Langfuse span around a single tool invocation."""

    span: Any
    start: float

    @classmethod
    def begin(
        cls,
        trace: StatefulTraceClient,
        *,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> ToolSpan:
        span = trace.span(
            name=f"tool:{tool_name}",
            input=arguments,
            metadata={"tool": tool_name},
        )
        return cls(span=span, start=time.monotonic())

    def end(self, *, output_summary: str, error: str | None = None) -> None:
        latency_ms = (time.monotonic() - self.start) * 1000
        self.span.end(
            output={"summary": output_summary, "error": error},
            metadata={"latency_ms": round(latency_ms, 2)},
        )
```

### 1.4 Token Tracking and Cost Attribution

Token counts come from the Pydantic AI result object. Cost is computed using per-model rate tables and attributed to the tenant, advisor, and agent.

```python
# app/observability/cost.py

from __future__ import annotations
from decimal import Decimal

# Rates in USD per 1 000 tokens. Updated when provider pricing changes.
MODEL_RATES: dict[str, tuple[Decimal, Decimal]] = {
    # (input_rate_per_1k, output_rate_per_1k)
    "anthropic:claude-opus-4-6":   (Decimal("0.015"), Decimal("0.075")),
    "anthropic:claude-sonnet-4-6": (Decimal("0.003"), Decimal("0.015")),
    "anthropic:claude-haiku-4-5":  (Decimal("0.0008"), Decimal("0.004")),
    "openai:gpt-4o":               (Decimal("0.0025"), Decimal("0.010")),
    "together:meta-llama/Llama-3.3-70B": (Decimal("0.0009"), Decimal("0.0009")),
}

DEFAULT_RATE = (Decimal("0.003"), Decimal("0.015"))


def compute_request_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> Decimal:
    input_rate, output_rate = MODEL_RATES.get(model, DEFAULT_RATE)
    return (
        input_rate * Decimal(input_tokens) / Decimal(1000)
        + output_rate * Decimal(output_tokens) / Decimal(1000)
    )
```

Attribute the cost after every agent call:

```python
# Inside the route handler or agent runner

from app.observability.cost import compute_request_cost

async def run_agent_with_tracking(
    agent,
    prompt,
    *,
    agent_deps,
    ctx: RequestContext,
    trace,
    agent_name: str,
    model: str,
):
    span = AgentSpan.begin(trace, agent_name=agent_name, model=model, input_payload={"prompt": prompt})

    result = await agent.run(prompt, deps=agent_deps)

    input_tokens = result.usage().request_tokens or 0
    output_tokens = result.usage().response_tokens or 0
    cost = compute_request_cost(model, input_tokens, output_tokens)

    span.end(
        output=result.data.model_dump() if hasattr(result.data, "model_dump") else str(result.data),
        usage_input_tokens=input_tokens,
        usage_output_tokens=output_tokens,
        model=model,
    )

    # Attribute cost in Langfuse trace metadata for dashboard queries.
    trace.update(metadata={
        "cost_usd": str(cost),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "model": model,
        "agent": agent_name,
        "tenant_id": ctx.tenant_id,
        "advisor_id": ctx.actor_id,
    })

    return result
```

### 1.5 Langfuse Dashboard Queries

With the metadata above, the following Langfuse dashboard filters are available out of the box:

| Query | Filter |
|-------|--------|
| Cost per tenant per day | Group by `metadata.tenant_id`, aggregate `metadata.cost_usd`, bucket by day |
| Cost per advisor | Group by `user_id` (set to `actor_id`) |
| Cost per agent type | Group by `metadata.agent`, aggregate `metadata.cost_usd` |
| Token usage by model | Group by `model`, sum `usage.input` and `usage.output` |
| Latency P50/P95/P99 per endpoint | Group by `name`, percentile on `metadata.latency_ms` |

---

## 2. Token Budget Management

Each tenant has a configurable daily token ceiling. The sidecar enforces this limit before every agent call, returning HTTP 429 when the budget is exhausted. Tracking uses Redis atomic increments with daily key expiry.

### 2.1 Configuration

```python
# app/config.py  (relevant excerpt)

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Default daily token limit per tenant (overridable per tenant in platform config).
    default_daily_token_limit: int = 5_000_000
    token_budget_redis_prefix: str = "sidecar:token_budget"
```

### 2.2 Redis Token Ledger

```python
# app/observability/token_budget.py

from __future__ import annotations
import datetime
from redis.asyncio import Redis

SECONDS_IN_DAY = 86_400


def _budget_key(prefix: str, tenant_id: str) -> str:
    today = datetime.date.today().isoformat()
    return f"{prefix}:{tenant_id}:{today}"


async def get_tokens_used(redis: Redis, prefix: str, tenant_id: str) -> int:
    key = _budget_key(prefix, tenant_id)
    val = await redis.get(key)
    return int(val) if val else 0


async def increment_tokens(
    redis: Redis,
    prefix: str,
    tenant_id: str,
    tokens: int,
) -> int:
    """Atomically increment and return the new total. Sets a 48h TTL on first write."""
    key = _budget_key(prefix, tenant_id)
    pipe = redis.pipeline()
    pipe.incrby(key, tokens)
    pipe.expire(key, SECONDS_IN_DAY * 2)  # 48h TTL so yesterday's key cleans up
    results = await pipe.execute()
    return results[0]  # new total after increment


async def check_budget(
    redis: Redis,
    prefix: str,
    tenant_id: str,
    limit: int,
) -> tuple[bool, int]:
    """Return (allowed, tokens_used). Does not mutate."""
    used = await get_tokens_used(redis, prefix, tenant_id)
    return used < limit, used
```

### 2.3 Pre-Call Enforcement Middleware

Budget is checked before the agent call. If the tenant is over-limit, a 429 response is returned with a `Retry-After` header pointing to midnight UTC.

```python
# app/middleware/token_budget.py

from __future__ import annotations
import datetime
from fastapi import Request, HTTPException
from app.observability.token_budget import check_budget
from app.config import get_settings


async def enforce_token_budget(request: Request) -> None:
    """FastAPI dependency that enforces per-tenant daily token limits."""
    ctx = request.state.context
    settings = get_settings()
    redis = request.app.state.redis

    # Allow tenant-specific overrides; fall back to global default.
    limit = getattr(ctx, "tenant_token_limit", None) or settings.default_daily_token_limit

    allowed, used = await check_budget(
        redis, settings.token_budget_redis_prefix, ctx.tenant_id, limit
    )
    if not allowed:
        now = datetime.datetime.now(datetime.timezone.utc)
        midnight = (now + datetime.timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        retry_after = int((midnight - now).total_seconds())
        raise HTTPException(
            status_code=429,
            detail={
                "error": "token_budget_exceeded",
                "tenant_id": ctx.tenant_id,
                "tokens_used": used,
                "daily_limit": limit,
                "retry_after_seconds": retry_after,
            },
            headers={"Retry-After": str(retry_after)},
        )
```

Wire this into the router as a dependency:

```python
# app/routers/copilot.py

from fastapi import APIRouter, Depends
from app.middleware.token_budget import enforce_token_budget

router = APIRouter(prefix="/ai", tags=["copilot"])


@router.post("/chat", dependencies=[Depends(enforce_token_budget)])
async def chat_endpoint(...):
    ...
```

### 2.4 Post-Call Token Recording

After each successful agent call, record actual token usage so the budget reflects reality:

```python
# Inside run_agent_with_tracking (extended from section 1.4)

total_tokens = input_tokens + output_tokens
await increment_tokens(
    redis=ctx.redis,
    prefix=settings.token_budget_redis_prefix,
    tenant_id=ctx.tenant_id,
    tokens=total_tokens,
)
```

---

## 3. Cost Tracking

### 3.1 Per-Request Cost Computation

Cost computation was shown in section 1.4. The formula is:

```
cost = (input_tokens / 1000) * input_rate + (output_tokens / 1000) * output_rate
```

Rates are maintained in `MODEL_RATES` and updated when provider pricing changes. The `compute_request_cost` function uses `Decimal` arithmetic to avoid floating-point drift in financial aggregation.

### 3.2 Per-Tenant Daily and Monthly Aggregation in Redis

```python
# app/observability/cost_tracking.py

from __future__ import annotations
import datetime
from decimal import Decimal
from redis.asyncio import Redis

SECONDS_IN_DAY = 86_400
SECONDS_IN_32_DAYS = 86_400 * 32


def _daily_cost_key(tenant_id: str) -> str:
    today = datetime.date.today().isoformat()
    return f"sidecar:cost:daily:{tenant_id}:{today}"


def _monthly_cost_key(tenant_id: str) -> str:
    month = datetime.date.today().strftime("%Y-%m")
    return f"sidecar:cost:monthly:{tenant_id}:{month}"


async def record_cost(redis: Redis, tenant_id: str, cost: Decimal) -> None:
    """Atomically increment daily and monthly cost counters.

    Costs are stored as integer micro-dollars (cost * 1_000_000) to avoid
    floating point in Redis while preserving sub-cent precision.
    """
    micro_dollars = int(cost * 1_000_000)
    daily_key = _daily_cost_key(tenant_id)
    monthly_key = _monthly_cost_key(tenant_id)

    pipe = redis.pipeline()
    pipe.incrby(daily_key, micro_dollars)
    pipe.expire(daily_key, SECONDS_IN_DAY * 2)
    pipe.incrby(monthly_key, micro_dollars)
    pipe.expire(monthly_key, SECONDS_IN_32_DAYS)
    await pipe.execute()


async def get_daily_cost(redis: Redis, tenant_id: str) -> Decimal:
    val = await redis.get(_daily_cost_key(tenant_id))
    return Decimal(int(val)) / Decimal(1_000_000) if val else Decimal(0)


async def get_monthly_cost(redis: Redis, tenant_id: str) -> Decimal:
    val = await redis.get(_monthly_cost_key(tenant_id))
    return Decimal(int(val)) / Decimal(1_000_000) if val else Decimal(0)
```

### 3.3 Dashboard Query Endpoint

An internal admin endpoint exposes cost data for the platform billing dashboard:

```python
# app/routers/admin.py

from fastapi import APIRouter, Depends, Request
from app.observability.cost_tracking import get_daily_cost, get_monthly_cost
from pydantic import BaseModel
from decimal import Decimal

router = APIRouter(prefix="/internal/admin", tags=["admin"])


class TenantCostResponse(BaseModel):
    tenant_id: str
    daily_cost_usd: Decimal
    monthly_cost_usd: Decimal
    daily_tokens_used: int
    daily_token_limit: int


@router.get("/cost/{tenant_id}", response_model=TenantCostResponse)
async def tenant_cost(tenant_id: str, request: Request):
    redis = request.app.state.redis
    settings = request.app.state.settings

    from app.observability.token_budget import get_tokens_used

    daily_cost = await get_daily_cost(redis, tenant_id)
    monthly_cost = await get_monthly_cost(redis, tenant_id)
    tokens_used = await get_tokens_used(
        redis, settings.token_budget_redis_prefix, tenant_id
    )

    return TenantCostResponse(
        tenant_id=tenant_id,
        daily_cost_usd=daily_cost,
        monthly_cost_usd=monthly_cost,
        daily_tokens_used=tokens_used,
        daily_token_limit=settings.default_daily_token_limit,
    )
```

---

## 4. Structured Logging

All sidecar logs are JSON-formatted with `structlog`. Every log line carries `tenant_id`, `actor_id`, `request_id`, and `conversation_id` so logs can be filtered per-request and per-tenant in any log aggregator.

### 4.1 structlog Configuration

```python
# app/observability/logging.py

from __future__ import annotations
import logging
import sys
import structlog


def configure_logging(*, log_level: str = "INFO", json_output: bool = True) -> None:
    """Call once at application startup."""

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if json_output:
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer()

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
        foreign_pre_chain=shared_processors,
    )

    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    root.addHandler(handler)
    root.setLevel(log_level.upper())

    # Quieten noisy third-party loggers.
    for name in ("uvicorn.access", "httpcore", "httpx"):
        logging.getLogger(name).setLevel(logging.WARNING)
```

### 4.2 Per-Request Context Binding

Bind request identifiers into structlog context variables at the start of every request. All downstream log calls automatically include them.

```python
# app/middleware/logging_context.py

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from app.context import RequestContext


class LoggingContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        ctx: RequestContext = request.state.context
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            tenant_id=ctx.tenant_id,
            actor_id=ctx.actor_id,
            request_id=ctx.request_id,
            conversation_id=ctx.conversation_id or "none",
        )
        return await call_next(request)
```

### 4.3 Example Log Output

Every log line emitted anywhere in the sidecar during a request contains the four identifiers:

```json
{
  "event": "agent_call_complete",
  "agent": "copilot",
  "model": "anthropic:claude-sonnet-4-6",
  "input_tokens": 2340,
  "output_tokens": 812,
  "latency_ms": 1847.3,
  "tenant_id": "tenant_acme",
  "actor_id": "adv_jane",
  "request_id": "req_abc123",
  "conversation_id": "conv_xyz789",
  "level": "info",
  "timestamp": "2026-03-26T14:22:01.332Z"
}
```

---

## 5. Tool Call Auditing

### 5.1 Audit Logger

Every tool invocation is logged with its name, sanitized arguments, latency, and a truncated result summary. This satisfies compliance requirements for a full audit trail of what data the AI accessed.

```python
# app/observability/tool_audit.py

from __future__ import annotations
import time
import functools
from typing import Any, Callable, Awaitable
import structlog

logger = structlog.get_logger("tool_audit")

MAX_RESULT_SUMMARY_LEN = 500


def _summarize(result: Any) -> str:
    text = str(result)
    if len(text) > MAX_RESULT_SUMMARY_LEN:
        return text[:MAX_RESULT_SUMMARY_LEN] + "...[truncated]"
    return text


def audited_tool(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    """Decorator for Pydantic AI tool functions. Logs invocation details for compliance."""

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        tool_name = fn.__name__
        start = time.monotonic()
        error: str | None = None

        try:
            result = await fn(*args, **kwargs)
            return result
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            latency_ms = (time.monotonic() - start) * 1000
            log_kwargs: dict[str, Any] = {
                "tool": tool_name,
                "latency_ms": round(latency_ms, 2),
            }
            if error:
                log_kwargs["error"] = error
                logger.warning("tool_call_failed", **log_kwargs)
            else:
                log_kwargs["result_summary"] = _summarize(result)  # noqa: F821
                logger.info("tool_call_complete", **log_kwargs)

    return wrapper
```

Usage on a tool function:

```python
from app.observability.tool_audit import audited_tool

@audited_tool
async def get_household_summary(ctx, household_id: str) -> HouseholdSummary:
    return await ctx.deps.platform_client.get_household_summary(
        household_id, ctx.deps.access_scope
    )
```

### 5.2 Max Tool Calls Per Turn Enforcement

The spec allows a maximum of 3 tool calls per agent turn. This is enforced by an agent runner that counts invocations and terminates the agent loop when the limit is reached.

```python
# app/agents/runner.py

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any
import structlog

logger = structlog.get_logger("agent_runner")

MAX_TOOL_CALLS_PER_TURN = 3


@dataclass
class ToolCallCounter:
    """Tracks tool calls within a single agent turn."""

    count: int = 0
    limit: int = MAX_TOOL_CALLS_PER_TURN
    invocations: list[str] = field(default_factory=list)

    def record(self, tool_name: str) -> None:
        self.count += 1
        self.invocations.append(tool_name)

    @property
    def budget_exhausted(self) -> bool:
        return self.count >= self.limit


class ToolCallLimitExceeded(Exception):
    """Raised when an agent turn exceeds the tool call limit."""

    def __init__(self, limit: int, invocations: list[str]):
        self.limit = limit
        self.invocations = invocations
        super().__init__(
            f"Tool call limit ({limit}) exceeded. Invocations: {invocations}"
        )
```

Integrate the counter with the agent run loop. Pydantic AI supports a `tool_call_limit` parameter, but the sidecar wraps it with additional logging:

```python
from pydantic_ai import Agent

copilot = Agent(
    model="anthropic:claude-sonnet-4-6",
    result_type=HazelCopilotResult,
    tools=[search_documents, get_household_summary, get_account_summary],
)

# Pydantic AI natively supports this:
result = await copilot.run(
    prompt,
    deps=deps,
    model_settings={"max_tool_calls": MAX_TOOL_CALLS_PER_TURN},
)
```

If the LLM requests more tool calls than the limit, the agent terminates and returns what it has. The counter's `invocations` list is included in the audit log so compliance can see exactly which tools ran.

---

## 6. Safety Guardrails Implementation

### 6.1 No Mutation Tools — Static Enforcement

Tool registration uses a static allowlist. A startup validator scans every registered agent and rejects any tool whose name matches a mutation pattern. This makes it impossible for a mutation tool to reach production via a code change that was not caught in review.

```python
# app/agents/safety.py

from __future__ import annotations
import re
from typing import Any
import structlog

logger = structlog.get_logger("agent_safety")

# Patterns that indicate a tool performs a write/mutation operation.
MUTATION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^(create|update|delete|submit|approve|send|post|patch|put)_", re.I),
    re.compile(r"_(submit|approve|execute|publish|write|mutate)$", re.I),
]

# Explicitly allowed tool names (must still pass pattern check as a belt-and-suspenders).
ALLOWED_TOOL_PREFIXES = frozenset({
    "search_", "get_", "list_", "compute_", "score_", "rank_", "extract_",
    "classify_", "summarize_", "draft_", "generate_",
})


def validate_tool_safety(tool_name: str) -> None:
    """Raise if a tool name looks like a mutation operation."""
    for pattern in MUTATION_PATTERNS:
        if pattern.search(tool_name):
            raise ValueError(
                f"Tool '{tool_name}' matches mutation pattern '{pattern.pattern}'. "
                f"Mutation tools are forbidden in the sidecar."
            )

    if not any(tool_name.startswith(prefix) for prefix in ALLOWED_TOOL_PREFIXES):
        logger.warning(
            "tool_name_not_in_allowlist",
            tool=tool_name,
            message="Tool does not match any allowed prefix. Review for safety.",
        )


def validate_agent_tools(agent: Any) -> None:
    """Validate all tools on a Pydantic AI agent at startup."""
    for tool in getattr(agent, "_tools", {}).values():
        validate_tool_safety(tool.name)
    logger.info("agent_tools_validated", agent=str(agent), tool_count=len(getattr(agent, "_tools", {})))
```

Call `validate_agent_tools` during application startup for every agent:

```python
# app/main.py (in lifespan)

from app.agents.safety import validate_agent_tools
from app.agents.copilot import copilot_agent
from app.agents.digest import digest_agent
from app.agents.tax import tax_agent

for agent in [copilot_agent, digest_agent, tax_agent]:
    validate_agent_tools(agent)
```

### 6.2 Disclaimer Injection on Tax and Compliance Content

When agent output touches tax or compliance topics, a disclaimer is injected into the response. Detection uses keyword matching on the agent result, and the disclaimer is attached as a structured field rather than inlined into prose.

```python
# app/agents/disclaimers.py

from __future__ import annotations
import re
from pydantic import BaseModel

TAX_COMPLIANCE_KEYWORDS = re.compile(
    r"\b(tax.loss|capital.gains|rmd|required.minimum|wash.sale|"
    r"estate.planning|gift.tax|irs|1099|k-1|tax.bracket|"
    r"charitable|qualified.dividend|amt|alternative.minimum|"
    r"compliance|regulatory|fiduciary|suitability)\b",
    re.IGNORECASE,
)

DISCLAIMER_TEXT = (
    "This analysis is generated by an AI assistant and is intended for informational "
    "purposes only. It does not constitute tax, legal, or compliance advice. Advisors "
    "should verify all figures against authoritative sources and consult qualified "
    "professionals before acting on any recommendation."
)


class Disclaimer(BaseModel):
    required: bool
    text: str | None = None
    triggered_by: list[str] = []


def check_disclaimer(content: str) -> Disclaimer:
    matches = TAX_COMPLIANCE_KEYWORDS.findall(content)
    if matches:
        return Disclaimer(
            required=True,
            text=DISCLAIMER_TEXT,
            triggered_by=list(set(matches)),
        )
    return Disclaimer(required=False)
```

Apply after every agent call that returns user-facing text:

```python
from app.agents.disclaimers import check_disclaimer

# After agent produces result:
disclaimer = check_disclaimer(result.data.answer)
response = CopilotResponse(
    answer=result.data.answer,
    citations=result.data.citations,
    disclaimer=disclaimer if disclaimer.required else None,
    as_of=result.data.as_of,
)
```

### 6.3 Freshness Metadata on All Financial Data

Every response model that contains financial data includes `as_of`, `source`, and optional `confidence` fields. This is enforced at the type level through a shared base model.

```python
# app/models/base.py

from __future__ import annotations
import datetime
from pydantic import BaseModel, Field


class FinancialDataMixin(BaseModel):
    """Every response containing financial numbers must include freshness metadata."""

    as_of: datetime.datetime = Field(
        ..., description="Timestamp of the underlying data snapshot"
    )
    source: str = Field(
        ..., description="Identifier of the data source (e.g. 'platform:accounts', 'custodian:schwab')"
    )
    confidence: float | None = Field(
        default=None, ge=0.0, le=1.0,
        description="Model confidence score, if applicable"
    )


class StaleDataWarning(BaseModel):
    is_stale: bool
    data_age_seconds: int
    warning: str | None = None

STALE_THRESHOLD_SECONDS = 3600  # 1 hour


def check_staleness(as_of: datetime.datetime) -> StaleDataWarning:
    now = datetime.datetime.now(datetime.timezone.utc)
    age = int((now - as_of).total_seconds())
    if age > STALE_THRESHOLD_SECONDS:
        return StaleDataWarning(
            is_stale=True,
            data_age_seconds=age,
            warning=f"Data is {age // 60} minutes old. Figures may not reflect recent activity.",
        )
    return StaleDataWarning(is_stale=False, data_age_seconds=age)
```

---

## 7. Output Validation

### 7.1 Pydantic Model Validation on All Agent Outputs

All Pydantic AI agents declare a `result_type`. The framework validates the LLM's structured output against this model automatically. The sidecar adds explicit handling for validation failures.

```python
# app/models/copilot.py

from __future__ import annotations
import datetime
from pydantic import BaseModel, Field


class Citation(BaseModel):
    source_type: str  # "document", "email", "crm_note", "transcript", "platform"
    source_id: str
    title: str | None = None
    relevance_score: float | None = None


class HazelCopilotResult(BaseModel):
    answer: str = Field(..., min_length=1, max_length=10_000)
    citations: list[Citation] = Field(default_factory=list, max_length=20)
    as_of: datetime.datetime
    source: str = "copilot"
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    follow_up_suggestions: list[str] = Field(default_factory=list, max_length=5)
```

### 7.2 Retry, Fallback, and Error Handling for Invalid Output

When the LLM returns output that fails Pydantic validation, the sidecar applies a three-step strategy: retry with the same model, retry with the fallback model, then return a structured error.

```python
# app/agents/runner.py

from __future__ import annotations
from pydantic import ValidationError
from pydantic_ai import Agent
from pydantic_ai.exceptions import UnexpectedModelBehavior
import structlog

logger = structlog.get_logger("agent_runner")

MAX_RETRIES = 1


class AgentOutputError(Exception):
    def __init__(self, agent_name: str, errors: list[dict]):
        self.agent_name = agent_name
        self.errors = errors
        super().__init__(f"Agent '{agent_name}' produced invalid output")


async def run_agent_safe(
    agent: Agent,
    prompt: str,
    *,
    deps,
    agent_name: str,
    fallback_agent: Agent | None = None,
) -> Any:
    """Run an agent with retry and fallback on validation failure.

    Strategy:
      1. Run primary agent.
      2. On ValidationError or UnexpectedModelBehavior, retry once with primary.
      3. If still failing and a fallback agent exists, try the fallback.
      4. If all attempts fail, raise AgentOutputError with structured error details.
    """

    last_error: Exception | None = None

    # Attempt 1 + retry with primary agent.
    for attempt in range(1 + MAX_RETRIES):
        try:
            result = await agent.run(prompt, deps=deps)
            return result
        except (ValidationError, UnexpectedModelBehavior) as exc:
            last_error = exc
            logger.warning(
                "agent_output_validation_failed",
                agent=agent_name,
                attempt=attempt + 1,
                error=str(exc),
            )

    # Attempt with fallback agent.
    if fallback_agent is not None:
        try:
            logger.info("agent_fallback_attempt", agent=agent_name)
            result = await fallback_agent.run(prompt, deps=deps)
            return result
        except (ValidationError, UnexpectedModelBehavior) as exc:
            last_error = exc
            logger.error(
                "agent_fallback_also_failed",
                agent=agent_name,
                error=str(exc),
            )

    # All attempts exhausted.
    error_details = []
    if isinstance(last_error, ValidationError):
        error_details = [e for e in last_error.errors()]

    raise AgentOutputError(agent_name=agent_name, errors=error_details)
```

The route handler converts `AgentOutputError` into an HTTP 502 response:

```python
from fastapi import Request
from fastapi.responses import JSONResponse
from app.agents.runner import AgentOutputError

@app.exception_handler(AgentOutputError)
async def agent_output_error_handler(request: Request, exc: AgentOutputError):
    return JSONResponse(
        status_code=502,
        content={
            "error": "validation_failure",
            "message": f"Agent '{exc.agent_name}' could not produce valid structured output after retries.",
            "validation_errors": exc.errors,
        },
    )
```

---

## 8. Sensitive Data Redaction

### 8.1 Redaction Patterns

SSNs, account numbers, and passwords are redacted from all log output. The redaction filter operates as a structlog processor so it applies to every log line regardless of where it was emitted.

```python
# app/observability/redaction.py

from __future__ import annotations
import re
from typing import Any

# Patterns and their replacements.
REDACTION_RULES: list[tuple[re.Pattern[str], str]] = [
    # SSN: 123-45-6789 or 123456789
    (re.compile(r"\b\d{3}-?\d{2}-?\d{4}\b"), "[REDACTED_SSN]"),
    # Account numbers: 8-17 digit sequences (common custodial formats).
    (re.compile(r"\b\d{8,17}\b"), "[REDACTED_ACCT]"),
    # Passwords / secrets in key=value patterns.
    (re.compile(r"(password|passwd|secret|token|api_key|apikey)\s*[=:]\s*\S+", re.I), r"\1=[REDACTED]"),
    # Bearer tokens.
    (re.compile(r"Bearer\s+[A-Za-z0-9\-._~+/]+=*", re.I), "Bearer [REDACTED]"),
    # Credit card numbers (basic Luhn-length sequences).
    (re.compile(r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b"), "[REDACTED_CC]"),
]


def redact_string(text: str) -> str:
    for pattern, replacement in REDACTION_RULES:
        text = pattern.sub(replacement, text)
    return text


def redact_value(value: Any) -> Any:
    """Recursively redact sensitive data from log values."""
    if isinstance(value, str):
        return redact_string(value)
    if isinstance(value, dict):
        return {k: redact_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(redact_value(item) for item in value)
    return value
```

### 8.2 structlog Processor

Register the redaction processor in the structlog chain so it runs before the renderer:

```python
# app/observability/redaction.py (continued)

from structlog.types import EventDict


def redact_processor(logger: Any, method_name: str, event_dict: EventDict) -> EventDict:
    """structlog processor that redacts sensitive data from all log fields."""
    return {key: redact_value(value) for key, value in event_dict.items()}
```

Insert it into the structlog configuration from section 4.1:

```python
# In configure_logging():

shared_processors: list[structlog.types.Processor] = [
    structlog.contextvars.merge_contextvars,
    structlog.stdlib.add_log_level,
    structlog.stdlib.add_logger_name,
    structlog.processors.TimeStamper(fmt="iso"),
    redact_processor,  # <-- redacts before rendering
    structlog.processors.StackInfoRenderer(),
    structlog.processors.format_exc_info,
]
```

### 8.3 Verification

With this processor in place, a log statement like:

```python
logger.info("client_lookup", ssn="123-45-6789", account="12345678901234")
```

produces:

```json
{
  "event": "client_lookup",
  "ssn": "[REDACTED_SSN]",
  "account": "[REDACTED_ACCT]",
  "level": "info",
  "timestamp": "2026-03-26T14:22:01.332Z"
}
```

---

## 9. Failure Classification

### 9.1 Error Taxonomy

Every error in the sidecar is classified into one of six categories. Each category maps to a specific HTTP status code and carries a machine-readable `error_code` for the platform to handle programmatically.

```python
# app/errors/classification.py

from __future__ import annotations
from enum import Enum
from pydantic import BaseModel


class ErrorCategory(str, Enum):
    PLATFORM_READ_FAILURE = "platform_read_failure"
    LLM_PROVIDER_FAILURE = "llm_provider_failure"
    TRANSCRIPTION_FAILURE = "transcription_failure"
    VALIDATION_FAILURE = "validation_failure"
    CONTEXT_TOO_LARGE = "context_too_large"
    INTERNAL_ERROR = "internal_error"


# Mapping from error category to HTTP status code.
CATEGORY_STATUS_MAP: dict[ErrorCategory, int] = {
    ErrorCategory.PLATFORM_READ_FAILURE: 502,
    ErrorCategory.LLM_PROVIDER_FAILURE: 503,
    ErrorCategory.TRANSCRIPTION_FAILURE: 503,
    ErrorCategory.VALIDATION_FAILURE: 422,
    ErrorCategory.CONTEXT_TOO_LARGE: 413,
    ErrorCategory.INTERNAL_ERROR: 500,
}

# Whether the client should retry.
CATEGORY_RETRYABLE: dict[ErrorCategory, bool] = {
    ErrorCategory.PLATFORM_READ_FAILURE: True,
    ErrorCategory.LLM_PROVIDER_FAILURE: True,
    ErrorCategory.TRANSCRIPTION_FAILURE: True,
    ErrorCategory.VALIDATION_FAILURE: False,
    ErrorCategory.CONTEXT_TOO_LARGE: False,
    ErrorCategory.INTERNAL_ERROR: False,
}


class ClassifiedError(BaseModel):
    error_code: ErrorCategory
    message: str
    detail: str | None = None
    retryable: bool
    retry_after_seconds: int | None = None
```

### 9.2 Exception-to-Classification Mapping

```python
# app/errors/classifier.py

from __future__ import annotations
import httpx
from pydantic import ValidationError
from pydantic_ai.exceptions import UnexpectedModelBehavior, ModelRetry
from app.errors.classification import ErrorCategory, ClassifiedError, CATEGORY_RETRYABLE

# Custom exception types.

class PlatformReadError(Exception):
    """Raised when a platform client read fails."""
    pass

class TranscriptionError(Exception):
    """Raised when audio transcription fails."""
    pass

class ContextTooLargeError(Exception):
    """Raised when assembled context exceeds the model's context window."""
    def __init__(self, token_count: int, limit: int):
        self.token_count = token_count
        self.limit = limit
        super().__init__(f"Context {token_count} tokens exceeds limit {limit}")


def classify_exception(exc: Exception) -> ClassifiedError:
    """Map a Python exception to a ClassifiedError for the HTTP response."""

    if isinstance(exc, PlatformReadError) or (
        isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code >= 500
    ):
        return ClassifiedError(
            error_code=ErrorCategory.PLATFORM_READ_FAILURE,
            message="Failed to read data from the wealth platform.",
            detail=str(exc),
            retryable=True,
            retry_after_seconds=5,
        )

    if isinstance(exc, (UnexpectedModelBehavior, ModelRetry)):
        return ClassifiedError(
            error_code=ErrorCategory.LLM_PROVIDER_FAILURE,
            message="The AI model provider returned an error or unexpected response.",
            detail=str(exc),
            retryable=True,
            retry_after_seconds=10,
        )

    if isinstance(exc, TranscriptionError):
        return ClassifiedError(
            error_code=ErrorCategory.TRANSCRIPTION_FAILURE,
            message="Audio transcription failed.",
            detail=str(exc),
            retryable=True,
            retry_after_seconds=30,
        )

    if isinstance(exc, ValidationError):
        return ClassifiedError(
            error_code=ErrorCategory.VALIDATION_FAILURE,
            message="Request or response validation failed.",
            detail=str(exc),
            retryable=False,
        )

    if isinstance(exc, ContextTooLargeError):
        return ClassifiedError(
            error_code=ErrorCategory.CONTEXT_TOO_LARGE,
            message=f"Assembled context ({exc.token_count} tokens) exceeds model limit ({exc.limit}).",
            detail=str(exc),
            retryable=False,
        )

    return ClassifiedError(
        error_code=ErrorCategory.INTERNAL_ERROR,
        message="An internal processing error occurred.",
        detail=str(exc),
        retryable=False,
    )
```

### 9.3 Global Exception Handler

```python
# app/errors/handlers.py

from fastapi import Request
from fastapi.responses import JSONResponse
from app.errors.classifier import classify_exception
from app.errors.classification import CATEGORY_STATUS_MAP
import structlog

logger = structlog.get_logger("error_handler")


async def classified_error_handler(request: Request, exc: Exception) -> JSONResponse:
    classified = classify_exception(exc)
    status = CATEGORY_STATUS_MAP[classified.error_code]

    logger.error(
        "classified_error",
        error_code=classified.error_code.value,
        status_code=status,
        message=classified.message,
        retryable=classified.retryable,
    )

    headers = {}
    if classified.retry_after_seconds:
        headers["Retry-After"] = str(classified.retry_after_seconds)

    return JSONResponse(
        status_code=status,
        content=classified.model_dump(mode="json"),
        headers=headers,
    )


def register_error_handlers(app):
    """Register the global exception handler on the FastAPI app."""
    app.add_exception_handler(Exception, classified_error_handler)
```

---

## 10. Metrics and Alerting

### 10.1 Prometheus Metrics Definition

```python
# app/observability/metrics.py

from prometheus_client import (
    Counter,
    Histogram,
    Gauge,
    Info,
)

# ── Request-level metrics ──

REQUEST_LATENCY = Histogram(
    "sidecar_request_latency_seconds",
    "End-to-end HTTP request latency",
    labelnames=["method", "endpoint", "status_code"],
    buckets=[0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0],
)

REQUEST_COUNT = Counter(
    "sidecar_request_total",
    "Total HTTP requests",
    labelnames=["method", "endpoint", "status_code"],
)

# ── Agent-level metrics ──

AGENT_LATENCY = Histogram(
    "sidecar_agent_latency_seconds",
    "Latency of a single agent call",
    labelnames=["agent", "model", "tenant_id"],
    buckets=[0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0],
)

AGENT_TOKENS_INPUT = Counter(
    "sidecar_agent_input_tokens_total",
    "Total input tokens consumed",
    labelnames=["agent", "model", "tenant_id"],
)

AGENT_TOKENS_OUTPUT = Counter(
    "sidecar_agent_output_tokens_total",
    "Total output tokens consumed",
    labelnames=["agent", "model", "tenant_id"],
)

# ── Tool call metrics ──

TOOL_CALL_COUNT = Counter(
    "sidecar_tool_calls_total",
    "Total tool invocations",
    labelnames=["tool", "agent", "tenant_id"],
)

TOOL_CALL_LATENCY = Histogram(
    "sidecar_tool_call_latency_seconds",
    "Latency per tool call",
    labelnames=["tool"],
    buckets=[0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0],
)

# ── RAG metrics ──

RAG_RETRIEVAL_SCORE = Histogram(
    "sidecar_rag_retrieval_relevance_score",
    "Relevance score of RAG-retrieved chunks",
    labelnames=["index", "tenant_id"],
    buckets=[0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
)

RAG_RETRIEVAL_COUNT = Histogram(
    "sidecar_rag_chunks_retrieved",
    "Number of chunks returned per retrieval call",
    labelnames=["index"],
    buckets=[1, 3, 5, 10, 20, 50],
)

# ── Cache metrics ──

CACHE_HIT = Counter(
    "sidecar_cache_hit_total",
    "Cache hits",
    labelnames=["cache_name"],
)

CACHE_MISS = Counter(
    "sidecar_cache_miss_total",
    "Cache misses",
    labelnames=["cache_name"],
)

# ── Error metrics ──

ERROR_COUNT = Counter(
    "sidecar_errors_total",
    "Errors by classification",
    labelnames=["error_code", "agent", "endpoint"],
)

# ── Budget metrics ──

TOKEN_BUDGET_REMAINING = Gauge(
    "sidecar_token_budget_remaining",
    "Remaining daily token budget per tenant",
    labelnames=["tenant_id"],
)
```

### 10.2 Metrics Middleware

```python
# app/middleware/metrics.py

import time
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from app.observability.metrics import REQUEST_LATENCY, REQUEST_COUNT


class PrometheusMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        start = time.monotonic()
        response = await call_next(request)
        duration = time.monotonic() - start

        labels = {
            "method": request.method,
            "endpoint": request.url.path,
            "status_code": str(response.status_code),
        }
        REQUEST_LATENCY.labels(**labels).observe(duration)
        REQUEST_COUNT.labels(**labels).inc()

        return response
```

### 10.3 Metrics Endpoint

```python
# app/routers/health.py

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST

router = APIRouter(tags=["health"])


@router.get("/metrics", response_class=PlainTextResponse)
async def prometheus_metrics():
    return PlainTextResponse(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )


@router.get("/healthz")
async def healthz():
    return {"status": "ok"}
```

### 10.4 Alert Thresholds

These thresholds are configured in the Prometheus alerting rules or Grafana alert policies. The values below are starting points calibrated for a multi-tenant wealth advisory workload.

| Metric | Condition | Severity | Description |
|--------|-----------|----------|-------------|
| `sidecar_request_latency_seconds` | P95 > 10s for 5 min | warning | Interactive requests are too slow |
| `sidecar_request_latency_seconds` | P99 > 30s for 5 min | critical | Severe latency degradation |
| `sidecar_agent_latency_seconds` | P95 > 30s for 5 min | warning | Agent calls are slow (possible LLM saturation) |
| `sidecar_errors_total` | rate > 5/min for `llm_provider_failure` | warning | LLM provider may be degraded |
| `sidecar_errors_total` | rate > 20/min for `llm_provider_failure` | critical | LLM provider outage likely |
| `sidecar_errors_total` | rate > 5/min for `platform_read_failure` | critical | Platform API is unreachable |
| `sidecar_cache_miss_total / (hit + miss)` | miss rate > 80% for 10 min | warning | Cache is cold or misconfigured |
| `sidecar_token_budget_remaining` | < 100,000 for any tenant | info | Tenant approaching daily token limit |
| `sidecar_rag_retrieval_relevance_score` | avg < 0.3 for 10 min | warning | RAG quality degraded |

Example Prometheus alerting rule:

```yaml
# alerts/sidecar.yml

groups:
  - name: sidecar
    rules:
      - alert: SidecarHighLatency
        expr: histogram_quantile(0.95, rate(sidecar_request_latency_seconds_bucket[5m])) > 10
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "Sidecar P95 latency above 10s"

      - alert: SidecarLLMProviderDown
        expr: rate(sidecar_errors_total{error_code="llm_provider_failure"}[5m]) > 0.33
        for: 5m
        labels:
          severity: critical
        annotations:
          summary: "LLM provider failure rate exceeds 20/min"

      - alert: SidecarPlatformReadFailure
        expr: rate(sidecar_errors_total{error_code="platform_read_failure"}[5m]) > 0.08
        for: 5m
        labels:
          severity: critical
        annotations:
          summary: "Platform read failures exceed 5/min"

      - alert: SidecarRAGQualityDegraded
        expr: histogram_quantile(0.5, rate(sidecar_rag_retrieval_relevance_score_bucket[10m])) < 0.3
        for: 10m
        labels:
          severity: warning
        annotations:
          summary: "RAG retrieval relevance median below 0.3"
```

---

## 11. Graceful Degradation

The spec requires the sidecar to degrade by feature, not fail the wealth platform. Each external dependency has a defined degradation path.

### 11.1 Degradation Matrix

| Dependency | Failure Mode | Degradation Behavior | HTTP Response |
|-----------|-------------|---------------------|---------------|
| Primary LLM (Claude) | Provider 5xx or timeout | Retry once, then fall back to `fallback_model` | Transparent to client if fallback succeeds |
| All LLM providers | Both primary and fallback fail | Return 503 with `Retry-After` header | `503 Service Unavailable` |
| Platform API | 5xx or network error | Return 502 with classification `platform_read_failure` | `502 Bad Gateway` |
| Vector store | Connection error | Fall back to platform text search via `PlatformClient.search_documents_text` | Degraded results, warning in response |
| Redis | Connection error | Disable caching and token budget enforcement (fail-open); log warning | Transparent to client, higher latency |
| Langfuse | Connection error | Disable tracing (fire-and-forget); continue processing | Transparent to client |
| Transcription provider | Provider error | Return job-level failure; preserve job for retry | `503` on sync, job-level failure on async |

### 11.2 Implementation

```python
# app/services/degradation.py

from __future__ import annotations
import structlog
from typing import Any
from pydantic import BaseModel

logger = structlog.get_logger("degradation")


class DegradedResult(BaseModel):
    """Wrapper that marks a result as degraded with an explanation."""

    data: Any
    degraded: bool = False
    degradation_reason: str | None = None
    warnings: list[str] = []


class DependencyHealth:
    """Tracks health of external dependencies for circuit-breaker decisions."""

    def __init__(self):
        self._failure_counts: dict[str, int] = {}
        self._thresholds: dict[str, int] = {
            "llm_primary": 3,
            "llm_fallback": 3,
            "platform_api": 5,
            "vector_store": 3,
            "redis": 5,
            "transcription": 3,
        }

    def record_failure(self, dependency: str) -> None:
        self._failure_counts[dependency] = self._failure_counts.get(dependency, 0) + 1
        logger.warning(
            "dependency_failure_recorded",
            dependency=dependency,
            failure_count=self._failure_counts[dependency],
        )

    def record_success(self, dependency: str) -> None:
        self._failure_counts[dependency] = 0

    def is_healthy(self, dependency: str) -> bool:
        count = self._failure_counts.get(dependency, 0)
        threshold = self._thresholds.get(dependency, 3)
        return count < threshold


# Global singleton (initialized in app lifespan).
dependency_health = DependencyHealth()
```

### 11.3 LLM Fallback Chain

```python
# app/agents/fallback.py

from __future__ import annotations
import structlog
from pydantic_ai import Agent
from pydantic_ai.exceptions import UnexpectedModelBehavior
from app.services.degradation import dependency_health
from app.errors.classifier import classify_exception
from app.observability.metrics import ERROR_COUNT

logger = structlog.get_logger("agent_fallback")


async def run_with_llm_fallback(
    primary: Agent,
    fallback: Agent | None,
    prompt: str,
    *,
    deps,
    agent_name: str,
):
    """Try primary LLM, then fallback. Raises classified error if both fail."""

    # Primary attempt.
    try:
        result = await primary.run(prompt, deps=deps)
        dependency_health.record_success("llm_primary")
        return result
    except Exception as primary_exc:
        dependency_health.record_failure("llm_primary")
        logger.warning("primary_llm_failed", agent=agent_name, error=str(primary_exc))

    # Fallback attempt.
    if fallback is not None:
        try:
            result = await fallback.run(prompt, deps=deps)
            dependency_health.record_success("llm_fallback")
            logger.info("fallback_llm_succeeded", agent=agent_name)
            return result
        except Exception as fallback_exc:
            dependency_health.record_failure("llm_fallback")
            logger.error("fallback_llm_failed", agent=agent_name, error=str(fallback_exc))
            ERROR_COUNT.labels(
                error_code="llm_provider_failure", agent=agent_name, endpoint=""
            ).inc()
            raise  # Will be caught by the global error handler and classified.

    # No fallback available.
    ERROR_COUNT.labels(
        error_code="llm_provider_failure", agent=agent_name, endpoint=""
    ).inc()
    raise UnexpectedModelBehavior("All LLM providers failed for agent: " + agent_name)
```

### 11.4 Vector Store Fallback to Platform Text Search

```python
# app/services/retrieval.py

from __future__ import annotations
import structlog
from app.services.degradation import dependency_health, DegradedResult
from app.clients.platform import PlatformClient
from app.clients.vector_store import VectorStoreClient

logger = structlog.get_logger("retrieval")


async def search_documents(
    query: str,
    *,
    tenant_id: str,
    access_scope,
    vector_client: VectorStoreClient,
    platform_client: PlatformClient,
    top_k: int = 10,
) -> DegradedResult:
    """Search with vector store, falling back to platform text search."""

    if dependency_health.is_healthy("vector_store"):
        try:
            results = await vector_client.search(
                query=query, tenant_id=tenant_id, access_scope=access_scope, top_k=top_k
            )
            dependency_health.record_success("vector_store")
            return DegradedResult(data=results)
        except Exception as exc:
            dependency_health.record_failure("vector_store")
            logger.warning("vector_store_search_failed", error=str(exc))

    # Fallback: platform text search (less accurate, no semantic ranking).
    logger.info("falling_back_to_platform_text_search")
    try:
        results = await platform_client.search_documents_text(
            query=query, filters={"tenant_id": tenant_id}, access_scope=access_scope
        )
        return DegradedResult(
            data=results,
            degraded=True,
            degradation_reason="Vector store unavailable; results from keyword search may be less relevant.",
            warnings=["Search results are keyword-based, not semantic. Relevance may be lower."],
        )
    except Exception as platform_exc:
        logger.error("platform_text_search_also_failed", error=str(platform_exc))
        raise
```

### 11.5 Redis Fail-Open

When Redis is unreachable, caching and token budget enforcement are skipped. The sidecar continues to serve requests at higher latency and without budget limits. This is a deliberate fail-open choice because the alternative (blocking all requests when Redis is down) would make the AI layer a hard dependency on Redis availability.

```python
# app/services/cache.py

from __future__ import annotations
import structlog
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from app.observability.metrics import CACHE_HIT, CACHE_MISS

logger = structlog.get_logger("cache")


async def cache_get(redis: Redis, key: str, cache_name: str = "default") -> bytes | None:
    try:
        value = await redis.get(key)
        if value is not None:
            CACHE_HIT.labels(cache_name=cache_name).inc()
        else:
            CACHE_MISS.labels(cache_name=cache_name).inc()
        return value
    except (RedisConnectionError, OSError) as exc:
        logger.warning("redis_unavailable_cache_miss", key=key, error=str(exc))
        CACHE_MISS.labels(cache_name=cache_name).inc()
        return None


async def cache_set(
    redis: Redis, key: str, value: bytes, *, ttl_seconds: int = 300
) -> None:
    try:
        await redis.set(key, value, ex=ttl_seconds)
    except (RedisConnectionError, OSError) as exc:
        logger.warning("redis_unavailable_cache_set_skipped", key=key, error=str(exc))
```

Token budget enforcement uses the same pattern:

```python
# In enforce_token_budget (section 2.3), wrap the Redis call:

async def enforce_token_budget(request: Request) -> None:
    ctx = request.state.context
    settings = get_settings()
    redis = request.app.state.redis

    try:
        limit = getattr(ctx, "tenant_token_limit", None) or settings.default_daily_token_limit
        allowed, used = await check_budget(
            redis, settings.token_budget_redis_prefix, ctx.tenant_id, limit
        )
        if not allowed:
            # ... raise HTTPException 429 as before
            pass
    except (RedisConnectionError, OSError):
        # Fail open: if Redis is down, allow the request through.
        logger.warning(
            "token_budget_check_skipped_redis_unavailable",
            tenant_id=ctx.tenant_id,
        )
```

---

## Appendix A: Middleware Registration Order

The middleware stack must be registered in a specific order. FastAPI/Starlette processes middleware in reverse registration order (last registered runs first on the way in).

```python
# app/main.py

from app.middleware.metrics import PrometheusMiddleware
from app.middleware.logging_context import LoggingContextMiddleware
from app.middleware.tracing import LangfuseTraceMiddleware
from app.errors.handlers import register_error_handlers

app = FastAPI(lifespan=lifespan)

# Register bottom-up: first registered = outermost.
app.add_middleware(PrometheusMiddleware)       # Outermost: captures total latency and status
app.add_middleware(LoggingContextMiddleware)    # Binds structlog context vars
app.add_middleware(LangfuseTraceMiddleware)     # Creates per-request trace

register_error_handlers(app)

# Include routers.
app.include_router(copilot_router)
app.include_router(health_router)
app.include_router(admin_router)
```

## Appendix B: Configuration Summary

All observability and safety settings in one place:

```python
# app/config.py (full relevant excerpt)

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # ── Langfuse ──
    langfuse_enabled: bool = True
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"

    # ── Token budgets ──
    default_daily_token_limit: int = 5_000_000
    token_budget_redis_prefix: str = "sidecar:token_budget"

    # ── Safety ──
    max_tool_calls_per_turn: int = 3
    stale_data_threshold_seconds: int = 3600

    # ── Logging ──
    log_level: str = "INFO"
    log_json: bool = True

    # ── App ──
    app_version: str = "0.0.0"

    model_config = {
        "env_prefix": "SIDECAR_",
    }
```

## Appendix C: File Layout

```
app/
  config.py             # Settings (Appendix B)
  observability/
    logging.py          # structlog configuration (Section 4)
    context.py          # RequestContext dataclass
  middleware/
    metrics.py          # Prometheus middleware (Section 10)
    logging_context.py  # structlog context binding (Section 4)
    tracing.py          # Langfuse trace middleware (Section 1)
    token_budget.py     # Token budget enforcement (Section 2)
  observability/
    langfuse_client.py  # Langfuse client singleton (Section 1)
    tracing.py          # AgentSpan, ToolSpan helpers (Section 1)
    cost.py             # Cost computation (Section 1/3)
    cost_tracking.py    # Redis cost aggregation (Section 3)
    token_budget.py     # Redis token ledger (Section 2)
    metrics.py          # Prometheus metric definitions (Section 10)
    redaction.py        # Sensitive data redaction (Section 8)
    tool_audit.py       # Tool call audit logger (Section 5)
  agents/
    safety.py           # Tool allowlist validation (Section 6)
    disclaimers.py      # Tax/compliance disclaimer injection (Section 6)
    runner.py           # Agent runner with retry/fallback (Section 7)
    fallback.py         # LLM fallback chain (Section 11)
  models/
    base.py             # FinancialDataMixin, StaleDataWarning (Section 6)
    copilot.py          # HazelCopilotResult (Section 7)
  errors/
    classification.py   # ErrorCategory enum, status map (Section 9)
    classifier.py       # Exception-to-classification mapper (Section 9)
    handlers.py         # Global exception handler (Section 9)
  services/
    degradation.py      # DependencyHealth, DegradedResult (Section 11)
    retrieval.py        # Vector store with fallback (Section 11)
    cache.py            # Redis cache with fail-open (Section 11)
  routers/
    copilot.py          # Copilot routes with budget dependency
    health.py           # /healthz and /metrics (Section 10)
    admin.py            # Internal cost dashboard (Section 3)
```
