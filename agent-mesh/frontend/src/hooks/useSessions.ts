import { useQuery } from "@tanstack/react-query";
import { listConversations } from "@/api/mesh";

/** Backs the sidebar session list — refetched whenever `useChat` pins/updates a session. */
export function useSessions(username: string) {
  const query = useQuery({
    queryKey: ["sessions", username],
    queryFn: () => listConversations(username),
    enabled: Boolean(username),
    staleTime: 10_000,
  });

  return {
    sessions: query.data ?? [],
    isLoading: query.isLoading,
  };
}
