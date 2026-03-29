---
name: handoff
description: Use when architecture is decided and you need to produce actionable implementation artifacts — either an implementation-context.md for direct execution or GitHub issues.
---

# Handoff

Turn the chosen architecture into execution-ready work items.

## Process

1. Read:
   - `.forge/features/{slug}/discovery.md`
   - `.forge/features/{slug}/exploration.md`
   - `.forge/features/{slug}/architecture.md`
2. Use the chosen approach supplied by the orchestrator.
3. If the run is in direct mode, write `.forge/features/{slug}/implementation-context.md` using the `Handoff Direct` contract in `skills/forge/phases/references/planning-artifacts.md`.
4. If the run is in GitHub mode, write `.forge/features/{slug}/issues.md` using the `Handoff GitHub` contract in `skills/forge/phases/references/planning-artifacts.md`.

## CRITICAL

- Every direct-mode implementation step must be a vertical slice that can be tested end-to-end.
- Every listed dependency must appear in `## External Dependencies`; the spike phase will trust that table.
- Keep scope boundaries explicit enough for the verify phase to enforce.
- Write only the handoff artifact for the selected mode.
- Respond with only `done`.
