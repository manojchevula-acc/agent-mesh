import { useEffect, useState } from "react";
import {
  Target,
  GitBranch,
  Wrench,
  Shield,
  FileText,
  PenLine,
  Clock,
  X,
  CheckCircle2,
  AlertCircle,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/Button";
import type {
  DimensionRating,
  StructuredFeedbackDimensions,
  CorrectionDimensionFeedback,
  DimensionFeedback,
} from "@/types/feedback";

// ─── Dimension config ─────────────────────────────────────────────────────────

interface DimensionConfig {
  key: keyof StructuredFeedbackDimensions;
  label: string;
  icon: React.ElementType;
  description: string;
  codes: readonly string[];
  codeLabels: Record<string, string>;
  hasCorrectionText?: boolean;
}

const DIMENSIONS: DimensionConfig[] = [
  {
    key: "intent",
    label: "Intent Capture",
    icon: Target,
    description: "Was the request understood correctly?",
    codes: ["wrong_intent", "missing_context", "wrong_product", "wrong_customer", "unclarified"],
    codeLabels: {
      wrong_intent: "Wrong intent",
      missing_context: "Missing context",
      wrong_product: "Wrong product",
      wrong_customer: "Wrong customer",
      unclarified: "Not clarified",
    },
  },
  {
    key: "workflow",
    label: "Workflow Correctness",
    icon: GitBranch,
    description: "Were steps performed in the right order?",
    codes: ["steps_out_of_order", "check_skipped", "check_repeated", "check_too_late"],
    codeLabels: {
      steps_out_of_order: "Steps out of order",
      check_skipped: "Check skipped",
      check_repeated: "Check repeated",
      check_too_late: "Check too late",
    },
  },
  {
    key: "tools",
    label: "Tool & Evidence Usage",
    icon: Wrench,
    description: "Were the right tools and sources used?",
    codes: ["wrong_tool", "evidence_missing", "evidence_stale", "evidence_irrelevant", "evidence_contradicted"],
    codeLabels: {
      wrong_tool: "Wrong tool",
      evidence_missing: "Evidence missing",
      evidence_stale: "Evidence stale",
      evidence_irrelevant: "Evidence irrelevant",
      evidence_contradicted: "Evidence contradicted",
    },
  },
  {
    key: "policy",
    label: "Policy & Risk Handling",
    icon: Shield,
    description: "Were guardrails and approvals applied correctly?",
    codes: ["policy_check_missed", "wrong_risk_class", "guardrail_not_applied", "escalation_wrong"],
    codeLabels: {
      policy_check_missed: "Policy check missed",
      wrong_risk_class: "Wrong risk class",
      guardrail_not_applied: "Guardrail not applied",
      escalation_wrong: "Escalation wrong",
    },
  },
  {
    key: "output",
    label: "Output Quality",
    icon: FileText,
    description: "Was the answer complete, correct, and grounded?",
    codes: ["incomplete", "incorrect", "unclear_explanation", "not_grounded", "not_actionable"],
    codeLabels: {
      incomplete: "Incomplete",
      incorrect: "Incorrect",
      unclear_explanation: "Unclear explanation",
      not_grounded: "Not grounded",
      not_actionable: "Not actionable",
    },
  },
  {
    key: "correction",
    label: "Correction / Override",
    icon: PenLine,
    description: "Did a reviewer correct, reject, or request rework?",
    codes: ["user_corrected", "user_rejected", "rework_requested", "value_changed"],
    codeLabels: {
      user_corrected: "User corrected",
      user_rejected: "User rejected",
      rework_requested: "Rework requested",
      value_changed: "Value changed",
    },
    hasCorrectionText: true,
  },
  {
    key: "effort",
    label: "Effort & Outcome",
    icon: Clock,
    description: "Did the user repeat, abandon, escalate, or restart?",
    codes: ["user_repeated_info", "user_abandoned", "user_escalated", "user_restarted"],
    codeLabels: {
      user_repeated_info: "Repeated information",
      user_abandoned: "Abandoned",
      user_escalated: "Escalated",
      user_restarted: "Restarted",
    },
  },
];

// ─── Per-dimension state ──────────────────────────────────────────────────────

interface DimensionState {
  rating?: DimensionRating;
  codes: Set<string>;
  note: string;
  correction_text: string;
}

type AllDimensionState = Record<keyof StructuredFeedbackDimensions, DimensionState>;
type ModalPhase = "editing" | "submitting" | "success" | "error";

function makeInitialState(): AllDimensionState {
  const blank = (): DimensionState => ({ rating: undefined, codes: new Set(), note: "", correction_text: "" });
  return {
    intent: blank(),
    workflow: blank(),
    tools: blank(),
    policy: blank(),
    output: blank(),
    correction: blank(),
    effort: blank(),
  };
}

function buildPayload(dims: AllDimensionState): StructuredFeedbackDimensions {
  const out: StructuredFeedbackDimensions = {};
  for (const { key } of DIMENSIONS) {
    const d = dims[key];
    if (d.rating === undefined) continue;
    const dim: DimensionFeedback & { correction_text?: string } = {
      rating: d.rating,
      ...(d.codes.size > 0 ? { codes: [...d.codes] } : {}),
      ...(d.note.trim() ? { note: d.note.trim() } : {}),
    };
    if (key === "correction" && d.correction_text.trim()) {
      (dim as CorrectionDimensionFeedback).correction_text = d.correction_text.trim();
    }
    (out as Record<string, unknown>)[key] = dim;
  }
  return out;
}

// ─── RatingPill ───────────────────────────────────────────────────────────────

const RATING_STYLES: Record<DimensionRating, { idle: string; active: string }> = {
  good: {
    idle: "border border-emerald-200 text-emerald-600 hover:bg-emerald-50 dark:border-emerald-800 dark:text-emerald-400 dark:hover:bg-emerald-900/20",
    active: "bg-emerald-100 border border-emerald-400 text-emerald-700 ring-1 ring-emerald-300 dark:bg-emerald-900/40 dark:border-emerald-500 dark:text-emerald-300 dark:ring-emerald-600",
  },
  partial: {
    idle: "border border-amber-200 text-amber-600 hover:bg-amber-50 dark:border-amber-800 dark:text-amber-400 dark:hover:bg-amber-900/20",
    active: "bg-amber-100 border border-amber-400 text-amber-700 ring-1 ring-amber-300 dark:bg-amber-900/40 dark:border-amber-500 dark:text-amber-300 dark:ring-amber-600",
  },
  poor: {
    idle: "border border-red-200 text-red-600 hover:bg-red-50 dark:border-red-800 dark:text-red-400 dark:hover:bg-red-900/20",
    active: "bg-red-100 border border-red-400 text-red-700 ring-1 ring-red-300 dark:bg-red-900/40 dark:border-red-500 dark:text-red-300 dark:ring-red-600",
  },
};

const RATING_LABELS: Record<DimensionRating, string> = { good: "Good", partial: "Partial", poor: "Poor" };

function RatingPill({
  value,
  selected,
  onClick,
}: {
  value: DimensionRating;
  selected: boolean;
  onClick: () => void;
}) {
  const s = RATING_STYLES[value];
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "px-2.5 py-1 rounded-full text-xs font-medium transition-all",
        selected ? s.active : s.idle
      )}
    >
      {RATING_LABELS[value]}
    </button>
  );
}

// ─── ReasonChip ───────────────────────────────────────────────────────────────

function ReasonChip({
  label,
  selected,
  tone,
  onClick,
}: {
  label: string;
  selected: boolean;
  tone: "amber" | "red";
  onClick: () => void;
}) {
  const activeClass =
    tone === "red"
      ? "bg-red-100 border-red-400 text-red-700 dark:bg-red-900/40 dark:border-red-500 dark:text-red-300"
      : "bg-amber-100 border-amber-400 text-amber-700 dark:bg-amber-900/40 dark:border-amber-500 dark:text-amber-300";
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium border transition-all",
        selected
          ? activeClass
          : "border-line text-muted hover:border-brand-400 hover:text-brand-600 dark:hover:text-brand-400"
      )}
    >
      {label}
    </button>
  );
}

// ─── DimensionCard ────────────────────────────────────────────────────────────

function DimensionCard({
  config,
  state,
  onChange,
}: {
  config: DimensionConfig;
  state: DimensionState;
  onChange: (patch: Partial<DimensionState>) => void;
}) {
  const showDetails = state.rating === "partial" || state.rating === "poor";
  const Icon = config.icon;

  return (
    <div className="rounded-lg border border-line bg-surface-2 p-3.5 space-y-3">
      <div className="flex items-start gap-2.5">
        <Icon className="h-4 w-4 text-muted shrink-0 mt-0.5" />
        <div className="flex-1 min-w-0">
          <span className="text-sm font-medium text-fg">{config.label}</span>
          <span className="text-xs text-muted ml-2">{config.description}</span>
        </div>
      </div>

      <div className="flex items-center gap-2">
        {(["good", "partial", "poor"] as DimensionRating[]).map((r) => (
          <RatingPill
            key={r}
            value={r}
            selected={state.rating === r}
            onClick={() =>
              onChange({
                rating: state.rating === r ? undefined : r,
                codes: r === "good" ? new Set() : state.codes,
              })
            }
          />
        ))}
      </div>

      {showDetails && (
        <div className="space-y-2.5 pt-1 border-t border-line/60 animate-fade-in">
          <div className="flex flex-wrap gap-1.5">
            {config.codes.map((code) => (
              <ReasonChip
                key={code}
                label={config.codeLabels[code]}
                selected={state.codes.has(code)}
                tone={state.rating === "poor" ? "red" : "amber"}
                onClick={() => {
                  const next = new Set(state.codes);
                  next.has(code) ? next.delete(code) : next.add(code);
                  onChange({ codes: next });
                }}
              />
            ))}
          </div>
          <input
            type="text"
            value={state.note}
            onChange={(e) => onChange({ note: e.target.value })}
            placeholder="Add a note (optional)"
            className="w-full rounded-md border border-line bg-canvas px-2.5 py-1.5 text-xs text-fg placeholder:text-faint focus:outline-none focus:ring-1 focus:ring-brand-500"
          />
          {config.hasCorrectionText && (
            <input
              type="text"
              value={state.correction_text}
              onChange={(e) => onChange({ correction_text: e.target.value })}
              placeholder="Enter the correct value (optional)"
              className="w-full rounded-md border border-line bg-canvas px-2.5 py-1.5 text-xs text-fg placeholder:text-faint focus:outline-none focus:ring-1 focus:ring-brand-500"
            />
          )}
        </div>
      )}
    </div>
  );
}

// ─── SuccessState ─────────────────────────────────────────────────────────────

function SuccessState({ onClose }: { onClose: () => void }) {
  return (
    <div className="px-5 py-10 flex flex-col items-center gap-3 text-center">
      <div className="h-12 w-12 rounded-full bg-emerald-100 dark:bg-emerald-900/30 flex items-center justify-center">
        <CheckCircle2 className="h-6 w-6 text-emerald-500" />
      </div>
      <p className="text-base font-semibold text-fg">Feedback submitted</p>
      <p className="text-sm text-muted">Thank you — this helps improve the assistant.</p>
      <button
        type="button"
        onClick={onClose}
        className="mt-2 text-sm text-brand-600 dark:text-brand-400 hover:underline"
      >
        Close
      </button>
    </div>
  );
}

// ─── Main modal ───────────────────────────────────────────────────────────────

export interface StructuredFeedbackModalProps {
  open: boolean;
  requestId: string;
  sessionId: string;
  user: string;
  onClose: () => void;
  onSubmit: (dims: StructuredFeedbackDimensions) => Promise<void>;
}

export function StructuredFeedbackModal({
  open,
  requestId,
  sessionId: _sessionId,
  onClose,
  onSubmit,
}: StructuredFeedbackModalProps) {
  const [dims, setDims] = useState<AllDimensionState>(makeInitialState);
  const [phase, setPhase] = useState<ModalPhase>("editing");

  useEffect(() => {
    if (open) {
      setDims(makeInitialState());
      setPhase("editing");
    }
  }, [open, requestId]);

  // Close on Escape
  useEffect(() => {
    if (!open || phase === "submitting") return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, phase, onClose]);

  if (!open) return null;

  const ratedCount = DIMENSIONS.filter((d) => dims[d.key].rating !== undefined).length;

  async function handleSubmit() {
    setPhase("submitting");
    try {
      await onSubmit(buildPayload(dims));
      setPhase("success");
    } catch {
      setPhase("error");
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center p-4 pt-[5vh] overflow-y-auto">
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-slate-900/60 backdrop-blur-sm"
        onClick={phase !== "submitting" ? onClose : undefined}
        aria-hidden="true"
      />

      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="sfb-title"
        className="relative w-full max-w-[640px] animate-scale-in rounded-xl border border-line bg-surface shadow-2xl my-auto"
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-line">
          <div>
            <h3 id="sfb-title" className="text-base font-semibold text-fg">
              Submit Detailed Feedback
            </h3>
            <p className="text-xs text-muted mt-0.5">
              {ratedCount === 0
                ? "Rate any dimensions that apply — all optional"
                : `${ratedCount} of 7 rated`}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={phase === "submitting"}
            aria-label="Close"
            className="p-1.5 rounded-lg text-muted hover:text-fg hover:bg-surface-2 transition-colors disabled:opacity-50"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Body */}
        {phase === "success" ? (
          <SuccessState onClose={onClose} />
        ) : (
          <>
            <div className="px-5 py-4 space-y-3 max-h-[65vh] overflow-y-auto">
              {DIMENSIONS.map((config) => (
                <DimensionCard
                  key={config.key}
                  config={config}
                  state={dims[config.key]}
                  onChange={(patch) =>
                    setDims((prev) => ({
                      ...prev,
                      [config.key]: { ...prev[config.key], ...patch },
                    }))
                  }
                />
              ))}
            </div>

            {/* Error banner */}
            {phase === "error" && (
              <div className="mx-5 mb-3 flex items-center gap-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 dark:border-red-800 dark:bg-red-900/20">
                <AlertCircle className="h-4 w-4 shrink-0 text-red-500" />
                <span className="text-xs text-red-700 dark:text-red-400">
                  Failed to submit — please try again.
                </span>
              </div>
            )}

            {/* Footer */}
            <div className="flex items-center justify-between px-5 py-3 border-t border-line bg-surface-2/50 rounded-b-xl">
              <span className="text-xs text-faint font-mono truncate max-w-[180px]" title={requestId}>
                {requestId}
              </span>
              <div className="flex gap-2 shrink-0">
                <button
                  type="button"
                  onClick={onClose}
                  disabled={phase === "submitting"}
                  className="text-sm px-3 py-1.5 rounded-lg border border-line text-muted hover:text-fg hover:bg-surface-2 transition-colors disabled:opacity-50"
                >
                  Cancel
                </button>
                <Button
                  variant="primary"
                  size="sm"
                  loading={phase === "submitting"}
                  disabled={ratedCount === 0 || phase === "submitting"}
                  onClick={handleSubmit}
                >
                  Submit Detailed Form
                </Button>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
