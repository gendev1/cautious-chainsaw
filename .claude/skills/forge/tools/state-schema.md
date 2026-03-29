# State Management

All state manipulation goes through `forge-state` in this directory. Never edit `.forge/state.json` directly.

## Commands

```bash
forge-state init <slug>                    # Create feature, migrate old schema, set active
                                           # Exit 0: ok. Exit 2: conflict (prints active slug)
forge-state set <slug> <phase> <status>    # running|done|fail|blocked. "done" auto-sets completed_at
forge-state get <slug> [phase]             # One phase status, or all phases as JSON
forge-state resume <slug> plan|execute     # Next phase to run, or "complete"
forge-state active                         # Print active slug (exit 1 if none)
forge-state remove <slug>                  # Remove feature, reassign active
```

## Status values

- `null` — not started
- `"running"` — agent currently executing
- `"done"` — completed successfully
- `"fail"` — validation rejected artifact
- `"blocked"` — design-discuss only, unresolved blocking questions

## Phase groups (used by `resume`)

- `plan`: discover, explore, design-discuss, architect, handoff
- `execute`: spike, test, implement, verify

The `/forge` orchestrator calls `resume` twice (once per group) to find where to pick up.

## Schema migration

The script auto-migrates old single-feature schema (top-level `"slug"` key) to multi-feature schema on `init`. No manual migration needed.
