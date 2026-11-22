export class ApiError extends Error {
  status: number;
  detail: string;

  constructor(status: number, detail: string) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

type Init = RequestInit & { json?: unknown };

function describe(detail: unknown, fallback: string): string {
  if (typeof detail === "string" && detail) return detail;
  if (Array.isArray(detail) && detail.length > 0) {
    // FastAPI 422: a list of {loc, msg, type}.
    return detail
      .map((d) => {
        if (d && typeof d === "object" && "msg" in d) {
          const loc = Array.isArray(d.loc) ? d.loc.join(".") : "";
          return loc ? `${loc}: ${d.msg}` : String(d.msg);
        }
        return typeof d === "string" ? d : JSON.stringify(d);
      })
      .join("; ");
  }
  if (detail && typeof detail === "object") return JSON.stringify(detail);
  return fallback;
}

async function errorFor(res: Response): Promise<ApiError> {
  let detail: unknown;
  try {
    detail = (await res.json())?.detail;
  } catch {
    detail = undefined;
  }
  return new ApiError(res.status, describe(detail, res.statusText));
}

function redirectFor(path: string, err: ApiError) {
  const here = window.location.pathname;
  if (err.status === 401 && path !== "/v1/auth/login" && here !== "/login") {
    const next = encodeURIComponent(here + window.location.search);
    window.location.assign("/login?next=" + next);
  } else if (
    err.status === 403 &&
    err.detail === "password change required" &&
    here !== "/change-password"
  ) {
    window.location.assign("/change-password");
  }
}

export async function apiFetch<T>(path: string, init: Init = {}): Promise<T> {
  const { json, headers, ...rest } = init;
  const merged = new Headers(headers);
  merged.set("X-Jerome-Client", "web");
  if (json !== undefined) merged.set("Content-Type", "application/json");
  const res = await fetch(path, {
    ...rest,
    headers: merged,
    credentials: "same-origin",
    body: json !== undefined ? JSON.stringify(json) : rest.body,
  });
  if (!res.ok) {
    const err = await errorFor(res);
    redirectFor(path, err);
    throw err;
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}
