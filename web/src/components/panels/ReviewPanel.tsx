import { useState } from "react";
import { CheckCircle2, ChevronRight, CircleDashed, FlaskConical, ShieldAlert, ShieldCheck, ShieldX, XCircle } from "lucide-react";
import type { CheckResult, RunDetail, Stage } from "../../api/types";
import { agentMeta } from "../../lib/agents";
import { fmtDuration, fmtTime } from "../../lib/format";
import { countChecks } from "../../lib/status";
import { useReview } from "../../state/queries";
import { Badge } from "../ui/Badge";
import { EmptyState, ErrorState, Loading } from "../ui/States";

export type CheckState = "passed" | "failed" | "not_executed";

/** A check without an exit code or duration that "passed" is reported, not proven; we show that difference. */
export function checkState(c: CheckResult & { skipped?: boolean }): CheckState {
  if (c.skipped) return "not_executed";
  return c.passed ? "passed" : "failed";
}

export function CheckRow({ c }: { c: CheckResult & { skipped?: boolean } }) {
  const [open, setOpen] = useState(false);
  const s = checkState(c);
  const icon = { passed: <CheckCircle2 className="size-4 text-emerald-700" />, failed: <XCircle className="size-4 text-rose-700" />,
    not_executed: <CircleDashed className="size-4 text-subtle" /> }[s];
  return (
    <li className="border-b border-line/50 last:border-0">
      <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open} disabled={!c.log}
        className="flex w-full items-center gap-3 px-3 py-2 text-left hover:bg-ink-800/60 disabled:cursor-default">
        <span aria-hidden>{icon}</span>
        <span className="flex-1 text-sm">{c.name}</span>
        <span className="sr-only">{s.replace("_", " ")}</span>
        <Badge tone={s === "passed" ? "ok" : s === "failed" ? "bad" : "neutral"}>{s === "not_executed" ? "not executed" : s}</Badge>
        <span className="num hidden w-16 text-right text-xs text-subtle sm:block">{fmtDuration(c.duration_ms ?? null)}</span>
        <span className="num hidden w-14 text-right font-mono text-xs text-subtle sm:block">{c.exit_code != null ? `exit ${c.exit_code}` : "—"}</span>
        {c.log && <ChevronRight className={`size-4 text-subtle transition ${open ? "rotate-90" : ""}`} aria-hidden />}
      </button>
      {open && c.log && <pre className="code-scroll mx-3 mb-2 max-h-80 overflow-auto rounded-lg border border-line bg-ink-950 p-3 text-[11.5px] text-muted whitespace-pre-wrap">{c.log}</pre>}
    </li>
  );
}

export function ReviewPanel({ run, stages }: { run: RunDetail; stages: Stage[] }) {
  const review = useReview(run.id);
  const builders = stages.filter((s) => s.result && (s.agent === "backend" || s.agent === "frontend"));
  const r = review.data;

  const verdict = !r
    ? { icon: <ShieldAlert className="size-6 text-amber-700" />, title: run.status === "done" ? "Completed but NOT verified" : "Not verified yet",
        text: "No Reviewer report exists. A model saying the code works is not proof; only executed checks count.", tone: "border-amber-400/30 bg-amber-400/5" }
    : r.verdict === "approve"
      ? { icon: <ShieldCheck className="size-6 text-emerald-700" />, title: "Verified: Reviewer approved on executed checks",
          text: r.summary, tone: "border-emerald-400/30 bg-emerald-400/5" }
      : r.verdict === "reject"
        ? { icon: <ShieldX className="size-6 text-rose-700" />, title: "Verification failed: Reviewer rejected", text: r.summary, tone: "border-rose-400/30 bg-rose-500/5" }
        : { icon: <ShieldAlert className="size-6 text-amber-700" />, title: "Review blocked", text: r.summary, tone: "border-amber-400/30 bg-amber-400/5" };

  return (
    <div className="space-y-4 p-4">
      {review.isError ? <ErrorState compact error={review.error} what="the review report" onRetry={() => void review.refetch()} />
        : review.isLoading ? <Loading label="Loading review" />
        : (
          <div className={`flex items-start gap-3 rounded-xl border px-4 py-3 ${verdict.tone}`}>
            {verdict.icon}
            <div className="min-w-0">
              <p className="font-medium">{verdict.title}</p>
              {verdict.text && <p className="mt-0.5 text-sm text-muted">{verdict.text}</p>}
              {r && <p className="num mt-1 text-xs text-subtle">{countChecks(r.checks).label} · stage <span className="font-mono">{r.stage_id}</span> · {fmtTime(r.created_at)}</p>}
            </div>
          </div>
        )}

      {r && r.required_fixes.length > 0 && (
        <section className="rounded-xl border border-line">
          <h3 className="panel-title border-b border-line px-3 py-2">Required fixes</h3>
          <ul className="list-inside list-disc space-y-1 px-4 py-2 text-sm text-muted">{r.required_fixes.map((f, i) => <li key={i}>{f}</li>)}</ul>
        </section>
      )}

      {r && (
        <section className="rounded-xl border border-line">
          <h3 className="panel-title border-b border-line px-3 py-2">Reviewer checks (source of truth)</h3>
          {r.checks.length ? <ul>{r.checks.map((c) => <CheckRow key={c.name} c={c} />)}</ul>
            : <p className="px-3 py-3 text-sm text-subtle">The report lists no checks.</p>}
        </section>
      )}

      <section className="rounded-xl border border-line">
        <h3 className="panel-title border-b border-line px-3 py-2">Builder self-checks <span className="normal-case tracking-normal text-subtle">(evidence from the agents; the Reviewer re-runs its own)</span></h3>
        {builders.length === 0 ? <EmptyState icon={<FlaskConical className="size-5" />} title="No builder results yet" /> : builders.map((s) => {
          const meta = agentMeta(s.agent);
          return (
            <div key={s.id} className="border-b border-line/60 last:border-0">
              <p className="flex items-center gap-2 px-3 pt-2 text-xs text-subtle">
                <meta.icon className="size-3.5" aria-hidden /> {meta.label} · attempt {s.attempt} · result <span className="font-mono">{s.result!.status}</span>
              </p>
              <ul>{s.result!.checks.map((c) => <CheckRow key={`${s.id}-${c.name}`} c={c} />)}</ul>
            </div>
          );
        })}
      </section>
    </div>
  );
}
