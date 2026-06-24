import { ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils";

// Maps raw trail stage names to human-readable labels.
const STAGE_LABELS: Record<string, string> = {
  guardrail_pass:    "Guardrail ✓",
  guardrail_block:   "Guardrail ✗",
  rbac_pass:         "RBAC ✓",
  rbac_block:        "RBAC ✗",
  compliance_pass:   "Compliance ✓",
  compliance_failed: "Compliance ✗",
  domain_answer:     "Answer ✓",
  domain_error:      "Answer ✗",
  output_redacted:   "Redacted",
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
}: PipelineTrailProps) {
  if (!trail.length) return null;

  return (
    <div className="flex flex-wrap items-center gap-0.5">
      <span className="text-xs text-muted font-medium mr-1">Pipeline:</span>
      {trail.map((step, i) => {
        const isLast = i === trail.length - 1;
        const isError = isErrorStep(step);

        return (
          <span key={`${step}-${i}`} className="inline-flex items-center gap-0.5">
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
          </span>
        );
      })}
    </div>
  );
}
