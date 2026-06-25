import { useEffect, useState } from "react";
import { cn } from "@/lib/utils";
import { Markdown } from "@/components/ui/Markdown";
import PipelineTrail from "./PipelineTrail";
import SecurityBadge from "./SecurityBadge";
import ExecutionPanel from "./ExecutionPanel";
import type { ChatMessage } from "@/types/mesh";

// Cycles through pipeline stages shown during loading
const LOADING_STAGES = [
  { label: "Running input guardrails…",     delay: 0 },
  { label: "Routing query to agents…",      delay: 1800 },
  { label: "Checking access control…",      delay: 3600 },
  { label: "Verifying compliance…",         delay: 5400 },
  { label: "Querying domain agents…",       delay: 7200 },
  { label: "Waiting for response…",         delay: 9500 },
  { label: "Redacting output…",             delay: 12000 },
];

function ThinkingIndicator() {
  const [stageIdx, setStageIdx] = useState(0);
  const [visible, setVisible] = useState(true);

  useEffect(() => {
    const timers: ReturnType<typeof setTimeout>[] = [];

    LOADING_STAGES.forEach((stage, idx) => {
      if (idx === 0) return;
      timers.push(
        setTimeout(() => {
          setVisible(false);
          setTimeout(() => {
            setStageIdx(idx);
            setVisible(true);
          }, 200);
        }, stage.delay),
      );
    });

    return () => timers.forEach(clearTimeout);
  }, []);

  const stage = LOADING_STAGES[stageIdx];

  return (
    <div className="flex items-center gap-2.5 py-1 text-muted">
      {/* Three-dot pulse */}
      <span className="flex items-center gap-1">
        {[0, 1, 2].map((i) => (
          <span
            key={i}
            className="h-2 w-2 rounded-full bg-brand-400 dark:bg-brand-500"
            style={{
              animation: `pulse 1.2s ease-in-out ${i * 0.2}s infinite`,
            }}
          />
        ))}
      </span>
      <span
        className={cn(
          "text-sm transition-opacity duration-200",
          visible ? "opacity-100" : "opacity-0",
        )}
      >
        {stage.label}
      </span>
    </div>
  );
}

interface MessageBubbleProps {
  message: ChatMessage;
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

export default function MessageBubble({ message }: MessageBubbleProps) {
  const isUser = message.role === "user";

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
            {formatTime(message.timestamp)}
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
            <ThinkingIndicator />
          ) : (
            <>
              {/* Security blocked indicator */}
              {isBlocked && result && (
                <SecurityBadge blockStage={result.block_stage} />
              )}

              {/* Answer text */}
              {message.content && (
                <div className="text-sm leading-relaxed prose-sm">
                  <Markdown>{message.content}</Markdown>
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
                  {result.confidence != null && (
                    <span className="inline-flex items-center text-xs px-2 py-0.5 rounded-full bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-400 font-medium">
                      {Math.round(result.confidence * 100)}% conf
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
              {result && !isBlocked && !result.route && !result.domain && result.trail.length > 0 && (() => {
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

              {/* Pipeline trail */}
              {result && result.trail.length > 0 && (
                <div className="mt-3 pt-3 border-t border-line">
                  <PipelineTrail trail={result.trail} blocked={isBlocked} blockStage={result.block_stage} />
                </div>
              )}

              {/* Execution trace panel — collapsible, mirrors run.py CLIRenderer output */}
              {result && <ExecutionPanel result={result} />}
            </>
          )}
        </div>

        <p className="text-xs text-muted mt-1 pl-1">
          {formatTime(message.timestamp)}
        </p>
      </div>
    </div>
  );
}
