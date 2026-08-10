import { useState, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  BarChart2, RefreshCw, Search, ChevronDown, ChevronUp,
  CheckCircle2, XCircle, Clock, Loader2, AlertCircle,
  TrendingUp, Zap,
} from "lucide-react";
import { getAudit, getAuditDetail } from "@/api/mesh";
import { useAuth } from "@/contexts/AuthContext";
import { Metric } from "@/components/ui/Metric";
import { CenteredSpinner } from "@/components/ui/Spinner";
import { cn } from "@/lib/utils";
import type { AuditRecord, AuditDetailRecord } from "@/types/mesh";

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
  return new Date(isoTs).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

function formatLatency(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
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

// ---------------------------------------------------------------------------
// Agent metadata
// ---------------------------------------------------------------------------

interface AgentMeta {
  displayName: string;
  description: string;
  accent: { bg: string; text: string; ring: string; bar: string; dot: string };
}

const AGENT_META: Record<string, AgentMeta> = {
  price_assist: {
    displayName: "Price Assistant",
    description: "Primary coordinator — orchestrates all pricing queries",
    accent: {
      bg: "bg-brand-50 dark:bg-brand-500/10",
      text: "text-brand-700 dark:text-brand-300",
      ring: "ring-brand-200 dark:ring-brand-500/30",
      bar: "bg-brand-500",
      dot: "bg-brand-500",
    },
  },
  compliance: {
    displayName: "Compliance Agent",
    description: "Policy & regulatory compliance checker",
    accent: {
      bg: "bg-violet-50 dark:bg-violet-500/10",
      text: "text-violet-700 dark:text-violet-300",
      ring: "ring-violet-200 dark:ring-violet-500/30",
      bar: "bg-violet-500",
      dot: "bg-violet-500",
    },
  },
  data_agent: {
    displayName: "Data Agent",
    description: "Live banking data retrieval via MCP",
    accent: {
      bg: "bg-emerald-50 dark:bg-emerald-500/10",
      text: "text-emerald-700 dark:text-emerald-300",
      ring: "ring-emerald-200 dark:ring-emerald-500/30",
      bar: "bg-emerald-500",
      dot: "bg-emerald-500",
    },
  },
  rag_agent: {
    displayName: "Knowledge Agent",
    description: "Document & knowledge base retrieval",
    accent: {
      bg: "bg-amber-50 dark:bg-amber-500/10",
      text: "text-amber-700 dark:text-amber-300",
      ring: "ring-amber-200 dark:ring-amber-500/30",
      bar: "bg-amber-500",
      dot: "bg-amber-500",
    },
  },
};

function agentMeta(name: string): AgentMeta {
  return AGENT_META[name] ?? {
    displayName: roleLabel(name),
    description: "Agent",
    accent: {
      bg: "bg-surface-2", text: "text-fg", ring: "ring-line",
      bar: "bg-slate-500", dot: "bg-slate-500",
    },
  };
}

// ---------------------------------------------------------------------------
// Per-agent stats
// ---------------------------------------------------------------------------

interface AgentStats {
  name: string;
  calls: number;
  successes: number;
  avgLatency: number;
  maxLatency: number;
  lastTs: string;
  recentLatencies: number[];
}

function computeAgentStats(records: AuditRecord[]): AgentStats[] {
  const map = new Map<string, { calls: number; successes: number; totalMs: number; maxMs: number; lastTs: string; latencies: number[] }>();

  for (const r of records) {
    const existing = map.get(r.agent_name);
    if (existing) {
      existing.calls++;
      if (r.status === "SUCCESS") existing.successes++;
      existing.totalMs += r.latency_ms;
      existing.maxMs = Math.max(existing.maxMs, r.latency_ms);
      if (r.timestamp > existing.lastTs) existing.lastTs = r.timestamp;
      existing.latencies.push(r.latency_ms);
    } else {
      map.set(r.agent_name, {
        calls: 1,
        successes: r.status === "SUCCESS" ? 1 : 0,
        totalMs: r.latency_ms,
        maxMs: r.latency_ms,
        lastTs: r.timestamp,
        latencies: [r.latency_ms],
      });
    }
  }

  return Array.from(map.entries())
    .map(([name, s]) => ({
      name,
      calls: s.calls,
      successes: s.successes,
      avgLatency: Math.round(s.totalMs / s.calls),
      maxLatency: s.maxMs,
      lastTs: s.lastTs,
      recentLatencies: s.latencies.slice(-12),
    }))
    .sort((a, b) => b.calls - a.calls);
}

// ---------------------------------------------------------------------------
// Agent performance card
// ---------------------------------------------------------------------------

function AgentCard({ stats }: { stats: AgentStats }) {
  const meta = agentMeta(stats.name);
  const successRate = Math.round((stats.successes / stats.calls) * 100);
  const { accent } = meta;

  // Mini bar chart — relative to max latency across recent
  const maxBar = Math.max(...stats.recentLatencies, 1);

  return (
    <div className={cn("rounded-xl p-4 ring-1 space-y-3", accent.bg, accent.ring)}>
      {/* Header */}
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className={cn("h-2.5 w-2.5 rounded-full shrink-0", accent.dot)} />
            <span className={cn("text-sm font-bold", accent.text)}>{meta.displayName}</span>
          </div>
          <p className="text-[11px] text-faint mt-0.5 leading-relaxed">{meta.description}</p>
        </div>
        <span className="text-[10px] text-faint shrink-0">{timeAgo(stats.lastTs)}</span>
      </div>

      {/* Stats row */}
      <div className="grid grid-cols-3 gap-2 text-center">
        <div>
          <p className={cn("text-lg font-bold tabular-nums", accent.text)}>{stats.calls}</p>
          <p className="text-[10px] text-faint uppercase tracking-wide">Calls</p>
        </div>
        <div>
          <p className={cn(
            "text-lg font-bold tabular-nums",
            successRate >= 90 ? "text-emerald-600 dark:text-emerald-400" :
            successRate >= 70 ? "text-amber-600 dark:text-amber-400" :
            "text-red-600 dark:text-red-400",
          )}>
            {successRate}%
          </p>
          <p className="text-[10px] text-faint uppercase tracking-wide">Success</p>
        </div>
        <div>
          <p className={cn("text-lg font-bold tabular-nums", accent.text)}>{formatLatency(stats.avgLatency)}</p>
          <p className="text-[10px] text-faint uppercase tracking-wide">Avg Time</p>
        </div>
      </div>

      {/* Latency sparkline */}
      {stats.recentLatencies.length > 1 && (
        <div className="flex items-end gap-0.5 h-8">
          {stats.recentLatencies.map((lat, i) => (
            <div
              key={i}
              className={cn("flex-1 rounded-t-sm min-h-[2px]", accent.bar, "opacity-70")}
              style={{ height: `${Math.max(8, Math.round((lat / maxBar) * 32))}px` }}
              title={`${formatLatency(lat)}`}
            />
          ))}
        </div>
      )}
      <p className="text-[9px] text-faint text-right -mt-1">recent latency trend</p>
    </div>
  );
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
    <div className="space-y-4 pt-3">
      <div>
        <p className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-muted">Prompt sent to agent</p>
        <pre className="max-h-48 overflow-y-auto rounded-lg bg-slate-900 dark:bg-slate-950 p-3 text-xs text-slate-200 whitespace-pre-wrap break-words leading-relaxed">
          {rawInput || "—"}
        </pre>
      </div>
      <div>
        <p className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-muted">Agent response</p>
        <div className="max-h-72 overflow-y-auto rounded-lg border border-line bg-surface-2 p-3 text-sm prose prose-sm dark:prose-invert max-w-none [&_table]:w-full [&_table]:text-xs [&_th]:text-left [&_th]:py-1 [&_td]:py-1 [&_table]:border-collapse [&_th]:border [&_th]:border-line [&_td]:border [&_td]:border-line [&_th]:px-2 [&_td]:px-2">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{data.output}</ReactMarkdown>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Invocation table row
// ---------------------------------------------------------------------------

function InvocationRow({ record }: { record: AuditRecord }) {
  const [expanded, setExpanded] = useState(false);
  const meta = agentMeta(record.agent_name);
  const { accent } = meta;

  return (
    <>
      <tr
        className={cn(
          "border-b border-line/50 hover:bg-surface-2 transition-colors cursor-pointer",
          expanded ? "bg-surface-2" : "",
        )}
        onClick={() => setExpanded(v => !v)}
      >
        {/* Agent */}
        <td className="px-4 py-3 whitespace-nowrap">
          <div className="flex items-center gap-2">
            <span className={cn("h-2 w-2 rounded-full shrink-0", accent.dot)} />
            <span className={cn("text-xs font-semibold", accent.text)}>{meta.displayName}</span>
          </div>
        </td>
        {/* User */}
        <td className="px-4 py-3 whitespace-nowrap">
          <div className="flex items-center gap-2">
            <div className={cn("flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-[10px] font-bold", avatarColor(record.user))}>
              {avatarInitial(record.user)}
            </div>
            <div>
              <p className="text-xs font-medium text-fg">{record.user}</p>
              <p className="text-[10px] text-faint">{roleLabel(record.role)}</p>
            </div>
          </div>
        </td>
        {/* Status */}
        <td className="px-4 py-3 whitespace-nowrap">
          {record.status === "SUCCESS" ? (
            <span className="inline-flex items-center gap-1 rounded-full bg-emerald-50 dark:bg-emerald-500/15 px-2.5 py-0.5 text-[10px] font-semibold text-emerald-700 dark:text-emerald-300 ring-1 ring-emerald-200 dark:ring-emerald-500/30">
              <CheckCircle2 className="h-3 w-3" />Success
            </span>
          ) : (
            <span className="inline-flex items-center gap-1 rounded-full bg-red-50 dark:bg-red-500/15 px-2.5 py-0.5 text-[10px] font-semibold text-red-700 dark:text-red-300 ring-1 ring-red-200 dark:ring-red-500/30">
              <XCircle className="h-3 w-3" />{record.status}
            </span>
          )}
        </td>
        {/* Latency */}
        <td className="px-4 py-3 whitespace-nowrap">
          <span className="flex items-center gap-1 text-xs text-muted">
            <Clock className="h-3 w-3" />{formatLatency(record.latency_ms)}
          </span>
        </td>
        {/* Input preview */}
        <td className="px-4 py-3 max-w-xs">
          <p className="text-xs text-muted truncate font-mono">{record.input_preview || "—"}</p>
        </td>
        {/* When */}
        <td className="px-4 py-3 whitespace-nowrap text-xs text-faint" title={formatTs(record.timestamp)}>
          {timeAgo(record.timestamp)}
        </td>
        {/* Expand */}
        <td className="px-3 py-3 text-faint">
          {expanded ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
        </td>
      </tr>

      {/* Expanded detail row */}
      {expanded && (
        <tr className="bg-surface-2 border-b border-line">
          <td colSpan={7} className="px-6 pb-4">
            <AuditDetail requestId={record.request_id} />
          </td>
        </tr>
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function AuditDashboardPage() {
  const { user } = useAuth();

  const { data, isLoading, isError, refetch, isFetching } = useQuery({
    queryKey: ["audit-list", user?.username],
    queryFn: getAudit,
    refetchInterval: 30_000,
  });

  const [statusFilter, setStatusFilter] = useState("all");
  const [agentFilter, setAgentFilter]   = useState("all");
  const [search, setSearch]             = useState("");

  const myRecords = useMemo(
    () => (data?.records ?? []).filter(r => r.user === user?.username),
    [data, user?.username],
  );

  const agentStats = useMemo(() =>
    computeAgentStats(myRecords),
    [myRecords],
  );

  const agents = useMemo(() =>
    Array.from(new Set(myRecords.map(r => r.agent_name))).sort(),
    [myRecords],
  );

  const filtered = useMemo(() => {
    return myRecords.filter(r => {
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
  }, [myRecords, statusFilter, agentFilter, search]);

  const mySuccessCount = useMemo(
    () => myRecords.filter(r => r.status === "SUCCESS").length,
    [myRecords],
  );

  const myAvgLatency = useMemo(() => {
    if (myRecords.length === 0) return 0;
    return Math.round(myRecords.reduce((s, r) => s + r.latency_ms, 0) / myRecords.length);
  }, [myRecords]);

  const successRate = myRecords.length === 0
    ? null
    : Math.round((mySuccessCount / myRecords.length) * 100);

  return (
    <div className="space-y-6 px-4 py-6 sm:px-6 lg:px-8 max-w-5xl mx-auto w-full">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-fg flex items-center gap-2.5">
            <BarChart2 className="h-6 w-6 text-emerald-500" />
            Agent Insights
          </h1>
          <p className="text-sm text-muted mt-0.5">
            Per-agent performance analytics — calls, success rates, and response times
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

      {/* Summary metrics */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
        <Metric label="My Calls" value={isLoading ? "—" : String(myRecords.length)} tone="default" />
        <Metric
          label="Success Rate"
          value={isLoading ? "—" : successRate !== null ? `${successRate}%` : "—"}
          tone={successRate === null ? "default" : successRate >= 90 ? "good" : successRate >= 70 ? "warn" : "bad"}
        />
        <Metric
          label="Avg Latency"
          value={isLoading ? "—" : formatLatency(myAvgLatency)}
          tone="default"
          hint="across my requests"
        />
        <Metric
          label="Errors"
          value={isLoading ? "—" : String(myRecords.length - mySuccessCount)}
          tone={isLoading ? "default" : (myRecords.length - mySuccessCount) === 0 ? "good" : "bad"}
        />
      </div>

      {/* Agent performance cards */}
      {!isLoading && agentStats.length > 0 && (
        <div>
          <div className="flex items-center gap-2 mb-3">
            <TrendingUp className="h-4 w-4 text-faint" />
            <h2 className="text-sm font-semibold text-fg">Agent Performance</h2>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            {agentStats.map(s => <AgentCard key={s.name} stats={s} />)}
          </div>
        </div>
      )}

      {/* Invocations table */}
      <div className="rounded-xl border border-line bg-surface shadow-sm overflow-hidden">
        {/* Table toolbar */}
        <div className="flex flex-wrap items-center gap-3 px-4 py-3 border-b border-line bg-surface-2">
          <div className="flex items-center gap-2">
            <Zap className="h-4 w-4 text-faint" />
            <h2 className="text-sm font-semibold text-fg">Invocations</h2>
          </div>
          <div className="flex items-center gap-2 ml-auto flex-wrap">
            <div className="relative">
              <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-faint pointer-events-none" />
              <input
                type="text"
                placeholder="User or request ID…"
                value={search}
                onChange={e => setSearch(e.target.value)}
                className="rounded-lg border border-line bg-surface pl-8 pr-3 py-1.5 text-xs text-fg placeholder:text-faint focus:outline-none focus:ring-2 focus:ring-brand-500/50 w-44"
              />
            </div>
            <select
              value={statusFilter}
              onChange={e => setStatusFilter(e.target.value)}
              className="rounded-lg border border-line bg-surface px-2.5 py-1.5 text-xs text-fg focus:outline-none focus:ring-2 focus:ring-brand-500/50"
            >
              <option value="all">All statuses</option>
              <option value="SUCCESS">✓ Success</option>
              <option value="ERROR">✕ Error</option>
            </select>
            {agents.length > 1 && (
              <select
                value={agentFilter}
                onChange={e => setAgentFilter(e.target.value)}
                className="rounded-lg border border-line bg-surface px-2.5 py-1.5 text-xs text-fg focus:outline-none focus:ring-2 focus:ring-brand-500/50"
              >
                <option value="all">All agents</option>
                {agents.map(a => <option key={a} value={a}>{agentMeta(a).displayName}</option>)}
              </select>
            )}
            {!isLoading && (
              <span className="text-xs text-faint">{filtered.length} of {myRecords.length}</span>
            )}
          </div>
        </div>

        {isLoading ? (
          <div className="py-12"><CenteredSpinner /></div>
        ) : isError ? (
          <div className="flex items-start gap-3 p-5 text-sm text-red-700 dark:text-red-400">
            <AlertCircle className="h-5 w-5 shrink-0 mt-0.5" />
            <div>
              <p className="font-semibold">Failed to load invocations</p>
              <p className="text-xs opacity-80 mt-0.5">Make sure the API server is running.</p>
            </div>
          </div>
        ) : filtered.length === 0 ? (
          <div className="flex flex-col items-center justify-center gap-3 py-16 text-center">
            <BarChart2 className="h-8 w-8 text-faint" />
            <p className="text-sm font-medium text-fg">
              {data?.total === 0 ? "No invocations yet" : "No results match your filters"}
            </p>
            <p className="text-xs text-muted">
              {data?.total === 0
                ? "Agent calls will appear here after the mesh handles queries."
                : "Try clearing the search or adjusting the filters."}
            </p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left">
              <thead>
                <tr className="border-b border-line">
                  <th className="px-4 py-2.5 text-[10px] font-semibold uppercase tracking-wide text-faint">Agent</th>
                  <th className="px-4 py-2.5 text-[10px] font-semibold uppercase tracking-wide text-faint">User</th>
                  <th className="px-4 py-2.5 text-[10px] font-semibold uppercase tracking-wide text-faint">Status</th>
                  <th className="px-4 py-2.5 text-[10px] font-semibold uppercase tracking-wide text-faint">Latency</th>
                  <th className="px-4 py-2.5 text-[10px] font-semibold uppercase tracking-wide text-faint">Input Preview</th>
                  <th className="px-4 py-2.5 text-[10px] font-semibold uppercase tracking-wide text-faint">When</th>
                  <th className="px-3 py-2.5 w-8" />
                </tr>
              </thead>
              <tbody>
                {filtered.map(r => <InvocationRow key={`${r.request_id}-${r.agent_name}`} record={r} />)}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
