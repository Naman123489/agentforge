import { useState } from "react";
import { ExternalLink, GitBranch, Rocket } from "lucide-react";
import type { DeploymentRequest, DeploymentStatus, RunDetail } from "../../api/types";
import { fmtTime } from "../../lib/format";
import type { Tone } from "../../lib/status";
import { useDeploy } from "../../state/mutations";
import { useCheckpoints, useDeployment, useHealth, useReview } from "../../state/queries";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { ConfirmDialog } from "../ui/ConfirmDialog";
import { EmptyState, ErrorState, Loading } from "../ui/States";
import { useToast } from "../ui/Toast";

export type DeployView =
  | "not_configured" | "disabled" | "waiting" | "queued" | "publishing" | "pushed" | "deploying" | "healthy" | "failed" | "rolled_back";

/** One state for the tab, from server facts only. A GitHub push is never shown as a live, healthy deployment. */
export function deployView(run: Pick<RunDetail, "status">, deployDep: string | undefined, latest: DeploymentStatus | null | undefined): DeployView {
  if (latest) return latest.status;
  if (deployDep !== "ok") return "not_configured";
  if (run.status !== "done") return "waiting";
  return "disabled";
}

export const VIEW: Record<DeployView, { label: string; tone: Tone; text: string; live?: boolean }> = {
  not_configured: { label: "Not configured", tone: "neutral", text: "This server has no deployment configured (it needs AF_DEPLOY_ENABLED=1 and a server-side GITHUB_TOKEN)." },
  disabled: { label: "Disabled", tone: "neutral", text: "Deployment was not requested for this run. You can publish the approved build below." },
  waiting: { label: "Waiting", tone: "neutral", text: "Deployment can only start after the Reviewer approves the final stage and the run finishes." },
  queued: { label: "Queued", tone: "live", text: "Accepted; the Deploy agent is starting.", live: true },
  publishing: { label: "Publishing", tone: "live", text: "Verifying the approved checkpoint, scanning for secrets and pushing to GitHub.", live: true },
  pushed: { label: "Pushed to GitHub", tone: "brand", text: "The approved checkpoint is on GitHub. Nothing was deployed, so there is no live URL or health check." },
  deploying: { label: "Deploying", tone: "live", text: "Deploying to the target and waiting for the health check.", live: true },
  healthy: { label: "Health check passed", tone: "ok", text: "Deployed, and the health check passed." },
  failed: { label: "Deployment failed", tone: "bad", text: "The deployment did not complete. The verified build and its checkpoint are unchanged." },
  rolled_back: { label: "Rolled back", tone: "warn", text: "The new deployment failed its health check; the previous healthy deployment was restored." },
};

export function DeployPanel({ run }: { run: RunDetail }) {
  const health = useHealth();
  const latest = useDeployment(run.id);
  const review = useReview(run.id);
  const cps = useCheckpoints(run.id);
  const deploy = useDeploy(run.id);
  const toast = useToast();
  const [confirm, setConfirm] = useState(false);
  const [req, setReq] = useState<DeploymentRequest>({ target: "github", health_check_path: "/health", rollback_on_failure: true,
    visibility: "private", repo_name: null });

  const deployDep = health.data?.dependencies?.deploy;
  const d = latest.data;
  const view = deployView(run, deployDep, d);
  const v = VIEW[view];
  const verified = review.data?.verdict === "approve";
  const hasCheckpoint = (cps.data?.checkpoints.length ?? 0) > 0;
  const inProgress = !!d && ["queued", "publishing", "deploying"].includes(d.status);
  const blockers = [
    deployDep !== "ok" && `deployment is not configured on this server${deployDep ? ` (deploy: ${deployDep})` : ""}`,
    run.status !== "done" && "the run has not finished",
    !verified && "the Reviewer has not approved the final stage",
    !hasCheckpoint && "there is no approved checkpoint",
    inProgress && "a deployment is already in progress",
  ].filter(Boolean) as string[];

  return (
    <div className="space-y-4 p-4">
      <section className="rounded-xl border border-line p-4">
        <div className="mb-3 flex items-center gap-2">
          <h3 className="panel-title">Deployment</h3>
          <Badge tone={v.tone} dot pulse={v.live}>{v.label}</Badge>
        </div>
        <p className="text-sm text-muted">{v.text}</p>
        {latest.isLoading ? <Loading label="Loading deployment" />
          : latest.isError ? <ErrorState compact error={latest.error} what="the deployment status" onRetry={() => void latest.refetch()} />
          : !d ? (view === "disabled" && <div className="mt-3"><EmptyState icon={<Rocket className="size-5" />} title="Not deployed">No deployment has been requested for this run.</EmptyState></div>)
          : (
            <dl className="mt-4 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-sm">
              <dt className="text-subtle">Target</dt><dd className="font-mono text-muted">{d.target}</dd>
              {d.git_tag && <><dt className="text-subtle">Checkpoint</dt><dd className="font-mono text-muted">{d.git_tag}</dd></>}
              <dt className="text-subtle">Repository</dt>
              <dd>{d.repo_url ? <a href={d.repo_url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-nv-700 hover:underline"><GitBranch className="size-3.5" />{d.repo_url}<ExternalLink className="size-3.5" /></a> : <span className="text-subtle">not pushed yet</span>}</dd>
              {d.commit_sha && <><dt className="text-subtle">Commit</dt><dd className="font-mono text-muted">{d.commit_sha.slice(0, 12)}</dd></>}
              <dt className="text-subtle">Live URL</dt>
              <dd>{d.url ? <a href={d.url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-nv-700 hover:underline">{d.url}<ExternalLink className="size-3.5" /></a> : <span className="text-subtle">none (no live deployment)</span>}</dd>
              <dt className="text-subtle">Health check</dt><dd className="text-muted">{d.health_check_passed == null ? "not run" : d.health_check_passed ? "passed" : "failed"}</dd>
              <dt className="text-subtle">Requested</dt><dd className="num text-muted">{fmtTime(d.created_at)}</dd>
              {d.error && (<><dt className="text-subtle">Error</dt><dd className="text-rose-700">{d.error}</dd></>)}
            </dl>
          )}
        {d && (d.checks?.length ?? 0) > 0 && (
          <ul className="mt-4 flex flex-wrap gap-1.5" aria-label="Deploy checks">
            {d.checks!.map((c) => <li key={c.name}><Badge tone={c.passed ? "ok" : "bad"} dot>{c.name.replaceAll("_", " ")}</Badge></li>)}
          </ul>
        )}
        {d && (d.notes?.length ?? 0) > 0 && (
          <ul className="mt-3 list-inside list-disc text-xs text-subtle">{d.notes!.map((n, i) => <li key={i}>{n}</li>)}</ul>
        )}
      </section>

      <section className="rounded-xl border border-line p-4">
        <h3 className="panel-title mb-2">Publish this build</h3>
        {blockers.length > 0 ? (
          <div className="text-sm text-muted">
            <p>Publishing is unavailable because:</p>
            <ul className="mt-1 list-inside list-disc text-subtle">{blockers.map((b) => <li key={b}>{b}</li>)}</ul>
          </div>
        ) : (
          <div className="flex flex-wrap items-end gap-3">
            <label className="flex flex-col gap-1 text-xs text-subtle">Target
              <select value={req.target} onChange={(e) => setReq((r) => ({ ...r, target: e.target.value as DeploymentRequest["target"] }))}
                className="h-9 rounded-lg border border-line bg-white px-2 text-sm text-fg">
                <option value="github">GitHub (publish only)</option>
                <option value="nebius_serverless" disabled>Nebius Serverless (not available)</option>
              </select>
            </label>
            <label className="flex flex-col gap-1 text-xs text-subtle">Repository name (optional)
              <input value={req.repo_name ?? ""} maxLength={100} placeholder="project-run id"
                onChange={(e) => setReq((r) => ({ ...r, repo_name: e.target.value.trim() ? e.target.value : null }))}
                className="h-9 rounded-lg border border-line bg-white px-2 font-mono text-sm text-fg" />
            </label>
            <label className="flex flex-col gap-1 text-xs text-subtle">Visibility
              <select value={req.visibility} onChange={(e) => setReq((r) => ({ ...r, visibility: e.target.value as "private" | "public" }))}
                className="h-9 rounded-lg border border-line bg-white px-2 text-sm text-fg">
                <option value="private">Private</option><option value="public">Public</option>
              </select>
            </label>
            <Button variant="primary" icon={<Rocket className="size-4" />} onClick={() => setConfirm(true)}>Publish…</Button>
          </div>
        )}
      </section>

      <ConfirmDialog open={confirm} title="Publish the approved build?" confirmLabel="Publish to GitHub" busy={deploy.isPending}
        onClose={() => { setConfirm(false); deploy.reset(); }}
        onConfirm={() => deploy.mutate(req, { onSuccess: (r) => { setConfirm(false); toast("info", `Deployment ${r.id} accepted (${r.status}). Status updates appear here.`); } })}>
        <p>The Deploy agent verifies the run's final approved checkpoint, scans it for secrets and pushes it to a <strong>{req.visibility}</strong> GitHub repository. It does not deploy a live app.</p>
        {req.visibility === "public" && <p className="mt-2 text-amber-800">A public repository is visible to everyone.</p>}
        {deploy.isError && <ErrorState compact error={deploy.error} what="the deployment" />}
      </ConfirmDialog>
    </div>
  );
}
