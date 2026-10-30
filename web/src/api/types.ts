// Platform types, generated from contracts/agentforge-openapi.yaml (npm run gen:api). Never hand-edit shapes here.
import type { components } from "./schema";

type S = components["schemas"];

export type HealthResponse = S["HealthResponse"];
export type CreateRunRequest = S["CreateRunRequest"];
export type FollowupRequest = S["FollowupRequest"];
export type RunBudget = S["RunBudget"];
export type RunOptions = S["RunOptions"];
export type RunStatus = S["RunStatus"];
export type StageStatus = S["StageStatus"];
export type AgentRole = S["AgentRole"];
export type RunDetail = S["RunDetail"];
export type RunList = S["RunList"];
export type Stage = S["Stage"];
export type RunEvent = S["RunEvent"];
export type RunEventType = RunEvent["type"];
export type BuildResult = S["BuildResult"];
export type CheckResult = S["CheckResult"];
export type ReviewReport = S["ReviewReport"];
export type Checkpoint = S["Checkpoint"];
export type RollbackRequest = S["RollbackRequest"];
export type ContractArtifact = S["ContractArtifact"];
export type UsageSummary = S["UsageSummary"];
export type ModelStatus = S["ModelStatus"];
export type FileTreeResponse = S["FileTreeResponse"];
export type FileContent = S["FileContent"];
export type PreviewInfo = S["PreviewInfo"];
export type DeploymentRequest = S["DeploymentRequest"];
export type DeploymentStatus = S["DeploymentStatus"];
export type ActionAccepted = S["ActionAccepted"];
export type ErrorResponse = S["ErrorResponse"];
export type ValidationErrorResponse = S["ValidationErrorResponse"];

export const TERMINAL_RUN_STATUSES: readonly RunStatus[] = ["done", "failed", "cancelled", "stopped"];
export const isTerminal = (s: RunStatus | undefined): boolean => !!s && TERMINAL_RUN_STATUSES.includes(s);
