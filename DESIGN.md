# DESIGN — Blackbox Jury MVP

**Status:** Draft v0.1 · **Sources of truth:** PRD.md · **Last updated:** 2026-10-26

Per PRD decision D3, the MVP interface is a **static HTML report** plus **CLI output**. There is no interactive dashboard. This document specifies those two surfaces carefully and sketches the dashboard as a stretch goal. Every choice below serves one goal: **an engineer can read the evidence and trust where each statement came from.**

---

## 1. Visual direction

- **Tone:** a forensic case file, not a "threat intelligence" dashboard. It should be calm, dense and precise.
- **Not allowed:**
  - neon-on-black "hacker" styling, gradients, glow or animated radar;
  - decorative "threat scores", gauges or donuts with no defined calculation;
  - fake live counters;
  - any "Secure ✓" badge.
- **Allowed visualizations** (each with a defined data source):
  - a verdict count bar (counts of executed scenarios per verdict);
  - the evidence timeline (ordered events);
  - the state diff table;
  - a retest comparison table.

## 2. Provenance: the core design rule

Every statement in the report carries one of three **provenance labels**, always as text and never as colour alone:

| Label | Source | Style |
|---|---|---|
| `VERIFIED` | Deterministic check over gateway evidence | Solid left border in `--verified`, plus the check id |
| `MODEL ANALYSIS` | Jury reviewer or counter-reviewer output | Dashed left border in `--model`, plus model id and prompt version, plus the note "Advisory — cannot change verdict" |
| `AGENT CLAIM` | The target agent's own final response (untrusted) | Quoted block in monospace, `--claim` border, plus the note "Unverified claim from target" |

Unresolved items (an INCONCLUSIVE reason, a jury-only flag) use `--inconclusive` styling and always state **what evidence is missing**.

## 3. Colour tokens

Defined on `:root`, with a dark-mode override under `prefers-color-scheme: dark`. All text and background pairs meet WCAG AA (4.5:1). Status colours always come with a text label and an icon glyph.

| Token | Light | Dark | Use |
|---|---|---|---|
| `--bg` | `#FAFAF8` | `#15171A` | Page background |
| `--surface` | `#FFFFFF` | `#1D2024` | Cards and tables |
| `--text` | `#1B1D21` | `#E6E7E9` | Body text |
| `--muted` | `#5B616B` | `#9BA1AA` | Metadata |
| `--border` | `#DADCE0` | `#33373D` | Rules |
| `--confirmed` | `#B42318` | `#F97066` | CONFIRMED |
| `--not-reproduced` | `#2E6B4F` | `#6CC79A` | NOT REPRODUCED |
| `--inconclusive` | `#9A6700` | `#E3B341` | INCONCLUSIVE |
| `--verified` | `#1F4E8C` | `#7AA7E6` | VERIFIED provenance |
| `--model` | `#6B4FA0` | `#B39DDB` | MODEL ANALYSIS provenance |
| `--claim` | `#5B616B` | `#9BA1AA` | AGENT CLAIM provenance |

The NOT REPRODUCED colour is deliberately muted green, not a celebratory one, because the verdict does not mean "secure".

## 4. Typography, spacing and layout

- **Fonts:** a system UI stack for prose and a system monospace stack (`ui-monospace, SFMono-Regular, Menlo, monospace`) for ids, tool calls, arguments, hashes and claims. No web-font downloads, because the report must work offline.
- **Scale:** 14 px base, 1.5 line height. Headings are 20, 16 and 14 px semibold. Monospace is 13 px.
- **Spacing:** a 4 px grid with steps 4, 8, 12, 16, 24 and 32.
- **Layout:** a single column, max width 1080 px, centred. The side gutter is 16 px on phones and 24 px from 768 px up. Tables scroll horizontally *inside* their container on narrow screens, so the page itself never scrolls sideways.
- **Report header:** sticky in-page navigation (Summary · Findings · Controls · Retest · Limitations).

## 5. Report structure

1. **Run summary**
   - Target name, image and mode; contract name and version; run id; start and end timestamps (ISO-8601 UTC); run status.
   - Counts: `Scenarios selected N · Executed N · CONFIRMED n · NOT REPRODUCED n · INCONCLUSIVE n`, plus the verdict count bar. If generated and executed counts differ, say so explicitly.
   - A fixed disclaimer: *"Results reflect only the scenarios executed in this run. They do not establish that the target is secure."*
2. **Findings table**, sorted by severity and then verdict. Columns: severity, verdict badge, title, category, failed checks, execution id. Each row links to its detail section.
3. **Finding detail**, one per finding:
   - **Verdict block:** verdict badge, rule-based severity with the rule that produced it, and failed check ids.
   - **What the agent said vs. what happened:** two columns. The left column is the `AGENT CLAIM` quote. The right column is the `VERIFIED` outcome, such as "issue_refund(B-200, 950.00) succeeded at 12:03:04.118Z; refunds table +1 row".
   - **Evidence timeline:** an ordered list of events: input delivered, each tool call (seq, timestamp, tool, redacted args, response status), agent exit, gateway state_after. Event ids are shown so checks can cite them.
   - **Tool-call trace:** a collapsible `<details>` per call with the full redacted request and response JSON and its sha256.
   - **State diff:** a table of table, key, before and after, with added rows marked `+` and removed rows marked `−` (not colour alone).
   - **Checks:** each check id, its outcome (PASS, FAIL or ERROR), the rule reference, a message and the cited event ids.
   - **Reproduction steps:** a numbered list, the scenario id and version, the seed hash and the CLI command to rerun.
   - **Jury:** the reviewer card and counter-reviewer card (`MODEL ANALYSIS`), each with model, prompt version, status and suggested severity, shown separately from the rule-based severity. If the jury didn't run, show "Jury not run (no provider configured)".
4. **Benign controls:** a table of scenario, task completed (yes or no) and verdict. A failed control is labelled **"Over-blocking / usability"**, not a vulnerability.
5. **Retest comparison** (retest runs only): original finding, original verdict → retest verdict, and both run ids.
6. **Limitations:** a fixed text block plus run-specific notes, such as "Jury not run" or "2 executions inconclusive: timeout".

## 6. Verdict and severity presentation

| Verdict | Badge |
|---|---|
| CONFIRMED | `■ CONFIRMED` |
| NOT REPRODUCED | `○ NOT REPRODUCED` |
| INCONCLUSIVE | `◐ INCONCLUSIVE — <reason>` |

Severity is shown as a text chip (`CRITICAL`, `HIGH`, `MEDIUM` or `LOW`). Next to it is an "ⓘ rule" link to the rubric row from PRD FR24. Severity is never a number.

## 7. States

| State | Report or CLI behaviour |
|---|---|
| In progress | CLI: one line per scenario showing state and elapsed time. A report requested mid-run shows "Run in progress — partial" with a banner, and pending executions are listed as pending. |
| Empty, no findings | "No violations observed in N executed scenarios." Never "Secure". |
| Empty, nothing executed | "No scenarios were executed." Totals show 0, and no verdicts are shown. |
| Error | A banner with the error code and a plain-language next step. No stack traces. |
| Timeout or cancelled | Run banner plus per-execution INCONCLUSIVE badges with the reason. |
| Sandbox unavailable | CLI: "Docker is not reachable — no tests were executed. Start Docker Desktop and retry." |

## 8. CLI design

- Output is plain text, and colour is used only when stdout is a TTY. `--json` gives machine-readable output.
- `bbj run --target refundbot --contract refund_support_v1 --suite demo` streams progress and ends with the counts and the report path.
- **Confirmation prompts**, each skippable with `--yes`, recorded in the audit log:
  - when registering or authorizing a target: "You confirm you are authorized to test <target>. [y/N]";
  - when cancelling a run: "Cancel run <id>? Remaining scenarios will be INCONCLUSIVE. [y/N]";
  - when changing target mode: "This changes the target configuration for future runs. [y/N]".
- External targets are not supported in the MVP. The CLI explains that and exits with a non-zero code.

## 9. Accessibility and keyboard

- Semantic HTML: `<header>`, `<nav>`, `<main>`, `<section>` with headings in order, and `<table>` with `<th scope>`.
- A skip link to the findings. Every interactive element is native (`<a>`, `<details>`), so it is keyboard-operable with a visible focus ring (2 px `--verified` outline).
- Status is never conveyed by colour alone: it always has a glyph and a text label.
- Respect `prefers-reduced-motion`. The report has no animations anyway.
- All agent and evidence text is HTML-escaped, and long tokens wrap (`overflow-wrap: anywhere`).

## 10. Stretch: interactive dashboard (superseded by §11)

This is built only after the IMPLEMENTATION_PLAN must-haves are done. It reuses the tokens, components and provenance rules above:
- runs list;
- run detail with live progress (polling `GET /runs/{id}`);
- finding detail (the same layout as the report);
- new run form, with target picker limited to authorized targets and a confirmation dialog.

There are no extra visualizations beyond those in section 1.

## 11. Web app: Evidence Core command center (approved 2026-10-28)

The team approved a richer visual identity **for the interactive web app only**. The static HTML report (§1–§9) keeps its calm, forensic style. §1's "no glow / animation" rule does not apply to the web app, but every honesty rule still does.

**Rules that still apply:**
- Provenance labels everywhere: VERIFIED, AGENT CLAIM, MODEL ANALYSIS.
- No invented statistics, scores, live events or "secure" badges.
- Severity is never shown by colour alone.
- Jury roles are shown exactly as the backend runs them: one reviewer plus one counter-reviewer.

**Layout:**
- Left sidebar: brand mark, navigation, sandbox status.
- Compact top bar: page title, target, a `LOCAL SANDBOX` badge.
- Responsive workspace.
- Pages: Overview, Targets, Test Lab, Test Runs, Evidence Explorer, Jury Findings, Regression Tests, Settings.

**Colour semantics (tokens):**

| Token | Value | Use |
|---|---|---|
| background | `#070A12` | Page background |
| secondary background | `#0B1120` | Secondary surfaces |
| panel | `#101827` | Panels and cards |
| cyan | `#42E8F5` | Active systems |
| blue | `#5488FF` | Navigation and links |
| violet | `#A78BFA` | Model analysis |
| red | `#FF4567` | CONFIRMED |
| amber | `#FFBF69` | INCONCLUSIVE |
| main text | `#EDF4FF` | Body text |
| secondary text | `#8C9BB5` | Secondary text |

Glow is restrained: active elements only, never every border.

**Typography:**
- Space Grotesk for headings.
- Inter for UI text.
- JetBrains Mono for ids, tool calls and timestamps.
- Fonts are self-hosted, so nothing is loaded from external servers.

**The Evidence Core (3D):**
- A translucent core (the target agent) with five orbital rings: scenarios, execution, evidence, verification, jury.
- Ring and colour states are **derived only from API data**:
  - run and execution status drive which stage is active;
  - verdict totals drive the final colour: red if anything was CONFIRMED, amber if anything was INCONCLUSIVE, cyan otherwise;
  - the jury ring is dim when the jury is not run.
- It is lazy-loaded, pauses when hidden, and has a static SVG fallback when WebGL is unavailable or the user prefers reduced motion.
- It is never the only place information appears.

**Demo mode:**
- A recorded dataset captured from a real sandbox run (`frontend/src/demo/fixture.json`).
- Every screen shows a persistent `DEMO DATA — recorded run` banner, and a replayed run is labelled `SIMULATED REPLAY`.
- Demo and live data are never mixed.

**Security:**
- The UI is served same-origin from `/ui`, with a strict CSP: scripts only from the same origin and no external connections.
- The API token is entered once and kept in `sessionStorage`, so it is cleared when the tab closes. It is never placed in URLs or logs.
