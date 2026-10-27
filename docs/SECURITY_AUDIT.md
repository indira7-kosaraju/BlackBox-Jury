# Security audit and code review: Blackbox Jury MVP

**Date:** 2026-10-27 · **Scope:** prompts 16 (security audit and hardening) and 17 (code review) · **Target:** this repository and the local sandbox only. No third-party systems were tested.

> Finding no issue in an area does **not** mean the system is secure. This is a time-boxed MVP review.

## 1. Method

- Concrete checks run against the code and the real sandbox:
  - git-history secret scan;
  - route inventory;
  - a container isolation probe;
  - a dependency audit;
  - a fresh-checkout start.
- An **independent code review** by a separate reviewer reading the code fresh, read-only. Every finding was verified against the code before being fixed.
- Each fix shipped with a regression test.

## 2. Findings and fixes

| # | Severity | Finding | Evidence | Fix | Test |
|---|---|---|---|---|---|
| A1 | High | Executions cancelled or timed out **before starting** were counted as `executed`, so an all-inconclusive run could report "No violations observed in 10 executed scenarios". An existing test asserted the wrong behaviour. | Code review: `repo.totals`, `_finish_pending` | `executed` now counts only started executions; new `not_run` count; the empty state requires 0 inconclusive; an inconclusive table with reasons | `test_cancel_mid_run`, `test_cancel_queued_run`, `test_cancelled_run_shows_not_run`, `test_inconclusive_run_renders` |
| A2 | Medium | A jury **persistence** error (for example a DB lock) failed a run whose deterministic results were already final | Code review: `run_service._review` | Persistence is wrapped and logged; the run completes. Only the CONFIRMED-change invariant (`JuryInvariantError`) still raises. | `test_jury_persistence_failure_does_not_fail_the_run` |
| A3 | Medium | A fresh checkout couldn't start: migrations ran before `data/` existed | Fresh-checkout run | `upgrade()` creates the directory (mode 0700) | Verified in an empty dir; `test_database_files_are_owner_only` |
| A4 | Low | DB files were world-readable (0644) | `ls -l` | Directory 0700, DB/WAL/SHM files 0600 | `test_database_files_are_owner_only` |
| A5 | Low | The sandbox execution id differed from the DB id (orphaned containers untraceable); the evidence-ref fallback could produce a ref pointing at nothing | Code review | The persisted id is passed to the sandbox; `artifact_ref` raises instead of fabricating | `test_sandbox_receives_the_persisted_execution_id`, `test_artifact_refs_are_never_fabricated` |
| A6 | Low | Agent output containing `</evidence>` could close the jury's data delimiter | Code review | `<` and `>` are JSON-escaped, and truncation keeps the JSON valid | `test_evidence_delimiter_cannot_be_closed_by_agent_output` |
| A7 | Low | A jury concern with **no** citations counted as evidence-backed | Code review | At least one valid citation is required | `test_concern_without_citations_is_not_flagged` |
| A8 | Low | A cancel arriving between executions could signal the previous token, letting one more scenario run | Code review | The token is installed before the cancel check | Fixed by construction (race not reproducible in a stable test) |
| A9 | Low | The CLI polling about 2 req/s could exceed the 120/min default limit mid-run | `make qa` hit 429 | Default limit raised to 600/min for this local single-user tool; the CLI honours 429 on GETs | `test_cli_retries_get_on_429` |
| A10 | Info | Dead code: the `jury_only_flag` branch in `_persist` | Code review | Removed | — |

## 3. Areas audited with no open finding

| Area | What was checked | Result |
|---|---|---|
| Authentication / authorization | **Every served route** (enumerated from OpenAPI) returns 401 without a token; constant-time token compare; all lookups scoped by project (404 for foreign ids); target authorization required, and revocation enforced | `test_every_registered_route_requires_auth`, `test_unknown_or_foreign_ids_are_404`, `test_revoked_target_cannot_be_retested` |
| SSRF / arbitrary hosts | No API input takes a URL, host or command (`_EXTERNAL_KEYS` rejected); the only HTTP client in `src/` is the CLI, restricted to 127.0.0.1/localhost; the gateway URL is fixed and internal | grep plus `test_target_validation`, `test_cli_refuses_non_local_api` |
| Command execution / sandbox escape | No `subprocess`, `os.system` or `exec` in `src/` (AST test); only `sandbox/` imports docker; containers run as uid 10001 with a read-only rootfs, noexec tmpfs, `cap_drop ALL`, `no-new-privileges`, no mounts, no ports, no Docker socket, memory/CPU/PID limits | `test_runner_guards.py`, `test_container_isolation_probe`, `test_memory_limit_is_enforced` |
| Network egress | A per-execution `internal` network. From inside the sandbox, IPv4 1.1.1.1:443, 8.8.8.8:53, IPv6 2606:4700:4700::1111:443, `host.docker.internal:80` and external DNS **all fail** | `test_container_isolation_probe` (real Docker) |
| Prompt injection via target output / evidence | The verifier is deterministic; LLM output can only become an `AdvisoryFlag` (no verdict or severity fields); hostile strings in evidence are inert; jury input is delimited and escaped | `test_verdict.py`, `test_hostile_strings_in_evidence_are_inert`, `test_jury.py` |
| Evidence integrity | Nonce-tagged, strict schema, contiguous seq, full lifecycle; a forged foreign-nonce line is discarded and marks evidence incomplete; evidence tables are append-only; hashes cover the redacted content | `test_collector.py` (38 tests) |
| Verdict state transitions | A state machine with optimistic guards; 28 DB CHECKs; non-completed executions can never pass | `test_db.py`, `test_verdict.py` |
| Injection / deserialization | ORM-parameterized SQL only (gateway table names come from a fixed allowlist); `yaml.safe_load` only (Python tags rejected); JSON parse errors and deep nesting handled; Jinja2 autoescape always on | `test_contracts.py`, `test_scenarios.py`, `test_report_html.py` |
| Secrets | Env-only `SecretStr`; a redacting JSON logger; evidence redacted before storage; API token **0 occurrences** in live server logs; git history contains only obviously fake test fixtures (8 strings, all in `tests/`); no `.env`, DB or key files tracked | Live run; history scan |
| Error leakage | 500 responses are generic; 422 responses never echo submitted values | `test_api_skeleton.py` |
| Abuse / DoS | One active run; per-execution 30 s and per-run 600 s limits; 100 tool calls, 16 KB bodies, 4 KB strings, 1 MB gateway logs, 256 KB artifacts, 60 KB jury payload; 256 KB API bodies (411 for chunked); rate limits | Various |
| Dependencies | 168 locked packages (all extras) | `pip-audit`: no known vulnerabilities |
| HTML report | No scripts or external resources; strict CSP; escaping of `<script>`, `onerror` and template syntax | `test_report_html.py` |

## 4. Residual risks (accepted for the MVP)

- **The control plane needs the Docker socket.** Whoever can run Blackbox Jury can control Docker on that host. Run it only on a trusted local machine.
- **Container isolation is runc plus hardening flags.** There is no gVisor or custom seccomp profile, so kernel or runtime escapes are out of scope.
- **Images are trusted by local tag.** The image ID is recorded per run, but signatures are not verified.
- **Single shared API token, in-memory rate limits, SQLite.** There are no users or roles and no prune/retention job.
- **The `verifying` run state** is defined in the PRD and DB but unused. Verification happens per execution inside `running`. It is kept for compatibility with PRD §10.
- **The jury has not been exercised against the live API,** and its prompts are unevaluated.
