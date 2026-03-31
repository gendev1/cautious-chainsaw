"""
app/agents/email_drafter.py — Email Drafter agent.

Drafts professional emails on behalf of wealth advisors,
matching client communication preferences and tone.
"""
from __future__ import annotations

from pydantic_ai import Agent, RunContext

from app.agents.base_deps import AgentDeps
from app.services.llm_client import get_model
from app.agents.registry import registry
from app.models.schemas import EmailDraft
from app.tools.platform import get_client_timeline
from app.tools.search import search_crm_notes, search_emails

email_drafter_agent: Agent[AgentDeps, EmailDraft] = Agent(
    model=get_model("copilot"),
    output_type=EmailDraft,
    tools=[
        search_emails,
        search_crm_notes,
        get_client_timeline,
    ],
    retries=2,
    defer_model_check=True,
)


@email_drafter_agent.system_prompt
async def build_email_drafter_prompt(
    ctx: RunContext[AgentDeps],
) -> str:
    """Build the email drafter system prompt."""
    return "\n".join([
        "You are a professional email drafter for wealth "
        "advisors.",
        "",
        "## Context",
        f"- Tenant: {ctx.deps.tenant_id}",
        f"- Advisor: {ctx.deps.actor_id}",
        "",
        "## Instructions",
        "- Draft clear, professional emails appropriate for "
        "wealth management communication.",
        "- Review prior email threads and CRM notes to match "
        "the client's preferred communication style and tone.",
        "- Use the client timeline to reference recent events "
        "or interactions naturally.",
        "- NEVER send emails directly. All drafts are for the "
        "advisor to review, edit, and send.",
        "- Include appropriate subject lines and CC suggestions "
        "when relevant.",
    ])


registry.register(
    "email_drafter",
    email_drafter_agent,
    tier="copilot",
    description=(
        "Drafts professional emails matching client "
        "communication preferences."
    ),
)
