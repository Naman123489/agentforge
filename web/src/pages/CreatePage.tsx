import { PromptComposer } from "../components/run/PromptComposer";

/** Page 2: the one place to start a project. Deliberately minimal; details live in the observer and Insights. */
export default function CreatePage() {
  return (
    <div className="relative flex min-h-full flex-col items-center px-4 pt-[12vh] pb-16 sm:px-6">
      <div aria-hidden className="pointer-events-none absolute top-[8vh] left-1/2 h-72 w-[38rem] max-w-full -translate-x-1/2 rounded-full bg-[radial-gradient(closest-side,rgb(118_185_0/0.16),rgb(99_102_241/0.08),transparent)] blur-2xl" />
      <div className="relative w-full max-w-3xl">
        <div className="mb-9 text-center">
          <h1 className="text-3xl font-semibold tracking-[-0.03em] sm:text-[2.6rem]">Welcome to AgentForge.</h1>
          <p className="mt-3 text-lg text-subtle sm:text-xl">What would you like to build today?</p>
        </div>
        <PromptComposer />
      </div>
    </div>
  );
}
