# Blackbox Jury
### Team Null Verdict · Agentic AI — Track 01
**“Trust Nothing. Verify Everything.”**

Blackbox Jury is a security-first testing framework for tool-using AI agents. It evaluates how AI agents behave under adversarial scenarios in an isolated sandbox, captures their actual tool calls and state changes, and uses deterministic checks to identify objective security violations.

Instead of relying only on an agent's responses or explanations, Blackbox Jury examines execution evidence to determine whether a violation occurred.

> **Important:** Results reflect only the scenarios executed. Blackbox Jury does not prove that an AI agent is completely secure.

## Project Status

- **MVP:** Complete — Phases 1–10
- **Demo readiness:** Ready with warnings
- **Target environment:** Isolated Docker sandbox
- **Demo target:** RefundBot, a scripted mock AI agent
- **Data:** Synthetic test data only

For the current readiness assessment, see [`docs/READINESS.md`](docs/READINESS.md).

## Table of Contents

- [Overview](#overview)
- [Key Features](#key-features)
- [How It Works](#how-it-works)
- [Verdict System](#verdict-system)
- [Technology Stack](#technology-stack)
- [Project Structure](#project-structure)
- [Prerequisites](#prerequisites)
- [Installation and Setup](#installation-and-setup)
- [Running the Demo](#running-the-demo)
- [CLI Commands](#cli-commands)
- [Web Interface](#web-interface)
- [Fix and Retest Workflow](#fix-and-retest-workflow)
- [Configuration](#configuration)
- [Testing and Quality Assurance](#testing-and-quality-assurance)
- [Security and Safety](#security-and-safety)
- [Known Limitations](#known-limitations)
- [Documentation](#documentation)

## Overview

AI agents can interact with tools, access information, modify application state, and perform actions on behalf of users. However, an agent's natural-language response alone may not reveal whether it violated a security policy during execution.

Blackbox Jury addresses this challenge by testing agents against controlled adversarial scenarios and evaluating the resulting execution evidence.

The system includes a mock tool gateway, a scripted target agent, deterministic policy checks, evidence collection, a verdict engine, regression testing, and a web-based command center.

### Problem Statement

Traditional testing may not adequately reveal whether a tool-using AI agent follows instructions securely when exposed to adversarial inputs. Responses can appear correct even when the underlying tool interactions or state changes violate expected rules.

### Proposed Solution

Blackbox Jury executes predefined security scenarios against an authorized target in an isolated environment. It records tool calls and before-and-after state changes, evaluates the collected evidence against deterministic checks, and generates a verdict with supporting evidence.

## Key Features

- **Adversarial scenario testing:** Run curated scenarios designed to expose unsafe agent behavior.
- **Execution evidence collection:** Record tool calls and relevant state changes.
- **Deterministic verification:** Evaluate objective violations using explicit checks instead of relying solely on an LLM's judgment.
- **Three-way verdict system:** Distinguish confirmed violations, unsuccessful reproductions, and inconclusive results.
- **Sandboxed execution:** Run supported targets inside restricted Docker containers.
- **Evidence explorer:** Inspect recorded execution details and findings through the web interface.
- **Regression testing:** Export a finding and retest it after applying a fix.
- **Run management:** Start, inspect, report, and cancel test runs.
- **REST API:** Expose testing and run-management operations through authenticated API routes.
- **Advisory LLM jury:** Optionally use an LLM for additional analysis without replacing deterministic checks.
- **Demo mode:** Explore recorded demo data without starting a server.
- **Security-focused defaults:** Use synthetic data, restrict target images, and redact secrets from evidence and logs.

## How It Works

The following workflow describes the main execution pipeline.

```text
       Adversarial Test Scenario
                 |
                 v
       Authorized Target Agent
                 |
                 v
        Isolated Docker Sandbox
                 |
                 v
          Mock Tool Gateway
                 |
                 v
     Tool Calls and State Changes
                 |
                 v
          Evidence Collection
                 |
                 v
       Deterministic Verification
                 |
                 v
           Verdict Generation
                 |
                 v
        Report and Evidence Review
                 |
                 v
          Fix and Regression Test
```

### Main Components

**1. Scenario Runner**

Selects and executes predefined security scenarios, either individually or as part of a test suite.

**2. Target Agent — RefundBot**

A scripted mock agent that simulates vulnerable and hardened behavior. Its vulnerable mode demonstrates selected security failures, while its hardened mode applies the intended protections.

**3. Tool Gateway**

Provides mock tools such as order lookup, customer lookup, refunds, email sending, and order-note updates. It records tool calls and relevant state before and after execution.

**4. Evidence Collector**

Captures execution evidence in nonce-tagged JSON Lines records for subsequent verification.

**5. Deterministic Jury**

Evaluates the evidence against explicit checks and produces the applicable verdict. An optional LLM jury can provide supplementary analysis.

**6. API and Web UI**

The API manages test execution, findings, reports, and related operations. The React-based interface provides a command center for inspecting runs and evidence.

**7. Regression Testing**

Exports a finding into a regression-test artifact so the original scenario can be executed again after a fix.

## Verdict System

Every executed scenario receives one of three verdicts.

| Verdict | Meaning |
|---|---|
| `CONFIRMED` | The executed scenario produced evidence satisfying the defined violation checks. |
| `NOT REPRODUCED` | The executed scenario did not reproduce the targeted violation under the tested conditions. |
| `INCONCLUSIVE` | The available execution or evidence was insufficient to determine the outcome reliably. |

A `NOT REPRODUCED` result does not establish that the target is secure. Verdicts must be interpreted in the context of the scenarios executed, their coverage, and the available evidence.

## Technology Stack

| Area | Technologies |
|---|---|
| Backend | Python 3.12, FastAPI, Starlette |
| Frontend | React, Vite, TypeScript |
| Database | SQLite |
| Sandbox | Docker |
| API authentication | Bearer-token authentication |
| Data validation and contracts | YAML contracts and structured JSON evidence |
| Quality assurance | pytest, Ruff, mypy |
| Frontend quality | TypeScript checks and UI tests |
| Dependency management | uv and npm |
| Optional LLM integration | Anthropic SDK |

## Project Structure

```text
Blackbox-Jury/
├── src/
│   └── blackbox_jury/
│       └── cli.py
├── frontend/
│   ├── src/
│   └── dist/
├── contracts/
├── seeds/
│   └── synthetic_store.json
├── scripts/
├── tests/
├── reports/
│   └── out/
├── docs/
│   ├── READINESS.md
│   ├── QA_REPORT.md
│   ├── SECURITY_AUDIT.md
│   ├── DEPLOYMENT.md
│   ├── LIMITATIONS.md
│   └── DEMO_SCRIPT.md
├── PRD.md
├── DESIGN.md
├── ARCHITECTURE.md
├── DATABASE.md
├── API.md
├── AGENTS.md
├── IMPLEMENTATION_PLAN.md
├── .env.example
├── Makefile
└── README.md
```

*This is a representative structure based on the documented project components. Keep the tree aligned with the actual repository when publishing.*

## Prerequisites

Install the following before starting:

- macOS or Linux
- Python 3.12 — installed automatically by `uv` when supported by the project configuration
- [`uv`](https://docs.astral.sh/uv/)
- Docker Desktop or a compatible Docker environment
- Node.js 20 or later
- npm

Docker is required for sandbox image builds and actual sandboxed execution. The frontend requires Node.js and npm when building or developing the UI.

## Installation and Setup

### 1. Clone the repository

```bash
git clone <YOUR_REPOSITORY_URL>
cd <YOUR_REPOSITORY_DIRECTORY>
```

Replace the placeholders with your actual repository URL and directory name.

### 2. Install Python dependencies

```bash
uv sync
```

### 3. Configure environment variables

Create your local environment file:

```bash
cp .env.example .env
```

Generate a strong API token:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

Set the generated value in `.env`:

```dotenv
BBJ_API_TOKEN=<YOUR_GENERATED_TOKEN>
```

Keep `.env` private and never commit real credentials to version control.

### 4. Run the checks

```bash
make check
```

This runs the configured linting, formatting, type-checking, and unit-test checks.

### 5. Build the sandbox images

Make sure Docker is running, then execute:

```bash
make images
```

This builds the local gateway and RefundBot sandbox images.

### 6. Start the API

```bash
make run
```

By default, the API listens on:

```text
http://127.0.0.1:8000
```

The service applies database migrations on startup. The API binds to the loopback interface by default.

## Running the Demo

After starting the API, open a second terminal in the project directory.

### 1. Seed demo data

```bash
make seed
```

This seeds the demo contract, ten curated scenarios, and the vulnerable RefundBot target.

### 2. Execute the demo suite

```bash
make demo
```

The demo runs the suite through the API, streams verdicts, and writes a JSON report under `reports/out/`.

### 3. Inspect a test run

Use the run ID returned by the system:

```bash
bbj status <RUN_ID>
bbj report <RUN_ID>
```

The demo is designed to show how the system records evidence, detects selected violations, and supports verification after a fix.

## CLI Commands

| Command | Purpose |
|---|---|
| `seed` | Seed demo data |
| `db upgrade` | Apply database migrations |
| `run --suite demo` | Run the demo suite |
| `run --suite slice` | Run the slice suite |
| `run --scenario <KEY>` | Execute a specific scenario |
| `status <RUN_ID>` | Show the status of a run |
| `report <RUN_ID>` | Print a run report |
| `cancel <RUN_ID>` | Cancel a run |
| `export <FINDING_ID>` | Export a finding for regression testing |
| `target set-mode refundbot hardened` | Switch the demo target to hardened mode after confirmation |
| `retest --file <PATH>` | Re-execute a regression test from an exported artifact |

If the `bbj` command is not available directly in your environment, invoke the CLI through the project environment:

```bash
PYTHONPATH=src uv run python -m blackbox_jury.cli <COMMAND>
```

For example:

```bash
PYTHONPATH=src uv run python -m blackbox_jury.cli run --suite demo
```

## Web Interface

Blackbox Jury includes an interactive command center built with React, Vite, and TypeScript.

### Build the UI

```bash
make ui
```

### Start the application

```bash
make run
```

Open [http://127.0.0.1:8000/ui](http://127.0.0.1:8000/ui) and enter the `BBJ_API_TOKEN` configured in `.env`.

### Available Pages

- Overview
- Targets
- Test Lab
- Test Runs
- Evidence Explorer
- Jury Findings
- Regression Tests
- Settings

### Demo Mode

The sign-in screen provides an **Explore demo data** option that works without a running server. It displays recorded data labelled `DEMO DATA`.

Starting a run in this mode replays a recorded run and labels it `SIMULATED REPLAY`. This is a demonstration of the interface, not a new live test execution.

To refresh the demo fixture from an actual sandboxed run:

```bash
make ui-fixture
```

Docker is required.

### Frontend Development

Run these commands in separate terminals:

```bash
make run
```

```bash
make ui-dev
```

Then open [http://127.0.0.1:5173/ui/](http://127.0.0.1:5173/ui/).

The development server proxies `/api` requests to the backend.

## Fix and Retest Workflow

Blackbox Jury supports a repeatable process for checking whether a previously observed violation can be reproduced after a fix.

### Step 1: Export a finding

```bash
bbj export <FINDING_ID>
```

The command creates a regression artifact under:

```text
reports/out/regressions/
```

### Step 2: Apply the demo fix

```bash
bbj target set-mode refundbot hardened
```

The command asks for confirmation before changing the target mode.

### Step 3: Retest the exported scenario

```bash
bbj retest --file reports/out/regressions/<REGRESSION_FILE>.json
```

The intended demo outcome is:

```text
Original verdict: CONFIRMED
Retest verdict:   NOT_REPRODUCED
```

This demonstrates that the previously observed behavior was not reproduced in the hardened demo mode. It does not prove that every possible attack has been prevented.

## Configuration

Configuration is loaded from environment variables or the local `.env` file. See [`.env.example`](.env.example) for the complete configuration template.

| Variable | Default | Description |
|---|---|---|
| `BBJ_API_TOKEN` | Required | Bearer token for protected API routes; must be at least 32 characters. |
| `BBJ_API_ACTOR` | `local-admin` | Actor label recorded in audit events. |
| `BBJ_ENV` | `dev` | Application environment: `dev`, `test`, or `prod`. |
| `BBJ_HOST` | `127.0.0.1` | API bind address and CLI default host. |
| `BBJ_PORT` | `8000` | API port. |
| `BBJ_LOG_LEVEL` | `INFO` | Logging level. |
| `BBJ_GATEWAY_IMAGE` | `bbj-gateway:0.1.0` | Local tool-gateway image. |
| `BBJ_ALLOWED_TARGET_IMAGES` | `["bbj-refundbot:0.1.0"]` | JSON list of permitted target images. |
| `BBJ_EXEC_TIMEOUT_S` | `30` | Per-scenario execution timeout; maximum 300 seconds. |
| `BBJ_DOCKER_TIMEOUT_S` | `20` | Docker API call timeout. |
| `BBJ_LLM_PROVIDER` | `none` | Optional advisory jury provider: `none` or `anthropic`. |
| `BBJ_LLM_MODEL` | `claude-opus-5-5` | Configured advisory jury model. |
| `BBJ_LLM_TIMEOUT_S` | `120` | Timeout per LLM jury call. |
| `BBJ_ANTHROPIC_API_KEY` | Optional | Anthropic API credential; redacted from logs. |
| `BBJ_RUN_TIMEOUT_S` | `600` | Whole-run timeout; maximum 3600 seconds. |
| `BBJ_DATABASE_URL` | `sqlite:///./data/bbj.db` | SQLite database URL. |
| `BBJ_SEED_PATH` | `seeds/synthetic_store.json` | Synthetic seed-data file. |
| `BBJ_CONTRACTS_DIR` | `contracts` | Directory containing YAML contracts. |
| `BBJ_UI_DIST_DIR` | `frontend/dist` | Built frontend directory served at `/ui`. |

The optional LLM integration requires the corresponding dependencies and credentials when using Anthropic.

```bash
uv sync --extra llm
```

The deterministic checks remain central to the verdict system.

## Testing and Quality Assurance

Blackbox Jury provides development checks and sandbox-specific testing commands.

| Command | Purpose |
|---|---|
| `make check` | Lint, format check, type check, and unit tests |
| `make lint` | Run Ruff linting |
| `make format` | Format Python code |
| `make typecheck` | Run strict mypy checks on `src/` |
| `make test` | Run pytest tests, excluding Docker-marked tests |
| `make test-docker` | Run sandbox integration tests |
| `make qa` | Measure configured PRD metrics against the real sandbox |
| `make sandbox-smoke` | Run a gateway and RefundBot smoke test |

Run `make qa` to generate the configured QA metrics artifact:

```text
docs/qa/qa_metrics.json
```

Consult [`docs/QA_REPORT.md`](docs/QA_REPORT.md) for the reported measurements. Do not assume coverage or performance figures without checking the latest results.

## Security and Safety

Blackbox Jury is designed to minimize risk during testing.

- **Authorized targets only:** The supported execution target is the bundled local mock agent.
- **Synthetic data:** Demo scenarios use synthetic orders and customer information.
- **Isolated execution:** Attack scenarios run inside restricted Docker containers rather than directly on the host.
- **No network egress:** Sandbox execution is configured without outbound network access.
- **Restricted images:** Only configured, permitted target images can be executed.
- **Non-root containers:** The documented images run as user `10001`.
- **Pinned base image:** Sandbox images use a digest-pinned `python:3.12-slim` base.
- **Secret handling:** Secrets are redacted from logs and evidence.
- **Local API binding:** The default host is `127.0.0.1`.
- **Authenticated API:** Protected API routes require a bearer token.
- **No real credentials:** Real credentials and customer data must never be placed in test scenarios.

Docker is a required safety boundary for execution. If Docker is unavailable, sandboxed scenarios must not execute.

## Known Limitations

Blackbox Jury evaluates only the behavior covered by its implemented scenarios, contracts, and checks.

- The bundled RefundBot is a scripted mock agent, not a general-purpose production AI agent.
- The available scenarios do not cover every possible adversarial input or attack technique.
- Deterministic checks can only detect violations that are represented by the implemented rules and available evidence.
- `NOT_REPRODUCED` is not equivalent to a security guarantee.
- The optional LLM jury provides advisory analysis and should not be treated as authoritative proof.
- The demo UI can replay previously recorded data; a simulated replay is not a fresh execution.
- Sandbox isolation reduces risk but should not be interpreted as an unconditional guarantee of containment.

Read [`docs/LIMITATIONS.md`](docs/LIMITATIONS.md) before interpreting test results. Also review the latest readiness, QA, and security audit documents before demonstrating or deploying the system.

## Documentation

| Document | Purpose |
|---|---|
| [`PRD.md`](PRD.md) | Product requirements and success criteria |
| [`DESIGN.md`](DESIGN.md) | CLI and report design |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | System components, trust boundaries, and sandbox architecture |
| [`DATABASE.md`](DATABASE.md) | Database schema and data model |
| [`API.md`](API.md) | REST API documentation |
| [`AGENTS.md`](AGENTS.md) | Rules for contributors and AI coding agents |
| [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md) | 48-hour implementation plan |
| [`docs/DEMO_SCRIPT.md`](docs/DEMO_SCRIPT.md) | Five-minute demo instructions and fallbacks |
| [`docs/READINESS.md`](docs/READINESS.md) | Demo readiness assessment |
| [`docs/QA_REPORT.md`](docs/QA_REPORT.md) | Quality assurance report |
| [`docs/SECURITY_AUDIT.md`](docs/SECURITY_AUDIT.md) | Security audit and code review |
| [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) | Deployment, operations, and monitoring |
| [`docs/LIMITATIONS.md`](docs/LIMITATIONS.md) | Detection and coverage limitations |

## Team

**Team Null Verdict**

Agentic AI — Track 01

**Project:** Blackbox Jury

**Tagline:** Trust Nothing. Verify Everything.

---

*Blackbox Jury is a controlled security-testing framework intended for authorized targets and isolated environments. Its findings describe observed behavior under tested conditions and should not be interpreted as certification of an agent's overall security.*
