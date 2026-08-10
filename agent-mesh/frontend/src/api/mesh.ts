import { apiClient } from "@/lib/apiClient";
import { config } from "@/lib/config";
import type { StructuredFeedbackRequest, StructuredFeedbackResponse, StructuredFeedbackListResponse } from "@/types/feedback";
import type {
  AuditDetailRecord,
  AuditListResponse,
  ConversationHistory,
  ConversationsResponse,
  FeedbackListResponse,
  FeedbackRequest,
  FeedbackResponse,
  HitlDetails,
  LLMReasoningEntry,
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
  bypassCache?: boolean,
  signal?: AbortSignal,
): AsyncGenerator<StreamEvent> {
  const url = `${config.apiBaseURL}/api/query/stream`;
  const body = JSON.stringify({
    username,
    query,
    ...(sessionId ? { session_id: sessionId } : {}),
    ...(bypassCache ? { bypass_cache: true } : {}),
  });

  let response: Response;
  try {
    response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
      signal,
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") return;
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
      if (signal?.aborted) break;
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
            yield { type: "stage", stage: data.stage, status: data.status, message: data.message, judge_invoked: data.judge_invoked, judge_decision: data.judge_decision, judge_reason: data.judge_reason };
          } else if (eventType === "reasoning") {
            yield { type: "reasoning", entries: data.entries as LLMReasoningEntry[] };
          } else if (eventType === "hitl") {
            yield { type: "hitl", approval_id: data.approval_id as string, details: data.details as HitlDetails };
          } else if (eventType === "intent_suggestion") {
            yield {
              type: "intent_suggestion",
              root_query: data.root_query as string,
              entry_id: data.entry_id as string,
              similarity: data.similarity as number,
              age_hours: data.age_hours as number,
              answer_preview: data.answer_preview as string,
              confidence: data.confidence as "high" | "intent_match" | "pending_judge",
              judge_verdict: data.judge_verdict as "YES" | "NO" | null,
              judge_reason: data.judge_reason as string | null,
              candidates: (data.candidates ?? []) as Array<{
                entry_id: string; root_query: string; similarity: number;
                age_hours: number; answer_preview: string;
                confidence: "high" | "intent_match" | "pending_judge";
              }>,
            };
          } else if (eventType === "intent_suggestion_judge") {
            yield {
              type: "intent_suggestion_judge",
              entry_id: data.entry_id as string,
              judge_verdict: data.judge_verdict as "YES" | "NO",
              judge_reason: data.judge_reason as string,
            };
          } else if (eventType === "cache_context") {
            yield {
              type: "cache_context",
              context: "hit" as const,
              candidates: (data.candidates ?? []) as Array<{
                entry_id: string; root_query: string; similarity: number;
                age_hours: number; answer_preview: string;
                confidence: "high" | "intent_match" | "pending_judge";
              }>,
            };
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

export async function submitStructuredFeedback(
  payload: StructuredFeedbackRequest,
): Promise<StructuredFeedbackResponse> {
  const { data } = await apiClient.post<StructuredFeedbackResponse>("/api/feedback/structured", payload);
  return data;
}

export async function getFeedback(): Promise<FeedbackListResponse> {
  const { data } = await apiClient.get<FeedbackListResponse>("/api/feedback/list");
  return data;
}

export async function getStructuredFeedback(): Promise<StructuredFeedbackListResponse> {
  const { data } = await apiClient.get<StructuredFeedbackListResponse>("/api/feedback/structured/list");
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

export async function getApprovalsList(): Promise<import("@/types/mesh").ApprovalListItem[]> {
  const { data } = await apiClient.get("/api/approvals");
  return data;
}

export async function getApprovalDetails(approvalId: string): Promise<HitlDetails & { approval_id: string }> {
  const { data } = await apiClient.get(`/api/approvals/${encodeURIComponent(approvalId)}`);
  return data;
}

export async function approveRequest(approvalId: string): Promise<void> {
  await apiClient.post(`/api/approvals/${encodeURIComponent(approvalId)}/approve`);
}

export async function rejectRequest(approvalId: string): Promise<void> {
  await apiClient.post(`/api/approvals/${encodeURIComponent(approvalId)}/reject`);
}

export async function resolveIntentDecision(
  primaryEntryId: string,
  chosenEntryId: string,
  accepted: boolean,
): Promise<void> {
  await apiClient.post("/api/cache/intent-decision", {
    entry_id: primaryEntryId,
    chosen_entry_id: chosenEntryId,
    accepted,
  });
}
