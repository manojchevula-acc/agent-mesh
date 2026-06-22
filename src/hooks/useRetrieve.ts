import { useMutation } from "@tanstack/react-query";
import { retrieve } from "@/api/retrieve";
import type { ApiError } from "@/lib/apiClient";
import type { RetrieveRequest, RetrieveResponse } from "@/types/api";

/**
 * Retrieval is a user-triggered action, so it's modelled as a mutation rather
 * than a query — we don't want it to run on mount or refetch automatically.
 */
export function useRetrieve() {
  return useMutation<RetrieveResponse, ApiError, RetrieveRequest>({
    mutationFn: retrieve,
  });
}
