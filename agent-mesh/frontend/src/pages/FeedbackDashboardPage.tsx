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
  Target,
  GitBranch,
  Wrench,
  Shield,
  FileText,
  PenLine,
  Clock,
} from "lucide-react";
import { getFeedback, getStructuredFeedback } from "@/api/mesh";
import { useAuth } from "@/contexts/AuthContext";
import { Card, CardBody } from "@/components/ui/Card";
import { Metric } from "@/components/ui/Metric";
import { Badge } from "@/components/ui/Badge";
import { CenteredSpinner } from "@/components/ui/Spinner";
import { cn } from "@/lib/utils";
import type { FeedbackRecord } from "@/types/mesh";
import type { StructuredFeedbackRecord } from "@/types/feedback";

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
// Structured feedback helpers
// ---------------------------------------------------------------------------

const DIMENSION_ORDER = [
  "intent", "workflow", "tools", "policy", "output", "correction", "effort",
] as const;

type DimKey = typeof DIMENSION_ORDER[number];

const DIMENSION_META: Record<DimKey, { label: string; icon: React.ElementType }> = {
  intent:     { label: "Intent Capture",     icon: Target },
  workflow:   { label: "Workflow",            icon: GitBranch },
  tools:      { label: "Tools & Evidence",    icon: Wrench },
  policy:     { label: "Policy & Risk",       icon: Shield },
  output:     { label: "Output Quality",      icon: FileText },
  correction: { label: "Correction/Override", icon: PenLine },
  effort:     { label: "Effort & Outcome",    icon: Clock },
};

const RATING_STYLE: Record<string, string> = {
  good:    "bg-emerald-50 text-emerald-700 ring-1 ring-emerald-200 dark:bg-emerald-500/15 dark:text-emerald-300 dark:ring-emerald-500/30",
  partial: "bg-amber-50 text-amber-700 ring-1 ring-amber-200 dark:bg-amber-500/15 dark:text-amber-300 dark:ring-amber-500/30",
  poor:    "bg-red-50 text-red-700 ring-1 ring-red-200 dark:bg-red-500/15 dark:text-red-300 dark:ring-red-500/30",
};

function StructuredFeedbackCard({ record }: { record: StructuredFeedbackRecord }) {
  const [expanded, setExpanded] = useState(false);
  const dims = record.dimensions ?? {};
  const ratedDims = DIMENSION_ORDER.filter((k) => dims[k]?.rating);

  return (
    <div className="rounded-xl border border-line bg-surface shadow-sm overflow-hidden">
      {/* Header */}
      <div className="flex items-start justify-between gap-3 px-4 py-3 border-b border-line">
        <div className="flex items-center gap-2.5 min-w-0">
          <div className={cn("flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-sm font-semibold", avatarColor(record.user))}>
            {avatarInitial(record.user)}
          </div>
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="text-sm font-semibold text-fg">{record.user}</span>
              {record.role && <Badge tone="slate">{roleLabel(record.role)}</Badge>}
              {record.route && <Badge tone="brand">{record.route}</Badge>}
              {record.blocked && <Badge tone="red">Blocked</Badge>}
            </div>
            <p className="mt-0.5 text-xs text-faint" title={formatTs(record.ts)}>
              {timeAgo(record.ts)} · {record.structured_feedback_id}
            </p>
          </div>
        </div>
        <span className="shrink-0 rounded-full bg-brand-50 dark:bg-brand-900/20 px-2.5 py-1 text-xs font-medium text-brand-600 dark:text-brand-400 ring-1 ring-brand-200 dark:ring-brand-500/30">
          {ratedDims.length}/7 rated
        </span>
      </div>

      {/* Dimension rows */}
      <div className="px-4 py-3 space-y-2">
        {DIMENSION_ORDER.map((key) => {
          const d = dims[key];
          if (!d?.rating) return null;
          const { label, icon: Icon } = DIMENSION_META[key];
          return (
            <div key={key} className="flex items-start gap-2.5 min-w-0">
              <Icon className="h-3.5 w-3.5 mt-0.5 shrink-0 text-muted" />
              <span className="w-32 shrink-0 text-xs text-muted">{label}</span>
              <span className={cn("rounded-full px-2 py-0.5 text-xs font-medium capitalize", RATING_STYLE[d.rating])}>
                {d.rating}
              </span>
              <div className="flex flex-wrap gap-1 min-w-0">
                {d.codes?.map((c) => (
                  <span key={c} className="rounded-full bg-surface-2 border border-line px-1.5 py-0.5 text-xs text-muted">
                    {c.replace(/_/g, " ")}
                  </span>
                ))}
                {d.note && (
                  <span className="text-xs italic text-faint">"{d.note}"</span>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {/* Q&A expandable */}
      {record.query && (
        <div className="px-4 pb-3 space-y-2.5 border-t border-line pt-3">
          <div>
            <p className="text-xs font-medium uppercase tracking-wide text-faint mb-1">Query</p>
            <p className={cn("text-sm text-fg", !expanded && "line-clamp-2")}>{record.query}</p>
          </div>
          <button
            onClick={() => setExpanded((v) => !v)}
            className="flex items-center gap-1.5 text-xs font-medium text-brand-600 dark:text-brand-400 hover:underline"
          >
            {expanded ? (
              <><ChevronUp className="h-3.5 w-3.5" />Hide answer</>
            ) : (
              <><ChevronDown className="h-3.5 w-3.5" />Show full Q&amp;A</>
            )}
          </button>
          {expanded && (
            <div className="rounded-lg bg-surface-2 border border-line p-3.5 space-y-3">
              <div>
                <p className="text-xs font-semibold uppercase tracking-wide text-muted mb-1.5">Question</p>
                <p className="text-sm text-fg whitespace-pre-wrap">{record.query}</p>
              </div>
              {record.answer && (
                <div className="border-t border-line pt-3">
                  <p className="text-xs font-semibold uppercase tracking-wide text-muted mb-1.5">Answer</p>
                  <div className="prose prose-sm dark:prose-invert max-w-none text-sm text-fg [&_table]:w-full [&_table]:text-xs [&_th]:text-left [&_th]:py-1 [&_td]:py-1 [&_table]:border-collapse [&_th]:border [&_th]:border-line [&_td]:border [&_td]:border-line [&_th]:px-2 [&_td]:px-2">
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{record.answer}</ReactMarkdown>
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function FeedbackDashboardPage() {
  const { user } = useAuth();
  const [activeTab, setActiveTab] = useState<"basic" | "structured">("basic");

  const { data, isLoading, isError, refetch, isFetching } = useQuery({
    queryKey: ["feedback-list", user?.username],
    queryFn: getFeedback,
    refetchInterval: 30_000,
  });

  const {
    data: structuredData,
    isLoading: sfLoading,
    isError: sfError,
    refetch: sfRefetch,
    isFetching: sfFetching,
  } = useQuery({
    queryKey: ["structured-feedback-list", user?.username],
    queryFn: getStructuredFeedback,
    refetchInterval: 30_000,
  });

  const [ratingFilter, setRatingFilter] = useState<"all" | "up" | "down">("all");
  const [routeFilter, setRouteFilter] = useState("all");
  const [search, setSearch] = useState("");

  const myRecords = useMemo(
    () => (data?.records ?? []).filter(r => r.user === user?.username),
    [data, user?.username],
  );

  const myStructured = useMemo(
    () => (structuredData?.records ?? []).filter(r => r.user === user?.username),
    [structuredData, user?.username],
  );

  const routes = useMemo(() => {
    return Array.from(new Set(myRecords.map((r) => r.route).filter(Boolean))).sort();
  }, [myRecords]);

  const filtered = useMemo(() => {
    return myRecords.filter((r) => {
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
  }, [myRecords, ratingFilter, routeFilter, search]);

  const myUp = useMemo(() => myRecords.filter(r => r.rating === "up").length, [myRecords]);
  const myDown = useMemo(() => myRecords.filter(r => r.rating === "down").length, [myRecords]);
  const myWithComment = useMemo(() => myRecords.filter(r => r.comment).length, [myRecords]);

  const satisfactionTone =
    myRecords.length === 0
      ? "default"
      : myUp / myRecords.length >= 0.8
      ? "good"
      : myUp / myRecords.length >= 0.5
      ? "warn"
      : "bad";

  return (
    <div className="space-y-6 px-4 py-6 sm:px-6 lg:px-8 max-w-5xl mx-auto w-full">
      {/* Page header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-fg">User Feedback</h1>
          <p className="text-sm text-muted mt-0.5">
            Ratings and structured evaluations collected from Price Assist responses
          </p>
        </div>
        <button
          onClick={() => activeTab === "basic" ? refetch() : sfRefetch()}
          disabled={activeTab === "basic" ? isFetching : sfFetching}
          className="flex items-center gap-1.5 text-sm text-muted hover:text-fg transition-colors disabled:opacity-50"
        >
          <RefreshCw className={cn("h-4 w-4", (isFetching || sfFetching) && "animate-spin")} />
          Refresh
        </button>
      </div>

      {/* Tab switcher */}
      <div className="inline-flex rounded-lg border border-line bg-surface-2 p-0.5">
        {(["basic", "structured"] as const).map((tab) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={cn(
              "flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-md transition-colors",
              activeTab === tab
                ? "bg-surface shadow-sm font-medium text-fg"
                : "text-muted hover:text-fg",
            )}
          >
            {tab === "basic" ? (
              <>Basic Feedback</>
            ) : (
              <>
                Structured Feedback
                {myStructured.length > 0 && (
                  <span className="rounded-full bg-brand-100 dark:bg-brand-900/30 px-1.5 text-xs text-brand-600 dark:text-brand-400">
                    {myStructured.length}
                  </span>
                )}
              </>
            )}
          </button>
        ))}
      </div>

      {/* ── Basic feedback tab ── */}
      {activeTab === "basic" && (
        <>
          {/* Stat tiles */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
            <Metric
              label="My Responses"
              value={isLoading ? "—" : String(myRecords.length)}
              hint="all time"
              tone="default"
            />
            <Metric
              label="Positive"
              value={
                isLoading
                  ? "—"
                  : myRecords.length > 0
                  ? `${Math.round((myUp / myRecords.length) * 100)}%`
                  : "—"
              }
              hint={isLoading ? undefined : `${myUp} thumbs up`}
              tone={satisfactionTone}
            />
            <Metric
              label="Negative"
              value={isLoading ? "—" : String(myDown)}
              hint="thumbs down"
              tone={isLoading ? "default" : myDown === 0 ? "good" : "bad"}
            />
            <Metric
              label="With Comments"
              value={isLoading ? "—" : String(myWithComment)}
              hint="added a note"
              tone="default"
            />
          </div>

          {/* Sentiment bar */}
          {!isLoading && myRecords.length > 0 && (
            <Card>
              <CardBody>
                <SentimentBar up={myUp} total={myRecords.length} />
              </CardBody>
            </Card>
          )}

          {/* Filter row */}
          <div className="flex flex-wrap items-center gap-3">
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
            <select
              value={ratingFilter}
              onChange={(e) => setRatingFilter(e.target.value as "all" | "up" | "down")}
              className="rounded-lg border border-line bg-surface px-3 py-2 text-sm text-fg focus:outline-none focus:ring-2 focus:ring-brand-500/50"
            >
              <option value="all">All ratings</option>
              <option value="up">👍 Positive only</option>
              <option value="down">👎 Negative only</option>
            </select>
            {routes.length > 1 && (
              <select
                value={routeFilter}
                onChange={(e) => setRouteFilter(e.target.value)}
                className="rounded-lg border border-line bg-surface px-3 py-2 text-sm text-fg focus:outline-none focus:ring-2 focus:ring-brand-500/50"
              >
                <option value="all">All routes</option>
                {routes.map((r) => (
                  <option key={r} value={r}>{r}</option>
                ))}
              </select>
            )}
            {!isLoading && (
              <span className="text-xs text-muted ml-auto">
                Showing {filtered.length} of {myRecords.length}
              </span>
            )}
          </div>

          {/* Basic records list */}
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
                {myRecords.length === 0 ? "No feedback yet" : "No results match your filters"}
              </p>
              <p className="text-sm text-muted">
                {myRecords.length === 0
                  ? "Feedback will appear here after you rate responses in the chat."
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
        </>
      )}

      {/* ── Structured feedback tab ── */}
      {activeTab === "structured" && (
        <>
          {sfLoading ? (
            <CenteredSpinner />
          ) : sfError ? (
            <div className="rounded-xl border border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-950/30 p-5 text-sm text-red-700 dark:text-red-400">
              Failed to load structured feedback. Make sure the API server is running.
            </div>
          ) : myStructured.length === 0 ? (
            <div className="flex flex-col items-center justify-center gap-3 py-20 text-center">
              <MessageSquare className="h-10 w-10 text-faint" />
              <p className="text-base font-medium text-fg">No structured feedback yet</p>
              <p className="text-sm text-muted">
                Use the "Feedback Form" button in the chat to submit a detailed evaluation.
              </p>
            </div>
          ) : (
            <div className="space-y-4">
              {myStructured.map((record) => (
                <StructuredFeedbackCard key={record.structured_feedback_id} record={record} />
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}
