import {
  FlaskConical,
  Gavel,
  LayoutDashboard,
  ListChecks,
  LogOut,
  Menu,
  RotateCcw,
  ScanSearch,
  Settings,
  Target,
} from "lucide-react";
import { useEffect, useState } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import { BrandMark } from "../components/ui";
import { isActiveRun } from "../lib/pipeline";
import { useQuery } from "../lib/useQuery";
import { useApi, useSession } from "./session";

export const NAV = [
  { to: "/", label: "Overview", icon: LayoutDashboard, end: true },
  { to: "/targets", label: "Targets", icon: Target },
  { to: "/lab", label: "Test Lab", icon: FlaskConical },
  { to: "/runs", label: "Test Runs", icon: ListChecks },
  { to: "/evidence", label: "Evidence Explorer", icon: ScanSearch },
  { to: "/findings", label: "Jury Findings", icon: Gavel },
  { to: "/regressions", label: "Regression Tests", icon: RotateCcw },
  { to: "/settings", label: "Settings", icon: Settings },
] as const;

function titleFor(pathname: string): string {
  const hit = [...NAV].reverse().find((n) => (n.to === "/" ? pathname === "/" : pathname.startsWith(n.to)));
  return hit?.label ?? "Blackbox Jury";
}

export function Shell() {
  const api = useApi();
  const { mode, signOut } = useSession();
  const location = useLocation();
  const [open, setOpen] = useState(false);
  const ready = useQuery(() => api.ready(), [api], { pollMs: 15_000 });
  const settings = useQuery(() => api.settings(), [api]);
  const latest = useQuery(() => api.runs(1), [api], { pollMs: 5000 });
  const activeRun = latest.data?.items.find(isActiveRun);

  useEffect(() => setOpen(false), [location.pathname]);

  const sandboxOk = ready.data?.status === "ready";
  const sandboxLabel = ready.error
    ? "Server unreachable"
    : !ready.data
      ? "Checking…"
      : sandboxOk
        ? "Sandbox ready"
        : `Not ready: ${Object.entries(ready.data.checks).filter(([, v]) => v !== "ok").map(([k, v]) => `${k} ${v}`).join(", ")}`;
  const title = titleFor(location.pathname);

  return (
    <div className="app">
      <a className="visually-hidden" href="#main">
        Skip to content
      </a>
      <aside className={`sidebar ${open ? "open" : ""}`} aria-label="Primary">
        <NavLink to="/" className="brand" aria-label="Blackbox Jury overview">
          <BrandMark size={34} />
          <span className="stack" style={{ gap: 2 }}>
            <span className="brand-name">BLACKBOX JURY</span>
            <span className="brand-team">NULL VERDICT</span>
          </span>
        </NavLink>
        <nav className="nav">
          {NAV.map(({ to, label, icon: Icon, ...rest }) => (
            <NavLink key={to} to={to} end={"end" in rest ? rest.end : false}>
              <Icon size={17} aria-hidden />
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="side-foot">
          <span className="row" style={{ gap: 8 }}>
            <span className={`status-dot ${ready.error ? "status-dot--bad" : sandboxOk ? "status-dot--ok" : "status-dot--warn"}`} aria-hidden />
            <span>{mode === "demo" ? "Demo mode — no sandbox" : sandboxLabel}</span>
          </span>
          <span className="muted mono">LOCAL · {settings.data?.app.env ?? "—"}</span>
          <span className="faint mono">v{settings.data?.app.version ?? "—"} · UI 0.1.0</span>
          <button className="btn btn--ghost btn--sm" onClick={signOut} style={{ alignSelf: "flex-start", paddingLeft: 0 }}>
            <LogOut size={14} aria-hidden /> {mode === "demo" ? "Exit demo" : "Sign out"}
          </button>
        </div>
      </aside>
      <div className="main">
        {mode === "demo" ? (
          <div className="demo-banner" role="note">
            DEMO DATA — recorded from a real sandbox run. Nothing on these screens is live.
          </div>
        ) : null}
        <header className="topbar">
          <button className="icon-btn menu-btn" onClick={() => setOpen((o) => !o)} aria-label="Toggle navigation" aria-expanded={open}>
            <Menu size={18} aria-hidden />
          </button>
          <div className="crumbs">
            <span className="hide-sm">Blackbox Jury</span>
            <span className="hide-sm faint">/</span>
            <strong>{title}</strong>
          </div>
          <span className="spacer" />
          {activeRun ? (
            <Link to={`/lab?run=${activeRun.id}`} className="row" style={{ gap: 6, fontSize: 13 }}>
              <span className="status-dot status-dot--ok" aria-hidden />
              Run {activeRun.status} · {activeRun.totals.executed}/{activeRun.totals.selected}
            </Link>
          ) : null}
          <span className="badge badge--cyan">{mode === "demo" ? "DEMO · RECORDED" : "LOCAL SANDBOX"}</span>
        </header>
        <main id="main" className="content">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
