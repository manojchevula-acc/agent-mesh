import { apiClient, toApiError } from "@/lib/apiClient";
import type {
  AnswerEvaluationResult,
  EvaluateAnswerRequest,
  EvaluationResult,
  TestCasesResponse,
} from "@/types/api";

/**
 * POST /api/v1/evaluate — run RAGAS over the FAB test set (5–10 min on CPU).
 *
 * When `referenceFree` is true, evaluation runs without ground truth
 * (faithfulness, answer_relevancy, context_utilization).
 *
 * `limit` caps how many test cases run; `topK` overrides how many chunks are
 * retrieved per question.
 */
export interface RunEvaluationOptions {
  referenceFree?: boolean;
  limit?: number;
  topK?: number;
}

export async function runEvaluation(
  options: RunEvaluationOptions = {},
): Promise<EvaluationResult> {
  const { referenceFree = false, limit, topK } = options;
  try {
    const { data } = await apiClient.post<EvaluationResult>(
      "/api/v1/evaluate",
      undefined,
      {
        params: {
          reference_free: referenceFree,
          ...(limit != null ? { limit } : {}),
          ...(topK != null ? { top_k: topK } : {}),
        },
        timeout: 900_000, // 15 min cap
      },
    );
    return data;
  } catch (err) {
    throw toApiError(err);
  }
}

/**
 * POST /api/v1/evaluate/answer — reference-free score for a single answer
 * the user just received (no ground truth). Typically 10–30s.
 */
export async function evaluateAnswer(
  body: EvaluateAnswerRequest,
): Promise<AnswerEvaluationResult> {
  try {
    const { data } = await apiClient.post<AnswerEvaluationResult>(
      "/api/v1/evaluate/answer",
      body,
      { timeout: 300_000 }, // 5 min — the free-tier judge is slow over full context
    );
    return data;
  } catch (err) {
    throw toApiError(err);
  }
}

/** GET /api/v1/evaluate/test-cases — the 7 FAB test questions + ground truths. */
export async function getTestCases(): Promise<TestCasesResponse> {
  try {
    const { data } = await apiClient.get<TestCasesResponse>("/api/v1/evaluate/test-cases", {
      timeout: 15_000,
    });
    return data;
  } catch (err) {
    throw toApiError(err);
  }
}
