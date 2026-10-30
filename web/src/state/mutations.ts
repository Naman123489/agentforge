// Commands. Each one only reports what the server accepted (202 = queued, not done) and then refetches.
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/endpoints";
import type { CreateRunRequest, DeploymentRequest, FollowupRequest, RollbackRequest } from "../api/types";
import { keys } from "./queryClient";

export const newIdempotencyKey = (): string =>
  typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : `af-${Date.now()}-${Math.random().toString(36).slice(2)}`;

export function useCreateRun() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ body, key }: { body: CreateRunRequest; key: string }) => api.createRun(body, key),
    onSuccess: (run) => {
      qc.setQueryData(keys.run(run.id), run);
      void qc.invalidateQueries({ queryKey: keys.runs() });
    },
  });
}

export function useFollowup(runId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ body, key }: { body: FollowupRequest; key: string }) => api.createFollowup(runId, body, key),
    onSuccess: (run) => {
      qc.setQueryData(keys.run(run.id), run);
      void qc.invalidateQueries({ queryKey: keys.runs() });
    },
  });
}

export function useCancelRun(runId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.cancelRun(runId),
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: keys.run(runId) });
      void qc.invalidateQueries({ queryKey: keys.stages(runId) });
    },
  });
}

export function useRollback(runId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: RollbackRequest) => api.rollbackRun(runId, body),
    onSettled: () => {
      for (const k of [keys.run, keys.stages, keys.checkpoints, keys.files, keys.review])
        void qc.invalidateQueries({ queryKey: k(runId) });
    },
  });
}

export function useDeploy(runId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: DeploymentRequest) => api.createDeployment(runId, body),
    onSuccess: (d) => qc.setQueryData(keys.deployment(runId), d),
    onSettled: () => void qc.invalidateQueries({ queryKey: keys.deployment(runId) }),
  });
}
