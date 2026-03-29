---
name: forge-bootstrap
description: Use when Forge is starting or resuming a feature run and needs to derive the feature slug, detect git, GitHub, and Docker availability, initialize Forge state, handle active-feature conflicts, and determine the correct resume phase before the pipeline continues.
---

# Forge Bootstrap

Prepare the run before any phase agent starts. This skill owns startup, mode selection, state conflict handling, and resume selection.

## Process

1. Get the feature description from `$ARGUMENTS`. If it is missing, ask the human for it before doing anything else.
2. Slugify the feature name and ensure `.forge/features/{slug}/` exists.
3. Detect the local environment:
   - Git: `git rev-parse --git-dir 2>/dev/null`
   - Docker: `docker compose version 2>/dev/null`
   - GitHub mode only when git is available and the request references a GitHub issue
   - If GitHub mode is selected, verify `gh auth status` and `gh repo view`
4. Initialize Forge state:
   - `./skills/forge/tools/forge-state init "{slug}"`
   - Exit `0` means the feature is ready
   - Exit `2` means another feature is active, so ask the human whether to switch
5. Determine the correct resume point:
   - `./skills/forge/tools/forge-state resume "{slug}" plan`
   - `./skills/forge/tools/forge-state resume "{slug}" execute`
   - If planning is incomplete, resume from the reported planning phase
   - If planning is complete and execution is incomplete, resume from the reported execution phase
   - If both are complete, tell the human the feature already finished and stop
6. Keep this runtime summary in working memory for the rest of the run:
   - feature description
   - slug
   - mode: `direct` or `github`
   - git available: true or false
   - docker available: true or false
   - resume phase

## CRITICAL

- Never run `git init`.
- Never edit `.forge/state.json` directly.
- Never run `gh` outside GitHub mode.
- Ask the human before switching away from an already-active feature.
- Route any human-facing wording through `forge-user-gates` so gate messaging stays consistent.
