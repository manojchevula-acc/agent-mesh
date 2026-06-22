import { Link } from "react-router-dom";
import {
  ArrowRight,
  BarChart3,
  FileSearch,
  Gauge,
  Layers,
  Lock,
  Search,
  Sparkles,
  Upload,
  Zap,
} from "lucide-react";
import { PublicNav } from "@/components/layout/PublicNav";
import { Button } from "@/components/ui/Button";
import { useAuth } from "@/contexts/AuthContext";

const FEATURES = [
  {
    icon: Search,
    title: "Hybrid retrieval",
    desc: "Dense + sparse search over FAB credit policies, CBUAE circulars and risk frameworks for pinpoint answers.",
  },
  {
    icon: Sparkles,
    title: "Grounded answers",
    desc: "Every LLM answer cites the exact source chunks that grounded it — no hallucinations, full traceability.",
  },
  {
    icon: Upload,
    title: "One-click ingestion",
    desc: "Drop a PDF or DOCX and watch extraction → chunking → embedding run end-to-end automatically.",
  },
  {
    icon: BarChart3,
    title: "RAGAS evaluation",
    desc: "Score faithfulness, relevancy and context quality against the FAB test set with a single click.",
  },
  {
    icon: Gauge,
    title: "Freshness aware",
    desc: "Stale-context warnings flag answers built on outdated policy versions before they reach a decision.",
  },
  {
    icon: Lock,
    title: "Enterprise ready",
    desc: "API-key auth, multi-tenant design and observability baked in for production deployment at the bank.",
  },
];

const STATS = [
  { value: "5+", label: "Policy domains" },
  { value: "<2s", label: "Typical latency" },
  { value: "Hybrid", label: "Dense + sparse" },
  { value: "RAGAS", label: "Quality scored" },
];

const STEPS = [
  { icon: Upload, title: "Ingest", desc: "Upload policy documents — they're parsed, chunked and embedded." },
  { icon: Search, title: "Ask", desc: "Pose a natural-language policy or regulatory question." },
  { icon: Layers, title: "Retrieve", desc: "Hybrid search surfaces the most relevant grounded chunks." },
  { icon: FileSearch, title: "Answer", desc: "Get a cited answer with quality metrics you can trust." },
];

export function HomePage() {
  const { isAuthenticated } = useAuth();
  const ctaTarget = isAuthenticated ? "/app/search" : "/signup";

  return (
    <div className="min-h-screen bg-canvas">
      <PublicNav />

      {/* ── Hero ───────────────────────────────────────────────────────── */}
      <section className="relative overflow-hidden">
        {/* Animated background */}
        <div className="pointer-events-none absolute inset-0 -z-10">
          <div className="absolute -left-32 -top-32 h-96 w-96 rounded-full bg-brand-500/20 blur-3xl animate-float-slow" />
          <div className="absolute right-0 top-20 h-80 w-80 rounded-full bg-accent-400/20 blur-3xl animate-float" />
          <div className="absolute bottom-0 left-1/3 h-72 w-72 rounded-full bg-brand-400/10 blur-3xl animate-float-slow" />
          <div className="absolute inset-0 bg-grid-light bg-[size:40px_40px] [mask-image:radial-gradient(ellipse_at_center,black,transparent_75%)] dark:bg-grid-dark" />
        </div>

        <div className="mx-auto max-w-6xl px-4 pb-20 pt-16 text-center sm:px-6 sm:pt-24">
          <div className="mb-6 inline-flex animate-fade-in-up items-center gap-2 rounded-full border border-line bg-surface/60 px-4 py-1.5 text-xs font-medium text-muted backdrop-blur">
            <span className="relative flex h-2 w-2">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75" />
              <span className="relative inline-flex h-2 w-2 rounded-full bg-emerald-500" />
            </span>
            GERNAS — Generative Retrieval Network for Assurance &amp; Strategy
          </div>

          <h1
            className="mx-auto max-w-4xl animate-fade-in-up text-4xl font-extrabold tracking-tight text-fg sm:text-6xl"
            style={{ animationDelay: "60ms" }}
          >
            Ask anything about{" "}
            <span className="text-gradient animate-gradient-pan">FAB policies</span>
            <br className="hidden sm:block" /> and get answers you can trust.
          </h1>

          <p
            className="mx-auto mt-6 max-w-2xl animate-fade-in-up text-lg leading-relaxed text-muted"
            style={{ animationDelay: "140ms" }}
          >
            A production-grade RAG assistant for credit policies, CBUAE circulars and risk
            frameworks. Grounded, cited, and continuously evaluated for quality.
          </p>

          <div
            className="mt-9 flex animate-fade-in-up flex-col items-center justify-center gap-3 sm:flex-row"
            style={{ animationDelay: "220ms" }}
          >
            <Link to={ctaTarget}>
              <Button size="lg" className="group w-full shadow-lg shadow-brand-600/25 sm:w-auto">
                {isAuthenticated ? "Open the app" : "Get started free"}
                <ArrowRight className="h-4 w-4 transition-transform group-hover:translate-x-1" />
              </Button>
            </Link>
            <Link to="/login">
              <Button variant="outline" size="lg" className="w-full sm:w-auto">
                Sign in
              </Button>
            </Link>
          </div>

          {/* Stats */}
          <div
            className="mx-auto mt-16 grid max-w-3xl animate-fade-in-up grid-cols-2 gap-4 sm:grid-cols-4"
            style={{ animationDelay: "300ms" }}
          >
            {STATS.map((s) => (
              <div
                key={s.label}
                className="rounded-2xl border border-line bg-surface/60 px-4 py-5 backdrop-blur transition-transform hover:-translate-y-1"
              >
                <div className="text-2xl font-bold text-gradient">{s.value}</div>
                <div className="mt-1 text-xs font-medium uppercase tracking-wide text-muted">
                  {s.label}
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ── Features ───────────────────────────────────────────────────── */}
      <section className="mx-auto max-w-6xl px-4 py-20 sm:px-6">
        <div className="mx-auto max-w-2xl text-center">
          <h2 className="text-3xl font-bold tracking-tight text-fg">Built for policy intelligence</h2>
          <p className="mt-3 text-muted">
            Everything you need to turn dense regulatory documents into instant, trustworthy answers.
          </p>
        </div>

        <div className="mt-12 grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-3">
          {FEATURES.map(({ icon: Icon, title, desc }, i) => (
            <div
              key={title}
              className="group relative animate-fade-in-up overflow-hidden rounded-2xl border border-line bg-surface p-6 shadow-sm transition-all duration-300 hover:-translate-y-1.5 hover:border-brand-400/60 hover:shadow-xl hover:shadow-brand-600/10"
              style={{ animationDelay: `${i * 70}ms` }}
            >
              <div className="absolute -right-10 -top-10 h-28 w-28 rounded-full bg-gradient-to-br from-brand-500/10 to-accent-400/10 opacity-0 blur-2xl transition-opacity duration-300 group-hover:opacity-100" />
              <div className="mb-4 inline-flex h-12 w-12 items-center justify-center rounded-xl bg-gradient-to-br from-brand-600 to-accent-500 text-white shadow-lg shadow-brand-600/20 transition-transform duration-300 group-hover:scale-110 group-hover:rotate-3">
                <Icon className="h-6 w-6" />
              </div>
              <h3 className="text-lg font-semibold text-fg">{title}</h3>
              <p className="mt-2 text-sm leading-relaxed text-muted">{desc}</p>
            </div>
          ))}
        </div>
      </section>

      {/* ── How it works ───────────────────────────────────────────────── */}
      <section className="border-y border-line bg-surface-2/50">
        <div className="mx-auto max-w-6xl px-4 py-20 sm:px-6">
          <div className="mx-auto max-w-2xl text-center">
            <h2 className="text-3xl font-bold tracking-tight text-fg">How it works</h2>
            <p className="mt-3 text-muted">From raw document to grounded answer in four steps.</p>
          </div>

          <div className="relative mt-14 grid grid-cols-1 gap-8 sm:grid-cols-2 lg:grid-cols-4">
            {STEPS.map(({ icon: Icon, title, desc }, i) => (
              <div key={title} className="relative text-center">
                <div className="mx-auto flex h-16 w-16 items-center justify-center rounded-2xl border border-line bg-surface text-brand-600 shadow-sm dark:text-brand-300">
                  <Icon className="h-7 w-7" />
                </div>
                <div className="mx-auto mt-3 flex h-6 w-6 items-center justify-center rounded-full bg-brand-600 text-xs font-bold text-white">
                  {i + 1}
                </div>
                <h3 className="mt-3 font-semibold text-fg">{title}</h3>
                <p className="mt-1.5 text-sm text-muted">{desc}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ── CTA ────────────────────────────────────────────────────────── */}
      <section className="mx-auto max-w-6xl px-4 py-20 sm:px-6">
        <div className="relative overflow-hidden rounded-3xl bg-gradient-to-br from-brand-700 via-brand-600 to-accent-600 px-6 py-16 text-center shadow-2xl shadow-brand-600/30">
          <div className="pointer-events-none absolute inset-0 bg-grid-dark bg-[size:32px_32px] opacity-30" />
          <div className="pointer-events-none absolute -right-20 -top-20 h-72 w-72 rounded-full bg-white/10 blur-3xl animate-float" />
          <div className="relative">
            <Zap className="mx-auto h-10 w-10 text-white/90" />
            <h2 className="mx-auto mt-4 max-w-xl text-3xl font-bold text-white sm:text-4xl">
              Start querying FAB policies in seconds
            </h2>
            <p className="mx-auto mt-3 max-w-lg text-white/80">
              No setup required. Create an account and ask your first policy question right away.
            </p>
            <Link to={ctaTarget} className="mt-8 inline-block">
              <Button
                size="lg"
                className="group bg-white text-brand-700 shadow-lg hover:bg-white/90 active:bg-white/80"
              >
                {isAuthenticated ? "Open the app" : "Create your account"}
                <ArrowRight className="h-4 w-4 transition-transform group-hover:translate-x-1" />
              </Button>
            </Link>
          </div>
        </div>
      </section>

      {/* ── Footer ─────────────────────────────────────────────────────── */}
      <footer className="border-t border-line">
        <div className="mx-auto flex max-w-6xl flex-col items-center justify-between gap-4 px-4 py-8 text-sm text-muted sm:flex-row sm:px-6">
          <p>© {new Date().getFullYear()} GERNAS RAG — FAB Policy &amp; Regulatory Assistant.</p>
          <div className="flex items-center gap-5">
            <Link to="/login" className="hover:text-fg">
              Sign in
            </Link>
            <Link to="/signup" className="hover:text-fg">
              Get started
            </Link>
          </div>
        </div>
      </footer>
    </div>
  );
}
