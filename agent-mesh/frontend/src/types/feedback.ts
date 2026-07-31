export type DimensionRating = "good" | "partial" | "poor";

export type IntentCode =
  | "wrong_intent"
  | "missing_context"
  | "wrong_product"
  | "wrong_customer"
  | "unclarified";

export type WorkflowCode =
  | "steps_out_of_order"
  | "check_skipped"
  | "check_repeated"
  | "check_too_late";

export type ToolsCode =
  | "wrong_tool"
  | "evidence_missing"
  | "evidence_stale"
  | "evidence_irrelevant"
  | "evidence_contradicted";

export type PolicyCode =
  | "policy_check_missed"
  | "wrong_risk_class"
  | "guardrail_not_applied"
  | "escalation_wrong";

export type OutputCode =
  | "incomplete"
  | "incorrect"
  | "unclear_explanation"
  | "not_grounded"
  | "not_actionable";

export type CorrectionCode =
  | "user_corrected"
  | "user_rejected"
  | "rework_requested"
  | "value_changed";

export type EffortCode =
  | "user_repeated_info"
  | "user_abandoned"
  | "user_escalated"
  | "user_restarted";

export interface DimensionFeedback<C extends string = string> {
  rating?: DimensionRating;
  codes?: C[];
  note?: string;
}

export interface CorrectionDimensionFeedback extends DimensionFeedback<CorrectionCode> {
  correction_text?: string;
}

export interface StructuredFeedbackDimensions {
  intent?: DimensionFeedback<IntentCode>;
  workflow?: DimensionFeedback<WorkflowCode>;
  tools?: DimensionFeedback<ToolsCode>;
  policy?: DimensionFeedback<PolicyCode>;
  output?: DimensionFeedback<OutputCode>;
  correction?: CorrectionDimensionFeedback;
  effort?: DimensionFeedback<EffortCode>;
}

export interface StructuredFeedbackRequest {
  request_id: string;
  session_id: string;
  user: string;
  role?: string;
  rating?: string;
  comment?: string;
  query?: string;
  answer?: string;
  route?: string;
  blocked?: boolean;
  dimensions: StructuredFeedbackDimensions;
}

export interface StructuredFeedbackResponse {
  success: boolean;
  structured_feedback_id: string;
}
