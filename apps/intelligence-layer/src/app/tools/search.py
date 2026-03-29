"""
app/tools/search.py — Search tools for agents.

All tools receive RunContext[AgentDeps] and delegate to the
read-only PlatformClient search methods.
"""
from __future__ import annotations

from pydantic_ai import RunContext

from app.agents.base_deps import AgentDeps


async def search_documents(
    ctx: RunContext[AgentDeps],
    query: str,
    *,
    client_id: str | None = None,
    document_type: str | None = None,
    max_results: int = 8,
) -> list:
    """Search across uploaded documents, tax returns, estate plans, and statements.

    Use this when the advisor asks about document contents, uploaded files,
    tax returns, or estate planning documents.
    """
    return await ctx.deps.platform.search_documents_text(
        query=query,
        filters={
            "client_id": client_id,
            "document_type": document_type,
            "limit": max_results,
        },
        access_scope=ctx.deps.access_scope,
    )


async def search_emails(
    ctx: RunContext[AgentDeps],
    query: str,
    *,
    client_id: str | None = None,
    max_results: int = 8,
) -> list:
    """Search across email communications.

    Use this when the advisor asks about email history, correspondence,
    or prior communications with a client.
    """
    return await ctx.deps.platform.search_emails(
        query=query,
        filters={"client_id": client_id, "limit": max_results},
        access_scope=ctx.deps.access_scope,
    )


async def search_crm_notes(
    ctx: RunContext[AgentDeps],
    query: str,
    *,
    client_id: str | None = None,
    max_results: int = 8,
) -> list:
    """Search CRM notes and activity records.

    Use this when the advisor asks about past interactions, meeting notes,
    or client relationship history.
    """
    return await ctx.deps.platform.search_crm_notes(
        query=query,
        filters={"client_id": client_id, "limit": max_results},
        access_scope=ctx.deps.access_scope,
    )


async def search_meeting_transcripts(
    ctx: RunContext[AgentDeps],
    query: str,
    *,
    client_id: str | None = None,
    max_results: int = 8,
) -> list:
    """Search meeting transcripts.

    Use this when the advisor asks about what was discussed in
    previous meetings with a client.
    """
    return await ctx.deps.platform.search_meeting_transcripts(
        query=query,
        filters={"client_id": client_id, "limit": max_results},
        access_scope=ctx.deps.access_scope,
    )
