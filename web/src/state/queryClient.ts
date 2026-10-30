import { QueryClient } from "@tanstack/react-query";
import { ApiError } from "../api/client";

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 2_000,
      refetchOnWindowFocus: true,
      // Retry only transient failures; a 404 or 401 will not fix itself.
      retry: (count, err) => err instanceof ApiError && err.retryable && count < 2,
    },
  },
});

export const keys = {
  health: ["health"] as const,
  models: ["models"] as const,
  runs: (status?: string) => ["runs", status ?? "all"] as const,
  run: (id: string) => ["run", id] as const,
  stages: (id: string) => ["stages", id] as const,
  usage: (id: string) => ["usage", id] as const,
  review: (id: string) => ["review", id] as const,
  checkpoints: (id: string) => ["checkpoints", id] as const,
  files: (id: string) => ["files", id] as const,
  file: (id: string, path: string) => ["file", id, path] as const,
  preview: (id: string) => ["preview", id] as const,
  contract: (id: string) => ["contract", id] as const,
  deployment: (id: string) => ["deployment", id] as const,
};
