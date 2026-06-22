import { useCallback, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { queryMesh } from "@/api/mesh";
import type { ChatMessage, MeshResult } from "@/types/mesh";

function makeId() {
  return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

interface UseChatOptions {
  username: string;
}

export function useChat({ username }: UseChatOptions) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);

  const mutation = useMutation({
    mutationFn: ({ query }: { query: string }) => queryMesh(username, query),
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
    onError: (error: Error, _vars, ctx) => {
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
                  domain: null,
                  domains: [],
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
    setMessages([]);
  }, []);

  return {
    messages,
    sendMessage,
    clearChat,
    isLoading: mutation.isPending,
    error: mutation.error,
  };
}
