.PHONY: install check lint format format-check typecheck test test-docker run

install:
	uv sync

check: lint format-check typecheck test

lint:
	uv run ruff check .

format:
	uv run ruff format .

format-check:
	uv run ruff format --check .

typecheck:
	uv run mypy

test:
	uv run pytest -m "not docker"

test-docker:
	uv run pytest -m docker

# Binds to 127.0.0.1 by default; reads BBJ_* from the environment or .env.
run:
	uv run uvicorn --app-dir src --factory blackbox_jury.api.app:create_app \
		--host $${BBJ_HOST:-127.0.0.1} --port $${BBJ_PORT:-8000}
