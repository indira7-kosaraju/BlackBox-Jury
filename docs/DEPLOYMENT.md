# Deployment, operations and monitoring: Blackbox Jury MVP

**Scope:** prompts 18 (deployment) and 19 (monitoring). The approved deployment is **one trusted local machine** running Docker Desktop or Docker Engine. Blackbox Jury is **not** designed for internet exposure or multi-user hosting.

## 1. Safe defaults

| Setting | Default | Why |
|---|---|---|
| Bind address | `127.0.0.1:8000` (`BBJ_HOST`, `BBJ_PORT`) | Not reachable from the network |
| Targets | Only `bbj-refundbot:0.1.0` (`BBJ_ALLOWED_TARGET_IMAGES`) | No arbitrary or external targets; images never pulled |
| Sandbox | Per-execution internal network, read-only rootfs, uid 10001, `cap_drop ALL`, `no-new-privileges`, 256 MiB / 0.5 CPU / 64 PIDs | Isolation is tested in `pytest -m docker` |
| Limits | 30 s per execution, 600 s per run, one active run | Bounded resource use |
| Jury / LLM | `BBJ_LLM_PROVIDER=none` | Fully offline; no data leaves the machine |
| Data | Synthetic seed only; DB at `data/bbj.db` (dir 0700, file 0600) | No real customer data |
| OpenAPI docs | only with `BBJ_ENV=dev` | Smaller surface in demo and prod |
| CORS | none | Browsers cannot call the API cross-origin |

## 2. Build and start (reproducible)

```bash
uv sync                      # locked deps (uv.lock); add --extra llm only if you will enable the jury
cp .env.example .env         # set BBJ_API_TOKEN (>= 32 chars); never commit .env
make images                  # builds bbj-gateway:0.1.0 and bbj-refundbot:0.1.0 (digest-pinned base)
make run                     # applies migrations, recovers interrupted runs, sweeps orphaned sandboxes
make seed                    # in a second terminal: contract, 10 scenarios, RefundBot target
make demo                    # bbj run --suite demo
```

**Readiness:** `GET /api/v1/ready` with the bearer token returns 200 only when the database, Docker and both images are OK. `GET /health` is unauthenticated liveness.

**Migrations:** Alembic `0001` is applied automatically at startup, and also by `bbj db upgrade` / `uv run alembic upgrade head`. Migrations are idempotent.

## 3. Manual approvals and boundaries

These are deliberate. Do not bypass them.
- **Registering or authorizing a target** requires `confirm_authorized: true`, which is recorded in the audit log. The CLI asks for confirmation when changing a target's mode.
- **Enabling the jury** (`BBJ_LLM_PROVIDER=anthropic`) sends **redacted synthetic** evidence to Anthropic. It needs an explicit decision by the team and an API key.
- **Adding a target image** to `BBJ_ALLOWED_TARGET_IMAGES` extends what may be executed in the sandbox. Only add images you own and are authorized to test.

**Unimplemented security boundaries:**
- multi-user auth or RBAC;
- TLS (localhost only);
- image signature verification;
- a gVisor or seccomp profile;
- a retention/prune job.

See [SECURITY_AUDIT.md](SECURITY_AUDIT.md) §4.

## 4. Timeouts, cancellation and cleanup behaviour

- **Timeout:** the agent is killed at `BBJ_EXEC_TIMEOUT_S`. The gateway is stopped with SIGTERM (graceful, emits its final state), then SIGKILL after 3 s. The execution is INCONCLUSIVE (`timeout`).
- **Cancel:** `POST /runs/{id}/cancel` or `bbj cancel`. The running sandbox is killed, and pending scenarios become INCONCLUSIVE with `not_run`.
- **Cleanup:** containers and the network are always removed (`finally`), with `cleanup_ok` recorded per execution.
- **Crash recovery:** on restart, interrupted runs are marked `failed`, and labelled orphaned containers and networks are removed.

## 5. Storage and retention

- **SQLite** at `BBJ_DATABASE_URL` (default `data/bbj.db`, git-ignored).
- **Evidence** is stored redacted, with artifacts capped at 256 KB.
- **Reports** go to `reports/out/` (git-ignored).
- **There is no automatic retention yet.** `bbj prune` is not implemented, so delete demo data manually (below).

## 6. Shut down and clean up

```bash
# stop the API: Ctrl+C in the `make run` terminal
make clean-demo      # asks first; removes data/ and reports/out/, plus any bbj-labelled containers/networks
make clean-images    # optional: remove bbj-gateway / bbj-refundbot images
```

`clean-demo` touches nothing outside `data/`, `reports/out/` and Docker resources labelled `bbj.execution`.

## 7. Monitoring (proportional to a local MVP)

| What | Where it is recorded | How to inspect |
|---|---|---|
| Run states (`queued`/`running`/`reviewing`/`completed`/`failed`/`cancelled`/`timed_out`) | `runs` table; `GET /runs` | `bbj status <run>`; `sqlite3 data/bbj.db "select status, count(*) from runs group by 1"` |
| Execution outcome, duration, exit code, cleanup | `executions.sandbox_meta` (`duration_ms`, `agent_exit_code`, `cleanup_ok`, `outcome`, `error`) | `GET /executions/{id}` |
| Evidence and verifier problems | `executions.evidence_problems`; check results with `ERROR` | Report "Evidence problems" banner; `inconclusive_reason` |
| Findings by severity and verdict | `findings`, `executions`; report totals | JSON/HTML report |
| Target-agent vs harness failures | JSON logs carry `component` = `target` / `harness` / `api` / `jury`; run `status_reason` (`sandbox_unavailable`, `interrupted`, `harness_error`) | Server stderr (redacted JSON lines) |
| Model-provider calls | `model_calls` (provider, model, prompt version, status, latency, tokens, retries) | SQL |
| Rate limits / auth failures | 429 responses; `audit_events` `auth.denied` (at most 1 per client per minute) | SQL |
| Database health | `GET /ready` → `database` | — |

**Suggested alert thresholds** (manual during the demo; nothing pages):
- any run `failed` with `sandbox_unavailable`, meaning Docker is down;
- `cleanup_ok = false`, meaning leftover containers;
- more than 20% of executions in a run INCONCLUSIVE;
- any `model_calls.status` in (`error`, `rate_limited`) on two consecutive runs;
- `/ready` not ready.

**Observability gaps:**
- no metrics endpoint or time series;
- no log shipping (logs go to stderr only);
- no disk-usage tracking for the DB;
- no per-container CPU or memory usage capture beyond the configured limits;
- no alerting system.
