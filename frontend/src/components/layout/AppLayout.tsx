import { useState } from "react";
import { Outlet, useLocation } from "react-router-dom";
import { Menu } from "lucide-react";
import { Sidebar } from "./Sidebar";
import { ApiStatus } from "./ApiStatus";
import { UserMenu } from "./UserMenu";
import { ThemeToggle } from "@/components/ui/ThemeToggle";

const PAGE_TITLES: Record<string, { title: string; subtitle: string }> = {
  "/app/chat": {
    title: "Agent Mesh Chat",
    subtitle: "Role-aware enterprise assistant — queries flow through a 7-stage security pipeline.",
  },
  "/app/mesh-status": {
    title: "Mesh Status",
    subtitle: "Real-time health of all 6 A2A agent nodes.",
  },
};

export function AppLayout() {
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const { pathname } = useLocation();
  const meta = PAGE_TITLES[pathname] ?? PAGE_TITLES["/app/chat"];

  return (
    <div className="flex min-h-screen bg-canvas">
      <Sidebar open={sidebarOpen} onClose={() => setSidebarOpen(false)} />

      <div className="flex min-w-0 flex-1 flex-col">
        {/* Top bar */}
        <header className="sticky top-0 z-20 border-b border-line bg-surface/80 backdrop-blur">
          <div className="flex items-center gap-3 px-4 py-3 sm:px-6 lg:px-8">
            <button
              className="rounded-md p-2 text-muted hover:bg-surface-2 lg:hidden"
              onClick={() => setSidebarOpen(true)}
              aria-label="Open menu"
            >
              <Menu className="h-5 w-5" />
            </button>
            <div className="min-w-0 flex-1">
              <h1 className="truncate text-lg font-semibold text-fg">{meta.title}</h1>
              <p className="hidden truncate text-sm text-muted sm:block">{meta.subtitle}</p>
            </div>
            <div className="hidden sm:block">
              <ApiStatus />
            </div>
            <ThemeToggle />
            <div className="h-6 w-px bg-line" />
            <UserMenu />
          </div>
        </header>

        {/* Page content */}
        <main className="flex-1 flex flex-col min-h-0 overflow-hidden">
          <div className="flex-1 flex flex-col min-h-0 animate-fade-in">
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  );
}
