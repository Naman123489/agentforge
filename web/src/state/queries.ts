// Server state. The backend owns run status, routing, budgets and verification; we only fetch and observe.
import { useQuery } from "@tanstack/react-query";
import { ApiError } from "../api/client";
import { api } from "../api/endpoints";
import { isTerminal, type RunDetail } from "../api/types";
import { keys } from "./queryClient";

/** 404 on "latest X" endpoints means "none yet", not an error worth shouting about. */
const notFoundIsEmpty = <T,>(p: Promise<T>): Promise<T | null> =>
  p.catch((e) => {
    if (e instanceof ApiError && e.kind === "not_found") return null;
    throw e;
  });

const livePoll = (run: RunDetail | undefined) => (run && !isTerminal(run.status) ? 5_000 : false);

export const useHealth = () =>
  useQuery({ queryKey: keys.health, queryFn: ({ signal }) => api.getHealth(signal), refetchInterval: 15_000, retry: false });

export const useModels = () =>
  useQuery({ queryKey: keys.models, queryFn: ({ signal }) => api.listConfiguredModels(signal), refetchInterval: 20_000 });

export const useRuns = (limit = 20) =>
  useQuery({ queryKey: [...keys.runs(), limit], queryFn: ({ signal }) => api.listRuns({ limit }, signal),
    refetchInterval: 10_000 });

export const useRun = (runId: string | undefined) =>
  useQuery({
    queryKey: keys.run(runId ?? ""), enabled: !!runId,
    queryFn: ({ signal }) => api.getRun(runId!, signal),
    // SSE is the fast path; this poll is the safety net that reconciles state if the stream drops.
    refetchInterval: (q) => livePoll(q.state.data),
  });

export const useStages = (runId: string | undefined, run?: RunDetail) =>
  useQuery({ queryKey: keys.stages(runId ?? ""), enabled: !!runId,
    queryFn: ({ signal }) => api.listStages(runId!, signal), refetchInterval: livePoll(run) });

export const useUsage = (runId: string | undefined, run?: RunDetail) =>
  useQuery({ queryKey: keys.usage(runId ?? ""), enabled: !!runId,
    queryFn: ({ signal }) => notFoundIsEmpty(api.getRunUsage(runId!, signal)), refetchInterval: livePoll(run) });

export const useReview = (runId: string | undefined) =>
  useQuery({ queryKey: keys.review(runId ?? ""), enabled: !!runId,
    queryFn: ({ signal }) => notFoundIsEmpty(api.getLatestReview(runId!, signal)) });

export const useCheckpoints = (runId: string | undefined) =>
  useQuery({ queryKey: keys.checkpoints(runId ?? ""), enabled: !!runId,
    queryFn: ({ signal }) => api.listCheckpoints(runId!, signal) });

export const useFiles = (runId: string | undefined) =>
  useQuery({ queryKey: keys.files(runId ?? ""), enabled: !!runId,
    queryFn: ({ signal }) => notFoundIsEmpty(api.listRunFiles(runId!, "", signal)) });

export const useFileContent = (runId: string | undefined, path: string | null) =>
  useQuery({ queryKey: keys.file(runId ?? "", path ?? ""), enabled: !!runId && !!path,
    queryFn: ({ signal }) => api.getRunFileContent(runId!, path!, signal), staleTime: 10_000 });

export const usePreview = (runId: string | undefined) =>
  useQuery({ queryKey: keys.preview(runId ?? ""), enabled: !!runId,
    queryFn: ({ signal }) => notFoundIsEmpty(api.getRunPreview(runId!, signal)) });

export const useContract = (runId: string | undefined) =>
  useQuery({ queryKey: keys.contract(runId ?? ""), enabled: !!runId,
    queryFn: ({ signal }) => notFoundIsEmpty(api.getRunContract(runId!, signal)) });

export const useDeployment = (runId: string | undefined) =>
  useQuery({ queryKey: keys.deployment(runId ?? ""), enabled: !!runId,
    queryFn: ({ signal }) => notFoundIsEmpty(api.getLatestDeployment(runId!, signal)),
    // Poll only while the server reports a deployment in progress.
    refetchInterval: (q) => (q.state.data && ["queued", "publishing", "deploying"].includes(q.state.data.status) ? 2_000 : false) });
