import { apiClient } from "@/lib/apiClient";
import { config } from "@/lib/config";
import type {
  AuditDetailRecord,
  AuditListResponse,
  ConversationHistory,
  ConversationsResponse,
  FeedbackListResponse,
  FeedbackRequest,
  FeedbackResponse,
  LogsResponse,
  MeshResult,
  MeshUser,
  NodeHealth,
  StreamEvent,
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

/**
 * Streams pipeline stage events via SSE, then yields a final "result" event
 * containing the full MeshResult. Uses native fetch (Axios doesn't support SSE).
 */
export async function* queryMeshStream(
  username: string,
  query: string,
  sessionId?: string,
): AsyncGenerator<StreamEvent> {
  const url = `${config.apiBaseURL}/api/query/stream`;
  const body = JSON.stringify({ username, query, ...(sessionId ? { session_id: sessionId } : {}) });

  let response: Response;
  try {
    response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
    });
  } catch {
    yield { type: "error", message: "Cannot reach the API server. Is the backend running?" };
    return;
  }

  if (!response.ok || !response.body) {
    yield { type: "error", message: `Server error: ${response.status}` };
    return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      // SSE messages are separated by double newlines
      const parts = buffer.split("\n\n");
      buffer = parts.pop() ?? "";

      for (const part of parts) {
        if (!part.trim()) continue;
        let eventType = "message";
        let dataLine = "";
        for (const line of part.split("\n")) {
          if (line.startsWith("event: ")) eventType = line.slice(7).trim();
          else if (line.startsWith("data: ")) dataLine = line.slice(6).trim();
        }
        if (!dataLine) continue;
        try {
          const data = JSON.parse(dataLine);
          if (eventType === "stage") {
            yield { type: "stage", stage: data.stage, status: data.status, message: data.message };
          } else if (eventType === "result") {
            yield { type: "result", result: data as MeshResult };
          } else if (eventType === "done") {
            yield { type: "done" };
            return;
          } else if (eventType === "error") {
            yield { type: "error", message: data.message ?? "Unknown error" };
          }
        } catch {
          // Malformed JSON chunk — skip
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
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

export async function getLogs(): Promise<LogsResponse> {
  const { data } = await apiClient.get<LogsResponse>("/api/logs");
  return data;
}
