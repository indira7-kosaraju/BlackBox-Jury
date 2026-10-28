import { Download } from "lucide-react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ApiError } from "../api/client";
import { useApi } from "../app/session";
import { CopyId, EmptyState, ErrorState, ReplayBadge, SeverityChip, Skeleton, StageTrack, useToast, VerdictBadge } from "../components/ui";
import { EvidenceCore } from "../core3d/EvidenceCore";
import { saveBlob } from "../lib/download";
import { CATEGORY_LABEL, fmtDuration, fmtMs, fmtTime, HARNESS_REASONS } from "../lib/format";
import { labStep } from "../lib/pipeline";
import { useRunWatch } from "../lib/useRunWatch";

export function RunDetail() {
  const { runId = "" } = useParams();
  const api = useApi();
  const toast = useToast();
  const navigate = useNavigate();
  const { run, executions, core } = useRunWatch(runId);
  const r = run.data;

  const download = async (format: "json" | "html") => {
    try {
      saveBlob(await api.reportFile(runId, format), `blackbox-jury-${runId}.${format}`);
    } catch (err) {
      toast(err instanceof ApiError ? err.message : "Download failed", "error");
    }
  };

  if (run.error) return <ErrorState error={run.error} onRetry={run.reload} />;
  if (!r) return <Skeleton lines={8} />;

  return (
    <>
      <div className="page-head">
        <div className="titles">
          <span className="eyebrow"><Link to="/runs">Test runs</Link> / {r.kind}</span>
          <h1 className="row" style={{ gap: 10 }}>Run <CopyId value={r.id} /> <ReplayBadge runId={r.id} /></h1>
          <span className="muted">{r.target_config_snapshot.image} · mode {r.target_config_snapshot.mode} · created {fmtTime(r.created_at)}</span>
        </div>
        <div className="row">
          <button className="btn" onClick={() => void download("json")}><Download size={15} aria-hidden /> JSON report</button>
          <button className="btn" onClick={() => void download("html")}><Download size={15} aria-hidden /> HTML report</button>
        </div>
      </div>

      <div className="grid cols-2">
        <section className="panel stack">
          <StageTrack step={labStep(core)} outcome={core.outcome} />
          <EvidenceCore state={core} compact />
        </section>
        <section className="panel">
          <h2 style={{ marginBottom: 12 }}>Run facts</h2>
          <dl className="kv">
            <dt>Status</dt><dd>{r.status}{r.status_reason ? ` (${r.status_reason})` : ""}</dd>
            <dt>Duration</dt><dd>{fmtDuration(r.started_at, r.finished_at)}</dd>
            <dt>Scenarios</dt><dd>{r.totals.selected} selected · {r.totals.executed} executed{r.totals.not_run ? ` · ${r.totals.not_run} not run` : ""}{r.totals.pending ? ` · ${r.totals.pending} pending` : ""}</dd>
            <dt>Verdicts</dt><dd>{r.totals.CONFIRMED} confirmed · {r.totals.NOT_REPRODUCED} not reproduced · {r.totals.INCONCLUSIVE} inconclusive</dd>
            <dt>Jury</dt><dd>{r.jury.replaceAll("_", " ")}</dd>
            <dt>Contract</dt><dd className="mono">{r.contract_id}</dd>
            <dt>Target config</dt><dd>v{r.target_config_snapshot.config_version ?? "—"} (snapshot at run time)</dd>
          </dl>
        </section>
      </div>

      <section className="panel panel--flush">
        <div className="panel-head" style={{ padding: "16px 16px 0" }}><h2>Executions</h2></div>
        {executions.error ? (
          <div style={{ padding: 16 }}><ErrorState error={executions.error} onRetry={executions.reload} /></div>
        ) : !executions.data ? (
          <div style={{ padding: 16 }}><Skeleton /></div>
        ) : executions.data.items.length === 0 ? (
          <div style={{ padding: 16 }}><EmptyState title="No executions" /></div>
        ) : (
          <div className="table-wrap">
            <table className="t">
              <thead>
                <tr><th>Scenario</th><th>Category</th><th>Status</th><th>Verdict</th><th>Severity</th><th>Duration</th></tr>
              </thead>
              <tbody>
                {executions.data.items.map((e) => (
                  <tr key={e.id} className="clickable" onClick={() => navigate(`/evidence/${e.id}`)}>
                    <td><Link to={`/evidence/${e.id}`}>{e.scenario_key}</Link></td>
                    <td>{CATEGORY_LABEL[e.category] ?? e.category}</td>
                    <td>{e.status}</td>
                    <td>
                      <VerdictBadge verdict={e.verdict} reason={e.inconclusive_reason} />
                      {e.inconclusive_reason && HARNESS_REASONS.has(e.inconclusive_reason) ? <div className="faint" style={{ fontSize: 11 }}>harness issue, not a target result</div> : null}
                      {e.task_completed !== null ? <div className="faint" style={{ fontSize: 11 }}>task {e.task_completed ? "completed" : "not completed"}</div> : null}
                    </td>
                    <td><SeverityChip severity={e.severity} /></td>
                    <td className="mono">{fmtMs(e.duration_ms)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </>
  );
}
