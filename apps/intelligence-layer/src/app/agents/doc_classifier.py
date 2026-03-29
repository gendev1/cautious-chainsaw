"""
app/agents/doc_classifier.py — Document Classifier agent.

Classifies documents by type (tax_return, estate_plan,
trust_document, etc.), extracts entities, and suggests
client/household association.
"""
from __future__ import annotations

from pydantic_ai import Agent, RunContext

from app.agents.base_deps import AgentDeps
from app.agents.registry import registry
from app.models.schemas import DocClassification
from app.tools.search import search_documents

doc_classifier_agent: Agent[AgentDeps, DocClassification] = (
    Agent(
        model="anthropic:claude-haiku-4-5",
        output_type=DocClassification,
        tools=[
            search_documents,
        ],
        retries=2,
    defer_model_check=True,
    )
)


@doc_classifier_agent.system_prompt
async def build_doc_classifier_prompt(
    ctx: RunContext[AgentDeps],
) -> str:
    """Build the document classifier system prompt."""
    return "\n".join([
        "You are a document classification assistant for "
        "wealth management firms.",
        "",
        "## Context",
        f"- Tenant: {ctx.deps.tenant_id}",
        f"- Advisor: {ctx.deps.actor_id}",
        "",
        "## Instructions",
        "- Classify documents into one of these types: "
        "tax_return, estate_plan, trust_document, "
        "insurance_policy, statement, correspondence, other.",
        "- Extract named entities found in the document "
        "(people, organizations, account numbers, dates).",
        "- Suggest the most likely client and household "
        "association based on document contents.",
        "- Provide a confidence score for the classification.",
        "- When confidence is below 0.7, explain what made "
        "classification ambiguous.",
    ])


registry.register(
    "doc_classifier",
    doc_classifier_agent,
    tier="extraction",
    description=(
        "Classifies documents by type, extracts entities, "
        "and suggests client/household association."
    ),
)
