import { useState } from "react";
import { ExternalLink, Laptop, MonitorOff, RefreshCw, Smartphone, Tablet } from "lucide-react";
import { usePreview } from "../../state/queries";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { EmptyState, ErrorState, Loading } from "../ui/States";

const WIDTHS = { desktop: "100%", tablet: "820px", mobile: "390px" } as const;

/** Only http(s) URLs supplied by the backend are ever loaded. */
export function safePreviewUrl(url: string | null | undefined): URL | null {
  if (!url) return null;
  try {
    const u = new URL(url, window.location.origin);
    return u.protocol === "http:" || u.protocol === "https:" ? u : null;
  } catch {
    return null;
  }
}

export function PreviewPanel({ runId }: { runId: string }) {
  const preview = usePreview(runId);
  const [device, setDevice] = useState<keyof typeof WIDTHS>("desktop");
  const [nonce, setNonce] = useState(0);
  const [loaded, setLoaded] = useState(false);

  if (preview.isLoading) return <Loading label="Checking preview" />;
  if (preview.isError) return <ErrorState error={preview.error} what="the preview status" onRetry={() => void preview.refetch()} />;
  const p = preview.data;
  if (!p || p.status === "unavailable")
    return <EmptyState icon={<MonitorOff className="size-5" />} title="Preview unavailable">{p?.error ?? "The server has not exposed a preview for this run (preview may be disabled or not built yet)."}</EmptyState>;
  if (p.status === "pending" || p.status === "building")
    return <EmptyState icon={<RefreshCw className="size-5 motion-safe:animate-spin" />} title={p.status === "pending" ? "Preview pending" : "Building preview…"}>The preview appears when the server reports it ready.</EmptyState>;
  if (p.status === "failed")
    return <ErrorState error={new Error(p.error ?? "The preview build failed (no reason supplied).")} what="the preview" onRetry={() => void preview.refetch()} />;

  const url = safePreviewUrl(p.url);
  if (!url) return <ErrorState error={new Error("The server reported the preview as ready but sent no valid http(s) URL.")} what="the preview" />;
  // A same-origin preview must not get allow-same-origin together with allow-scripts (it could escape the sandbox).
  const sameOrigin = url.origin === window.location.origin;
  const sandbox = `allow-scripts allow-forms allow-popups allow-modals${sameOrigin ? "" : " allow-same-origin"}`;

  return (
    <div className="flex flex-col">
      <div className="flex flex-wrap items-center gap-2 border-b border-line px-3 py-2">
        <div role="group" aria-label="Viewport" className="flex gap-1">
          {([["desktop", Laptop], ["tablet", Tablet], ["mobile", Smartphone]] as const).map(([k, Icon]) => (
            <button key={k} type="button" aria-pressed={device === k} aria-label={`${k} width`} onClick={() => setDevice(k)}
              className={`rounded-md p-1.5 ${device === k ? "bg-nv-500/20 text-nv-800" : "text-subtle hover:text-fg"}`}>
              <Icon className="size-4" />
            </button>
          ))}
        </div>
        <span className="min-w-0 flex-1 truncate rounded-md border border-line bg-ink-900 px-2.5 py-1 font-mono text-xs text-muted">{url.href}</span>
        {p.health_check_passed != null && <Badge tone={p.health_check_passed ? "ok" : "bad"} dot>{p.health_check_passed ? "health check passed" : "health check failed"}</Badge>}
        <Button size="sm" variant="ghost" icon={<RefreshCw className="size-3.5" />} onClick={() => { setLoaded(false); setNonce((n) => n + 1); }}>Reload</Button>
        <a href={url.href} target="_blank" rel="noopener noreferrer" className="inline-flex h-7 items-center gap-1.5 rounded-lg px-2.5 text-xs text-muted hover:bg-ink-750 hover:text-fg">
          <ExternalLink className="size-3.5" /> Open
        </a>
      </div>
      <div className="flex justify-center bg-ink-950/60 p-3">
        <div className="relative w-full overflow-hidden rounded-lg border border-line bg-white transition-[max-width]" style={{ maxWidth: WIDTHS[device] }}>
          {!loaded && <div className="absolute inset-0 grid place-items-center bg-ink-900"><Loading label="Loading preview" /></div>}
          <iframe key={nonce} title="Generated application preview" src={url.href} sandbox={sandbox}
            referrerPolicy="no-referrer" onLoad={() => setLoaded(true)} className="h-[36rem] w-full" />
        </div>
      </div>
    </div>
  );
}
