import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { KeyRound, PlugZap, ShieldAlert } from "lucide-react";
import { api } from "../api/endpoints";
import { DEMO_MODE, setConnection, useConnection } from "../api/config";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Panel } from "../components/ui/Panel";
import { ErrorState } from "../components/ui/States";
import { useToast } from "../components/ui/Toast";

export default function SettingsPage() {
  const conn = useConnection();
  const [apiBase, setApiBase] = useState(conn.apiBase);
  const [token, setToken] = useState(conn.token);
  const [test, setTest] = useState<{ ok: boolean; text: string } | { error: unknown } | null>(null);
  const [testing, setTesting] = useState(false);
  const qc = useQueryClient();
  const toast = useToast();
  const looksLikeProviderKey = /^v1\.|^sk-|nebius/i.test(token.trim());

  const save = (e: React.FormEvent) => {
    e.preventDefault();
    setConnection({ apiBase: apiBase.trim() || "/api/v1", token: token.trim() });
    void qc.invalidateQueries();
    toast("ok", "Connection settings saved for this browser tab.");
  };
  const runTest = async () => {
    setTesting(true);
    setTest(null);
    try {
      const h = await api.getHealth();
      setTest({ ok: h.status === "ok", text: `API ${h.status}, version ${h.version}` });
    } catch (e) {
      setTest({ error: e });
    } finally {
      setTesting(false);
    }
  };

  return (
    <div className="mx-auto max-w-3xl space-y-4 p-4 sm:p-6">
      <h1 className="text-xl font-semibold tracking-tight">Settings</h1>
      {DEMO_MODE && <p className="rounded-lg border border-amber-400/30 bg-amber-400/5 px-3 py-2 text-sm text-amber-900">Demo mode is on (built with <code>--mode demo</code>). Requests go to an in-browser mock, not a server.</p>}
      <Panel title="Connection to the AgentForge API" id="conn">
        <form onSubmit={save} className="space-y-4">
          <label className="block text-sm">
            <span className="text-muted">API base URL</span>
            <input value={apiBase} onChange={(e) => setApiBase(e.target.value)} placeholder="/api/v1"
              className="mt-1 h-9 w-full rounded-lg border border-line bg-ink-900 px-3 font-mono text-sm text-fg" />
            <span className="mt-1 block text-xs text-subtle">The Control Plane API (contract prefix <code>/api/v1</code>). In development, Vite proxies <code>/api</code> to the orchestrator.</span>
          </label>
          <label className="block text-sm">
            <span className="flex items-center gap-1.5 text-muted"><KeyRound className="size-3.5" />Platform access token</span>
            <input type="password" autoComplete="off" value={token} onChange={(e) => setToken(e.target.value)} placeholder="Bearer token issued by the AgentForge server"
              className="mt-1 h-9 w-full rounded-lg border border-line bg-ink-900 px-3 font-mono text-sm text-fg" />
            <span className="mt-1 block text-xs text-subtle">Stored in this tab's sessionStorage only and sent as <code>Authorization: Bearer</code>. Never paste model-provider keys (e.g. Nebius) here.</span>
          </label>
          {looksLikeProviderKey && (
            <p role="alert" className="flex items-start gap-2 rounded-lg border border-rose-400/30 bg-rose-500/5 px-3 py-2 text-sm text-rose-800">
              <ShieldAlert className="mt-0.5 size-4 shrink-0" /> This looks like a model-provider API key. Provider keys must stay on the server; use the AgentForge platform token instead.
            </p>
          )}
          <div className="flex flex-wrap items-center gap-2">
            <Button type="submit" variant="primary">Save</Button>
            <Button onClick={() => void runTest()} loading={testing} icon={<PlugZap className="size-4" />}>Test connection</Button>
            {test && "ok" in test && <Badge tone={test.ok ? "ok" : "warn"} dot>{test.text}</Badge>}
          </div>
          {test && "error" in test && <ErrorState compact error={test.error} what="the API health" />}
        </form>
      </Panel>
    </div>
  );
}
