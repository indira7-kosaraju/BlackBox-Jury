import { Download, RotateCcw } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ApiError } from "../api/client";
import type { RegressionTest, Retest } from "../api/schemas";
import { useApi } from "../app/session";
import { ConfirmDialog, CopyId, EmptyState, ErrorState, SeverityChip, Skeleton, useToast, VerdictBadge } from "../components/ui";
import { saveBlob } from "../lib/download";
import { fmtTime, shortId } from "../lib/format";
import { useQuery } from "../lib/useQuery";

/** Retest status wording. Never "fixed" or "secure" (API.md §7). */
function retestStatus(r: Retest): string {
  if (r.verdict === "CONFIRMED") return "still reproduces";
  if (r.verdict === "NOT_REPRODUCED") return "not reproduced";
  if (r.verdict === "INCONCLUSIVE") return "inconclusive";
  return r.status;
}

export function Regressions() {
  const api = useApi();
  const toast = useToast();
  const navigate = useNavigate();
  const tests = useQuery(() => api.regressionTests(), [api], { pollMs: 5000 });
  const targets = useQuery(() => api.targets(), [api]);
  const authorized = targets.data?.items.filter((t) => t.authorization_status === "authorized") ?? [];
  const [targetId, setTargetId] = useState("");
  const [pending, setPending] = useState<RegressionTest | null>(null);
  const target = authorized.find((t) => t.id === targetId) ?? authorized[0];

  const start = async () => {
    const t = pending;
    setPending(null);
    if (!t || !target) return;
    try {
      const run = await api.startRetest(t.id, target.id);
      toast(`Retest ${shortId(run.id)} queued`);
      navigate(`/lab?run=${run.id}`);
    } catch (err) {
      toast(err instanceof ApiError ? err.message : "Retest failed", "error");
    }
  };
  const download = async (id: string) => {
    try {
      saveBlob(await api.regressionFile(id), `${id}.json`);
    } catch (err) {
      toast(err instanceof ApiError ? err.message : "Download failed", "error");
    }
  };

  return (
    <>
      <div className="page-head">
        <div className="titles">
          <span className="eyebrow">Retest after a fix</span>
          <h1>Regression Tests</h1>
          <span className="muted">Each test replays a confirmed finding's exact scenario and expects its failed checks to pass. A pass means "not reproduced", never "fixed".</span>
        </div>
        {authorized.length ? (
          <label className="field" style={{ minWidth: 240 }}>
            <span className="label">Retest against</span>
            <select className="input" value={target?.id ?? ""} onChange={(e) => setTargetId(e.target.value)}>
              {authorized.map((t) => <option key={t.id} value={t.id}>{t.name} · {t.config.mode} (v{t.config_version})</option>)}
            </select>
          </label>
        ) : null}
      </div>
      {tests.error ? <ErrorState error={tests.error} onRetry={tests.reload} /> : null}
      {!tests.data ? (
        <Skeleton lines={6} />
      ) : tests.data.items.length === 0 ? (
        <EmptyState title="No regression tests yet" action={<Link to="/findings" className="btn btn--sm">Go to findings</Link>}>
          Export one from a CONFIRMED finding.
        </EmptyState>
      ) : (
        tests.data.items.map((t) => (
          <section key={t.id} className="panel stack">
            <div className="row">
              <div className="stack" style={{ gap: 2, flex: 1, minWidth: 240 }}>
                <h2>{t.title ?? t.scenario_key ?? t.id}</h2>
                <span className="row muted" style={{ gap: 8, fontSize: 13 }}>
                  <CopyId value={t.id} label={shortId(t.id)} /> · {t.scenario_key} · exported {fmtTime(t.created_at)}
                </span>
              </div>
              <button className="btn btn--sm" onClick={() => void download(t.id)}><Download size={14} aria-hidden /> JSON</button>
              <button className="btn btn--primary btn--sm" disabled={!target} onClick={() => setPending(t)}>
                <RotateCcw size={14} aria-hidden /> Run regression test
              </button>
            </div>
            <dl className="kv">
              <dt>Original</dt>
              <dd className="row" style={{ gap: 6 }}>
                <VerdictBadge verdict={t.original_verdict} /> <SeverityChip severity={t.original_severity} />
                {t.original_run_id ? <Link to={`/runs/${t.original_run_id}`} className="mono">{shortId(t.original_run_id)}</Link> : null}
                <Link to={`/findings/${t.finding_id}`}>finding</Link>
              </dd>
              <dt>Expects</dt>
              <dd className="mono" style={{ fontSize: 12 }}>{Object.entries(t.expected_checks).map(([k, v]) => `${k}=${v}`).join(", ")}</dd>
              <dt>Body sha256</dt>
              <dd className="mono" style={{ fontSize: 12 }}>{t.body_sha256.slice(0, 24)}…</dd>
            </dl>
            {t.retests.length === 0 ? (
              <span className="faint">Not retested yet.</span>
            ) : (
              <div className="table-wrap">
                <table className="t">
                  <thead><tr><th>Retest run</th><th>Target mode</th><th>Verdict</th><th>Result</th><th>Finished</th></tr></thead>
                  <tbody>
                    {t.retests.map((r) => (
                      <tr key={r.run_id}>
                        <td><Link to={`/runs/${r.run_id}`} className="mono">{shortId(r.run_id)}</Link></td>
                        <td>{r.target_mode ?? "—"}{r.config_version ? ` (v${r.config_version})` : ""}</td>
                        <td><VerdictBadge verdict={r.verdict} /></td>
                        <td>{retestStatus(r)}</td>
                        <td className="faint">{fmtTime(r.finished_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        ))
      )}
      <ConfirmDialog
        open={pending !== null}
        title="Run regression test?"
        body={<>This starts a retest run in the Docker sandbox against <strong>{target?.name}</strong> ({target?.config.mode} mode).</>}
        confirmLabel="Start retest"
        onConfirm={() => void start()}
        onCancel={() => setPending(null)}
      />
    </>
  );
}
