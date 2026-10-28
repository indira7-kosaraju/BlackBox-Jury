/**
 * Derive the Evidence Core / stage-track state from REAL run data only.
 * Nothing here invents progress: every stage maps to a backend status.
 */
import type { ExecutionSummary, Run, Totals } from "../api/schemas";

export const STAGES = ["scenarios", "execution", "evidence", "verification", "jury"] as const;
export type Stage = (typeof STAGES)[number];

export const STAGE_LABELS: Record<Stage, string> = {
  scenarios: "Scenario selection",
  execution: "Adversarial execution",
  evidence: "Evidence collection",
  verification: "Policy verification",
  jury: "Jury review",
};

export const LAB_STEPS = ["PREPARE", "EXECUTE", "OBSERVE", "VERIFY", "REVIEW"] as const;

export type StageState = "idle" | "active" | "done" | "skipped";
export type Outcome = "none" | "confirmed" | "inconclusive" | "clear" | "failed";

export interface CoreState {
  /** True while a run is queued/running/reviewing. */
  live: boolean;
  stages: Record<Stage, StageState>;
  active: Stage | null;
  outcome: Outcome;
  /** Short human label for the current situation. */
  caption: string;
}

const ACTIVE_RUN = new Set(["queued", "running", "verifying", "reviewing"]);

export function outcomeOf(totals: Totals, status: string): Outcome {
  if (status === "failed" || status === "timed_out") return "failed";
  if (totals.CONFIRMED > 0) return "confirmed";
  if (totals.INCONCLUSIVE > 0) return "inconclusive";
  if (totals.executed > 0 && status === "completed") return "clear";
  return "none";
}

function all(state: StageState): Record<Stage, StageState> {
  return { scenarios: state, execution: state, evidence: state, verification: state, jury: state };
}

export function deriveCoreState(run: Run | null | undefined, executions: ExecutionSummary[] = []): CoreState {
  if (!run) {
    return { live: false, stages: all("idle"), active: null, outcome: "none", caption: "Idle — no test run yet" };
  }
  const juryRan = run.jury === "completed" || run.jury === "in_progress";
  const stages = all("idle");
  const live = ACTIVE_RUN.has(run.status);

  if (run.status === "queued") {
    stages.scenarios = "active";
    return { live, stages, active: "scenarios", outcome: "none", caption: "Queued — preparing scenarios" };
  }

  stages.scenarios = "done";
  if (run.status === "running" || run.status === "verifying") {
    const running = executions.find((e) => e.status === "running");
    const anyDone = executions.some((e) => e.verdict !== null);
    stages.execution = running ? "active" : anyDone ? "done" : "active";
    stages.evidence = anyDone ? "done" : "idle";
    stages.verification = anyDone ? "done" : "idle";
    if (running && anyDone) {
      stages.evidence = "done";
      stages.verification = "done";
    }
    const active: Stage = running ? "execution" : anyDone ? "verification" : "execution";
    stages[active] = "active";
    const finished = executions.filter((e) => e.verdict !== null).length;
    return {
      live,
      stages,
      active,
      outcome: outcomeOf(run.totals, run.status),
      caption: running
        ? `Executing ${running.scenario_key} (${finished}/${run.scenario_count} verified)`
        : `Verifying (${finished}/${run.scenario_count})`,
    };
  }

  stages.execution = "done";
  stages.evidence = "done";
  stages.verification = "done";
  if (run.status === "reviewing") {
    stages.jury = "active";
    return { live, stages, active: "jury", outcome: outcomeOf(run.totals, run.status), caption: "Jury reviewing findings" };
  }
  stages.jury = juryRan ? "done" : "skipped";
  const outcome = outcomeOf(run.totals, run.status);
  const t = run.totals;
  const caption =
    run.status === "completed"
      ? `Completed — ${t.CONFIRMED} confirmed · ${t.NOT_REPRODUCED} not reproduced · ${t.INCONCLUSIVE} inconclusive`
      : `Run ${run.status}${run.status_reason ? ` (${run.status_reason})` : ""}`;
  return { live, stages, active: null, outcome, caption };
}

/** Map the five-step Test Lab track onto the same data. */
export function labStep(core: CoreState): number {
  if (!core.active) return core.live ? 0 : -1;
  return { scenarios: 0, execution: 1, evidence: 2, verification: 3, jury: 4 }[core.active];
}

export function isActiveRun(run: Run | null | undefined): boolean {
  return !!run && ACTIVE_RUN.has(run.status);
}
