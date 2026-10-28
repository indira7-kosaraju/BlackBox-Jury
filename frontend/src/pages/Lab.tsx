import { Play, Square } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ApiError } from "../api/client";
import type { ExecutionSummary, Scenario } from "../api/schemas";
import { useApi } from "../app/session";
import {
  ConfirmDialog,
  EmptyState,
  ErrorState,
  Provenance,
  ReplayBadge,
  SeverityChip,
  Skeleton,
  StageTrack,
  useToast,
  VerdictBadge,
} from "../components/ui";
import { EvidenceCore } from "../core3d/EvidenceCore";
import { CATEGORY_GROUP, CATEGORY_LABEL, fmtDuration, fmtMs, HARNESS_REASONS, shortId } from "../lib/format";
import { labStep } from "../lib/pipeline";
import { useQuery } from "../lib/useQuery";
import { useRunWatch } from "../lib/useRunWatch";

const GROUPS = ["Security", "Reliability", "Control"] as const;

function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [active]);
  return now;
}

function groupScenarios(items: Scenario[]) {
  const out = new Map<string, Map<string, Scenario[]>>();
  for (const s of items) {
    const g = CATEGORY_GROUP[s.category] ?? "Security";
    const byCat = out.get(g) ?? new Map<string, Scenario[]>();
    byCat.set(s.category, [...(byCat.get(s.category) ?? []), s]);
    out.set(g, byCat);
  }
  return out;
}

export function Lab() {
  const api = useApi();
  const toast = useToast();
  const [params, setParams] = useSearchParams();
  const targets = useQuery(() => api.targets(), [api]);
  const contracts = useQuery(() => api.contracts(), [api]);
  const scenarios = useQuery(() => api.scenarios(), [api]);
  const runs = useQuery(() => api.runs(5), [api]);

  const authorized = useMemo(() => targets.data?.items.filter((t) => t.authorization_status === "authorized") ?? [], [targets.data]);
  const [targetId, setTargetId] = useState("");
  const [contractId, setContractId] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [launching, setLaunching] = useState(false);
  const [launchError, setLaunchError] = useState<ApiError | null>(null);
  const [confirmCancel, setConfirmCancel] = useState(false);

  useEffect(() => {
    if (!targetId && authorized[0]) setTargetId(authorized[0].id);
  }, [authorized, targetId]);
  useEffect(() => {
    const first = contracts.data?.items[0];
    if (!contractId && first) setContractId(first.id);
  }, [contracts.data, contractId]);

  // Which run to show: ?run=… or the most recent run.
  const runId = params.get("run") ?? runs.data?.items[0]?.id ?? null;
  const { run, executions, core, active } = useRunWatch(runId);
  const now = useNow(active);
  void now;

  const grouped = useMemo(() => groupScenarios(scenarios.data?.items ?? []), [scenarios.data]);
  const target = authorized.find((t) => t.id === targetId);

  const toggle = (id: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const launch = async (suite: boolean) => {
    setLaunching(true);
    setLaunchError(null);
    try {
      const r = await api.startRun({
        target_id: targetId,
        contract_id: contractId,
        ...(suite ? { suite: "demo" } : { scenario_ids: [...selected] }),
      });
      setParams({ run: r.id });
      toast(`Run ${shortId(r.id)} queued`);
    } catch (err) {
      setLaunchError(err instanceof ApiError ? err : new ApiError(0, "client_error", String(err)));
    } finally {
      setLaunching(false);
    }
  };

  const cancel = async () => {
    setConfirmCancel(false);
    if (!runId) return;
    try {
      await api.cancelRun(runId);
      toast("Cancellation requested");
      run.reload();
    } catch (err) {
      toast(err instanceof ApiError ? err.message : "Cancel failed", "error");
    }
  };

  const done: ExecutionSummary[] = (executions.data?.items ?? []).filter((e) => e.verdict !== null);
  const harness = done.filter((e) => e.inconclusive_reason && HARNESS_REASONS.has(e.inconclusive_reason));
  const r = run.data;

  return (
    <>
      <div className="page-head">
        <div className="titles">
          <span className="eyebrow">Adversarial testing</span>
          <h1>Test Lab</h1>
          <span className="muted">Runs execute only in the Docker sandbox, against registered and authorized local targets.</span>
        </div>
      </div>

      <div className="grid cols-lab">
        <section className="panel stack" aria-labelledby="setup-h">
          <h2 id="setup-h">Configure run</h2>
          {targets.error ? <ErrorState error={targets.error} onRetry={targets.reload} /> : null}
          <label className="field">
            <span className="label">Target</span>
            <select className="input" value={targetId} onChange={(e) => setTargetId(e.target.value)} disabled={!authorized.length}>
              {authorized.length === 0 ? <option>No authorized targets</option> : null}
              {authorized.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name} · {t.config.mode} (v{t.config_version})
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span className="label">Task contract</span>
            <select className="input" value={contractId} onChange={(e) => setContractId(e.target.value)}>
              {contracts.data?.items.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name} v{c.version}
                </option>
              ))}
            </select>
          </label>
          {target?.config.mode === "hardened" ? (
            <span className="notice" style={{ fontSize: 13 }}>Target is in <strong>hardened</strong> mode.</span>
          ) : null}

          <div className="stack" style={{ gap: 8 }}>
            <div className="row">
              <span className="label" style={{ flex: 1 }}>Scenarios ({selected.size} selected)</span>
              <button className="btn btn--ghost btn--sm" onClick={() => setSelected(new Set(scenarios.data?.items.map((s) => s.id)))}>All</button>
              <button className="btn btn--ghost btn--sm" onClick={() => setSelected(new Set())}>None</button>
            </div>
            {scenarios.error ? <ErrorState error={scenarios.error} onRetry={scenarios.reload} /> : null}
            {!scenarios.data ? (
              <Skeleton lines={6} />
            ) : (
              <div className="scenario-list">
                {GROUPS.filter((g) => grouped.has(g)).map((g) => (
                  <div key={g} className="scenario-group">
                    <h3 className="label">{g}</h3>
                    {[...(grouped.get(g)?.entries() ?? [])].map(([cat, items]) => (
                      <div key={cat}>
                        <div className="faint" style={{ fontSize: 12, margin: "4px 0" }}>{CATEGORY_LABEL[cat] ?? cat}</div>
                        {items.map((s) => (
                          <label key={s.id} className="check scenario-item">
                            <input type="checkbox" checked={selected.has(s.id)} onChange={() => toggle(s.id)} />
                            <span className="stack" style={{ gap: 0 }}>
                              <span>{s.title ?? s.key}</span>
                              <span className="faint mono" style={{ fontSize: 11 }}>{s.key} · v{s.version}</span>
                            </span>
                          </label>
                        ))}
                      </div>
                    ))}
                  </div>
                ))}
              </div>
            )}
          </div>

          {launchError ? <ErrorState error={launchError} /> : null}
          <div className="row">
            <button
              className="btn btn--primary"
              disabled={launching || active || !targetId || !contractId || selected.size === 0 || selected.size > 25}
              onClick={() => void launch(false)}
            >
              <Play size={15} aria-hidden /> Run {selected.size || ""} selected
            </button>
            <button className="btn" disabled={launching || active || !targetId || !contractId} onClick={() => void launch(true)}>
              Run demo suite
            </button>
          </div>
          {active ? <span className="faint" style={{ fontSize: 12 }}>One run at a time: wait for the active run or cancel it.</span> : null}
        </section>

        <section className="stack" aria-labelledby="live-h">
          <div className="panel stack">
            <div className="panel-head" style={{ marginBottom: 0 }}>
              <h2 id="live-h">{active ? "Live run" : "Last run"}</h2>
              {r ? <ReplayBadge runId={r.id} /> : null}
              {r ? <Link to={`/runs/${r.id}`} className="mono">{shortId(r.id)}</Link> : null}
              {active && api.mode === "live" ? (
                <button className="btn btn--danger btn--sm" onClick={() => setConfirmCancel(true)} disabled={r?.cancel_requested}>
                  <Square size={13} aria-hidden /> {r?.cancel_requested ? "Cancelling…" : "Cancel"}
                </button>
              ) : null}
            </div>
            {run.error ? <ErrorState error={run.error} onRetry={run.reload} /> : null}
            {!runId ? (
              <EmptyState title="No runs yet">Choose scenarios and start a run. Progress appears here as the backend reports it.</EmptyState>
            ) : (
              <>
                <StageTrack step={labStep(core)} outcome={core.outcome} />
                <EvidenceCore state={core} compact />
                {r ? (
                  <dl className="kv">
                    <dt>Status</dt>
                    <dd>{r.status}{r.status_reason ? ` (${r.status_reason})` : ""}</dd>
                    <dt>Target</dt>
                    <dd>{r.target_config_snapshot.image} · {r.target_config_snapshot.mode}</dd>
                    <dt>Elapsed</dt>
                    <dd>{fmtDuration(r.started_at ?? r.created_at, r.finished_at)}</dd>
                    <dt>Verified</dt>
                    <dd>{r.totals.executed} of {r.totals.selected} executions{r.totals.not_run ? ` · ${r.totals.not_run} not run` : ""}</dd>
                  </dl>
                ) : null}
              </>
            )}
          </div>

          {r && !active && done.length > 0 ? (
            <div className="panel stack" style={{ gap: 8 }}>
              <h3>Summary</h3>
              <div className="row">
                <VerdictBadge verdict="CONFIRMED" /> <strong>{r.totals.CONFIRMED}</strong>
                <VerdictBadge verdict="NOT_REPRODUCED" /> <strong>{r.totals.NOT_REPRODUCED}</strong>
                <VerdictBadge verdict="INCONCLUSIVE" /> <strong>{r.totals.INCONCLUSIVE}</strong>
              </div>
              {harness.length > 0 ? (
                <span className="notice" style={{ fontSize: 13 }}>
                  {harness.length} inconclusive result{harness.length > 1 ? "s were" : " was"} caused by the test harness
                  ({[...new Set(harness.map((h) => h.inconclusive_reason))].join(", ")}), not by the target. They are not passes.
                </span>
              ) : null}
            </div>
          ) : null}

          <div className="panel">
            <div className="panel-head">
              <h3>Evidence stream</h3>
              <span className="faint" style={{ fontSize: 12 }}>verified executions, newest last</span>
            </div>
            {!runId ? null : done.length === 0 ? (
              <EmptyState title={active ? "Waiting for the first verified execution…" : "No verified executions"} />
            ) : (
              <ul className="stream">
                {done.map((e) => (
                  <li key={e.id}>
                    <Provenance kind={api.mode === "demo" ? "demo" : "verified"} />
                    <Link to={`/evidence/${e.id}`} style={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis" }}>
                      {e.scenario_key}
                      <span className="faint" style={{ marginLeft: 8, fontSize: 12 }}>{fmtMs(e.duration_ms)}</span>
                    </Link>
                    <span className="row" style={{ gap: 6, flexWrap: "nowrap" }}>
                      <SeverityChip severity={e.severity} />
                      <VerdictBadge verdict={e.verdict} reason={e.inconclusive_reason} />
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </section>
      </div>

      <ConfirmDialog
        open={confirmCancel}
        title="Cancel this run?"
        body="Executions that have not finished will be recorded as INCONCLUSIVE (cancelled). They are never counted as passed."
        confirmLabel="Cancel run"
        danger
        onConfirm={() => void cancel()}
        onCancel={() => setConfirmCancel(false)}
      />
    </>
  );
}
