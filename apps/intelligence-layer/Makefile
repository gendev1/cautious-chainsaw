PYTHONPYCACHEPREFIX := .cache/pycache

export PYTHONPYCACHEPREFIX

.PHONY: sync run test lint clean-cache

sync:
	uv sync

run:
	uv run intelligence-layer

test:
	uv run pytest

lint:
	uv run ruff check .

clean-cache:
	rm -rf .cache .pytest_cache src/__pycache__ src/app/__pycache__ src/app/api/__pycache__ tests/__pycache__
