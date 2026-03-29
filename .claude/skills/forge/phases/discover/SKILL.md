---
name: discover
description: Use when analyzing a feature request, GitHub issue, or user description to extract structured requirements, decisions, constraints, and open questions.
---

# Discovery

Extract the feature request into a clean planning artifact. This phase is structured extraction, not architecture.

## Process

1. Read the raw request and the slug supplied by Forge.
2. If a GitHub issue is referenced, read the full issue thread with `gh issue view`.
3. Extract only four categories:
   - requirements
   - decisions already made
   - constraints
   - open questions that need a human answer
4. Write `.forge/features/{slug}/discovery.md` using the `Discovery` contract in `skills/forge/phases/references/planning-artifacts.md`.

## CRITICAL

- Read the source request before writing anything.
- Keep interpretation minimal. Capture what is stated, not speculative solutions.
- Write only `.forge/features/{slug}/discovery.md`.
- Respond with only `done` or `done - N open questions need answers`.
- Keep findings in the artifact, not in your response.
