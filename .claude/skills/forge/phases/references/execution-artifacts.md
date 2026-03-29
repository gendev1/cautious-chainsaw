# Execution Artifact Contracts

Use these contracts when writing execution-phase artifacts.

## Spike Report

File: `.forge/features/{slug}/spike-report.md`

Required sections:

- `## Dependencies Explored`
- `## Scratch Files`

For each dependency, capture:

- package or service name and version when available
- what it is used for
- happy-path behavior
- error behavior
- edge cases
- mock template
- contract record and rerun trigger

Response:

- `done - spiked N dependencies`
- `done - no external dependencies`

## Test Manifest

File: `.forge/features/{slug}/test-manifest.md`

Required sections:

- `## Test Files Created`
- `## Spec → Test Mapping`
- `## Edge Cases Covered`
- `## Test File Checksums`
- `## Run Command`

Rules:

- every spec case must map to at least one test location
- include checksums for all created test files
- edge-case checklist should reflect real coverage, not aspirations

Response:

- `done - wrote N test files with M test cases`

## Implementation Manifest

File: `.forge/features/{slug}/impl-manifest.md`

Required sections:

- `## Files Created`
- `## Files Modified`
- `## Patterns Followed`
- `## Test Results`

Optional section:

- `## Blocked Tests`

Response:

- `done - tests passing`
- `done - N tests still failing`

## Verify Report

File: `.forge/features/{slug}/verify-report.md`

Required sections:

- `## Overall`
- `## Test File Integrity`
- `## Tests`
- `## Scope Compliance`
- `## Structural Contracts`
- `## Action Required`

Rules:

- tampered test files force overall failure
- skipped structural rules must be labeled clearly
- unexpected exports belong under structural or scope review warnings

Response:

- `pass`
- `fail - N test failures, N scope violations, N structural violations`
