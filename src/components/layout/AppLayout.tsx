import { useState } from "react";
import { Outlet, useLocation } from "react-router-dom";
import { Menu } from "lucide-react";
import { Sidebar } from "./Sidebar";
import { ApiStatus } from "./ApiStatus";
import { UserMenu } from "./UserMenu";
import { ThemeToggle } from "@/components/ui/ThemeToggle";

const PAGE_TITLES: Record<string, { title: string; subtitle: string }> = {
  "/app/search": {
    title: "Policy & Regulatory Search",
    subtitle: "Ask questions about FAB credit policies, CBUAE circulars and risk frameworks.",
  },
  "/app/upload": {
    title: "Document Upload",
    subtitle: "Ingest a PDF or DOCX through the full extraction → chunking → embedding pipeline.",
  },
  "/app/evaluation": {
    title: "RAGAS Evaluation",
    subtitle: "Score the RAG pipeline against the FAB test set with RAGAS metrics.",
  },
  "/app/admin": {
    title: "Administration",
    subtitle: "Manage the vector collection and inspect service readiness.",
  },
};

export function AppLayout() {
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const { pathname } = useLocation();
  const meta = PAGE_TITLES[pathname] ?? PAGE_TITLES["/app/search"];

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
        <main className="flex-1 px-4 py-6 sm:px-6 lg:px-8">
          <div className="mx-auto max-w-6xl animate-fade-in">
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  );
}
