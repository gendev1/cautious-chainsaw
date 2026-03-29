---
name: explore
description: Use when you need to deeply analyze a codebase for structural patterns, conventions, architecture, and key files relevant to a feature being built.
---

# Exploration

Map the codebase around the requested feature and turn repeated structures into reusable contracts.

## Process

1. Read `.forge/features/{slug}/discovery.md` first so the exploration stays feature-specific.
2. Detect whether `ast-grep` is available:
   - if yes, use structural queries
   - if no, fall back to `grep -rn` and mark every derived pattern as `[grep-fallback]`
3. Identify:
   - the most similar existing implementation
   - the architecture layers involved in this feature
   - structural patterns for error handling, API or route shape, test structure, and naming
   - key files that matter for reading, editing, or reuse
4. For every structural pattern, count distinct matches.
   - if fewer than 3 matches exist, mark it `[insufficient-sample: N matches]`
   - keep the pattern, but note that verify should treat it as non-binding
5. Write `.forge/features/{slug}/exploration.md` using `skills/forge/phases/references/exploration-contract.md`.

## CRITICAL

- Include real code snippets and file references from the repository, not abstract descriptions.
- If `ast-grep` is unavailable, do not halt. Fall back and mark the weaker contract clearly.
- Keep patterns tied to the requested feature instead of cataloging the entire repo.
- Write only `.forge/features/{slug}/exploration.md`.
- Respond with only `done`.
