import React from "react";
import { ShieldAlert } from "lucide-react";

const STAGE_DESCRIPTIONS: Record<string, { title: string; detail: string }> = {
  input_guardrail: {
    title: "Input blocked by guardrail",
    detail: "The request matched a deterministic security pattern (prompt injection, PII, destructive intent, or toxicity).",
  },
  role_based: {
    title: "Access denied",
    detail: "Your role does not have permission to access this domain. Leadership-only domains require the leadership role.",
  },
  compliance: {
    title: "Blocked by compliance",
    detail: "The semantic compliance review flagged this request as unsafe or outside policy boundaries.",
  },
  approval: {
    title: "Payment not approved",
    detail: "The outbound payment request was denied by the approval gate.",
  },
  api_error: {
    title: "API connection error",
    detail: "Could not reach the mesh API server. Ensure the mesh and API server are both running.",
  },
};

function getDescription(blockStage: string | null) {
  if (!blockStage) {
    return { title: "Request blocked", detail: "This request was blocked by a security gate." };
  }
  const match = Object.keys(STAGE_DESCRIPTIONS).find((k) => blockStage.includes(k));
  return match
    ? STAGE_DESCRIPTIONS[match]
    : { title: `Blocked: ${blockStage.replace(/_/g, " ")}`, detail: "This request was blocked by a security gate." };
}

interface SecurityBadgeProps {
  blockStage: string | null | undefined;
}

export default function SecurityBadge({ blockStage }: SecurityBadgeProps) {
  const { title, detail } = getDescription(blockStage ?? null);

  return (
    <div className="flex items-start gap-2 mb-3">
      <ShieldAlert className="h-4 w-4 text-red-600 dark:text-red-400 flex-shrink-0 mt-0.5" />
      <div>
        <p className="text-sm font-medium text-red-700 dark:text-red-400">{title}</p>
        <p className="text-xs text-red-600/80 dark:text-red-500/80 mt-0.5">{detail}</p>
      </div>
    </div>
  );
}
