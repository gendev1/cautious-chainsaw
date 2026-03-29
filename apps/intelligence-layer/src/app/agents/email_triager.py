"""
app/agents/email_triager.py — Email Triager agent.

Classifies and prioritizes incoming emails by urgency and
category to help advisors focus on what matters.
"""
from __future__ import annotations

from pydantic_ai import Agent, RunContext

from app.agents.base_deps import AgentDeps
from app.agents.registry import registry
from app.models.schemas import TriagedEmail
from app.tools.search import search_crm_notes, search_emails

email_triager_agent: Agent[AgentDeps, list[TriagedEmail]] = Agent(
    model="anthropic:claude-haiku-4-5",
    output_type=list[TriagedEmail],
    tools=[
        search_emails,
        search_crm_notes,
    ],
    retries=2,
    defer_model_check=True,
)


@email_triager_agent.system_prompt
async def build_email_triager_prompt(
    ctx: RunContext[AgentDeps],
) -> str:
    """Build the email triager system prompt."""
    return "\n".join([
        "You are an email triage assistant for wealth advisors.",
        "",
        "## Context",
        f"- Tenant: {ctx.deps.tenant_id}",
        f"- Advisor: {ctx.deps.actor_id}",
        "",
        "## Instructions",
        "- Classify and prioritize incoming emails.",
        "- Assign an urgency level (high, medium, low) based "
        "on sender importance, content, and time sensitivity.",
        "- Assign a category: client_request, meeting_followup, "
        "compliance, marketing, internal, or other.",
        "- Cross-reference CRM notes to identify known clients "
        "and enrich context.",
        "- Provide a brief summary and suggested action for "
        "each email.",
    ])


registry.register(
    "email_triager",
    email_triager_agent,
    tier="batch",
    description=(
        "Classifies and prioritizes incoming emails by "
        "urgency and category."
    ),
)
