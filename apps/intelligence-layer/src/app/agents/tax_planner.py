"""
app/agents/tax_planner.py — Tax Planner agent.

Analyzes tax situations, identifies opportunities such as
tax-loss harvesting, Roth conversions, and charitable QCDs,
and models scenarios. Always includes a disclaimer.
"""
from __future__ import annotations

from pydantic_ai import Agent, RunContext

from app.agents.base_deps import AgentDeps
from app.agents.registry import registry
from app.models.schemas import TaxPlan
from app.tools.platform import (
    get_account_summary,
    get_household_summary,
)
from app.tools.search import search_documents

tax_planner_agent: Agent[AgentDeps, TaxPlan] = Agent(
    model="anthropic:claude-opus-4-6",
    output_type=TaxPlan,
    tools=[
        get_household_summary,
        get_account_summary,
        search_documents,
    ],
    retries=2,
    defer_model_check=True,
)


@tax_planner_agent.system_prompt
async def build_tax_planner_prompt(
    ctx: RunContext[AgentDeps],
) -> str:
    """Build the tax planner system prompt."""
    return "\n".join([
        "You are a tax planning analysis assistant for wealth "
        "advisors.",
        "",
        "## Context",
        f"- Tenant: {ctx.deps.tenant_id}",
        f"- Advisor: {ctx.deps.actor_id}",
        "",
        "## Instructions",
        "- Analyze the client's tax situation using household "
        "and account data.",
        "- Identify tax optimization opportunities including:",
        "  - Tax-loss harvesting across taxable accounts.",
        "  - Roth conversion analysis with income projections.",
        "  - Charitable giving strategies including QCDs.",
        "  - Gain deferral and asset location optimization.",
        "- Model at least two scenarios comparing outcomes.",
        "- List assumptions and trade-offs for each scenario.",
        "- Include warnings for compliance-sensitive items.",
        "",
        "## Disclaimer",
        "ALWAYS include this disclaimer in your response: "
        "'This is decision support, not tax advice. Consult a "
        "qualified tax professional before taking action.'",
    ])


registry.register(
    "tax_planner",
    tax_planner_agent,
    tier="analysis",
    description=(
        "Analyzes tax situations and models optimization "
        "scenarios with disclaimers."
    ),
)
