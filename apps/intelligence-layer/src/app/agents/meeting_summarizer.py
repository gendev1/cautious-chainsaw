"""
app/agents/meeting_summarizer.py — Meeting Summarizer agent.

Summarizes meetings with executive summary, key topics, action
items, follow-up drafts, client sentiment, and CRM sync payloads.
"""
from __future__ import annotations

from pydantic_ai import Agent, RunContext

from app.agents.base_deps import AgentDeps
from app.services.llm_client import get_model
from app.agents.registry import registry
from app.models.schemas import MeetingSummary
from app.tools.platform import (
    get_account_summary,
    get_household_summary,
)
from app.tools.search import search_crm_notes

meeting_summarizer_agent: Agent[AgentDeps, MeetingSummary] = (
    Agent(
        model=get_model("copilot"),
        output_type=MeetingSummary,
        tools=[
            get_household_summary,
            get_account_summary,
            search_crm_notes,
        ],
        retries=2,
    defer_model_check=True,
    )
)


@meeting_summarizer_agent.system_prompt
async def build_meeting_summarizer_prompt(
    ctx: RunContext[AgentDeps],
) -> str:
    """Build the meeting summarizer system prompt."""
    return "\n".join([
        "You are a meeting summarization assistant for wealth "
        "advisors.",
        "",
        "## Context",
        f"- Tenant: {ctx.deps.tenant_id}",
        f"- Advisor: {ctx.deps.actor_id}",
        "",
        "## Instructions",
        "- Produce a concise executive summary (3-5 sentences).",
        "- Break down key topics with speaker attribution and "
        "decisions made.",
        "- Extract all action items with assignees and "
        "deadlines.",
        "- Draft follow-up emails for key participants.",
        "- Assess client sentiment (positive, neutral, "
        "concerned) when determinable.",
        "- Generate CRM sync payloads so the platform can "
        "automatically update records.",
    ])


registry.register(
    "meeting_summarizer",
    meeting_summarizer_agent,
    tier="copilot",
    description=(
        "Summarizes meetings with action items, follow-up "
        "drafts, and CRM sync payloads."
    ),
)
