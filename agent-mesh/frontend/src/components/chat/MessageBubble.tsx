import { memo, useMemo, useEffect, useRef, useState } from "react";
import { ThumbsUp, ThumbsDown, Zap, RefreshCw } from "lucide-react";
import { cn } from "@/lib/utils";
import { Markdown } from "@/components/ui/Markdown";
import PipelineTrail from "./PipelineTrail";
import SecurityBadge from "./SecurityBadge";
import ExecutionPanel from "./ExecutionPanel";
import type { ChatMessage } from "@/types/mesh";

const FALLBACK_STAGE = "Processing…";

function ThinkingIndicator({ currentStage }: { currentStage?: string }) {
  const [visible, setVisible] = useState(true);
  const prevStage = useRef<string | undefined>(undefined);

  // Fade out → in whenever the stage label changes
  useEffect(() => {
    if (currentStage === prevStage.current) return;
    prevStage.current = currentStage;
    setVisible(false);
    const t = setTimeout(() => setVisible(true), 150);
    return () => clearTimeout(t);
  }, [currentStage]);

  return (
    <div className="flex items-center gap-2.5 py-1 text-muted">
      {/* Three-dot pulse */}
      <span className="flex items-center gap-1">
        {[0, 1, 2].map((i) => (
          <span
            key={i}
            className="h-2 w-2 rounded-full bg-brand-400 dark:bg-brand-500"
            style={{ animation: `pulse 1.2s ease-in-out ${i * 0.2}s infinite` }}
          />
        ))}
      </span>
      <span
        className={cn(
          "text-sm transition-opacity duration-150",
          visible ? "opacity-100" : "opacity-0",
        )}
      >
        {currentStage ?? FALLBACK_STAGE}
      </span>
    </div>
  );
}

// ── Feedback bar ─────────────────────────────────────────────────────────────

interface FeedbackBarProps {
  messageId: string;
  existing?: { rating: "up" | "down"; comment?: string };
  onSubmit: (messageId: string, rating: "up" | "down", comment?: string) => Promise<void>;
}

function FeedbackBar({ messageId, existing, onSubmit }: FeedbackBarProps) {
  const [selected, setSelected] = useState<"up" | "down" | null>(existing?.rating ?? null);
  const [comment, setComment] = useState(existing?.comment ?? "");
  const [submitted, setSubmitted] = useState(!!existing);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const locked = submitted || isSubmitting;

  function handleThumb(rating: "up" | "down") {
    if (locked) return;
    setSelected((prev) => (prev === rating ? null : rating));
  }

  async function handleSubmit() {
    if (!selected || locked) return;
    setIsSubmitting(true);
    try {
      await onSubmit(messageId, selected, comment.trim() || undefined);
      setSubmitted(true);
    } catch {
      // Silent fail — feedback is best-effort; don't break the chat UI.
    } finally {
      setIsSubmitting(false);
    }
  }

  if (submitted) {
    return (
      <div className="flex items-center gap-2 mt-3 pt-2 border-t border-line">
        {selected === "up" ? (
          <ThumbsUp className="h-3.5 w-3.5 text-emerald-500" />
        ) : (
          <ThumbsDown className="h-3.5 w-3.5 text-red-400" />
        )}
        <span className="text-xs text-muted">Thanks for your feedback</span>
      </div>
    );
  }

  return (
    <div className="mt-3 pt-2 border-t border-line">
      <div className="flex items-center gap-2">
        <span className="text-xs text-muted mr-1">Was this helpful?</span>
        <button
          onClick={() => handleThumb("up")}
          disabled={locked}
          aria-label="Thumbs up"
          className={cn(
            "p-1 rounded transition-colors",
            selected === "up"
              ? "text-emerald-500"
              : "text-muted hover:text-emerald-500"
          )}
        >
          <ThumbsUp className="h-3.5 w-3.5" />
        </button>
        <button
          onClick={() => handleThumb("down")}
          disabled={locked}
          aria-label="Thumbs down"
          className={cn(
            "p-1 rounded transition-colors",
            selected === "down"
              ? "text-red-400"
              : "text-muted hover:text-red-400"
          )}
        >
          <ThumbsDown className="h-3.5 w-3.5" />
        </button>
      </div>

      {selected && (
        <div className="mt-2 flex flex-col gap-1.5">
          <textarea
            ref={textareaRef}
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            placeholder="Add a comment (optional)"
            rows={2}
            disabled={locked}
            className={cn(
              "w-full resize-none rounded-lg border border-line bg-canvas px-2.5 py-1.5",
              "text-xs text-fg placeholder:text-muted",
              "focus:outline-none focus:ring-1 focus:ring-brand-500 focus:border-brand-500",
              "disabled:opacity-60 transition-colors"
            )}
          />
          <div className="flex justify-end">
            <button
              onClick={handleSubmit}
              disabled={locked}
              className={cn(
                "text-xs px-3 py-1 rounded-lg font-medium transition-colors",
                "bg-brand-600 text-white hover:bg-brand-700 disabled:opacity-60"
              )}
            >
              {isSubmitting ? "Submitting…" : "Submit"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────

interface MessageBubbleProps {
  message: ChatMessage;
  onFeedback?: (messageId: string, rating: "up" | "down", comment?: string) => Promise<void>;
  onRefresh?: (messageId: string) => void;
}

function formatTime(date: Date): string {
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

// Route chip coloured by service type
function RouteChip({ route }: { route: string }) {
  const lower = route.toLowerCase();
  let cls = "bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-400";
  if (lower.includes("hybrid")) {
    cls = "bg-teal-100 dark:bg-teal-900/40 text-teal-700 dark:text-teal-300";
  } else if (lower.includes("rag")) {
    cls = "bg-violet-100 dark:bg-violet-900/40 text-violet-700 dark:text-violet-300";
  } else if (lower.includes("data")) {
    cls = "bg-blue-100 dark:bg-blue-900/40 text-blue-700 dark:text-blue-300";
  }
  return (
    <span className={cn("inline-flex items-center text-xs px-2 py-0.5 rounded-full font-medium", cls)}>
      {route}
    </span>
  );
}

const LLM_REASONING_RE = /<llm_reasoning>[\s\S]*?<\/llm_reasoning>/g;

const MessageBubble = memo(function MessageBubble({ message, onFeedback, onRefresh }: MessageBubbleProps) {
  const isUser = message.role === "user";
  const timeLabel = useMemo(() => formatTime(message.timestamp), [message.timestamp]);
  const safeContent = useMemo(
    () => message.content.replace(LLM_REASONING_RE, "").trim(),
    [message.content],
  );

  if (isUser) {
    return (
      <div className="flex justify-end mb-4">
        <div className="max-w-[75%]">
          <div
            className={cn(
              "rounded-2xl rounded-tr-sm px-4 py-3",
              "bg-brand-600 text-white shadow-sm"
            )}
          >
            <p className="whitespace-pre-wrap break-words text-sm leading-relaxed">
              {message.content}
            </p>
          </div>
          <p className="text-xs text-muted mt-1 text-right pr-1">
            {timeLabel}
          </p>
        </div>
      </div>
    );
  }

  // Assistant message
  const result = message.result;
  const isBlocked = result?.blocked ?? false;

  return (
    <div className="flex justify-start mb-4">
      <div className="max-w-[80%] w-full">
        {/* Bubble */}
        <div
          className={cn(
            "rounded-2xl rounded-tl-sm px-4 py-3 shadow-sm",
            isBlocked
              ? "bg-red-50 dark:bg-red-950/30 border border-red-200 dark:border-red-800"
              : "bg-surface border border-line"
          )}
        >
          {message.isLoading ? (
            <>
              <ThinkingIndicator currentStage={message.streamingStage} />
              {/* Execution panel visible during streaming so stages + reasoning appear live */}
              {!!(message.streamingEvents?.length || message.streamingReasoning?.length) && (
                <ExecutionPanel
                  liveEvents={message.streamingEvents}
                  liveReasoning={message.streamingReasoning}
                />
              )}
            </>
          ) : (
            <>
              {/* Security blocked indicator */}
              {isBlocked && result && (
                <SecurityBadge blockStage={result.block_stage} />
              )}

              {/* Cache hit banner */}
              {result?.cache_hit && (
                <div className="mb-3 px-3 py-2 rounded-lg bg-amber-50 dark:bg-amber-950/30 border border-amber-200 dark:border-amber-800/50">
                  {/* Row 1: icon + label + age + match % */}
                  <div className="flex items-center gap-2">
                    <Zap className="h-3.5 w-3.5 text-amber-500 shrink-0" />
                    <span className="text-xs font-medium text-amber-700 dark:text-amber-300">
                      Served from semantic cache
                    </span>
                    {result.cache_age_hours != null && (
                      <span className="text-xs text-amber-600 dark:text-amber-400">
                        · {result.cache_age_hours < 1
                            ? `${Math.round(result.cache_age_hours * 60)}m ago`
                            : `${result.cache_age_hours.toFixed(1)}h ago`}
                      </span>
                    )}
                    {result.cache_similarity != null && (
                      <span className="text-xs text-amber-500 dark:text-amber-500 ml-auto font-mono">
                        {(result.cache_similarity * 100).toFixed(0)}% match
                      </span>
                    )}
                  </div>
                  {/* Row 2: LLM judge reason — only shown when judge was invoked */}
                  {result.cache_judge_invoked && result.cache_judge_reason && (
                    <div className="flex items-center gap-1.5 mt-1.5 pt-1.5 border-t border-amber-200/60 dark:border-amber-800/30">
                      <span className="text-[10px] font-semibold uppercase tracking-wide text-amber-500 dark:text-amber-400 shrink-0">
                        LLM Judge
                      </span>
                      <span className="text-[11px] text-amber-600 dark:text-amber-400 italic">
                        {result.cache_judge_reason}
                      </span>
                    </div>
                  )}
                  {/* Row 3: Get updated answer — runs the full pipeline, bypassing cache */}
                  {onRefresh && (
                    <div className="flex items-center mt-1.5 pt-1.5 border-t border-amber-200/60 dark:border-amber-800/30">
                      <button
                        onClick={() => onRefresh(message.id)}
                        className="flex items-center gap-1.5 text-[11px] text-amber-600 dark:text-amber-400 hover:text-amber-800 dark:hover:text-amber-200 transition-colors"
                      >
                        <RefreshCw className="h-3 w-3" />
                        Get updated answer
                      </button>
                      <span className="ml-2 text-[10px] text-amber-400 dark:text-amber-600">
                        runs full pipeline, skips cache
                      </span>
                    </div>
                  )}
                </div>
              )}

              {/* Answer text */}
              {safeContent && (
                <div className="text-sm leading-relaxed prose-sm">
                  <Markdown>{safeContent}</Markdown>
                </div>
              )}

              {/* Route chip + execution meta (from tracer summary) */}
              {result && !isBlocked && (result.route || result.domain || result.execution_path?.length) && (
                <div className="flex flex-wrap items-center gap-1.5 mt-3">
                  {result.route && <RouteChip route={result.route} />}
                  {result.domain && (
                    <span className="inline-flex items-center text-xs px-2 py-0.5 rounded-full bg-brand-100 dark:bg-brand-900/40 text-brand-700 dark:text-brand-300 font-medium">
                      {result.domain}
                    </span>
                  )}
                  {result.total_duration_ms != null && (
                    <span className="inline-flex items-center text-xs px-2 py-0.5 rounded-full bg-slate-100 dark:bg-slate-800 text-slate-500 dark:text-slate-500">
                      {(result.total_duration_ms / 1000).toFixed(1)}s
                    </span>
                  )}
                  {result.execution_path && result.execution_path.length > 0 && (
                    <span className="text-xs text-muted">
                      {result.execution_path.join(" → ")}
                    </span>
                  )}
                </div>
              )}

              {/* Fallback domain chip from trail when tracer data is absent */}
              {result && !isBlocked && !result.route && !result.domain && result.trail.length > 0 &&
                (() => {
                  const domainStep = result.trail.find((t) => t.startsWith("domain_answer:"));
                  const node = domainStep?.split(":")[1];
                  if (!node) return null;
                  return (
                    <div className="flex flex-wrap gap-1.5 mt-3">
                      <span className="inline-flex items-center text-xs px-2 py-0.5 rounded-full bg-brand-100 dark:bg-brand-900/40 text-brand-700 dark:text-brand-300 font-medium">
                        {node.replace(/_/g, " ")}
                      </span>
                    </div>
                  );
                })()}

              {/* Session / Request ID chips — always visible metadata */}
              {result && (result.session_id || result.request_id) && (
                <div className="flex flex-wrap gap-2 mt-3 pt-2 border-t border-line">
                  {result.session_id && (
                    <span className="inline-flex items-center gap-1 text-[10px] font-mono text-muted bg-surface border border-line rounded px-1.5 py-0.5" title="Session ID — persistent conversation thread">
                      <span className="text-faint font-sans not-italic">session</span>
                      {result.session_id}
                    </span>
                  )}
                  {result.request_id && (
                    <span className="inline-flex items-center gap-1 text-[10px] font-mono text-muted bg-surface border border-line rounded px-1.5 py-0.5" title="Request ID — unique to this turn">
                      <span className="text-faint font-sans not-italic">req</span>
                      {result.request_id}
                    </span>
                  )}
                </div>
              )}

              {/* Pipeline trail */}
              {result && result.trail.length > 0 && (
                <div className="mt-3 pt-3 border-t border-line">
                  <PipelineTrail trail={result.trail} blocked={isBlocked} blockStage={result.block_stage} />
                </div>
              )}

              {/* Execution trace panel — switches from live events to final result on completion */}
              {(result || message.streamingEvents?.length || message.streamingReasoning?.length) && (
                <ExecutionPanel
                  result={result}
                  liveEvents={message.streamingEvents}
                  liveReasoning={message.streamingReasoning}
                />
              )}

              {/* Feedback bar — shown on non-blocked responses once loaded */}
              {onFeedback && result?.request_id && !isBlocked && (
                <FeedbackBar
                  messageId={message.id}
                  existing={message.feedback}
                  onSubmit={onFeedback}
                />
              )}
            </>
          )}
        </div>

        <p className="text-xs text-muted mt-1 pl-1">
          {timeLabel}
        </p>
      </div>
    </div>
  );
});

export default MessageBubble;
