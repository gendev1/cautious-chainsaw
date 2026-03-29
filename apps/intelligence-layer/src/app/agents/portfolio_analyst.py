"""
app/agents/portfolio_analyst.py — Portfolio Analyst agent.

Analyzes portfolio allocation, drift, performance, and risk
metrics. Provides actionable recommendations.
"""
from __future__ import annotations

from pydantic_ai import Agent, RunContext

from app.agents.base_deps import AgentDeps
from app.agents.registry import registry
from app.models.schemas import PortfolioAnalysis
from app.tools.platform import (
    get_account_summary,
    get_household_summary,
    get_order_projection,
    get_report_snapshot,
)

portfolio_analyst_agent: Agent[AgentDeps, PortfolioAnalysis] = (
    Agent(
        model="anthropic:claude-sonnet-4-6",
        output_type=PortfolioAnalysis,
        tools=[
            get_household_summary,
            get_account_summary,
            get_order_projection,
            get_report_snapshot,
        ],
        retries=2,
    defer_model_check=True,
    )
)


@portfolio_analyst_agent.system_prompt
async def build_portfolio_analyst_prompt(
    ctx: RunContext[AgentDeps],
) -> str:
    """Build the portfolio analyst system prompt."""
    return "\n".join([
        "You are a portfolio analysis assistant for wealth "
        "advisors.",
        "",
        "## Context",
        f"- Tenant: {ctx.deps.tenant_id}",
        f"- Advisor: {ctx.deps.actor_id}",
        "",
        "## Instructions",
        "- Analyze portfolio allocation across asset classes "
        "and compare to target model.",
        "- Calculate and report drift from the model portfolio.",
        "- Summarize year-to-date and trailing performance.",
        "- Compute risk metrics: volatility, Sharpe ratio, "
        "drawdown, and beta where data is available.",
        "- Provide actionable recommendations: rebalance "
        "trades, risk reduction, or opportunity capture.",
        "- Flag any warnings such as concentration risk or "
        "excessive cash drag.",
    ])


registry.register(
    "portfolio_analyst",
    portfolio_analyst_agent,
    tier="copilot",
    description=(
        "Analyzes portfolio allocation, drift, performance, "
        "and risk with actionable recommendations."
    ),
)
