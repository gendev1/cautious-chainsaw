# Docker Pipeline Integration

Use this reference when the Docker test infrastructure needs lifecycle wiring, retry behavior, CI setup, or a canonical output tree.

## Local Lifecycle

Start the generated stack:

```bash
docker compose -f .forge/features/{slug}/docker/docker-compose.test.yml up -d --wait
```

Run the project's normal tests with the generated environment variables loaded.

Tear the stack down:

```bash
docker compose -f .forge/features/{slug}/docker/docker-compose.test.yml down -v
```

## Forge Execute Loop

When Docker assets exist:

1. Start Docker before `write-tests` or any later phase that needs live services.
2. Export values from `.forge/features/{slug}/docker/.env.test` before running tests.
3. Between `implement -> verify` retries, reset the stack:

```bash
docker compose -f .forge/features/{slug}/docker/docker-compose.test.yml down -v
docker compose -f .forge/features/{slug}/docker/docker-compose.test.yml up -d --wait
```

4. Always tear Docker down on final success, final failure, or explicit stop.

If Docker is unavailable:

- keep unit tests running
- skip integration and flow tiers
- record the limitation in the phase output

## CI Workflow Skeleton

Generate a workflow only when the repository wants CI coverage for the Docker-backed test path.

```yaml
name: Sandbox Test

on:
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest

    steps:
      - uses: actions/checkout@v4

      - name: Install dependencies
        run: |
          if [ -f package.json ]; then npm ci; fi
          if [ -f requirements.txt ]; then pip install -r requirements.txt; fi
          if [ -f go.mod ]; then go mod download; fi

      - name: Start test infrastructure
        run: docker compose -f .forge/features/${{ github.head_ref }}/docker/docker-compose.test.yml up -d --wait

      - name: Run tests
        run: |
          if [ -f package.json ]; then npm test; fi
          if [ -f pytest.ini ] || [ -f pyproject.toml ]; then pytest; fi
          if [ -f go.mod ]; then go test ./...; fi

      - name: Tear down
        if: always()
        run: docker compose -f .forge/features/${{ github.head_ref }}/docker/docker-compose.test.yml down -v
```

## Output Tree

Expected Docker artifact layout:

```text
.forge/features/{slug}/docker/
├── docker-compose.test.yml
├── .env.test
├── fixtures/
│   ├── init.sql
│   ├── seed.sql
│   └── ...
└── wiremock/
    ├── __files/
    └── mappings/
```

## Generation Checklist

- the compose file includes only the needed services
- every service has a healthcheck
- `.env.test` matches the compose file
- fixture filenames are deterministic
- WireMock stubs reflect real observed or documented contracts
- teardown uses `down -v`
