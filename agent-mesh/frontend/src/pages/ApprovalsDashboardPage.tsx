import { useState, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  ClipboardCheck, RefreshCw, ShieldAlert, Wrench,
  CheckCircle2, XCircle, Loader2, AlertCircle, InboxIcon,
} from "lucide-react";
import { getApprovalsList, approveRequest, rejectRequest } from "@/api/mesh";
import { useAuth } from "@/contexts/AuthContext";
import { CenteredSpinner } from "@/components/ui/Spinner";
import { cn } from "@/lib/utils";
import type { ApprovalListItem } from "@/types/mesh";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function roleLabel(r: string) {
  return r.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
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
// Approval row
// ---------------------------------------------------------------------------

interface ApprovalRowProps {
  item: ApprovalListItem;
  acting: boolean;
  onApprove: () => void;
  onReject: () => void;
}

function ApprovalRow({ item, acting, onApprove, onReject }: ApprovalRowProps) {
  const isToolApproval = item.hitl_type === "tool_approval";

  return (
    <tr className="border-b border-line last:border-0 hover:bg-surface-2/50 transition-colors">
      {/* Type badge */}
      <td className="px-4 py-3 w-24">
        {isToolApproval ? (
          <span className="inline-flex items-center gap-1 rounded-full bg-blue-50 dark:bg-blue-500/15 px-2.5 py-0.5 text-[10px] font-semibold text-blue-700 dark:text-blue-300 ring-1 ring-blue-200 dark:ring-blue-500/30">
            <Wrench className="h-2.5 w-2.5" />Tool
          </span>
        ) : (
          <span className="inline-flex items-center gap-1 rounded-full bg-amber-50 dark:bg-amber-500/15 px-2.5 py-0.5 text-[10px] font-semibold text-amber-700 dark:text-amber-300 ring-1 ring-amber-200 dark:ring-amber-500/30">
            <ShieldAlert className="h-2.5 w-2.5" />Role
          </span>
        )}
      </td>

      {/* Requested by */}
      <td className="px-4 py-3 w-44">
        <div className="flex items-center gap-2">
          <span className={cn(
            "flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-xs font-bold",
            avatarColor(item.user_name),
          )}>
            {avatarInitial(item.user_name)}
          </span>
          <div className="min-w-0">
            <p className="truncate text-sm font-medium text-fg">{item.user_name}</p>
            <p className="truncate text-[10px] text-muted">{roleLabel(item.role)}</p>
          </div>
        </div>
      </td>

      {/* Details */}
      <td className="px-4 py-3">
        {isToolApproval ? (
          <div>
            <code className="text-xs font-mono bg-surface rounded px-2 py-0.5 border border-line text-fg">
              {item.tool_name}
            </code>
            {item.tool_args && Object.keys(item.tool_args).length > 0 && (
              <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-xs">
                {Object.entries(item.tool_args).map(([k, v]) => (
                  <>
                    <dt key={`k-${k}`} className="font-medium text-muted">{k}</dt>
                    <dd key={`v-${k}`} className="font-mono text-fg truncate">{String(v)}</dd>
                  </>
                ))}
              </dl>
            )}
          </div>
        ) : (
          <p className="text-sm text-fg line-clamp-2">
            {item.query ? `"${item.query.slice(0, 160)}${item.query.length > 160 ? "…" : ""}"` : "—"}
          </p>
        )}
      </td>

      {/* Actions */}
      <td className="px-4 py-3 w-36">
        <div className="flex items-center gap-2">
          <button
            onClick={onApprove}
            disabled={acting}
            className={cn(
              "inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-semibold transition-colors",
              acting
                ? "cursor-not-allowed bg-surface-2 text-muted"
                : "bg-emerald-600 hover:bg-emerald-700 dark:bg-emerald-500 dark:hover:bg-emerald-400 text-white",
            )}
          >
            {acting ? (
              <Loader2 className="h-3 w-3 animate-spin" />
            ) : (
              <CheckCircle2 className="h-3 w-3" />
            )}
            Approve
          </button>
          <button
            onClick={onReject}
            disabled={acting}
            className={cn(
              "inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-semibold transition-colors",
              acting
                ? "cursor-not-allowed bg-surface-2 text-muted"
                : "bg-red-600 hover:bg-red-700 dark:bg-red-500 dark:hover:bg-red-400 text-white",
            )}
          >
            <XCircle className="h-3 w-3" />
            Reject
          </button>
        </div>
      </td>
    </tr>
  );
}

// ---------------------------------------------------------------------------
// Role group section
// ---------------------------------------------------------------------------

function RoleSection({
  role,
  items,
  acting,
  onAction,
}: {
  role: string;
  items: ApprovalListItem[];
  acting: Record<string, boolean>;
  onAction: (aid: string, action: "approve" | "reject") => void;
}) {
  return (
    <section className="mb-6">
      <div className="flex items-center gap-2 mb-2 px-1">
        <span className="inline-flex items-center rounded-full bg-slate-100 dark:bg-slate-700 px-3 py-1 text-xs font-semibold text-slate-700 dark:text-slate-200 ring-1 ring-slate-200 dark:ring-slate-600">
          {roleLabel(role)}
        </span>
        <span className="text-xs text-muted">{items.length} pending</span>
      </div>
      <div className="rounded-xl border border-line bg-surface overflow-hidden">
        <table className="w-full text-left">
          <thead>
            <tr className="border-b border-line bg-surface-2/50">
              <th className="px-4 py-2 text-[11px] font-semibold uppercase tracking-wide text-muted w-24">Type</th>
              <th className="px-4 py-2 text-[11px] font-semibold uppercase tracking-wide text-muted w-44">Requested by</th>
              <th className="px-4 py-2 text-[11px] font-semibold uppercase tracking-wide text-muted">Details</th>
              <th className="px-4 py-2 text-[11px] font-semibold uppercase tracking-wide text-muted w-36">Actions</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <ApprovalRow
                key={item.approval_id}
                item={item}
                acting={!!acting[item.approval_id]}
                onApprove={() => onAction(item.approval_id, "approve")}
                onReject={() => onAction(item.approval_id, "reject")}
              />
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function ApprovalsDashboardPage() {
  const { user } = useAuth();
  const [activeRoleTab, setActiveRoleTab] = useState<string>("all");
  const [acting, setActing] = useState<Record<string, boolean>>({});

  const { data: rawApprovals = [], isLoading, isError, refetch, isFetching } = useQuery({
    queryKey: ["approvals-list", user?.username],
    queryFn: getApprovalsList,
    refetchInterval: 10_000,
  });

  const approvals = useMemo(
    () => rawApprovals.filter(a => a.user_name === user?.username),
    [rawApprovals, user?.username],
  );

  const rolesWithPending = useMemo(
    () => [...new Set(approvals.map((a) => a.role))].sort(),
    [approvals],
  );

  const visible = useMemo(
    () => activeRoleTab === "all" ? approvals : approvals.filter((a) => a.role === activeRoleTab),
    [approvals, activeRoleTab],
  );

  const byRole = useMemo(
    () =>
      visible.reduce<Record<string, ApprovalListItem[]>>((acc, a) => {
        (acc[a.role] ??= []).push(a);
        return acc;
      }, {}),
    [visible],
  );

  async function handleAction(aid: string, action: "approve" | "reject") {
    setActing((p) => ({ ...p, [aid]: true }));
    try {
      if (action === "approve") await approveRequest(aid);
      else await rejectRequest(aid);
      await refetch();
    } finally {
      setActing((p) => ({ ...p, [aid]: false }));
    }
  }

  // Reset tab if it no longer has pending items
  const safeTab = activeRoleTab === "all" || rolesWithPending.includes(activeRoleTab)
    ? activeRoleTab
    : "all";

  return (
    <div className="flex flex-col gap-6 p-6 lg:p-8 overflow-y-auto">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-amber-100 dark:bg-amber-500/15">
            <ClipboardCheck className="h-5 w-5 text-amber-600 dark:text-amber-400" />
          </div>
          <div>
            <h2 className="text-base font-semibold text-fg">Pending Approvals</h2>
            <p className="text-sm text-muted">
              {approvals.length === 0
                ? "No pending approvals"
                : `${approvals.length} request${approvals.length !== 1 ? "s" : ""} awaiting decision`}
            </p>
          </div>
        </div>
        <button
          onClick={() => refetch()}
          disabled={isFetching}
          className="inline-flex items-center gap-2 rounded-lg border border-line bg-surface px-3 py-2 text-sm font-medium text-muted hover:bg-surface-2 hover:text-fg transition-colors disabled:opacity-50"
        >
          <RefreshCw className={cn("h-4 w-4", isFetching && "animate-spin")} />
          Refresh
        </button>
      </div>

      {/* Role filter tabs */}
      {rolesWithPending.length > 0 && (
        <div className="flex items-center gap-1 border-b border-line pb-0 overflow-x-auto">
          {["all", ...rolesWithPending].map((tab) => (
            <button
              key={tab}
              onClick={() => setActiveRoleTab(tab)}
              className={cn(
                "whitespace-nowrap border-b-2 px-4 py-2 text-sm font-medium transition-colors -mb-px",
                safeTab === tab
                  ? "border-brand-600 text-brand-700 dark:text-brand-300"
                  : "border-transparent text-muted hover:text-fg hover:border-line",
              )}
            >
              {tab === "all" ? "All" : roleLabel(tab)}
              {tab !== "all" && (
                <span className="ml-1.5 rounded-full bg-amber-100 dark:bg-amber-500/20 px-1.5 py-0.5 text-[10px] font-bold text-amber-700 dark:text-amber-300">
                  {approvals.filter((a) => a.role === tab).length}
                </span>
              )}
            </button>
          ))}
        </div>
      )}

      {/* Body */}
      {isLoading ? (
        <CenteredSpinner />
      ) : isError ? (
        <div className="flex flex-col items-center gap-3 py-16 text-center">
          <AlertCircle className="h-10 w-10 text-red-400" />
          <p className="text-sm font-medium text-fg">Failed to load approvals</p>
          <button
            onClick={() => refetch()}
            className="text-sm text-brand-600 hover:underline"
          >
            Try again
          </button>
        </div>
      ) : Object.keys(byRole).length === 0 ? (
        <div className="flex flex-col items-center gap-4 py-20 text-center">
          <div className="flex h-16 w-16 items-center justify-center rounded-full bg-surface-2">
            <InboxIcon className="h-8 w-8 text-faint" />
          </div>
          <div>
            <p className="text-base font-medium text-fg">No pending approvals</p>
            <p className="mt-1 text-sm text-muted">
              {safeTab === "all"
                ? "All caught up — no requests are waiting for a decision."
                : `No pending approvals for ${roleLabel(safeTab)}.`}
            </p>
          </div>
        </div>
      ) : (
        Object.entries(byRole).map(([role, items]) => (
          <RoleSection
            key={role}
            role={role}
            items={items}
            acting={acting}
            onAction={handleAction}
          />
        ))
      )}
    </div>
  );
}
