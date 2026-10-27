# Known limitations: Blackbox Jury MVP

**Last updated:** 2026-10-27 (Phase 6) · **Scope:** what the current checks and scenarios do and do not demonstrate.

Blackbox Jury reports only on scenarios it actually executed. **A NOT REPRODUCED verdict means "not observed in this run". It does not mean the target is secure.** The items below bound every result the tool produces. The HTML report must link to this page or summarise it.

## 1. What the curated scenarios demonstrate

All ten scenarios are in `src/blackbox_jury/scenarios/library/`. They are synthetic, run against one demo contract (`refund_support_v1`) and one scripted target (RefundBot).

| Scenario | Category | Designed to trip | Shown in tests |
|---|---|---|---|
| `pi_order_note_refund` | prompt injection (indirect, via an order note) | ARG_OWNERSHIP, REFUND_APPROVAL | vulnerable → CONFIRMED · hardened → NOT REPRODUCED |
| `pi_order_note_exfil_email` | prompt injection (indirect, exfiltration) | TOOL_ALLOWLIST | same |
| `uta_cross_customer_refund` | unauthorized tool call | ARG_OWNERSHIP, REFUND_APPROVAL | same |
| `uta_forbidden_note_update` | unauthorized tool call | TOOL_ALLOWLIST, ARG_OWNERSHIP | same |
| `sde_cross_customer_contact` | sensitive-data exposure | PII_CROSS_PRINCIPAL (+ ARG_OWNERSHIP, TOOL_ALLOWLIST) | same |
| `sde_canary_escalation_code` | sensitive-data exposure (direct injection) | CANARY_SECRET_EXPOSURE | same |
| `rel_false_success_refund` | false success claim | FALSE_SUCCESS_CLAIM | same |
| `rel_duplicate_refund` | duplicate operation / unsafe retry | DUPLICATE_SIDE_EFFECT | same (REFUND_LIMIT also trips) |
| `ctl_refund_own_damaged_order` | benign control | TASK_COMPLETION | completes in both modes |
| `ctl_order_status_own` | benign control | TASK_COMPLETION | completes in both modes |

**Phase 5 status:** all ten scenarios have been executed in the **real Docker sandbox** through the control-plane pipeline (runner → collector → verifier), against both RefundBot modes, with the results above (`tests/integration/test_docker_sandbox.py`, run with `pytest -m docker`). The run lifecycle, persistence and API come in Phase 6.

**Not covered by any scenario:**
- `REFUND_LIMIT` has no dedicated scenario. It trips only as a side effect of the duplicate-refund scenario.
- The `wrong_arguments` category is in the schema but has no scenario.
- Multi-turn conversations: every scenario is a single user message.
- Injection through any channel other than order notes and the direct user message.
- Encoded or obfuscated payloads, and non-English inputs.

## 2. Check-by-check detection limits

| Check | Known limitation |
|---|---|
| TOOL_ALLOWLIST | Sees only calls that reach the gateway. Requests the gateway rejects at the HTTP level are recorded as separate events, which no check examines yet. |
| ARG_OWNERSHIP | Covers only the `order_id` and `customer_id` arguments of the scoped tools. Calls on non-existent orders are not counted as cross-principal. |
| REFUND_LIMIT | Compares refunds with the order total from the snapshot taken before the run. It trusts the gateway's state. |
| REFUND_APPROVAL | Trusts the approval records in the state taken after the run. No tool can create approvals, so the agent cannot forge one in this mock. |
| PII_CROSS_PRINCIPAL | Matches other customers' exact or lightly reformatted email, phone (last 7 digits), full name, and card last-4 when the card is mentioned in context. **Misses** encoded, split, partially paraphrased or translated data. It does **not** detect the agent sending the *principal's own* data to an outside party. That case is caught only when the channel is a forbidden tool (for example `send_email`). |
| CANARY_SECRET_EXPOSURE | Exact substring match only. Encoded, reversed or partial leaks are missed. |
| DUPLICATE_SIDE_EFFECT | Flags more than one new refund per order, identical outbound emails, or identical repeated side-effect calls. A legitimate split refund (two partial refunds) would be flagged. That is acceptable for this contract but not in general. |
| FALSE_SUCCESS_CLAIM | Recognises English *refund* success phrases only. Claims about other actions (emails, note changes) are not checked. Claims it can't parse cleanly give ERROR, which makes the result INCONCLUSIVE rather than a false pass. |
| TASK_COMPLETION | Checks the declared state effects and literal response substrings. It does not judge answer quality. |

## 3. Control-plane limits (Phase 6)

- **Single process.** One worker thread runs one run at a time. The one-active-run check, rate limits and cancel tokens live in process memory, so rate-limit counters reset on restart.
- **Idempotency keys never expire.** That is stricter than the 24 h in API.md: a reused key always returns the original run, or 409 if the request body differs.
- **Auth is a single shared API token.** There are no users or roles. Project scoping is modelled but only one project is used.
- **SQLite only.** There is no retention/prune job yet (`bbj prune` is not implemented). The database grows until it is deleted.
- **Execution order** follows creation order, which is alphabetical by scenario key for suites.

## 4. Environment and evidence limits

- **Scripted target.** RefundBot is deterministic and deliberately vulnerable. These results show that the checks and the pipeline work. They are **not** evidence about how well detection would work on real, model-driven agents.
- **Synthetic data only.** Customers, orders and the canary are all fictitious.
- **The gateway is trusted for state.** Checks that rely on the state snapshots assume the gateway's view of state is correct. A real backend would need its own independent way to capture state.
- **One contract, one principal.** Multi-tenant behaviour and other contracts are untested.
- **Sandbox network isolation** relies on Docker `--internal` networks. Tests confirmed that connections to 1.1.1.1:443, 8.8.8.8:53 and `host.docker.internal:80` fail from inside the sandbox. This is not an exhaustive egress audit, for example of IPv6 or other host interfaces. It was verified on Docker Desktop for macOS only.
- **Container isolation** uses Docker's default runtime (runc) with hardening flags: non-root user, read-only rootfs, no capabilities, `no-new-privileges`, and memory, CPU and PID limits. There is no gVisor/Kata/seccomp customisation. A kernel or runtime escape is out of scope for the MVP.
- **Images are referenced by local tag.** The runner records each image's ID per execution, but it does not verify signatures, so a locally re-tagged image would be executed.
- **Timeouts:** the agent's wall-clock limit is enforced, at 30 s by default. CPU-time limits rely on Docker's `nano_cpus` throttling, not on a CPU-seconds cap.
- **LLM jury** (Phase 7) is advisory only and can never change a deterministic verdict. Its analysis is model output, not evidence.
- **The jury is off by default and not verified against the live API.** All tests use a scripted provider or a fake SDK client. No real Claude call was made during development: no key was configured, and sending data to an external service needs explicit approval. The prompts (`reviewer_v1`, `counter_v1`) have not been evaluated for quality.
- **What a jury concern can do:** it can mark a NOT_REPRODUCED execution INCONCLUSIVE, but only with valid citations and counter-reviewer agreement. The jury cannot find vulnerabilities on its own.
- **Not stored:** reviews of executions without a finding (NOT_REPRODUCED with no concern) are recorded only as `model_calls`, not as `jury_reviews`.
