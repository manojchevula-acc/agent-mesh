import { useState, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  ScrollText, RefreshCw, Search, ChevronDown, ChevronUp,
  AlertTriangle, XCircle, CheckCircle2, Activity, ShieldCheck,
  Scale, Bot, ArrowRightLeft, Database, GitMerge, Info, Code2,
  Eye, AlertCircle, Coins,
} from "lucide-react";
import { getLogs } from "@/api/mesh";
import { Card, CardBody } from "@/components/ui/Card";
import { Metric } from "@/components/ui/Metric";
import { Badge } from "@/components/ui/Badge";
import { CenteredSpinner } from "@/components/ui/Spinner";
import { cn } from "@/lib/utils";
import type { LogEntry, RequestGroup } from "@/types/mesh";

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
  return new Date(isoTs).toLocaleString(undefined, { timeStyle: "medium" });
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function relativeOffset(first_ts: string, ts: string): string {
  try {
    const diff = new Date(ts).getTime() - new Date(first_ts).getTime();
    if (diff < 0) return "+0ms";
    if (diff < 1000) return `+${diff}ms`;
    return `+${(diff / 1000).toFixed(1)}s`;
  } catch { return ""; }
}

function avatarInitial(u: string) { return u.charAt(0).toUpperCase(); }
function kFmt(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000)     return `${(n / 1_000).toFixed(1)}K`;
  return n.toLocaleString();
}
const AVATAR_COLORS = [
  "bg-brand-500 text-white", "bg-emerald-500 text-white",
  "bg-amber-500 text-white", "bg-violet-500 text-white", "bg-rose-500 text-white",
];
function avatarColor(u: string) {
  let h = 0;
  for (let i = 0; i < u.length; i++) h = (h * 31 + u.charCodeAt(i)) & 0xffff;
  return AVATAR_COLORS[h % AVATAR_COLORS.length];
}

function roleLabel(r: string) {
  return r.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase());
}

function numFmt(n: number) { return n.toLocaleString(); }

// ---------------------------------------------------------------------------
// JOURNEY MODE — noise filter
// ---------------------------------------------------------------------------

const NOISE_PREFIXES = [
  "agent_framework",
  "opentelemetry",
  "mcp.client",
];
function isNoise(logger: string): boolean {
  return NOISE_PREFIXES.some(p => logger.startsWith(p));
}

// ---------------------------------------------------------------------------
// JOURNEY MODE — message translation
// ---------------------------------------------------------------------------

function translateMessage(e: LogEntry): string {
  const msg = e.msg;

  // ── mesh.system ──────────────────────────────────────────────────────────
  if (msg.startsWith("Request start")) {
    const userM = msg.match(/user=([\w]+)/);
    const roleM = msg.match(/role=([\w_]+)/);
    const lenM  = msg.match(/query_len=(\d+)/);
    const user  = userM ? userM[1] : "user";
    const role  = roleM ? ` (${roleLabel(roleM[1])})` : "";
    const len   = lenM  ? ` — ${lenM[1]}-character query` : "";
    return `Request started by ${user}${role}${len}`;
  }
  if (msg.startsWith("Request complete")) {
    return "Request completed successfully";
  }
  if (msg.startsWith("Feedback recorded")) {
    const ratingM = msg.match(/rating=(\w+)/);
    const rating  = ratingM?.[1];
    return rating === "up"
      ? "User gave positive feedback 👍"
      : rating === "down"
      ? "User gave negative feedback 👎"
      : "User submitted feedback";
  }
  if (msg.startsWith("Logging configured")) return "System started";
  if (msg.startsWith("Observability"))       return "Monitoring connected";
  if (msg.startsWith("httpx"))               return "Network tracing enabled";
  if (msg.startsWith("api_server shutting")) return "Server shutting down";

  // ── mesh.workflow ─────────────────────────────────────────────────────────
  if (/^Input guardrail PASS/i.test(msg))
    return "Safety check passed — query is safe to process";
  if (/^Input guardrail/i.test(msg))
    return `Query blocked at safety check: ${msg.replace(/^input guardrail/i, "").trim()}`;

  if (/^RBAC PASS/i.test(msg)) {
    const roleM = msg.match(/role=([\w_]+)/);
    return roleM
      ? `Access permitted for ${roleLabel(roleM[1])}`
      : "Access permission granted";
  }
  if (/^RBAC/i.test(msg))
    return `Access check: ${msg.replace(/^RBAC\s*/i, "")}`;

  if (/^Compliance BYPASS/i.test(msg))
    return "Compliance check skipped (not required for this request type)";
  if (/^Compliance PASS/i.test(msg))
    return "Compliance check passed — response meets policy requirements";
  if (/^Compliance FAIL/i.test(msg))
    return "Request blocked by compliance policy";
  if (/^Compliance/i.test(msg))
    return `Compliance: ${msg.replace(/^compliance\s*/i, "")}`;

  if (/^Domain direct answer/i.test(msg)) {
    const charM = msg.match(/(\d+)\s*chars?/);
    return charM
      ? `Answer prepared (${numFmt(parseInt(charM[1]))} characters)`
      : "Answer prepared";
  }
  if (/^Request complete trail=/i.test(msg))
    return "All pipeline stages completed";

  if (/^Policy/i.test(msg))
    return `Policy check: ${msg.replace(/^policy\s*/i, "")}`;

  // ── mesh.agent ───────────────────────────────────────────────────────────
  if (msg.startsWith("agent=")) {
    const agentM = msg.match(/agent=([\w]+)/);
    const latM   = msg.match(/latency_ms=(\d+)/);
    const agent  = agentM?.[1] ?? "Agent";
    const dur    = latM ? ` in ${(parseInt(latM[1]) / 1000).toFixed(1)}s` : "";
    const ok     = e.status === "SUCCESS";
    return `${agent} finished${dur}${ok ? " ✓" : ""}`;
  }

  // ── mesh.a2a ──────────────────────────────────────────────────────────────
  if (msg.startsWith("A2A call")) {
    const nodeM = msg.match(/node=([\w_]+)/);
    const msM   = msg.match(/\((\d+)\s*ms/);
    const charM = msg.match(/(\d+)\s*chars?\)/);
    const node  = nodeM ? nodeM[1].replace(/_/g, " ") : "agent";
    const dur   = msM  ? ` in ${(parseInt(msM[1]) / 1000).toFixed(1)}s` : "";
    const chars = charM ? `, ${numFmt(parseInt(charM[1]))} chars` : "";
    return `Contacted ${node}${dur}${chars}`;
  }

  // ── mesh.mcp ──────────────────────────────────────────────────────────────
  if (msg.includes("latency_ms=")) {
    const latM   = msg.match(/latency_ms=(\d+)/);
    const agentM = msg.match(/agent=([\w]+)/);
    const dur    = latM ? ` in ${(parseInt(latM[1]) / 1000).toFixed(1)}s` : "";
    const agent  = agentM ? ` via ${agentM[1]}` : "";
    return `Banking data retrieved${agent}${dur}`;
  }

  return msg; // safe fallback
}

// ---------------------------------------------------------------------------
// JOURNEY MODE — step icon + stage label per logger
// ---------------------------------------------------------------------------

type StageInfo = { Icon: React.ElementType; label: string; iconClass: string };

function stageInfo(entry: LogEntry): StageInfo {
  const { logger, msg } = entry;
  if (logger.startsWith("mesh.system"))   return { Icon: Activity,       label: "System",               iconClass: "text-brand-500" };
  if (logger.startsWith("mesh.mcp"))      return { Icon: Database,       label: "Data Retrieval",       iconClass: "text-rose-500" };
  if (logger.startsWith("mesh.a2a"))      return { Icon: ArrowRightLeft,  label: "Agent Communication",  iconClass: "text-amber-500" };
  if (logger.startsWith("mesh.agent"))    return { Icon: Bot,             label: "AI Agent",             iconClass: "text-emerald-500" };
  if (logger.startsWith("mesh.workflow")) {
    if (/compliance/i.test(msg))          return { Icon: Scale,           label: "Compliance",           iconClass: "text-violet-500" };
    if (/guardrail/i.test(msg) || /rbac/i.test(msg))
                                          return { Icon: ShieldCheck,     label: "Safety & Access",      iconClass: "text-blue-500" };
    return                                       { Icon: GitMerge,        label: "Pipeline",             iconClass: "text-slate-500" };
  }
  return { Icon: Info, label: "System", iconClass: "text-faint" };
}

// Color of the outcome dot in Journey view
function outcomeClass(e: LogEntry): string {
  if (e.level === "ERROR")   return "bg-red-500";
  if (e.level === "WARNING") return "bg-amber-500";
  if (e.status === "SUCCESS" || e.status === "PASS") return "bg-emerald-500";
  if (e.status === "FAIL" || e.status === "BLOCK")   return "bg-red-500";
  return "bg-blue-400";
}

// ---------------------------------------------------------------------------
// TECHNICAL MODE — helpers (same as original)
// ---------------------------------------------------------------------------

const LEVEL_DOT: Record<string, string> = {
  INFO: "bg-blue-500", WARNING: "bg-amber-500", ERROR: "bg-red-500", DEBUG: "bg-slate-400",
};
const STAGE_COLORS: Record<string, string> = {
  "mesh.system":   "border-brand-400 dark:border-brand-500",
  "mesh.workflow": "border-violet-400 dark:border-violet-500",
  "mesh.agent":    "border-emerald-400 dark:border-emerald-500",
  "mesh.a2a":      "border-amber-400 dark:border-amber-500",
  "mesh.mcp":      "border-rose-400 dark:border-rose-500",
};
function stageColor(logger: string): string {
  for (const [k, v] of Object.entries(STAGE_COLORS)) if (logger.startsWith(k)) return v;
  return "border-slate-300 dark:border-slate-600";
}
function loggerShort(l: string): string {
  const p = l.split(".");
  if (p[0] === "mesh" && p[1]) return p[1];
  if (p[0] === "agent_framework") return "framework";
  return l.length > 24 ? l.slice(0, 24) + "…" : l;
}
interface Stage { logger: string; entries: LogEntry[]; }
function buildStages(entries: LogEntry[]): Stage[] {
  const out: Stage[] = [];
  for (const e of entries) {
    const last = out[out.length - 1];
    if (last && last.logger === e.logger) last.entries.push(e);
    else out.push({ logger: e.logger, entries: [e] });
  }
  return out;
}

// ---------------------------------------------------------------------------
// Journey step — one translated log line
// ---------------------------------------------------------------------------

function JourneyStep({ entry, firstTs }: { entry: LogEntry; firstTs: string }) {
  const { Icon, label, iconClass } = stageInfo(entry);
  const translated = translateMessage(entry);
  const offset = relativeOffset(firstTs, entry.ts);

  return (
    <div className="flex items-start gap-3 py-2">
      {/* Stage icon */}
      <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-surface-2 border border-line mt-0.5">
        <Icon className={cn("h-3.5 w-3.5", iconClass)} />
      </div>

      {/* Content */}
      <div className="flex-1 min-w-0">
        <p className="text-[10px] font-semibold uppercase tracking-wide text-faint mb-0.5">{label}</p>
        <p className={cn(
          "text-sm leading-relaxed",
          entry.level === "ERROR" ? "text-red-600 dark:text-red-400" :
          entry.level === "WARNING" ? "text-amber-600 dark:text-amber-400" :
          "text-fg",
        )}>
          {translated}
        </p>
        {/* Token chips — shown on steps where token data was joined from audit trail */}
        {(entry.total_tokens ?? 0) > 0 && (
          <div className="flex items-center gap-1.5 mt-1.5 flex-wrap">
            {entry.tokens_estimated && (
              <span className="text-[9px] text-faint italic">approx.</span>
            )}
            <span className="flex items-center gap-1 rounded-full bg-blue-50 dark:bg-blue-500/10 px-2 py-0.5 text-[10px] font-medium text-blue-700 dark:text-blue-300 ring-1 ring-blue-200 dark:ring-blue-500/30">
              In {entry.tokens_estimated ? "~" : ""}{(entry.input_tokens ?? 0).toLocaleString()}
            </span>
            <span className="flex items-center gap-1 rounded-full bg-violet-50 dark:bg-violet-500/10 px-2 py-0.5 text-[10px] font-medium text-violet-700 dark:text-violet-300 ring-1 ring-violet-200 dark:ring-violet-500/30">
              Out {entry.tokens_estimated ? "~" : ""}{(entry.output_tokens ?? 0).toLocaleString()}
            </span>
            <span className="flex items-center gap-1 rounded-full bg-surface-2 border border-line px-2 py-0.5 text-[10px] font-medium text-fg">
              Total {entry.tokens_estimated ? "~" : ""}{(entry.total_tokens ?? 0).toLocaleString()}
            </span>
          </div>
        )}
      </div>

      {/* Time offset */}
      <span className="shrink-0 text-[10px] tabular-nums text-faint mt-1">{offset}</span>

      {/* Outcome dot */}
      <span className={cn("mt-2 h-2 w-2 shrink-0 rounded-full", outcomeClass(entry))} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Technical log line (raw view)
// ---------------------------------------------------------------------------

function TechLogLine({ entry, firstTs }: { entry: LogEntry; firstTs: string }) {
  const dot = LEVEL_DOT[entry.level] ?? "bg-slate-400";
  const offset = relativeOffset(firstTs, entry.ts);
  return (
    <div className="flex items-start gap-2 py-0.5">
      <span className={cn("mt-1.5 h-2 w-2 shrink-0 rounded-full", dot)} />
      <span className="w-14 shrink-0 text-right text-[10px] tabular-nums text-faint pt-0.5">{offset}</span>
      <p className="flex-1 text-xs text-fg leading-relaxed break-words min-w-0">
        {entry.msg}
        {entry.status && <span className="ml-1.5 text-[10px] font-semibold uppercase tracking-wide text-muted">[{entry.status}]</span>}
        {entry.agent  && <span className="ml-1.5 text-[10px] text-muted">agent:{entry.agent}</span>}
        {entry.node   && <span className="ml-1.5 text-[10px] text-muted">node:{entry.node}</span>}
      </p>
      <span className="shrink-0 text-[10px] tabular-nums text-faint font-mono">{entry.span_id !== "-" ? entry.span_id.slice(0, 8) : ""}</span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Request group — Journey mode header
// ---------------------------------------------------------------------------

function journeySummary(group: RequestGroup): string {
  const entries = group.entries;
  const trailEntry = entries.find(e => e.msg.startsWith("Request complete trail="));
  const dur = formatDuration(group.duration_ms);

  // Try to infer what happened from trail
  if (trailEntry) {
    const trail = trailEntry.msg.replace("Request complete trail=", "");
    const blocked = /block|fail/i.test(trail);
    const user = group.user ?? "The user";
    if (blocked) return `${user} made a request that was blocked during processing. Total time: ${dur}.`;
    const agentM = trail.match(/data_agent|rag_agent|price_assist/i);
    const agent = agentM ? agentM[0].replace(/_/g, " ") : "an AI agent";
    return `${user} made a request. It passed all safety checks and was answered by the ${agent} in ${dur}.`;
  }
  const hasError   = group.has_error;
  const hasWarning = group.has_warning;
  const user = group.user ?? "A user";
  if (hasError)   return `${user} made a request that encountered an error. Total time: ${dur}.`;
  if (hasWarning) return `${user} made a request that completed with warnings. Total time: ${dur}.`;
  return `${user} made a request that was processed in ${dur}.`;
}

// ---------------------------------------------------------------------------
// Request group card
// ---------------------------------------------------------------------------

function RequestGroupCard({
  group, levelFilter, loggerFilter, msgSearch, journeyMode,
}: {
  group: RequestGroup;
  levelFilter: string; loggerFilter: string; msgSearch: string;
  journeyMode: boolean;
}) {
  const [expanded, setExpanded] = useState(false);

  // Journey: strip noise, then apply filters
  const visibleEntries = useMemo(() => {
    return group.entries.filter(e => {
      if (journeyMode && isNoise(e.logger)) return false;
      if (levelFilter !== "all" && e.level !== levelFilter) return false;
      if (loggerFilter !== "all" && !e.logger.startsWith(loggerFilter)) return false;
      if (msgSearch) {
        const q = msgSearch.toLowerCase();
        const haystack = journeyMode ? translateMessage(e).toLowerCase() : e.msg.toLowerCase();
        if (!haystack.includes(q)) return false;
      }
      return true;
    });
  }, [group.entries, journeyMode, levelFilter, loggerFilter, msgSearch]);

  const stages = useMemo(() => buildStages(visibleEntries), [visibleEntries]);

  const statusIcon = group.has_error
    ? <XCircle className="h-5 w-5 text-red-500 shrink-0" />
    : group.has_warning
    ? <AlertTriangle className="h-5 w-5 text-amber-500 shrink-0" />
    : <CheckCircle2 className="h-5 w-5 text-emerald-500 shrink-0" />;

  const borderClass = group.has_error
    ? "border-red-200 dark:border-red-800"
    : group.has_warning
    ? "border-amber-200 dark:border-amber-800"
    : "border-line";

  return (
    <div className={cn("rounded-xl border bg-surface shadow-sm overflow-hidden", borderClass)}>
      {/* Header */}
      <button
        className="w-full flex items-center gap-3 px-4 py-3.5 text-left hover:bg-surface-2 transition-colors"
        onClick={() => setExpanded(v => !v)}
      >
        {expanded ? <ChevronUp className="h-4 w-4 shrink-0 text-muted" /> : <ChevronDown className="h-4 w-4 shrink-0 text-muted" />}

        {statusIcon}

        {/* User */}
        {group.user && (
          <div className="flex items-center gap-2 shrink-0">
            <div className={cn("flex h-7 w-7 items-center justify-center rounded-full text-xs font-bold", avatarColor(group.user))}>
              {avatarInitial(group.user)}
            </div>
            <div className="text-left">
              <p className="text-sm font-semibold text-fg leading-tight">{group.user}</p>
              {group.session_id && (
                <p className="text-[10px] text-faint leading-tight hidden sm:block truncate max-w-[140px]">
                  {group.session_id}
                </p>
              )}
            </div>
          </div>
        )}

        {journeyMode ? (
          <>
            <Badge tone="slate">{visibleEntries.length} steps</Badge>
            <span className="text-xs font-semibold text-fg">{formatDuration(group.duration_ms)}</span>
            {(group.token_total ?? 0) > 0 && (
              <span className="flex items-center gap-1 rounded-full bg-amber-50 dark:bg-amber-500/10 px-2.5 py-0.5 text-[10px] font-medium text-amber-700 dark:text-amber-300 ring-1 ring-amber-200 dark:ring-amber-500/30 shrink-0">
                <Coins className="h-3 w-3" /> {group.token_estimated ? "~" : ""}{kFmt(group.token_total!)} tokens
              </span>
            )}
          </>
        ) : (
          <>
            <span className="shrink-0 rounded bg-surface-2 border border-line px-2 py-0.5 text-xs font-mono font-semibold text-muted">
              {group.request_id}
            </span>
            <Badge tone="slate">{group.entry_count} lines</Badge>
            <span className="text-xs text-muted">{formatDuration(group.duration_ms)}</span>
          </>
        )}

        <span className="ml-auto text-xs text-faint shrink-0" title={group.first_ts}>{timeAgo(group.first_ts)}</span>
      </button>

      {/* Body */}
      {expanded && (
        <div className="border-t border-line px-4 py-3">
          {/* Journey summary sentence */}
          {journeyMode && (
            <p className="text-sm text-muted italic mb-3 pb-3 border-b border-line">
              {journeySummary(group)}
            </p>
          )}

          {visibleEntries.length === 0 ? (
            <p className="text-xs text-muted py-2">No entries match the current filters.</p>
          ) : journeyMode ? (
            /* Journey view: flat chronological steps with icons */
            <div className="divide-y divide-line/50">
              {visibleEntries.map((e, i) => (
                <JourneyStep key={i} entry={e} firstTs={group.first_ts} />
              ))}
            </div>
          ) : (
            /* Technical view: stage-grouped raw lines */
            <div className="space-y-1">
              {stages.map((stage, si) => (
                <div key={si} className={cn("border-l-2 pl-3 py-1", stageColor(stage.logger))}>
                  <div className="flex items-center gap-2 mb-1">
                    <span className="text-[10px] font-semibold uppercase tracking-widest text-faint">{loggerShort(stage.logger)}</span>
                    <span className="text-[9px] text-faint truncate hidden sm:inline">{stage.logger}</span>
                  </div>
                  <div className="space-y-0.5">
                    {stage.entries.map((e, ei) => (
                      <TechLogLine key={ei} entry={e} firstTs={group.first_ts} />
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// System entries accordion (technical view only)
// ---------------------------------------------------------------------------

function SystemEntriesAccordion({ entries }: { entries: LogEntry[] }) {
  const [open, setOpen] = useState(false);
  if (entries.length === 0) return null;
  return (
    <div className="rounded-xl border border-line bg-surface shadow-sm overflow-hidden opacity-60">
      <button
        className="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-surface-2 transition-colors"
        onClick={() => setOpen(v => !v)}
      >
        {open ? <ChevronUp className="h-4 w-4 text-muted" /> : <ChevronDown className="h-4 w-4 text-muted" />}
        <Info className="h-4 w-4 text-faint" />
        <span className="text-sm text-muted font-medium">System / startup entries</span>
        <Badge tone="slate">{entries.length} lines</Badge>
      </button>
      {open && (
        <div className="border-t border-line px-4 py-3 space-y-0.5">
          {entries.map((e, i) => (
            <div key={i} className="flex items-start gap-2 py-0.5">
              <span className={cn("mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full", LEVEL_DOT[e.level] ?? "bg-slate-400")} />
              <span className="text-[10px] text-faint shrink-0 w-20 tabular-nums">{formatTs(e.ts)}</span>
              <span className="text-[10px] text-faint shrink-0 w-24 truncate font-mono">{e.logger}</span>
              <p className="text-xs text-muted leading-relaxed break-words min-w-0">{e.msg}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function LogsDashboardPage() {
  const { data, isLoading, isError, refetch, isFetching } = useQuery({
    queryKey: ["logs-list"],
    queryFn: getLogs,
    refetchInterval: 15_000,
  });

  const [journeyMode, setJourneyMode] = useState(true);
  const [levelFilter, setLevelFilter]   = useState("all");
  const [loggerFilter, setLoggerFilter] = useState("all");
  const [requestSearch, setRequestSearch] = useState("");
  const [msgSearch, setMsgSearch]         = useState("");

  const meshLoggers = useMemo(() => {
    if (!data) return [];
    return data.loggers.filter(l => l.startsWith("mesh.")).sort();
  }, [data]);

  const avgDuration = useMemo(() => {
    if (!data || data.groups.length === 0) return 0;
    return Math.round(data.groups.reduce((s, g) => s + g.duration_ms, 0) / data.groups.length);
  }, [data]);

  const visibleGroups = useMemo(() => {
    if (!data) return [];
    return data.groups.filter(g => {
      if (requestSearch) {
        const q = requestSearch.toLowerCase();
        if (!g.request_id.toLowerCase().includes(q) &&
            !(g.user ?? "").toLowerCase().includes(q) &&
            !(g.session_id ?? "").toLowerCase().includes(q)) return false;
      }
      if (levelFilter !== "all" || loggerFilter !== "all" || msgSearch) {
        const hasMatch = g.entries.some(e => {
          if (journeyMode && isNoise(e.logger)) return false;
          if (levelFilter !== "all" && e.level !== levelFilter) return false;
          if (loggerFilter !== "all" && !e.logger.startsWith(loggerFilter)) return false;
          if (msgSearch) {
            const q = msgSearch.toLowerCase();
            const hay = journeyMode ? translateMessage(e).toLowerCase() : e.msg.toLowerCase();
            if (!hay.includes(q)) return false;
          }
          return true;
        });
        if (!hasMatch) return false;
      }
      return true;
    });
  }, [data, requestSearch, levelFilter, loggerFilter, msgSearch, journeyMode]);

  return (
    <div className="space-y-6 px-4 py-6 sm:px-6 lg:px-8 max-w-5xl mx-auto w-full">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-fg">
            {journeyMode ? "Request Journey" : "Logs"}
          </h1>
          <p className="text-sm text-muted mt-0.5">
            {journeyMode
              ? "Plain-English view of what happened during each request"
              : "Raw agent_mesh.log — grouped by request with pipeline stage hierarchy"}
          </p>
        </div>

        <div className="flex items-center gap-2">
          {/* Journey / Technical toggle */}
          <button
            onClick={() => setJourneyMode(v => !v)}
            className={cn(
              "flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium border transition-colors",
              journeyMode
                ? "bg-brand-50 dark:bg-brand-500/10 text-brand-700 dark:text-brand-300 border-brand-200 dark:border-brand-500/30"
                : "bg-surface-2 text-muted border-line hover:text-fg",
            )}
            title={journeyMode ? "Switch to technical/raw view" : "Switch to friendly journey view"}
          >
            {journeyMode ? <Eye className="h-3.5 w-3.5" /> : <Code2 className="h-3.5 w-3.5" />}
            {journeyMode ? "Journey view" : "Technical view"}
          </button>

          <button
            onClick={() => refetch()}
            disabled={isFetching}
            className="flex items-center gap-1.5 text-sm text-muted hover:text-fg transition-colors disabled:opacity-50"
          >
            <RefreshCw className={cn("h-4 w-4", isFetching && "animate-spin")} />
            Refresh
          </button>
        </div>
      </div>

      {/* Stat tiles */}
      <div className="grid grid-cols-2 sm:grid-cols-5 gap-4">
        <Metric
          label="Log Lines"
          value={isLoading ? "—" : String(data?.total_entries ?? 0)}
          tone="default"
        />
        <Metric
          label="Queries Processed"
          value={isLoading ? "—" : String(data?.unique_requests ?? 0)}
          tone="default"
        />
        <Metric
          label="Avg Response Time"
          value={isLoading ? "—" : avgDuration > 0 ? formatDuration(avgDuration) : "—"}
          tone="default"
          hint="per request"
        />
        <Metric
          label="Warnings"
          value={isLoading ? "—" : String(data?.warning_count ?? 0)}
          tone={isLoading ? "default" : (data?.warning_count ?? 0) > 0 ? "warn" : "good"}
        />
        <Metric
          label="Errors"
          value={isLoading ? "—" : String(data?.error_count ?? 0)}
          tone={isLoading ? "default" : (data?.error_count ?? 0) > 0 ? "bad" : "good"}
        />
      </div>

      {/* Journey mode legend */}
      {journeyMode && !isLoading && data && data.unique_requests > 0 && (
        <Card>
          <CardBody>
            <p className="text-xs font-semibold uppercase tracking-wide text-muted mb-2">What each icon means</p>
            <div className="flex flex-wrap gap-4 text-xs text-muted">
              {[
                { Icon: Activity,       label: "System start/end",     cls: "text-brand-500" },
                { Icon: ShieldCheck,    label: "Safety & access check", cls: "text-blue-500" },
                { Icon: Scale,          label: "Compliance check",      cls: "text-violet-500" },
                { Icon: Bot,            label: "AI Agent processing",   cls: "text-emerald-500" },
                { Icon: ArrowRightLeft, label: "Agent communication",   cls: "text-amber-500" },
                { Icon: Database,       label: "Data retrieval",        cls: "text-rose-500" },
              ].map(({ Icon, label, cls }) => (
                <span key={label} className="flex items-center gap-1.5">
                  <div className="flex h-5 w-5 items-center justify-center rounded-full bg-surface-2 border border-line">
                    <Icon className={cn("h-3 w-3", cls)} />
                  </div>
                  {label}
                </span>
              ))}
            </div>
          </CardBody>
        </Card>
      )}

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="relative min-w-[160px]">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-faint pointer-events-none" />
          <input
            type="text"
            placeholder="Request ID / user…"
            value={requestSearch}
            onChange={e => setRequestSearch(e.target.value)}
            className="w-full rounded-lg border border-line bg-surface pl-9 pr-3 py-2 text-sm text-fg placeholder:text-faint focus:outline-none focus:ring-2 focus:ring-brand-500/50"
          />
        </div>
        <div className="relative flex-1 min-w-[160px]">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-faint pointer-events-none" />
          <input
            type="text"
            placeholder={journeyMode ? "Search steps…" : "Search messages…"}
            value={msgSearch}
            onChange={e => setMsgSearch(e.target.value)}
            className="w-full rounded-lg border border-line bg-surface pl-9 pr-3 py-2 text-sm text-fg placeholder:text-faint focus:outline-none focus:ring-2 focus:ring-brand-500/50"
          />
        </div>
        <select
          value={levelFilter}
          onChange={e => setLevelFilter(e.target.value)}
          className="rounded-lg border border-line bg-surface px-3 py-2 text-sm text-fg focus:outline-none focus:ring-2 focus:ring-brand-500/50"
        >
          <option value="all">All levels</option>
          <option value="INFO">ℹ Info</option>
          <option value="WARNING">⚠ Warnings</option>
          <option value="ERROR">✕ Errors only</option>
        </select>
        {!journeyMode && meshLoggers.length > 1 && (
          <select
            value={loggerFilter}
            onChange={e => setLoggerFilter(e.target.value)}
            className="rounded-lg border border-line bg-surface px-3 py-2 text-sm text-fg focus:outline-none focus:ring-2 focus:ring-brand-500/50"
          >
            <option value="all">All stages</option>
            {meshLoggers.map(l => <option key={l} value={l}>{l}</option>)}
          </select>
        )}
        {!isLoading && data && (
          <span className="text-xs text-muted ml-auto">
            Showing {visibleGroups.length} of {data.unique_requests} requests
          </span>
        )}
      </div>

      {/* Content */}
      {isLoading ? (
        <CenteredSpinner />
      ) : isError ? (
        <div className="rounded-xl border border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-950/30 p-5 text-sm text-red-700 dark:text-red-400 flex items-start gap-3">
          <AlertCircle className="h-5 w-5 shrink-0 mt-0.5" />
          <div>
            <p className="font-semibold">Failed to load logs</p>
            <p className="mt-0.5 text-xs opacity-80">Make sure the API server is running.</p>
          </div>
        </div>
      ) : visibleGroups.length === 0 && (data?.unique_requests ?? 0) === 0 ? (
        <div className="flex flex-col items-center justify-center gap-3 py-20 text-center">
          <ScrollText className="h-10 w-10 text-faint" />
          <p className="text-base font-medium text-fg">No activity yet</p>
          <p className="text-sm text-muted">Request journeys will appear here after users submit queries.</p>
        </div>
      ) : visibleGroups.length === 0 ? (
        <div className="flex flex-col items-center justify-center gap-3 py-16 text-center">
          <ScrollText className="h-8 w-8 text-faint" />
          <p className="text-base font-medium text-fg">No requests match your filters</p>
          <p className="text-sm text-muted">Try clearing the search or changing the level filter.</p>
        </div>
      ) : (
        <div className="space-y-3">
          {visibleGroups.map(group => (
            <RequestGroupCard
              key={group.request_id}
              group={group}
              levelFilter={levelFilter}
              loggerFilter={loggerFilter}
              msgSearch={msgSearch}
              journeyMode={journeyMode}
            />
          ))}
          {!journeyMode && !requestSearch && levelFilter === "all" && loggerFilter === "all" && !msgSearch && (
            <SystemEntriesAccordion entries={data?.system_entries ?? []} />
          )}
        </div>
      )}
    </div>
  );
}
