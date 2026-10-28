import { AlertTriangle, Check, CircleDashed, Copy, RotateCcw, X } from "lucide-react";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import type { ApiError } from "../api/client";
import { LAB_STEPS } from "../lib/pipeline";

/* ---------- verdict / severity / provenance (never colour alone) ---------- */

const VERDICT_TEXT: Record<string, string> = {
  CONFIRMED: "■ CONFIRMED",
  NOT_REPRODUCED: "○ NOT REPRODUCED",
  INCONCLUSIVE: "◐ INCONCLUSIVE",
};

export function VerdictBadge({ verdict, reason }: { verdict: string | null; reason?: string | null }) {
  if (!verdict) return <span className="badge badge--pending">… pending</span>;
  return (
    <span className={`badge badge--${verdict.toLowerCase()}`} title={reason ? `Reason: ${reason}` : undefined}>
      {VERDICT_TEXT[verdict] ?? verdict}
      {reason ? <span style={{ fontWeight: 500 }}>· {reason}</span> : null}
    </span>
  );
}

export function SeverityChip({ severity }: { severity: string | null | undefined }) {
  if (!severity) return <span className="faint">—</span>;
  return <span className={`sev sev--${severity}`}>{severity.toUpperCase()}</span>;
}

export function Provenance({ kind }: { kind: "verified" | "claim" | "model" | "demo" }) {
  const text = { verified: "VERIFIED", claim: "AGENT CLAIM", model: "MODEL ANALYSIS", demo: "DEMO DATA" }[kind];
  const title = {
    verified: "Decided by a deterministic check over gateway evidence",
    claim: "The target agent's own, unverified statement",
    model: "Advisory model output — cannot change a verdict",
    demo: "Recorded fixture, not live backend data",
  }[kind];
  return (
    <span className={`prov prov--${kind}`} title={title}>
      {text}
    </span>
  );
}

export function CheckOutcomeTag({ outcome }: { outcome: string }) {
  const cls = outcome === "FAIL" ? "badge--confirmed" : outcome === "ERROR" ? "badge--inconclusive" : "badge--not_reproduced";
  const sym = outcome === "FAIL" ? "✗" : outcome === "ERROR" ? "!" : "✓";
  return (
    <span className={`badge ${cls}`}>
      {sym} {outcome}
    </span>
  );
}

/* ---------- states ---------- */

export function Skeleton({ lines = 3, height = 14 }: { lines?: number; height?: number }) {
  return (
    <div className="stack" style={{ gap: 8 }} aria-busy="true" aria-label="Loading">
      {Array.from({ length: lines }, (_, i) => (
        <div key={i} className="skeleton" style={{ height, width: `${90 - i * 12}%` }} />
      ))}
    </div>
  );
}

export function EmptyState({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="empty">
      <CircleDashed size={22} aria-hidden />
      <strong style={{ color: "var(--text)" }}>{title}</strong>
      {children ? <div style={{ maxWidth: 460 }}>{children}</div> : null}
      {action}
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: ApiError; onRetry?: () => void }) {
  const hint =
    error.code === "network_error"
      ? "Is `make run` running in terminal A?"
      : error.code === "unauthorized"
        ? "Your token was rejected — sign out and paste the token from .env."
        : error.code === "sandbox_unavailable"
          ? "Docker is not reachable — start Docker Desktop. Nothing was executed."
          : null;
  return (
    <div className="error row" role="alert">
      <AlertTriangle size={18} color="var(--red)" aria-hidden />
      <div className="stack" style={{ gap: 2, flex: 1 }}>
        <strong>{error.message}</strong>
        <span className="muted mono">
          {error.code}
          {error.requestId ? ` · ${error.requestId}` : ""}
        </span>
        {hint ? <span className="muted">{hint}</span> : null}
      </div>
      {onRetry ? (
        <button className="btn btn--sm" onClick={onRetry}>
          <RotateCcw size={14} aria-hidden /> Retry
        </button>
      ) : null}
    </div>
  );
}

/* ---------- copyable id ---------- */

export function CopyId({ value, label }: { value: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  const copy = useCallback(() => {
    void navigator.clipboard?.writeText(value).then(() => {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1200);
    });
  }, [value]);
  return (
    <span className="row" style={{ gap: 4, flexWrap: "nowrap", minWidth: 0 }}>
      <code className="mono" style={{ overflow: "hidden", textOverflow: "ellipsis" }} title={value}>
        {label ?? value}
      </code>
      <button className="icon-btn" onClick={copy} aria-label={`Copy ${value}`} title="Copy">
        {copied ? <Check size={13} aria-hidden /> : <Copy size={13} aria-hidden />}
      </button>
    </span>
  );
}

/* ---------- drawer & dialog ---------- */

export function Drawer({ title, open, onClose, children }: { title: string; open: boolean; onClose: () => void; children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    ref.current?.focus();
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="overlay" onClick={onClose}>
      <aside
        className="drawer"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
        ref={ref}
        onClick={(e) => e.stopPropagation()}
      >
        <header className="row">
          <h2 style={{ flex: 1 }}>{title}</h2>
          <button className="icon-btn" onClick={onClose} aria-label="Close">
            <X size={16} aria-hidden />
          </button>
        </header>
        <div className="drawer-body">{children}</div>
      </aside>
    </div>
  );
}

export function ConfirmDialog({
  open,
  title,
  body,
  confirmLabel,
  danger,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  title: string;
  body: ReactNode;
  confirmLabel: string;
  danger?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const ref = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return;
    ref.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onCancel();
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onCancel]);
  if (!open) return null;
  return (
    <div className="overlay overlay--center" onClick={onCancel}>
      <div className="dialog panel" role="alertdialog" aria-modal="true" aria-label={title} onClick={(e) => e.stopPropagation()}>
        <h2>{title}</h2>
        <div className="muted" style={{ margin: "12px 0 20px" }}>
          {body}
        </div>
        <div className="row" style={{ justifyContent: "flex-end" }}>
          <button className="btn" onClick={onCancel}>
            Cancel
          </button>
          <button ref={ref} className={`btn ${danger ? "btn--danger" : "btn--primary"}`} onClick={onConfirm}>
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

/* ---------- toasts (only for real, completed actions) ---------- */

interface Toast {
  id: number;
  text: string;
  tone: "ok" | "error";
}
const ToastContext = createContext<(text: string, tone?: "ok" | "error") => void>(() => undefined);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const push = useCallback((text: string, tone: "ok" | "error" = "ok") => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, text, tone }]);
    window.setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 4500);
  }, []);
  return (
    <ToastContext.Provider value={push}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={`toast toast--${t.tone}`}>
            {t.text}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}
export const useToast = () => useContext(ToastContext);

/* ---------- five-step lab track ---------- */

export function StageTrack({ step, outcome }: { step: number; outcome: string }) {
  return (
    <ol className="track" aria-label="Pipeline progress">
      {LAB_STEPS.map((label, i) => {
        const state = step < 0 ? (outcome !== "none" ? "done" : "idle") : i < step ? "done" : i === step ? "active" : "idle";
        return (
          <li key={label} className={`track-step track-step--${state}`} aria-current={state === "active" ? "step" : undefined}>
            <span className="track-dot" aria-hidden />
            <span>{label}</span>
          </li>
        );
      })}
    </ol>
  );
}

/* ---------- demo replay label ---------- */

/** Runs started in demo mode replay a recorded run on a timer; say so wherever one is shown. */
export function ReplayBadge({ runId }: { runId: string }) {
  if (!runId.startsWith("replay_")) return null;
  const source = runId.slice("replay_".length).replace(/_[a-z0-9]+$/, "");
  return (
    <span className="prov prov--demo" title={`Timed replay of recorded run ${source}. Nothing is executing.`}>
      SIMULATED REPLAY
    </span>
  );
}

/* ---------- brand ---------- */

export function BrandMark({ size = 30 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden>
      <path d="M16 3 27 9.5v13L16 29 5 22.5v-13z" fill="none" stroke="var(--cyan)" strokeWidth="1.6" />
      <path d="M16 10 21.5 13.2v5.6L16 22l-5.5-3.2v-5.6z" fill="rgba(66,232,245,.18)" stroke="var(--violet)" strokeWidth="1.3" />
      <circle cx="16" cy="16" r="1.8" fill="var(--cyan)" />
    </svg>
  );
}
