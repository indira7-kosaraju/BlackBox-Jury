import { Component, lazy, Suspense, useSyncExternalStore, type ReactNode } from "react";
import { STAGES, STAGE_LABELS, type CoreState } from "../lib/pipeline";
import { CoreFallback } from "./CoreFallback";

const Core3D = lazy(() => import("./Core3D"));

const OUTCOME_TEXT: Record<string, string> = {
  none: "No verdict yet",
  confirmed: "Vulnerability confirmed",
  inconclusive: "Inconclusive results",
  clear: "Nothing reproduced",
  failed: "Run did not complete",
};

let webgl: boolean | null = null;
function hasWebGL(): boolean {
  if (webgl === null) {
    try {
      const c = document.createElement("canvas");
      webgl = !!(c.getContext("webgl2") ?? c.getContext("webgl"));
    } catch {
      webgl = false;
    }
  }
  return webgl;
}

const motionQuery = "(prefers-reduced-motion: reduce)";
function subscribeMotion(cb: () => void): () => void {
  const mq = window.matchMedia?.(motionQuery);
  mq?.addEventListener("change", cb);
  return () => mq?.removeEventListener("change", cb);
}
function useReducedMotion(): boolean {
  return useSyncExternalStore(subscribeMotion, () => window.matchMedia?.(motionQuery).matches ?? false, () => true);
}

/** Any 3D failure (context lost, driver issue) degrades to the static SVG. */
class Fallback3D extends Component<{ fallback: ReactNode; children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  componentDidCatch(error: unknown) {
    console.warn("Evidence Core 3D disabled:", error instanceof Error ? error.message : error);
  }
  render() {
    return this.state.failed ? this.props.fallback : this.props.children;
  }
}

/** Evidence Core panel. Every visual state comes from `deriveCoreState` (real run data). */
export function EvidenceCore({ state, compact = false }: { state: CoreState; compact?: boolean }) {
  const reduced = useReducedMotion();
  const use3D = !reduced && hasWebGL();
  const flat = <CoreFallback state={state} />;
  return (
    <div className={`core ${compact ? "core--compact" : ""}`}>
      <div className="core-stage" role="img" aria-label={`Evidence Core: ${state.caption}`}>
        {use3D ? (
          <Fallback3D fallback={flat}>
            <Suspense fallback={flat}>
              <Core3D state={state} />
            </Suspense>
          </Fallback3D>
        ) : (
          flat
        )}
      </div>
      <div className="core-legend">
        <span className="label">{OUTCOME_TEXT[state.outcome]}</span>
        <strong aria-live="polite">{state.caption}</strong>
        <ul className="core-rings">
          {STAGES.map((s) => (
            <li key={s} className={`ring-${state.stages[s]}`}>
              <span className="ring-swatch" aria-hidden />
              {STAGE_LABELS[s]}
              <span className="faint mono">{state.stages[s]}</span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
