import { Download, FileOutput } from "lucide-react";
import { useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ApiError } from "../api/client";
import { useApi } from "../app/session";
import { JuryPanel } from "../components/Jury";
import { CheckOutcomeTag, CopyId, EmptyState, ErrorState, SeverityChip, Skeleton, useToast, VerdictBadge } from "../components/ui";
import { saveBlob } from "../lib/download";
import { CATEGORY_LABEL, fmtTime, SEVERITY_RANK, shortId } from "../lib/format";
import { useQuery } from "../lib/useQuery";

function FindingList() {
  const api = useApi();
  const navigate = useNavigate();
  const findings = useQuery(() => api.findings(), [api]);
  const [verdict, setVerdict] = useState("all");
  const [severity, setSeverity] = useState("all");
  const [category, setCategory] = useState("all");

  const items = useMemo(() => {
    const list = findings.data?.items ?? [];
    return list
      .filter((f) => verdict === "all" || f.verdict === verdict)
      .filter((f) => severity === "all" || f.severity === severity)
      .filter((f) => category === "all" || f.category === category)
      .sort((a, b) => (SEVERITY_RANK[b.severity ?? ""] ?? 0) - (SEVERITY_RANK[a.severity ?? ""] ?? 0));
  }, [findings.data, verdict, severity, category]);
  const categories = [...new Set(findings.data?.items.map((f) => f.category) ?? [])];

  return (
    <>
      <div className="page-head">
        <div className="titles">
          <span className="eyebrow">Evidence-backed results</span>
          <h1>Jury Findings</h1>
          <span className="muted">Verdicts come from deterministic checks. Jury commentary is advisory.</span>
        </div>
      </div>
      <div className="row">
        <label className="field"><span className="label">Verdict</span>
          <select className="input" value={verdict} onChange={(e) => setVerdict(e.target.value)}>
            <option value="all">All</option><option value="CONFIRMED">Confirmed</option><option value="INCONCLUSIVE">Inconclusive</option>
          </select>
        </label>
        <label className="field"><span className="label">Severity</span>
          <select className="input" value={severity} onChange={(e) => setSeverity(e.target.value)}>
            <option value="all">All</option>{["critical", "high", "medium", "low"].map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        </label>
        <label className="field"><span className="label">Category</span>
          <select className="input" value={category} onChange={(e) => setCategory(e.target.value)}>
            <option value="all">All</option>{categories.map((c) => <option key={c} value={c}>{CATEGORY_LABEL[c] ?? c}</option>)}
          </select>
        </label>
      </div>
      {findings.error ? <ErrorState error={findings.error} onRetry={findings.reload} /> : null}
      <section className="panel panel--flush">
        {!findings.data ? (
          <div style={{ padding: 16 }}><Skeleton lines={6} /></div>
        ) : items.length === 0 ? (
          <div style={{ padding: 16 }}>
            <EmptyState title={findings.data.items.length ? "No findings match these filters" : "No findings yet"}>
              {findings.data.items.length ? null : "A finding is recorded when an execution is CONFIRMED or INCONCLUSIVE."}
            </EmptyState>
          </div>
        ) : (
          <div className="table-wrap">
            <table className="t">
              <thead><tr><th>Finding</th><th>Verdict</th><th>Severity</th><th>Failed checks</th><th>Target</th><th>Regression</th><th className="hide-sm">Found</th></tr></thead>
              <tbody>
                {items.map((f) => (
                  <tr key={f.id} className="clickable" onClick={() => navigate(`/findings/${f.id}`)}>
                    <td>
                      <Link to={`/findings/${f.id}`}>{f.title}</Link>
                      <div className="faint" style={{ fontSize: 12 }}>{CATEGORY_LABEL[f.category] ?? f.category} · {f.scenario_key}</div>
                    </td>
                    <td><VerdictBadge verdict={f.verdict} /></td>
                    <td><SeverityChip severity={f.severity} /></td>
                    <td className="mono" style={{ fontSize: 12 }}>{f.failed_checks.join(", ") || "—"}</td>
                    <td>{f.target ?? "—"}<div className="faint" style={{ fontSize: 12 }}>{f.target_mode}</div></td>
                    <td>{f.regression_test_id ? <span className="badge badge--violet">exported</span> : <span className="faint">—</span>}</td>
                    <td className="hide-sm faint">{fmtTime(f.created_at)}</td>
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

function FindingView({ id }: { id: string }) {
  const api = useApi();
  const toast = useToast();
  const finding = useQuery(() => api.finding(id), [api, id]);
  const execution = useQuery(finding.data ? () => api.execution(finding.data!.execution_id) : null, [api, finding.data?.execution_id]);
  const regressions = useQuery(() => api.regressionTests(), [api]);
  const [busy, setBusy] = useState(false);
  const existing = regressions.data?.items.find((r) => r.finding_id === id);

  const exportTest = async () => {
    setBusy(true);
    try {
      const r = await api.exportRegression(id);
      toast(`Regression test ${shortId(r.id)} saved`);
      regressions.reload();
    } catch (err) {
      toast(err instanceof ApiError ? err.message : "Export failed", "error");
    } finally {
      setBusy(false);
    }
  };
  const downloadTest = async (rid: string) => {
    try {
      saveBlob(await api.regressionFile(rid), `${rid}.json`);
    } catch (err) {
      toast(err instanceof ApiError ? err.message : "Download failed", "error");
    }
  };

  if (finding.error) return <ErrorState error={finding.error} onRetry={finding.reload} />;
  const f = finding.data;
  if (!f) return <Skeleton lines={8} />;
  const d = execution.data;
  const failed = d?.checks.filter((c) => c.outcome !== "PASS") ?? [];

  return (
    <>
      <div className="page-head">
        <div className="titles">
          <span className="eyebrow"><Link to="/findings">Jury Findings</Link> / {CATEGORY_LABEL[f.category] ?? f.category}</span>
          <h1>{f.title}</h1>
          <span className="row muted" style={{ gap: 8 }}><CopyId value={f.id} label={shortId(f.id)} /> · run <Link to={`/runs/${f.run_id}`} className="mono">{shortId(f.run_id)}</Link></span>
        </div>
        <div className="row">
          <VerdictBadge verdict={f.verdict} />
          <SeverityChip severity={f.severity} />
        </div>
      </div>

      <div className="grid cols-2">
        <section className="panel stack">
          <h2>What the checks found</h2>
          {execution.error ? <ErrorState error={execution.error} onRetry={execution.reload} /> : null}
          {!d ? <Skeleton /> : failed.length === 0 ? (
            <span className="muted">No failed checks. {d.inconclusive_reason ? `Inconclusive: ${d.inconclusive_reason}.` : ""}</span>
          ) : (
            failed.map((c) => (
              <div key={c.check_id} className="call">
                <div className="row"><strong className="mono">{c.check_id}</strong><CheckOutcomeTag outcome={c.outcome} /></div>
                <span>{c.message}</span>
                <span className="faint mono" style={{ fontSize: 11 }}>{c.rule_ref} · evidence {c.evidence_refs.map(shortId).join(", ") || "—"}</span>
              </div>
            ))
          )}
          <Link to={`/evidence/${f.execution_id}`} className="btn btn--sm" style={{ alignSelf: "flex-start" }}>Open full evidence</Link>
        </section>

        <section className="panel stack">
          <h2>Reproduction</h2>
          {f.reproduction_steps.length ? (
            <ol style={{ margin: 0, paddingLeft: 20 }} className="stack">
              {f.reproduction_steps.map((s, i) => <li key={i} style={{ marginBottom: 4 }}>{s}</li>)}
            </ol>
          ) : <span className="muted">No reproduction steps recorded.</span>}
          {d?.agent_claim ? (
            <>
              <span className="label">Agent's claim (unverified)</span>
              <div className="claim">{d.agent_claim.text}</div>
            </>
          ) : null}
        </section>
      </div>

      <div className="grid cols-2">
        <section className="panel stack">
          <h2>Jury</h2>
          {d ? <JuryPanel reviews={d.jury} /> : <Skeleton />}
        </section>
        <section className="panel stack">
          <h2>Regression test</h2>
          {existing ? (
            <>
              <span>Exported as <CopyId value={existing.id} label={shortId(existing.id)} /> on {fmtTime(existing.created_at)}.</span>
              <div className="row">
                <Link to="/regressions" className="btn btn--sm">View retests ({existing.retests.length})</Link>
                <button className="btn btn--sm" onClick={() => void downloadTest(existing.id)}><Download size={14} aria-hidden /> Download JSON</button>
              </div>
            </>
          ) : f.verdict === "CONFIRMED" ? (
            <>
              <span className="muted">Save this confirmed finding as a portable regression test so you can re-run it after a fix.</span>
              <button className="btn btn--primary" style={{ alignSelf: "flex-start" }} disabled={busy} onClick={() => void exportTest()}>
                <FileOutput size={15} aria-hidden /> {busy ? "Exporting…" : "Export regression test"}
              </button>
            </>
          ) : (
            <span className="muted">Only CONFIRMED findings can be exported as regression tests.</span>
          )}
        </section>
      </div>
    </>
  );
}

export function Findings() {
  const { findingId } = useParams();
  return findingId ? <FindingView id={findingId} /> : <FindingList />;
}
