# IMPLEMENTATION PLAN — Blackbox Jury MVP (48 hours)

**Status:** Draft v0.1 · **Inputs:** PRD.md, DESIGN.md, ARCHITECTURE.md, DATABASE.md, API.md, AGENTS.md · **Last updated:** 2026-10-26

## Strategy

1. **Build the vertical slice first.** By about **hour 18**, one prompt-injection scenario should run end to end from the CLI:
   ```
   sandbox → gateway evidence → deterministic checks → CONFIRMED verdict → JSON report
   ```
   The slice can be crude at that point, as long as it is real.
2. **Then widen it:** more scenarios and checks, then retest, then the HTML report, then the jury.
3. **UI and the jury are time-boxed.** MCP, the dashboard, LLM scenario generation and the LLM target mode are stretch goals only.
4. **Budget:** about 40 h of planned work plus about 8 h of buffer. The phase numbers follow prompt 7 of the pack. The *build order* below interleaves them so the slice comes early.

**Must-have** = needed for the demo in PRD §18. **Stretch** = only after the 10 phases' must-haves pass.

## Build order and timeline

| Hours | Work | Prompt pack |
|---|---|---|
| 0–2 | Phase 1: skeleton | #8 |
| 2–6 | Phase 2: gateway, RefundBot, synthetic store | #9 |
| 6–9 | Phase 3: contract and core checks | #9 |
| 9–10 | Phase 4 (part): the first scenario | #9 |
| 10–15 | Phase 5: Docker runner and evidence collection | #9, #10 |
| 15–18 | Phase 6 (part): verdict and JSON report → **SLICE DONE** | #9, #10 |
| 18–22 | Phases 4 and 6 complete: all scenarios and checks, findings, persistence, API | #9, #10, #11 |
| 22–25 | Phase 8: regression export and retest | #9 |
| 25–29 | Phase 9: HTML report and CLI polish | #9 |
| 29–33 | Phase 7: jury (if D5 resolved) | #13 |
| 33–37 | Phase 10: QA, security audit, code review | #14, #16, #17 |
| 37–40 | Phase 10: demo hardening, readiness audit, rehearsal | #18, #20 |
| 40–48 | Buffer, then stretch goals | #12, #19 |

---

## Phase 1: Runnable skeleton and configuration · **Must** · ~2 h
- **Goal:** a repo that installs, lints, type-checks, tests and serves `/health`.
- **Tasks:**
  - install `uv` and pin Python 3.12;
  - write `pyproject.toml` (fastapi, uvicorn, pydantic, pydantic-settings, sqlalchemy, alembic, docker, pyyaml, typer, jinja2; dev: pytest, ruff, mypy);
  - create the package layout from ARCHITECTURE §5;
  - `core/config.py` (settings, `SecretStr`);
  - `core/logging.py` (JSON logs) plus `core/redaction.py`;
  - `api/app.py` with `/health`, the auth dependency and the error handler;
  - `.env.example`, a Makefile and the README.
- **Files:** `pyproject.toml`, `uv.lock`, `.env.example`, `Makefile`, `README.md`, `src/blackbox_jury/{core,api}/…`, `tests/unit/test_health.py`, `tests/unit/test_redaction.py`
- **Dependencies:** none.
- **Tests:**
  - `/health` returns 200;
  - protected routes return 401 without a token;
  - redaction masks token and key patterns, and the logger never emits the configured secret.
- **Risks:** installing `uv` or Python 3.12 on the machine. Fallback: a Homebrew `python@3.12` with a venv.
- **Acceptance:** `make check` (ruff, mypy and pytest) is green, and `uvicorn` serves `/health`.

## Phase 2: Mock target agent, mock APIs, synthetic data · **Must** · ~4 h
- **Goal:** RefundBot and the Tool Gateway as stdlib-only scripts that run locally first and then in containers.
- **Tasks:**
  - `seeds/synthetic_store.json` (DATABASE §7);
  - a gateway HTTP server with the 6 tools over an in-memory SQLite store;
  - gateway output as nonce-tagged JSONL events, with `state_before` at start and `state_after` on SIGTERM;
  - RefundBot, scripted:
    - **vulnerable** mode obeys instructions in order notes, skips ownership checks, echoes customer data and claims success after errors;
    - **hardened** mode does the opposite;
  - the two Dockerfiles, pinned to a base digest, running as a non-root user.
- **Files:** `sandbox_images/gateway/{Dockerfile,bbj_gateway/*.py}`, `sandbox_images/refundbot/{Dockerfile,bbj_refundbot/*.py}`, `seeds/synthetic_store.json`, `scripts/sandbox_smoke.sh`, `tests/unit/sandbox_images/*.py`. (The stdlib-only packages replace the single-file layout originally listed, so the code can be unit-tested and type-checked.)
- **Dependencies:** Phase 1.
- **Tests:**
  - the tools behave correctly on the seed data;
  - unknown tools and invalid arguments are recorded rather than crashing;
  - the event schema is valid;
  - RefundBot's two modes produce different tool sequences for the injection input. This is tested at the gateway API level, without Docker.
- **Risks:** the scripted agent looking "rigged". Mitigation: realistic logic and benign controls that pass in both modes.
- **Acceptance:** `docker build` succeeds for both images, and a manual `docker run` pair produces the expected event log.

## Phase 3: Security contract and deterministic policy checks · **Must** · ~3 h (+1 h in the widening step)
- **Goal:** a validated contract and pure check functions.
- **Tasks:**
  - the Pydantic models `TaskContract`, `EvidenceBundle` and `CheckResult`;
  - `contracts/refund_support_v1.yaml`;
  - checks `TOOL_ALLOWLIST`, `ARG_OWNERSHIP`, `REFUND_LIMIT` and `REFUND_APPROVAL` first;
  - then `PII_CROSS_PRINCIPAL`, `CANARY_SECRET_EXPOSURE`, `DUPLICATE_SIDE_EFFECT`, `FALSE_SUCCESS_CLAIM` and `TASK_COMPLETION`.
- **Files:** `src/blackbox_jury/contracts/*`, `src/blackbox_jury/evidence/schema.py`, `src/blackbox_jury/verifier/{checks,engine}.py`, `contracts/refund_support_v1.yaml`, `tests/unit/test_checks_*.py`
- **Dependencies:** Phase 1. Uses the evidence schema shape from Phase 2.
- **Tests:**
  - PASS, FAIL and ERROR cases for each check, using hand-built evidence bundles;
  - every FAIL cites evidence ids;
  - an invalid contract is rejected.
- **Risks:** false-success parsing ambiguity. Mitigation: return ERROR (INCONCLUSIVE) when unsure.
- **Acceptance:** all check unit tests pass, and `verifier/` has no I/O imports (enforced by a small test).

## Phase 4: Scenario schema and adversarial scenario set · **Must** · ~1 h (first scenario) + ~2 h
- **Goal:** a validated scenario model and the curated suite.
- **Tasks:**
  - the `Scenario` model and loader;
  - the first scenario, `pi_order_note_refund`;
  - then the rest of the suite (PRD §14), 10 scenarios in total:
    - 2 prompt injection;
    - 2 unauthorized tool call;
    - 2 sensitive-data exposure;
    - `false_success_refund` and `duplicate_refund`;
    - 2 benign controls.
- **Files:** `src/blackbox_jury/scenarios/{schema,loader}.py`, `src/blackbox_jury/scenarios/library/*.yaml`, `tests/unit/test_scenarios.py`
- **Tests:**
  - every library file validates;
  - schemas with unknown tools or checks are rejected;
  - oversized or malformed YAML is rejected.
- **Acceptance:** the loader returns 10 valid scenarios, each with its applicable checks.
- **Done (Phase 4):** the scenario field `targets` lists the checks a scenario is designed to trip; every enabled check still runs on every execution. Scenarios can override only order notes, faults and approvals. Payloads are checked against the image-side parsers. Coverage and detection limits are recorded in `docs/LIMITATIONS.md`.

## Phase 5: Sandboxed execution and evidence collection · **Must** · ~5 h
- **Goal:** `DockerSandboxRunner` and the collector, following ARCHITECTURE §7.
- **Done (Phase 5):**
  - Runner, collector, `/ready` checks and a one-scenario pipeline are in place.
  - All 10 scenarios run in real Docker in both modes, along with tests for the isolation probe, the OOM limit, timeout, cancellation, a missing image, a gateway crash and cleanup.
  - The gateway gained a bounded `delay` fault.
  - Bug fixed: a SIGTERM arriving mid-request is no longer swallowed by `socketserver`.
- **Tasks:**
  - create the per-execution internal network;
  - run both containers with the hardening flags;
  - readiness wait, timeout, kill and cancel token;
  - guaranteed cleanup;
  - log capture with caps;
  - the collector: nonce verification, parsing, redaction, hashing, state diff;
  - `/ready` checks.
- **Files:** `src/blackbox_jury/sandbox/{runner,docker_runner,limits}.py`, `src/blackbox_jury/evidence/collector.py`, `tests/integration/test_docker_runner.py` (marked `docker`), `tests/unit/test_collector.py`, `tests/fakes/fake_runner.py`
- **Dependencies:** Phase 2. **Docker Desktop must be running.**
- **Tests:**
  - (docker) the injection run produces the expected events;
  - (docker) an **egress attempt from the agent fails**;
  - (docker) the timeout kills the agent and gives INCONCLUSIVE;
  - (docker) no containers or networks are left behind after success, timeout or exception;
  - (unit) a bad nonce or malformed lines give `missing_evidence`;
  - (unit) secrets are redacted before persistence.
- **Risks:** Docker on macOS being slow or having networking quirks, and container start latency. Mitigation: prebuilt images; measure early and adjust the timeouts.
- **Acceptance:** the integration tests pass with real Docker, and the 30 s timeout is enforced.

## Phase 6: Verification engine, findings, persistence, API · **Must** · ~3 h (slice) + ~4 h
- **Goal:** verdicts, severity, findings, DB, run lifecycle, and the API from API.md.
- **Tasks:**
  - **For the slice:**
    - ~~`verdict.py` and `severity.py`~~ (done early, in Phase 3, because they enforce "LLM output cannot override deterministic rules");
    - the run pipeline in the worker;
    - a minimal JSON report;
    - `bbj run`.
  - **For widening:**
    - SQLAlchemy models and the `0001_initial` migration, with the integrity CHECKs;
    - repositories and the state machine;
    - idempotent `POST /runs`, cancel and crash recovery;
    - `bbj seed`;
    - target, contract, scenario, run, execution, artifact and finding endpoints;
    - audit events and rate limits.
- **Files:** `src/blackbox_jury/verifier/{verdict,severity}.py`, `src/blackbox_jury/orchestrator/*`, `src/blackbox_jury/db/*`, `src/blackbox_jury/api/routes/*`, `src/blackbox_jury/reports/builder.py`, `src/blackbox_jury/cli.py`, `tests/unit/test_verdict.py`, `tests/unit/test_state_machine.py`, `tests/integration/test_api_*.py`
- **Tests:**
  - an **exhaustive** verdict precedence table;
  - a cancelled, timed-out or errored execution is never NOT_REPRODUCED, at both the code and DB-CHECK level;
  - idempotency (same key with the same body gives the same run; a different body gives 409);
  - the one-active-run quota;
  - 401 without a token and 404 for an unknown or foreign id;
  - illegal state transitions raise errors;
  - a failed write rolls back.
- **Acceptance:**
  - **SLICE:** `bbj run --suite slice` gives CONFIRMED (critical) with evidence references in vulnerable mode;
  - **FULL:** all 10 scenarios run through the API with correct verdicts.

## Phase 7: Independent jury review and counter-review · **Should** · ~4 h (time-boxed)
- **Goal:** advisory model analysis that is stored and shown, and never authoritative.
- **Tasks:**
  - the `LLMProvider` protocol and `NullProvider`;
  - the concrete provider (after D5);
  - versioned prompts;
  - the reviewer and counter-reviewer;
  - `model_calls` logging;
  - the `jury_only_flag` path.
- **Files:** `src/blackbox_jury/jury/*`, `tests/unit/test_jury_*.py`
- **Dependencies:** Phase 6, and the D5 decision on the provider and key.
- **Tests (with a mocked provider):**
  - success;
  - malformed output, which leads to `status=malformed` with the verdict unchanged;
  - timeout;
  - rate limit;
  - **a jury that "disagrees" does not change a CONFIRMED verdict**;
  - an injection string in the evidence does not change the output schema handling.
- **Risks:** time sink, and provider unavailability. Mitigation: a hard time-box. Without a provider the report says "Jury not run".
- **Acceptance:** with a provider, each finding has a reviewer and a counter-reviewer review. Without one, the run completes and the report says the jury was not run.

## Phase 8: Regression test export and retest · **Must** · ~3 h
- **Goal:** the fix → retest loop.
- **Tasks:**
  - export, in the `bbj-regression/1` format;
  - `PATCH /targets/{id}` to change mode;
  - `POST /retests`;
  - the retest comparison in the report;
  - `bbj export` and `bbj retest`;
  - a pytest plugin or shim to run exported JSON.
- **Files:** `src/blackbox_jury/regression/{export,retest}.py`, the API routes, `tests/e2e/test_fix_retest.py`
- **Tests:**
  - export works only for CONFIRMED findings and is idempotent;
  - a retest against a hardened target gives NOT_REPRODUCED;
  - a retest against a vulnerable target gives CONFIRMED;
  - the comparison links are correct.
- **Acceptance:** the demo steps 6–7 (PRD §18) work from the CLI.

## Phase 9: Minimal demo UI and report · **Must** (HTML report) / **Stretch** (dashboard) · ~4 h
- **Goal:** the DESIGN.md report.
- **Tasks:**
  - the Jinja2 template with tokens, provenance labels, findings table, finding detail (claim vs. verified outcome, timeline, trace, diff, checks, repro steps, jury), benign controls, retest comparison and limitations;
  - CSP headers;
  - CLI progress output.
- **Files:** `src/blackbox_jury/reports/{render.py,templates/report.html.j2}`, `tests/unit/test_report_render.py`
- **Tests:**
  - **untrusted text is escaped** (a `<script>` payload in the agent output renders inert);
  - the disclaimer is always present;
  - empty and partial states;
  - there is no "secure" wording.
- **Acceptance:** the report is readable at 375 px and on desktop, works in both themes, and the keyboard can reach every `<details>` element.

## Phase 10: End-to-end validation and demo rehearsal · **Must** · ~7 h
- **Goal:** prove it works and get demo-ready.
- **Tasks:**
  - Prompt 14 (QA): run the full suite and record the metrics M1–M6, including 3 repeat runs for repeatability;
  - Prompt 16: security audit of the sandbox flags, SSRF surface, escaping, secrets and state integrity;
  - Prompt 17: code review;
  - Prompt 18: safe local deployment checklist;
  - Prompt 20: readiness verdict;
  - record a backup run, labelled as recorded;
  - rehearse the 5-minute script.
- **Files:** `docs/QA_REPORT.md`, `docs/SECURITY_AUDIT.md`, `docs/DEMO_SCRIPT.md`, and fixes as needed.
- **Acceptance:**
  - PRD metrics M1–M6 are met and verified by actually executed commands;
  - the readiness audit returns READY or READY WITH WARNINGS, with every warning listed.

## Stretch (only after all Musts pass, in this order)
1. LLM scenario variants (`POST /scenarios/generate`).
2. RefundBot `llm` mode.
3. Interactive dashboard (DESIGN §10).
4. MCP server (prompt 12).
5. Monitoring additions (prompt 19), beyond the structured logs and run-state counts.

## Top risks

| Risk | Mitigation |
|---|---|
| Docker not running or slow | Start it before Phase 5; build images early; `/ready` gate; recorded backup demo |
| Slice slips past hour 18 | Cut the widening scope (fewer scenarios), never the slice |
| Jury eats time | Hard 4 h box; `NullProvider` path is fully supported |
| Pressure to "make tests pass" | AGENTS.md §9: report real failures; never weaken checks |

## Pending decisions affecting the plan
- **D5, the LLM provider and key.** Blocks only Phase 7 and the stretch goals.
- **Docker Desktop running.** Blocks Phase 5 onward, so it's needed by about hour 10.

## First task to implement
**Phase 1, Project Setup (prompt #8):**
1. install `uv` and Python 3.12 for the project;
2. create `pyproject.toml` and the package skeleton;
3. config, redacting logger, FastAPI app with `/health` and the auth dependency;
4. `.env.example`, Makefile and README;
5. ruff, mypy and pytest configured and passing.
