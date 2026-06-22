import { useState, type FormEvent } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { User, ShieldCheck, Users, Briefcase } from "lucide-react";
import { AuthLayout } from "@/components/layout/AuthLayout";
import { Button } from "@/components/ui/Button";
import { Alert } from "@/components/ui/Alert";
import { useAuth } from "@/contexts/AuthContext";
import { cn } from "@/lib/utils";

const DEMO_USERS = [
  {
    username: "alice",
    display_name: "Alice (CFO)",
    role: "leadership",
    description: "Full access — finance, HR, policy, compliance",
    icon: ShieldCheck,
    color: "text-amber-600 dark:text-amber-400",
    bg: "bg-amber-50 dark:bg-amber-900/20 border-amber-200 dark:border-amber-800",
  },
  {
    username: "carol",
    display_name: "Carol (HR Partner)",
    role: "hr",
    description: "HR domain + policy access",
    icon: Users,
    color: "text-teal-600 dark:text-teal-400",
    bg: "bg-teal-50 dark:bg-teal-900/20 border-teal-200 dark:border-teal-800",
  },
  {
    username: "bob",
    display_name: "Bob (Engineer)",
    role: "employee",
    description: "HR, job postings, policy — no finance",
    icon: Briefcase,
    color: "text-slate-600 dark:text-slate-400",
    bg: "bg-slate-50 dark:bg-slate-900/20 border-slate-200 dark:border-slate-700",
  },
  {
    username: "dave",
    display_name: "Dave (Analyst)",
    role: "employee",
    description: "HR, job postings, policy — no finance",
    icon: User,
    color: "text-slate-600 dark:text-slate-400",
    bg: "bg-slate-50 dark:bg-slate-900/20 border-slate-200 dark:border-slate-700",
  },
] as const;

export function LoginPage() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const from = (location.state as { from?: string } | null)?.from ?? "/app/chat";

  const [customUsername, setCustomUsername] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState<string | null>(null); // holds the username being submitted

  async function handleLogin(username: string) {
    setError(null);
    setSubmitting(username);
    try {
      await login(username);
      navigate(from, { replace: true });
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Sign in failed. Is the API server running? (python api_server.py)",
      );
    } finally {
      setSubmitting(null);
    }
  }

  async function handleCustomSubmit(e: FormEvent) {
    e.preventDefault();
    const u = customUsername.trim();
    if (u) await handleLogin(u);
  }

  return (
    <AuthLayout
      title="Sign in to Agent Mesh"
      subtitle="Select a demo user or enter any username"
    >
      <div className="space-y-5">
        {error && (
          <Alert variant="error" title="Sign in failed">
            {error}
          </Alert>
        )}

        {/* Demo user cards */}
        <div className="space-y-2">
          <p className="text-xs font-medium text-muted uppercase tracking-wide mb-3">
            Demo users
          </p>
          {DEMO_USERS.map((u) => {
            const Icon = u.icon;
            const isLoading = submitting === u.username;
            return (
              <button
                key={u.username}
                type="button"
                disabled={submitting !== null}
                onClick={() => handleLogin(u.username)}
                className={cn(
                  "w-full flex items-center gap-3 rounded-xl border px-4 py-3 text-left",
                  "transition-all hover:shadow-sm disabled:opacity-60",
                  u.bg,
                )}
              >
                <div className={cn("shrink-0 h-9 w-9 rounded-lg flex items-center justify-center bg-white/60 dark:bg-black/20")}>
                  {isLoading ? (
                    <span className="h-4 w-4 border-2 border-current border-t-transparent rounded-full animate-spin" />
                  ) : (
                    <Icon className={cn("h-4 w-4", u.color)} />
                  )}
                </div>
                <div className="min-w-0">
                  <p className="text-sm font-semibold text-fg">{u.display_name}</p>
                  <p className="text-xs text-muted truncate">{u.description}</p>
                </div>
                <span className={cn("ml-auto text-xs font-medium px-2 py-0.5 rounded-full shrink-0",
                  u.role === "leadership" ? "bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300"
                  : u.role === "hr" ? "bg-teal-100 text-teal-700 dark:bg-teal-900/40 dark:text-teal-300"
                  : "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300"
                )}>
                  {u.role}
                </span>
              </button>
            );
          })}
        </div>

        {/* Divider */}
        <div className="relative">
          <div className="absolute inset-0 flex items-center">
            <div className="w-full border-t border-line" />
          </div>
          <div className="relative flex justify-center text-xs">
            <span className="bg-canvas px-2 text-muted">or enter a username</span>
          </div>
        </div>

        {/* Custom username form */}
        <form onSubmit={handleCustomSubmit} className="flex gap-2">
          <input
            type="text"
            value={customUsername}
            onChange={(e) => setCustomUsername(e.target.value)}
            placeholder="any username (defaults to employee role)"
            className="flex-1 h-10 rounded-lg border border-line bg-surface px-3 text-sm text-fg placeholder:text-faint focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-brand-500"
            disabled={submitting !== null}
          />
          <Button
            type="submit"
            disabled={!customUsername.trim() || submitting !== null}
            loading={submitting === customUsername.trim()}
            size="md"
          >
            Sign in
          </Button>
        </form>

        <p className="text-xs text-muted text-center">
          Unknown usernames are treated as <strong>employee</strong> role.
        </p>
      </div>
    </AuthLayout>
  );
}
