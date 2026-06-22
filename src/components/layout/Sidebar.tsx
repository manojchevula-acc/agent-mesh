import { Link, NavLink } from "react-router-dom";
import { BarChart3, Search, Settings, Upload, X } from "lucide-react";
import { ApiStatus } from "./ApiStatus";
import { Logo } from "@/components/ui/Logo";
import { cn } from "@/lib/utils";

const NAV_ITEMS = [
  { to: "/app/search", label: "Search", icon: Search, description: "Query policies" },
  { to: "/app/upload", label: "Upload", icon: Upload, description: "Ingest documents" },
  { to: "/app/evaluation", label: "Evaluation", icon: BarChart3, description: "RAGAS quality" },
  { to: "/app/admin", label: "Admin", icon: Settings, description: "Collection ops" },
];

export function Sidebar({ open, onClose }: { open: boolean; onClose: () => void }) {
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
            <Logo subtitle="FAB Policy Assistant" />
          </Link>
          <button
            className="rounded-md p-1.5 text-muted hover:bg-surface-2 lg:hidden"
            onClick={onClose}
            aria-label="Close menu"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

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
            Hybrid dense + sparse retrieval over FAB credit policies, CBUAE circulars and risk
            frameworks.
          </p>
        </div>
      </aside>
    </>
  );
}
