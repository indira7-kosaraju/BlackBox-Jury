# Blackbox Jury

**Team Null Verdict · Agentic AI — Track 01 · "Trust Nothing. Verify Everything."**

Blackbox Jury is a security-first testing system for tool-using AI agents. It runs adversarial scenarios against an **authorized** target inside an **isolated sandbox**, records what the agent actually did (tool calls and state changes), and decides objective violations with **deterministic checks**. Every executed scenario gets one of three verdicts: `CONFIRMED`, `NOT REPRODUCED` or `INCONCLUSIVE`.

> Results reflect only the scenarios executed. Blackbox Jury does not establish that any agent is secure.

**Status:** Phase 4 (scenario schema and curated scenarios). See [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md).

## Project documents

| Doc | Purpose |
|---|---|
| [PRD.md](PRD.md) | Product requirements (source of truth) |
| [DESIGN.md](DESIGN.md) | Report and CLI design |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Components, trust boundaries, sandbox |
| [DATABASE.md](DATABASE.md) | Data model |
| [API.md](API.md) | REST API |
| [AGENTS.md](AGENTS.md) | Mandatory rules for contributors and AI coding agents |
| [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) | 48-hour build plan |
| [docs/LIMITATIONS.md](docs/LIMITATIONS.md) | **Known detection and coverage limitations.** Read this before interpreting any result. |

## Local setup

**Requirements:** macOS or Linux, [uv](https://docs.astral.sh/uv/), and Docker Desktop (needed from Phase 5 onward, for sandboxed execution). uv installs the pinned Python 3.12 automatically.

```bash
# 1. Install dependencies (creates .venv with Python 3.12)
uv sync

# 2. Create your local config. Never commit .env.
cp .env.example .env
# Generate a token and put it in .env as BBJ_API_TOKEN:
python3 -c "import secrets; print(secrets.token_urlsafe(32))"

# 3. Run the checks
make check

# 4. Start the API (binds to 127.0.0.1 only)
make run
curl http://127.0.0.1:8000/health
```

## Configuration

All configuration comes from environment variables, or from `.env`, which is git-ignored. See [.env.example](.env.example).

| Variable | Required | Default | Notes |
|---|---|---|---|
| `BBJ_API_TOKEN` | yes | – | Bearer token for all `/api/v1` routes. At least 32 characters. Placeholder values are rejected. |
| `BBJ_API_ACTOR` | no | `local-admin` | Actor label recorded in audit events |
| `BBJ_ENV` | no | `dev` | `dev`, `test` or `prod`. OpenAPI docs (`/docs`) are served only in `dev`. |
| `BBJ_HOST` | no | `127.0.0.1` | Bind address used by `make run` |
| `BBJ_PORT` | no | `8000` | Bind port used by `make run` |
| `BBJ_LOG_LEVEL` | no | `INFO` | `DEBUG`, `INFO`, `WARNING` or `ERROR` |

## Developer commands

| Command | What it does |
|---|---|
| `make check` | lint, format check, type check, unit tests (no Docker) |
| `make lint` / `make format` | ruff check / ruff format |
| `make typecheck` | mypy (strict) over `src/` |
| `make test` | pytest, excluding Docker-marked tests |
| `make test-docker` | sandbox integration tests (requires Docker) |
| `make run` | start the API on `127.0.0.1:8000` |
| `make images` | build the sandbox images `bbj-gateway:0.1.0` and `bbj-refundbot:0.1.0` (requires Docker) |
| `make sandbox-smoke` | run one gateway + RefundBot pair in the hardened sandbox and print the evidence. Args: `scripts/sandbox_smoke.sh [vulnerable\|hardened] "message"`; optional `NOTE="..."` overrides order A-101's note. |

## Known issues

- **Starlette test-client deprecation warning (dev only, accepted).** Starlette 1.10's `TestClient` prefers the new `httpx2` package. It still fully supports `httpx`, but warns on import. We keep `httpx` for now rather than adopt a new, unvetted dependency. The warning is filtered in `pyproject.toml` with a filter that matches only that exact message, so other deprecations still show. Revisit this when upgrading FastAPI or Starlette.
- **Editable-install `.pth` file on macOS.** macOS can set the "hidden" flag on files in `.venv`, and Python 3.12.15+ skips hidden `.pth` files. To avoid depending on that file, pytest uses `pythonpath = ["src"]` and `make run` passes `--app-dir src`.

## Sandbox images

| Image | Contents |
|---|---|
| `bbj-gateway` | Mock tool API (`get_order`, `list_orders`, `get_customer`, `issue_refund`, `send_email`, `update_order_note`) over synthetic data. It records every call and the state before and after as nonce-tagged JSON lines. It observes but does not enforce policy. |
| `bbj-refundbot` | Scripted mock target agent. In `vulnerable` mode it follows injected instructions, skips ownership checks, leaks data and retries blindly. In `hardened` mode (the "fix") it does none of these. Legitimate tasks succeed in both modes. |

Both images are stdlib-only Python on a digest-pinned `python:3.12-slim`, run as the non-root user `10001` in isolated mode (`python -I`), and install no packages.

## Safety defaults

- The only supported target is the bundled local mock agent, and all data is synthetic.
- Attack scenarios never run on the host. They run only in locked-down Docker containers with no network egress, so if Docker is unavailable, nothing runs.
- Secrets are redacted from logs and evidence. Never put real credentials or real customer data into this system.
