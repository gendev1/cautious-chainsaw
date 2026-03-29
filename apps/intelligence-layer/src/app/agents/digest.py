"""
app/agents/digest.py — Daily Digest agent.

Generates a personalized daily briefing for an advisor covering
meetings, emails, tasks, alerts, and suggested actions.
"""
from __future__ import annotations

from pydantic_ai import Agent, RunContext

from app.agents.base_deps import AgentDeps
from app.agents.registry import registry
from app.models.schemas import DailyDigest
from app.tools.calendar_adapter import get_todays_meetings
from app.tools.crm_adapter import get_pending_tasks
from app.tools.email_adapter import get_unread_priority_emails
from app.tools.platform import (
    get_advisor_clients,
    get_household_summary,
)

digest_agent: Agent[AgentDeps, DailyDigest] = Agent(
    model="anthropic:claude-haiku-4-5",
    output_type=DailyDigest,
    tools=[
        get_advisor_clients,
        get_household_summary,
        get_todays_meetings,
        get_unread_priority_emails,
        get_pending_tasks,
    ],
    retries=2,
    defer_model_check=True,
)


@digest_agent.system_prompt
async def build_digest_prompt(
    ctx: RunContext[AgentDeps],
) -> str:
    """Build the daily digest system prompt."""
    return "\n".join([
        "You are a daily briefing generator for wealth advisors.",
        "",
        "## Context",
        f"- Tenant: {ctx.deps.tenant_id}",
        f"- Advisor: {ctx.deps.actor_id}",
        "",
        "## Instructions",
        "Generate a personalized daily briefing that includes:",
        "- Today's meetings with client context.",
        "- Priority unread emails requiring attention.",
        "- Pending CRM tasks and their deadlines.",
        "- Alerts for significant portfolio or client events.",
        "- Suggested actions to start the day effectively.",
        "",
        "Organize the briefing into clear sections. "
        "Prioritize items by urgency. Keep summaries concise.",
    ])


registry.register(
    "digest",
    digest_agent,
    tier="batch",
    description=(
        "Generates personalized daily briefings with meetings, "
        "emails, tasks, alerts, and suggested actions."
    ),
)
