import { Link, useNavigate } from "react-router-dom";
import { useApi } from "../app/session";
import { EmptyState, ErrorState, Skeleton } from "../components/ui";
import { fmtDuration, fmtTime, shortId } from "../lib/format";
import { useQuery } from "../lib/useQuery";

export function Runs() {
  const api = useApi();
  const navigate = useNavigate();
  const runs = useQuery(() => api.runs(100), [api], { pollMs: 5000 });

  return (
    <>
      <div className="page-head">
        <div className="titles">
          <span className="eyebrow">History</span>
          <h1>Test Runs</h1>
        </div>
        <Link to="/lab" className="btn btn--primary">New run</Link>
      </div>
      {runs.error ? <ErrorState error={runs.error} onRetry={runs.reload} /> : null}
      <section className="panel panel--flush">
        {!runs.data ? (
          <div style={{ padding: 16 }}><Skeleton lines={6} /></div>
        ) : runs.data.items.length === 0 ? (
          <div style={{ padding: 16 }}><EmptyState title="No runs yet" action={<Link to="/lab" className="btn btn--sm">Open Test Lab</Link>} /></div>
        ) : (
          <div className="table-wrap">
            <table className="t">
              <thead>
                <tr>
                  <th>Run</th><th>Kind</th><th>Status</th><th>Target</th><th>Confirmed</th><th>Not reproduced</th><th>Inconclusive</th><th>Duration</th><th>Created</th>
                </tr>
              </thead>
              <tbody>
                {runs.data.items.map((r) => (
                  <tr key={r.id} className="clickable" onClick={() => navigate(`/runs/${r.id}`)}>
                    <td><Link to={`/runs/${r.id}`} className="mono">{shortId(r.id)}</Link></td>
                    <td>{r.kind}</td>
                    <td>{r.status}{r.status_reason ? <span className="faint"> ({r.status_reason})</span> : null}</td>
                    <td>{r.target_config_snapshot.mode}</td>
                    <td className="mono">{r.totals.CONFIRMED}</td>
                    <td className="mono">{r.totals.NOT_REPRODUCED}</td>
                    <td className="mono">{r.totals.INCONCLUSIVE}</td>
                    <td className="mono">{fmtDuration(r.started_at, r.finished_at)}</td>
                    <td className="faint">{fmtTime(r.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {runs.data?.next_cursor ? <p className="faint" style={{ padding: 16 }}>Showing the 100 most recent runs.</p> : null}
      </section>
    </>
  );
}
