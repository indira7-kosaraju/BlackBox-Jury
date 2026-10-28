import type { JuryReview } from "../api/schemas";
import { Provenance } from "./ui";

const ROLE_LABEL: Record<string, string> = { reviewer: "Reviewer", counter_reviewer: "Counter-reviewer" };

function text(v: unknown): string | null {
  return typeof v === "string" && v.trim() ? v : null;
}

/** Advisory jury output. The backend has one reviewer and one counter-reviewer; nothing else is shown. */
export function JuryPanel({ reviews }: { reviews: JuryReview[] }) {
  if (reviews.length === 0) {
    return (
      <p className="muted">
        No jury review was recorded for this execution. The jury is optional and advisory; the verdict above comes from
        deterministic checks only.
      </p>
    );
  }
  return (
    <div className="stack" style={{ gap: 12 }}>
      <p className="faint" style={{ fontSize: 12 }}>
        Model analysis is advisory. It can never change a deterministic FAIL, a verdict or a severity.
      </p>
      {reviews.map((r, i) => {
        const o = r.output ?? {};
        const points = Array.isArray(o.unsupported_points) ? o.unsupported_points.filter((p): p is string => typeof p === "string") : [];
        return (
          <div key={`${r.role}-${i}`} className="call">
            <div className="row">
              <strong style={{ flex: 1 }}>{ROLE_LABEL[r.role] ?? r.role}</strong>
              <Provenance kind="model" />
              <span className="badge badge--neutral">{r.status}</span>
            </div>
            <span className="faint mono" style={{ fontSize: 11 }}>
              {r.model} · prompt {r.prompt_version}
            </span>
            {r.status !== "ok" ? (
              <span className="muted">Output was {r.status}; it was not used.</span>
            ) : (
              <>
                {text(o.summary) ? <p>{text(o.summary)}</p> : null}
                {text(o.root_cause) ? <p className="muted"><strong>Root cause:</strong> {text(o.root_cause)}</p> : null}
                {text(o.suggested_severity) ? <p className="muted"><strong>Suggested severity (advisory):</strong> {text(o.suggested_severity)}</p> : null}
                {typeof o.supports_review === "boolean" ? <p className="muted"><strong>Supports the review:</strong> {o.supports_review ? "yes" : "no"}</p> : null}
                {points.length ? (
                  <ul className="muted" style={{ margin: 0 }}>{points.map((p) => <li key={p}>{p}</li>)}</ul>
                ) : null}
                {text(o.concern) ? <p className="notice" style={{ fontSize: 13 }}>Concern: {text(o.concern)}</p> : null}
              </>
            )}
          </div>
        );
      })}
    </div>
  );
}
