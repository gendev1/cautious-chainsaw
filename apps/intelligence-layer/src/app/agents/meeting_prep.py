"""
app/agents/meeting_prep.py — Meeting Prep agent.

Generates comprehensive meeting preparation briefs with client
context, portfolio highlights, talking points, and open items.
"""
from __future__ import annotations

from pydantic_ai import Agent, RunContext

from app.agents.base_deps import AgentDeps
from app.services.llm_client import get_model
from app.agents.registry import registry
from app.models.schemas import MeetingPrep
from app.tools.platform import (
    get_account_summary,
    get_client_timeline,
    get_household_summary,
)
from app.tools.search import (
    search_crm_notes,
    search_meeting_transcripts,
)

meeting_prep_agent: Agent[AgentDeps, MeetingPrep] = Agent(
    model=get_model("copilot"),
    output_type=MeetingPrep,
    tools=[
        get_household_summary,
        get_account_summary,
        get_client_timeline,
        search_crm_notes,
        search_meeting_transcripts,
    ],
    retries=2,
    defer_model_check=True,
)


@meeting_prep_agent.system_prompt
async def build_meeting_prep_prompt(
    ctx: RunContext[AgentDeps],
) -> str:
    """Build the meeting prep system prompt."""
    return "\n".join([
        "You are a meeting preparation assistant for wealth "
        "advisors.",
        "",
        "## Context",
        f"- Tenant: {ctx.deps.tenant_id}",
        f"- Advisor: {ctx.deps.actor_id}",
        "",
        "## Instructions",
        "- Generate a comprehensive meeting preparation brief.",
        "- Include relevant client context: recent life events, "
        "account changes, and prior interactions.",
        "- Summarize portfolio highlights: performance, drift, "
        "allocation, and notable positions.",
        "- Propose talking points tailored to the client's "
        "current situation.",
        "- List open items and unresolved issues from prior "
        "meetings or CRM notes.",
        "- Suggest questions the advisor might want to ask.",
    ])


registry.register(
    "meeting_prep",
    meeting_prep_agent,
    tier="copilot",
    description=(
        "Generates meeting preparation briefs with client "
        "context, portfolio highlights, and talking points."
    ),
)
