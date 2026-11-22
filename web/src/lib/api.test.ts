import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, apiFetch, apiFetchPage } from "./api";

function reply(status: number, body?: unknown, statusText = "") {
  const res = new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    statusText,
  });
  return vi.fn().mockResolvedValue(res);
}

function raw(status: number, text: string, statusText: string) {
  return vi.fn().mockResolvedValue(new Response(text, { status, statusText }));
}

async function failure(call: Promise<unknown>): Promise<ApiError> {
  try {
    await call;
  } catch (e) {
    return e as ApiError;
  }
  throw new Error("expected apiFetch to throw");
}

const assign = vi.fn();
const original = window.location;

function at(pathname: string, search = "") {
  Object.defineProperty(window, "location", {
    configurable: true,
    value: { pathname, search, assign },
  });
}

beforeEach(() => {
  assign.mockClear();
  at("/projects/3", "?tab=2");
});

afterEach(() => {
  vi.unstubAllGlobals();
  Object.defineProperty(window, "location", { configurable: true, value: original });
});

describe("apiFetch", () => {
  it("sends the client header and same-origin credentials", async () => {
    const fetchMock = reply(200, { ok: true });
    vi.stubGlobal("fetch", fetchMock);
    await expect(apiFetch("/v1/auth/me")).resolves.toEqual({ ok: true });
    const [path, init] = fetchMock.mock.calls[0];
    expect(path).toBe("/v1/auth/me");
    expect(init.credentials).toBe("same-origin");
    expect(new Headers(init.headers).get("X-Jerome-Client")).toBe("web");
  });

  it("serialises json bodies and sets the content type", async () => {
    const fetchMock = reply(200, {});
    vi.stubGlobal("fetch", fetchMock);
    await apiFetch("/v1/x", { method: "POST", json: { a: 1 } });
    const init = fetchMock.mock.calls[0][1];
    expect(init.method).toBe("POST");
    expect(init.body).toBe('{"a":1}');
    expect(new Headers(init.headers).get("Content-Type")).toBe("application/json");
  });

  it("returns undefined for 204", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 204 })));
    await expect(apiFetch("/v1/x", { method: "DELETE" })).resolves.toBeUndefined();
  });

  it("throws ApiError carrying the API detail", async () => {
    vi.stubGlobal("fetch", reply(409, { detail: "already decided" }));
    const err = await failure(apiFetch("/v1/x"));
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(409);
    expect(err.detail).toBe("already decided");
    expect(err.message).toBe("already decided");
    expect(assign).not.toHaveBeenCalled();
  });

  it("falls back to the status text when the body is not JSON", async () => {
    vi.stubGlobal("fetch", raw(502, "<html>bad gateway</html>", "Bad Gateway"));
    const err = await failure(apiFetch("/v1/x"));
    expect(err.status).toBe(502);
    expect(err.detail).toBe("Bad Gateway");
  });

  it("falls back to the status text when the JSON has no detail", async () => {
    vi.stubGlobal("fetch", reply(500, { error: "x" }, "Server Error"));
    const err = await failure(apiFetch("/v1/x"));
    expect(err.detail).toBe("Server Error");
  });

  it("turns a 422 detail list into readable text", async () => {
    const detail = [
      { loc: ["body", "email"], msg: "field required", type: "missing" },
      { loc: ["body", "name"], msg: "too short", type: "string_too_short" },
    ];
    vi.stubGlobal("fetch", reply(422, { detail }));
    const err = await failure(apiFetch("/v1/x", { method: "POST", json: {} }));
    expect(err.detail).toBe("body.email: field required; body.name: too short");
  });

  it("on 401 goes to /login with the current page as next, and still throws", async () => {
    vi.stubGlobal("fetch", reply(401, { detail: "Not authenticated" }));
    const err = await failure(apiFetch("/v1/projects"));
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(401);
    expect(assign).toHaveBeenCalledWith("/login?next=" + encodeURIComponent("/projects/3?tab=2"));
  });

  it("does not redirect a 401 from the login request, so the form can show it", async () => {
    vi.stubGlobal("fetch", reply(401, { detail: "Invalid email or password" }));
    const err = await failure(apiFetch("/v1/auth/login", { method: "POST", json: {} }));
    expect(err.status).toBe(401);
    expect(err.detail).toBe("Invalid email or password");
    expect(assign).not.toHaveBeenCalled();
  });

  it("does not redirect a 401 when already on /login", async () => {
    at("/login");
    vi.stubGlobal("fetch", reply(401, { detail: "Not authenticated" }));
    await apiFetch("/v1/auth/me").catch(() => undefined);
    expect(assign).not.toHaveBeenCalled();
  });

  it("on 403 password change required goes to /change-password", async () => {
    vi.stubGlobal("fetch", reply(403, { detail: "password change required" }));
    const err = await failure(apiFetch("/v1/projects"));
    expect(err.status).toBe(403);
    expect(assign).toHaveBeenCalledWith("/change-password");
  });

  it("does not redirect that 403 when already on /change-password", async () => {
    at("/change-password");
    vi.stubGlobal("fetch", reply(403, { detail: "password change required" }));
    await apiFetch("/v1/projects").catch(() => undefined);
    expect(assign).not.toHaveBeenCalled();
  });

  it("does not redirect other 403s", async () => {
    vi.stubGlobal("fetch", reply(403, { detail: "admin only" }));
    const err = await failure(apiFetch("/v1/users"));
    expect(err.detail).toBe("admin only");
    expect(assign).not.toHaveBeenCalled();
  });

  it("parses Retry-After seconds into retryAfter", async () => {
    const res = new Response(JSON.stringify({ detail: "slow down" }), {
      status: 429,
      headers: { "Retry-After": "900" },
    });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(res));
    const err = await failure(apiFetch("/v1/auth/login", { method: "POST", json: {} }));
    expect(err.status).toBe(429);
    expect(err.retryAfter).toBe(900);
  });

  it("leaves retryAfter undefined when the header is missing or not a number", async () => {
    vi.stubGlobal("fetch", reply(429, { detail: "slow down" }));
    expect((await failure(apiFetch("/v1/x"))).retryAfter).toBeUndefined();
    const res = new Response(null, { status: 429, headers: { "Retry-After": "soon" } });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(res));
    expect((await failure(apiFetch("/v1/x"))).retryAfter).toBeUndefined();
  });
});

describe("apiFetchPage", () => {
  it("returns the body and the X-Total-Count total", async () => {
    const res = new Response(JSON.stringify([{ id: 1 }]), {
      status: 200,
      headers: { "X-Total-Count": "42" },
    });
    const fetchMock = vi.fn().mockResolvedValue(res);
    vi.stubGlobal("fetch", fetchMock);
    await expect(apiFetchPage("/v1/books/1/suggestions?limit=1")).resolves.toEqual({
      data: [{ id: 1 }],
      total: 42,
    });
    const [path, init] = fetchMock.mock.calls[0];
    expect(path).toBe("/v1/books/1/suggestions?limit=1");
    expect(init.credentials).toBe("same-origin");
    expect(new Headers(init.headers).get("X-Jerome-Client")).toBe("web");
  });

  it("falls back to the list length when the header is missing or not a number", async () => {
    vi.stubGlobal("fetch", reply(200, [{ id: 1 }, { id: 2 }]));
    await expect(apiFetchPage("/v1/x")).resolves.toEqual({ data: [{ id: 1 }, { id: 2 }], total: 2 });
    const res = new Response(JSON.stringify([]), { status: 200, headers: { "X-Total-Count": "lots" } });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(res));
    await expect(apiFetchPage("/v1/x")).resolves.toEqual({ data: [], total: 0 });
  });

  it("throws ApiError and redirects like apiFetch", async () => {
    vi.stubGlobal("fetch", reply(409, { detail: "already decided" }));
    const err = await failure(apiFetchPage("/v1/x"));
    expect(err).toBeInstanceOf(ApiError);
    expect(err.detail).toBe("already decided");
    vi.stubGlobal("fetch", reply(401, { detail: "Not authenticated" }));
    await failure(apiFetchPage("/v1/projects"));
    expect(assign).toHaveBeenCalledWith("/login?next=" + encodeURIComponent("/projects/3?tab=2"));
  });
});
