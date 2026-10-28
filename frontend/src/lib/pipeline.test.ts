import { describe, expect, it } from "vitest";
import type { ExecutionSummary, Run } from "../api/schemas";
import { deriveCoreState, isActiveRun, labStep, outcomeOf } from "./pipeline";

const totals = { selected: 3, executed: 0, CONFIRMED: 0, NOT_REPRODUCED: 0, INCONCLUSIVE: 0, not_run: 0, pending: 3 };

function run(over: Partial<Run> = {}): Run {
  return {
    id: "run_1", status: "queued", status_reason: null, kind: "standard", target_id: "tgt", contract_id: "ctr",
    target_config_snapshot: { image: "bbj-refundbot:0.1.0", mode: "vulnerable" }, scenario_count: 3,
    cancel_requested: false, created_at: "2026-10-28T00:00:00Z", started_at: null, finished_at: null,
    totals, jury: "not_run", ...over,
  };
}
function exe(over: Partial<ExecutionSummary>): ExecutionSummary {
  return { id: "exe", scenario_key: "k", category: "prompt_injection", status: "pending", verdict: null,
    inconclusive_reason: null, severity: null, task_completed: null, duration_ms: null, ...over };
}

describe("deriveCoreState", () => {
  it("is idle with no run", () => {
    const s = deriveCoreState(null);
    expect(s.live).toBe(false);
    expect(s.outcome).toBe("none");
    expect(Object.values(s.stages).every((v) => v === "idle")).toBe(true);
    expect(labStep(s)).toBe(-1);
  });

  it("queued run activates only scenario selection", () => {
    const s = deriveCoreState(run());
    expect(s.active).toBe("scenarios");
    expect(s.stages.execution).toBe("idle");
    expect(labStep(s)).toBe(0);
  });

  it("running run reports the executing scenario and verified count", () => {
    const s = deriveCoreState(run({ status: "running" }), [
      exe({ id: "a", status: "verified", verdict: "CONFIRMED", severity: "high" }),
      exe({ id: "b", status: "running", scenario_key: "pi_note" }),
      exe({ id: "c" }),
    ]);
    expect(s.active).toBe("execution");
    expect(s.stages.verification).toBe("done");
    expect(s.caption).toContain("pi_note");
    expect(s.caption).toContain("1/3");
  });

  it("does not claim progress before any execution finished", () => {
    const s = deriveCoreState(run({ status: "running" }), [exe({ status: "running" }), exe({})]);
    expect(s.stages.evidence).toBe("idle");
    expect(s.stages.verification).toBe("idle");
  });

  it("completed run without jury marks jury skipped", () => {
    const s = deriveCoreState(run({ status: "completed", totals: { ...totals, executed: 3, NOT_REPRODUCED: 3, pending: 0 } }));
    expect(s.live).toBe(false);
    expect(s.stages.jury).toBe("skipped");
    expect(s.outcome).toBe("clear");
  });

  it("confirmed beats inconclusive; failed runs are never clear", () => {
    expect(outcomeOf({ ...totals, executed: 2, CONFIRMED: 1, INCONCLUSIVE: 1 }, "completed")).toBe("confirmed");
    expect(outcomeOf({ ...totals, executed: 2, INCONCLUSIVE: 2 }, "completed")).toBe("inconclusive");
    expect(outcomeOf({ ...totals, executed: 3, NOT_REPRODUCED: 3 }, "failed")).toBe("failed");
    expect(outcomeOf({ ...totals, executed: 3, NOT_REPRODUCED: 3 }, "timed_out")).toBe("failed");
  });

  it("cancelled run with nothing executed has no outcome", () => {
    const s = deriveCoreState(run({ status: "cancelled", status_reason: "user" }));
    expect(s.outcome).toBe("none");
    expect(s.caption).toContain("cancelled");
  });

  it("isActiveRun covers only non-terminal statuses", () => {
    for (const st of ["queued", "running", "verifying", "reviewing"]) expect(isActiveRun(run({ status: st }))).toBe(true);
    for (const st of ["completed", "failed", "cancelled", "timed_out"]) expect(isActiveRun(run({ status: st }))).toBe(false);
  });
});
