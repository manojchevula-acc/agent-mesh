import { useCallback, useEffect, useRef, useState } from "react";
import { getConversation, queryMeshStream, resolveIntentDecision, submitFeedback } from "@/api/mesh";
import type { CandidateItem, ChatMessage, ExecutionEvent, LLMReasoningEntry, MeshResult, SessionMessage } from "@/types/mesh";

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

function toRestoredMessage(m: SessionMessage): ChatMessage {
  const base: ChatMessage = {
    id: makeId(),
    role: m.role,
    content: m.content,
    timestamp: m.ts ? new Date(m.ts) : new Date(),
  };
  if (m.role === "assistant" && (m.route || m.trail?.length || m.trace?.length || m.cache_hit || m.request_id)) {
    const result: MeshResult = {
      answer: m.content,
      blocked: m.blocked ?? false,
      block_stage: null,
      trail: m.trail ?? [],
      request_id: m.request_id,
      domain: m.domain,
      route: m.route,
      total_duration_ms: m.duration_ms,
      events: m.trace ?? [],
      llm_reasoning: m.reasoning ?? [],
      // Cache provenance — restores the amber banner on history load
      cache_hit: m.cache_hit,
      cache_age_hours: m.cache_age_hours,
      cache_similarity: m.cache_similarity,
      cache_judge_invoked: m.cache_judge_invoked,
      cache_judge_decision: m.cache_judge_decision,
      cache_judge_reason: m.cache_judge_reason,
    };
    return { ...base, result };
  }
  return base;
}

interface UseChatOptions {
  username: string;
  role: string;
  /** When set (from URL param), this session is loaded instead of localStorage. */
  initialSessionId?: string;
}

export function useChat({ username, role, initialSessionId }: UseChatOptions) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  // Holds the active conversation id. Persisted to localStorage so the thread
  // survives a page refresh; pinned by the first response that returns it.
  const sessionIdRef = useRef<string | null>(readSessionId());

  // Restore conversation history on mount (or when the URL session param changes).
  // If initialSessionId is provided (resume from dashboard), it overrides localStorage.
  // Otherwise falls back to whatever is stored in localStorage (existing behaviour).
  useEffect(() => {
    const sid = initialSessionId || readSessionId();
    if (!sid) return;

    // Pin this session so subsequent sends continue on the same thread.
    sessionIdRef.current = sid;
    writeSessionId(sid);

    // Clear stale messages before loading the new session's history.
    setMessages([]);

    let cancelled = false;
    getConversation(sid)
      .then((history) => {
        if (cancelled || history.messages.length === 0) return;
        setMessages(history.messages.map(toRestoredMessage));
      })
      .catch(() => {
        /* API unreachable or session not found — start fresh */
      });
    return () => {
      cancelled = true;
    };
  // Re-run when the URL session param changes (browser back/forward between sessions).
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialSessionId]);

  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const stopGeneration = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  const sendMessage = useCallback(
    (query: string) => {
      const trimmed = query.trim();
      if (!trimmed || isLoading) return;

      const assistantId = makeId();
      abortRef.current = new AbortController();
      setIsLoading(true);
      setError(null);

      setMessages((prev) => [
        ...prev,
        { id: makeId(), role: "user" as const, content: trimmed, timestamp: new Date() },
        { id: assistantId, role: "assistant" as const, content: "", isLoading: true, timestamp: new Date() },
      ]);

      (async () => {
        try {
          const stream = queryMeshStream(username, trimmed, sessionIdRef.current ?? undefined, undefined, abortRef.current!.signal);
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
            } else if (event.type === "hitl") {
              // Store details in localStorage so the new tab can read them instantly
              // (same origin = shared storage; avoids any API race condition)
              try {
                localStorage.setItem(
                  `hitl-approval-${event.approval_id}`,
                  JSON.stringify({ approval_id: event.approval_id, ...event.details })
                );
              } catch {
                // ignore storage quota errors
              }
              // Open approval page in a new tab — future: replace with an email link
              window.open(`/approval/${event.approval_id}`, "_blank");
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === assistantId
                    ? { ...m, streamingStage: "Awaiting human approval in new tab…" }
                    : m
                )
              );
            } else if (event.type === "intent_suggestion") {
              // Build multi-candidate list; fall back to wrapping top-1 for backward compat
              const rawCandidates = event.candidates?.length
                ? event.candidates
                : [{ entry_id: event.entry_id, root_query: event.root_query,
                     similarity: event.similarity, age_hours: event.age_hours,
                     answer_preview: event.answer_preview, confidence: event.confidence }];
              const candidates: CandidateItem[] = rawCandidates.map((c) => ({
                entryId: c.entry_id,
                rootQuery: c.root_query,
                similarity: c.similarity,
                ageHours: c.age_hours,
                answerPreview: c.answer_preview,
                confidence: c.confidence,
                judgeVerdict: null,
                judgeReason: null,
              }));
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === assistantId
                    ? {
                        ...m,
                        intentSuggestion: { primaryEntryId: event.entry_id, candidates },
                        streamingStage: "Waiting for your confirmation…",
                      }
                    : m
                )
              );
            } else if (event.type === "intent_suggestion_judge") {
              // LLM judge result — update the matching candidate row in-place
              setMessages((prev) =>
                prev.map((m) => {
                  if (m.id !== assistantId || !m.intentSuggestion) return m;
                  const updatedCandidates = m.intentSuggestion.candidates.map((c) =>
                    c.entryId === event.entry_id
                      ? { ...c, judgeVerdict: event.judge_verdict, judgeReason: event.judge_reason }
                      : c
                  );
                  return { ...m, intentSuggestion: { ...m.intentSuggestion, candidates: updatedCandidates } };
                })
              );
            } else if (event.type === "cache_context") {
              // HIT zone: informational strip shown below the final answer
              const candidates: CandidateItem[] = event.candidates.map((c) => ({
                entryId: c.entry_id,
                rootQuery: c.root_query,
                similarity: c.similarity,
                ageHours: c.age_hours,
                answerPreview: c.answer_preview,
                confidence: c.confidence,
              }));
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === assistantId ? { ...m, cacheContext: candidates } : m
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
                    ? { ...m, content: result.answer, result, isLoading: false, streamingStage: undefined, intentSuggestion: undefined, timestamp: new Date() }
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
          // AbortError = user clicked Stop — settle the message silently
          if (err instanceof DOMException && err.name === "AbortError") {
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId
                  ? { ...m, isLoading: false, streamingStage: undefined }
                  : m
              )
            );
          } else {
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
          }
        } finally {
          setIsLoading(false);
        }
      })();
    },
    [isLoading, username]
  );

  // Re-runs the query for a cached assistant message with bypass_cache=true,
  // replacing that message in-place with the fresh pipeline response.
  // The preceding user message content is used as the query.
  const refreshAnswer = useCallback(
    (messageId: string) => {
      if (isLoading) return;

      const msgIndex = messages.findIndex((m) => m.id === messageId);
      if (msgIndex === -1) return;
      const userMsg = msgIndex > 0 ? messages[msgIndex - 1] : null;
      const query = userMsg?.content?.trim();
      if (!query) return;

      setIsLoading(true);
      setError(null);

      // Reset the target message to loading state, keeping its id stable.
      setMessages((prev) =>
        prev.map((m) =>
          m.id === messageId
            ? { ...m, content: "", result: undefined, isLoading: true, streamingStage: undefined, streamingEvents: undefined, streamingReasoning: undefined, timestamp: new Date() }
            : m
        )
      );

      abortRef.current = new AbortController();

      (async () => {
        try {
          const stream = queryMeshStream(username, query, sessionIdRef.current ?? undefined, true, abortRef.current!.signal);
          for await (const event of stream) {
            if (event.type === "stage") {
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === messageId
                    ? {
                        ...m,
                        streamingStage: event.message ? `${event.message}` : event.stage,
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
                  m.id === messageId
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
                  m.id === messageId
                    ? { ...m, content: result.answer, result, isLoading: false, streamingStage: undefined, timestamp: new Date() }
                    : m
                )
              );
            } else if (event.type === "error") {
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === messageId
                    ? { ...m, content: event.message || "Failed to refresh answer.", isLoading: false, streamingStage: undefined }
                    : m
                )
              );
            }
          }
        } catch (err) {
          if (err instanceof DOMException && err.name === "AbortError") {
            setMessages((prev) =>
              prev.map((m) =>
                m.id === messageId
                  ? { ...m, isLoading: false, streamingStage: undefined }
                  : m
              )
            );
          } else {
            setError(err instanceof Error ? err : new Error(String(err)));
            setMessages((prev) =>
              prev.map((m) =>
                m.id === messageId
                  ? { ...m, content: "Failed to reach the mesh.", isLoading: false, streamingStage: undefined }
                  : m
              )
            );
          }
        } finally {
          setIsLoading(false);
        }
      })();
    },
    [isLoading, messages, username]
  );

  // Called when the user clicks "Use cached answer" or "Run fresh" in the
  // IntentSuggestionBanner. Clears the banner optimistically and unblocks the
  // paused orchestrator via POST /api/cache/intent-decision.
  // Called when the user clicks "Use this answer" (accepted=true, chosenEntryId=candidate)
  // or "Run fresh" (accepted=false, chosenEntryId=primaryEntryId).
  const resolveIntentSuggestion = useCallback(
    async (messageId: string, chosenEntryId: string, accepted: boolean) => {
      const msg = messages.find((m) => m.id === messageId);
      if (!msg?.intentSuggestion) return;
      const primaryEntryId = msg.intentSuggestion.primaryEntryId;
      // Optimistic UI update — clear banner, update stage label
      setMessages((prev) =>
        prev.map((m) =>
          m.id === messageId
            ? {
                ...m,
                intentSuggestion: undefined,
                streamingStage: accepted ? "Loading cached answer…" : "Running full pipeline…",
              }
            : m
        )
      );
      try {
        await resolveIntentDecision(primaryEntryId, chosenEntryId, accepted);
      } catch {
        // Network error — the orchestrator will timeout (60s) and treat as rejected
      }
    },
    [messages]
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
    refreshAnswer,
    resolveIntentSuggestion,
    stopGeneration,
    clearChat,
    handleFeedback,
    isLoading,
    error,
  };
}
