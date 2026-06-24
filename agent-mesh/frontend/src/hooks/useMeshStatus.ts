import { useQuery } from "@tanstack/react-query";
import { getMeshStatus } from "@/api/mesh";
import type { NodeHealth } from "@/types/mesh";

export const MESH_STATUS_KEY = ["meshStatus"] as const;

export function useMeshStatus() {
  return useQuery<NodeHealth[]>({
    queryKey: MESH_STATUS_KEY,
    queryFn: getMeshStatus,
    refetchInterval: 15_000,
    retry: 1,
    staleTime: 10_000,
  });
}
