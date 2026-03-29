---
name: write-tests
description: Use when you need to write failing tests from a spec before any implementation exists. RED phase of red-green-verify — tests SHOULD fail because no implementation exists yet.
---

# Write Tests

Write failing tests first, using the codebase's real test style and the feature spec from handoff.

## Process

1. Read:
   - `.forge/features/{slug}/implementation-context.md` or `.forge/features/{slug}/issues.md`
   - `.forge/features/{slug}/exploration.md`
   - `.forge/features/{slug}/spike-report.md` when it exists
2. Study the existing test structure in the repo before writing new tests.
3. If `spike-report.md` exists, reuse its mock templates instead of inventing mock shapes.
4. Write tests that:
   - match existing project test patterns
   - cover every spec case in the handoff artifact
   - fail against the current implementation
5. Write `.forge/features/{slug}/test-manifest.md` using the `Test Manifest` contract in `skills/forge/phases/references/execution-artifacts.md`.

## CRITICAL

- Write only tests and the manifest. Do not add implementation code.
- Ensure every spec case maps to at least one concrete test location.
- Include checksums for all created test files.
- Tests should fail because implementation is missing or incomplete.
- Respond with only `done - wrote N test files with M test cases`.
