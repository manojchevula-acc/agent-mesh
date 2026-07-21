import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import {
  Layers, RefreshCw, CheckCircle2,
  Clock, Coins, ArrowRight, AlertCircle, TrendingUp,
} from "lucide-react";
import { getLogs } from "@/api/mesh";
import { Metric } from "@/components/ui/Metric";
import { CenteredSpinner } from "@/components/ui/Spinner";
import { cn } from "@/lib/utils";
import type { RequestGroup } from "@/types/mesh";

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

function kFmt(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return n.toLocaleString();
}

function avatarInitial(u: string) { return u.charAt(0).toUpperCase(); }

const AVATAR_COLORS = [
  "bg-brand-500", "bg-emerald-500", "bg-amber-500", "bg-violet-500", "bg-rose-500",
];
function avatarColor(u: string) {
  let h = 0;
  for (let i = 0; i < u.length; i++) h = (h * 31 + u.charCodeAt(i)) & 0xffff;
  return AVATAR_COLORS[h % AVATAR_COLORS.length];
}

// ---------------------------------------------------------------------------
// Pipeline stage analysis
// ---------------------------------------------------------------------------

interface StageStat { pass: number; fail: number; skip: number; }
interface PipelineStats {
  guardrail: StageStat;
  rbac: StageStat;
  compliance: StageStat;
  domain: StageStat;
  answer: StageStat;
}

function analysePipeline(groups: RequestGroup[]): PipelineStats {
  const zero = (): StageStat => ({ pass: 0, fail: 0, skip: 0 });
  const s: PipelineStats = {
    guardrail: zero(), rbac: zero(), compliance: zero(),
    domain: zero(), answer: zero(),
  };

  for (const g of groups) {
    for (const e of g.entries) {
      const msg = e.msg;
      if (/^Input guardrail PASS/i.test(msg))    { s.guardrail.pass++; continue; }
      if (/^Input guardrail/i.test(msg))          { s.guardrail.fail++; continue; }
      if (/^RBAC PASS/i.test(msg))               { s.rbac.pass++; continue; }
      if (/^RBAC/i.test(msg))                    { s.rbac.fail++; continue; }
      if (/^Compliance BYPASS/i.test(msg))        { s.compliance.skip++; continue; }
      if (/^Compliance PASS/i.test(msg))          { s.compliance.pass++; continue; }
      if (/^Compliance FAIL/i.test(msg))          { s.compliance.fail++; continue; }
      if (/^Domain direct answer/i.test(msg))     { s.domain.pass++; continue; }
    }
    // Answer = request completed without error
    if (!g.has_error && g.entries.some(e => /^Request complete/i.test(e.msg))) {
      s.answer.pass++;
    } else if (g.has_error) {
      s.answer.fail++;
    }
  }
  return s;
}

// Build compact trail for the table row
function buildTrail(group: RequestGroup): { label: string; ok: boolean; skipped?: boolean }[] {
  const trail: { label: string; ok: boolean; skipped?: boolean }[] = [];
  const msgs = group.entries.map(e => e.msg);

  const hasGuardrailPass = msgs.some(m => /^Input guardrail PASS/i.test(m));
  const hasGuardrailFail = msgs.some(m => /^Input guardrail/i.test(m) && !/PASS/i.test(m));
  if (hasGuardrailPass) trail.push({ label: "Guard", ok: true });
  else if (hasGuardrailFail) { trail.push({ label: "Guard", ok: false }); return trail; }

  const hasRbacPass = msgs.some(m => /^RBAC PASS/i.test(m));
  const hasRbacFail = msgs.some(m => /^RBAC/i.test(m) && !/PASS/i.test(m));
  if (hasRbacPass) trail.push({ label: "RBAC", ok: true });
  else if (hasRbacFail) { trail.push({ label: "RBAC", ok: false }); return trail; }

  const hasCompliancePass   = msgs.some(m => /^Compliance PASS/i.test(m));
  const hasComplianceBypass = msgs.some(m => /^Compliance BYPASS/i.test(m));
  const hasComplianceFail   = msgs.some(m => /^Compliance FAIL/i.test(m));
  if (hasCompliancePass)        trail.push({ label: "Policy", ok: true });
  else if (hasComplianceBypass) trail.push({ label: "Policy", ok: true, skipped: true });
  else if (hasComplianceFail)   { trail.push({ label: "Policy", ok: false }); return trail; }

  const hasDomain = msgs.some(m => /^Domain direct answer/i.test(m));
  if (hasDomain) trail.push({ label: "Domain", ok: true });

  const completed = msgs.some(m => /^Request complete/i.test(m));
  if (completed) trail.push({ label: "Answer", ok: !group.has_error });

  return trail;
}

// ---------------------------------------------------------------------------
// Pipeline Funnel Section
// ---------------------------------------------------------------------------

const STAGE_META = [
  { key: "guardrail" as const,  label: "Safety Guard",  color: "blue"    },
  { key: "rbac"      as const,  label: "Access",         color: "violet"  },
  { key: "compliance" as const, label: "Policy",         color: "amber"   },
  { key: "domain"    as const,  label: "Domain",         color: "emerald" },
  { key: "answer"    as const,  label: "Answer",         color: "brand"   },
];

const FUNNEL_COLORS: Record<string, { bg: string; text: string; ring: string; bar: string }> = {
  blue:    { bg: "bg-blue-50 dark:bg-blue-500/10",    text: "text-blue-700 dark:text-blue-300",    ring: "ring-blue-200 dark:ring-blue-500/30",    bar: "bg-blue-500" },
  violet:  { bg: "bg-violet-50 dark:bg-violet-500/10",text: "text-violet-700 dark:text-violet-300",ring: "ring-violet-200 dark:ring-violet-500/30",bar: "bg-violet-500" },
  amber:   { bg: "bg-amber-50 dark:bg-amber-500/10",  text: "text-amber-700 dark:text-amber-300",  ring: "ring-amber-200 dark:ring-amber-500/30",  bar: "bg-amber-500" },
  emerald: { bg: "bg-emerald-50 dark:bg-emerald-500/10",text:"text-emerald-700 dark:text-emerald-300",ring:"ring-emerald-200 dark:ring-emerald-500/30",bar:"bg-emerald-500"},
  brand:   { bg: "bg-brand-50 dark:bg-brand-500/10",  text: "text-brand-700 dark:text-brand-300",  ring: "ring-brand-200 dark:ring-brand-500/30",  bar: "bg-brand-500" },
};

function PipelineFunnel({ stats, total }: { stats: PipelineStats; total: number }) {
  if (total === 0) return null;

  return (
    <div className="rounded-xl border border-line bg-surface shadow-sm p-5">
      <div className="flex items-center gap-2 mb-4">
        <TrendingUp className="h-4 w-4 text-brand-500" />
        <h2 className="text-sm font-semibold text-fg">Pipeline Stage Health</h2>
        <span className="ml-auto text-xs text-faint">{total} requests total</span>
      </div>

      <div className="flex items-stretch gap-1.5 flex-wrap sm:flex-nowrap">
        {STAGE_META.map(({ key, label, color }, idx) => {
          const stat = stats[key];
          const seen = stat.pass + stat.fail + stat.skip;
          if (seen === 0) return null;
          const passRate = seen > 0 ? Math.round((stat.pass + stat.skip) / seen * 100) : 100;
          const c = FUNNEL_COLORS[color];

          return (
            <div key={key} className="flex items-center gap-1.5 flex-1 min-w-0">
              <div className={cn("flex-1 rounded-lg ring-1 p-3 min-w-0", c.bg, c.ring)}>
                <p className={cn("text-[10px] font-semibold uppercase tracking-wide mb-1.5", c.text)}>{label}</p>

                {/* Pass bar */}
                <div className="w-full h-1.5 rounded-full bg-black/10 dark:bg-white/10 mb-2 overflow-hidden">
                  <div
                    className={cn("h-full rounded-full", c.bar)}
                    style={{ width: `${passRate}%` }}
                  />
                </div>

                <div className="flex items-center justify-between gap-1">
                  <span className={cn("text-xs font-bold", c.text)}>{passRate}%</span>
                  <div className="flex gap-1.5 flex-wrap justify-end">
                    {stat.pass > 0 && (
                      <span className="text-[10px] text-emerald-600 dark:text-emerald-400 font-medium">{stat.pass}✓</span>
                    )}
                    {stat.skip > 0 && (
                      <span className="text-[10px] text-faint font-medium">{stat.skip}–</span>
                    )}
                    {stat.fail > 0 && (
                      <span className="text-[10px] text-red-600 dark:text-red-400 font-medium">{stat.fail}✗</span>
                    )}
                  </div>
                </div>
              </div>
              {idx < STAGE_META.length - 1 && (
                <ArrowRight className="h-3.5 w-3.5 shrink-0 text-faint hidden sm:block" />
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Trail chip inline
// ---------------------------------------------------------------------------

function TrailChips({ trail }: { trail: { label: string; ok: boolean; skipped?: boolean }[] }) {
  if (trail.length === 0) return <span className="text-xs text-faint italic">—</span>;
  return (
    <div className="flex items-center gap-1 flex-wrap">
      {trail.map((step, i) => (
        <span key={i} className={cn(
          "inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold ring-1",
          step.ok && !step.skipped
            ? "bg-emerald-50 dark:bg-emerald-500/10 text-emerald-700 dark:text-emerald-300 ring-emerald-200 dark:ring-emerald-500/30"
            : step.skipped
            ? "bg-surface-2 text-faint ring-line"
            : "bg-red-50 dark:bg-red-500/10 text-red-700 dark:text-red-300 ring-red-200 dark:ring-red-500/30",
        )}>
          {step.label}{step.ok ? " ✓" : step.skipped ? " —" : " ✗"}
        </span>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Compact request row
// ---------------------------------------------------------------------------

function RequestRow({ group }: { group: RequestGroup }) {
  const navigate = useNavigate();
  const trail = useMemo(() => buildTrail(group), [group]);

  const statusDot = group.has_error
    ? "bg-red-500 ring-red-200 dark:ring-red-500/40"
    : group.has_warning
    ? "bg-amber-400 ring-amber-200 dark:ring-amber-500/40"
    : "bg-emerald-500 ring-emerald-200 dark:ring-emerald-500/40";

  const statusLabel = group.has_error ? "Error" : group.has_warning ? "Warning" : "Success";

  return (
    <div
      className="group flex items-center gap-3 px-4 py-3 hover:bg-surface-2 transition-colors cursor-pointer border-b border-line/50 last:border-0"
      onClick={() => navigate(`/app/logs?request=${group.request_id}`)}
      title={`View journey for ${group.request_id}`}
    >
      {/* Status dot */}
      <span className={cn("h-2.5 w-2.5 shrink-0 rounded-full ring-2", statusDot)} title={statusLabel} />

      {/* User avatar */}
      <div className={cn(
        "flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-xs font-bold text-white",
        avatarColor(group.user ?? "?"),
      )}>
        {avatarInitial(group.user ?? "?")}
      </div>

      {/* User name */}
      <span className="w-20 shrink-0 text-sm font-medium text-fg truncate">{group.user ?? "—"}</span>

      {/* Pipeline trail */}
      <div className="flex-1 min-w-0">
        <TrailChips trail={trail} />
      </div>

      {/* Duration */}
      <span className="shrink-0 flex items-center gap-1 text-xs text-muted">
        <Clock className="h-3 w-3" />{formatDuration(group.duration_ms)}
      </span>

      {/* Tokens */}
      {(group.token_total ?? 0) > 0 ? (
        <span className="shrink-0 flex items-center gap-1 text-xs text-amber-600 dark:text-amber-400">
          <Coins className="h-3 w-3" />{kFmt(group.token_total!)}
        </span>
      ) : (
        <span className="shrink-0 w-14" />
      )}

      {/* Time */}
      <span className="shrink-0 text-xs text-faint w-14 text-right">{timeAgo(group.first_ts)}</span>

      {/* Arrow hint */}
      <ArrowRight className="h-3.5 w-3.5 shrink-0 text-faint opacity-0 group-hover:opacity-100 transition-opacity" />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function RequestActivityPage() {
  const { data, isLoading, isError, refetch, isFetching } = useQuery({
    queryKey: ["logs-list"],
    queryFn: getLogs,
    refetchInterval: 15_000,
  });

  const [statusFilter, setStatusFilter] = useState<"all" | "success" | "issues">("all");
  const [userSearch, setUserSearch] = useState("");

  const avgDuration = useMemo(() => {
    if (!data || data.groups.length === 0) return 0;
    return Math.round(data.groups.reduce((s, g) => s + g.duration_ms, 0) / data.groups.length);
  }, [data]);

  const successCount = useMemo(() =>
    data?.groups.filter(g => !g.has_error && !g.has_warning).length ?? 0,
    [data],
  );

  const successRate = useMemo(() => {
    if (!data || data.groups.length === 0) return null;
    return Math.round((successCount / data.groups.length) * 100);
  }, [data, successCount]);

  const pipelineStats = useMemo(() =>
    data ? analysePipeline(data.groups) : null,
    [data],
  );

  const visibleGroups = useMemo(() => {
    if (!data) return [];
    return data.groups.filter(g => {
      if (userSearch) {
        const q = userSearch.toLowerCase();
        if (!(g.user ?? "").toLowerCase().includes(q) &&
            !g.request_id.toLowerCase().includes(q)) return false;
      }
      if (statusFilter === "success" && (g.has_error || g.has_warning)) return false;
      if (statusFilter === "issues" && !g.has_error && !g.has_warning) return false;
      return true;
    });
  }, [data, userSearch, statusFilter]);

  return (
    <div className="space-y-6 px-4 py-6 sm:px-6 lg:px-8 max-w-5xl mx-auto w-full">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-fg flex items-center gap-2.5">
            <Layers className="h-6 w-6 text-brand-500" />
            Activity
          </h1>
          <p className="text-sm text-muted mt-0.5">
            Real-time pipeline health — scan every request at a glance
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
          label="Total Requests"
          value={isLoading ? "—" : String(data?.unique_requests ?? 0)}
          tone="default"
        />
        <Metric
          label="Success Rate"
          value={isLoading || successRate === null ? "—" : `${successRate}%`}
          tone={successRate === null ? "default" : successRate >= 90 ? "good" : successRate >= 70 ? "warn" : "bad"}
        />
        <Metric
          label="Avg Response Time"
          value={isLoading ? "—" : avgDuration > 0 ? formatDuration(avgDuration) : "—"}
          tone="default"
          hint="per request"
        />
        <Metric
          label="Errors"
          value={isLoading ? "—" : String(data?.error_count ?? 0)}
          tone={isLoading ? "default" : (data?.error_count ?? 0) === 0 ? "good" : "bad"}
        />
      </div>

      {/* Pipeline funnel */}
      {!isLoading && pipelineStats && (
        <PipelineFunnel stats={pipelineStats} total={data?.unique_requests ?? 0} />
      )}

      {/* Requests table */}
      <div className="rounded-xl border border-line bg-surface shadow-sm overflow-hidden">
        {/* Table header + filters */}
        <div className="flex flex-wrap items-center gap-3 px-4 py-3 border-b border-line bg-surface-2">
          <h2 className="text-sm font-semibold text-fg">Recent Requests</h2>
          <div className="flex items-center gap-2 ml-auto flex-wrap">
            <input
              type="text"
              placeholder="Search user or ID…"
              value={userSearch}
              onChange={e => setUserSearch(e.target.value)}
              className="rounded-lg border border-line bg-surface px-3 py-1.5 text-xs text-fg placeholder:text-faint focus:outline-none focus:ring-2 focus:ring-brand-500/50 w-40"
            />
            <div className="flex rounded-lg border border-line overflow-hidden text-xs">
              {(["all", "success", "issues"] as const).map(f => (
                <button
                  key={f}
                  onClick={() => setStatusFilter(f)}
                  className={cn(
                    "px-3 py-1.5 font-medium transition-colors",
                    statusFilter === f
                      ? "bg-brand-600 text-white"
                      : "text-muted hover:bg-surface-2",
                  )}
                >
                  {f === "all" ? "All" : f === "success" ? "✓ Success" : "⚠ Issues"}
                </button>
              ))}
            </div>
          </div>
        </div>

        {/* Column header */}
        {!isLoading && (data?.unique_requests ?? 0) > 0 && (
          <div className="hidden sm:flex items-center gap-3 px-4 py-2 text-[10px] font-semibold uppercase tracking-wide text-faint border-b border-line/50">
            <span className="w-2.5 shrink-0" />
            <span className="w-7 shrink-0" />
            <span className="w-20 shrink-0">User</span>
            <span className="flex-1">Pipeline</span>
            <span className="w-14 shrink-0 text-right">Time</span>
            <span className="w-14 shrink-0 text-right">Tokens</span>
            <span className="w-14 shrink-0 text-right">When</span>
            <span className="w-3.5 shrink-0" />
          </div>
        )}

        {/* Rows */}
        {isLoading ? (
          <div className="py-12"><CenteredSpinner /></div>
        ) : isError ? (
          <div className="flex items-start gap-3 p-5 text-sm text-red-700 dark:text-red-400">
            <AlertCircle className="h-5 w-5 shrink-0 mt-0.5" />
            <div>
              <p className="font-semibold">Failed to load activity</p>
              <p className="text-xs opacity-80 mt-0.5">Make sure the API server is running.</p>
            </div>
          </div>
        ) : visibleGroups.length === 0 && (data?.unique_requests ?? 0) === 0 ? (
          <div className="flex flex-col items-center justify-center gap-3 py-16 text-center">
            <Layers className="h-8 w-8 text-faint" />
            <p className="text-base font-medium text-fg">No activity yet</p>
            <p className="text-sm text-muted">Requests will appear here after users submit queries.</p>
          </div>
        ) : visibleGroups.length === 0 ? (
          <div className="flex flex-col items-center gap-2 py-12 text-center">
            <CheckCircle2 className="h-7 w-7 text-faint" />
            <p className="text-sm font-medium text-fg">No requests match your filters</p>
            <p className="text-xs text-muted">Try "All" or clear the search.</p>
          </div>
        ) : (
          <div>
            {visibleGroups.map(g => <RequestRow key={g.request_id} group={g} />)}
          </div>
        )}

        {/* Footer count */}
        {!isLoading && (data?.unique_requests ?? 0) > 0 && (
          <div className="px-4 py-2.5 border-t border-line/50 text-xs text-faint">
            Showing {visibleGroups.length} of {data!.unique_requests} requests
            {" · "}
            <span className="text-emerald-600 dark:text-emerald-400">{successCount} succeeded</span>
            {(data!.error_count ?? 0) > 0 && (
              <>, <span className="text-red-600 dark:text-red-400">{data!.error_count} errored</span></>
            )}
            {" · "}
            <button
              onClick={() => refetch()}
              className="text-brand-600 dark:text-brand-400 hover:underline"
            >
              refresh
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
