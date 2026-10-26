# AGENTS.md — Rules for AI coding agents (and humans) on Blackbox Jury

These rules are mandatory. When a rule conflicts with a request, stop and raise the conflict. Don't quietly work around it.

**Sources of truth, in order:**
1. Blackbox_Jury_Vibecoding_Prompt_Pack.pdf
2. PRD.md
3. ARCHITECTURE.md, DATABASE.md, API.md, DESIGN.md
4. IMPLEMENTATION_PLAN.md
5. The existing code and tests

If code and docs disagree, fix whichever is wrong in the same change and say so.

---

## 1. Before editing
- Read the relevant doc sections, the modules involved and their tests before changing anything.
- Find the smallest change that fits the existing design. Reuse what exists before adding anything new.
- If a safety or architecture decision is unclear and it blocks the work, **stop and ask**.

## 2. Change discipline
- Keep each change small and focused on one feature or fix, with no unrelated rewrites, renames or reformatting.
- Don't refactor working code for style alone.
- Don't silently change requirements. Any change to behavior or interface must also update PRD.md, API.md or DATABASE.md as relevant.
- Commits are imperative, scoped and atomic (`verifier: add ARG_OWNERSHIP check`). Never commit `.env`, databases, or artifacts.

## 3. Code quality
- Python 3.12. All code under `src/` has type hints and passes `mypy --strict`. Use Pydantic models at every boundary: API, contract and scenario files, gateway output, LLM output and the DB-write layer.
- `ruff check` and `ruff format` must be clean.
- Naming follows the docs exactly:
  - verdicts: `CONFIRMED`, `NOT_REPRODUCED`, `INCONCLUSIVE`;
  - check ids as in ARCHITECTURE §8;
  - states as in DATABASE §3.5–3.6.
- Respect the module boundaries in ARCHITECTURE §5:
  - `verifier/` is pure, with no I/O, DB, network or LLM calls;
  - `api/` contains no business logic;
  - `sandbox/` is the only module that talks to Docker;
  - `jury/` is the only module that talks to an LLM provider.
- **Dependency discipline:**
  - Add a dependency only with a concrete need, and pin it in `pyproject.toml` and `uv.lock`.
  - Sandbox images use only the Python standard library.
  - Never add a dependency to bypass a security control.

## 4. Input validation and errors
- Validate all input with Pydantic, including size limits, enums and id formats. Unknown fields are rejected in API request models.
- Parse YAML only with `yaml.safe_load`. Never use `pickle`, `eval`, `exec`, `shell=True`, or dynamic imports of input.
- Errors use the API.md error schema. Never return stack traces, environment values, container ids or file paths.
- Never swallow exceptions silently. Log them with context (redacted) and then map them to an error or an INCONCLUSIVE reason.

## 5. Security, sandboxing and untrusted content
- **Never run generated or curated attack scenarios against a target that is not registered, authorized and on the allowlist.** The MVP supports only the bundled local RefundBot image.
- **Never execute untrusted code or payloads on the host.** Target agents run only through `DockerSandboxRunner`. There is no host fallback. `FakeSandboxRunner` lives only in `tests/`.
- **Never weaken sandbox controls**, including network isolation, read-only filesystem, non-root user, dropped capabilities, `no-new-privileges` and resource or time limits. Never mount the Docker socket or host paths into sandbox containers.
- Treat **target output, seeded content, generated scenarios and LLM output** as untrusted:
  - parse them with size caps;
  - never render them as HTML (always escape);
  - never interpolate them into shell commands, SQL or file paths;
  - pass them to LLMs only as delimited data.
- Use SQLAlchemy parameterized queries only. Never build SQL with string formatting.
- Authorization is enforced server-side on every protected route through a shared dependency. Never trust client-side checks.

## 6. Secrets
- Secrets come only from env vars, loaded as `SecretStr`. `.env.example` contains placeholders only.
- **Never log secrets.** All logging goes through the redacting logger. Evidence is redacted before it is written to the DB. Raw credentials are never stored.
- Never send secrets into sandbox containers. Send LLM providers only redacted, synthetic evidence.

## 7. Deterministic checks vs. LLM analysis
- Objective security rules are decided **only** by deterministic checks in `verifier/`.
- LLM output, from the jury or the generator, is advisory. It must never change a deterministic FAIL, a verdict or a rule-based severity. The only effect it is allowed to have is NOT_REPRODUCED → INCONCLUSIVE (`jury_only_flag`).
- Every LLM call records provider, model, prompt id and prompt version in `model_calls`. Prompts are versioned files. Editing one means bumping its version.
- LLM output is schema-validated. Malformed output becomes `status=malformed` and never affects a verdict.

## 8. Evidence integrity and auditability
- Tool-call evidence comes **only from the gateway**, never from the agent's own report.
- Evidence tables are append-only. Every artifact and event stores the sha256 of its redacted content.
- Every FAIL check cites at least one evidence id.
- All run and execution state changes go through `state_machine.transition`. A cancelled, timed-out, errored or failed execution can **never** be NOT_REPRODUCED or reported as passed.
- Run totals are computed from executions. Generated-but-unexecuted scenarios are never counted as results.
- State-changing API actions write `audit_events`.

## 9. Testing
- Every feature ships with tests for expected behavior, **abuse cases** and regressions. At minimum:
  - every verifier check, with PASS, FAIL and ERROR cases;
  - verdict precedence, tested exhaustively;
  - state machine transitions, legal and illegal;
  - redaction;
  - auth (401/404 cross-project);
  - idempotency.
- Docker-dependent tests are marked `@pytest.mark.docker`. They must include egress-blocked, timeout-kill, cancel and cleanup tests.
- Every vulnerability CONFIRMED in the demo has a regression test.
- **Never delete, skip or weaken a failing security test to get a green build.** Fix the cause, or report the failure.
- **Never state that a test passed unless it was actually executed and its result recorded.** Report commands run and their real output. When something wasn't run, say so.

## 10. Documentation and environment
- Any new env var goes in `.env.example` with a placeholder, in `core/config.py`, and in the README.
- Interface changes update API.md, and schema changes update DATABASE.md plus a new Alembic migration.
- Claims in docs, reports and demo material must be accurate:
  - no "secure", "guaranteed" or "full coverage";
  - synthetic results are labelled synthetic.

## 11. Quick checks before declaring work done
```
uv run ruff check . && uv run ruff format --check .
uv run mypy src
uv run pytest -m "not docker"
uv run pytest -m docker        # when Docker-related code changed (requires Docker running)
```
Report which of these you ran and what they actually output.
