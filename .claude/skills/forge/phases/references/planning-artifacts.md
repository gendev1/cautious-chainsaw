# Planning Artifact Contracts

Use these contracts when writing planning-phase artifacts.

## Discovery

File: `.forge/features/{slug}/discovery.md`

Required sections:

- `## Requirements`
- `## Decisions Already Made`
- `## Constraints`
- `## Open Questions`

Keep requirements concrete and implementation-agnostic. Open questions should be checklist items the human can answer.

Response:

- `done`
- `done - N open questions need answers`

## Design Discussion

File: `.forge/features/{slug}/design-discuss.md`

Required sections:

- `## Resolved Decisions`
- `## Open Questions`
- `## Summary for Architect`

For each resolved question, record:

- category: blocking or informing
- decision
- rationale when available
- explicit constraint for the architect

Response:

- `done - N questions resolved`
- `blocked - N questions unresolved`

## Architecture

File: `.forge/features/{slug}/architecture.md`

Required sections:

- one section per candidate approach
- `## Recommendation`
- `## Task Breakdown (recommended approach)`

Each approach should include:

- a short summary
- references to similar code or patterns
- trade-offs
- dependency-ordered tasks

Response:

- `done`

## Handoff Direct

File: `.forge/features/{slug}/implementation-context.md`

Required sections:

- `## Chosen Approach`
- `## Implementation Order`
- `## External Dependencies`
- `## Test Cases`
- `## Scope Boundaries`

Rules:

- each step must be a vertical slice
- every external dependency the feature relies on must be listed
- scope boundaries must be concrete enough for verify to enforce

Response:

- `done`

## Handoff GitHub

File: `.forge/features/{slug}/issues.md`

Each issue should include:

- title
- story points
- in scope
- out of scope
- steps
- test cases
- definition of done
- `Blocked by` when dependency ordering matters

Response:

- `done`
