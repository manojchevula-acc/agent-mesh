/**
 * TypeScript mirrors of the backend Pydantic models.
 * Keep in sync with `src/gernas_rag/models/*.py` and the API routers.
 */

// ── Document types (src/gernas_rag/models/chunk.py: DocumentType) ──────────────
export type DocumentType =
  | "pricing_policy"
  | "regulatory"
  | "mrm"
  | "product_manual"
  | "risk_policy"
  | "other";

// ── Retrieval (models/retrieval.py) ───────────────────────────────────────────
export interface DocumentFilter {
  document_type?: string[] | null;
  product_applicability?: string[] | null;
  effective_date_from?: string | null;
  deprecated?: boolean;
}

export interface RetrieveRequest {
  query: string;
  filters?: DocumentFilter;
  top_k?: number;
  include_parent?: boolean;
  generate_answer?: boolean;
}

export interface RetrievedChunk {
  text: string;
  source: string;
  clause_reference: string;
  score: number;
  effective_date: string;
  freshness_warning: boolean;
  parent_text?: string | null;
}

export interface RetrieveResponse {
  chunks: RetrievedChunk[];
  total_results: number;
  latency_ms: number;
  freshness_warning_global: boolean;
  answer?: string | null;
  cache_hit: boolean;
}

// ── Ingestion (api/routers/ingest.py) ─────────────────────────────────────────
// Mirrors IngestionStatus in models/ingestion.py (note: success, not "completed").
export type JobStatus = "pending" | "running" | "success" | "error";

export interface IngestAccepted {
  job_id: string;
  status: "accepted";
}

export interface IngestJobStatus {
  job_id: string;
  status: JobStatus;
  chunks_created?: number;
  error?: string | null;
}

export interface IngestParams {
  file: File;
  documentType: string; // "" => auto-detect from filename
  productApplicability: string; // comma-separated
  effectiveDate: string; // YYYY-MM-DD or ""
}

// ── Evaluation (api/routers/evaluate.py + evaluator) ──────────────────────────
export interface EvaluationMetric {
  score: number;
  pass: boolean;
}

export interface EvaluationPerQuestion {
  question: string;
  ground_truth?: string | null; // absent in reference-free runs
  answer?: string;
  chunks_retrieved?: number;
  sources?: string[];
}

export interface EvaluationResult {
  metrics: Record<string, EvaluationMetric>;
  per_question: EvaluationPerQuestion[];
  all_pass: boolean;
  reference_free?: boolean;
  test_cases_count?: number;
  top_k?: number;
}

// Reference-free scoring of a single, already-generated answer (no ground truth).
export interface EvaluateAnswerRequest {
  question: string;
  answer: string;
  contexts: string[];
}

export interface AnswerEvaluationResult {
  metrics: Record<string, EvaluationMetric>;
  reference_free: boolean;
  all_pass: boolean;
}

export interface TestCase {
  question: string;
  ground_truth: string;
}

export interface TestCasesResponse {
  test_cases: TestCase[];
  count: number;
}

// ── Health / admin ────────────────────────────────────────────────────────────
export interface HealthResponse {
  status: string;
}

export interface ReadyResponse {
  status: "ready" | "degraded";
  vectordb: boolean;
}

export interface AdminResponse {
  status: string;
  collection: string;
}
