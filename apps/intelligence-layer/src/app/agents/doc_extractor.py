"""
app/agents/doc_extractor.py — Document Extractor agent.

Extracts structured data from documents including key-value
fields, tables, and summaries. Flags low-confidence extractions.
"""
from __future__ import annotations

from pydantic_ai import Agent, RunContext

from app.agents.base_deps import AgentDeps
from app.services.llm_client import get_model
from app.agents.registry import registry
from app.models.schemas import DocExtraction
from app.tools.search import search_documents

doc_extractor_agent: Agent[AgentDeps, DocExtraction] = Agent(
    model=get_model("extraction"),
    output_type=DocExtraction,
    tools=[
        search_documents,
    ],
    retries=2,
    defer_model_check=True,
)


@doc_extractor_agent.system_prompt
async def build_doc_extractor_prompt(
    ctx: RunContext[AgentDeps],
) -> str:
    """Build the document extractor system prompt."""
    return "\n".join([
        "You are a document data extraction assistant for "
        "wealth management firms.",
        "",
        "## Context",
        f"- Tenant: {ctx.deps.tenant_id}",
        f"- Advisor: {ctx.deps.actor_id}",
        "",
        "## Instructions",
        "- Extract structured data from documents into "
        "key-value fields.",
        "- Identify and extract tabular data preserving "
        "row/column structure.",
        "- Produce a concise summary of the document's "
        "contents.",
        "- Assign an overall confidence score for the "
        "extraction quality.",
        "- Flag individual fields with low confidence in "
        "the warnings list.",
        "- When a value is ambiguous or partially legible, "
        "include the best guess and add a warning.",
    ])


registry.register(
    "doc_extractor",
    doc_extractor_agent,
    tier="extraction",
    description=(
        "Extracts structured key-value fields, tables, and "
        "summaries from documents."
    ),
)
