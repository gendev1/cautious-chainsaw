"""
app/agents/firm_reporter.py — Firm-Wide Reporter agent.

Generates firm-wide analytical reports covering AUM, accounts,
households, highlights, and concerns.
"""
from __future__ import annotations

from pydantic_ai import Agent, RunContext

from app.agents.base_deps import AgentDeps
from app.agents.registry import registry
from app.models.schemas import FirmWideReport
from app.tools.platform import (
    get_advisor_clients,
    get_household_summary,
    get_report_snapshot,
)

firm_reporter_agent: Agent[AgentDeps, FirmWideReport] = Agent(
    model="anthropic:claude-opus-4-6",
    output_type=FirmWideReport,
    tools=[
        get_advisor_clients,
        get_household_summary,
        get_report_snapshot,
    ],
    retries=2,
    defer_model_check=True,
)


@firm_reporter_agent.system_prompt
async def build_firm_reporter_prompt(
    ctx: RunContext[AgentDeps],
) -> str:
    """Build the firm reporter system prompt."""
    return "\n".join([
        "You are a firm-wide reporting assistant for wealth "
        "management firms.",
        "",
        "## Context",
        f"- Tenant: {ctx.deps.tenant_id}",
        f"- Advisor: {ctx.deps.actor_id}",
        "",
        "## Instructions",
        "- Generate comprehensive firm-wide analytical reports.",
        "- Include total AUM, account counts, and household "
        "counts.",
        "- Highlight positive trends: new clients, AUM growth, "
        "and strong performance.",
        "- Flag concerns: client attrition, underperforming "
        "accounts, and compliance issues.",
        "- Provide key metrics with period-over-period "
        "comparisons when data is available.",
        "- Structure the report for executive consumption.",
    ])


registry.register(
    "firm_reporter",
    firm_reporter_agent,
    tier="analysis",
    description=(
        "Generates firm-wide analytical reports covering AUM, "
        "accounts, highlights, and concerns."
    ),
)
