// One fetch wrapper for every backend call. Requests go to /api on this origin and Next proxies
// them to FastAPI (next.config.ts), so the session cookie is first-party.

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

type Method = "GET" | "POST" | "PATCH" | "DELETE";

export async function api<T>(path: string, method: Method = "GET", body?: unknown): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method,
    credentials: "include",
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: "no-store",
  });
  const text = await res.text();
  let data: unknown = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = text;
  }
  if (!res.ok) {
    if (res.status === 401 && typeof window !== "undefined" && !window.location.pathname.startsWith("/login")) {
      // A session that expired mid-use: outside React here, so no router — a full load of the login page.
      // eslint-disable-next-line @next/next/no-location-assign-relative-destination
      window.location.assign(`/login?next=${encodeURIComponent(window.location.pathname)}`);
    }
    throw new ApiError(errorMessage(data, res.status), res.status);
  }
  return data as T;
}

function errorMessage(data: unknown, status: number): string {
  if (data && typeof data === "object") {
    const d = data as { error?: unknown; detail?: unknown };
    if (typeof d.error === "string") return d.error;
    if (Array.isArray(d.detail)) {
      return d.detail.map((e: { loc?: unknown[]; msg?: string }) => `${(e.loc ?? []).slice(1).join(".")}: ${e.msg}`).join("; ");
    }
    if (typeof d.detail === "string") return d.detail;
  }
  if (status === 502 || status === 504) return "the backend is not reachable — is it running on port 8000?";
  return `request failed (HTTP ${status})`;
}

/** SWR fetcher: the key is the path. */
export const fetcher = <T,>(path: string) => api<T>(path);

export function requestId(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID().replace(/-/g, "")
    : `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 12)}`;
}
