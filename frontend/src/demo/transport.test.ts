import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError } from "../api/client";
import { DemoTransport } from "./transport";

afterEach(() => vi.useRealTimers());

describe("DemoTransport", () => {
  it("serves recorded responses that pass the same schemas as live data", async () => {
    const a = api(new DemoTransport());
    const runs = await a.runs(100);
    expect(runs.items.length).toBeGreaterThan(0);
    await a.settings();
    await a.targets();
    await a.scenarios();
    const findings = await a.findings();
    await a.regressionTests();
    await a.auditEvents(10);
    for (const r of runs.items) {
      const ex = await a.executions(r.id);
      for (const e of ex.items) {
        const d = await a.execution(e.id);
        for (const art of d.artifacts) await a.artifact(art.id);
      }
    }
    for (const f of findings.items) await a.finding(f.id);
  });

  it("refuses writes other than replays", async () => {
    const a = api(new DemoTransport());
    const t = (await a.targets()).items[0];
    const err = await a.setTargetMode(t?.id ?? "x", "hardened").catch((e: unknown) => e);
    expect((err as ApiError).code).toBe("demo_read_only");
  });

  it("replays a recorded run step by step and never shows unrevealed verdicts", async () => {
    vi.useFakeTimers();
    const a = api(new DemoTransport());
    const started = await a.startRun({ target_id: "t", contract_id: "c", suite: "demo" });
    expect(started.id.startsWith("replay_")).toBe(true);
    expect(started.status).toBe("queued");
    expect(started.totals.executed).toBe(0);

    vi.advanceTimersByTime(1500);
    const mid = await a.run(started.id);
    expect(mid.status).toBe("running");
    const ex = await a.executions(started.id);
    expect(ex.items.filter((e) => e.verdict !== null).length).toBe(mid.totals.executed);

    // A second run is refused while the replay is active (same rule as the server).
    const busy = await a.startRun({ target_id: "t", contract_id: "c", suite: "demo" }).catch((e: unknown) => e);
    expect((busy as ApiError).code).toBe("run_already_active");

    vi.advanceTimersByTime(120_000);
    const end = await a.run(started.id);
    expect(end.status).toBe("completed");
    const recorded = (await a.runs(100)).items.find((r) => r.kind === "standard" && !r.id.startsWith("replay_"));
    expect(end.totals.CONFIRMED).toBe(recorded?.totals.CONFIRMED);
  });

  it("serves recorded report files, including for replays", async () => {
    const t = new DemoTransport();
    const a = api(t);
    const run = (await a.runs(100)).items[0];
    const blob = await a.reportFile(run?.id ?? "", "html");
    expect(blob.type).toBe("text/html");
  });
});
