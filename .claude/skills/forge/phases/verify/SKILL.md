---
name: verify
description: Use when implementation is complete and you need to verify correctness — runs tests, checks scope compliance, validates structural contracts via ast-grep, and detects test tampering.
---

# Verify

Audit the implementation adversarially before Forge declares success.

## Process

1. Read:
   - `.forge/features/{slug}/implementation-context.md`
   - `.forge/features/{slug}/exploration.md`
   - `.forge/features/{slug}/test-manifest.md`
   - `.forge/features/{slug}/impl-manifest.md`
2. Check test integrity first:

```bash
./skills/forge/tools/verify-test-integrity .forge/features/{slug}/test-manifest.md
```

3. Run the test command from the manifest and record pass or fail.
4. Check spec coverage with `parse-markdown-table` and compare it with the test outcomes.
5. Check scope compliance with:

```bash
./skills/forge/tools/check-scope-compliance .forge/features/{slug}/implementation-context.md --manifest .forge/features/{slug}/impl-manifest.md
```

6. If `ast-grep` is available, extract structural rules and check them against the changed files. Otherwise mark structural validation as skipped.
7. Write `.forge/features/{slug}/verify-report.md` using the `Verify Report` contract in `skills/forge/phases/references/execution-artifacts.md`.

## CRITICAL

- Actually run the tests. Do not infer pass or fail from code inspection.
- Integrity failure is an automatic overall failure.
- Skip non-binding exploration rules such as `[insufficient-sample]` patterns.
- Flag unexpected exports as potential scope creep when they are not justified by the implementation context.
- Respond with only `pass` or `fail - N test failures, N scope violations, N structural violations`.
