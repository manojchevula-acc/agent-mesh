import { useCallback, useEffect, useRef, useState } from "react";
import { getConversation, queryMeshStream, submitFeedback } from "@/api/mesh";
import type { ChatMessage, ExecutionEvent, LLMReasoningEntry } from "@/types/mesh";

const SESSION_ID_KEY = "agent-mesh-session-id";

function makeId() {
  return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function readSessionId(): string | null {
  try {
    return localStorage.getItem(SESSION_ID_KEY);
  } catch {
    return null;
  }
}

function writeSessionId(id: string | null) {
  try {
    if (id) localStorage.setItem(SESSION_ID_KEY, id);
    else localStorage.removeItem(SESSION_ID_KEY);
  } catch {
    /* ignore storage failures (private mode, etc.) */
  }
}

interface UseChatOptions {
  username: string;
  role: string;
}

export function useChat({ username, role }: UseChatOptions) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  // Holds the active conversation id. Persisted to localStorage so the thread
  // survives a page refresh; pinned by the first response that returns it.
  const sessionIdRef = useRef<string | null>(readSessionId());

  // On mount, restore prior turns for the stored session so a refresh doesn't
  // lose the conversation. Best-effort — failure just leaves the chat empty.
  useEffect(() => {
    const sid = sessionIdRef.current;
    if (!sid) return;
    let cancelled = false;
    getConversation(sid)
      .then((history) => {
        if (cancelled || history.messages.length === 0) return;
        setMessages(
          history.messages.map((m) => ({
            id: makeId(),
            role: m.role,
            content: m.content,
            timestamp: m.ts ? new Date(m.ts) : new Date(),
          })),
        );
      })
      .catch(() => {
        /* API unreachable or no history — start fresh */
      });
    return () => {
      cancelled = true;
    };
    // Restore once on mount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  const sendMessage = useCallback(
    (query: string) => {
      const trimmed = query.trim();
      if (!trimmed || isLoading) return;

      const assistantId = makeId();
      setIsLoading(true);
      setError(null);

      setMessages((prev) => [
        ...prev,
        { id: makeId(), role: "user" as const, content: trimmed, timestamp: new Date() },
        { id: assistantId, role: "assistant" as const, content: "", isLoading: true, timestamp: new Date() },
      ]);

      (async () => {
        try {
          const stream = queryMeshStream(username, trimmed, sessionIdRef.current ?? undefined);
          for await (const event of stream) {
            if (event.type === "stage") {
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === assistantId
                    ? {
                        ...m,
                        streamingStage: event.message
                          ? `${event.message}`
                          : event.stage,
                        streamingEvents: [
                          ...(m.streamingEvents ?? []),
                          { stage: event.stage, status: event.status, message: event.message } as ExecutionEvent,
                        ],
                      }
                    : m
                )
              );
            } else if (event.type === "reasoning") {
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === assistantId
                    ? { ...m, streamingReasoning: [...(m.streamingReasoning ?? []), ...(event.entries as LLMReasoningEntry[])] }
                    : m
                )
              );
            } else if (event.type === "result") {
              const result = event.result;
              if (result.session_id && result.session_id !== sessionIdRef.current) {
                sessionIdRef.current = result.session_id;
                writeSessionId(result.session_id);
              }
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === assistantId
                    ? { ...m, content: result.answer, result, isLoading: false, streamingStage: undefined, timestamp: new Date() }
                    : m
                )
              );
            } else if (event.type === "error") {
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === assistantId
                    ? {
                        ...m,
                        content: event.message || "Failed to reach the mesh. Make sure the mesh is running (`python launch_mesh.py`) and the API server is up (`python api_server.py`).",
                        isLoading: false,
                        streamingStage: undefined,
                        result: { answer: "", blocked: true, block_stage: "api_error", trail: [] },
                      }
                    : m
                )
              );
            }
          }
        } catch (err) {
          setError(err instanceof Error ? err : new Error(String(err)));
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantId
                ? {
                    ...m,
                    content: "Failed to reach the mesh. Make sure the mesh is running (`python launch_mesh.py`) and the API server is up (`python api_server.py`).",
                    isLoading: false,
                    streamingStage: undefined,
                    result: { answer: "", blocked: true, block_stage: "api_error", trail: [] },
                  }
                : m
            )
          );
        } finally {
          setIsLoading(false);
        }
      })();
    },
    [isLoading, username]
  );

  const clearChat = useCallback(() => {
    // "New Chat": drop the local transcript AND the session id so the next query
    // starts a fresh conversation server-side.
    setMessages([]);
    sessionIdRef.current = null;
    writeSessionId(null);
  }, []);

  const handleFeedback = useCallback(
    async (messageId: string, rating: "up" | "down", comment?: string) => {
      const msgIndex = messages.findIndex((m) => m.id === messageId);
      if (msgIndex === -1) return;
      const msg = messages[msgIndex];
      if (!msg.result?.request_id) return;
      // Find the preceding user message to get the original query text.
      const userMsg = msgIndex > 0 ? messages[msgIndex - 1] : null;
      await submitFeedback({
        request_id: msg.result.request_id,
        session_id: msg.result.session_id ?? sessionIdRef.current ?? "",
        user: username,
        role,
        rating,
        query: userMsg?.content ?? "",
        answer: msg.content,
        route: msg.result.route ?? undefined,
        blocked: msg.result.blocked,
        comment: comment ?? "",
      });
      // Lock the message — no re-rating after submission.
      setMessages((prev) =>
        prev.map((m) =>
          m.id === messageId ? { ...m, feedback: { rating, comment } } : m
        )
      );
    },
    [messages, username, role]
  );

  return {
    messages,
    sendMessage,
    clearChat,
    handleFeedback,
    isLoading,
    error,
  };
}
