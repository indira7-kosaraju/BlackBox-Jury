import { FlaskConical } from "lucide-react";
import { Link, useNavigate } from "react-router-dom";
import type { AuditEvent } from "../api/schemas";
import { useApi } from "../app/session";
import { EmptyState, ErrorState, SeverityChip, Skeleton, VerdictBadge } from "../components/ui";
import { EvidenceCore } from "../core3d/EvidenceCore";
import { CATEGORY_LABEL, fmtTime, shortId } from "../lib/format";
import { useQuery } from "../lib/useQuery";
import { useRunWatch } from "../lib/useRunWatch";

const ACTION_LABEL: Record<string, string> = {
  "run.create": "Run started",
  "run.cancel": "Run cancelled",
  "retest.create": "Retest started",
  "regression.export": "Regression test exported",
  "target.create": "Target registered",
  "target.authorize": "Target authorized",
  "target.update": "Target configuration changed",
  "contract.create": "Contract version stored",
  "scenario.generate": "Scenarios generated",
};

function auditLine(e: AuditEvent): string {
  const mode = (v: unknown) => (v && typeof v === "object" && "mode" in v ? String(v.mode) : null);
  if (e.action === "target.update") {
    const before = mode(e.details.before);
    const after = mode(e.details.after);
    if (before && after) return `Target mode ${before} → ${after}`;
  }
  return ACTION_LABEL[e.action] ?? e.action;
}

function Metric({ label, value, sub, to }: { label: string; value: string | number; sub?: string; to: string }) {
  return (
    <Link to={to} className="panel metric">
      <span className="label">{label}</span>
      <span className="value">{value}</span>
      {sub ? <span className="faint" style={{ fontSize: 12 }}>{sub}</span> : null}
    </Link>
  );
}

export function Overview() {
  const api = useApi();
  const navigate = useNavigate();
  const runs = useQuery(() => api.runs(25), [api], { pollMs: 10_000 });
  const findings = useQuery(() => api.findings(), [api], { pollMs: 15_000 });
  const regressions = useQuery(() => api.regressionTests(), [api]);
  const targets = useQuery(() => api.targets(), [api]);
  const audit = useQuery(() => api.auditEvents(15), [api], { pollMs: 15_000 });
  const latest = runs.data?.items[0];
  const watch = useRunWatch(latest?.id);

  const confirmed = findings.data?.items.filter((f) => f.verdict === "CONFIRMED") ?? [];
  const critical = confirmed.filter((f) => f.severity === "critical" || f.severity === "high").length;
  const authorized = targets.data?.items.filter((t) => t.authorization_status === "authorized").length;
  const runCount = runs.data ? `${runs.data.items.length}${runs.data.next_cursor ? "+" : ""}` : "—";

  return (
    <>
      <div className="page-head">
        <div className="titles">
          <span className="eyebrow">Local sandbox · synthetic data</span>
          <h1>AI Agent Security Command Center</h1>
          <span className="muted">Adversarial scenarios, gateway evidence and deterministic verdicts for your authorized agents.</span>
        </div>
        <button className="btn btn--primary" onClick={() => navigate("/lab")}>
          <FlaskConical size={16} aria-hidden /> Open Test Lab
        </button>
      </div>

      {runs.error ? <ErrorState error={runs.error} onRetry={runs.reload} /> : null}

      <div className="grid metrics">
        <Metric label="Test runs" value={runCount} sub="most recent 25 shown" to="/runs" />
        <Metric label="Confirmed findings" value={findings.data ? confirmed.length : "—"} sub={findings.data ? `${critical} high or critical` : undefined} to="/findings" />
        <Metric label="Regression tests" value={regressions.data?.items.length ?? "—"} sub="exported from confirmed findings" to="/regressions" />
        <Metric label="Authorized targets" value={authorized ?? "—"} sub="local sandbox only" to="/targets" />
      </div>

      <div className="grid cols-2">
        <section className="panel" aria-labelledby="core-h">
          <div className="panel-head">
            <h2 id="core-h">Evidence Core</h2>
            {latest ? (
              <Link to={`/runs/${latest.id}`} className="mono faint">
                {shortId(latest.id)}
              </Link>
            ) : null}
          </div>
          {runs.loading && !runs.data ? <Skeleton lines={4} /> : <EvidenceCore state={watch.core} />}
        </section>

        <section className="panel" aria-labelledby="act-h">
          <div className="panel-head">
            <h2 id="act-h">Activity</h2>
            <span className="faint" style={{ fontSize: 12 }}>audit log</span>
          </div>
          {audit.error ? (
            <ErrorState error={audit.error} onRetry={audit.reload} />
          ) : !audit.data ? (
            <Skeleton lines={5} />
          ) : audit.data.items.length === 0 ? (
            <EmptyState title="No activity yet" />
          ) : (
            <ol className="timeline">
              {audit.data.items.map((e) => (
                <li key={e.id}>
                  <span className={`tl-dot ${e.action.includes("cancel") ? "tl-dot--warn" : ""}`} aria-hidden />
                  <div className="stack" style={{ gap: 2 }}>
                    <span>{auditLine(e)}</span>
                    <span className="faint mono" style={{ fontSize: 11 }}>
                      {fmtTime(e.created_at)} · {e.actor}
                      {e.entity_id ? ` · ${shortId(e.entity_id)}` : ""}
                    </span>
                  </div>
                </li>
              ))}
            </ol>
          )}
        </section>
      </div>

      <div className="grid cols-2">
        <section className="panel panel--flush" aria-labelledby="runs-h">
          <div className="panel-head" style={{ padding: "16px 16px 0" }}>
            <h2 id="runs-h">Recent runs</h2>
            <Link to="/runs">All runs</Link>
          </div>
          {!runs.data ? (
            <div style={{ padding: 16 }}><Skeleton /></div>
          ) : runs.data.items.length === 0 ? (
            <div style={{ padding: 16 }}>
              <EmptyState title="No runs yet" action={<Link to="/lab" className="btn btn--sm">Start a test run</Link>}>
                Run the demo suite from the Test Lab, or <code className="mono">make demo</code> in a terminal.
              </EmptyState>
            </div>
          ) : (
            <div className="table-wrap">
              <table className="t">
                <thead>
                  <tr><th>Run</th><th>Status</th><th>Mode</th><th>Results</th><th className="hide-sm">Created</th></tr>
                </thead>
                <tbody>
                  {runs.data.items.slice(0, 6).map((r) => (
                    <tr key={r.id} className="clickable" onClick={() => navigate(`/runs/${r.id}`)}>
                      <td><Link to={`/runs/${r.id}`} className="mono">{shortId(r.id)}</Link>{r.kind === "retest" ? <span className="badge badge--violet" style={{ marginLeft: 6 }}>retest</span> : null}</td>
                      <td>{r.status}</td>
                      <td>{r.target_config_snapshot.mode}</td>
                      <td className="mono" style={{ fontSize: 12 }}>
                        {r.totals.CONFIRMED}C · {r.totals.NOT_REPRODUCED}N · {r.totals.INCONCLUSIVE}I
                      </td>
                      <td className="hide-sm faint">{fmtTime(r.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        <section className="panel panel--flush" aria-labelledby="find-h">
          <div className="panel-head" style={{ padding: "16px 16px 0" }}>
            <h2 id="find-h">Latest findings</h2>
            <Link to="/findings">All findings</Link>
          </div>
          {findings.error ? (
            <div style={{ padding: 16 }}><ErrorState error={findings.error} onRetry={findings.reload} /></div>
          ) : !findings.data ? (
            <div style={{ padding: 16 }}><Skeleton /></div>
          ) : findings.data.items.length === 0 ? (
            <div style={{ padding: 16 }}><EmptyState title="No findings">Findings appear here when a run confirms a vulnerability or ends inconclusive.</EmptyState></div>
          ) : (
            <div className="table-wrap">
              <table className="t">
                <tbody>
                  {findings.data.items.slice(0, 6).map((f) => (
                    <tr key={f.id} className="clickable" onClick={() => navigate(`/findings/${f.id}`)}>
                      <td>
                        <Link to={`/findings/${f.id}`}>{f.title}</Link>
                        <div className="faint" style={{ fontSize: 12 }}>{CATEGORY_LABEL[f.category] ?? f.category}</div>
                      </td>
                      <td><VerdictBadge verdict={f.verdict} /></td>
                      <td><SeverityChip severity={f.severity} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </div>
    </>
  );
}
