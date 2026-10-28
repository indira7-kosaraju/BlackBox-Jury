import { CheckCircle2, Plus, ShieldCheck } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { ApiError } from "../api/client";
import type { ContractBody, Target } from "../api/schemas";
import { useApi } from "../app/session";
import { ConfirmDialog, CopyId, Drawer, EmptyState, ErrorState, Skeleton, useToast } from "../components/ui";
import { fmtTime, prettyJson, shortId } from "../lib/format";
import { useQuery } from "../lib/useQuery";

const MAX_CONTRACT_CHARS = 64_000;

function TargetCard({ t, onChanged }: { t: Target; onChanged: () => void }) {
  const api = useApi();
  const toast = useToast();
  const [confirm, setConfirm] = useState(false);
  const next = t.config.mode === "vulnerable" ? "hardened" : "vulnerable";
  const apply = async () => {
    setConfirm(false);
    try {
      const u = await api.setTargetMode(t.id, next);
      toast(`${u.name} is now ${u.config.mode} (config v${u.config_version})`);
      onChanged();
    } catch (err) {
      toast(err instanceof ApiError ? err.message : "Update failed", "error");
    }
  };
  return (
    <article className="panel target-card">
      <div className="row">
        <h2 style={{ flex: 1 }}>{t.name}</h2>
        <span className={`badge ${t.authorization_status === "authorized" ? "badge--cyan" : "badge--neutral"}`}>
          {t.authorization_status === "authorized" ? <ShieldCheck size={12} aria-hidden /> : null} {t.authorization_status}
        </span>
      </div>
      <dl className="kv">
        <dt>Id</dt><dd><CopyId value={t.id} label={shortId(t.id)} /></dd>
        <dt>Image</dt><dd className="mono" style={{ fontSize: 12 }}>{t.config.image}</dd>
        <dt>Mode</dt><dd><strong>{t.config.mode}</strong> · config v{t.config_version}</dd>
        <dt>Adapter</dt><dd>{t.adapter_type}{t.is_local ? " · local" : ""}</dd>
        <dt>Authorized</dt><dd>{t.authorized_by} · {fmtTime(t.authorized_at)}</dd>
      </dl>
      {t.authorization_status === "authorized" ? (
        <button className="btn btn--sm" style={{ alignSelf: "flex-start" }} onClick={() => setConfirm(true)}>
          Switch to {next} mode
        </button>
      ) : null}
      <ConfirmDialog
        open={confirm}
        title={`Switch ${t.name} to ${next}?`}
        body={<>This changes the target configuration (v{t.config_version} → v{t.config_version + 1}) and is recorded in the audit log. Existing runs keep their snapshot.</>}
        confirmLabel={`Switch to ${next}`}
        onConfirm={() => void apply()}
        onCancel={() => setConfirm(false)}
      />
    </article>
  );
}

function AddTarget({ open, onClose, onCreated }: { open: boolean; onClose: () => void; onCreated: () => void }) {
  const api = useApi();
  const toast = useToast();
  const settings = useQuery(open ? () => api.settings() : null, [api, open]);
  const images = settings.data?.sandbox.allowed_target_images ?? [];
  const [name, setName] = useState("");
  const [image, setImage] = useState("");
  const [mode, setMode] = useState<"vulnerable" | "hardened">("vulnerable");
  const [statement, setStatement] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  useEffect(() => {
    if (!image && images[0]) setImage(images[0]);
  }, [images, image]);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    try {
      const t = await api.createTarget({ name: name.trim(), config: { image, mode }, authorization: { confirm_authorized: confirmed, statement: statement.trim() } });
      toast(`Target ${t.name} registered`);
      onCreated();
      onClose();
    } catch (err) {
      setError(err instanceof ApiError ? err : new ApiError(0, "client_error", String(err)));
    }
  };

  return (
    <Drawer title="Register target" open={open} onClose={onClose}>
      <p className="muted">Only images on the server allowlist can be registered. External or remote targets are not supported.</p>
      <form className="stack" onSubmit={submit}>
        <label className="field"><span className="label">Name</span>
          <input className="input" value={name} onChange={(e) => setName(e.target.value)} required maxLength={64} pattern="[a-z][a-z0-9_\-]{1,63}" title="2–64 chars: starts with a lowercase letter; then lowercase letters, digits, - or _" />
        </label>
        <label className="field"><span className="label">Image (allowlisted)</span>
          <select className="input" value={image} onChange={(e) => setImage(e.target.value)} required>
            {images.map((i) => <option key={i} value={i}>{i}</option>)}
          </select>
        </label>
        <label className="field"><span className="label">Mode</span>
          <select className="input" value={mode} onChange={(e) => setMode(e.target.value as "vulnerable" | "hardened")}>
            <option value="vulnerable">vulnerable</option><option value="hardened">hardened</option>
          </select>
        </label>
        <label className="field"><span className="label">Authorization statement</span>
          <textarea className="input" rows={3} value={statement} onChange={(e) => setStatement(e.target.value)} required maxLength={500} placeholder="e.g. Bundled local mock target, owned by our team" />
        </label>
        <label className="check">
          <input type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} />
          <span>I confirm I am authorized to run adversarial tests against this target.</span>
        </label>
        {error ? <ErrorState error={error} /> : null}
        <button className="btn btn--primary" type="submit" disabled={!confirmed || !image}>Register target</button>
      </form>
    </Drawer>
  );
}

function RuleList({ title, items }: { title: string; items: Record<string, unknown>[] }) {
  return (
    <div>
      <span className="label">{title}</span>
      {items.length === 0 ? <div className="faint">—</div> : (
        <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>
          {items.map((r, i) => {
            const { check, ...rest } = r;
            const extra = Object.keys(rest).length ? ` ${JSON.stringify(rest)}` : "";
            return <li key={i}><span className="mono">{String(check)}</span><span className="faint mono" style={{ fontSize: 11 }}>{extra}</span></li>;
          })}
        </ul>
      )}
    </div>
  );
}

function ContractView({ body }: { body: ContractBody }) {
  return (
    <div className="stack" style={{ gap: 12 }}>
      <p>{body.task}</p>
      <dl className="kv">
        <dt>Principal</dt><dd className="mono">{body.principal}</dd>
        <dt>Allowed tools</dt><dd className="mono">{body.allowed_tools.join(", ") || "—"}</dd>
        <dt>Forbidden tools</dt><dd className="mono">{body.forbidden_tools.join(", ") || "—"}</dd>
      </dl>
      <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))" }}>
        <RuleList title="Argument constraints" items={body.argument_constraints} />
        <RuleList title="Invariants" items={body.invariants} />
        <RuleList title="Data rules" items={body.data_rules} />
        <RuleList title="Reliability" items={body.reliability} />
      </div>
    </div>
  );
}

function fieldErrors(err: ApiError): string[] {
  const fields = err.details.fields;
  if (!Array.isArray(fields)) return [];
  return fields.map((f) => {
    const o = (f ?? {}) as { loc?: unknown; msg?: unknown };
    const loc = Array.isArray(o.loc) ? o.loc.join(".") : "";
    return `${loc ? `${loc}: ` : ""}${typeof o.msg === "string" ? o.msg : "invalid"}`;
  });
}

function Contracts() {
  const api = useApi();
  const toast = useToast();
  const list = useQuery(() => api.contracts(), [api]);
  const [id, setId] = useState("");
  const selected = id || list.data?.items[0]?.id || "";
  const detail = useQuery(selected ? () => api.contract(selected) : null, [api, selected]);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [result, setResult] = useState<{ ok: boolean; lines: string[] } | null>(null);

  const startEdit = () => {
    if (!detail.data) return;
    setDraft(prettyJson({ ...detail.data.body, version: detail.data.body.version + 1 }));
    setResult(null);
    setEditing(true);
  };
  const parsed = (): unknown => {
    if (draft.length > MAX_CONTRACT_CHARS) throw new Error(`Contract is larger than ${MAX_CONTRACT_CHARS} characters.`);
    return JSON.parse(draft);
  };
  const run = async (save: boolean) => {
    setResult(null);
    let body: unknown;
    try {
      body = parsed();
    } catch (err) {
      setResult({ ok: false, lines: [err instanceof Error ? err.message : "Not valid JSON."] });
      return;
    }
    try {
      if (save) {
        const c = await api.createContract(body);
        toast(`Stored ${c.name} v${c.version}`);
        setEditing(false);
        list.reload();
        setId(c.id);
      } else {
        const v = await api.validateContract(body);
        setResult({ ok: v.valid, lines: v.warnings.length ? v.warnings : ["Valid. Server checks passed: schema, tool catalogue, check ids, principal."] });
      }
    } catch (err) {
      const e = err instanceof ApiError ? err : new ApiError(0, "client_error", String(err));
      const lines = fieldErrors(e);
      setResult({ ok: false, lines: lines.length ? lines : [e.message] });
    }
  };

  return (
    <section className="panel stack" aria-labelledby="ctr-h">
      <div className="row">
        <h2 id="ctr-h" style={{ flex: 1 }}>Task contracts</h2>
        {list.data && list.data.items.length > 0 ? (
          <select className="input" style={{ width: "auto" }} value={selected} onChange={(e) => { setId(e.target.value); setEditing(false); }} aria-label="Contract">
            {list.data.items.map((c) => <option key={c.id} value={c.id}>{c.name} v{c.version}</option>)}
          </select>
        ) : null}
        {detail.data && !editing ? <button className="btn btn--sm" onClick={startEdit}>New version…</button> : null}
      </div>
      {list.error ? <ErrorState error={list.error} onRetry={list.reload} /> : null}
      {detail.error ? <ErrorState error={detail.error} onRetry={detail.reload} /> : null}
      {list.data?.items.length === 0 ? <EmptyState title="No contracts">Run <code className="mono">make seed</code> to load the bundled contract.</EmptyState> : null}
      {detail.data && !editing ? (
        <>
          <span className="faint mono" style={{ fontSize: 11 }}>sha256 {detail.data.body_sha256.slice(0, 24)}… · stored versions are immutable</span>
          <ContractView body={detail.data.body} />
        </>
      ) : null}
      {editing ? (
        <div className="stack" style={{ gap: 8 }}>
          <span className="muted" style={{ fontSize: 13 }}>Edit as JSON. Saving stores a new immutable version; the version number must be new.</span>
          <textarea className="input mono" rows={18} spellCheck={false} value={draft} onChange={(e) => setDraft(e.target.value)} aria-label="Contract JSON" style={{ fontSize: 12 }} />
          {result ? (
            <div className={result.ok ? "notice" : "error"} role="status" style={result.ok ? { borderColor: "rgba(95,212,166,.5)", background: "var(--teal-dim)" } : undefined}>
              {result.ok ? <CheckCircle2 size={15} aria-hidden /> : null}
              <ul style={{ margin: 0, paddingLeft: 18 }}>{result.lines.map((l) => <li key={l}>{l}</li>)}</ul>
            </div>
          ) : null}
          <div className="row">
            <button className="btn" onClick={() => void run(false)}>Validate</button>
            <button className="btn btn--primary" onClick={() => void run(true)}>Save new version</button>
            <button className="btn btn--ghost" onClick={() => setEditing(false)}>Cancel</button>
          </div>
        </div>
      ) : null}
    </section>
  );
}

export function Targets() {
  const api = useApi();
  const targets = useQuery(() => api.targets(), [api]);
  const [adding, setAdding] = useState(false);
  return (
    <>
      <div className="page-head">
        <div className="titles">
          <span className="eyebrow">Authorized agents</span>
          <h1>Targets</h1>
          <span className="muted">Tests run only against registered, authorized, allowlisted local images.</span>
        </div>
        <button className="btn btn--primary" onClick={() => setAdding(true)}><Plus size={15} aria-hidden /> Register target</button>
      </div>
      {targets.error ? <ErrorState error={targets.error} onRetry={targets.reload} /> : null}
      {!targets.data ? <Skeleton lines={4} /> : targets.data.items.length === 0 ? (
        <EmptyState title="No targets registered">Run <code className="mono">make seed</code> to register the bundled RefundBot, or register one here.</EmptyState>
      ) : (
        <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))" }}>
          {targets.data.items.map((t) => <TargetCard key={t.id} t={t} onChanged={targets.reload} />)}
        </div>
      )}
      <Contracts />
      <AddTarget open={adding} onClose={() => setAdding(false)} onCreated={targets.reload} />
    </>
  );
}
