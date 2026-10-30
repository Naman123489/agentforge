"""Contract -> TypeScript: types and a typed API client for the generated frontend.

The Frontend agent's model never writes fetch() calls or API shapes; it imports this generated client, so paths,
methods, field names and types follow the per-run contract by construction (tsc enforces the payloads).
"""

from __future__ import annotations

import hashlib
import re

from .contract import Contract, Endpoint, FieldSpec

GENERATED_PATH = "frontend/src/api/generated.ts"
HEADER = "// GENERATED from contract/openapi.yaml by AgentForge. Do not edit: regenerate from the contract instead.\n"

_TS = {"string": "string", "integer": "number", "number": "number", "boolean": "boolean",
       "array": "unknown[]", "object": "Record<string, unknown>"}


def _pascal(s: str) -> str:
    return "".join(p[:1].upper() + p[1:] for p in re.split(r"[^A-Za-z0-9]+", s) if p)


def op_name(e: Endpoint) -> str:
    """POST /api/login -> postLogin; GET /api/leaderboard/{game_id} -> getLeaderboardByGameId."""
    segs = [s for s in e.path.split("/") if s and s != "api"]
    words = [_pascal(s) for s in segs if not s.startswith("{")]
    params = [_pascal(s.strip("{}")) for s in segs if s.startswith("{")]
    return e.method.lower() + "".join(words) + ("By" + "And".join(params) if params else "")


def _camel(s: str) -> str:
    p = _pascal(s)
    return p[:1].lower() + p[1:] if p else "param"


def _ts_type(f: FieldSpec) -> str:
    if f.enum:
        return " | ".join(f'"{v}"' if isinstance(v, str) else str(v).lower() if isinstance(v, bool) else str(v) for v in f.enum)
    return _TS.get(f.type, "unknown")


def _iface(name: str, fields: list[FieldSpec]) -> str:
    if not fields:
        return f"export type {name} = Record<string, never>;\n"
    lines = [f"  {f.name if re.fullmatch(r'[A-Za-z_$][A-Za-z0-9_$]*', f.name) else repr(f.name)}{'' if f.required else '?'}: {_ts_type(f)};"
             for f in fields]
    return f"export interface {name} {{\n" + "\n".join(lines) + "\n}\n"


def unique_names(c: Contract) -> list[tuple[str, Endpoint]]:
    seen: dict[str, int] = {}
    out = []
    for e in c.endpoints:
        n = op_name(e)
        seen[n] = seen.get(n, 0) + 1
        out.append((n if seen[n] == 1 else f"{n}{seen[n]}", e))
    return out


def generate_client(c: Contract) -> str:
    parts = [HEADER, CORE]
    api_lines, meta = [], []
    for name, e in unique_names(c):
        P = name[:1].upper() + name[1:]
        if e.request:
            parts.append(_iface(f"{P}Request", e.request))
        if e.query:
            parts.append(_iface(f"{P}Query", e.query))
        if e.response:
            parts.append(_iface(f"{P}{'Item' if e.response_is_list else 'Response'}", e.response))
            if e.response_is_list:
                parts.append(f"export type {P}Response = {P}Item[];\n")
        else:
            parts.append(f"export type {P}Response = unknown;\n")
        args = [f"{_camel(p.name)}: {_ts_type(p)}" for p in e.path_params]
        if e.request:
            args.append(f"body: {P}Request")
        if e.query:
            optional = all(not q.required for q in e.query)
            args.append(f"query{'?' if optional else ''}: {P}Query")
        path = e.path
        for p in e.path_params:
            path = path.replace("{" + p.name + "}", "${encodeURIComponent(String(" + _camel(p.name) + "))}")
        opts = ", ".join(x for x in (
            "body" if e.request else "", "query" if e.query else "",
            f"auth: {'true' if e.auth else 'false'}") if x)
        doc = f"  /** {e.method} {e.path}{' (requires login)' if e.auth else ''}{' - ' + e.summary if e.summary else ''} */\n"
        api_lines.append(f"{doc}  {name}: ({', '.join(args)}): Promise<{P}Response> =>\n"
                         f"    request<{P}Response>(\"{e.method}\", `{path}`, {{ {opts} }}),")
        meta.append(f'  {{ name: "{name}", method: "{e.method}", path: "{e.path}", auth: {"true" if e.auth else "false"} }},')
    parts.append("\nexport const api = {\n" + "\n".join(api_lines) + "\n};\n")
    parts.append("\n/** Every contract operation (used by AgentForge checks). */\nexport const endpoints = [\n" + "\n".join(meta) + "\n] as const;\n")
    return "\n".join(parts)


def client_digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def client_summary(c: Contract) -> str:
    """Signatures for the prompt (cheaper than the whole generated file)."""
    out = []
    for name, e in unique_names(c):
        P = name[:1].upper() + name[1:]
        args = [_camel(p.name) for p in e.path_params] + (["body"] if e.request else []) + (["query"] if e.query else [])
        out.append(f"api.{name}({', '.join(args)}) -> {P}Response   // {e.method} {e.path}{' [needs login]' if e.auth else ''}")
    return "\n".join(out)


CORE = r'''
const BASE_URL: string = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "";
const TOKEN_KEY = "app.token";

// Storage can be unavailable (sandboxed preview iframes, private mode): fall back to memory.
let memoryToken: string | null = null;
export function getToken(): string | null {
  try { return localStorage.getItem(TOKEN_KEY) ?? memoryToken; } catch { return memoryToken; }
}
export function setToken(token: string | null): void {
  memoryToken = token;
  try { if (token) localStorage.setItem(TOKEN_KEY, token); else localStorage.removeItem(TOKEN_KEY); } catch { /* memory only */ }
}

/** Error from the API: `status` is the HTTP status (0 = network failure), `message` is the server's detail. */
export class ApiError extends Error {
  readonly status: number;
  readonly detail: unknown;
  constructor(status: number, message: string, detail?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

function detailMessage(detail: unknown, status: number): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((d) => (d && typeof d === "object" && "msg" in d ? String((d as { msg: unknown }).msg) : String(d))).join("; ");
  return `Request failed (HTTP ${status})`;
}

async function request<T>(method: string, path: string, opts: { body?: unknown; query?: object; auth: boolean }): Promise<T> {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(opts.query ?? {}) as [string, unknown][]) if (v !== undefined && v !== null && v !== "") qs.set(k, String(v));
  const headers: Record<string, string> = { Accept: "application/json" };
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  const token = getToken();
  if (opts.auth && token) headers.Authorization = `Bearer ${token}`;
  let res: Response;
  try {
    res = await fetch(`${BASE_URL}${path}${qs.toString() ? `?${qs}` : ""}`, {
      method, headers, body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
    });
  } catch {
    throw new ApiError(0, "Cannot reach the server. Check your connection and try again.");
  }
  const text = await res.text();
  let data: unknown = undefined;
  try { data = text ? JSON.parse(text) : undefined; } catch { data = text; }
  if (!res.ok) {
    const detail = data && typeof data === "object" && "detail" in data ? (data as { detail: unknown }).detail : data;
    if (res.status === 401) setToken(null);
    throw new ApiError(res.status, detailMessage(detail, res.status), detail);
  }
  return data as T;
}
'''
