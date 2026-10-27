.PHONY: install check lint format format-check typecheck test test-docker run images sandbox-smoke seed demo qa clean-demo clean-images

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

# Host/port come from settings (environment or .env); default 127.0.0.1:8000.
run:
	PYTHONPATH=src uv run python -m blackbox_jury.serve

# Sandbox images (stdlib-only, base pinned by digest). Requires Docker.
IMAGE_TAG ?= 0.1.0
images:
	docker build -t bbj-gateway:$(IMAGE_TAG) sandbox_images/gateway
	docker build -t bbj-refundbot:$(IMAGE_TAG) sandbox_images/refundbot

# Manual Phase 2 smoke test: one gateway+agent pair on an internal network with
# full hardening flags. Superseded by DockerSandboxRunner in Phase 5.
sandbox-smoke:
	scripts/sandbox_smoke.sh

# CLI via `python -m` with src on the path (works around the macOS hidden-.pth issue).
BBJ = PYTHONPATH=src uv run python -m blackbox_jury.cli

seed:
	$(BBJ) seed

# Requires the API running (`make run`) and Docker with images built.
demo:
	$(BBJ) run --suite demo

# Measure PRD success metrics against the real sandbox (writes docs/qa/qa_metrics.json).
qa:
	PYTHONPATH=src uv run python scripts/qa_metrics.py

# Shutdown/cleanup: remove local demo data (SQLite DB, reports) and any sandbox
# containers/networks left behind. Touches only data/, reports/out/ and resources
# labelled bbj.execution. Asks for confirmation first.
clean-demo:
	@printf "Delete data/ and reports/out/ (local demo DB and reports)? [y/N] "; read a; [ "$$a" = "y" ]
	rm -rf data reports/out
	@for c in $$(docker ps -aq --filter label=bbj.execution); do docker rm -f $$c; done
	@for n in $$(docker network ls -q --filter label=bbj.execution); do docker network rm $$n; done
	@echo "demo data and sandbox resources removed"

# Remove the locally built sandbox images.
clean-images:
	-docker rmi bbj-gateway:$(IMAGE_TAG) bbj-refundbot:$(IMAGE_TAG)
