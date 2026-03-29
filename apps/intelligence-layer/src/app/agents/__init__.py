"""
Pydantic AI agent definitions.

Importing this module triggers registration of all agents
in the global registry.
"""
# Importing each module triggers its register() call.
from app.agents import (  # noqa: F401
    copilot,
    digest,
    doc_classifier,
    doc_extractor,
    email_drafter,
    email_triager,
    firm_reporter,
    meeting_prep,
    meeting_summarizer,
    portfolio_analyst,
    task_extractor,
    tax_planner,
)
from app.agents.registry import registry

__all__ = ["registry"]
