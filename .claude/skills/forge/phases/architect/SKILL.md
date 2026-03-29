---
name: architect
description: Use when designing implementation approaches for a feature after discovery and exploration are complete. Produces 2-3 approaches with trade-offs and a recommendation.
---

# Architecture

Design plausible implementation approaches constrained by the real codebase and the resolved human decisions.

## Process

1. Read:
   - `.forge/features/{slug}/discovery.md`
   - `.forge/features/{slug}/exploration.md`
   - `.forge/features/{slug}/design-discuss.md` when it exists
2. Produce 2-3 approaches.
3. For every approach:
   - follow the structural patterns from exploration
   - reference specific existing code as templates
   - call out any deliberate deviation from existing patterns
   - include a dependency-ordered task breakdown
4. Recommend one approach and explain why it is the best trade-off.
5. Write `.forge/features/{slug}/architecture.md` using the `Architecture` contract in `skills/forge/phases/references/planning-artifacts.md`.

## CRITICAL

- Respect all constraints from `design-discuss.md`, especially `## Summary for Architect`.
- Keep the recommendation grounded in actual codebase patterns, not generic preferences.
- Write only `.forge/features/{slug}/architecture.md`.
- Respond with only `done`.
