/**
 * Typed API service layer. All network access goes through a Transport:
 * - LiveTransport talks to the real backend (same origin, /api/v1, bearer token);
 * - DemoTransport (src/demo) answers from a recorded fixture, clearly labelled.
 * Responses are validated with the schemas in ./schemas.
 */
import type * as z from "zod";
import * as S from "./schemas";

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly requestId: string | null;
  readonly details: Record<string, unknown>;
  /** Parsed JSON body of a failed response, when there was one (untrusted). */
  readonly body: unknown;

  constructor(status: number, code: string, message: string, requestId: string | null = null,
    details: Record<string, unknown> = {}, body: unknown = null) {
    super(message);
    this.body = body;
    this.status = status;
    this.code = code;
    this.requestId = requestId;
    this.details = details;
  }
}

export interface Transport {
  readonly mode: "live" | "demo";
  request(method: string, path: string, body?: unknown, headers?: Record<string, string>): Promise<unknown>;
  /** Raw file download (reports, regression JSON). */
  download(path: string): Promise<Blob>;
}

const TIMEOUT_MS = 15_000;

export class LiveTransport implements Transport {
  readonly mode = "live" as const;
  private readonly token: string;

  constructor(token: string) {
    this.token = token;
  }

  private async send(method: string, path: string, body?: unknown, headers: Record<string, string> = {}): Promise<Response> {
    try {
      return await fetch(`/api/v1${path}`, {
        method,
        headers: {
          Authorization: `Bearer ${this.token}`,
          ...(body === undefined ? {} : { "Content-Type": "application/json" }),
          ...headers,
        },
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: AbortSignal.timeout(TIMEOUT_MS),
        credentials: "omit",
        cache: "no-store",
      });
    } catch (err) {
      const timedOut = err instanceof DOMException && err.name === "TimeoutError";
      throw new ApiError(0, timedOut ? "timeout" : "network_error",
        timedOut ? "The server did not respond in time." : "Cannot reach the Blackbox Jury server.");
    }
  }

  async download(path: string): Promise<Blob> {
    const response = await this.send("GET", path);
    if (!response.ok) await this.fail(response, await response.text());
    return response.blob();
  }

  private async fail(response: Response, text: string): Promise<never> {
    let payload: unknown = null;
    try {
      payload = text ? JSON.parse(text) : null;
    } catch {
      /* non-JSON error body */
    }
    const parsed = S.ErrorBody.safeParse(payload);
    if (parsed.success) {
      const e = parsed.data.error;
      throw new ApiError(response.status, e.code, e.message, e.request_id ?? null, e.details ?? {});
    }
    throw new ApiError(response.status, "http_error", `Request failed (${response.status}).`, null, {}, payload);
  }

  async request(method: string, path: string, body?: unknown, headers: Record<string, string> = {}): Promise<unknown> {
    const response = await this.send(method, path, body, headers);
    const text = await response.text();
    if (!response.ok) await this.fail(response, text);
    let payload: unknown = null;
    if (text) {
      try {
        payload = JSON.parse(text);
      } catch {
        throw new ApiError(response.status, "invalid_response", "The server returned a non-JSON response.");
      }
    }
    return payload;
  }
}

export function parse<T extends z.ZodType>(schema: T, data: unknown, what: string): z.infer<T> {
  const result = schema.safeParse(data);
  if (!result.success) {
    throw new ApiError(0, "invalid_response", `Unexpected ${what} response from the server.`);
  }
  return result.data;
}

function key(): string {
  return crypto.randomUUID().replaceAll("-", "");
}

/** Endpoint functions. Each validates its response. */
export function api(t: Transport) {
  const get = async <T extends z.ZodType>(path: string, schema: T, what: string) =>
    parse(schema, await t.request("GET", path), what);
  const enc = encodeURIComponent;
  return {
    mode: t.mode,
    // /ready answers 503 with a readiness body when a dependency is down: that is a state, not an error.
    ready: async () => {
      try {
        return await get("/ready", S.Ready, "readiness");
      } catch (err) {
        if (err instanceof ApiError && err.status === 503 && S.Ready.safeParse(err.body).success) {
          return S.Ready.parse(err.body);
        }
        throw err;
      }
    },
    settings: () => get("/settings", S.Settings, "settings"),
    targets: () => get("/targets", S.TargetList, "targets"),
    contracts: () => get("/contracts", S.ContractList, "contracts"),
    contract: (id: string) => get(`/contracts/${enc(id)}`, S.ContractDetail, "contract"),
    scenarios: () => get("/scenarios?include=body", S.ScenarioList, "scenarios"),
    runs: (limit = 25) => get(`/runs?limit=${limit}`, S.RunList, "runs"),
    run: (id: string) => get(`/runs/${enc(id)}`, S.Run, "run"),
    executions: (runId: string) => get(`/runs/${enc(runId)}/executions?limit=100`, S.ExecutionList, "executions"),
    execution: (id: string) => get(`/executions/${enc(id)}`, S.ExecutionDetail, "execution"),
    artifact: (id: string) => get(`/artifacts/${enc(id)}`, S.Artifact, "artifact"),
    findings: () => get("/findings?limit=200", S.FindingList, "findings"),
    finding: (id: string) => get(`/findings/${enc(id)}`, S.FindingDetail, "finding"),
    regressionTests: () => get("/regression-tests", S.RegressionList, "regression tests"),
    auditEvents: (limit = 50) => get(`/audit-events?limit=${limit}`, S.AuditList, "activity"),
    reportFile: (runId: string, format: "json" | "html") => t.download(`/runs/${enc(runId)}/report?format=${format}`),
    regressionFile: (id: string) => t.download(`/regression-tests/${enc(id)}?download=true`),
    validateContract: async (body: unknown) =>
      parse(S.ContractValidation, await t.request("POST", "/contracts/validate", body), "validation"),
    createContract: async (body: unknown) =>
      parse(S.ContractSummary, await t.request("POST", "/contracts", body), "contract"),
    createTarget: async (body: {
      name: string;
      config: { image: string; mode: "vulnerable" | "hardened" };
      authorization: { confirm_authorized: boolean; statement: string };
    }) => parse(S.Target, await t.request("POST", "/targets", { adapter_type: "sandboxed_container", ...body }), "target"),
    startRun: async (body: { target_id: string; contract_id: string; scenario_ids?: string[]; suite?: string }) =>
      parse(S.Run, await t.request("POST", "/runs", body, { "Idempotency-Key": key() }), "run"),
    cancelRun: (id: string) => t.request("POST", `/runs/${enc(id)}/cancel`),
    setTargetMode: async (id: string, mode: "vulnerable" | "hardened") =>
      parse(S.Target, await t.request("PATCH", `/targets/${enc(id)}`, { config: { mode } }), "target"),
    exportRegression: async (findingId: string) =>
      parse(S.RegressionExport, await t.request("POST", `/findings/${enc(findingId)}/regression-test`), "export"),
    startRetest: async (regressionTestId: string, targetId: string) =>
      parse(S.Run, await t.request("POST", "/retests", { regression_test_id: regressionTestId, target_id: targetId },
        { "Idempotency-Key": key() }), "retest"),
  };
}
export type Api = ReturnType<typeof api>;
