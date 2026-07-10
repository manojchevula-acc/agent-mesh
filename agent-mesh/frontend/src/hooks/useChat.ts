import { useCallback, useEffect, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { getConversation, queryMesh, submitFeedback } from "@/api/mesh";
import type { ChatMessage, MeshResult } from "@/types/mesh";

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
  const queryClient = useQueryClient();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  // Holds the active conversation id. Persisted to localStorage so the thread
  // survives a page refresh; pinned by the first response that returns it.
  const sessionIdRef = useRef<string | null>(readSessionId());
  // Mirrors sessionIdRef in React state purely so the sidebar can re-render to
  // highlight the active session — the ref stays the source of truth read inside
  // the mutation closure to avoid stale-closure bugs.
  const [sessionId, setSessionIdState] = useState<string | null>(sessionIdRef.current);

  const setSessionId = useCallback((id: string | null) => {
    sessionIdRef.current = id;
    writeSessionId(id);
    setSessionIdState(id);
  }, []);

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

  const mutation = useMutation({
    mutationFn: ({ query }: { query: string }) =>
      queryMesh(username, query, sessionIdRef.current ?? undefined),
    onMutate: ({ query }: { query: string }) => {
      const userMsgId = makeId();
      const assistantPlaceholderId = makeId();

      setMessages((prev) => [
        ...prev,
        {
          id: userMsgId,
          role: "user" as const,
          content: query,
          timestamp: new Date(),
        },
        {
          id: assistantPlaceholderId,
          role: "assistant" as const,
          content: "",
          isLoading: true,
          timestamp: new Date(),
        },
      ]);

      return { assistantPlaceholderId };
    },
    onSuccess: (result: MeshResult, _vars, ctx) => {
      // Pin / persist the conversation id returned by the backend.
      if (result.session_id && result.session_id !== sessionIdRef.current) {
        setSessionId(result.session_id);
      }
      // The sidebar's session list (preview text, timestamp) is now stale — refresh it.
      queryClient.invalidateQueries({ queryKey: ["sessions", username] });
      if (!ctx) return;
      setMessages((prev) =>
        prev.map((msg) =>
          msg.id === ctx.assistantPlaceholderId
            ? {
                ...msg,
                content: result.answer,
                result,
                isLoading: false,
                timestamp: new Date(),
              }
            : msg
        )
      );
    },
    onError: (_error: Error, _vars, ctx) => {
      if (!ctx) return;
      setMessages((prev) =>
        prev.map((msg) =>
          msg.id === ctx.assistantPlaceholderId
            ? {
                ...msg,
                content:
                  "Failed to reach the mesh. Make sure the mesh is running (`python launch_mesh.py`) and the API server is up (`python api_server.py`).",
                isLoading: false,
                result: {
                  answer: "",
                  blocked: true,
                  block_stage: "api_error",
                  trail: [],
                },
                timestamp: new Date(),
              }
            : msg
        )
      );
    },
  });

  const sendMessage = useCallback(
    (query: string) => {
      const trimmed = query.trim();
      if (!trimmed || mutation.isPending) return;
      mutation.mutate({ query: trimmed });
    },
    [mutation]
  );

  const clearChat = useCallback(() => {
    // "New Chat": drop the local transcript AND the session id so the next query
    // starts a fresh conversation server-side. The OLD session's history is left
    // untouched on the backend — it stays switchable from the session list.
    setMessages([]);
    setSessionId(null);
  }, [setSessionId]);

  const switchSession = useCallback(
    async (id: string) => {
      if (id === sessionIdRef.current) return;
      try {
        const history = await getConversation(id);
        setMessages(
          history.messages.map((m) => ({
            id: makeId(),
            role: m.role,
            content: m.content,
            timestamp: m.ts ? new Date(m.ts) : new Date(),
          })),
        );
        setSessionId(id);
      } catch {
        /* API unreachable or session not found — leave the current chat as-is */
      }
    },
    [setSessionId],
  );

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
    sessionId,
    sendMessage,
    clearChat,
    switchSession,
    handleFeedback,
    isLoading: mutation.isPending,
    error: mutation.error,
  };
}
