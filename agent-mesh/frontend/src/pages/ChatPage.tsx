import React, { useEffect, useRef, useState } from "react";
import { Send, Trash2, Bot } from "lucide-react";
import { useAuth } from "@/contexts/AuthContext";
import { useChat } from "@/hooks/useChat";
import MessageBubble from "@/components/chat/MessageBubble";
import { Button } from "@/components/ui/Button";
import { cn } from "@/lib/utils";
import { SAMPLE_QUERIES } from "@/config/constants";

export default function ChatPage() {
  const { user } = useAuth();
  const username = user?.username ?? "bob";

  const { messages, sendMessage, clearChat, isLoading } = useChat({ username });

  const [input, setInput] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Auto-scroll to latest message
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  function handleSubmit(e?: React.FormEvent) {
    e?.preventDefault();
    const trimmed = input.trim();
    if (!trimmed || isLoading) return;
    sendMessage(trimmed);
    setInput("");
    setTimeout(() => textareaRef.current?.focus(), 50);
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
      e.preventDefault();
      handleSubmit();
    }
  }

  const isEmpty = messages.length === 0;

  return (
    <div className="flex flex-col h-full max-h-[calc(100vh-64px)]">
      {/* Header bar */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-line bg-surface/50 backdrop-blur-sm shrink-0">
        <div className="flex items-center gap-2">
          <Bot className="h-5 w-5 text-brand-500" />
          <h1 className="font-semibold text-fg text-sm">Agent Mesh</h1>
          {user && (
            <span className="text-xs text-muted ml-1">
              — signed in as{" "}
              <span className="font-medium text-fg">{user.display_name}</span>
              {user.role && (
                <RoleBadge role={user.role} />
              )}
            </span>
          )}
        </div>
        {!isEmpty && (
          <button
            onClick={clearChat}
            className="flex items-center gap-1.5 text-xs text-muted hover:text-fg transition-colors"
          >
            <Trash2 className="h-3.5 w-3.5" />
            Clear
          </button>
        )}
      </div>

      {/* Message list */}
      <div className="flex-1 overflow-y-auto px-4 py-4 space-y-1">
        {isEmpty ? (
          <EmptyState onSampleClick={(q) => { setInput(q); textareaRef.current?.focus(); }} />
        ) : (
          messages.map((msg) => (
            <MessageBubble key={msg.id} message={msg} />
          ))
        )}
        <div ref={bottomRef} />
      </div>

      {/* Input area */}
      <div className="shrink-0 border-t border-line bg-surface px-4 py-3">
        <form onSubmit={handleSubmit} className="flex items-end gap-2">
          <textarea
            ref={textareaRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Ask anything… (Ctrl+Enter to send)"
            rows={2}
            disabled={isLoading}
            className={cn(
              "flex-1 resize-none rounded-xl border border-line bg-canvas px-3 py-2.5",
              "text-sm text-fg placeholder:text-muted",
              "focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-brand-500",
              "disabled:opacity-60 transition-colors"
            )}
          />
          <Button
            type="submit"
            disabled={!input.trim() || isLoading}
            loading={isLoading}
            className="shrink-0 h-[52px] px-4"
          >
            <Send className="h-4 w-4" />
          </Button>
        </form>
        <p className="text-xs text-muted mt-1.5 text-right">
          Ctrl+Enter to send
        </p>
      </div>
    </div>
  );
}

// ── Role badge ──────────────────────────────────────────────────────────────

const ROLE_COLORS: Record<string, string> = {
  relationship_manager:       "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300",
  credit_officer:             "bg-blue-100 text-blue-800 dark:bg-blue-900/40 dark:text-blue-300",
  compliance_officer:         "bg-teal-100 text-teal-800 dark:bg-teal-900/40 dark:text-teal-300",
  branch_operations_officer:  "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300",
  operations_manager:         "bg-purple-100 text-purple-800 dark:bg-purple-900/40 dark:text-purple-300",
  platform_administrator:     "bg-rose-100 text-rose-800 dark:bg-rose-900/40 dark:text-rose-300",
  customer:                   "bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-300",
};

function RoleBadge({ role }: { role: string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center ml-1.5 px-1.5 py-0.5 rounded text-xs font-medium",
        ROLE_COLORS[role] ?? "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300"
      )}
    >
      {role.replace(/_/g, " ")}
    </span>
  );
}

// ── Empty state / sample queries ─────────────────────────────────────────────

interface EmptyStateProps {
  onSampleClick: (q: string) => void;
}

function EmptyState({ onSampleClick }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center h-full py-16 px-4 text-center">
      <div className="w-16 h-16 rounded-2xl bg-brand-100 dark:bg-brand-900/30 flex items-center justify-center mb-4">
        <Bot className="h-8 w-8 text-brand-600 dark:text-brand-400" />
      </div>
      <h2 className="text-lg font-semibold text-fg mb-1">FAB Pricing Assistant</h2>
      <p className="text-sm text-muted max-w-sm mb-8">
        Queries flow through a 5-stage pipeline: guardrail → RBAC → compliance →
        PriceAssist (data + knowledge) → redaction.
      </p>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 w-full max-w-lg">
        {SAMPLE_QUERIES.map((item) => (
          <button
            key={item.query}
            onClick={() => onSampleClick(item.query)}
            className={cn(
              "text-left px-3 py-2.5 rounded-xl border border-line bg-surface",
              "hover:border-brand-400 hover:bg-brand-50 dark:hover:bg-brand-900/20",
              "transition-colors text-xs group"
            )}
          >
            <span className="block font-medium text-fg group-hover:text-brand-600 dark:group-hover:text-brand-400 mb-0.5">
              {item.label}
            </span>
            <span className="text-muted truncate block">{item.query}</span>
          </button>
        ))}
      </div>
    </div>
  );
}
