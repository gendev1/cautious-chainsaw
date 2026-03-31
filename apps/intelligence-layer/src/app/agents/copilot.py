"""
app/agents/copilot.py — Hazel Copilot agent.

The primary conversational agent for wealth advisors, capable of
answering questions about clients, portfolios, and documents with
full access to all platform and search tools.
"""
from __future__ import annotations

from dataclasses import dataclass

from pydantic_ai import Agent, RunContext

from app.agents.base_deps import AgentDeps
from app.agents.registry import registry
from app.models.schemas import HazelCopilot
from app.tools.platform import (
    get_account_summary,
    get_client_timeline,
    get_constructed_portfolio,
    get_household_summary,
    get_order_projection,
    get_report_snapshot,
    get_transfer_case,
)
from app.services.llm_client import get_model
from app.tools.search import (
    search_crm_notes,
    search_documents,
    search_emails,
    search_meeting_transcripts,
)


@dataclass
class CopilotDeps(AgentDeps):
    """Extended deps with the currently active client/household/portfolio."""

    active_client_id: str | None = None
    active_household_id: str | None = None
    active_portfolio_job_id: str | None = None


copilot_agent: Agent[CopilotDeps, HazelCopilot] = Agent(
    model=get_model("copilot"),
    output_type=HazelCopilot,
    tools=[
        get_household_summary,
        get_account_summary,
        get_client_timeline,
        get_transfer_case,
        get_order_projection,
        get_report_snapshot,
        get_constructed_portfolio,
        search_documents,
        search_emails,
        search_crm_notes,
        search_meeting_transcripts,
    ],
    retries=2,
    defer_model_check=True,
)


@copilot_agent.system_prompt
async def build_copilot_prompt(
    ctx: RunContext[CopilotDeps],
) -> str:
    """Build the Hazel copilot system prompt."""
    parts = [
        "You are Hazel, an AI assistant for wealth advisors.",
        "",
        "## Context",
        f"- Tenant: {ctx.deps.tenant_id}",
        f"- Advisor (actor): {ctx.deps.actor_id}",
    ]
    if ctx.deps.active_client_id:
        parts.append(
            f"- Active client: {ctx.deps.active_client_id}"
        )
    if ctx.deps.active_household_id:
        parts.append(
            f"- Active household: {ctx.deps.active_household_id}"
        )
    if ctx.deps.active_portfolio_job_id:
        parts.extend([
            f"- Active portfolio proposal: job_id={ctx.deps.active_portfolio_job_id}",
            "  (Use get_constructed_portfolio to load details when "
            "the advisor asks about this portfolio.)",
        ])
    parts.extend([
        "",
        "## Guidelines",
        "- Always cite your sources using the citation schema.",
        "- Include a confidence score reflecting data quality "
        "and completeness.",
        "- Suggest concrete follow-up actions when relevant.",
        "- Propose follow-up questions to deepen the analysis.",
        "- NEVER fabricate financial numbers, balances, or "
        "performance figures. If data is unavailable, say so.",
        "- When uncertain, state your confidence level and "
        "suggest how the advisor can verify the information.",
        "",
        "## Portfolio Construction",
        "- When an active portfolio proposal is in context, "
        "use get_constructed_portfolio to load it and answer "
        "questions about holdings, weights, scores, and rationale.",
        "- You can explain why specific stocks were picked, "
        "compare sector weights, discuss factor scores, and "
        "suggest modifications.",
        "- If the advisor wants to modify the portfolio, suggest "
        "they use the /portfolio/construct endpoint with a "
        "prior_job_id to revise it.",
        "",
        "## Document Handling",
        "- When a specific document ID is provided, use "
        "extract_document to get structured fields. This is "
        "faster and more accurate than searching.",
        "- Use search_documents only for open-ended queries "
        "across many documents (e.g. 'find estate planning "
        "documents').",
        "- Extracted fields persist in this conversation — "
        "no need to re-extract on follow-up questions.",
    ])
    return "\n".join(parts)


@copilot_agent.tool
async def extract_document(
    ctx: RunContext[CopilotDeps],
    document_id: str,
) -> dict:
    """Extract structured fields from a specific document.

    Use this when the advisor uploads or references a known
    document (W-2, 1099, K-1, account statement, etc.).
    Returns classified type and extracted key-value fields
    that persist in conversation for follow-up questions.

    Prefer this over search_documents when a specific
    document ID is available.
    """
    content = await ctx.deps.platform.get_document_content(
        document_id, ctx.deps.access_scope
    )
    metadata = (
        await ctx.deps.platform.get_document_metadata(
            document_id, ctx.deps.access_scope
        )
    )

    from app.agents.doc_classifier import (
        doc_classifier_agent,
    )

    classify_result = await doc_classifier_agent.run(
        f"Classify this document:\n"
        f"Filename: {metadata.filename}\n"
        f"Content preview:\n{content[:3000]}",
        deps=ctx.deps,
    )
    classification = classify_result.output

    from app.agents.doc_extractor import (
        doc_extractor_agent,
    )

    extract_result = await doc_extractor_agent.run(
        f"Extract structured fields from this "
        f"{classification.document_type} document:\n\n"
        f"Filename: {metadata.filename}\n"
        f"Document type: "
        f"{classification.document_type}\n"
        f"Content:\n{content[:8000]}",
        deps=ctx.deps,
    )
    extraction = extract_result.output

    return {
        "document_id": document_id,
        "filename": metadata.filename,
        "document_type": classification.document_type,
        "classification_confidence": (
            classification.confidence
        ),
        "extracted_fields": extraction.extracted_fields,
        "tables": extraction.tables,
        "summary": extraction.summary,
        "extraction_confidence": extraction.confidence,
        "warnings": extraction.warnings,
    }


registry.register(
    "copilot",
    copilot_agent,
    tier="copilot",
    description=(
        "Primary conversational copilot for wealth advisors "
        "with full platform and search access."
    ),
)
