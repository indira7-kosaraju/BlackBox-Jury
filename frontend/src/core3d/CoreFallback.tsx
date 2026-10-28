import { STAGES, STAGE_LABELS, type CoreState } from "../lib/pipeline";

/**
 * Static SVG Evidence Core: used with reduced motion, without WebGL, and
 * while the 3D scene loads. Five rings, one per pipeline stage.
 */
export const RING_COLOR: Record<string, string> = {
  idle: "rgba(140,155,181,0.28)",
  active: "var(--cyan)",
  done: "var(--blue)",
  skipped: "rgba(140,155,181,0.14)",
};

export const OUTCOME_COLOR: Record<string, string> = {
  none: "var(--text-3)",
  confirmed: "var(--red)",
  inconclusive: "var(--amber)",
  clear: "var(--teal)",
  failed: "var(--amber)",
};

export function CoreFallback({ state }: { state: CoreState }) {
  return (
    <svg viewBox="0 0 240 240" className="core-svg" aria-hidden>
      {STAGES.map((stage, i) => {
        const r = 104 - i * 17;
        const s = state.stages[stage];
        return (
          <circle
            key={stage}
            cx="120"
            cy="120"
            r={r}
            fill="none"
            stroke={RING_COLOR[s]}
            strokeWidth={s === "active" ? 3 : 1.6}
            strokeDasharray={s === "skipped" ? "3 6" : s === "idle" ? "2 4" : undefined}
          >
            <title>{`${STAGE_LABELS[stage]}: ${s}`}</title>
          </circle>
        );
      })}
      <circle cx="120" cy="120" r="22" fill={OUTCOME_COLOR[state.outcome]} opacity="0.22" />
      <circle cx="120" cy="120" r="12" fill={OUTCOME_COLOR[state.outcome]} />
    </svg>
  );
}
