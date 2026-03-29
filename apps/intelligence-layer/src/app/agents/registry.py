"""
app/agents/registry.py — Central registry of all Pydantic AI agents.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic_ai import Agent


@dataclass
class AgentEntry:
    """Metadata wrapper around a registered agent."""

    name: str
    agent: Agent[Any, Any]
    tier: str
    description: str


class AgentRegistry:
    """Central registry of all Pydantic AI agents.

    Agents are registered at import time and looked up by name
    at request time.
    """

    def __init__(self) -> None:
        self._agents: dict[str, AgentEntry] = {}

    def register(
        self,
        name: str,
        agent: Agent[Any, Any],
        *,
        tier: str,
        description: str = "",
    ) -> None:
        if name in self._agents:
            raise ValueError(f"Agent '{name}' is already registered")
        self._agents[name] = AgentEntry(
            name=name,
            agent=agent,
            tier=tier,
            description=description,
        )

    def get(self, name: str) -> AgentEntry:
        try:
            return self._agents[name]
        except KeyError as err:
            raise KeyError(
                f"Unknown agent '{name}'. "
                f"Registered: {list(self._agents)}"
            ) from err

    def list_agents(self) -> list[AgentEntry]:
        return list(self._agents.values())


# Module-level singleton
registry = AgentRegistry()
