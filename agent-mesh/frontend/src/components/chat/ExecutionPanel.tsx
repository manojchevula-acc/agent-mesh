import { useState } from "react";
import { ChevronDown, ChevronRight, CheckCircle2, XCircle, AlertTriangle, Activity } from "lucide-react";
import { cn } from "@/lib/utils";
import type { MeshResult, ExecutionEvent } from "@/types/mesh";

// Maps Python stage keys → human-readable step titles (mirrors cli_renderer.py _STAGE_LABELS)
const STAGE_LABELS: Record<string, string> = {
  input_processing:      "Input Processing",
  guardrail:             "Guardrail Validation",
  rbac:                  "RBAC Validation",
  compliance:            "Compliance Validation",
  domain_classification: "Domain Classification",
  routing:               "Routing Decision",
  agent_handoff:         "Agent Handoff",
  data_retrieval:        "Data Retrieval",
  response_generation:   "Response Generation",
  output_redaction:      "Output Redaction",
};

function stageLabel(stage: string): string {
  return STAGE_LABELS[stage] ?? stage.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

// Collapse started+completed pairs for the same stage into one entry,
// keeping blocked/failed events separate.
function collapseEvents(events: ExecutionEvent[]): ExecutionEvent[] {
  const seen = new Map<string, ExecutionEvent>();
  const order: string[] = [];
  for (const ev of events) {
    const key = ev.stage;
    if (!seen.has(key)) order.push(key);
    const existing = seen.get(key);
    // completed/blocked/failed always overwrites started for the same stage
    if (!existing || ev.status !== "started") {
      seen.set(key, ev);
    }
  }
  return order.map((k) => seen.get(k)!);
}

// ── Single step row ──────────────────────────────────────────────────────────

interface StepProps {
  index: number;
  event: ExecutionEvent;
}

function Step({ index, event }: StepProps) {
  const [open, setOpen] = useState(true);
  const isBlocked = event.status === "blocked" || event.status === "failed";
  const hasDetail =
    (event.checks && event.checks.length > 0) ||
    event.result ||
    (event.rationale && event.rationale.length > 0) ||
    (event.metadata && Object.keys(event.metadata).length > 0);

  return (
    <div
      className={cn(
        "rounded-lg border text-xs",
        isBlocked
          ? "border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-950/20"
          : "border-line bg-canvas"
      )}
    >
      {/* Step header */}
      <button
        onClick={() => hasDetail && setOpen((v) => !v)}
        className={cn(
          "w-full flex items-center gap-2 px-3 py-2 text-left",
          hasDetail ? "cursor-pointer" : "cursor-default"
        )}
      >
        <span
          className={cn(
            "shrink-0 w-5 h-5 rounded-full flex items-center justify-center text-[10px] font-bold",
            isBlocked
              ? "bg-red-200 dark:bg-red-800 text-red-700 dark:text-red-300"
              : "bg-brand-100 dark:bg-brand-900/50 text-brand-700 dark:text-brand-300"
          )}
        >
          {index}
        </span>
        <span className={cn("flex-1 font-semibold uppercase tracking-wide text-[10px]",
          isBlocked ? "text-red-700 dark:text-red-400" : "text-fg")}>
          {stageLabel(event.stage)}
        </span>
        {event.result && !isBlocked && (
          <span className="text-green-600 dark:text-green-400 font-medium truncate max-w-[120px]">
            {event.result}
          </span>
        )}
        {isBlocked && (
          <span className="text-red-600 dark:text-red-400 font-semibold">BLOCKED</span>
        )}
        {hasDetail && (
          open
            ? <ChevronDown className="shrink-0 h-3 w-3 text-muted" />
            : <ChevronRight className="shrink-0 h-3 w-3 text-muted" />
        )}
      </button>

      {/* Step body */}
      {open && hasDetail && (
        <div className="px-3 pb-3 space-y-1.5 border-t border-line">
          {/* Checks list */}
          {event.checks && event.checks.length > 0 && (
            <ul className="mt-2 space-y-0.5">
              {event.checks.map((chk, i) => (
                <li key={i} className="flex items-start gap-1.5 text-muted">
                  <CheckCircle2 className="shrink-0 h-3 w-3 text-green-500 mt-0.5" />
                  <span>{chk}</span>
                </li>
              ))}
            </ul>
          )}

          {/* Result */}
          {event.result && (
            <div className="flex items-start gap-1.5 mt-1">
              {isBlocked
                ? <XCircle className="shrink-0 h-3 w-3 text-red-500 mt-0.5" />
                : <CheckCircle2 className="shrink-0 h-3 w-3 text-green-500 mt-0.5" />
              }
              <span className={cn("font-medium", isBlocked ? "text-red-600 dark:text-red-400" : "text-fg")}>
                {event.result}
              </span>
            </div>
          )}

          {/* Rationale */}
          {event.rationale && event.rationale.length > 0 && (
            <div className="mt-1.5">
              <p className="text-[10px] uppercase tracking-wider text-muted mb-1">Reasoning</p>
              <ul className="space-y-0.5">
                {event.rationale.map((r, i) => (
                  <li key={i} className="flex items-start gap-1.5 text-muted">
                    <span className="text-brand-400 shrink-0">›</span>
                    <span>{r}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Agent handoff tree (from metadata.handoff_path) */}
          {event.stage === "agent_handoff" && Array.isArray(event.metadata?.handoff_path) && (
            <div className="mt-1.5">
              <HandoffTree path={event.metadata.handoff_path as string[]} />
            </div>
          )}

          {/* Data retrieval stats */}
          {event.stage === "data_retrieval" && (
            <div className="flex flex-wrap gap-3 mt-1.5">
              {event.metadata?.records_retrieved != null && (
                <span className="text-muted">
                  Records: <strong className="text-fg">{String(event.metadata.records_retrieved)}</strong>
                </span>
              )}
              {event.metadata?.latency_ms != null && (
                <span className="text-muted">
                  Latency: <strong className="text-fg">{String(event.metadata.latency_ms)} ms</strong>
                </span>
              )}
            </div>
          )}

          {/* Duration */}
          {event.duration_ms != null && (
            <p className="text-muted mt-1">{event.duration_ms} ms</p>
          )}
        </div>
      )}
    </div>
  );
}

// ── Agent handoff tree ───────────────────────────────────────────────────────

function HandoffTree({ path }: { path: string[] }) {
  return (
    <div className="font-mono text-xs text-muted">
      {path.map((node, i) => (
        <div key={i} style={{ paddingLeft: `${i * 12}px` }}>
          {i > 0 && <span className="text-brand-400 mr-1">└──</span>}
          <span className={i === 0 ? "text-fg font-semibold" : "text-fg"}>{node}</span>
        </div>
      ))}
    </div>
  );
}

// ── Summary table ────────────────────────────────────────────────────────────

function SummaryTable({ result }: { result: MeshResult }) {
  const rows: Array<{ label: string; value: string; cls?: string }> = [];
  if (result.request_id) rows.push({ label: "Request ID", value: result.request_id });
  if (result.domain) rows.push({ label: "Domain", value: result.domain });
  if (result.route) rows.push({ label: "Route", value: result.route });
  if (result.execution_path?.length) rows.push({ label: "Execution Path", value: result.execution_path.join(" → ") });
  if (result.agents_invoked != null) rows.push({ label: "Agents Invoked", value: String(result.agents_invoked) });
  if (result.tools_used != null) rows.push({ label: "Tools Used", value: String(result.tools_used) });
  if (result.total_duration_ms != null) rows.push({ label: "Total Duration", value: `${(result.total_duration_ms / 1000).toFixed(1)} s` });
  rows.push({
    label: "Status",
    value: result.blocked ? `BLOCKED (at ${result.block_stage ?? "unknown"})` : "SUCCESS",
    cls: result.blocked ? "text-red-600 dark:text-red-400 font-semibold" : "text-green-600 dark:text-green-400 font-semibold",
  });

  if (rows.length === 0) return null;
  return (
    <div className="rounded-lg border border-line bg-canvas overflow-hidden">
      <table className="w-full text-xs">
        <tbody>
          {rows.map(({ label, value, cls }) => (
            <tr key={label} className="border-b border-line last:border-0">
              <td className="px-3 py-1.5 text-muted whitespace-nowrap font-medium w-36">{label}</td>
              <td className={cn("px-3 py-1.5 text-fg", cls)}>{value}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── ExecutionPanel (public component) ───────────────────────────────────────

interface ExecutionPanelProps {
  result: MeshResult;
}

export default function ExecutionPanel({ result }: ExecutionPanelProps) {
  const [open, setOpen] = useState(false);

  const events = result.events ?? [];
  const steps = collapseEvents(events);
  const stepCount = steps.length;
  const durationLabel = result.total_duration_ms != null
    ? `${(result.total_duration_ms / 1000).toFixed(1)} s`
    : null;

  const hasSummary = result.request_id || result.route || result.domain;

  if (!hasSummary && stepCount === 0) return null;

  return (
    <div className="mt-3 pt-3 border-t border-line">
      {/* Toggle button */}
      <button
        onClick={() => setOpen((v) => !v)}
        className={cn(
          "flex items-center gap-1.5 text-xs text-muted hover:text-fg transition-colors"
        )}
      >
        <Activity className="h-3.5 w-3.5" />
        <span className="font-medium">Execution Trace</span>
        {stepCount > 0 && (
          <span className="text-muted">· {stepCount} steps</span>
        )}
        {durationLabel && (
          <span className="text-muted">· {durationLabel}</span>
        )}
        {open
          ? <ChevronDown className="h-3 w-3" />
          : <ChevronRight className="h-3 w-3" />
        }
      </button>

      {open && (
        <div className="mt-3 space-y-2">
          {/* Step-by-step events */}
          {steps.map((ev, i) => (
            <Step key={`${ev.stage}-${i}`} index={i + 1} event={ev} />
          ))}

          {/* Execution summary table */}
          {hasSummary && (
            <div className="mt-3">
              <p className="text-[10px] uppercase tracking-wider text-muted mb-1.5 font-semibold">
                Execution Summary
              </p>
              <SummaryTable result={result} />
            </div>
          )}
        </div>
      )}
    </div>
  );
}
