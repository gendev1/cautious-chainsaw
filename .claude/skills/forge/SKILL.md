---
name: forge
description: Use when the user wants to build or change a feature end-to-end in one conversation, from requirement extraction through tests, implementation, verification, and optional GitHub issue handoff. Orchestrates the Forge pipeline, resumes from `.forge/state.json`, asks the human for decisions at the right gates, and delegates the detailed work to narrower Forge skills.
argument-hint: feature description
user-invokable: true
---

# Forge

You are the thin orchestrator for Forge. Keep user interaction here, and push operational detail into the dedicated Forge skills.

## Delegation Map

- `forge-bootstrap` owns startup, mode detection, state initialization, and resume selection.
- `forge-phase-runner` owns phase execution, validation, retries, and state transitions.
- `forge-user-gates` owns every human checkpoint, phrasing, and artifact summarization.
- `discover`, `explore`, `design-discuss`, `architect`, `handoff`, `spike`, `write-tests`, `implement`, and `verify` own the phase artifacts.
- `docker-test-infra` and `ast-grep` are opt-in reference skills. Load them only when a phase actually needs them.
- `status` and `cleanup` are standalone utilities, not part of the main run.

## Run Loop

1. Load `forge-bootstrap` immediately.
2. Resolve the feature description, `{slug}`, run mode, environment, and resume point.
3. For the current phase, load `forge-phase-runner` and execute only that phase.
4. Before asking the human anything, load `forge-user-gates`.
5. Repeat until verify passes, the human redirects the run, or Forge reaches a final stop.

## Pipeline

| Order | Phase | Producing skill | Outcome |
|---|---|---|---|
| 1 | discover | `discover` | requirements, constraints, open questions |
| 2 | explore | `explore` | codebase map, patterns, key files |
| 3 | design-discuss | `design-discuss` | resolved design decisions |
| 4 | architect | `architect` | candidate approaches and recommendation |
| 5 | handoff | `handoff` | implementation context or GitHub issue plan |
| 6 | spike | `spike` | verified dependency behavior |
| 7 | write-tests | `write-tests` | failing tests and test manifest |
| 8 | implement | `implement` | code changes and implementation manifest |
| 9 | verify | `verify` | pass/fail report and retry decision |

## Orchestration Rules

- Treat `.forge/features/{slug}/` as agent-to-agent workspace. Summarize artifacts for the user instead of dumping them.
- Let phase skills write their own artifacts. Keep orchestration logic, retries, and summaries outside those phase artifacts.
- Planning ends after `handoff`. Execution begins at `spike`.
- `implement` is the only phase allowed to change product code.
- `verify` is the only phase allowed to decide whether the run passes or loops.

## Hard Constraints

- Never run `git init`.
- Never edit `.forge/state.json` directly. Use `skills/forge/tools/forge-state`.
- Never add anything under `.forge/` to git.
- Never run `gh` unless bootstrap put the run in GitHub mode.
- Never show raw artifact contents to the user. Summarize only the decision-relevant parts.
- Always tear down Forge-started Docker infrastructure on completion or final stop.
- Prefer `ast-grep` when it is available. Allow the phase skills to fall back when they already define a fallback.
