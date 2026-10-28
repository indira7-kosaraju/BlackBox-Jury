export function fmtTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export function fmtDuration(startIso: string | null | undefined, endIso: string | null | undefined): string {
  if (!startIso) return "—";
  const start = new Date(startIso).getTime();
  const end = endIso ? new Date(endIso).getTime() : Date.now();
  return fmtMs(end - start);
}

export function fmtMs(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || Number.isNaN(ms)) return "—";
  if (ms < 1000) return `${Math.max(0, Math.round(ms))} ms`;
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(1)} s`;
  return `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`;
}

export function shortId(id: string): string {
  return id.length > 14 ? `${id.slice(0, 4)}…${id.slice(-6)}` : id;
}

export const CATEGORY_LABEL: Record<string, string> = {
  prompt_injection: "Prompt injection",
  unauthorized_tool_call: "Unauthorized tool call",
  sensitive_data_exposure: "Sensitive-data exposure",
  wrong_arguments: "Wrong tool arguments",
  duplicate_operation: "Duplicate operation",
  false_success_claim: "False success claim",
  benign: "Benign control",
};

export const CATEGORY_GROUP: Record<string, "Security" | "Reliability" | "Control"> = {
  prompt_injection: "Security",
  unauthorized_tool_call: "Security",
  sensitive_data_exposure: "Security",
  wrong_arguments: "Reliability",
  duplicate_operation: "Reliability",
  false_success_claim: "Reliability",
  benign: "Control",
};

export const SEVERITY_RANK: Record<string, number> = { critical: 4, high: 3, medium: 2, low: 1 };

/** Harness failures (not target vulnerabilities): the test could not run cleanly. */
export const HARNESS_REASONS = new Set(["sandbox_error", "missing_evidence", "verifier_error", "timeout"]);

export function prettyJson(value: unknown): string {
  return JSON.stringify(value, null, 2);
}
