import { Link } from "react-router-dom";
import {
  ArrowRight,
  Bot,
  GitMerge,
  Lock,
  Shield,
  ShieldCheck,
  Zap,
  Network,
  Activity,
} from "lucide-react";
import { PublicNav } from "@/components/layout/PublicNav";
import { Button } from "@/components/ui/Button";
import { useAuth } from "@/contexts/AuthContext";

const FEATURES = [
  {
    icon: GitMerge,
    title: "4-stage pipeline",
    desc: "Every query flows through guardrails → compliance → policy → redaction.",
  },
  {
    icon: Network,
    title: "Distributed A2A mesh",
    desc: "Independent governance agents on separate ports communicate via the A2A protocol with W3C distributed tracing.",
  },
  {
    icon: ShieldCheck,
    title: "Deterministic guardrails",
    desc: "Regex-based prompt injection, PII, and destructive intent detection before any LLM sees the input.",
  },
  {
    icon: Shield,
    title: "Semantic compliance",
    desc: "LLM-powered compliance reviewer screens every request for injection, leakage, and harmful intent — fails closed.",
  },
  {
    icon: Lock,
    title: "Policy responder",
    desc: "The Policy agent resolves which corporate rules apply to a request and answers, grounded in policies.json.",
  },
  {
    icon: Activity,
    title: "Enterprise observability",
    desc: "Custom OpenTelemetry metrics, trace-correlated loggers, append-only audit trail and Grafana dashboards.",
  },
];

const PIPELINE_STEPS = [
  { n: "1", label: "Input Guardrail",  desc: "Regex screens for injection, PII, and destructive intent" },
  { n: "2", label: "Compliance",       desc: "Semantic LLM safety review — fails closed on injection, leakage, harm" },
  { n: "3", label: "Policy",           desc: "Policy agent resolves and answers which corporate rules apply" },
  { n: "4", label: "Output Redaction", desc: "PII scrubbing before the answer reaches the user" },
];

export function HomePage() {
  const { isAuthenticated } = useAuth();

  return (
    <div className="min-h-screen bg-canvas">
      <PublicNav />

      {/* Hero */}
      <section className="relative overflow-hidden px-6 pt-24 pb-20 text-center">
        <div className="pointer-events-none absolute inset-0 -z-10">
          <div className="absolute left-1/4 top-0 h-96 w-96 rounded-full bg-brand-500/10 blur-3xl" />
          <div className="absolute right-1/4 top-24 h-72 w-72 rounded-full bg-accent-500/10 blur-3xl" />
        </div>

        <div className="mx-auto max-w-3xl">
          <div className="mb-6 inline-flex items-center gap-2 rounded-full border border-brand-200 dark:border-brand-800 bg-brand-50 dark:bg-brand-900/30 px-4 py-1.5 text-sm text-brand-700 dark:text-brand-300">
            <Bot className="h-4 w-4" />
            Microsoft Agent Framework · A2A Protocol · OpenTelemetry
          </div>

          <h1 className="text-4xl font-bold tracking-tight text-fg sm:text-5xl lg:text-6xl">
            Safety &amp;{" "}
            <span className="bg-gradient-to-r from-brand-600 to-accent-500 bg-clip-text text-transparent">
              Governance
            </span>{" "}
            Agent Mesh
          </h1>

          <p className="mx-auto mt-6 max-w-xl text-lg text-muted leading-relaxed">
            A distributed multi-agent system with defense-in-depth security, semantic
            compliance, policy-grounded answers, and enterprise-grade observability — built on
            the Microsoft Agent Framework.
          </p>

          <div className="mt-8 flex flex-wrap items-center justify-center gap-3">
            {isAuthenticated ? (
              <Link to="/app/chat">
                <Button size="lg">
                  Open Chat <ArrowRight className="ml-2 h-4 w-4" />
                </Button>
              </Link>
            ) : (
              <>
                <Link to="/login">
                  <Button size="lg">
                    Get started <ArrowRight className="ml-2 h-4 w-4" />
                  </Button>
                </Link>
                <Link to="/login">
                  <Button variant="outline" size="lg">
                    Sign in
                  </Button>
                </Link>
              </>
            )}
          </div>

          <div className="mt-10 grid grid-cols-2 sm:grid-cols-4 gap-4 max-w-lg mx-auto">
            {[
              { v: "2", l: "Agent nodes" },
              { v: "4", l: "Pipeline stages" },
              { v: "OTel", l: "Observability" },
              { v: "A2A", l: "Protocol" },
            ].map(({ v, l }) => (
              <div key={l} className="rounded-xl bg-surface border border-line p-3 text-center">
                <p className="text-2xl font-bold text-brand-600 dark:text-brand-400">{v}</p>
                <p className="text-xs text-muted mt-0.5">{l}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Pipeline walkthrough */}
      <section className="py-16 px-6 bg-surface border-y border-line">
        <div className="mx-auto max-w-5xl">
          <h2 className="text-2xl font-bold text-fg text-center mb-2">
            Defense-in-depth pipeline
          </h2>
          <p className="text-muted text-center text-sm mb-10">
            Every request passes through all 4 stages. Gates are fail-closed — a block at any
            stage returns a safe error response.
          </p>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
            {PIPELINE_STEPS.map(({ n, label, desc }) => (
              <div
                key={n}
                className="rounded-xl border border-line bg-canvas p-4 flex flex-col gap-2"
              >
                <div className="flex items-center gap-2">
                  <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-brand-100 dark:bg-brand-900/40 text-xs font-bold text-brand-700 dark:text-brand-300">
                    {n}
                  </span>
                  <p className="text-sm font-semibold text-fg">{label}</p>
                </div>
                <p className="text-xs text-muted leading-relaxed">{desc}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Features */}
      <section className="py-16 px-6">
        <div className="mx-auto max-w-5xl">
          <h2 className="text-2xl font-bold text-fg text-center mb-10">
            What's inside
          </h2>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-6">
            {FEATURES.map(({ icon: Icon, title, desc }) => (
              <div
                key={title}
                className="rounded-2xl border border-line bg-surface p-6 flex flex-col gap-3"
              >
                <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-brand-100 dark:bg-brand-900/30">
                  <Icon className="h-5 w-5 text-brand-600 dark:text-brand-400" />
                </div>
                <div>
                  <h3 className="font-semibold text-fg text-sm">{title}</h3>
                  <p className="text-xs text-muted mt-1 leading-relaxed">{desc}</p>
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* CTA */}
      <section className="py-16 px-6 text-center">
        <div className="mx-auto max-w-xl">
          <Zap className="h-10 w-10 text-brand-500 mx-auto mb-4" />
          <h2 className="text-2xl font-bold text-fg mb-3">Ready to explore the mesh?</h2>
          <p className="text-muted text-sm mb-6">
            Sign in as a demo user and see how the security gates, semantic compliance and
            policy responder work in practice.
          </p>
          <Link to="/login">
            <Button size="lg">
              Sign in and try it <ArrowRight className="ml-2 h-4 w-4" />
            </Button>
          </Link>
        </div>
      </section>

      <footer className="border-t border-line py-6 text-center text-xs text-muted">
        Agent Mesh · Microsoft Agent Framework · Built with React + Starlette
      </footer>
    </div>
  );
}
