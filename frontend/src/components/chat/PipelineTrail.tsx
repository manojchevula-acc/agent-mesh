import React from "react";
import { ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils";

// Maps raw trail stage names to human-readable labels.
const STAGE_LABELS: Record<string, string> = {
  guardrail_pass: "Guardrail ✓",
  guardrail_block: "Guardrail ✗",
  router: "Router",
  access_ok: "Access ✓",
  access_denied: "Access ✗",
  compliance_pass: "Compliance ✓",
  compliance_failed: "Compliance ✗",
  payment_approved: "Payment ✓",
  payment_denied: "Payment ✗",
  domain_answer: "Answer",
  output_redacted: "Redacted",
};

function labelFor(step: string): string {
  // Try exact match first, then prefix match, then title-case the raw value.
  if (STAGE_LABELS[step]) return STAGE_LABELS[step];
  const prefix = Object.keys(STAGE_LABELS).find((k) => step.startsWith(k));
  if (prefix) return STAGE_LABELS[prefix];
  return step.replace(/_/g, " ");
}

function isErrorStep(step: string): boolean {
  return (
    step.includes("block") ||
    step.includes("denied") ||
    step.includes("failed")
  );
}

interface PipelineTrailProps {
  trail: string[];
  blocked?: boolean;
  blockStage?: string | null;
}

export default function PipelineTrail({
  trail,
  blocked = false,
  blockStage,
}: PipelineTrailProps) {
  if (!trail.length) return null;

  return (
    <div className="flex flex-wrap items-center gap-0.5">
      <span className="text-xs text-muted font-medium mr-1">Pipeline:</span>
      {trail.map((step, i) => {
        const isLast = i === trail.length - 1;
        const isError = isErrorStep(step);

        return (
          <React.Fragment key={`${step}-${i}`}>
            <span
              className={cn(
                "inline-flex items-center text-xs px-1.5 py-0.5 rounded",
                isError
                  ? "bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-400"
                  : "bg-canvas text-muted"
              )}
            >
              {labelFor(step)}
            </span>
            {!isLast && (
              <ChevronRight className="h-3 w-3 text-faint flex-shrink-0" />
            )}
          </React.Fragment>
        );
      })}
    </div>
  );
}
