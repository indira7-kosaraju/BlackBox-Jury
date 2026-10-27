# Demo readiness audit: Blackbox Jury MVP

**Date:** 2026-10-27 · **Scope:** prompt 20 · **Basis:** checks and tests actually executed (listed below), not code inspection alone.

## Verdict: **READY WITH WARNINGS**

There are **no blockers**. The core loop works end to end against the real Docker sandbox on a fresh copy of the repository. The warnings below are things the team must know or check manually before presenting.

## Warnings (highest first)

1. **Docker Desktop must be running.** Without it, nothing executes, by design: the run returns `sandbox_unavailable`. Start it before the demo and check `/ready`.
2. **The target is scripted.** RefundBot's vulnerable and hardened modes are deterministic. That is honest and repeatable, but say so explicitly. Results are synthetic and do not measure detection quality on real, model-driven agents.
3. **The jury (LLM) is untested against the live API.** It is off by default and covered only by mocked tests. Do not enable it on stage unless someone has tested it with a real key beforehand.
4. ~~The HTML report has not been viewed in a browser.~~ **Verified 2026-10-27** with headless Chrome:
   - At an exact 375 px viewport (rendered in an iframe), the page fits. The findings table scrolls inside its own container, and the page does not scroll sideways.
   - Light theme (forced) and dark theme both render correctly; desktop width (1280 px) renders correctly.
5. **The CLI needs `PYTHONPATH=src`** because of macOS hidden `.pth` files. Use the `make` targets or the command in `DEMO_SCRIPT.md`.
6. **Only macOS + Docker Desktop was tested.** Linux / Docker Engine is unverified.

## Checklist (prompt 20)

**Re-verified 2026-10-27, second session.** Docker 29.7.2 was running, and the images were rebuilt from HEAD. Results: `make check` passed with 764 tests; `pytest -m docker` passed all 37; a fresh-copy end-to-end run (vulnerable demo, export, fix, retest, hardened demo, server restart on the existing DB) passed; 0 leftover containers. No API keys were set, and the jury stayed off.

| Requirement | Status | Evidence (executed) |
|---|---|---|
| End-to-end creation and execution of a test run | ✅ | Fresh-copy rehearsal: `make run` → `make seed` → `make demo` → 10 executed, 8 CONFIRMED, 2 NOT REPRODUCED; `tests/integration/test_api_docker.py` |
| Authorized sandbox target and safe isolation | ✅ | Target needs `confirm_authorized` and an image allowlist. The isolation probe passed in real Docker: uid 10001, read-only rootfs, no exec in /tmp, CapEff 0, NoNewPrivs 1, 256 MiB / 64 PIDs, OOM kill, IPv4/IPv6/host/DNS egress denied |
| Prompt-injection, unauthorized-tool-call and sensitive-data scenarios | ✅ | 2 + 2 + 2 scenarios, all CONFIRMED vs vulnerable and NOT REPRODUCED vs hardened in real Docker |
| Evidence collection and deterministic policy/state verification | ✅ | Nonce-validated gateway evidence (38 collector tests); 9 deterministic checks; 0/80 false-positive FAILs on hardened |
| Clear distinction between CONFIRMED, NOT REPRODUCED and INCONCLUSIVE | ✅ | Verdict grid tests; DB CHECKs; report shows `executed` / `not_run` / inconclusive reasons; never "No violations observed" when anything was inconclusive |
| Independent jury analysis and counter-review | ⚠️ implemented, mocked only | 26+ tests; advisory-only invariant tested (a "false positive" jury changes nothing) |
| Reproducible report with timestamps, trace refs and impact | ✅ | JSON + HTML reports with tool-call ids, artifact hashes, state diff, rule refs, reproduction steps; identical verdicts across 3 runs |
| Regression export and successful retest after a fix | ✅ | Rehearsal: export → `set-mode hardened` → retest `CONFIRMED → NOT_REPRODUCED`, expected checks PASS; 8 committed regressions pass on hardened and fail on vulnerable (real Docker) |
| Benign controls check for excessive blocking | ✅ | 2/2 complete in both modes; a refused legitimate task is reported as over-blocking, never as a vulnerability |
| Error, timeout, cancellation and cleanup behaviour | ✅ | Timeout stops at 3.05 s for a 2 s limit with a graceful gateway stop; cancel in 1.8 s; Docker loss → run `failed`, rest INCONCLUSIVE; 0 leftover containers after every suite |
| No real secrets, production data or unauthorized external targets | ✅ | Git-history scan: only fake test fixtures; no tracked `.env`/DB; synthetic seed; external targets rejected; token 0× in live logs |
| Demo setup, reproducibility and stated limitations | ✅ | `docs/DEMO_SCRIPT.md`; fresh-copy rehearsal in 23 s from server start to retest; `docs/LIMITATIONS.md` linked from the README and summarised in every report |

## Tests executed for this audit

```
uv run ruff check . && uv run ruff format --check .   → clean
uv run mypy                                           → no issues (68 source files)
uv run pytest -m "not docker"                         → 764 passed
uv run pytest -m docker                               → 37 passed (real Docker 29.7.2)
make qa  (scripts/qa_metrics.py)                      → M1–M6 met; see docs/QA_REPORT.md
uvx pip-audit (168 locked packages)                   → no known vulnerabilities
fresh-copy rehearsal (make run/seed/demo + export/fix/retest) → passed, 23 s
```

## Must be verified manually before presenting

- Rehearse the 5-minute script once on the presentation laptop, with Docker running.
- If the jury will be shown, run it once with a real key and read its output.

## Not claimed

This audit does **not** claim complete security coverage, security of RefundBot or any other agent, or detection performance outside the tested synthetic scenarios.
