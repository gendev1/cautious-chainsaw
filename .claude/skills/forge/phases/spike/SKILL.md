---
name: spike
description: Use when a feature has external dependencies (APIs, databases, npm packages) whose behavior needs to be verified before implementation. Produces spike-report.md with real observed behavior and mock templates.
---

# Spike

Verify the behavior of every external dependency before implementation relies on it.

## Process

1. Read:
   - `.forge/features/{slug}/implementation-context.md`
   - `.forge/features/{slug}/exploration.md`
2. Use `## External Dependencies` in `implementation-context.md` as the dependency manifest.
   - if it says `None — pure internal logic. Skip spike phase.`, stop with `done - no external dependencies`
3. Cross-check the manifest with:

```bash
./skills/forge/tools/detect-dependencies .
```

4. Create `.forge/features/{slug}/scratch/` and write small runnable probes for each dependency.
5. Run the probes and capture:
   - happy-path behavior
   - error behavior
   - edge cases
   - type or contract details that affect mocking
6. Write `.forge/features/{slug}/spike-report.md` using the `Spike Report` contract in `skills/forge/phases/references/execution-artifacts.md`.
7. Before finishing, check freshness:

```bash
./skills/forge/tools/check-spike-freshness .forge/features/{slug}/spike-report.md .
```

## CRITICAL

- Actually run the probes whenever local execution is possible.
- If behavior comes from docs rather than observation, label it as documentation-derived.
- Keep scratch scripts as living contract artifacts under `.forge/features/{slug}/scratch/`.
- Include mock templates because later test writing depends on them.
- Respond with only `done - spiked N dependencies` or `done - no external dependencies`.
