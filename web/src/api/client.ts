// HTTP layer for the AgentForge Control Plane API. One place for base URL, auth, and error mapping.
import { getConnection } from "./config";
import type { ErrorResponse, ValidationErrorResponse } from "./types";

export type ApiErrorKind =
  | "network" | "unauthorized" | "forbidden" | "not_found" | "conflict" | "validation" | "rate_limited"
  | "bad_request" | "server" | "unexpected";

export class ApiError extends Error {
  readonly kind: ApiErrorKind;
  readonly status: number | null;
  readonly code: string | null;
  readonly issues: ValidationErrorResponse["issues"];
  readonly retryAfterSeconds: number | null;

  constructor(kind: ApiErrorKind, message: string, opts: {
    status?: number | null; code?: string | null; issues?: ValidationErrorResponse["issues"];
    retryAfterSeconds?: number | null;
  } = {}) {
    super(message);
    this.name = "ApiError";
    this.kind = kind;
    this.status = opts.status ?? null;
    this.code = opts.code ?? null;
    this.issues = opts.issues ?? [];
    this.retryAfterSeconds = opts.retryAfterSeconds ?? null;
  }

  /** Worth offering a "Try again" button? (Not for validation, auth or missing resources.) */
  get retryable(): boolean {
    return this.kind === "network" || this.kind === "rate_limited" || this.kind === "server";
  }
}

// Transport is swappable: the labelled demo mode and tests install a mock; production uses fetch.
type Transport = (input: string, init: RequestInit) => Promise<Response>;
let transport: Transport = (input, init) => fetch(input, init);
export function setTransport(t: Transport): void {
  transport = t;
}

export function apiUrl(path: string, query?: Record<string, string | number | boolean | undefined | null>): string {
  const { apiBase } = getConnection();
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(query ?? {})) if (v !== undefined && v !== null && v !== "") qs.set(k, String(v));
  const s = qs.toString();
  return `${apiBase}${path}${s ? `?${s}` : ""}`;
}

export function authHeaders(): Record<string, string> {
  const { token } = getConnection();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

export async function rawRequest(path: string, init: RequestInit & {
  query?: Record<string, string | number | boolean | undefined | null>;
} = {}): Promise<Response> {
  const { query, headers, ...rest } = init;
  let res: Response;
  try {
    res = await transport(apiUrl(path, query), { ...rest, headers: { Accept: "application/json", ...authHeaders(), ...headers } });
  } catch (e) {
    if ((e as Error)?.name === "AbortError") throw e;
    throw new ApiError("network", "Cannot reach the AgentForge API. Check that the orchestrator/API server is running and the API URL in Settings is correct.");
  }
  if (!res.ok) throw await toApiError(res);
  return res;
}

export async function request<T>(method: string, path: string, opts: {
  query?: Record<string, string | number | boolean | undefined | null>;
  body?: unknown;
  headers?: Record<string, string>;
  signal?: AbortSignal;
} = {}): Promise<T> {
  const res = await rawRequest(path, {
    method,
    query: opts.query,
    signal: opts.signal,
    headers: { ...(opts.body !== undefined ? { "Content-Type": "application/json" } : {}), ...opts.headers },
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
  });
  if (res.status === 204) return undefined as T;
  try {
    return (await res.json()) as T;
  } catch {
    throw new ApiError("unexpected", `The API returned a non-JSON response for ${method} ${path}.`, { status: res.status });
  }
}

async function toApiError(res: Response): Promise<ApiError> {
  let body: Partial<ErrorResponse & ValidationErrorResponse> = {};
  try {
    body = await res.json();
  } catch {
    /* non-JSON error body */
  }
  // A 5xx without the contract's ErrorResponse shape comes from a proxy/gateway (e.g. the dev proxy when the
  // orchestrator is down), not from the AgentForge server itself.
  if (res.status >= 500 && !body.error)
    return new ApiError("network", `The AgentForge API is not responding (HTTP ${res.status} from the proxy/gateway). Start the orchestrator/API server or check the API URL in Settings.`, { status: res.status });
  const msg = body.message || res.statusText || `HTTP ${res.status}`;
  const common = { status: res.status, code: body.error ?? null };
  switch (res.status) {
    case 401:
      return new ApiError("unauthorized", "The API rejected the access token. Set a valid platform token in Settings.", common);
    case 403:
      return new ApiError("forbidden", msg, common);
    case 404:
      return new ApiError("not_found", msg, common);
    case 409:
      return new ApiError("conflict", msg, common);
    case 422:
      return new ApiError("validation", msg, { ...common, issues: body.issues ?? [] });
    case 429: {
      const ra = Number(res.headers.get("Retry-After"));
      return new ApiError("rate_limited", `${msg}${Number.isFinite(ra) && ra > 0 ? ` Retry in ${ra}s.` : ""}`,
        { ...common, retryAfterSeconds: Number.isFinite(ra) ? ra : null });
    }
    case 400:
      return new ApiError("bad_request", msg, common);
    default:
      return new ApiError(res.status >= 500 ? "server" : "unexpected", msg, common);
  }
}

/** Workspace-relative path that cannot escape the run workspace (contract guide: reject traversal). */
export function isSafeRelativePath(p: string): boolean {
  if (!p || p.length > 1024) return false;
  if (p.startsWith("/") || p.startsWith("\\") || /^[a-zA-Z]:/.test(p) || p.includes("\\") || p.includes("\0")) return false;
  return !p.split("/").some((seg) => seg === ".." || seg === ".");
}
