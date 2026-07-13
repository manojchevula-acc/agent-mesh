import { useState, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  ShieldCheck,
  RefreshCw,
  ChevronDown,
  ChevronUp,
  Search,
  CheckCircle2,
  XCircle,
  Clock,
  Loader2,
} from "lucide-react";
import { getAudit, getAuditDetail } from "@/api/mesh";
import { Card, CardBody } from "@/components/ui/Card";
import { Metric } from "@/components/ui/Metric";
import { Badge } from "@/components/ui/Badge";
import { CenteredSpinner } from "@/components/ui/Spinner";
import { cn } from "@/lib/utils";
import type { AuditRecord, AuditDetailRecord } from "@/types/mesh";

// ---------------------------------------------------------------------------
// Helpers (shared with FeedbackDashboardPage pattern)
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
function roleLabel(r: string) { return r.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase()); }

function formatLatency(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

// ---------------------------------------------------------------------------
// Full I/O detail (lazy loaded on expand)
// ---------------------------------------------------------------------------

function AuditDetail({ requestId }: { requestId: string }) {
  const { data, isLoading, isError } = useQuery<AuditDetailRecord>({
    queryKey: ["audit-detail", requestId],
    queryFn: () => getAuditDetail(requestId),
    staleTime: Infinity,
  });

  if (isLoading) return (
    <div className="flex items-center gap-2 py-4 text-sm text-muted">
      <Loader2 className="h-4 w-4 animate-spin" /> Loading full record…
    </div>
  );
  if (isError || !data) return (
    <p className="py-3 text-sm text-red-600 dark:text-red-400">Failed to load full record.</p>
  );

  const rawInput = Array.isArray(data.inputs) ? data.inputs[0] ?? "" : "";

  return (
    <div className="space-y-4">
      {/* Input */}
      <div>
        <p className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-muted">
          Full Input (prompt sent to agent)
        </p>
        <pre className="max-h-48 overflow-y-auto rounded-lg bg-slate-900 dark:bg-slate-950 p-3 text-xs text-slate-200 whitespace-pre-wrap break-words leading-relaxed">
          {rawInput}
        </pre>
      </div>
      {/* Output */}
      <div>
        <p className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-muted">
          Agent Output
        </p>
        <div className="max-h-72 overflow-y-auto rounded-lg border border-line bg-surface-2 p-3 text-sm prose prose-sm dark:prose-invert max-w-none [&_table]:w-full [&_table]:text-xs [&_th]:text-left [&_th]:py-1 [&_td]:py-1 [&_table]:border-collapse [&_th]:border [&_th]:border-line [&_td]:border [&_td]:border-line [&_th]:px-2 [&_td]:px-2">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{data.output}</ReactMarkdown>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Single audit card
// ---------------------------------------------------------------------------

function AuditCard({ record }: { record: AuditRecord }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="rounded-xl border border-line bg-surface shadow-sm overflow-hidden">
      {/* Header */}
      <div className="flex items-start justify-between gap-3 px-4 py-3 border-b border-line">
        <div className="flex items-center gap-2.5 min-w-0">
          <div className={cn(
            "flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-sm font-semibold",
            avatarColor(record.user),
          )}>
            {avatarInitial(record.user)}
          </div>
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="text-sm font-semibold text-fg">{record.user}</span>
              <Badge tone="slate">{roleLabel(record.role)}</Badge>
              <Badge tone="brand">{record.agent_name}</Badge>
            </div>
            <p className="mt-0.5 text-xs text-faint" title={formatTs(record.timestamp)}>
              {timeAgo(record.timestamp)} · {record.request_id}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {/* Latency */}
          <span className="flex items-center gap-1 rounded-full bg-surface-2 border border-line px-2.5 py-0.5 text-xs text-muted">
            <Clock className="h-3 w-3" /> {formatLatency(record.latency_ms)}
          </span>
          {/* Status */}
          {record.status === "SUCCESS" ? (
            <span className="flex items-center gap-1 rounded-full bg-emerald-50 dark:bg-emerald-500/15 px-2.5 py-0.5 text-xs font-medium text-emerald-700 dark:text-emerald-300 ring-1 ring-emerald-200 dark:ring-emerald-500/30">
              <CheckCircle2 className="h-3.5 w-3.5" /> Success
            </span>
          ) : (
            <span className="flex items-center gap-1 rounded-full bg-red-50 dark:bg-red-500/15 px-2.5 py-0.5 text-xs font-medium text-red-700 dark:text-red-300 ring-1 ring-red-200 dark:ring-red-500/30">
              <XCircle className="h-3.5 w-3.5" /> {record.status}
            </span>
          )}
        </div>
      </div>

      {/* Body */}
      <div className="px-4 py-3 space-y-2.5">
        {/* Input preview */}
        <div>
          <p className="text-xs font-medium uppercase tracking-wide text-faint mb-1">Input Preview</p>
          <p className="text-xs text-muted line-clamp-2 font-mono leading-relaxed bg-surface-2 rounded px-2 py-1.5">
            {record.input_preview || "—"}
          </p>
        </div>

        {/* Output preview */}
        <div>
          <p className="text-xs font-medium uppercase tracking-wide text-faint mb-1">Output Preview</p>
          <p className="text-sm text-fg line-clamp-2">{record.output_preview || "—"}</p>
        </div>

        {/* Expand toggle */}
        <button
          onClick={() => setExpanded(v => !v)}
          className="flex items-center gap-1.5 text-xs font-medium text-brand-600 dark:text-brand-400 hover:underline"
        >
          {expanded ? (
            <><ChevronUp className="h-3.5 w-3.5" /> Hide full I/O</>
          ) : (
            <><ChevronDown className="h-3.5 w-3.5" /> Show full I/O</>
          )}
        </button>

        {expanded && (
          <div className="rounded-lg border border-line bg-surface-2 p-4">
            <AuditDetail requestId={record.request_id} />
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function AuditDashboardPage() {
  const { data, isLoading, isError, refetch, isFetching } = useQuery({
    queryKey: ["audit-list"],
    queryFn: getAudit,
    refetchInterval: 30_000,
  });

  const [statusFilter, setStatusFilter] = useState("all");
  const [agentFilter, setAgentFilter] = useState("all");
  const [search, setSearch] = useState("");

  const agents = useMemo(() =>
    Array.from(new Set((data?.records ?? []).map(r => r.agent_name))).sort(),
    [data],
  );

  const filtered = useMemo(() => {
    if (!data) return [];
    return data.records.filter(r => {
      if (statusFilter !== "all" && r.status !== statusFilter) return false;
      if (agentFilter !== "all" && r.agent_name !== agentFilter) return false;
      if (search) {
        const q = search.toLowerCase();
        if (!r.user.toLowerCase().includes(q) &&
            !r.session_id.toLowerCase().includes(q) &&
            !r.request_id.toLowerCase().includes(q)) return false;
      }
      return true;
    });
  }, [data, statusFilter, agentFilter, search]);

  const successRate = !data || data.total === 0
    ? null
    : Math.round((data.success_count / data.total) * 100);

  return (
    <div className="space-y-6 px-4 py-6 sm:px-6 lg:px-8 max-w-5xl mx-auto w-full">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-fg">Audit Trail</h1>
          <p className="text-sm text-muted mt-0.5">
            Complete log of every agent invocation — inputs, outputs, latency, and status
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
        <Metric label="Total Calls" value={isLoading ? "—" : String(data?.total ?? 0)} tone="default" />
        <Metric
          label="Success Rate"
          value={isLoading ? "—" : successRate !== null ? `${successRate}%` : "—"}
          tone={isLoading ? "default" : successRate === null ? "default" : successRate >= 90 ? "good" : successRate >= 70 ? "warn" : "bad"}
        />
        <Metric
          label="Avg Latency"
          value={isLoading ? "—" : formatLatency(data?.avg_latency_ms ?? 0)}
          tone="default"
        />
        <Metric
          label="Errors"
          value={isLoading ? "—" : String(data?.error_count ?? 0)}
          tone={isLoading ? "default" : (data?.error_count ?? 0) === 0 ? "good" : "bad"}
        />
      </div>

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="relative flex-1 min-w-[200px]">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-faint pointer-events-none" />
          <input
            type="text"
            placeholder="Search user, session, request ID…"
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
        {agents.length > 1 && (
          <select
            value={agentFilter}
            onChange={e => setAgentFilter(e.target.value)}
            className="rounded-lg border border-line bg-surface px-3 py-2 text-sm text-fg focus:outline-none focus:ring-2 focus:ring-brand-500/50"
          >
            <option value="all">All agents</option>
            {agents.map(a => <option key={a} value={a}>{a}</option>)}
          </select>
        )}
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
          Failed to load audit trail. Make sure the API server is running.
        </div>
      ) : filtered.length === 0 ? (
        <div className="flex flex-col items-center justify-center gap-3 py-20 text-center">
          <ShieldCheck className="h-10 w-10 text-faint" />
          <p className="text-base font-medium text-fg">
            {data?.total === 0 ? "No audit records yet" : "No results match your filters"}
          </p>
          <p className="text-sm text-muted">
            {data?.total === 0
              ? "Records will appear here after the mesh handles queries."
              : "Try clearing your search or adjusting the filters."}
          </p>
        </div>
      ) : (
        <div className="space-y-4">
          {filtered.map(r => <AuditCard key={r.request_id} record={r} />)}
        </div>
      )}
    </div>
  );
}
