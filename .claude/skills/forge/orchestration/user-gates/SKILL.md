---
name: forge-user-gates
description: Use when Forge needs to talk to the human at a decision point: missing startup context, active-feature conflicts, discovery questions, design decisions, architecture choice, handoff confirmation, verify retries, or final completion.
---

# Forge User Gates

Handle every human checkpoint. Your job is to summarize the artifact, ask the smallest useful question, and capture the answer without exposing raw working files.

## Process

1. Keep the question set tight. Ask only what is needed to unblock the next phase.
2. Read the relevant artifact before speaking to the human.
3. Summarize in plain language. Do not paste the artifact.
4. Preserve the human's answer verbatim when the next phase needs the exact wording.
5. Write any resolved answers back into the relevant Forge artifact when the orchestration flow expects it.

## Gates

### Startup

- If the feature description is missing, ask for it directly.
- If `forge-state init` reports an active feature conflict, ask whether to keep the current feature or switch to the new slug.
- If bootstrap says the feature is already complete, say so and stop.

### Discovery

- Read `discovery.md`.
- If `## Open Questions` contains unchecked items, present them together in one message.
- After the human answers, record the answers in `discovery.md` so later phases do not have to rediscover them.

### Design Discussion

- Combine unresolved discovery questions with exploration findings that imply a human decision.
- Split them into:
  - blocking: architecture cannot proceed without the answer
  - informing: architecture can proceed, but the answer improves quality
- Ask all blocking and informing questions in one message.

### Architecture Choice

- Read `architecture.md`.
- Summarize each approach in 2-3 sentences.
- State the recommendation and why it is preferred.
- Ask the human to pick an approach or accept the recommendation.

### Handoff

- In direct mode, announce that planning is complete and execution is starting.
- In GitHub mode, summarize the issue breakdown after issue creation.

### Verify

- On pass, summarize the result: tests passing, scope clean, ready to commit or already committed.
- On failure, summarize:
  - how many tests failed
  - any scope violations
  - any structural contract violations
- If the failure looks like a spec contradiction, ask whether to adjust the spec or try a different implementation approach.
- If it is a normal failure and retries remain, tell the human Forge is retrying implementation.
- After the retry limit is reached, stop and ask how to proceed.

## CRITICAL

- Never show raw artifact contents to the human.
- Keep blocking questions grouped together instead of dribbling them out one by one.
- Do not rewrite the human's decision into stronger language than they used.
- Keep summaries decision-oriented: what changed, what is blocked, and what choice is needed next.
