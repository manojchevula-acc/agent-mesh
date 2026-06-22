import type { DocumentType } from "@/types/api";

/** Document-type filter options for the Search page. `null` => all documents. */
export const DOC_TYPE_OPTIONS: { label: string; value: DocumentType | null }[] = [
  { label: "All documents", value: null },
  { label: "Pricing Policy", value: "pricing_policy" },
  { label: "Regulatory (CBUAE)", value: "regulatory" },
  { label: "Model Risk (MRM)", value: "mrm" },
  { label: "Risk Policy", value: "risk_policy" },
  { label: "Product Manual", value: "product_manual" },
];

/** Document-type options for the Upload page (includes auto-detect + other). */
export const UPLOAD_DOC_TYPES: { label: string; value: string }[] = [
  { label: "Auto-detect from filename", value: "" },
  { label: "Pricing Policy", value: "pricing_policy" },
  { label: "Regulatory (CBUAE)", value: "regulatory" },
  { label: "Model Risk (MRM)", value: "mrm" },
  { label: "Risk Policy", value: "risk_policy" },
  { label: "Product Manual", value: "product_manual" },
  { label: "Other", value: "other" },
];

/** Human labels for document_type values (used on chunk badges). */
export const DOC_TYPE_LABELS: Record<string, string> = {
  pricing_policy: "Pricing Policy",
  regulatory: "Regulatory",
  mrm: "Model Risk",
  product_manual: "Product Manual",
  risk_policy: "Risk Policy",
  other: "Other",
};

export interface SampleCategory {
  category: string;
  questions: string[];
}

/** Sample questions shown in the sidebar, mirroring the Streamlit UI. */
export const SAMPLE_QUESTIONS: SampleCategory[] = [
  {
    category: "Pricing Policy",
    questions: [
      "What is the pricing floor for BB-rated AED corporate loans?",
      "What are the interest rate components for term loans?",
      "How is credit spread determined for different risk ratings?",
    ],
  },
  {
    category: "Regulatory / CBUAE",
    questions: [
      "What are the AI governance requirements under the CBUAE circular?",
      "When must a bank notify CBUAE about an AI model incident?",
      "What oversight controls are required for AI models in credit decisions?",
    ],
  },
  {
    category: "Model Risk",
    questions: [
      "What constitutes a model incident and what is the reporting deadline?",
      "What are the model validation requirements in the MRM framework?",
      "How should model risk be escalated to senior management?",
    ],
  },
  {
    category: "Concentration Limits",
    questions: [
      "What are the credit concentration limits for corporate counterparties?",
      "What triggers a breach of the concentration limit policy?",
    ],
  },
  {
    category: "Product Manual",
    questions: [
      "What are the eligibility criteria for a corporate term loan?",
      "What documentation is required to apply for a term loan?",
    ],
  },
];

/** RAGAS pass/fail thresholds — full mode (requires ground truth). */
export const RAGAS_THRESHOLDS: Record<string, number> = {
  faithfulness: 0.85,
  answer_relevancy: 0.8,
  context_precision: 0.75,
  context_recall: 0.8,
};

/**
 * Reference-free thresholds — no ground truth needed. Swaps context_precision
 * for context_utilization and drops context_recall (mirrors REFERENCE_FREE_THRESHOLDS
 * in src/gernas_rag/evaluation/metrics.py).
 */
export const RAGAS_REFERENCE_FREE_THRESHOLDS: Record<string, number> = {
  faithfulness: 0.85,
  answer_relevancy: 0.8,
  context_utilization: 0.75,
};

export const RAGAS_METRIC_DESCRIPTIONS: Record<string, string> = {
  faithfulness: "Is the answer grounded in retrieved chunks? (no hallucination)",
  answer_relevancy: "Does the answer actually address the question asked?",
  context_precision: "Are the retrieved chunks relevant? (no noisy results)",
  context_recall: "Did retrieval find all chunks needed to answer fully?",
  context_utilization: "Are the retrieved chunks relevant & used? (no ground truth needed)",
};

export const TOP_K_MIN = 1;
export const TOP_K_MAX = 10;
export const TOP_K_DEFAULT = 5;
