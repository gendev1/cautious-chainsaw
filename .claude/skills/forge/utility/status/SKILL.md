---
name: status
description: Show current pipeline progress for the active forge feature. Use when the user asks about progress, status, or what phase we're in.
user-invokable: true
---

# Forge Status

Show where the active feature is in the pipeline.

**State tool**: `skills/forge/tools/forge-state`

## Process

1. Get the active feature:
   ```bash
   slug=$(./skills/forge/tools/forge-state active)
   # exit 1 → "No active forge feature. Run /forge to start."
   ```
   Then read all phases:
   ```bash
   ./skills/forge/tools/forge-state get "$slug"
   # prints all phases as JSON
   ```

2. **Show the active feature first**, display the progress table:

   | Phase | Artifact file |
   |-------|---------------|
   | discover | discovery.md |
   | explore | exploration.md |
   | design-discuss | design-discuss.md |
   | architect | architecture.md |
   | handoff | implementation-context.md |
   | spike | spike-report.md |
   | test | test-manifest.md |
   | implement | impl-manifest.md |
   | verify | verify-report.md |

   Output format:
   ```
   Active feature: {slug}
   Started: {features.{slug}.started_at}

   Phase           Status     Completed At
   --------------  ---------  ----------------------
   discover        done       2026-03-13T10:05:00Z
   explore         done       2026-03-13T10:12:00Z
   design-discuss  done       2026-03-13T10:15:00Z
   architect       done       2026-03-13T10:20:00Z
   handoff         done       2026-03-13T10:25:00Z
   spike           done       2026-03-13T10:28:00Z
   test            done       2026-03-13T10:35:00Z
   implement       running    —
   verify          —          —
   ```

3. **If there are other features** in `state.features` besides the active one, list them below the table:
   ```
   Other features in progress:
   - {slug2}: {N/9 phases complete} (started {started_at})
   - {slug3}: {N/9 phases complete} (started {started_at})
   ```
   If there is only one feature, omit this section.

Status symbols:
- `done` — completed successfully
- `running` — agent currently active
- `fail` — agent returned failure or validation hook rejected artifact
- `blocked` — design discussion has unresolved questions
- `—` — not yet started

## CRITICAL

- Do NOT spawn any agents
- Do NOT write any files
- Output the progress table directly in your response
- Respond with the table and a one-line summary: "Active feature {slug}: {N/9} phases complete." plus "{M} other feature(s) in progress." if applicable, or "No active feature."
