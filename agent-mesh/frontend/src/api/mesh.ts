import { apiClient } from "@/lib/apiClient";
import type {
  ConversationHistory,
  ConversationSummary,
  FeedbackRequest,
  FeedbackResponse,
  MeshResult,
  MeshUser,
  NodeHealth,
} from "@/types/mesh";

export async function queryMesh(
  username: string,
  query: string,
  sessionId?: string,
): Promise<MeshResult> {
  const { data } = await apiClient.post<MeshResult>("/api/query", {
    username,
    query,
    ...(sessionId ? { session_id: sessionId } : {}),
  });
  return data;
}

export async function getConversation(sessionId: string): Promise<ConversationHistory> {
  const { data } = await apiClient.get<ConversationHistory>(
    `/api/conversations/${encodeURIComponent(sessionId)}`,
  );
  return data;
}

export async function listConversations(username: string): Promise<ConversationSummary[]> {
  const { data } = await apiClient.get<{ sessions: ConversationSummary[] }>(
    "/api/conversations",
    { params: { username } },
  );
  return data.sessions;
}

export async function renameConversation(
  sessionId: string,
  username: string,
  title: string,
): Promise<void> {
  await apiClient.patch(
    `/api/conversations/${encodeURIComponent(sessionId)}`,
    { username, title },
  );
}

export async function deleteConversation(
  sessionId: string,
  username: string,
): Promise<void> {
  await apiClient.delete(`/api/conversations/${encodeURIComponent(sessionId)}`, {
    params: { username },
  });
}

export async function listUsers(): Promise<MeshUser[]> {
  const { data } = await apiClient.get<MeshUser[]>("/api/users");
  return data;
}

export async function loginUser(username: string): Promise<MeshUser> {
  const { data } = await apiClient.post<MeshUser>("/api/login", { username });
  return data;
}

export async function getMeshStatus(): Promise<NodeHealth[]> {
  const { data } = await apiClient.get<NodeHealth[]>("/api/mesh/status");
  return data;
}

export async function submitFeedback(payload: FeedbackRequest): Promise<FeedbackResponse> {
  const { data } = await apiClient.post<FeedbackResponse>("/api/feedback", payload);
  return data;
}
