import { ShieldAlert, ShieldCheck } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import type { Artifact, Check, ExecutionDetail } from "../api/schemas";
import { useApi } from "../app/session";
import { JuryPanel } from "../components/Jury";
import {
  CheckOutcomeTag,
  CopyId,
  Drawer,
  EmptyState,
  ErrorState,
  Provenance,
  SeverityChip,
  Skeleton,
  VerdictBadge,
} from "../components/ui";
import { CATEGORY_LABEL, fmtMs, fmtTime, HARNESS_REASONS, prettyJson, shortId } from "../lib/format";
import { useQuery } from "../lib/useQuery";

async function sha256Hex(text: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

function ArtifactViewer({ id, onClose }: { id: string | null; onClose: () => void }) {
  const api = useApi();
  const art = useQuery(id ? () => api.artifact(id) : null, [api, id]);
  const [match, setMatch] = useState<boolean | null>(null);
  useEffect(() => {
    setMatch(null);
    const a: Artifact | undefined = art.data;
    if (a) void sha256Hex(a.content_redacted).then((h) => setMatch(h === a.sha256));
  }, [art.data]);
  return (
    <Drawer title="Artifact" open={id !== null} onClose={onClose}>
      {art.error ? <ErrorState error={art.error} onRetry={art.reload} /> : null}
      {!art.data ? (
        <Skeleton lines={6} />
      ) : (
        <>
          <dl className="kv">
            <dt>Kind</dt><dd>{art.data.kind}</dd>
            <dt>Id</dt><dd><CopyId value={art.data.id} /></dd>
            <dt>sha256</dt><dd className="mono" style={{ fontSize: 12 }}>{art.data.sha256}</dd>
            <dt>Integrity</dt>
            <dd>
              {match === null ? "checking…" : match ? (
                <span className="row" style={{ gap: 6 }}><ShieldCheck size={15} color="var(--teal)" aria-hidden /> Content hash matches the stored sha256 (checked in this browser)</span>
              ) : (
                <span className="row" style={{ gap: 6 }}><ShieldAlert size={15} color="var(--red)" aria-hidden /> Hash mismatch — do not trust this content</span>
              )}
            </dd>
            {art.data.truncated ? <><dt>Truncated</dt><dd>Yes — content was capped before storage</dd></> : null}
          </dl>
          <span className="faint" style={{ fontSize: 12 }}>Redacted, untrusted content. Shown as plain text.</span>
          <pre className="code-block json">{art.data.content_redacted}</pre>
        </>
      )}
    </Drawer>
  );
}

function ExecutionPicker() {
  const api = useApi();
  const navigate = useNavigate();
  const runs = useQuery(() => api.runs(10), [api]);
  const [runId, setRunId] = useState<string>("");
  const selected = runId || runs.data?.items[0]?.id || "";
  const execs = useQuery(selected ? () => api.executions(selected) : null, [api, selected]);
  return (
    <>
      <div className="page-head">
        <div className="titles">
          <span className="eyebrow">Forensics</span>
          <h1>Evidence Explorer</h1>
          <span className="muted">Pick an execution to inspect its gateway evidence, checks, state diff and artifacts.</span>
        </div>
        {runs.data && runs.data.items.length > 0 ? (
          <label className="field" style={{ minWidth: 260 }}>
            <span className="label">Run</span>
            <select className="input" value={selected} onChange={(e) => setRunId(e.target.value)}>
              {runs.data.items.map((r) => (
                <option key={r.id} value={r.id}>{shortId(r.id)} · {r.kind} · {r.target_config_snapshot.mode} · {fmtTime(r.created_at)}</option>
              ))}
            </select>
          </label>
        ) : null}
      </div>
      {runs.error ? <ErrorState error={runs.error} onRetry={runs.reload} /> : null}
      {runs.data?.items.length === 0 ? <EmptyState title="No runs yet">Evidence appears after a run executes in the sandbox.</EmptyState> : null}
      {execs.data ? (
        <section className="panel panel--flush">
          <div className="table-wrap">
            <table className="t">
              <thead><tr><th>Scenario</th><th>Category</th><th>Verdict</th><th>Severity</th></tr></thead>
              <tbody>
                {execs.data.items.map((e) => (
                  <tr key={e.id} className="clickable" onClick={() => navigate(`/evidence/${e.id}`)}>
                    <td><Link to={`/evidence/${e.id}`}>{e.scenario_key}</Link></td>
                    <td>{CATEGORY_LABEL[e.category] ?? e.category}</td>
                    <td><VerdictBadge verdict={e.verdict} reason={e.inconclusive_reason} /></td>
                    <td><SeverityChip severity={e.severity} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ) : selected ? <Skeleton lines={5} /> : null}
    </>
  );
}

function citations(checks: Check[]): Map<string, Check[]> {
  const m = new Map<string, Check[]>();
  for (const c of checks) for (const ref of c.evidence_refs) m.set(ref, [...(m.get(ref) ?? []), c]);
  return m;
}

function ExecutionView({ d }: { d: ExecutionDetail }) {
  const [artifactId, setArtifactId] = useState<string | null>(null);
  const cited = useMemo(() => citations(d.checks), [d.checks]);
  const failed = d.checks.filter((c) => c.outcome === "FAIL");
  const harness = d.inconclusive_reason && HARNESS_REASONS.has(d.inconclusive_reason);

  return (
    <>
      <div className="page-head">
        <div className="titles">
          <span className="eyebrow"><Link to="/evidence">Evidence Explorer</Link> / {CATEGORY_LABEL[d.scenario.category] ?? d.scenario.category}</span>
          <h1>{d.scenario.title ?? d.scenario.key}</h1>
          <span className="row muted" style={{ gap: 8 }}>
            <span className="mono">{d.scenario.key} v{d.scenario.version}</span>·<CopyId value={d.execution_id} label={shortId(d.execution_id)} />
          </span>
        </div>
        <div className="row">
          <VerdictBadge verdict={d.verdict} reason={d.inconclusive_reason} />
          <SeverityChip severity={d.severity} />
        </div>
      </div>

      {harness ? (
        <div className="notice">This result is INCONCLUSIVE because of a harness problem ({d.inconclusive_reason}), not because of the target. It is not a pass.</div>
      ) : null}
      {d.evidence_problems.length ? (
        <div className="notice">
          <strong>Evidence problems:</strong>
          <ul style={{ margin: "4px 0 0" }}>{d.evidence_problems.map((p) => <li key={p}>{p}</li>)}</ul>
        </div>
      ) : null}

      <div className="grid cols-2">
        <section className="panel stack" aria-labelledby="calls-h">
          <div className="panel-head" style={{ marginBottom: 0 }}>
            <h2 id="calls-h">Tool calls</h2>
            <Provenance kind="verified" />
          </div>
          <span className="faint" style={{ fontSize: 12 }}>Recorded by the gateway, never taken from the agent's own report.</span>
          {d.verified_outcome.tool_calls.length === 0 ? (
            <EmptyState title="No tool calls recorded" />
          ) : (
            d.verified_outcome.tool_calls.map((c) => {
              const by = cited.get(c.id) ?? [];
              const flagged = by.some((x) => x.outcome === "FAIL");
              return (
                <div key={c.id} id={c.id} className={`call ${flagged ? "call--flagged" : ""}`}>
                  <div className="row">
                    <span className="mono faint">#{c.seq}</span>
                    <strong className="mono">{c.tool}</strong>
                    <span className="badge badge--neutral">{c.outcome}</span>
                    {c.side_effect ? <span className="badge badge--violet">side effect</span> : null}
                    <span className="spacer" />
                    <span className="faint mono" style={{ fontSize: 11 }}>{shortId(c.id)}</span>
                  </div>
                  <pre className="json">{c.args ? prettyJson(c.args) : "(args unavailable)"}</pre>
                  {by.length ? (
                    <div className="row" style={{ gap: 6 }}>
                      <span className="faint" style={{ fontSize: 12 }}>Cited by:</span>
                      {by.map((x) => (
                        <a key={x.check_id} href={`#chk-${x.check_id}`} className="mono" style={{ fontSize: 12 }}>
                          {x.check_id} ({x.outcome})
                        </a>
                      ))}
                    </div>
                  ) : null}
                </div>
              );
            })
          )}
        </section>

        <section className="stack">
          <div className="panel stack" style={{ gap: 8 }}>
            <div className="panel-head" style={{ marginBottom: 0 }}>
              <h2>Agent's claim</h2>
              <Provenance kind="claim" />
            </div>
            {d.agent_claim ? (
              <div className="claim">{d.agent_claim.text}</div>
            ) : (
              <span className="muted">The agent produced no final message.</span>
            )}
            <span className="faint" style={{ fontSize: 12 }}>Unverified. Compare it with the gateway record and checks.</span>
          </div>

          <div className="panel">
            <h2 style={{ marginBottom: 12 }}>Sandbox</h2>
            <dl className="kv">
              <dt>Status</dt><dd>{d.status}</dd>
              <dt>Duration</dt><dd>{fmtMs(d.sandbox_meta.duration_ms ?? null)}</dd>
              <dt>Agent exit</dt><dd>{d.sandbox_meta.agent_exit_code ?? "—"}</dd>
              <dt>Cleanup</dt><dd>{d.sandbox_meta.cleanup_ok === true ? "ok" : d.sandbox_meta.cleanup_ok === false ? "FAILED" : "—"}</dd>
              <dt>Outcome</dt><dd>{d.sandbox_meta.outcome ?? "—"}</dd>
              <dt>Image</dt><dd className="mono" style={{ fontSize: 12 }}>{d.sandbox_meta.agent_image ?? "—"}</dd>
              <dt>Window</dt><dd>{fmtTime(d.started_at)} → {fmtTime(d.finished_at)}</dd>
            </dl>
          </div>
        </section>
      </div>

      <section className="panel panel--flush" aria-labelledby="checks-h">
        <div className="panel-head" style={{ padding: "16px 16px 0" }}>
          <h2 id="checks-h">Deterministic checks</h2>
          <span className="faint" style={{ fontSize: 12 }}>{failed.length} failed of {d.checks.length}</span>
        </div>
        {d.checks.length === 0 ? (
          <div style={{ padding: 16 }}><EmptyState title="No checks ran">Checks run only when evidence is complete.</EmptyState></div>
        ) : (
          <div className="table-wrap">
            <table className="t">
              <thead><tr><th>Check</th><th>Outcome</th><th>Message</th><th>Evidence</th><th className="hide-sm">Rule</th></tr></thead>
              <tbody>
                {d.checks.map((c) => (
                  <tr key={c.check_id} id={`chk-${c.check_id}`}>
                    <td className="mono">{c.check_id}</td>
                    <td><CheckOutcomeTag outcome={c.outcome} /></td>
                    <td>{c.message}</td>
                    <td>
                      {c.evidence_refs.length === 0 ? <span className="faint">—</span> : c.evidence_refs.map((ref) => (
                        <div key={ref}>
                          {ref.startsWith("art_") ? (
                            <button className="btn btn--ghost btn--sm mono" style={{ padding: 0 }} onClick={() => setArtifactId(ref)}>{shortId(ref)}</button>
                          ) : (
                            <a href={`#${ref}`} className="mono" style={{ fontSize: 12 }}>{shortId(ref)}</a>
                          )}
                        </div>
                      ))}
                    </td>
                    <td className="hide-sm faint mono" style={{ fontSize: 11 }}>{c.rule_ref}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <div className="grid cols-2">
        <section className="panel" aria-labelledby="diff-h">
          <h2 id="diff-h" style={{ marginBottom: 12 }}>State diff</h2>
          {d.state_diff === null ? (
            <span className="muted">No state diff was captured.</span>
          ) : d.state_diff.length === 0 ? (
            <span className="muted">No database changes.</span>
          ) : (
            <div className="table-wrap">
              <table className="t">
                <thead><tr><th></th><th>Table</th><th>Key</th><th>Before</th><th>After</th></tr></thead>
                <tbody>
                  {d.state_diff.map((row) => (
                    <tr key={`${row.table}-${String(row.key)}-${row.change}`}>
                      <td className={`mono ${row.change === "+" ? "diff-add" : row.change === "-" ? "diff-del" : "diff-mod"}`}>
                        {row.change === "+" ? "+ added" : row.change === "-" ? "− removed" : "~ changed"}
                      </td>
                      <td className="mono">{row.table}</td>
                      <td className="mono">{String(row.key)}</td>
                      <td><pre className="json">{row.before ? prettyJson(row.before) : "—"}</pre></td>
                      <td><pre className="json">{row.after ? prettyJson(row.after) : "—"}</pre></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        <section className="panel stack" aria-labelledby="jury-h">
          <h2 id="jury-h">Jury</h2>
          <JuryPanel reviews={d.jury} />
        </section>
      </div>

      <section className="panel panel--flush" aria-labelledby="art-h">
        <div className="panel-head" style={{ padding: "16px 16px 0" }}><h2 id="art-h">Artifacts</h2></div>
        {d.artifacts.length === 0 ? (
          <div style={{ padding: 16 }}><EmptyState title="No artifacts" /></div>
        ) : (
          <div className="table-wrap">
            <table className="t">
              <thead><tr><th>Kind</th><th>sha256</th><th>Truncated</th><th></th></tr></thead>
              <tbody>
                {d.artifacts.map((a) => (
                  <tr key={a.id}>
                    <td>{a.kind}</td>
                    <td className="mono" style={{ fontSize: 12 }}>{a.sha256.slice(0, 16)}…</td>
                    <td>{a.truncated ? "yes" : "no"}</td>
                    <td><button className="btn btn--sm" onClick={() => setArtifactId(a.id)}>View & verify</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      <ArtifactViewer id={artifactId} onClose={() => setArtifactId(null)} />
    </>
  );
}

export function Evidence() {
  const { executionId } = useParams();
  const api = useApi();
  const detail = useQuery(executionId ? () => api.execution(executionId) : null, [api, executionId]);
  if (!executionId) return <ExecutionPicker />;
  if (detail.error) return <ErrorState error={detail.error} onRetry={detail.reload} />;
  if (!detail.data) return <Skeleton lines={10} />;
  return <ExecutionView d={detail.data} />;
}
