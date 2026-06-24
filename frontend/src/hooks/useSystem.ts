import { useQuery } from "@tanstack/react-query";
import { apiClient } from "@/lib/apiClient";

interface HealthResponse {
  status: string;
  node?: string;
  uptime_seconds?: number;
  model?: string;
  service?: string;
}

/** Polls GET /health every 15 s to drive the API status indicator. */
export function useHealth() {
  return useQuery<HealthResponse>({
    queryKey: ["health"],
    queryFn: async () => {
      const { data } = await apiClient.get<HealthResponse>("/health");
      return data;
    },
    refetchInterval: 15_000,
    retry: 1,
    staleTime: 10_000,
  });
}
