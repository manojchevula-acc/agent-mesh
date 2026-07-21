import { memo, useState } from "react";
import { ChevronDown, ChevronRight, Brain, ShieldCheck, GitBranch, Layers, Database, Search } from "lucide-react";
import { cn } from "@/lib/utils";
import type { LLMReasoningEntry, LLMReasoningData } from "@/types/mesh";

// ── Labels & colours ──────────────────────────────────────────────────────────

const AGENT_LABELS: Record<string, string> = {
  compliance:   "Compliance Agent",
  price_assist: "Price Assist Agent",
  data:         "Data Agent",
  data_agent:   "Data Agent",
  rag:          "RAG Agent",
  rag_agent:    "RAG Agent",
};

const PHASE_LABELS: Record<string, string> = {
  safety_review:  "Safety Review",
  intent_routing: "Intent Routing",
  synthesis:      "Answer Synthesis",
  tool_selection: "Tool Selection",
  data_synthesis: "Data Synthesis",
  rag_synthesis:  "RAG Synthesis",
  unknown:        "Reasoning",
};

// Colour scheme: agent → Tailwind classes for badge bg / text
const AGENT_COLOURS: Record<string, { badge: string; border: string; icon: string }> = {
  compliance:   { badge: "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300",   border: "border-amber-200 dark:border-amber-800",  icon: "text-amber-500" },
  price_assist: { badge: "bg-brand-100 text-brand-800 dark:bg-brand-900/40 dark:text-brand-300",   border: "border-brand-200 dark:border-brand-700",  icon: "text-brand-500" },
  data:         { badge: "bg-teal-100 text-teal-800 dark:bg-teal-900/40 dark:text-teal-300",        border: "border-teal-200 dark:border-teal-700",    icon: "text-teal-500" },
  data_agent:   { badge: "bg-teal-100 text-teal-800 dark:bg-teal-900/40 dark:text-teal-300",        border: "border-teal-200 dark:border-teal-700",    icon: "text-teal-500" },
  rag:          { badge: "bg-violet-100 text-violet-800 dark:bg-violet-900/40 dark:text-violet-300", border: "border-violet-200 dark:border-violet-700", icon: "text-violet-500" },
  rag_agent:    { badge: "bg-violet-100 text-violet-800 dark:bg-violet-900/40 dark:text-violet-300", border: "border-violet-200 dark:border-violet-700", icon: "text-violet-500" },
};

const PHASE_ICONS: Record<string, React.ReactNode> = {
  safety_review:  <ShieldCheck className="h-3.5 w-3.5" />,
  intent_routing: <GitBranch   className="h-3.5 w-3.5" />,
  synthesis:      <Layers      className="h-3.5 w-3.5" />,
  tool_selection: <Brain       className="h-3.5 w-3.5" />,
  data_synthesis: <Database    className="h-3.5 w-3.5" />,
  rag_synthesis:  <Search      className="h-3.5 w-3.5" />,
};

function agentColours(agent: string) {
  return AGENT_COLOURS[agent] ?? AGENT_COLOURS.price_assist;
}

function phaseIcon(phase: string) {
  return PHASE_ICONS[phase] ?? <Brain className="h-3.5 w-3.5" />;
}

// ── Confidence bar ─────────────────────────────────────────────────────────────

function ConfidenceBar({ value }: { value: number }) {
  const pct = Math.round(Math.min(1, Math.max(0, value)) * 100);
  const colour =
    pct >= 80 ? "bg-green-500" :
    pct >= 60 ? "bg-yellow-500" : "bg-red-500";

  return (
    <div className="flex items-center gap-2 mt-1">
      <div className="flex-1 h-1.5 bg-line rounded-full overflow-hidden">
        <div className={cn("h-full rounded-full transition-all", colour)} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-muted text-[10px] w-8 text-right">{pct}%</span>
    </div>
  );
}

// ── Intent badge (data / knowledge / hybrid) ────────────────────────────────

function IntentBadge({ intent }: { intent: string }) {
  const styles: Record<string, string> = {
    data:      "bg-teal-100 text-teal-800 dark:bg-teal-900/40 dark:text-teal-300",
    knowledge: "bg-violet-100 text-violet-800 dark:bg-violet-900/40 dark:text-violet-300",
    hybrid:    "bg-sky-100 text-sky-800 dark:bg-sky-900/40 dark:text-sky-300",
  };
  const label = intent === "knowledge" ? "Knowledge / RAG" : intent.charAt(0).toUpperCase() + intent.slice(1);
  return (
    <span className={cn("inline-block px-2 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wider", styles[intent] ?? "bg-line text-muted")}>
      {label}
    </span>
  );
}

// ── Signal pill list ──────────────────────────────────────────────────────────

function SignalList({ label, signals, colour }: { label: string; signals: string[]; colour: string }) {
  if (!signals || signals.length === 0) return null;
  return (
    <div className="mt-1.5">
      <p className="text-[10px] uppercase tracking-wider text-muted mb-1">{label}</p>
      <div className="flex flex-wrap gap-1">
        {signals.map((s, i) => (
          <span key={`${i}-${s}`} className={cn("px-1.5 py-0.5 rounded text-[10px] font-medium", colour)}>
            {s}
          </span>
        ))}
      </div>
    </div>
  );
}

// ── Chain-of-thought steps ────────────────────────────────────────────────────

function StepsChain({ steps }: { steps: string[] }) {
  if (!steps || steps.length === 0) return null;
  return (
    <div className="mt-2">
      <p className="text-[10px] uppercase tracking-wider text-muted mb-1">Reasoning chain</p>
      <ol className="space-y-1 border-l-2 border-slate-200 dark:border-slate-700 pl-3">
        {steps.map((step, i) => (
          <li key={i} className="text-[10px] text-muted flex gap-1.5">
            <span className="font-semibold text-slate-400 shrink-0">{i + 1}.</span>
            <span>{step}</span>
          </li>
        ))}
      </ol>
    </div>
  );
}

// ── Single reasoning card ─────────────────────────────────────────────────────

const ReasoningCard = memo(function ReasoningCard({
  index,
  entry,
}: {
  index: number;
  entry: LLMReasoningEntry;
}) {
  const [open, setOpen] = useState(true);
  const cols = agentColours(entry.agent);
  const { data } = entry;

  const agentLabel = AGENT_LABELS[entry.agent] ?? entry.agent;
  const phaseLabel = PHASE_LABELS[entry.phase] ?? entry.phase.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase());

  // Decide status colour for safety_review
  const decisionPassed = data.decision ? data.decision.toUpperCase() === "PASSED" : true;

  return (
    <div className={cn("rounded-lg border text-xs", cols.border, "bg-canvas")}>
      {/* Card header */}
      <button
        onClick={() => setOpen(v => !v)}
        className="w-full flex items-center gap-2 px-3 py-2 text-left cursor-pointer"
      >
        <span className={cn("shrink-0 w-5 h-5 rounded-full flex items-center justify-center text-[10px] font-bold bg-canvas border", cols.border, cols.icon)}>
          {index}
        </span>

        {/* Agent badge */}
        <span className={cn("shrink-0 px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wide", cols.badge)}>
          {agentLabel}
        </span>

        {/* Phase badge */}
        <span className="flex items-center gap-1 shrink-0 text-muted">
          <span className={cols.icon}>{phaseIcon(entry.phase)}</span>
          <span className="font-medium text-[10px] uppercase tracking-wide">{phaseLabel}</span>
        </span>

        <span className="flex-1" />

        {/* Quick verdict for safety_review */}
        {entry.phase === "safety_review" && data.decision && (
          <span className={cn("shrink-0 font-semibold text-[10px]", decisionPassed ? "text-green-600 dark:text-green-400" : "text-red-600 dark:text-red-400")}>
            {decisionPassed ? "PASSED" : "FAILED"}
          </span>
        )}

        {open
          ? <ChevronDown className="shrink-0 h-3 w-3 text-muted" />
          : <ChevronRight className="shrink-0 h-3 w-3 text-muted" />
        }
      </button>

      {/* Card body */}
      {open && (
        <div className="px-3 pb-3 space-y-2 border-t border-line">

          {/* ── INTENT ROUTING ───────────────────────────── */}
          {entry.phase === "intent_routing" && (
            <>
              {data.intent && (
                <div className="mt-2 flex items-center gap-2">
                  <span className="text-muted text-[10px] uppercase tracking-wider w-14 shrink-0">Routed to</span>
                  <IntentBadge intent={data.intent} />
                </div>
              )}
              {data.rationale && (
                <blockquote className="mt-1.5 border-l-2 border-brand-300 dark:border-brand-700 pl-2 text-muted italic">
                  {data.rationale}
                </blockquote>
              )}
              <SignalList
                label="Data signals detected"
                signals={data.data_signals ?? []}
                colour="bg-teal-100 text-teal-800 dark:bg-teal-900/40 dark:text-teal-300"
              />
              <SignalList
                label="Policy / RAG signals detected"
                signals={data.rag_signals ?? []}
                colour="bg-violet-100 text-violet-800 dark:bg-violet-900/40 dark:text-violet-300"
              />
              {data.confidence != null && (
                <div className="mt-1.5">
                  <p className="text-[10px] uppercase tracking-wider text-muted mb-0.5">Routing confidence</p>
                  <ConfidenceBar value={data.confidence} />
                </div>
              )}
              <StepsChain steps={(data.steps as string[]) ?? []} />
            </>
          )}

          {/* ── SYNTHESIS ────────────────────────────────── */}
          {entry.phase === "synthesis" && (
            <>
              {data.sources_used && data.sources_used.length > 0 && (
                <div className="mt-2">
                  <p className="text-[10px] uppercase tracking-wider text-muted mb-1">Sources used</p>
                  <div className="flex flex-wrap gap-1">
                    {data.sources_used.map((s, i) => (
                      <span key={`${i}-${s}`} className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-brand-100 text-brand-800 dark:bg-brand-900/40 dark:text-brand-300">
                        {s}
                      </span>
                    ))}
                  </div>
                </div>
              )}
              {data.key_findings && data.key_findings.length > 0 && (
                <div className="mt-1.5">
                  <p className="text-[10px] uppercase tracking-wider text-muted mb-1">Key findings</p>
                  <ul className="space-y-0.5">
                    {data.key_findings.map((f, i) => (
                      <li key={`${i}-${f}`} className="flex items-start gap-1.5 text-muted">
                        <span className="text-brand-400 shrink-0">›</span>
                        <span>{f}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {data.answer_rationale && (
                <blockquote className="mt-1.5 border-l-2 border-brand-300 dark:border-brand-700 pl-2 text-muted italic">
                  {data.answer_rationale}
                </blockquote>
              )}
              <StepsChain steps={(data.steps as string[]) ?? []} />
            </>
          )}

          {/* ── SAFETY REVIEW ────────────────────────────── */}
          {entry.phase === "safety_review" && (
            <>
              {data.checks && data.checks.length > 0 && (
                <div className="mt-2">
                  <p className="text-[10px] uppercase tracking-wider text-muted mb-1">Checks performed</p>
                  <ul className="space-y-0.5">
                    {data.checks.map((c, i) => (
                      <li key={`${i}-${c}`} className="flex items-start gap-1.5 text-muted">
                        <span className={cn("shrink-0", decisionPassed ? "text-green-500" : "text-amber-500")}>✓</span>
                        <span className="capitalize">{c.replace(/_/g, " ")}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {data.risk_signals && data.risk_signals.length > 0 && (
                <SignalList
                  label="Risk signals"
                  signals={data.risk_signals}
                  colour="bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300"
                />
              )}
              {/* ── Authorization block (rbac_authorization check) ── */}
              {data.authorization && (
                <div className="mt-2 rounded border border-line bg-surface p-2 space-y-1.5">
                  <p className="text-[10px] uppercase tracking-wider text-muted font-semibold">Role Authorization</p>
                  <div className="flex flex-wrap items-center gap-2">
                    {data.authorization.role && (
                      <span className="px-1.5 py-0.5 rounded text-[10px] font-semibold bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300">
                        {data.authorization.role.replace(/_/g, " ")}
                      </span>
                    )}
                    {data.authorization.request_task_category && (
                      <span className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300">
                        {data.authorization.request_task_category.replace(/_/g, " ")}
                      </span>
                    )}
                    <span className={cn(
                      "ml-auto flex items-center gap-1 text-[10px] font-bold",
                      data.authorization.authorized
                        ? "text-green-600 dark:text-green-400"
                        : "text-red-600 dark:text-red-400"
                    )}>
                      {data.authorization.authorized ? "✓ Authorized" : "✗ Unauthorized"}
                    </span>
                  </div>
                  {data.authorization.authorization_rationale && (
                    <p className="text-[10px] text-muted italic">
                      {data.authorization.authorization_rationale}
                    </p>
                  )}
                </div>
              )}
              {data.rationale && (
                <blockquote className={cn("mt-1.5 border-l-2 pl-2 italic text-muted", decisionPassed ? "border-green-400 dark:border-green-700" : "border-red-400 dark:border-red-700")}>
                  {data.rationale}
                </blockquote>
              )}
              <StepsChain steps={(data.steps as string[]) ?? []} />
            </>
          )}

          {/* ── TOOL SELECTION (data / rag agents) ───────── */}
          {entry.phase === "tool_selection" && (
            <>
              {data.call_index != null && Number(data.call_index) > 1 && (
                <div className="mt-2 flex items-center gap-2">
                  <span className="px-1.5 py-0.5 rounded text-[10px] font-semibold bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300">
                    Additional call #{String(data.call_index)}
                  </span>
                </div>
              )}
              {data.tool_selected && (
                <div className="mt-2 flex items-center gap-2">
                  <span className="text-muted text-[10px] uppercase tracking-wider w-14 shrink-0">Tool</span>
                  <span className={cn("px-1.5 py-0.5 rounded text-[10px] font-semibold", cols.badge)}>
                    {data.tool_selected as string}
                  </span>
                </div>
              )}
              {(data.customer_id as string) && (
                <div className="flex items-center gap-2 mt-1">
                  <span className="text-muted text-[10px] uppercase tracking-wider w-14 shrink-0">Customer</span>
                  <span className="text-fg font-medium text-[10px]">{data.customer_id as string}</span>
                </div>
              )}
              {(data.search_query as string) && (
                <div className="mt-1.5">
                  <p className="text-[10px] uppercase tracking-wider text-muted mb-0.5">Search query</p>
                  <span className="font-mono text-[10px] text-fg bg-surface px-2 py-1 rounded border border-line block">
                    {data.search_query as string}
                  </span>
                </div>
              )}
              {(data.knowledge_domain as string) && (
                <div className="flex items-center gap-2 mt-1">
                  <span className="text-muted text-[10px] uppercase tracking-wider w-14 shrink-0">Domain</span>
                  <span className={cn("px-1.5 py-0.5 rounded text-[10px] font-medium", cols.badge)}>
                    {(data.knowledge_domain as string).replace(/_/g, " ")}
                  </span>
                </div>
              )}
              {(data.query_intent as string) && (
                <div className="flex items-center gap-2 mt-1">
                  <span className="text-muted text-[10px] uppercase tracking-wider w-14 shrink-0">Intent</span>
                  <span className="text-muted text-[10px]">{data.query_intent as string}</span>
                </div>
              )}
              {data.rationale && (
                <blockquote className="mt-1.5 border-l-2 border-line pl-2 text-muted italic">
                  {data.rationale}
                </blockquote>
              )}
              {(data.additional_call_reason as string) && (
                <div className="mt-1.5">
                  <p className="text-[10px] uppercase tracking-wider text-amber-600 dark:text-amber-400 mb-0.5">Why an additional call was made</p>
                  <blockquote className="border-l-2 border-amber-400 dark:border-amber-700 pl-2 text-muted italic">
                    {data.additional_call_reason as string}
                  </blockquote>
                </div>
              )}
              {data.duplicate_suppressed_count != null && Number(data.duplicate_suppressed_count) > 0 && (
                <div className="mt-1.5 flex items-center gap-1.5 text-[10px] text-amber-700 dark:text-amber-400">
                  <span className="px-1.5 py-0.5 rounded font-semibold bg-amber-100 dark:bg-amber-900/40">
                    {String(data.duplicate_suppressed_count)} duplicate retrieval{Number(data.duplicate_suppressed_count) !== 1 ? "s" : ""} suppressed
                  </span>
                  <span className="text-muted">— identical call(s) repeated with no new information (see server log for real MCP count).</span>
                </div>
              )}
              <StepsChain steps={(data.steps as string[]) ?? []} />
            </>
          )}

          {/* ── DATA SYNTHESIS (DataAgent final response) ── */}
          {entry.phase === "data_synthesis" && (
            <>
              <div className="mt-2 flex items-center gap-3 flex-wrap">
                {data.rows !== undefined && (
                  <span className="px-1.5 py-0.5 rounded text-[10px] font-semibold bg-teal-100 text-teal-800 dark:bg-teal-900/40 dark:text-teal-300">
                    {String(data.rows)} row{Number(data.rows) !== 1 ? "s" : ""} returned
                  </span>
                )}
              </div>
              {data.finding && (
                <p className="mt-1.5 text-[10px] font-medium text-fg">{data.finding as string}</p>
              )}
              <StepsChain steps={(data.steps as string[]) ?? []} />
            </>
          )}

          {/* ── RAG SYNTHESIS (RAGAgent final response) ───── */}
          {entry.phase === "rag_synthesis" && (
            <>
              <div className="mt-2 flex items-center gap-3 flex-wrap">
                {data.docs !== undefined && (
                  <span className="px-1.5 py-0.5 rounded text-[10px] font-semibold bg-violet-100 text-violet-800 dark:bg-violet-900/40 dark:text-violet-300">
                    {String(data.docs)} document{Number(data.docs) !== 1 ? "s" : ""} matched
                  </span>
                )}
              </div>
              {data.finding && (
                <p className="mt-1.5 text-[10px] font-medium text-fg">{data.finding as string}</p>
              )}
              <StepsChain steps={(data.steps as string[]) ?? []} />
            </>
          )}

          {/* ── FALLBACK (raw / unknown phase) ───────────── */}
          {entry.phase !== "intent_routing" && entry.phase !== "synthesis" && entry.phase !== "safety_review" && entry.phase !== "tool_selection" && entry.phase !== "data_synthesis" && entry.phase !== "rag_synthesis" && (
            <div className="mt-2">
              {data.rationale && (
                <blockquote className="border-l-2 border-line pl-2 text-muted italic">{data.rationale}</blockquote>
              )}
              {data.raw && (
                <pre className="mt-1.5 text-[10px] text-muted whitespace-pre-wrap break-all font-mono bg-surface p-2 rounded border border-line">
                  {data.raw}
                </pre>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
});

// ── Public component ──────────────────────────────────────────────────────────

interface LLMReasoningPanelProps {
  entries: LLMReasoningEntry[];
}

// Collapse redundant tool_selection blocks (Option A).
//
// The trace is built from <llm_reasoning> blocks the model *wrote*, so an
// identical retrieval can appear twice even though it carries no new decision.
// Rather than render a second card with a hollow "justification", we DROP exact
// duplicates (same agent + same tool + same query as an earlier call in the same
// request) and annotate the surviving first card with how many were suppressed.
//
// A genuinely DIFFERENT follow-up call (different tool, or a materially different
// query) is kept and labelled with its real reason — that IS a distinct decision.
//
// Note: this de-duplicates the *display*. The authoritative count of REAL MCP
// invocations is logged server-side by ToolCallLogMiddleware (see
// src/middleware/tool_call_logger.py) — the UI never guesses at that.
function annotateToolCalls(entries: LLMReasoningEntry[]): LLMReasoningEntry[] {
  const seqByAgent: Record<string, number> = {};
  const callsByAgent: Record<string, LLMReasoningData[]> = {};
  // Map from an agent's kept first-call data object → suppressed duplicate count.
  const suppressed = new Map<LLMReasoningData, number>();
  const out: LLMReasoningEntry[] = [];

  // Canonical signature: tool name + a normalized token SET of the query so that
  // trivial differences (stopwords like "for"/"the", punctuation, word order)
  // collapse to the same value. "pricing floor BB-rated AED corporate loans" and
  // "pricing floor for BB-rated AED corporate loans" → identical signature.
  const STOPWORDS = new Set([
    "for", "the", "a", "an", "of", "in", "on", "to", "and", "or", "with",
    "what", "is", "are", "please", "show", "me", "give",
  ]);
  const norm = (d: LLMReasoningData) => {
    const tool = (d.tool_selected ?? "").trim().toLowerCase();
    const query = (d.search_query ?? d.query_intent ?? "").toLowerCase();
    const tokens = query
      .replace(/[^a-z0-9\s]/g, " ") // strip punctuation/hyphens
      .split(/\s+/)
      .filter((t) => t && !STOPWORDS.has(t))
      .sort();
    return `${tool}::${Array.from(new Set(tokens)).join(" ")}`;
  };

  for (const entry of entries) {
    if (entry.phase !== "tool_selection") {
      out.push(entry);
      continue;
    }

    const agent = entry.agent;
    const prior = callsByAgent[agent] ?? [];
    const sig = norm(entry.data);

    // Exact duplicate of an earlier call this request → suppress from display.
    const firstMatch = prior.find((d) => norm(d) === sig);
    if (firstMatch) {
      suppressed.set(firstMatch, (suppressed.get(firstMatch) ?? 0) + 1);
      continue; // do not push a card for the redundant call
    }

    const n = (seqByAgent[agent] ?? 0) + 1;
    seqByAgent[agent] = n;
    prior.push(entry.data);
    callsByAgent[agent] = prior;

    const data: LLMReasoningData = { ...entry.data };
    if (data.call_index == null) data.call_index = n;

    // A kept call after the first is a genuinely different follow-up.
    if (n > 1 && !data.additional_call_reason) {
      const curTool = (data.tool_selected ?? "").trim();
      data.additional_call_reason =
        `Follow-up call${curTool ? ` to "${curTool}"` : ""} for information the ` +
        `previous call did not provide.`;
    }

    out.push({ ...entry, data });
  }

  // Attach suppressed-duplicate counts to the surviving first-call cards.
  if (suppressed.size > 0) {
    for (const entry of out) {
      if (entry.phase !== "tool_selection") continue;
      // Find the original data object this card was derived from.
      for (const [firstData, count] of suppressed) {
        if (norm(entry.data) === norm(firstData)) {
          (entry.data as LLMReasoningData).duplicate_suppressed_count = count;
        }
      }
    }
  }

  return out;
}

export default function LLMReasoningPanel({ entries }: LLMReasoningPanelProps) {
  if (!entries || entries.length === 0) {
    return (
      <div className="text-xs text-muted text-center py-4">
        No LLM reasoning captured for this request.
      </div>
    );
  }

  const annotated = annotateToolCalls(entries);

  return (
    <div className="space-y-2">
      <p className="text-[10px] uppercase tracking-wider text-muted font-semibold">
        {annotated.length} decision point{annotated.length !== 1 ? "s" : ""} captured
      </p>
      {annotated.map((entry, i) => (
        <ReasoningCard key={`${entry.agent}-${entry.phase}-${i}`} index={i + 1} entry={entry} />
      ))}
    </div>
  );
}
