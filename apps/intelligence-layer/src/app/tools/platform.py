"""
app/tools/platform.py — Platform API read tools for agents.

All tools receive RunContext[AgentDeps] and delegate to the
read-only PlatformClient. No mutations.
"""
from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from app.agents.base_deps import AgentDeps


async def get_household_summary(
    ctx: RunContext[AgentDeps],
    household_id: str,
) -> Any:
    """Retrieve a summary of a household including AUM, accounts, and members.

    Use this when the advisor asks about a household's overall financial
    picture, total assets, or account composition.
    """
    return await ctx.deps.platform.get_household_summary(
        household_id=household_id,
        access_scope=ctx.deps.access_scope,
    )


async def get_account_summary(
    ctx: RunContext[AgentDeps],
    account_id: str,
) -> Any:
    """Retrieve detailed summary for a single account.

    Use this when the advisor asks about a specific account's balance,
    holdings, performance, drift, or unrealized gains/losses.
    """
    return await ctx.deps.platform.get_account_summary(
        account_id=account_id,
        access_scope=ctx.deps.access_scope,
    )


async def get_client_timeline(
    ctx: RunContext[AgentDeps],
    client_id: str,
    days: int = 30,
) -> list:
    """Retrieve recent activity timeline for a client.

    Use this when the advisor asks about recent client interactions,
    account changes, or activity history.
    """
    return await ctx.deps.platform.get_client_timeline(
        client_id=client_id,
        access_scope=ctx.deps.access_scope,
        days=days,
    )


async def get_transfer_case(
    ctx: RunContext[AgentDeps],
    transfer_id: str,
) -> Any:
    """Retrieve transfer case details.

    Use this when the advisor asks about the status of an ACAT,
    wire, or ACH transfer.
    """
    return await ctx.deps.platform.get_transfer_case(
        transfer_id=transfer_id,
        access_scope=ctx.deps.access_scope,
    )


async def get_order_projection(
    ctx: RunContext[AgentDeps],
    account_id: str,
) -> Any:
    """Retrieve projected orders for an account.

    Use this when the advisor asks about pending rebalance proposals,
    projected trades, or order status.
    """
    return await ctx.deps.platform.get_order_projection(
        account_id=account_id,
        access_scope=ctx.deps.access_scope,
    )


async def get_report_snapshot(
    ctx: RunContext[AgentDeps],
    report_id: str,
) -> Any:
    """Retrieve a report snapshot.

    Use this when the advisor asks about a previously generated report
    or performance snapshot.
    """
    return await ctx.deps.platform.get_report_snapshot(
        report_id=report_id,
        access_scope=ctx.deps.access_scope,
    )


async def get_advisor_clients(
    ctx: RunContext[AgentDeps],
) -> list:
    """Retrieve the list of clients assigned to the current advisor.

    Use this when generating digests or reports that span all of
    the advisor's client relationships.
    """
    return await ctx.deps.platform.get_advisor_clients(
        advisor_id=ctx.deps.actor_id,
        access_scope=ctx.deps.access_scope,
    )
