"""Tests for tool safety — no mutation methods, no direct HTTP calls."""
from __future__ import annotations

import ast
import pathlib

FORBIDDEN_PREFIXES = [
    "create_", "update_", "delete_", "submit_",
    "approve_", "send_", "post_", "put_", "patch_",
    "execute_", "mutate_", "write_",
]

TOOLS_DIR = pathlib.Path("src/app/tools")


def test_no_mutation_methods_in_tools() -> None:
    """T7: No tool file calls a mutation method on any dependency."""
    if not TOOLS_DIR.exists():
        return
    for py_file in TOOLS_DIR.glob("*.py"):
        if py_file.name == "__init__.py":
            continue
        tree = ast.parse(py_file.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                for prefix in FORBIDDEN_PREFIXES:
                    assert not node.attr.startswith(prefix), (
                        f"{py_file.name} calls '{node.attr}' which looks "
                        f"like a mutation method (prefix '{prefix}'). "
                        f"Tools must be read-only."
                    )


def test_no_httpx_direct_calls_in_tools() -> None:
    """T8: No tool file makes direct httpx calls."""
    if not TOOLS_DIR.exists():
        return
    for py_file in TOOLS_DIR.glob("*.py"):
        if py_file.name == "__init__.py":
            continue
        source = py_file.read_text()
        assert "httpx.post(" not in source, (
            f"{py_file.name} has direct httpx.post"
        )
        assert "httpx.put(" not in source, (
            f"{py_file.name} has direct httpx.put"
        )
        assert "httpx.patch(" not in source, (
            f"{py_file.name} has direct httpx.patch"
        )
        assert "httpx.delete(" not in source, (
            f"{py_file.name} has direct httpx.delete"
        )
