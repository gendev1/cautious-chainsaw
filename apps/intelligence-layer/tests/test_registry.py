"""Tests for agent registry."""
from __future__ import annotations

import pytest

from app.agents.registry import AgentEntry, AgentRegistry


def test_register_and_get() -> None:
    """T1: Register and get returns correct AgentEntry."""
    reg = AgentRegistry()
    # Use a mock agent (any object works for the registry)
    mock_agent = object()
    reg.register("test_agent", mock_agent, tier="copilot", description="A test agent")

    entry = reg.get("test_agent")
    assert isinstance(entry, AgentEntry)
    assert entry.name == "test_agent"
    assert entry.agent is mock_agent
    assert entry.tier == "copilot"
    assert entry.description == "A test agent"


def test_get_unknown_raises_key_error() -> None:
    """T2: get raises KeyError for unknown agent."""
    reg = AgentRegistry()
    with pytest.raises(KeyError, match="Unknown agent 'nonexistent'"):
        reg.get("nonexistent")


def test_duplicate_register_raises_value_error() -> None:
    """Registering the same name twice raises ValueError."""
    reg = AgentRegistry()
    reg.register("dup", object(), tier="batch")
    with pytest.raises(ValueError, match="already registered"):
        reg.register("dup", object(), tier="batch")


def test_list_agents() -> None:
    """list_agents returns all registered entries."""
    reg = AgentRegistry()
    reg.register("a1", object(), tier="copilot")
    reg.register("a2", object(), tier="batch")
    entries = reg.list_agents()
    assert len(entries) == 2
    names = {e.name for e in entries}
    assert names == {"a1", "a2"}


def test_full_registry_has_12_agents() -> None:
    """T3: After importing app.agents, registry has 12 agents."""
    from app.agents import registry

    agents = registry.list_agents()
    assert len(agents) == 12, (
        f"Expected 12 agents, got {len(agents)}: "
        f"{[a.name for a in agents]}"
    )
