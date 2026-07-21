import { useState, useMemo, useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import {
  GitBranch, RefreshCw, Search, ChevronDown, ChevronUp,
  XCircle, CheckCircle2, Activity, ShieldCheck,
  Scale, Bot, ArrowRightLeft, Database, GitMerge, Info,
  AlertCircle, Coins,
} from "lucide-react";
import { getLogs } from "@/api/mesh";
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
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
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

const NOISE_PREFIXES = ["agent_framework", "opentelemetry", "mcp.client"];
function isNoise(logger: string): boolean {
  return NOISE_PREFIXES.some(p => logger.startsWith(p));
}

// ---------------------------------------------------------------------------
// Message translation
// ---------------------------------------------------------------------------

function translateMessage(e: LogEntry): string {
  const msg = e.msg;

  if (msg.startsWith("Request start")) {
    const userM = msg.match(/user=([\w]+)/);
    const roleM = msg.match(/role=([\w_]+)/);
    const lenM  = msg.match(/query_len=(\d+)/);
    const user  = userM ? userM[1] : "user";
    const role  = roleM ? ` (${roleLabel(roleM[1])})` : "";
    const len   = lenM  ? ` — ${lenM[1]}-character query` : "";
    return `Request started by ${user}${role}${len}`;
  }
  if (msg.startsWith("Request complete")) return "Request completed successfully";
  if (msg.startsWith("Feedback recorded")) {
    const ratingM = msg.match(/rating=(\w+)/);
    const rating  = ratingM?.[1];
    return rating === "up" ? "User gave positive feedback 👍"
         : rating === "down" ? "User gave negative feedback 👎"
         : "User submitted feedback";
  }
  if (msg.startsWith("Logging configured")) return "System started";
  if (msg.startsWith("Observability"))       return "Monitoring connected";
  if (msg.startsWith("httpx"))               return "Network tracing enabled";
  if (msg.startsWith("api_server shutting")) return "Server shutting down";

  if (/^Input guardrail PASS/i.test(msg)) return "Safety check passed — query is safe to process";
  if (/^Input guardrail/i.test(msg))
    return `Query blocked at safety check: ${msg.replace(/^input guardrail/i, "").trim()}`;

  if (/^RBAC PASS/i.test(msg)) {
    const roleM = msg.match(/role=([\w_]+)/);
    return roleM ? `Access permitted for ${roleLabel(roleM[1])}` : "Access permission granted";
  }
  if (/^RBAC/i.test(msg)) return `Access check: ${msg.replace(/^RBAC\s*/i, "")}`;

  if (/^Compliance BYPASS/i.test(msg)) return "Compliance check skipped (not required for this role)";
  if (/^Compliance PASS/i.test(msg))   return "Compliance check passed — response meets policy requirements";
  if (/^Compliance FAIL/i.test(msg))   return "Request blocked by compliance policy";
  if (/^Compliance/i.test(msg))        return `Compliance: ${msg.replace(/^compliance\s*/i, "")}`;

  if (/^Domain direct answer/i.test(msg)) {
    const charM = msg.match(/(\d+)\s*chars?/);
    return charM ? `Answer prepared (${numFmt(parseInt(charM[1]))} characters)` : "Answer prepared";
  }
  if (/^Request complete trail=/i.test(msg)) return "All pipeline stages completed";
  if (/^Policy/i.test(msg)) return `Policy check: ${msg.replace(/^policy\s*/i, "")}`;

  if (/price_assist returned bare tool-call/i.test(msg))
    return "Price Assistant produced an incomplete response — automatically retrying";
  if (/price_assist returned meta-response/i.test(msg))
    return "Price Assistant returned a non-answer response — automatically retrying";
  if (/price_assist returned hallucinated/i.test(msg))
    return "Price Assistant produced a placeholder response — automatically retrying";
  if (/Domain hop failed node=price_assist/i.test(msg))
    return "Price Assistant could not be reached — request failed";
  if (/Domain direct hop failed node=data_agent/i.test(msg))
    return "Data Agent could not be reached — request failed";
  if (/DomainExecutor: answer was empty/i.test(msg))
    return "AI response was empty after processing — attempting recovery";
  if (/DomainExecutor: answer still empty/i.test(msg))
    return "AI response remained empty after recovery attempt — retrying with simpler prompt";
  if (/DomainExecutor: plain-answer retry failed/i.test(msg))
    return "All retry attempts failed — could not generate a valid response";
  if (/Input guardrail BLOCK/i.test(msg))
    return "Query was blocked by the safety filter";
  if (/RBAC BLOCK/i.test(msg)) return "Request blocked — user role is not authorized";

  if (msg.startsWith("agent=")) {
    const agentM = msg.match(/agent=([\w]+)/);
    const latM   = msg.match(/latency_ms=(\d+)/);
    const agent  = agentM?.[1] ?? "Agent";
    const dur    = latM ? ` in ${(parseInt(latM[1]) / 1000).toFixed(1)}s` : "";
    return `${agent} finished${dur}${e.status === "SUCCESS" ? " ✓" : ""}`;
  }

  if (msg.startsWith("A2A call")) {
    const nodeM = msg.match(/node=([\w_]+)/);
    const msM   = msg.match(/\((\d+)\s*ms/);
    const charM = msg.match(/(\d+)\s*chars?\)/);
    const node  = nodeM ? nodeM[1].replace(/_/g, " ") : "agent";
    const dur   = msM  ? ` in ${(parseInt(msM[1]) / 1000).toFixed(1)}s` : "";
    const chars = charM ? `, ${numFmt(parseInt(charM[1]))} chars` : "";
    return `Contacted ${node}${dur}${chars}`;
  }

  if (msg.includes("latency_ms=")) {
    const latM   = msg.match(/latency_ms=(\d+)/);
    const agentM = msg.match(/agent=([\w]+)/);
    const dur    = latM ? ` in ${(parseInt(latM[1]) / 1000).toFixed(1)}s` : "";
    const agent  = agentM ? ` via ${agentM[1]}` : "";
    return `Banking data retrieved${agent}${dur}`;
  }

  return msg;
}

// ---------------------------------------------------------------------------
// Stage icon + label
// ---------------------------------------------------------------------------

type StageInfo = { Icon: React.ElementType; label: string; iconClass: string };

function stageInfo(entry: LogEntry): StageInfo {
  const { logger, msg } = entry;
  if (logger.startsWith("mesh.system"))   return { Icon: Activity,       label: "System",              iconClass: "text-brand-500" };
  if (logger.startsWith("mesh.mcp"))      return { Icon: Database,       label: "Data Retrieval",      iconClass: "text-rose-500" };
  if (logger.startsWith("mesh.a2a"))      return { Icon: ArrowRightLeft, label: "Agent Communication", iconClass: "text-amber-500" };
  if (logger.startsWith("mesh.agent"))    return { Icon: Bot,            label: "AI Agent",            iconClass: "text-emerald-500" };
  if (logger.startsWith("mesh.workflow")) {
    if (/compliance/i.test(msg))          return { Icon: Scale,          label: "Compliance",          iconClass: "text-violet-500" };
    if (/guardrail|rbac/i.test(msg))      return { Icon: ShieldCheck,    label: "Safety & Access",     iconClass: "text-blue-500" };
    return                                       { Icon: GitMerge,       label: "Pipeline",            iconClass: "text-slate-500" };
  }
  return { Icon: Info, label: "System", iconClass: "text-faint" };
}

function outcomeClass(e: LogEntry): string {
  if (e.level === "ERROR")                             return "bg-red-500";
  if (e.level === "WARNING")                           return "bg-amber-400";
  if (e.status === "SUCCESS" || e.status === "PASS")   return "bg-emerald-500";
  if (e.status === "FAIL" || e.status === "BLOCK")     return "bg-red-500";
  return "bg-blue-400";
}

// ---------------------------------------------------------------------------
// Journey step — timeline style
// ---------------------------------------------------------------------------

function JourneyStep({
  entry, firstTs, isLast,
}: { entry: LogEntry; firstTs: string; isLast: boolean }) {
  const { Icon, label, iconClass } = stageInfo(entry);
  const translated = translateMessage(entry);
  const offset = relativeOffset(firstTs, entry.ts);

  return (
    <div className="flex gap-3">
      {/* Timeline column */}
      <div className="flex flex-col items-center shrink-0">
        <div className={cn(
          "flex h-8 w-8 items-center justify-center rounded-full border-2 border-surface bg-surface-2 z-10",
          entry.level === "ERROR" ? "ring-2 ring-red-300 dark:ring-red-500/50" : "",
        )}>
          <Icon className={cn("h-4 w-4", iconClass)} />
        </div>
        {!isLast && <div className="w-px flex-1 bg-line mt-1" />}
      </div>

      {/* Content */}
      <div className={cn("flex-1 min-w-0 pb-4", isLast ? "pb-0" : "")}>
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <p className="text-[10px] font-semibold uppercase tracking-wide text-faint mb-0.5">{label}</p>
            <p className={cn(
              "text-sm leading-relaxed",
              entry.level === "ERROR"   ? "text-red-600 dark:text-red-400 font-medium" :
              entry.level === "WARNING" ? "text-amber-600 dark:text-amber-400" :
              "text-fg",
            )}>
              {translated}
            </p>
            {(entry.total_tokens ?? 0) > 0 && (
              <div className="flex items-center gap-1.5 mt-1.5 flex-wrap">
                {entry.tokens_estimated && <span className="text-[9px] text-faint italic">approx.</span>}
                <span className="rounded-full bg-blue-50 dark:bg-blue-500/10 px-2 py-0.5 text-[10px] font-medium text-blue-700 dark:text-blue-300 ring-1 ring-blue-200 dark:ring-blue-500/30">
                  In {entry.tokens_estimated ? "~" : ""}{(entry.input_tokens ?? 0).toLocaleString()}
                </span>
                <span className="rounded-full bg-violet-50 dark:bg-violet-500/10 px-2 py-0.5 text-[10px] font-medium text-violet-700 dark:text-violet-300 ring-1 ring-violet-200 dark:ring-violet-500/30">
                  Out {entry.tokens_estimated ? "~" : ""}{(entry.output_tokens ?? 0).toLocaleString()}
                </span>
                <span className="rounded-full bg-surface-2 border border-line px-2 py-0.5 text-[10px] font-medium text-fg">
                  Total {entry.tokens_estimated ? "~" : ""}{(entry.total_tokens ?? 0).toLocaleString()}
                </span>
              </div>
            )}
          </div>
          <div className="flex items-center gap-2 shrink-0 mt-0.5">
            <span className="text-[10px] tabular-nums text-faint">{offset}</span>
            <span className={cn("h-2 w-2 rounded-full shrink-0", outcomeClass(entry))} />
          </div>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Status reasons — extract WHY a group is warning/error/success
// ---------------------------------------------------------------------------

interface StatusReason {
  kind: "warning" | "error" | "success";
  messages: string[];           // translated plain-English reasons
}

function statusReasons(group: RequestGroup): StatusReason {
  // Scan ALL entries for level (not just non-noise) so we never miss a warning
  const lvl = (e: { level: string }) => e.level?.toUpperCase();

  const errorEntries = group.entries.filter(e => lvl(e) === "ERROR");
  if (errorEntries.length > 0) {
    return {
      kind: "error",
      messages: [...new Set(errorEntries.map(translateMessage).filter(Boolean))],
    };
  }

  // Warnings are treated as success — only errors surface as actionable

  // Success — find the completion trail entry to name what stages ran
  const completionEntry = group.entries.find(e => /^Request complete trail=/i.test(e.msg));
  if (completionEntry) {
    const trail = completionEntry.msg.replace(/^Request complete trail=/i, "").trim();
    // trail is like "guardrail_pass rbac_pass compliance_pass domain_answer_price_assist"
    const stages = trail
      .split(/[\s,]+/)
      .filter(Boolean)
      .map(s => s
        .replace(/_pass$/i, "")
        .replace(/_/g, " ")
        .replace(/\b\w/g, c => c.toUpperCase()),
      )
      .join(" → ");
    return { kind: "success", messages: [`Pipeline completed: ${stages}`] };
  }

  return { kind: "success", messages: ["Request processed successfully"] };
}

// ---------------------------------------------------------------------------
// Journey summary sentence
// ---------------------------------------------------------------------------

function journeySummary(group: RequestGroup): string {
  const trailEntry = group.entries.find(e => e.msg.startsWith("Request complete trail="));
  const dur = formatDuration(group.duration_ms);
  if (trailEntry) {
    const trail = trailEntry.msg.replace("Request complete trail=", "");
    const blocked = /block|fail/i.test(trail);
    const user = group.user ?? "The user";
    if (blocked) return `${user}'s request was blocked during processing. Total time: ${dur}.`;
    const agentM = trail.match(/data_agent|rag_agent|price_assist/i);
    const agent = agentM ? agentM[0].replace(/_/g, " ") : "an AI agent";
    return `${group.user ?? "A user"}'s request passed all checks and was answered by the ${agent} in ${dur}.`;
  }
  const user = group.user ?? "A user";
  if (group.has_error) return `${user}'s request encountered an error. Total time: ${dur}.`;
  return `${user}'s request was processed in ${dur}.`;
}

// ---------------------------------------------------------------------------
// Status justification callout
// ---------------------------------------------------------------------------

function StatusJustification({ group }: { group: RequestGroup }) {
  const reasons = statusReasons(group);

  if (reasons.kind === "success") {
    return (
      <div className="pl-11 flex items-start gap-2">
        <span className="mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-emerald-100 dark:bg-emerald-500/20">
          <CheckCircle2 className="h-3 w-3 text-emerald-600 dark:text-emerald-400" />
        </span>
        <p className="text-xs text-emerald-700 dark:text-emerald-400 leading-relaxed">
          {reasons.messages[0]}
        </p>
      </div>
    );
  }

  const isError = reasons.kind === "error";
  const containerCls = isError
    ? "bg-red-50 dark:bg-red-500/10 border border-red-200 dark:border-red-500/30 rounded-lg"
    : "bg-amber-50 dark:bg-amber-500/10 border border-amber-200 dark:border-amber-500/30 rounded-lg";
  const labelCls = isError
    ? "text-red-700 dark:text-red-400"
    : "text-amber-700 dark:text-amber-400";
  const dotCls = isError ? "bg-red-500" : "bg-amber-500";

  return (
    <div className={cn("ml-11 px-3 py-2", containerCls)}>
      <p className={cn("text-[10px] font-semibold uppercase tracking-wide mb-1", labelCls)}>
        {isError ? "Error reason" : "Warning reason"}
      </p>
      <ul className="space-y-0.5">
        {reasons.messages.map((msg, i) => (
          <li key={i} className="flex items-start gap-2">
            <span className={cn("mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full", dotCls)} />
            <span className={cn("text-xs leading-relaxed", labelCls)}>{msg}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Request journey card
// ---------------------------------------------------------------------------

function JourneyCard({ group, preExpanded, msgSearch }: {
  group: RequestGroup; preExpanded: boolean; msgSearch: string;
}) {
  const [expanded, setExpanded] = useState(preExpanded);

  // Auto-expand when pre-selected from Activity page
  useEffect(() => { if (preExpanded) setExpanded(true); }, [preExpanded]);

  const journeyEntries = useMemo(() =>
    group.entries.filter(e => !isNoise(e.logger)),
    [group.entries],
  );

  const filteredEntries = useMemo(() => {
    if (!msgSearch) return journeyEntries;
    const q = msgSearch.toLowerCase();
    return journeyEntries.filter(e => translateMessage(e).toLowerCase().includes(q));
  }, [journeyEntries, msgSearch]);

  const statusIcon = group.has_error
    ? <XCircle className="h-5 w-5 text-red-500 shrink-0" />
    : <CheckCircle2 className="h-5 w-5 text-emerald-500 shrink-0" />;

  const borderClass = group.has_error
    ? "border-l-4 border-l-red-400 dark:border-l-red-600"
    : "border-l-4 border-l-emerald-400 dark:border-l-emerald-600";

  return (
    <div className={cn("rounded-xl border border-line bg-surface shadow-sm overflow-hidden", borderClass)}>
      {/* Header — always visible */}
      <div className="px-4 pt-4 pb-3 space-y-2">
        {/* Row 1: status, avatar, user, time */}
        <div className="flex items-center gap-3">
          {statusIcon}
          {group.user && (
            <div className={cn("flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-sm font-bold", avatarColor(group.user))}>
              {avatarInitial(group.user)}
            </div>
          )}
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-1.5 flex-wrap">
              <span className="text-sm font-semibold text-fg">{group.user ?? "Unknown"}</span>
              <Badge tone="slate">{filteredEntries.length} steps</Badge>
            </div>
            <p className="text-[10px] text-faint font-mono truncate mt-0.5" title={group.request_id}>
              {group.request_id}
            </p>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <span className="text-xs text-faint">{timeAgo(group.first_ts)}</span>
            <span className="text-xs font-medium text-muted bg-surface-2 border border-line rounded-full px-2 py-0.5">
              {formatDuration(group.duration_ms)}
            </span>
            {(group.token_total ?? 0) > 0 && (
              <span className="flex items-center gap-1 rounded-full bg-amber-50 dark:bg-amber-500/10 px-2 py-0.5 text-[10px] font-medium text-amber-700 dark:text-amber-300 ring-1 ring-amber-200 dark:ring-amber-500/30">
                <Coins className="h-3 w-3" />{group.token_estimated ? "~" : ""}{kFmt(group.token_total!)}
              </span>
            )}
          </div>
        </div>

        {/* Row 2: summary sentence */}
        <p className="text-sm text-muted italic leading-relaxed pl-11">
          {journeySummary(group)}
        </p>

        {/* Row 3: status justification callout */}
        <StatusJustification group={group} />

        {/* Expand toggle */}
        <div className="pl-11">
          <button
            onClick={() => setExpanded(v => !v)}
            className="flex items-center gap-1.5 text-xs font-medium text-brand-600 dark:text-brand-400 hover:underline"
          >
            {expanded
              ? <><ChevronUp className="h-3.5 w-3.5" />Hide steps</>
              : <><ChevronDown className="h-3.5 w-3.5" />View {filteredEntries.length} steps</>}
          </button>
        </div>
      </div>

      {/* Timeline body */}
      {expanded && (
        <div className="border-t border-line px-4 pt-4 pb-2">
          {filteredEntries.length === 0 ? (
            <p className="text-xs text-muted py-2">No steps match the current search.</p>
          ) : (
            <div>
              {filteredEntries.map((e, i) => (
                <JourneyStep
                  key={i}
                  entry={e}
                  firstTs={group.first_ts}
                  isLast={i === filteredEntries.length - 1}
                />
              ))}
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

export default function LogsDashboardPage() {
  const [searchParams] = useSearchParams();
  const preSelectedId = searchParams.get("request") ?? "";

  const { data, isLoading, isError, refetch, isFetching } = useQuery({
    queryKey: ["logs-list"],
    queryFn: getLogs,
    refetchInterval: 15_000,
  });

  const [requestSearch, setRequestSearch] = useState(preSelectedId);
  const [msgSearch, setMsgSearch]         = useState("");
  const [statusFilter, setStatusFilter]   = useState<"all" | "success" | "warning" | "error">("all");

  // If navigated from Activity with a specific request ID, pre-fill search
  useEffect(() => {
    if (preSelectedId) setRequestSearch(preSelectedId);
  }, [preSelectedId]);

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
      if (statusFilter === "success" && (g.has_error || g.has_warning)) return false;
      if (statusFilter === "warning" && !g.has_warning) return false;
      if (statusFilter === "error"   && !g.has_error)   return false;
      if (msgSearch) {
        const q = msgSearch.toLowerCase();
        const hasMatch = g.entries.some(e =>
          !isNoise(e.logger) && translateMessage(e).toLowerCase().includes(q),
        );
        if (!hasMatch) return false;
      }
      return true;
    });
  }, [data, requestSearch, msgSearch, statusFilter]);

  return (
    <div className="space-y-6 px-4 py-6 sm:px-6 lg:px-8 max-w-5xl mx-auto w-full">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-fg flex items-center gap-2.5">
            <GitBranch className="h-6 w-6 text-violet-500" />
            Request Journey
          </h1>
          <p className="text-sm text-muted mt-0.5">
            Step-by-step breakdown of what happened during each request
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
        <Metric label="Requests" value={isLoading ? "—" : String(data?.unique_requests ?? 0)} tone="default" />
        <Metric
          label="Avg Response Time"
          value={isLoading ? "—" : avgDuration > 0 ? `${(avgDuration / 1000).toFixed(1)}s` : "—"}
          tone="default"
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

      {/* Stage legend — compact inline */}
      {!isLoading && (data?.unique_requests ?? 0) > 0 && (
        <div className="flex flex-wrap gap-4 text-xs text-muted px-1">
          {[
            { Icon: Activity,       label: "System",          cls: "text-brand-500" },
            { Icon: ShieldCheck,    label: "Safety & Access", cls: "text-blue-500" },
            { Icon: Scale,          label: "Compliance",      cls: "text-violet-500" },
            { Icon: Bot,            label: "AI Agent",        cls: "text-emerald-500" },
            { Icon: ArrowRightLeft, label: "Agent Comms",     cls: "text-amber-500" },
            { Icon: Database,       label: "Data Retrieval",  cls: "text-rose-500" },
          ].map(({ Icon, label, cls }) => (
            <span key={label} className="flex items-center gap-1.5">
              <div className="flex h-5 w-5 items-center justify-center rounded-full bg-surface-2 border border-line">
                <Icon className={cn("h-3 w-3", cls)} />
              </div>
              {label}
            </span>
          ))}
        </div>
      )}

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="relative min-w-[180px] flex-1">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-faint pointer-events-none" />
          <input
            type="text"
            placeholder="User, session, or request ID…"
            value={requestSearch}
            onChange={e => setRequestSearch(e.target.value)}
            className="w-full rounded-lg border border-line bg-surface pl-9 pr-3 py-2 text-sm text-fg placeholder:text-faint focus:outline-none focus:ring-2 focus:ring-brand-500/50"
          />
        </div>
        <div className="relative min-w-[160px] flex-1">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-faint pointer-events-none" />
          <input
            type="text"
            placeholder="Search steps…"
            value={msgSearch}
            onChange={e => setMsgSearch(e.target.value)}
            className="w-full rounded-lg border border-line bg-surface pl-9 pr-3 py-2 text-sm text-fg placeholder:text-faint focus:outline-none focus:ring-2 focus:ring-brand-500/50"
          />
        </div>
        <select
          value={statusFilter}
          onChange={e => setStatusFilter(e.target.value as typeof statusFilter)}
          className="rounded-lg border border-line bg-surface px-3 py-2 text-sm text-fg focus:outline-none focus:ring-2 focus:ring-brand-500/50"
        >
          <option value="all">All statuses</option>
          <option value="success">✓ Success</option>
          <option value="warning">⚠ Warnings</option>
          <option value="error">✕ Errors</option>
        </select>
        {!isLoading && data && (
          <span className="text-xs text-muted ml-auto">
            {visibleGroups.length} of {data.unique_requests} requests
          </span>
        )}
      </div>

      {/* Cards */}
      {isLoading ? (
        <CenteredSpinner />
      ) : isError ? (
        <div className="flex items-start gap-3 rounded-xl border border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-950/30 p-5 text-sm text-red-700 dark:text-red-400">
          <AlertCircle className="h-5 w-5 shrink-0 mt-0.5" />
          <div>
            <p className="font-semibold">Failed to load journeys</p>
            <p className="text-xs opacity-80 mt-0.5">Make sure the API server is running.</p>
          </div>
        </div>
      ) : (data?.unique_requests ?? 0) === 0 ? (
        <div className="flex flex-col items-center justify-center gap-3 py-20 text-center">
          <GitBranch className="h-10 w-10 text-faint" />
          <p className="text-base font-medium text-fg">No journeys yet</p>
          <p className="text-sm text-muted">Request journeys will appear here after users submit queries.</p>
        </div>
      ) : visibleGroups.length === 0 ? (
        <div className="flex flex-col items-center justify-center gap-3 py-16 text-center">
          <GitBranch className="h-8 w-8 text-faint" />
          <p className="text-base font-medium text-fg">No requests match your filters</p>
          <p className="text-sm text-muted">Try clearing the search or changing the filter.</p>
        </div>
      ) : (
        <div className="space-y-4">
          {visibleGroups.map(group => (
            <JourneyCard
              key={group.request_id}
              group={group}
              preExpanded={!!preSelectedId && group.request_id === preSelectedId}
              msgSearch={msgSearch}
            />
          ))}
        </div>
      )}
    </div>
  );
}
