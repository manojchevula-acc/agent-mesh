import { Link, NavLink } from "react-router-dom";
import { MessageSquare, Activity, X, SquarePen, ThumbsUp, ShieldCheck, Zap, MessagesSquare, ScrollText } from "lucide-react";
import { ApiStatus } from "./ApiStatus";
import { Logo } from "@/components/ui/Logo";
import { cn } from "@/lib/utils";

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
  {
    to: "/app/feedback",
    label: "Feedback",
    icon: ThumbsUp,
    description: "User ratings & satisfaction",
  },
  {
    to: "/app/audit",
    label: "Audit Trail",
    icon: ShieldCheck,
    description: "Full agent invocation log",
  },
  {
    to: "/app/traces",
    label: "Trace Spans",
    icon: Zap,
    description: "A2A wire call latency",
  },
  {
    to: "/app/conversations",
    label: "Conversations",
    icon: MessagesSquare,
    description: "All chat session threads",
  },
  {
    to: "/app/logs",
    label: "Logs",
    icon: ScrollText,
    description: "Live agent_mesh.log viewer",
  },
];

export function Sidebar({ open, onClose, onNewChat }: { open: boolean; onClose: () => void; onNewChat?: () => void }) {
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
        <nav className="flex-1 space-y-1 overflow-y-auto px-3 py-4">
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
