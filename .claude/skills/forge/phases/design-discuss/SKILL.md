---
name: design-discuss
description: Use when there are unresolved design questions from discovery and exploration that need human decisions before architecture can begin.
---

# Design Discussion

Turn unresolved planning questions into explicit constraints for the architect.

## Process

1. Read `.forge/features/{slug}/discovery.md` and `.forge/features/{slug}/exploration.md`.
2. Collect questions from discovery plus any exploration findings that imply a human decision.
3. Categorize each question as:
   - blocking
   - informing
4. The human answers are already present in the prompt from the orchestrator. Resolve them into concrete decisions and explicit instructions for the architect.
5. Write `.forge/features/{slug}/design-discuss.md` using the `Design Discussion` contract in `skills/forge/phases/references/planning-artifacts.md`.

## CRITICAL

- Record the human's answer faithfully instead of rewriting it into a stronger decision.
- Leave deferred or unanswered blocking questions in `## Open Questions`.
- Respond with only `done - N questions resolved` or `blocked - N questions unresolved`.
- Keep findings in the artifact, not in your response.
