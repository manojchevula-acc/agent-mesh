import { useState, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { Zap, RefreshCw, Search, ChevronDown, ChevronUp, CheckCircle2, XCircle } from "lucide-react";
import { getTraces } from "@/api/mesh";
import { Card, CardHeader, CardBody } from "@/components/ui/Card";
import { Metric } from "@/components/ui/Metric";
import { Badge } from "@/components/ui/Badge";
import { CenteredSpinner } from "@/components/ui/Spinner";
import { cn } from "@/lib/utils";
import type { TraceRecord } from "@/types/mesh";

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

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function formatBytes(n: number): string {
  if (n < 1000) return `${n}`;
  return `${(n / 1000).toFixed(1)}k`;
}

type Bucket = { label: string; min: number; max: number; color: string };
const BUCKETS: Bucket[] = [
  { label: "< 1s",   min: 0,      max: 1000,  color: "bg-emerald-500" },
  { label: "1–5s",   min: 1000,   max: 5000,  color: "bg-amber-400" },
  { label: "5–20s",  min: 5000,   max: 20000, color: "bg-orange-500" },
  { label: "> 20s",  min: 20000,  max: Infinity, color: "bg-red-500" },
];

function bucketFor(ms: number): number {
  return BUCKETS.findIndex(b => ms >= b.min && ms < b.max);
}

// ---------------------------------------------------------------------------
// Duration distribution bar (pure CSS, no chart lib)
// ---------------------------------------------------------------------------

function DurationDistribution({ records }: { records: TraceRecord[] }) {
  const counts = BUCKETS.map((b, i) => records.filter(r => bucketFor(r.duration_ms) === i).length);
  const total = records.length;

  return (
    <Card>
      <CardHeader title="Duration Distribution" subtitle="How long each A2A call took" />
      <CardBody>
        <div className="space-y-3">
          {BUCKETS.map((b, i) => {
            const pct = total === 0 ? 0 : Math.round((counts[i] / total) * 100);
            return (
              <div key={b.label} className="flex items-center gap-3">
                <span className="w-14 shrink-0 text-right text-xs text-muted">{b.label}</span>
                <div className="flex-1 h-5 rounded bg-surface-2 border border-line overflow-hidden">
                  <div
                    className={cn("h-full rounded transition-all duration-500", b.color)}
                    style={{ width: `${pct}%` }}
                    title={`${counts[i]} spans (${pct}%)`}
                  />
                </div>
                <span className="w-16 text-xs text-muted">
                  {counts[i]} ({pct}%)
                </span>
              </div>
            );
          })}
        </div>
      </CardBody>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Trace row (table-style, expandable)
// ---------------------------------------------------------------------------

function TraceRow({ record, index }: { record: TraceRecord; index: number }) {
  const [expanded, setExpanded] = useState(false);
  const bi = bucketFor(record.duration_ms);
  const bucketColor = ["text-emerald-600 dark:text-emerald-400", "text-amber-600 dark:text-amber-400", "text-orange-600 dark:text-orange-400", "text-red-600 dark:text-red-400"][bi] ?? "text-fg";

  return (
    <>
      <tr
        className={cn(
          "cursor-pointer transition-colors",
          index % 2 === 0 ? "bg-surface" : "bg-surface-2",
          "hover:bg-brand-50/50 dark:hover:bg-brand-500/5",
        )}
        onClick={() => setExpanded(v => !v)}
      >
        {/* Event type */}
        <td className="px-4 py-2.5 text-xs">
          <Badge tone="brand">{record.event_type}</Badge>
        </td>
        {/* Target */}
        <td className="px-4 py-2.5 text-sm text-fg font-medium">
          {record.attributes?.target_node ?? record.name}
        </td>
        {/* Status */}
        <td className="px-4 py-2.5">
          {record.status === "SUCCESS" ? (
            <span className="flex items-center gap-1 text-xs text-emerald-600 dark:text-emerald-400">
              <CheckCircle2 className="h-3.5 w-3.5" /> Success
            </span>
          ) : (
            <span className="flex items-center gap-1 text-xs text-red-600 dark:text-red-400">
              <XCircle className="h-3.5 w-3.5" /> {record.status}
            </span>
          )}
        </td>
        {/* Duration */}
        <td className={cn("px-4 py-2.5 text-sm font-semibold tabular-nums", bucketColor)}>
          {formatDuration(record.duration_ms)}
        </td>
        {/* Sizes */}
        <td className="px-4 py-2.5 text-xs text-muted tabular-nums">
          {formatBytes(record.attributes?.prompt_length ?? 0)} → {formatBytes(record.attributes?.response_length ?? 0)}
        </td>
        {/* Timestamp */}
        <td className="px-4 py-2.5 text-xs text-faint whitespace-nowrap">
          {timeAgo(record.timestamp)}
        </td>
        {/* Expand icon */}
        <td className="px-3 py-2.5 text-faint">
          {expanded ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
        </td>
      </tr>

      {/* Expanded detail row */}
      {expanded && (
        <tr className="bg-surface-2">
          <td colSpan={7} className="px-4 py-3">
            <div className="space-y-2">
              <div className="flex gap-4 text-xs text-muted flex-wrap">
                <span><span className="font-medium text-fg">Span ID:</span> {record.span_id}</span>
                {record.trace_id && <span><span className="font-medium text-fg">Trace ID:</span> {record.trace_id}</span>}
                {record.error && (
                  <span className="text-red-600 dark:text-red-400">
                    <span className="font-medium">Error:</span> {record.error}
                  </span>
                )}
              </div>
              {record.attributes?.response_preview && (
                <div>
                  <p className="text-xs font-medium text-faint uppercase tracking-wide mb-1">Response Preview</p>
                  <p className="text-sm text-fg bg-surface rounded border border-line px-3 py-2 leading-relaxed">
                    {record.attributes.response_preview}
                  </p>
                </div>
              )}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function TraceDashboardPage() {
  const { data, isLoading, isError, refetch, isFetching } = useQuery({
    queryKey: ["trace-list"],
    queryFn: getTraces,
    refetchInterval: 30_000,
  });

  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");

  const filtered = useMemo(() => {
    if (!data) return [];
    return data.records.filter(r => {
      if (statusFilter !== "all" && r.status !== statusFilter) return false;
      if (search) {
        const q = search.toLowerCase();
        if (!r.name.toLowerCase().includes(q) &&
            !(r.attributes?.target_node ?? "").toLowerCase().includes(q)) return false;
      }
      return true;
    });
  }, [data, statusFilter, search]);

  const successRate = !data || data.total === 0
    ? null
    : Math.round((data.success_count / data.total) * 100);

  return (
    <div className="space-y-6 px-4 py-6 sm:px-6 lg:px-8 max-w-5xl mx-auto w-full">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-fg">Trace Spans</h1>
          <p className="text-sm text-muted mt-0.5">
            Agent-to-agent wire call spans — latency, payload sizes, and response previews
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
        <Metric label="Total Spans" value={isLoading ? "—" : String(data?.total ?? 0)} tone="default" />
        <Metric
          label="Success Rate"
          value={isLoading ? "—" : successRate !== null ? `${successRate}%` : "—"}
          tone={isLoading ? "default" : successRate === null ? "default" : successRate >= 90 ? "good" : successRate >= 70 ? "warn" : "bad"}
        />
        <Metric
          label="Avg Duration"
          value={isLoading ? "—" : formatDuration(data?.avg_duration_ms ?? 0)}
          tone="default"
        />
        <Metric
          label="Max Duration"
          value={isLoading ? "—" : formatDuration(data?.max_duration_ms ?? 0)}
          tone={isLoading ? "default" : (data?.max_duration_ms ?? 0) > 20000 ? "bad" : (data?.max_duration_ms ?? 0) > 5000 ? "warn" : "good"}
          hint="slowest call"
        />
      </div>

      {/* Distribution */}
      {!isLoading && data && data.total > 0 && (
        <DurationDistribution records={data.records} />
      )}

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="relative flex-1 min-w-[200px]">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-faint pointer-events-none" />
          <input
            type="text"
            placeholder="Search span name or target node…"
            value={search}
            onChange={e => setSearch(e.target.value)}
            className="w-full rounded-lg border border-line bg-surface pl-9 pr-3 py-2 text-sm text-fg placeholder:text-faint focus:outline-none focus:ring-2 focus:ring-brand-500/50"
          />
        </div>
        <select
          value={statusFilter}
          onChange={e => setStatusFilter(e.target.value)}
          className="rounded-lg border border-line bg-surface px-3 py-2 text-sm text-fg focus:outline-none focus:ring-2 focus:ring-brand-500/50"
        >
          <option value="all">All statuses</option>
          <option value="SUCCESS">✅ Success</option>
          <option value="ERROR">❌ Error</option>
        </select>
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
          Failed to load trace spans. Make sure the API server is running.
        </div>
      ) : filtered.length === 0 ? (
        <div className="flex flex-col items-center justify-center gap-3 py-20 text-center">
          <Zap className="h-10 w-10 text-faint" />
          <p className="text-base font-medium text-fg">
            {data?.total === 0 ? "No trace spans yet" : "No results match your filters"}
          </p>
          <p className="text-sm text-muted">
            {data?.total === 0
              ? "Spans will appear here after the mesh routes queries. Enable ENABLE_TRACE_JSONL in config."
              : "Try clearing your search or adjusting the filters."}
          </p>
        </div>
      ) : (
        <Card>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-line bg-surface-2">
                  <th className="px-4 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-muted">Event</th>
                  <th className="px-4 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-muted">Target</th>
                  <th className="px-4 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-muted">Status</th>
                  <th className="px-4 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-muted">Duration</th>
                  <th className="px-4 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-muted">Prompt → Response</th>
                  <th className="px-4 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-muted">When</th>
                  <th className="px-3 py-2.5" />
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {filtered.map((r, i) => <TraceRow key={r.span_id} record={r} index={i} />)}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}
