// Thin fetch wrapper. Every state-changing request carries the X-CAT-Client header, which the
// backend requires so that no other web page can trigger an action on this local server.

export class ApiError extends Error {
  status: number;
  code: string;
  details: Record<string, unknown>;

  constructor(status: number, code: string, message: string, details: Record<string, unknown> = {}) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

type Method = "GET" | "POST" | "PUT" | "DELETE";

export async function api<T>(path: string, options: { method?: Method; body?: unknown; signal?: AbortSignal } = {}): Promise<T> {
  const method = options.method ?? "GET";
  const headers: Record<string, string> = { Accept: "application/json" };
  let body: BodyInit | undefined;
  if (options.body instanceof FormData) {
    body = options.body;
  } else if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(options.body);
  }
  if (method !== "GET") headers["X-CAT-Client"] = "web";
  let response: Response;
  try {
    response = await fetch(path, { method, headers, body, signal: options.signal });
  } catch (err) {
    if ((err as Error).name === "AbortError") throw err;
    throw new ApiError(0, "backend_unreachable", "The backend is not reachable. Is `python -m app serve` running?");
  }
  const text = await response.text();
  let data: unknown = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = { message: text };
    }
  }
  if (!response.ok) {
    const d = (data ?? {}) as { error?: string; message?: string; details?: Record<string, unknown> };
    throw new ApiError(response.status, d.error ?? "http_error", d.message ?? `Request failed (${response.status})`, d.details ?? {});
  }
  return data as T;
}

export const get = <T,>(path: string, signal?: AbortSignal) => api<T>(path, { signal });
export const post = <T,>(path: string, body?: unknown) => api<T>(path, { method: "POST", body: body ?? {} });
export const put = <T,>(path: string, body?: unknown) => api<T>(path, { method: "PUT", body });
export const del = <T,>(path: string) => api<T>(path, { method: "DELETE" });

export function qs(params: Record<string, string | number | boolean | undefined | null>): string {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === "") continue;
    sp.set(k, String(v));
  }
  const s = sp.toString();
  return s ? `?${s}` : "";
}

export function errorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    const errors = (err.details?.errors as { loc: string; msg: string }[] | undefined) ?? [];
    if (errors.length) return `${err.message}: ${errors.map((e) => `${e.loc} ${e.msg}`).join("; ")}`;
    return err.message;
  }
  if (err instanceof Error) return err.message;
  return String(err);
}
