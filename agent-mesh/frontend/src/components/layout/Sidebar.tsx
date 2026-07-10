import { Link, NavLink } from "react-router-dom";
import { MessageSquare, Activity, X, SquarePen } from "lucide-react";
import { ApiStatus } from "./ApiStatus";
import { Logo } from "@/components/ui/Logo";
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
}

export function Sidebar({
  open,
  onClose,
  onNewChat,
  sessions = [],
  activeSessionId = null,
  onSelectSession,
}: SidebarProps) {
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
                return (
                  <button
                    key={s.session_id}
                    onClick={() => onSelectSession?.(s.session_id)}
                    title={s.preview}
                    className={cn(
                      "w-full flex flex-col items-start gap-0.5 rounded-lg px-3 py-2 text-left text-sm transition-colors",
                      isActive
                        ? "bg-brand-50 text-brand-800 dark:bg-brand-500/10 dark:text-brand-200"
                        : "text-muted hover:bg-surface-2 hover:text-fg",
                    )}
                  >
                    <span className="w-full truncate">{s.preview}</span>
                    <span className="text-[11px] text-faint">{relativeTime(s.updated_at)}</span>
                  </button>
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
    </>
  );
}
