import { apiClient } from "@/lib/apiClient";
import type {
  AuditDetailRecord,
  AuditListResponse,
  ConversationHistory,
  ConversationsResponse,
  FeedbackListResponse,
  FeedbackRequest,
  FeedbackResponse,
  MeshResult,
  MeshUser,
  NodeHealth,
  TraceListResponse,
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

export async function getFeedback(): Promise<FeedbackListResponse> {
  const { data } = await apiClient.get<FeedbackListResponse>("/api/feedback/list");
  return data;
}

export async function getAudit(): Promise<AuditListResponse> {
  const { data } = await apiClient.get<AuditListResponse>("/api/audit");
  return data;
}

export async function getAuditDetail(requestId: string): Promise<AuditDetailRecord> {
  const { data } = await apiClient.get<AuditDetailRecord>(`/api/audit/${encodeURIComponent(requestId)}`);
  return data;
}

export async function getTraces(): Promise<TraceListResponse> {
  const { data } = await apiClient.get<TraceListResponse>("/api/traces");
  return data;
}

export async function getConversations(): Promise<ConversationsResponse> {
  const { data } = await apiClient.get<ConversationsResponse>("/api/conversations/list");
  return data;
}
