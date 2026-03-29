---
name: forge-phase-runner
description: Use when Forge needs to execute one pipeline phase, apply the shared state and validation pattern, select the correct phase skill and artifact contract, and manage retries between implement and verify.
---

# Forge Phase Runner

Run one Forge phase at a time. This skill owns the shared lifecycle: mark running, spawn the right phase skill, validate its artifact, update state, and decide whether the pipeline advances, waits for a gate, or loops.

## Process

1. Mark the phase as running:
   - `./skills/forge/tools/forge-state set "{slug}" "{phase}" running`
2. Use the phase registry below to choose the phase skill, prompt inputs, expected artifact, and validation contract.
3. Run the phase skill with only the context it needs.
4. Validate the artifact immediately after the phase completes.
   - On validation failure, mark the phase `fail` and stop for human review.
5. Mark the phase `done` only after validation succeeds.
6. Hand off to `forge-user-gates` if the phase has a human checkpoint. Otherwise move straight to the next phase.

## Phase Registry

| Phase | Skill | Required inputs | Artifact | Validation | Notes |
|---|---|---|---|---|---|
| discover | `discover` | feature, slug, optional issue reference | `discovery.md` | `validate-artifact ... "## Requirements"` | gate on open questions |
| explore | `explore` | slug plus `discovery.md` | `exploration.md` | `validate-artifact ... "## Structural Patterns" "## Key Files"` | no gate |
| design-discuss | `design-discuss` | slug plus verbatim human answers | `design-discuss.md` | `validate-artifact ... "## Resolved Decisions"` | stop if skill returns `blocked - ...` |
| architect | `architect` | slug plus planning artifacts | `architecture.md` | `validate-artifact ... "## Recommendation"` | gate on approach choice |
| handoff | `handoff` | slug, chosen approach, mode | `implementation-context.md` or `issues.md` | artifact must exist | direct mode continues, GitHub mode may create issues |
| spike | `spike` | slug plus `implementation-context.md` | `spike-report.md` | artifact exists unless phase is skipped | load `docker-test-infra` only for real external dependencies |
| write-tests | `write-tests` | slug plus planning artifacts and optional spike output | tests plus `test-manifest.md` | `validate-artifact ... "## Test File Checksums"` | tests should fail before implementation |
| implement | `implement` | slug plus context, tests, and optional `verify-report.md` on retry | `impl-manifest.md` | artifact exists | only phase allowed to modify source code |
| verify | `verify` | slug plus all execution artifacts | `verify-report.md` | artifact exists and skill returns `pass` or `fail - ...` | decides retry loop or completion |

## Retry Policy

1. If `verify` returns `pass`, finish the run and let `forge-user-gates` deliver the completion summary.
2. If `verify` returns `fail - ...`, summarize the report through `forge-user-gates`.
3. Retry `implement -> verify` up to 3 times for normal failures.
4. Treat repeated failure on the same test with the same error as a likely spec contradiction and hand it back to the human instead of looping forever.
5. If Docker-backed infrastructure was started, reset it between retries with `docker compose down -v` followed by `docker compose up -d --wait`.

## CRITICAL

- Use `skills/forge/tools/validate-artifact` for every artifact contract that defines required sections.
- Keep all phase artifacts inside `.forge/features/{slug}/`.
- Mark failures in Forge state before stopping.
- Do not let phase agents bypass their artifact contract or write ad hoc planning files elsewhere.
- `implement` may edit product code, but it must not modify tests.
- `verify` is the final authority on pass or fail.
