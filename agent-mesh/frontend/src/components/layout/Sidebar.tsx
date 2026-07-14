import { useState } from "react";
import { Link, NavLink } from "react-router-dom";
import { MessageSquare, Activity, X, SquarePen, Pencil, Trash2, Check } from "lucide-react";
import { ApiStatus } from "./ApiStatus";
import { Logo } from "@/components/ui/Logo";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { cn } from "@/lib/utils";
import type { ConversationSummary } from "@/types/mesh";

function relativeTime(iso: string): string {
  const diffMs = Date.now() - new Date(iso).getTime();
  const mins = Math.round(diffMs / 60_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  return `${days}d ago`;
}

const NAV_ITEMS = [
  {
    to: "/app/chat",
    label: "Chat",
    icon: MessageSquare,
    description: "Ask the mesh a question",
  },
  {
    to: "/app/mesh-status",
    label: "Mesh Status",
    icon: Activity,
    description: "Health of all 6 A2A nodes",
  },
];

interface SidebarProps {
  open: boolean;
  onClose: () => void;
  onNewChat?: () => void;
  sessions?: ConversationSummary[];
  activeSessionId?: string | null;
  onSelectSession?: (sessionId: string) => void;
  onRenameSession?: (sessionId: string, title: string) => void | Promise<void>;
  onDeleteSession?: (sessionId: string) => void | Promise<void>;
}

export function Sidebar({
  open,
  onClose,
  onNewChat,
  sessions = [],
  activeSessionId = null,
  onSelectSession,
  onRenameSession,
  onDeleteSession,
}: SidebarProps) {
  // Which session row is currently in inline-rename mode, and the draft text.
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editValue, setEditValue] = useState("");
  // Session pending a delete confirmation (null when the dialog is closed).
  const [pendingDelete, setPendingDelete] = useState<ConversationSummary | null>(null);
  const [deleting, setDeleting] = useState(false);

  function beginEdit(s: ConversationSummary) {
    setEditingId(s.session_id);
    setEditValue(s.title || s.preview || "");
  }

  function cancelEdit() {
    setEditingId(null);
    setEditValue("");
  }

  function commitEdit(s: ConversationSummary) {
    const next = editValue.trim();
    // Only persist when the title actually changed, to avoid a needless write.
    if (next !== (s.title || "")) {
      onRenameSession?.(s.session_id, next);
    }
    cancelEdit();
  }

  async function confirmDelete() {
    if (!pendingDelete) return;
    setDeleting(true);
    try {
      await onDeleteSession?.(pendingDelete.session_id);
    } finally {
      setDeleting(false);
      setPendingDelete(null);
    }
  }

  return (
    <>
      {/* Mobile backdrop */}
      {open && (
        <div
          className="fixed inset-0 z-30 bg-slate-900/50 backdrop-blur-sm lg:hidden"
          onClick={onClose}
          aria-hidden
        />
      )}

      <aside
        className={cn(
          "fixed inset-y-0 left-0 z-40 flex w-72 flex-col border-r border-line bg-surface transition-transform lg:static lg:translate-x-0",
          open ? "translate-x-0" : "-translate-x-full",
        )}
      >
        {/* Brand */}
        <div className="flex items-center justify-between gap-2 border-b border-line px-5 py-5">
          <Link to="/" className="transition-transform hover:scale-[1.02]">
            <Logo subtitle="Price Intelligence" />
          </Link>
          <button
            className="rounded-md p-1.5 text-muted hover:bg-surface-2 lg:hidden"
            onClick={onClose}
            aria-label="Close menu"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* New Chat */}
        {onNewChat && (
          <div className="px-3 pt-3 pb-1">
            <button
              onClick={() => { onNewChat(); onClose(); }}
              className="w-full flex items-center gap-2 rounded-lg px-3 py-2 text-sm font-medium bg-brand-50 dark:bg-brand-900/30 text-brand-700 dark:text-brand-300 hover:bg-brand-100 dark:hover:bg-brand-900/50 transition-colors"
            >
              <SquarePen className="h-4 w-4" />
              New Chat
            </button>
          </div>
        )}

        {/* Nav */}
        <nav className="shrink-0 space-y-1 px-3 pt-3 pb-1">
          {NAV_ITEMS.map(({ to, label, icon: Icon, description }) => (
            <NavLink
              key={to}
              to={to}
              onClick={onClose}
              className={({ isActive }) =>
                cn(
                  "group relative flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm transition-all",
                  isActive
                    ? "bg-brand-50 text-brand-800 dark:bg-brand-500/10 dark:text-brand-200"
                    : "text-muted hover:bg-surface-2 hover:text-fg",
                )
              }
            >
              {({ isActive }) => (
                <>
                  <span
                    className={cn(
                      "absolute left-0 top-1/2 h-6 w-1 -translate-y-1/2 rounded-r-full bg-gradient-to-b from-brand-600 to-accent-500 transition-all",
                      isActive ? "opacity-100" : "opacity-0",
                    )}
                  />
                  <Icon
                    className={cn(
                      "h-5 w-5 shrink-0 transition-transform group-hover:scale-110",
                      isActive ? "text-brand-700 dark:text-brand-300" : "text-faint",
                    )}
                  />
                  <div className="min-w-0">
                    <p className="font-medium">{label}</p>
                    <p className="truncate text-xs text-faint">{description}</p>
                  </div>
                </>
              )}
            </NavLink>
          ))}
        </nav>

        {/* Recent chats */}
        {sessions.length > 0 && (
          <div className="flex-1 min-h-0 flex flex-col border-t border-line pt-2">
            <p className="px-4 pb-1 text-[11px] font-semibold uppercase tracking-wider text-faint shrink-0">
              Recent Chats
            </p>
            <div className="flex-1 min-h-0 overflow-y-auto px-3 pb-2 space-y-0.5">
              {sessions.map((s) => {
                const isActive = s.session_id === activeSessionId;
                const label = s.title || s.preview;
                const isEditing = editingId === s.session_id;

                if (isEditing) {
                  return (
                    <div
                      key={s.session_id}
                      className="flex items-center gap-1 rounded-lg bg-surface-2 px-2 py-1.5"
                    >
                      <input
                        autoFocus
                        value={editValue}
                        onChange={(e) => setEditValue(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") { e.preventDefault(); commitEdit(s); }
                          else if (e.key === "Escape") { e.preventDefault(); cancelEdit(); }
                        }}
                        onBlur={() => commitEdit(s)}
                        className="min-w-0 flex-1 rounded border border-line bg-canvas px-2 py-1 text-sm text-fg focus:outline-none focus:ring-1 focus:ring-brand-500"
                      />
                      <button
                        type="button"
                        // onMouseDown so it fires before the input's onBlur cancels focus.
                        onMouseDown={(e) => { e.preventDefault(); commitEdit(s); }}
                        className="rounded p-1 text-muted hover:bg-surface hover:text-fg"
                        aria-label="Save name"
                      >
                        <Check className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  );
                }

                return (
                  <div
                    key={s.session_id}
                    className={cn(
                      "group relative flex items-center rounded-lg text-sm transition-colors",
                      isActive
                        ? "bg-brand-50 text-brand-800 dark:bg-brand-500/10 dark:text-brand-200"
                        : "text-muted hover:bg-surface-2 hover:text-fg",
                    )}
                  >
                    <button
                      onClick={() => onSelectSession?.(s.session_id)}
                      title={label}
                      className="flex min-w-0 flex-1 flex-col items-start gap-0.5 px-3 py-2 text-left"
                    >
                      <span className="w-full truncate pr-12">{label}</span>
                      <span className="text-[11px] text-faint">{relativeTime(s.updated_at)}</span>
                    </button>
                    {(onRenameSession || onDeleteSession) && (
                      <div className="absolute right-1.5 top-1/2 flex -translate-y-1/2 items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
                        {onRenameSession && (
                          <button
                            type="button"
                            onClick={(e) => { e.stopPropagation(); beginEdit(s); }}
                            className="rounded p-1 text-faint hover:bg-surface hover:text-fg"
                            aria-label="Rename chat"
                            title="Rename"
                          >
                            <Pencil className="h-3.5 w-3.5" />
                          </button>
                        )}
                        {onDeleteSession && (
                          <button
                            type="button"
                            onClick={(e) => { e.stopPropagation(); setPendingDelete(s); }}
                            className="rounded p-1 text-faint hover:bg-red-100 hover:text-red-600 dark:hover:bg-red-500/15 dark:hover:text-red-400"
                            aria-label="Delete chat"
                            title="Delete"
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </button>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* Footer */}
        <div className="border-t border-line px-4 py-4">
          <ApiStatus />
          <p className="mt-3 px-1 text-[11px] leading-relaxed text-faint">
            FAB Price Assist — AI-powered pricing intelligence for corporate banking.
          </p>
        </div>
      </aside>

      <ConfirmDialog
        open={pendingDelete !== null}
        title="Delete chat?"
        description={
          <>
            This permanently deletes{" "}
            <span className="font-medium text-fg">
              “{pendingDelete?.title || pendingDelete?.preview}”
            </span>{" "}
            and its history. This can’t be undone.
          </>
        }
        confirmLabel="Delete"
        loading={deleting}
        onConfirm={confirmDelete}
        onCancel={() => setPendingDelete(null)}
      />
    </>
  );
}
