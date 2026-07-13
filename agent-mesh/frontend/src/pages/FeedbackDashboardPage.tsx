import { useState, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  ThumbsUp,
  ThumbsDown,
  MessageSquare,
  RefreshCw,
  ChevronDown,
  ChevronUp,
  Search,
} from "lucide-react";
import { getFeedback } from "@/api/mesh";
import { Card, CardHeader, CardBody } from "@/components/ui/Card";
import { Metric } from "@/components/ui/Metric";
import { Badge } from "@/components/ui/Badge";
import { CenteredSpinner } from "@/components/ui/Spinner";
import { cn } from "@/lib/utils";
import type { FeedbackRecord } from "@/types/mesh";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function timeAgo(isoTs: string): string {
  const diff = Date.now() - new Date(isoTs).getTime();
  const mins = Math.floor(diff / 60_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

function formatTs(isoTs: string): string {
  return new Date(isoTs).toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

function avatarInitial(username: string): string {
  return username.charAt(0).toUpperCase();
}

const AVATAR_COLORS = [
  "bg-brand-500 text-white",
  "bg-emerald-500 text-white",
  "bg-amber-500 text-white",
  "bg-violet-500 text-white",
  "bg-rose-500 text-white",
];

function avatarColor(username: string): string {
  let h = 0;
  for (let i = 0; i < username.length; i++) h = (h * 31 + username.charCodeAt(i)) & 0xffff;
  return AVATAR_COLORS[h % AVATAR_COLORS.length];
}

function roleLabel(role: string): string {
  return role.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

// ---------------------------------------------------------------------------
// Sentiment bar (pure CSS — no chart lib needed)
// ---------------------------------------------------------------------------

function SentimentBar({ up, total }: { up: number; total: number }) {
  const pct = total === 0 ? 0 : Math.round((up / total) * 100);
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between text-xs text-muted">
        <span className="flex items-center gap-1">
          <ThumbsUp className="h-3.5 w-3.5 text-emerald-500" /> {up} positive
        </span>
        <span className="font-medium text-fg">{pct}% satisfaction</span>
        <span className="flex items-center gap-1">
          {total - up} negative <ThumbsDown className="h-3.5 w-3.5 text-red-500" />
        </span>
      </div>
      <div className="h-3 w-full overflow-hidden rounded-full bg-red-200 dark:bg-red-900/40">
        <div
          className="h-full rounded-full bg-emerald-500 transition-all duration-500"
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Single feedback card
// ---------------------------------------------------------------------------

function FeedbackCard({ record }: { record: FeedbackRecord }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="rounded-xl border border-line bg-surface shadow-sm overflow-hidden">
      {/* Card header row */}
      <div className="flex items-start justify-between gap-3 px-4 py-3 border-b border-line">
        <div className="flex items-center gap-2.5 min-w-0">
          {/* Avatar */}
          <div
            className={cn(
              "flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-sm font-semibold",
              avatarColor(record.user),
            )}
          >
            {avatarInitial(record.user)}
          </div>

          {/* User + meta */}
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="text-sm font-semibold text-fg">{record.user}</span>
              <Badge tone="slate">{roleLabel(record.role)}</Badge>
              {record.route && (
                <Badge tone="brand">{record.route}</Badge>
              )}
              {record.blocked && (
                <Badge tone="red">Blocked</Badge>
              )}
            </div>
            <p className="mt-0.5 text-xs text-faint" title={formatTs(record.ts)}>
              {timeAgo(record.ts)} · {record.feedback_id}
            </p>
          </div>
        </div>

        {/* Rating icon */}
        <div className="shrink-0">
          {record.rating === "up" ? (
            <div className="flex items-center gap-1.5 rounded-full bg-emerald-50 dark:bg-emerald-500/15 px-2.5 py-1 text-xs font-medium text-emerald-700 dark:text-emerald-300 ring-1 ring-emerald-200 dark:ring-emerald-500/30">
              <ThumbsUp className="h-3.5 w-3.5" /> Positive
            </div>
          ) : (
            <div className="flex items-center gap-1.5 rounded-full bg-red-50 dark:bg-red-500/15 px-2.5 py-1 text-xs font-medium text-red-700 dark:text-red-300 ring-1 ring-red-200 dark:ring-red-500/30">
              <ThumbsDown className="h-3.5 w-3.5" /> Negative
            </div>
          )}
        </div>
      </div>

      {/* Body */}
      <div className="px-4 py-3 space-y-2.5">
        {/* Comment */}
        {record.comment && (
          <div className="border-l-2 border-amber-400 pl-3">
            <p className="text-sm italic text-muted">"{record.comment}"</p>
          </div>
        )}

        {/* Query preview */}
        <div>
          <p className="text-xs font-medium uppercase tracking-wide text-faint mb-1">Query</p>
          <p
            className={cn(
              "text-sm text-fg",
              !expanded && "line-clamp-2",
            )}
          >
            {record.query}
          </p>
        </div>

        {/* Expand / collapse Q&A */}
        <button
          onClick={() => setExpanded((v) => !v)}
          className="flex items-center gap-1.5 text-xs font-medium text-brand-600 dark:text-brand-400 hover:underline"
        >
          {expanded ? (
            <><ChevronUp className="h-3.5 w-3.5" /> Hide answer</>
          ) : (
            <><ChevronDown className="h-3.5 w-3.5" /> Show full Q&amp;A</>
          )}
        </button>

        {expanded && (
          <div className="rounded-lg bg-surface-2 border border-line p-3.5 space-y-3">
            <div>
              <p className="text-xs font-semibold uppercase tracking-wide text-muted mb-1.5">
                Question
              </p>
              <p className="text-sm text-fg whitespace-pre-wrap">{record.query}</p>
            </div>
            <div className="border-t border-line pt-3">
              <p className="text-xs font-semibold uppercase tracking-wide text-muted mb-1.5">
                Answer
              </p>
              <div className="prose prose-sm dark:prose-invert max-w-none text-sm text-fg [&_table]:w-full [&_table]:text-xs [&_th]:text-left [&_th]:py-1 [&_td]:py-1 [&_table]:border-collapse [&_th]:border [&_th]:border-line [&_td]:border [&_td]:border-line [&_th]:px-2 [&_td]:px-2">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                  {record.answer}
                </ReactMarkdown>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function FeedbackDashboardPage() {
  const { data, isLoading, isError, refetch, isFetching } = useQuery({
    queryKey: ["feedback-list"],
    queryFn: getFeedback,
    refetchInterval: 30_000,
  });

  const [ratingFilter, setRatingFilter] = useState<"all" | "up" | "down">("all");
  const [routeFilter, setRouteFilter] = useState("all");
  const [search, setSearch] = useState("");

  const routes = useMemo(() => {
    if (!data) return [];
    return Array.from(new Set(data.records.map((r) => r.route).filter(Boolean))).sort();
  }, [data]);

  const filtered = useMemo(() => {
    if (!data) return [];
    return data.records.filter((r) => {
      if (ratingFilter !== "all" && r.rating !== ratingFilter) return false;
      if (routeFilter !== "all" && r.route !== routeFilter) return false;
      if (search) {
        const q = search.toLowerCase();
        if (
          !r.query.toLowerCase().includes(q) &&
          !(r.comment ?? "").toLowerCase().includes(q) &&
          !r.user.toLowerCase().includes(q)
        )
          return false;
      }
      return true;
    });
  }, [data, ratingFilter, routeFilter, search]);

  const satisfactionTone =
    !data || data.total === 0
      ? "default"
      : data.up / data.total >= 0.8
      ? "good"
      : data.up / data.total >= 0.5
      ? "warn"
      : "bad";

  return (
    <div className="space-y-6 px-4 py-6 sm:px-6 lg:px-8 max-w-5xl mx-auto w-full">
      {/* Page header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-fg">User Feedback</h1>
          <p className="text-sm text-muted mt-0.5">
            Thumbs-up / thumbs-down ratings collected from Price Assist responses
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

      {/* Stat tiles */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
        <Metric
          label="Total Responses"
          value={isLoading ? "—" : String(data?.total ?? 0)}
          hint="all time"
          tone="default"
        />
        <Metric
          label="Positive"
          value={
            isLoading
              ? "—"
              : data && data.total > 0
              ? `${Math.round((data.up / data.total) * 100)}%`
              : "—"
          }
          hint={isLoading ? undefined : `${data?.up ?? 0} thumbs up`}
          tone={satisfactionTone}
        />
        <Metric
          label="Negative"
          value={isLoading ? "—" : String(data?.down ?? 0)}
          hint="thumbs down"
          tone={
            isLoading ? "default" : (data?.down ?? 0) === 0 ? "good" : "bad"
          }
        />
        <Metric
          label="With Comments"
          value={isLoading ? "—" : String(data?.with_comment ?? 0)}
          hint="added a note"
          tone="default"
        />
      </div>

      {/* Sentiment bar */}
      {!isLoading && data && data.total > 0 && (
        <Card>
          <CardBody>
            <SentimentBar up={data.up} total={data.total} />
          </CardBody>
        </Card>
      )}

      {/* Filter row */}
      <div className="flex flex-wrap items-center gap-3">
        {/* Search */}
        <div className="relative flex-1 min-w-[200px]">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-faint pointer-events-none" />
          <input
            type="text"
            placeholder="Search queries, comments, users…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full rounded-lg border border-line bg-surface pl-9 pr-3 py-2 text-sm text-fg placeholder:text-faint focus:outline-none focus:ring-2 focus:ring-brand-500/50"
          />
        </div>

        {/* Rating filter */}
        <select
          value={ratingFilter}
          onChange={(e) => setRatingFilter(e.target.value as "all" | "up" | "down")}
          className="rounded-lg border border-line bg-surface px-3 py-2 text-sm text-fg focus:outline-none focus:ring-2 focus:ring-brand-500/50"
        >
          <option value="all">All ratings</option>
          <option value="up">👍 Positive only</option>
          <option value="down">👎 Negative only</option>
        </select>

        {/* Route filter */}
        {routes.length > 1 && (
          <select
            value={routeFilter}
            onChange={(e) => setRouteFilter(e.target.value)}
            className="rounded-lg border border-line bg-surface px-3 py-2 text-sm text-fg focus:outline-none focus:ring-2 focus:ring-brand-500/50"
          >
            <option value="all">All routes</option>
            {routes.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
        )}

        {/* Active filter count */}
        {!isLoading && data && (
          <span className="text-xs text-muted ml-auto">
            Showing {filtered.length} of {data.total}
          </span>
        )}
      </div>

      {/* Content */}
      {isLoading ? (
        <CenteredSpinner />
      ) : isError ? (
        <div className="rounded-xl border border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-950/30 p-5 text-sm text-red-700 dark:text-red-400">
          Failed to load feedback. Make sure the API server is running.
        </div>
      ) : filtered.length === 0 ? (
        <div className="flex flex-col items-center justify-center gap-3 py-20 text-center">
          <MessageSquare className="h-10 w-10 text-faint" />
          <p className="text-base font-medium text-fg">
            {data?.total === 0 ? "No feedback yet" : "No results match your filters"}
          </p>
          <p className="text-sm text-muted">
            {data?.total === 0
              ? "Feedback will appear here after users rate responses in the chat."
              : "Try clearing your search or changing the filters above."}
          </p>
        </div>
      ) : (
        <div className="space-y-4">
          {filtered.map((record) => (
            <FeedbackCard key={record.feedback_id} record={record} />
          ))}
        </div>
      )}
    </div>
  );
}
