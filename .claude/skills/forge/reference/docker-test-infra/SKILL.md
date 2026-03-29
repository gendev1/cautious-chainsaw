---
name: docker-test-infra
description: Generate Docker-based test infrastructure for integration and flow tests. Use when a feature touches real I/O boundaries such as databases, caches, queues, object stores, or external HTTP APIs and Forge needs containerized services, seed fixtures, WireMock stubs, `.env.test`, or CI lifecycle guidance.
---

# Docker Test Infrastructure

Generate only the Docker assets the feature actually needs. This skill adds real dependency-backed integration coverage; it is not the default path for pure internal logic.

## Process

1. Read `.forge/features/{slug}/implementation-context.md` and inspect `## External Dependencies`.
2. If the spec says `None — pure internal logic`, skip Docker generation and keep the run on unit tests only.
3. Cross-check the declared dependencies with:

```bash
./skills/forge/tools/detect-dependencies .
```

4. Map each dependency to a container service using `references/service_configs.md`.
5. Generate the Docker artifacts under `.forge/features/{slug}/docker/`:
   - `docker-compose.test.yml`
   - `.env.test`
   - service-specific fixtures or init scripts only for the services that are actually needed
   - WireMock mappings derived from real spike observations when external HTTP APIs are involved
6. If the repository needs CI coverage for the generated stack, use `references/pipeline_integration.md` to generate the workflow and lifecycle wiring.

## Output Rules

- Give every generated service a healthcheck.
- Use environment variables for every connection point. Do not hardcode `localhost` values into test code.
- Keep seed data deterministic and small.
- Only generate the services the feature actually uses.
- If a contract comes from docs instead of a runnable spike, mark it clearly as documentation-derived.

## References

- `references/service_configs.md` for service mapping, compose fragments, env vars, and fixture patterns
- `references/wiremock_reference.md` for stub syntax, scenarios, response templating, and fault injection
- `references/pipeline_integration.md` for local lifecycle, retry resets, CI workflow, and expected output tree

## CRITICAL

- Do not generate Docker assets for pure internal logic.
- Do not invent HTTP response shapes that were neither observed in the spike nor documented in source material.
- Write generated Docker assets only under `.forge/features/{slug}/docker/`, except for CI workflow files when explicitly needed.
- Keep unit tests viable without Docker; Docker extends integration and flow coverage, it does not replace baseline tests.
