# Exploration Contract

File: `.forge/features/{slug}/exploration.md`

Required sections:

- `## Most Similar Feature`
- `## Architecture Map`
- `## Structural Patterns`
- `## Key Files`

## Most Similar Feature

Include:

- the closest existing implementation
- why it is relevant
- what to reuse or avoid

## Architecture Map

Map only the layers relevant to the requested feature. Typical layers include:

- routes
- handlers
- services
- database access

## Structural Patterns

Record patterns for:

- error handling
- API or route shape
- test structure
- naming

For each pattern, include:

- the matching rule or text pattern
- at least one real code example
- whether it came from `ast-grep` or grep fallback

If fewer than 3 distinct examples exist:

- mark the pattern `[insufficient-sample: N matches]`
- keep it in the artifact
- note that verify should treat it as non-binding

If `ast-grep` is unavailable:

- mark the pattern `[grep-fallback]`
- use text patterns rather than YAML ast-grep rules

## Key Files

List the files that matter for:

- reference reading
- expected edits
- expected tests
