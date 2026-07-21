import { useState, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  MessagesSquare,
  RefreshCw,
  Search,
  ChevronDown,
  ChevronUp,
} from "lucide-react";
import { getConversations } from "@/api/mesh";
import { Metric } from "@/components/ui/Metric";
import { Badge } from "@/components/ui/Badge";
import { CenteredSpinner } from "@/components/ui/Spinner";
import { cn } from "@/lib/utils";
import MessageBubble from "@/components/chat/MessageBubble";
import type { SessionSummary, SessionMessage, ChatMessage } from "@/types/mesh";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function timeAgo(isoTs: string): string {
  if (!isoTs) return "";
  const diff = Date.now() - new Date(isoTs).getTime();
  const mins = Math.floor(diff / 60_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

function formatTs(isoTs: string): string {
  if (!isoTs) return "";
  return new Date(isoTs).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

function avatarInitial(u: string) { return u.charAt(0).toUpperCase(); }

const AVATAR_COLORS = [
  "bg-brand-500 text-white", "bg-emerald-500 text-white",
  "bg-amber-500 text-white", "bg-violet-500 text-white", "bg-rose-500 text-white",
];
function avatarColor(u: string) {
  let h = 0;
  for (let i = 0; i < u.length; i++) h = (h * 31 + u.charCodeAt(i)) & 0xffff;
  return AVATAR_COLORS[h % AVATAR_COLORS.length];
}

// ---------------------------------------------------------------------------
// Adapter: SessionMessage → ChatMessage (for MessageBubble reuse)
// ---------------------------------------------------------------------------

function toRichMessage(m: SessionMessage, idx: number): ChatMessage {
  return {
    id: m.request_id ?? `hist-${idx}`,
    role: m.role,
    content: m.content,
    timestamp: m.ts ? new Date(m.ts) : new Date(0),
    isLoading: false,
    result: m.role === "assistant" && (m.route || m.trail?.length)
      ? {
          answer: m.content,
          blocked: m.blocked ?? false,
          block_stage: null,
          trail: m.trail ?? [],
          request_id: m.request_id,
          route: m.route ?? null,
          domain: m.domain ?? null,
          total_duration_ms: m.duration_ms,
          events: m.trace ?? [],
          llm_reasoning: m.reasoning ?? [],
        }
      : undefined,
  };
}

// ---------------------------------------------------------------------------
// Session card
// ---------------------------------------------------------------------------

function SessionCard({ session }: { session: SessionSummary }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="rounded-xl border border-line bg-surface shadow-sm overflow-hidden">
      {/* Header */}
      <div className="flex items-start justify-between gap-3 px-4 py-3 border-b border-line">
        <div className="flex items-center gap-2.5 min-w-0">
          <div className={cn(
            "flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-sm font-bold",
            avatarColor(session.user),
          )}>
            {avatarInitial(session.user)}
          </div>
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="text-sm font-semibold text-fg">{session.user}</span>
              <Badge tone="brand">{session.message_count} messages</Badge>
            </div>
            <p className="mt-0.5 text-xs text-faint truncate max-w-[300px]" title={session.session_id}>
              {session.session_id}
            </p>
          </div>
        </div>

        {/* Time range */}
        <div className="shrink-0 text-right">
          <p className="text-xs text-fg font-medium" title={formatTs(session.last_ts)}>
            {timeAgo(session.last_ts)}
          </p>
          {session.first_ts !== session.last_ts && (
            <p className="text-[10px] text-faint mt-0.5" title={formatTs(session.first_ts)}>
              started {timeAgo(session.first_ts)}
            </p>
          )}
        </div>
      </div>

      {/* Body */}
      <div className="px-4 py-3 space-y-2.5">
        {/* First query preview */}
        {session.first_query && (
          <p className="text-sm italic text-muted line-clamp-1">
            "{session.first_query}"
          </p>
        )}

        {/* Expand toggle */}
        <button
          onClick={() => setExpanded(v => !v)}
          className="flex items-center gap-1.5 text-xs font-medium text-brand-600 dark:text-brand-400 hover:underline"
        >
          {expanded ? (
            <><ChevronUp className="h-3.5 w-3.5" /> Hide conversation</>
          ) : (
            <><ChevronDown className="h-3.5 w-3.5" /> View conversation</>
          )}
        </button>

        {/* Expanded chat thread */}
        {expanded && (
          <div className="rounded-xl border border-line bg-surface-2 p-4 space-y-4 max-h-[600px] overflow-y-auto">
            {session.messages.map((msg, i) => (
              <MessageBubble key={i} message={toRichMessage(msg, i)} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function ConversationsDashboardPage() {
  const { data, isLoading, isError, refetch, isFetching } = useQuery({
    queryKey: ["conversations-list"],
    queryFn: getConversations,
    refetchInterval: 30_000,
  });

  const [search, setSearch] = useState("");

  const filtered = useMemo(() => {
    if (!data) return [];
    if (!search) return data.sessions;
    const q = search.toLowerCase();
    return data.sessions.filter(s =>
      s.user.toLowerCase().includes(q) ||
      s.session_id.toLowerCase().includes(q) ||
      s.first_query.toLowerCase().includes(q),
    );
  }, [data, search]);

  const avgMessages = !data || data.total_sessions === 0
    ? 0
    : Math.round(data.total_messages / data.total_sessions);

  return (
    <div className="space-y-6 px-4 py-6 sm:px-6 lg:px-8 max-w-5xl mx-auto w-full">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-fg">Conversations</h1>
          <p className="text-sm text-muted mt-0.5">
            Full session snapshots — responses, execution trace, and AI reasoning preserved for each conversation
          </p>
        </div>
        <button
          onClick={() => refetch()}
          disabled={isFetching}
          className="flex items-center gap-1.5 text-sm text-muted hover:text-fg transition-colors disabled:opacity-50"
        >
          <RefreshCw className={cn("h-4 w-4", isFetching && "animate-spin")} />
          Refresh
        </button>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
        <Metric
          label="Total Sessions"
          value={isLoading ? "—" : String(data?.total_sessions ?? 0)}
          tone="default"
        />
        <Metric
          label="Total Messages"
          value={isLoading ? "—" : String(data?.total_messages ?? 0)}
          tone="default"
        />
        <Metric
          label="Unique Users"
          value={isLoading ? "—" : String(data?.unique_users ?? 0)}
          tone="default"
        />
        <Metric
          label="Avg Msg / Session"
          value={isLoading ? "—" : String(avgMessages)}
          tone="default"
          hint="messages per session"
        />
      </div>

      {/* Search */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="relative flex-1 min-w-[200px]">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-faint pointer-events-none" />
          <input
            type="text"
            placeholder="Search user, session ID, or first query…"
            value={search}
            onChange={e => setSearch(e.target.value)}
            className="w-full rounded-lg border border-line bg-surface pl-9 pr-3 py-2 text-sm text-fg placeholder:text-faint focus:outline-none focus:ring-2 focus:ring-brand-500/50"
          />
        </div>
        {!isLoading && data && (
          <span className="text-xs text-muted ml-auto">
            Showing {filtered.length} of {data.total_sessions}
          </span>
        )}
      </div>

      {/* Content */}
      {isLoading ? (
        <CenteredSpinner />
      ) : isError ? (
        <div className="rounded-xl border border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-950/30 p-5 text-sm text-red-700 dark:text-red-400">
          Failed to load conversations. Make sure the API server is running.
        </div>
      ) : filtered.length === 0 ? (
        <div className="flex flex-col items-center justify-center gap-3 py-20 text-center">
          <MessagesSquare className="h-10 w-10 text-faint" />
          <p className="text-base font-medium text-fg">
            {data?.total_sessions === 0 ? "No conversations yet" : "No sessions match your search"}
          </p>
          <p className="text-sm text-muted">
            {data?.total_sessions === 0
              ? "Sessions will appear here after users chat with Price Assist."
              : "Try a different search term."}
          </p>
        </div>
      ) : (
        <div className="space-y-4">
          {filtered.map(s => <SessionCard key={s.session_id} session={s} />)}
        </div>
      )}
    </div>
  );
}
