import { Fragment } from "react";
import { useApi, useSession } from "../app/session";
import { ErrorState, Skeleton } from "../components/ui";
import { useQuery } from "../lib/useQuery";

function mib(bytes: number): string {
  return `${Math.round(bytes / (1024 * 1024))} MiB`;
}

export function SettingsPage() {
  const api = useApi();
  const { mode } = useSession();
  const settings = useQuery(() => api.settings(), [api]);
  const ready = useQuery(() => api.ready(), [api]);
  const s = settings.data;

  return (
    <>
      <div className="page-head">
        <div className="titles">
          <span className="eyebrow">Read-only</span>
          <h1>Settings</h1>
          <span className="muted">Configuration comes from the server environment (<code className="mono">.env</code>). Secrets are never shown.</span>
        </div>
      </div>
      {settings.error ? <ErrorState error={settings.error} onRetry={settings.reload} /> : null}
      {!s ? <Skeleton lines={8} /> : (
        <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))" }}>
          <section className="panel">
            <h2 style={{ marginBottom: 12 }}>Sandbox</h2>
            <dl className="kv">
              <dt>Network</dt><dd>{s.sandbox.network}</dd>
              <dt>Filesystem</dt><dd>{s.sandbox.filesystem}</dd>
              <dt>User</dt><dd>{s.sandbox.user}</dd>
              <dt>Memory</dt><dd>{mib(s.sandbox.mem_bytes)}</dd>
              <dt>CPUs</dt><dd>{s.sandbox.cpus}</dd>
              <dt>PIDs limit</dt><dd>{s.sandbox.pids_limit}</dd>
              <dt>Execution timeout</dt><dd>{s.sandbox.exec_timeout_s} s</dd>
              <dt>Run timeout</dt><dd>{s.sandbox.run_timeout_s} s</dd>
              <dt>Gateway image</dt><dd className="mono" style={{ fontSize: 12 }}>{s.sandbox.gateway_image}</dd>
              <dt>Allowed targets</dt><dd className="mono" style={{ fontSize: 12 }}>{s.sandbox.allowed_target_images.join(", ")}</dd>
              <dt>Adapters</dt><dd>{s.sandbox.adapter_types.join(", ")}</dd>
            </dl>
          </section>
          <section className="panel">
            <h2 style={{ marginBottom: 12 }}>Readiness</h2>
            {ready.error ? <ErrorState error={ready.error} onRetry={ready.reload} /> : !ready.data ? <Skeleton /> : (
              <dl className="kv">
                <dt>Overall</dt><dd>{ready.data.status}</dd>
                {Object.entries(ready.data.checks).map(([k, v]) => (
                  <Fragment key={k}><dt>{k}</dt><dd>{v}</dd></Fragment>
                ))}
              </dl>
            )}
            <h2 style={{ margin: "20px 0 12px" }}>Application</h2>
            <dl className="kv">
              <dt>Version</dt><dd>{s.app.version}</dd>
              <dt>Environment</dt><dd>{s.app.env}</dd>
              <dt>Data source</dt><dd>{mode === "demo" ? "Recorded demo fixture" : "Live server"}</dd>
            </dl>
          </section>
          <section className="panel">
            <h2 style={{ marginBottom: 12 }}>Jury (advisory)</h2>
            <dl className="kv">
              <dt>Enabled</dt><dd>{s.jury.enabled ? "yes" : "no"}</dd>
              <dt>Provider</dt><dd>{s.jury.provider}</dd>
              <dt>Model</dt><dd>{s.jury.model ?? "—"}</dd>
              <dt>Credentials</dt><dd>{s.jury.credentials_configured === null ? "not required" : s.jury.credentials_configured ? "configured" : "missing"}</dd>
            </dl>
            <p className="faint" style={{ fontSize: 12, marginTop: 12 }}>The jury never changes verdicts or severities.</p>
          </section>
          <section className="panel">
            <h2 style={{ marginBottom: 12 }}>Limits & retention</h2>
            <dl className="kv">
              <dt>Max scenarios / run</dt><dd>{s.limits.max_scenarios_per_run}</dd>
              {Object.entries(s.limits.rate_limits_per_minute).map(([k, v]) => (
                <Fragment key={k}><dt>Rate · {k}</dt><dd>{v} / min</dd></Fragment>
              ))}
              <dt>Retention</dt><dd>{s.retention.implemented ? "enabled" : "Not available"} <span className="faint">— {s.retention.note}</span></dd>
            </dl>
          </section>
        </div>
      )}
    </>
  );
}
