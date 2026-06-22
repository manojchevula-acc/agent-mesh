import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { CheckCircle2 } from "lucide-react";
import { Logo } from "@/components/ui/Logo";
import { ThemeToggle } from "@/components/ui/ThemeToggle";

const HIGHLIGHTS = [
  "Grounded, cited answers over FAB policies",
  "Hybrid dense + sparse retrieval",
  "RAGAS quality scoring built in",
  "Freshness-aware, enterprise ready",
];

/** Two-pane shell for the login / signup forms. */
export function AuthLayout({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="flex min-h-screen bg-canvas">
      {/* Brand panel */}
      <div className="relative hidden w-1/2 overflow-hidden bg-gradient-to-br from-brand-800 via-brand-700 to-accent-700 lg:flex lg:flex-col lg:justify-between lg:p-12">
        <div className="pointer-events-none absolute inset-0 bg-grid-dark bg-[size:32px_32px] opacity-30" />
        <div className="pointer-events-none absolute -left-24 top-1/4 h-80 w-80 rounded-full bg-accent-400/30 blur-3xl animate-float-slow" />
        <div className="pointer-events-none absolute -right-16 bottom-10 h-72 w-72 rounded-full bg-brand-400/30 blur-3xl animate-float" />

        <Link to="/" className="relative">
          <Logo
            className="text-white [&_p:first-child]:text-white [&_p:last-child]:text-white/70"
            subtitle="FAB Policy Assistant"
          />
        </Link>

        <div className="relative">
          <h2 className="max-w-md text-3xl font-bold leading-tight text-white">
            Turn dense policy documents into instant, trustworthy answers.
          </h2>
          <ul className="mt-8 space-y-3">
            {HIGHLIGHTS.map((h) => (
              <li key={h} className="flex items-center gap-3 text-white/85">
                <CheckCircle2 className="h-5 w-5 shrink-0 text-accent-200" />
                <span>{h}</span>
              </li>
            ))}
          </ul>
        </div>

        <p className="relative text-sm text-white/60">
          © {new Date().getFullYear()} GERNAS RAG — FAB Policy &amp; Regulatory Assistant.
        </p>
      </div>

      {/* Form panel */}
      <div className="flex w-full flex-col lg:w-1/2">
        <div className="flex items-center justify-between px-6 py-5 sm:px-10">
          <Link to="/" className="lg:hidden">
            <Logo showText={false} />
          </Link>
          <span className="hidden lg:block" />
          <ThemeToggle />
        </div>

        <div className="flex flex-1 items-center justify-center px-6 pb-12 sm:px-10">
          <div className="w-full max-w-md animate-fade-in-up">
            <h1 className="text-2xl font-bold tracking-tight text-fg">{title}</h1>
            <p className="mt-2 text-sm text-muted">{subtitle}</p>
            <div className="mt-8">{children}</div>
          </div>
        </div>
      </div>
    </div>
  );
}
