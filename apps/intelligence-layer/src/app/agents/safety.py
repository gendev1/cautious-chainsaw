"""
app/agents/safety.py — Tool safety validation.
"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger("sidecar.agent_safety")

MUTATION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"^(create|update|delete|submit|approve"
        r"|send|post|patch|put)_",
        re.I,
    ),
    re.compile(
        r"_(submit|approve|execute|publish"
        r"|write|mutate)$",
        re.I,
    ),
]

ALLOWED_TOOL_PREFIXES = frozenset(
    {
        "search_",
        "get_",
        "list_",
        "compute_",
        "score_",
        "rank_",
        "extract_",
        "classify_",
        "summarize_",
        "draft_",
        "generate_",
    }
)


def validate_tool_safety(tool_name: str) -> None:
    """Raise if a tool name looks like a mutation."""
    for pattern in MUTATION_PATTERNS:
        if pattern.search(tool_name):
            raise ValueError(
                f"Tool '{tool_name}' matches mutation "
                f"pattern '{pattern.pattern}'. "
                "Mutation tools are forbidden."
            )

    if not any(
        tool_name.startswith(prefix)
        for prefix in ALLOWED_TOOL_PREFIXES
    ):
        logger.warning(
            "tool_name_not_in_allowlist: %s",
            tool_name,
        )


def validate_agent_tools(agent: Any) -> None:
    """Validate all tools on a Pydantic AI agent."""
    tools = getattr(agent, "_tools", {})
    for tool in tools.values():
        validate_tool_safety(tool.name)
    logger.info(
        "agent_tools_validated: %d tools",
        len(tools),
    )
