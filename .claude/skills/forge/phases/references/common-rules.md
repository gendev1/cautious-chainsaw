# Common Phase Rules

Use these rules across all Forge phase skills.

## Artifact Discipline

- Read upstream Forge artifacts from `.forge/features/{slug}/` before writing a downstream artifact.
- Write only the artifact that belongs to the current phase.
- Keep all phase artifacts under `.forge/features/{slug}/`.
- Put findings in the artifact, not in the agent response.

## Response Discipline

- Keep the response to the orchestrator short and machine-friendly.
- Use only the explicit status strings documented by the phase skill.
- Do not paste summaries, code, or report contents into the response body.

## Scope Discipline

- Do not create ad hoc side documents outside the named Forge artifacts.
- Preserve the distinction between planning artifacts and execution artifacts.
- Treat `.forge/features/{slug}/` as agent-to-agent transfer space, not user-facing output.
