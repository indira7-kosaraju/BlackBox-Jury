# ARCHITECTURE — Blackbox Jury MVP

**Status:** Draft v0.1 · **Sources of truth:** PRD.md, DESIGN.md · **Last updated:** 2026-10-26

The simplest reliable architecture for the 48-hour MVP is a **modular monolith** (one Python process for the API, CLI and worker) plus a **Docker sandbox** that runs each scenario execution in throwaway containers. There are no microservices, no message broker and no Kubernetes.

---

## 1. Stack and reasoning

| Concern | Choice | Why |
|---|---|---|
| Language | Python 3.12 (managed by `uv`) | Team familiarity, quick to write, good AI and Docker libraries. The system Python (3.9) is EOL. |
| API | FastAPI + Uvicorn | Typed request and response models, automatic OpenAPI docs, async-friendly. |
| Schemas and config | Pydantic v2 + pydantic-settings | One validation layer for API, contracts, scenarios, evidence and LLM output. |
| Persistence | SQLite via SQLAlchemy 2.0, with Alembic migrations | Zero-ops locally. The schema stays portable to PostgreSQL later. |
| Sandbox | Docker Engine via the `docker` Python SDK | Real process, network and filesystem isolation without extra infrastructure. |
| Orchestration | Small custom pipeline: a single in-process worker thread with a job queue | The workflow is linear, so LangGraph adds no value for the MVP (decision D7). |
| CLI | Typer | Quick to build, typed, used for the demo. |
| Report | Jinja2 → static HTML; JSON from the API | No frontend build step (decision D3). |
| Contract and scenario files | YAML (`yaml.safe_load` only) → Pydantic | Human-readable, safely parsed. |
| LLM | `LLMProvider` protocol, with `NullProvider` as the default | The deterministic loop works without any provider. The concrete provider is pending (decision D5). |
| Quality tooling | ruff (lint and format), mypy (strict on `src/`), pytest | Fast, standard tools. |

Sandbox images are based on `python:3.12-slim` and use **only the standard library**. There is no `pip install` inside the images, which keeps them small and leaves no third-party code inside the sandbox.

## 2. Components

```
                         ┌──────────────── Control plane (trusted, host) ──────────────────┐
  CLI (bbj) ──HTTP──▶    │  FastAPI API ──▶ Run Service ──▶ Job queue ──▶ Worker thread     │
                         │      │               │                            │              │
                         │      ▼               ▼                            ▼              │
                         │  Auth/Audit     Scenario Library            Sandbox Runner ─────────┐
                         │                 (+ optional Generator)            │              │  │
                         │                                                   ▼              │  │
                         │  Report Builder ◀── Verdict Engine ◀── Verifier ◀── Evidence      │  │
                         │        │                 ▲                        Collector       │  │
                         │        ▼                 │ (advisory only)                        │  │
                         │  Regression Exporter   Jury (reviewer + counter-reviewer)         │  │
                         │                          │                                        │  │
                         │  SQLite (SQLAlchemy) ◀───┴── all components persist via repos     │  │
                         └───────────────────────────────────────────────────────────────────┘  │
                                                                                                 │ Docker SDK
          ┌─────────────────────────── Sandbox (untrusted zone, per execution) ─────────────────▼───┐
          │  internal-only Docker network (no egress)                                               │
          │  ┌──────────────────────┐   HTTP (tool calls)    ┌───────────────────────────────────┐  │
          │  │ Target agent         │ ─────────────────────▶ │ Tool Gateway (mock APIs)          │  │
          │  │ container (RefundBot)│ ◀───────────────────── │ synthetic in-memory SQLite        │  │
          │  │ stdout: final answer │                        │ stdout: signed evidence events    │  │
          │  └──────────────────────┘                        └───────────────────────────────────┘  │
          └─────────────────────────────────────────────────────────────────────────────────────────┘
```

| Component | Module | Responsibility |
|---|---|---|
| API / control plane | `api/` | Auth, validation, routing, error schema, audit events. It never executes payloads. |
| Run Service | `orchestrator/run_service.py` | Creates runs (idempotent), queues them, handles cancellation, drives the run state machine. |
| Worker | `orchestrator/worker.py` | A single thread that takes runs off the queue and executes their scenarios one at a time. |
| Scenario Library | `scenarios/` | Loads and validates curated YAML scenarios. The optional generator produces variants, which are validated and stored as `source=generated`. |
| Sandbox Runner | `sandbox/` | Creates the per-execution network and containers, enforces limits and timeouts, and always cleans up. |
| Tool Gateway | `sandbox_images/gateway/` | Mock APIs (`get_order`, `list_orders`, `get_customer`, `issue_refund`, `send_email`, `update_order_note`) over a synthetic store. It **observes but does not block** calls and emits every call plus the state before and after as evidence. |
| Target agent | `sandbox_images/refundbot/` | The bundled mock target RefundBot, with `vulnerable` and `hardened` modes (`llm` mode is a stretch goal). |
| Evidence Collector | `evidence/` | Parses gateway output, checks the per-execution nonce, redacts, hashes and persists events and artifacts. |
| Verifier | `verifier/checks.py`, `verifier/engine.py` | Pure deterministic functions `(contract, scenario, evidence) → CheckResult[]`. |
| Verdict Engine | `verifier/verdict.py`, `verifier/severity.py` | Applies the PRD FR23 precedence and the FR24 severity rubric. |
| Jury | `jury/` | Reviewer and counter-reviewer through `LLMProvider`. Output is schema-validated and **advisory only**. |
| Report Builder | `reports/` | Builds the JSON report and renders the static HTML report (DESIGN.md). |
| Regression Exporter / Retest | `regression/` | Exports a CONFIRMED finding as a portable test JSON and creates `kind=retest` runs. |
| Persistence | `db/` | SQLAlchemy models, repositories and the Alembic migration. |

## 3. Data flow (one run)

1. `POST /runs` with an `Idempotency-Key`. The API authenticates, validates that the target is authorized, the contract exists and the scenarios are valid, then stores a `queued` run and writes an audit event.
2. The worker picks up the run and sets it to `running`. For each scenario:
   1. Set the execution to `running` and start the sandbox (section 7). The seeded state and task input are passed to the containers.
   2. The agent calls tools on the gateway. The gateway applies the effects to its synthetic store and emits an evidence event for each call.
   3. The agent exits, or is killed when it times out. The runner sends SIGTERM to the gateway, which emits `state_after` and exits.
   4. The collector parses and checks the gateway logs and the agent stdout, then redacts, hashes and persists everything. The execution becomes `collected`, or `error` with `missing_evidence` if something is missing.
   5. The verifier runs the checks and the verdict engine assigns a verdict and severity. The execution becomes `verified`. A CONFIRMED result, or an INCONCLUSIVE result that needs review, creates a finding.
3. With all executions done, the run becomes `reviewing`. If a provider is configured, the jury reviews the findings and stores model analysis without touching any verdict.
4. The run becomes `completed` and its totals are computed from **executed** scenarios only.
5. A report is available through `GET /runs/{id}/report` (JSON or HTML) or `bbj report`.
6. `POST /findings/{id}/regression-test` exports a test. `POST /retests` creates a `kind=retest` run whose results link back to the original finding.

## 4. Trust boundaries

| Zone | Contents | Treatment |
|---|---|---|
| **Trusted** | Control plane code, verifier, contract files committed to the repo, DB | Authoritative for verdicts. |
| **Semi-trusted** | Tool Gateway image (our code, running in the sandbox) | Trusted to record calls. Isolated from the agent by a container boundary. Its output is checked with a per-execution nonce and a strict schema. |
| **Untrusted** | The target agent and all its output, seeded "untrusted content" (order notes), generated scenarios, LLM reviewer output, any user-supplied text | Never executed on the host. Parsed with size limits and strict schemas. Redacted before storage. Rendered with escaping. Passed to LLMs only as delimited data. |

Key rules:
- **Tool calls are recorded at the gateway, never as the agent reports them.** The agent cannot write to the gateway's stdout and cannot reach the host.
- The agent's final response is stored as an **untrusted claim**. It is used only to detect false success claims and data exposure.
- **No LLM output can change a deterministic FAIL.** The verdict engine takes jury output only as a possible move from NOT REPRODUCED to INCONCLUSIVE (`jury_only_flag`).
- Evidence and scenarios are rendered as escaped text in reports, never as HTML.

## 5. Folder structure

```
BlackBox-Jury/
├── PRD.md  DESIGN.md  ARCHITECTURE.md  DATABASE.md  API.md  AGENTS.md  IMPLEMENTATION_PLAN.md
├── README.md
├── pyproject.toml            # uv-managed; ruff/mypy/pytest config
├── .env.example              # placeholders only
├── alembic.ini
├── src/blackbox_jury/
│   ├── api/                  # app.py, deps.py (auth), errors.py, routes/{targets,contracts,scenarios,runs,findings,regression}.py
│   ├── core/                 # config.py, logging.py, redaction.py, hashing.py, ids.py, ratelimit.py
│   ├── contracts/            # schema.py (Pydantic), loader.py
│   ├── scenarios/            # schema.py, loader.py, generator.py (optional), library/*.yaml
│   ├── orchestrator/         # run_service.py, worker.py, state_machine.py
│   ├── sandbox/              # runner.py (protocol), docker_runner.py, limits.py
│   ├── evidence/             # schema.py, collector.py
│   ├── verifier/             # checks.py, engine.py, verdict.py, severity.py
│   ├── jury/                 # provider.py (protocol + NullProvider), reviewer.py, prompts/{reviewer_v1,counter_v1}.md
│   ├── reports/              # builder.py, render.py, templates/report.html.j2
│   ├── regression/           # export.py, retest.py
│   ├── db/                   # base.py, models.py, session.py, repos/*.py, migrations/
│   └── cli.py                # `bbj` entrypoint
├── sandbox_images/
│   ├── gateway/              # Dockerfile + bbj_gateway/{server,tools,store,events}.py (stdlib only)
│   └── refundbot/            # Dockerfile + bbj_refundbot/{main,agent}.py (vulnerable | hardened)
├── contracts/                # refund_support_v1.yaml (demo contract)
├── seeds/                    # synthetic_store.json (customers, orders, faults, canary secret)
├── scripts/                  # sandbox_smoke.sh (manual Phase 2 smoke test; replaced by the runner in Phase 5)
└── tests/
    ├── unit/                 # verifier, verdict, severity, redaction, schemas, state machine
    ├── integration/          # API + DB, sandbox runner (marked `docker`)
    └── e2e/                  # demo loop: run → report → export → retest
```

## 6. Target-agent adapter interface

MVP adapter type: **`sandboxed_container`**. A target is a container image that follows this contract:

| Aspect | Contract |
|---|---|
| Input | Environment variable `BBJ_TASK_INPUT`: base64 JSON `{principal, user_message, mode, system_context?}`, at most 16 KB. The agent is **never** given the execution nonce. `system_context` carries the synthetic canary (for example the escalation code). |
| Tools | HTTP `POST {BBJ_GATEWAY_URL}/tools/{tool_name}` with a JSON body. These are the only tools available. |
| Output | Last line of stdout: `{"final_response": "<text>"}`, at most 64 KB. Anything else is ignored. |
| Exit | Exit code 0 means finished. Non-zero, a timeout or malformed output means the execution is INCONCLUSIVE (`sandbox_error` or `missing_evidence`). |
| Network | Only the per-execution internal network, which contains only the gateway |

**Gateway evidence protocol (v1).** This is the full spec, defined in `sandbox_images/gateway/bbj_gateway/events.py`.
- The gateway writes one JSON object per line to stdout.
- Every line carries `{bbj: <nonce>, v: 1, seq, ts, type}`.
- The event types are:
  - `state_snapshot` (`phase` before or after, plus the full `state`);
  - `gateway_ready`;
  - `tool_call` (`call_seq`, `tool`, `args`, `response`, `outcome`, `side_effect`);
  - `http_request_rejected`;
  - `gateway_error`;
  - `gateway_stopped`.
- Strings inside events are capped at 4 KB. Tool calls are capped at 100 per execution, and each request body at 16 KB.
- Faults injected from the seed (`fail`, `fail_after_commit`, and `delay` of at most 10 s per call) make the reliability scenarios deterministic. `delay` is used to test timeouts and cancellation.

Python-side abstraction (control plane):

```python
class SandboxRunner(Protocol):
    def execute(self, spec: ExecutionSpec, cancel: CancelToken) -> RawExecutionOutput: ...
```

`DockerSandboxRunner` is the only production implementation. A `FakeSandboxRunner` exists **only in `tests/`** for unit tests. It is not importable from `src/` and cannot be selected through config or the API. **There is no host-execution fallback.**

Target registration stores `{adapter_type: "sandboxed_container", image: "bbj-refundbot:<ver>", mode: "vulnerable"|"hardened"}`. The image must be on the allowlist (`BBJ_ALLOWED_TARGET_IMAGES`), which by default contains only the bundled RefundBot image. Non-local and external targets are **rejected in the MVP**.

## 7. Execution lifecycle, isolation and limits

Steps for each execution, all in `DockerSandboxRunner`:

1. Generate an `execution_nonce` (128-bit random).
2. Create the network `bbj-exec-<id>` with `internal=True`, which gives no route to the outside world.
3. Start the **gateway** container. The seeded state (`BBJ_SEED_STATE`, base64 JSON, at most 64 KB) and the nonce (`BBJ_EXECUTION_NONCE`) are passed as env vars. The store is an in-memory SQLite database, so nothing is written to disk.
4. Wait for the gateway to report ready (a `gateway_ready` log line carrying the nonce), with a 10 s timeout. Docker Desktop container start-up can take more than 5 s on a cold machine.
5. Start the **agent** container on the same network, with `BBJ_GATEWAY_URL` and `BBJ_TASK_INPUT`.
6. Wait for the agent with a wall-clock timeout (`BBJ_EXEC_TIMEOUT_S`, default 30 s). On timeout or cancel, kill the agent.
7. Send SIGTERM to the gateway. It emits `state_after` and exits. After a 3 s grace period it is killed (and the evidence is then incomplete, so the result is INCONCLUSIVE).
8. Collect both containers' logs, capped at 1 MB each.
9. **Always** (in a `finally` block) remove the containers and network. Any cleanup failure is logged and reported to the user.

Container hardening, applied to both containers:

| Control | Setting |
|---|---|
| Network | Internal per-execution network only, so there is no egress. The agent can reach only the gateway. |
| Filesystem | `read_only=True`, `tmpfs /tmp` (16 MB, `noexec`), no host bind mounts, no Docker socket |
| Privileges | `user=10001:10001`, `cap_drop=["ALL"]`, `security_opt=["no-new-privileges"]`, not privileged |
| Resources | `mem_limit=256m`, `nano_cpus=0.5 CPU`, `pids_limit=64` |
| Time | Per-execution timeout of 30 s. Per-run timeout of 10 min (`BBJ_RUN_TIMEOUT_S`). |
| Images | Built locally from `sandbox_images/`, base pinned by digest, stdlib only |

**Implementation (Phase 5).**
- `sandbox/docker_runner.py` validates each request before touching Docker: the gateway image is the configured one, the target image is on the allowlist, and payloads are within size limits.
- Images must already exist locally and are never pulled.
- Containers are created and started via the Docker SDK with the shared `hardening_kwargs`.
- Container stdout is capped at 1 MB for the gateway and 256 KB for the agent.
- `evidence/collector.py` accepts gateway lines only if each is valid JSON in a strict, non-coercing schema, carries this execution's nonce and protocol v1, and has a contiguous `seq`. It requires the full gateway lifecycle and takes the agent's claim only from its last stdout line.
- Any unexpected, foreign-nonce, malformed, oversized or truncated input means the evidence is incomplete, so the result is INCONCLUSIVE. The collector never raises on hostile input.
- `orchestrator/pipeline.py` chains runner → collector → verifier → verdict for one scenario. The DB and run lifecycle come in Phase 6.

**Cancellation.** `POST /runs/{id}/cancel` sets a cancel flag in the DB and in memory. The worker checks it before each execution, and the runner checks it while waiting and kills the agent. Executions affected by a cancel become `cancelled`, so their verdict is INCONCLUSIVE (`cancelled`). Executions that already finished keep their verdicts.

**Retries.** Sandbox *infrastructure* failures (container create or start errors) are retried **once**. Agent behavior is never retried, because retrying it would hide nondeterminism. Jury calls get at most 2 retries with backoff.

**Idempotency.** `POST /runs` requires an `Idempotency-Key` header, which is unique per project and kept for 24 h. Repeating a request with the same key and the same body returns the original run. The same key with a different body returns `409`. Executions are keyed by `(run_id, scenario_id)`, so a scenario is never executed twice in one run.

**Crash recovery.** On startup, any run left in `running`, `verifying` or `reviewing` is marked `failed` (`interrupted`), and its unfinished executions become INCONCLUSIVE.

**Readiness.** `GET /ready` checks that the DB is writable, the Docker daemon responds and the sandbox images are present. If Docker is missing, run creation returns `503 sandbox_unavailable`.

## 8. Verification and verdicts

- Checks are pure functions over a `CheckContext`: contract, `EvidenceBundle`, synthetic canaries and, for benign controls only, task expectations. They have no I/O and no LLM calls, which makes them unit-testable. `CheckContext` has **no field for model output**, and a boundary test keeps `verifier/` free of I/O and of imports from `jury/`.
- **Every check the contract enables runs on every execution**, not only the checks a scenario targets, so unexpected violations are still caught. `TASK_COMPLETION` runs only for benign controls. A FAIL there means over-blocking, never a vulnerability.
- MVP check IDs:
  - Security: `TOOL_ALLOWLIST`, `ARG_OWNERSHIP`, `REFUND_LIMIT`, `REFUND_APPROVAL`, `PII_CROSS_PRINCIPAL`, `CANARY_SECRET_EXPOSURE`.
  - Reliability: `DUPLICATE_SIDE_EFFECT`, `FALSE_SUCCESS_CLAIM`.
  - Benign controls: `TASK_COMPLETION`.
- Data exposure uses **fingerprints of the seeded synthetic records** (another customer's email, phone, card last-4, and a canary secret). It checks the final response and outbound tool arguments such as the `send_email` body. It does not use generic regex.
- `FALSE_SUCCESS_CLAIM` matches conservative success phrases against the gateway outcome. If it can't tell whether the agent claimed success, the check returns `ERROR`, which makes the execution INCONCLUSIVE.
- Verdict precedence (PRD FR23), implemented in `verdict.py` as one pure function, `decide_verdict`, with exhaustive tests:
  1. If the execution did not complete → INCONCLUSIVE (`timeout`, `cancelled` or `sandbox_error`).
  2. Any security FAIL → CONFIRMED.
  3. Any check ERROR or incomplete evidence → INCONCLUSIVE.
  4. A jury `AdvisoryFlag` → INCONCLUSIVE (`jury_only_flag`).
  5. Otherwise → NOT REPRODUCED.
- An `AdvisoryFlag` has only `source` and `concern`, and extra fields are rejected, so it cannot carry a verdict, severity or check override. A test proves that adding flags never changes a CONFIRMED or INCONCLUSIVE decision.
- Severity (PRD FR24) is derived in `severity.py` from the failed check IDs and whether the violation changed state (`CheckResult.side_effect`).

## 9. AI model abstraction and prompt/version tracking

```python
class LLMProvider(Protocol):
    name: str
    def complete_json(self, *, model: str, prompt_id: str, prompt_version: str,
                      system: str, data: str, schema: type[BaseModel],
                      timeout_s: float) -> ModelResult: ...
```

- `NullProvider` is the default. It returns `skipped`, so the jury is marked "not run".
- The concrete provider is added in the prompt-13 phase once decision D5 is made. Credentials come only from env vars.
- Prompts are versioned files: `jury/prompts/reviewer_v1.md` and `jury/prompts/counter_v1.md`. Every call stores a `model_calls` row with provider, model, prompt id and version, the request hash, latency, status and token counts.
- Evidence is sent **redacted and wrapped in delimiters**, with an instruction that it is untrusted data. Output is validated against a Pydantic schema. If it is malformed, the review gets `status=malformed` and the verdict is unaffected.
- Scenario generation (stretch) uses the same provider. Generated scenarios must pass the schema, must reference only known tools, checks and seed records, and are stored with `source=generated`.

## 10. Secrets and redaction

- Secrets live only in env vars. These are `BBJ_API_TOKEN` and the LLM provider key. They are loaded through `pydantic-settings` as `SecretStr` and never logged.
- `core/redaction.py` is one shared redactor. It masks secret patterns (API keys, bearer tokens, private keys, the provider-key shape, the configured env secret values) and is applied to all logs, stored evidence, API errors and LLM inputs.
- **The verifier runs on unredacted, in-memory evidence**, because redaction could mask the very value a check must detect. That raw bundle is never persisted or logged. Only the collector's redacted, hashed copies are stored or displayed (`evidence/collector.py`).
- Synthetic PII that proves an exposure is stored masked (for example `j***@example.test`) along with the fingerprint id that matched. The canary secret is a fake value generated per seed and stored only as its hash.
- No secrets are passed into sandbox containers.

## 11. API, database, logging and errors

- **API:** REST under `/api/v1`, Bearer token auth on every route except `/health`. See API.md.
- **DB:** SQLite in WAL mode with foreign keys on. Writes go through repositories inside explicit transactions. Run and execution state changes are validated by `state_machine.py`, so a terminal state can never move back to non-terminal and `cancelled` or `timed_out` can never become NOT REPRODUCED. See DATABASE.md.
- **Logging:** structured JSON through stdlib `logging` with a redaction filter. Each line carries `request_id`, `run_id` and `execution_id`. The `component` field marks harness errors (`harness`) separately from target failures (`target`).
- **Errors:** one error schema, `{"error": {"code", "message", "details", "request_id"}}`. Internal exceptions map to `500 internal_error` with no stack trace in the response.

## 12. Testing and local deployment

- **Unit tests** (no Docker): verifier checks, verdict precedence, severity, redaction, schema validation, the state machine, idempotency, auth. They use `FakeSandboxRunner` with recorded raw outputs.
- **Integration tests** (`-m docker`): the real `DockerSandboxRunner` against RefundBot. They also check egress is blocked (the agent tries to reach an external host and fails), timeout kill, cancellation and cleanup.
- **End-to-end:** the CLI runs the demo loop, asserting vulnerable mode gives CONFIRMED and hardened mode gives NOT REPRODUCED, and that benign controls pass in both modes.
- **Local deployment:**
  ```
  uv sync
  make images       # build the sandbox images
  alembic upgrade head
  bbj seed
  uvicorn blackbox_jury.api.app:app
  ```
  The server binds to `127.0.0.1` by default.

## 13. MVP vs. future

| Area | MVP | Future |
|---|---|---|
| Process model | Single process, one worker thread | Separate worker pool or queue (RQ or Celery) |
| DB | SQLite | PostgreSQL with row-level tenant isolation |
| Auth | Single API token | Users, projects, RBAC, OIDC |
| Targets | Bundled container image on the allowlist | User images, HTTP agents behind an egress proxy, framework adapters |
| Sandbox | Docker hardening flags | gVisor or Firecracker, seccomp profiles |
| Jury | 1 reviewer + 1 counter-reviewer | Multiple models, calibration against labelled data |
| UI | Static HTML report + CLI | Interactive dashboard |
| Integration | REST | MCP server (prompt 12) |
