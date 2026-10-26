.PHONY: install check lint format format-check typecheck test test-docker run images sandbox-smoke

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

# Sandbox images (stdlib-only, base pinned by digest). Requires Docker.
IMAGE_TAG ?= 0.1.0
images:
	docker build -t bbj-gateway:$(IMAGE_TAG) sandbox_images/gateway
	docker build -t bbj-refundbot:$(IMAGE_TAG) sandbox_images/refundbot

# Manual Phase 2 smoke test: one gateway+agent pair on an internal network with
# full hardening flags. Superseded by DockerSandboxRunner in Phase 5.
sandbox-smoke:
	scripts/sandbox_smoke.sh
