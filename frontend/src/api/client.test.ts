import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError, LiveTransport } from "./client";

function mockFetch(status: number, body: unknown, raw = false) {
  const fn = vi.fn(async () => new Response(raw ? (body as string) : JSON.stringify(body), { status }));
  vi.stubGlobal("fetch", fn);
  return fn;
}

afterEach(() => vi.unstubAllGlobals());

describe("LiveTransport", () => {
  it("sends the bearer token, omits cookies and targets /api/v1", async () => {
    const fn = mockFetch(200, { status: "ready", checks: { database: "ok" } });
    await api(new LiveTransport("t".repeat(40))).ready();
    const [url, init] = fn.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/v1/ready");
    expect((init.headers as Record<string, string>).Authorization).toBe(`Bearer ${"t".repeat(40)}`);
    expect(init.credentials).toBe("omit");
  });

  it("maps the API error schema to ApiError", async () => {
    mockFetch(401, { error: { code: "unauthorized", message: "Missing or invalid token.", request_id: "req_1" } });
    const err = await api(new LiveTransport("x")).targets().catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).code).toBe("unauthorized");
    expect((err as ApiError).requestId).toBe("req_1");
  });

  it("treats a 503 readiness body as a not-ready state, not an error", async () => {
    mockFetch(503, { status: "not_ready", checks: { database: "ok", docker: "unreachable", images: "missing" } });
    const r = await api(new LiveTransport("x")).ready();
    expect(r.status).toBe("not_ready");
    expect(r.checks.docker).toBe("unreachable");
  });

  it("rejects responses that do not match the schema instead of rendering them", async () => {
    mockFetch(200, { items: [{ id: 1 }] });
    const err = await api(new LiveTransport("x")).targets().catch((e: unknown) => e);
    expect((err as ApiError).code).toBe("invalid_response");
  });

  it("rejects non-JSON success bodies", async () => {
    mockFetch(200, "<html>", true);
    const err = await api(new LiveTransport("x")).settings().catch((e: unknown) => e);
    expect((err as ApiError).code).toBe("invalid_response");
  });

  it("reports network failures without leaking details", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("Failed to fetch"); }));
    const err = await api(new LiveTransport("x")).runs().catch((e: unknown) => e);
    expect((err as ApiError).code).toBe("network_error");
  });

  it("sends an Idempotency-Key when starting a run", async () => {
    const fn = mockFetch(500, { error: { code: "internal", message: "x" } });
    await api(new LiveTransport("x")).startRun({ target_id: "t", contract_id: "c", suite: "demo" }).catch(() => undefined);
    const init = (fn.mock.calls[0] as unknown as [string, RequestInit])[1];
    expect((init.headers as Record<string, string>)["Idempotency-Key"]).toMatch(/^[0-9a-f]{32}$/);
  });
});
