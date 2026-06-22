import { useMutation, useQuery } from "@tanstack/react-query";
import { getIngestStatus, ingestDocument } from "@/api/ingest";
import type { ApiError } from "@/lib/apiClient";
import { queryKeys } from "@/lib/queryClient";
import type { IngestAccepted, IngestJobStatus, IngestParams } from "@/types/api";

export function useIngestUpload() {
  return useMutation<IngestAccepted, ApiError, IngestParams>({
    mutationFn: ingestDocument,
  });
}

/**
 * Polls ingestion job status every `intervalMs` until the job leaves the
 * "running" state. Pass `jobId = null` to disable.
 */
export function useIngestStatus(jobId: string | null, intervalMs = 4_000) {
  return useQuery<IngestJobStatus, ApiError>({
    queryKey: jobId ? queryKeys.ingestStatus(jobId) : ["ingest", "idle"],
    queryFn: () => getIngestStatus(jobId as string),
    enabled: Boolean(jobId),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status && status !== "running" ? false : intervalMs;
    },
    retry: 1,
  });
}
