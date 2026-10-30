// One function per operation in contracts/agentforge-openapi.yaml. No endpoints beyond the contract.
import { ApiError, isSafeRelativePath, rawRequest, request } from "./client";
import type {
  ActionAccepted, Checkpoint, ContractArtifact, CreateRunRequest, DeploymentRequest, DeploymentStatus,
  FileContent, FileTreeResponse, FollowupRequest, HealthResponse, ModelStatus, PreviewInfo, ReviewReport,
  RollbackRequest, RunDetail, RunList, RunStatus, Stage, UsageSummary,
} from "./types";

const enc = encodeURIComponent;

export const api = {
  getHealth: (signal?: AbortSignal) => request<HealthResponse>("GET", "/health", { signal }),

  createRun: (body: CreateRunRequest, idempotencyKey: string) =>
    request<RunDetail>("POST", "/runs", { body, headers: { "Idempotency-Key": idempotencyKey } }),
  listRuns: (q: { status?: RunStatus; limit?: number; cursor?: string } = {}, signal?: AbortSignal) =>
    request<RunList>("GET", "/runs", { query: q, signal }),
  getRun: (runId: string, signal?: AbortSignal) => request<RunDetail>("GET", `/runs/${enc(runId)}`, { signal }),
  cancelRun: (runId: string) => request<ActionAccepted>("DELETE", `/runs/${enc(runId)}`),
  createFollowup: (runId: string, body: FollowupRequest, idempotencyKey: string) =>
    request<RunDetail>("POST", `/runs/${enc(runId)}/followups`, { body, headers: { "Idempotency-Key": idempotencyKey } }),

  listStages: (runId: string, signal?: AbortSignal) =>
    request<{ run_id: string; stages: Stage[] }>("GET", `/runs/${enc(runId)}/stages`, { signal }),
  getRunContract: (runId: string, signal?: AbortSignal) =>
    request<ContractArtifact>("GET", `/runs/${enc(runId)}/contracts`, { signal }),

  listRunFiles: (runId: string, path = "", signal?: AbortSignal) => {
    if (path && !isSafeRelativePath(path)) throw new ApiError("bad_request", `Refusing unsafe path "${path}".`);
    return request<FileTreeResponse>("GET", `/runs/${enc(runId)}/files`, { query: { path }, signal });
  },
  getRunFileContent: (runId: string, path: string, signal?: AbortSignal) => {
    if (!isSafeRelativePath(path)) throw new ApiError("bad_request", `Refusing unsafe path "${path}".`);
    return request<FileContent>("GET", `/runs/${enc(runId)}/files/content`, { query: { path }, signal });
  },
  downloadRunZip: async (runId: string): Promise<Blob> =>
    (await rawRequest(`/runs/${enc(runId)}/download`, { headers: { Accept: "application/zip" } })).blob(),

  listCheckpoints: (runId: string, signal?: AbortSignal) =>
    request<{ run_id: string; checkpoints: Checkpoint[] }>("GET", `/runs/${enc(runId)}/checkpoints`, { signal }),
  rollbackRun: (runId: string, body: RollbackRequest) =>
    request<ActionAccepted>("POST", `/runs/${enc(runId)}/rollback`, { body }),

  getLatestReview: (runId: string, signal?: AbortSignal) =>
    request<ReviewReport>("GET", `/runs/${enc(runId)}/review`, { signal }),
  getRunUsage: (runId: string, signal?: AbortSignal) =>
    request<UsageSummary>("GET", `/runs/${enc(runId)}/usage`, { signal }),
  listConfiguredModels: (signal?: AbortSignal) => request<{ models: ModelStatus[] }>("GET", "/models", { signal }),
  getRunPreview: (runId: string, signal?: AbortSignal) =>
    request<PreviewInfo>("GET", `/runs/${enc(runId)}/preview`, { signal }),

  createDeployment: (runId: string, body: DeploymentRequest) =>
    request<DeploymentStatus>("POST", `/runs/${enc(runId)}/deployments`, { body }),
  getLatestDeployment: (runId: string, signal?: AbortSignal) =>
    request<DeploymentStatus>("GET", `/runs/${enc(runId)}/deployments/latest`, { signal }),
};
