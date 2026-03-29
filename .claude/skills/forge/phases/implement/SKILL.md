---
name: implement
description: Use when failing tests exist and you need to write minimal implementation code to make them pass. GREEN phase of red-green-verify.
---

# Implement

Write the minimum implementation needed to make the failing tests pass without violating scope or structural contracts.

## Process

1. Read:
   - `.forge/features/{slug}/implementation-context.md`
   - `.forge/features/{slug}/exploration.md`
   - `.forge/features/{slug}/test-manifest.md`
   - `.forge/features/{slug}/verify-report.md` when retrying
2. Read the actual test files listed in the manifest.
3. Implement the feature to satisfy the tests while following the structural patterns from exploration.
4. Run the project test command and capture the result.
5. Write `.forge/features/{slug}/impl-manifest.md` using the `Implementation Manifest` contract in `skills/forge/phases/references/execution-artifacts.md`.

## CRITICAL

- Never modify test files.
- Stay inside the scope boundaries from `implementation-context.md`.
- If a test appears impossible because the spec is wrong, record it under `Blocked Tests` in the manifest instead of mutating the test.
- Respond with only `done - tests passing` or `done - N tests still failing`.
