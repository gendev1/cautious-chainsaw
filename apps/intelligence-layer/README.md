## Intelligence Layer

Python sidecar for AI/ML inference, retrieval, summarization, recommendation, and analytical
modeling.

### Quick Start

```bash
make sync
make run
```

The service starts on `http://localhost:8081` by default.

### Common Commands

```bash
make test
make lint
make clean-cache
```

The `Makefile` exports `PYTHONPYCACHEPREFIX=.cache/pycache`, so Python bytecode and tool
caches stay under `.cache/` instead of creating scattered `__pycache__` folders.

### Endpoints

- `GET /healthz`
- `GET /readyz`

### Environment

Key variables:

- `APP_ENV`
- `LOG_LEVEL`
- `HOST`
- `PORT`
- `RELOAD`
- `PLATFORM_API_BASE_URL`
- `PLATFORM_SERVICE_TOKEN`
- `REDIS_URL`
- `LANGFUSE_PUBLIC_KEY`
- `LANGFUSE_SECRET_KEY`
- `LANGFUSE_HOST`
