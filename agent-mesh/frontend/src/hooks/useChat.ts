import { useCallback, useEffect, useRef, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { getConversation, queryMesh } from "@/api/mesh";
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
}

export function useChat({ username }: UseChatOptions) {
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
        sessionIdRef.current = result.session_id;
        writeSessionId(result.session_id);
      }
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
    // starts a fresh conversation server-side.
    setMessages([]);
    sessionIdRef.current = null;
    writeSessionId(null);
  }, []);

  return {
    messages,
    sendMessage,
    clearChat,
    isLoading: mutation.isPending,
    error: mutation.error,
  };
}
