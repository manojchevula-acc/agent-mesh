import { QueryClient } from "@tanstack/react-query";

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
      staleTime: 30_000,
    },
  },
});

/** Centralised query keys to avoid stringly-typed drift. */
export const queryKeys = {
  health: ["health"] as const,
  ready: ["ready"] as const,
  testCases: ["evaluate", "test-cases"] as const,
  ingestStatus: (jobId: string) => ["ingest", jobId] as const,
};
