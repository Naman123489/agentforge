import { ChevronDown } from "lucide-react";

const DOCS: { q: string; a: string[] }[] = [
  { q: "How AgentForge works", a: [
    "You describe an application. The API creates a run and the orchestrator moves it through fixed states: planning, contract, building, verifying, checkpointing, done.",
    "Each state is a stage owned by one agent. The dashboard only observes: every status you see comes from the server.",
  ] },
  { q: "How the agents collaborate", a: [
    "The Planner writes an OpenAPI contract. It is validated before anything is built.",
    "The Backend and Frontend agents build against that same contract in parallel, each limited to its own folder (backend/** or frontend/**). The frontend gets a typed client generated from the contract, so it cannot call an endpoint that does not exist.",
    "If a builder needs a different API, it raises a contract change request instead of improvising.",
  ] },
  { q: "How model routing works", a: [
    "Every model call goes through the Model Router. It has three slots: A premium reasoning, B main coding, C economy.",
    "Planning goes to A. Routine code goes to B, and repeated failures escalate a task to A. On errors or rate limits a call falls back to the next healthy slot.",
    "Token, cost and premium-spend limits are hard limits checked before every call. Reaching one stops the run with the reason shown.",
  ] },
  { q: "How projects are generated", a: [
    "The backend is FastAPI + SQLite with its own tests. The frontend is React + TypeScript built with Vite.",
    "When both pass review, a preview server serves the UI and API from one origin. You can open it from the observer's Preview tab and download the files as a ZIP.",
  ] },
  { q: "How verification works", a: [
    "Builders run self-checks, but those never approve a stage. The Reviewer re-runs every check itself: tests, type check, build, contract usage, and the UI served with the backend.",
    "A check that could not run is reported as \"not executed\", never as passed. A stage turns green only when the Reviewer approves it.",
  ] },
  { q: "How checkpoints and rollback work", a: [
    "Each approved state is committed and tagged in the project's git repository (a checkpoint).",
    "A rejected builder is retried with the Reviewer's failures, up to the retry limit. If retries run out, the run rolls back to the last checkpoint and fails with the reason. You can also roll back manually from the Checkpoints tab.",
  ] },
];

export function Docs() {
  return (
    <div className="space-y-2.5">
      {DOCS.map((d, i) => (
        <details key={d.q} className="panel group overflow-hidden" open={i === 0}>
          <summary className="flex cursor-pointer list-none items-center gap-3 px-5 py-4 font-medium [&::-webkit-details-marker]:hidden">
            <span className="num grid size-7 place-items-center rounded-lg bg-ink-800 text-xs text-muted">{i + 1}</span>
            {d.q}
            <ChevronDown className="ml-auto size-4 text-subtle transition group-open:rotate-180" aria-hidden />
          </summary>
          <div className="space-y-2 px-5 pb-5 pl-15 text-[15px] leading-relaxed text-muted">
            {d.a.map((p) => <p key={p}>{p}</p>)}
          </div>
        </details>
      ))}
    </div>
  );
}
