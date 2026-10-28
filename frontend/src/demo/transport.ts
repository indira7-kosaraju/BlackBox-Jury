/**
 * Demo data source: answers from a fixture recorded from a REAL sandboxed run
 * (scripts/export_ui_demo_fixture.py). Nothing here is generated. Starting a
 * run or retest replays a recorded run on a timer ("SIMULATED REPLAY"); every
 * other write is refused. The whole module is lazy-loaded only in demo mode.
 */
import { ApiError, type Transport } from "../api/client";
import type { ExecutionSummary, Run } from "../api/schemas";
import fixture from "./fixture.json";

interface Fixture {
  recorded_at: string;
  responses: Record<string, unknown>;
  files: Record<string, { type: string; text: string }>;
}
const FX = fixture as unknown as Fixture;

export const REPLAY_PREFIX = "replay_";
const QUEUE_MS = 1200;
const MIN_STEP_MS = 900;
const MAX_STEP_MS = 3000;

interface Replay {
  id: string;
  source: Run;
  executions: ExecutionSummary[];
  startedAt: number;
}

function notFound(): ApiError {
  return new ApiError(404, "not_found", "Not found in the recorded demo data.");
}
function readOnly(): ApiError {
  return new ApiError(403, "demo_read_only", "Disabled in demo mode — connect to a live server to make changes.");
}
function clone<T>(v: T): T {
  return structuredClone(v);
}

export class DemoTransport implements Transport {
  readonly mode = "demo" as const;
  readonly recordedAt = FX.recorded_at;
  private readonly replays = new Map<string, Replay>();

  private recorded(key: string): unknown {
    if (!(key in FX.responses)) throw notFound();
    return clone(FX.responses[key]);
  }

  private recordedRuns(): Run[] {
    return (this.recorded("/runs") as { items: Run[] }).items;
  }

  private executionsOf(runId: string): ExecutionSummary[] {
    return (this.recorded(`/runs/${runId}/executions`) as { items: ExecutionSummary[] }).items;
  }

  /** Reveal recorded executions one at a time, paced by their recorded durations. */
  private replayState(r: Replay): { run: Run; executions: ExecutionSummary[] } {
    const elapsed = Date.now() - r.startedAt;
    const iso = (ms: number) => new Date(r.startedAt + ms).toISOString();
    const steps = r.executions.map((e) => Math.min(MAX_STEP_MS, Math.max(MIN_STEP_MS, e.duration_ms ?? MIN_STEP_MS)));
    let t = QUEUE_MS;
    let revealed = 0;
    for (const s of steps) {
      if (elapsed < t + s) break;
      t += s;
      revealed += 1;
    }
    const queued = elapsed < QUEUE_MS;
    const done = revealed === r.executions.length;
    const executions = r.executions.map((e, i): ExecutionSummary => {
      if (i < revealed) return e;
      const isRunning = !queued && i === revealed;
      return { ...e, status: isRunning ? "running" : "pending", verdict: null, inconclusive_reason: null, severity: null, task_completed: null, duration_ms: null };
    });
    const shown = executions.filter((e) => e.verdict !== null);
    const count = (v: string) => shown.filter((e) => e.verdict === v).length;
    const run: Run = {
      ...r.source,
      id: r.id,
      status: queued ? "queued" : done ? r.source.status : "running",
      created_at: iso(0),
      started_at: queued ? null : iso(QUEUE_MS),
      finished_at: done ? iso(t) : null,
      cancel_requested: false,
      totals: {
        selected: r.executions.length,
        executed: shown.length,
        CONFIRMED: count("CONFIRMED"),
        NOT_REPRODUCED: count("NOT_REPRODUCED"),
        INCONCLUSIVE: count("INCONCLUSIVE"),
        not_run: 0,
        pending: r.executions.length - shown.length,
      },
      jury: done ? r.source.jury : "not_run",
    };
    return { run, executions };
  }

  private startReplay(source: Run | undefined): Run {
    if (!source) throw notFound();
    const id = `${REPLAY_PREFIX}${source.id}_${Date.now().toString(36)}`;
    const r: Replay = { id, source, executions: this.executionsOf(source.id), startedAt: Date.now() };
    this.replays.set(id, r);
    return this.replayState(r).run;
  }

  private activeReplay(): boolean {
    return [...this.replays.values()].some((r) => this.replayState(r).run.finished_at === null);
  }

  request(method: string, path: string, body?: unknown): Promise<unknown> {
    try {
      return Promise.resolve(this.handle(method, path, body));
    } catch (err) {
      return Promise.reject(err);
    }
  }

  private handle(method: string, fullPath: string, body?: unknown): unknown {
    const [path = "", query = ""] = fullPath.split("?");
    const params = new URLSearchParams(query);

    if (method === "POST" && (path === "/runs" || path === "/retests")) {
      if (this.activeReplay()) throw new ApiError(409, "run_already_active", "A run is already active.");
      const runs = this.recordedRuns();
      if (path === "/runs") return this.startReplay(runs.find((r) => r.kind === "standard"));
      const testId = (body as { regression_test_id?: string } | undefined)?.regression_test_id;
      const tests = (this.recorded("/regression-tests") as { items: { id: string; retests: { run_id: string }[] }[] }).items;
      const retestRun = tests.find((t) => t.id === testId)?.retests[0]?.run_id;
      return this.startReplay(runs.find((r) => r.id === retestRun));
    }
    if (method !== "GET") throw readOnly();

    const replayMatch = /^\/runs\/(replay_[^/]+)(\/executions)?$/.exec(path);
    if (replayMatch) {
      const r = this.replays.get(replayMatch[1] ?? "");
      if (!r) throw notFound();
      const state = this.replayState(r);
      return replayMatch[2] ? { items: state.executions, next_cursor: null } : state.run;
    }
    if (path === "/runs") {
      const limit = Number(params.get("limit") ?? 25);
      const replays = [...this.replays.values()].reverse().map((r) => this.replayState(r).run);
      return { items: [...replays, ...this.recordedRuns()].slice(0, limit), next_cursor: null };
    }
    if (path === "/audit-events") {
      const limit = Number(params.get("limit") ?? 50);
      const all = this.recorded("/audit-events") as { items: unknown[] };
      return { items: all.items.slice(0, limit) };
    }
    return this.recorded(path);
  }

  download(fullPath: string): Promise<Blob> {
    const [path = "", query = ""] = fullPath.split("?");
    const runId = /^\/runs\/([^/]+)\/report$/.exec(path)?.[1];
    const key = runId
      ? `/runs/${runId.startsWith(REPLAY_PREFIX) ? (this.replays.get(runId)?.source.id ?? "") : runId}/report?${query}`
      : path;
    const file = FX.files[key];
    if (!file) return Promise.reject(notFound());
    return Promise.resolve(new Blob([file.text], { type: file.type }));
  }
}
