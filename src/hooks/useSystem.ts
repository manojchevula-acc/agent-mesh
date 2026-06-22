import { useMutation, useQuery } from "@tanstack/react-query";
import { deleteCollection, getHealth, getReady, reindex } from "@/api/system";
import { queryClient, queryKeys } from "@/lib/queryClient";

/** Polls /health every 15s to drive the API status indicator. */
export function useHealth() {
  return useQuery({
    queryKey: queryKeys.health,
    queryFn: getHealth,
    refetchInterval: 15_000,
    retry: 0,
    staleTime: 10_000,
  });
}

/** Readiness (vector DB reachability) for the Admin page. */
export function useReady(enabled = true) {
  return useQuery({
    queryKey: queryKeys.ready,
    queryFn: getReady,
    enabled,
    retry: 0,
  });
}

export function useReindex() {
  return useMutation({
    mutationFn: reindex,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.ready }),
  });
}

export function useDeleteCollection() {
  return useMutation({
    mutationFn: deleteCollection,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.ready }),
  });
}
