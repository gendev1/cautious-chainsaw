"""
app/agents/task_extractor.py — Task Extractor agent.

Extracts actionable tasks from meeting transcripts, emails,
and CRM notes, assigning priority and due dates.
"""
from __future__ import annotations

from pydantic_ai import Agent, RunContext

from app.agents.base_deps import AgentDeps
from app.agents.registry import registry
from app.models.schemas import ExtractedTask
from app.tools.search import (
    search_crm_notes,
    search_emails,
    search_meeting_transcripts,
)

task_extractor_agent: Agent[AgentDeps, list[ExtractedTask]] = (
    Agent(
        model="anthropic:claude-haiku-4-5",
        output_type=list[ExtractedTask],
        tools=[
            search_meeting_transcripts,
            search_emails,
            search_crm_notes,
        ],
        retries=2,
    defer_model_check=True,
    )
)


@task_extractor_agent.system_prompt
async def build_task_extractor_prompt(
    ctx: RunContext[AgentDeps],
) -> str:
    """Build the task extractor system prompt."""
    return "\n".join([
        "You are a task extraction assistant for wealth "
        "advisors.",
        "",
        "## Context",
        f"- Tenant: {ctx.deps.tenant_id}",
        f"- Advisor: {ctx.deps.actor_id}",
        "",
        "## Instructions",
        "- Extract actionable tasks from meeting transcripts, "
        "emails, and CRM notes.",
        "- Assign each task a priority (high, medium, low) "
        "based on urgency and client impact.",
        "- Estimate due dates when timing cues are available.",
        "- Identify the responsible party (assignee) when "
        "mentioned.",
        "- Link tasks to their source (meeting, email, or "
        "CRM note) and associated client.",
        "- Only extract genuine action items, not general "
        "discussion points.",
    ])


registry.register(
    "task_extractor",
    task_extractor_agent,
    tier="batch",
    description=(
        "Extracts actionable tasks from meeting transcripts "
        "and emails with priority and due dates."
    ),
)
