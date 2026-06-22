import { useMutation, useQuery } from "@tanstack/react-query";
import { evaluateAnswer, getTestCases, runEvaluation } from "@/api/evaluate";
import type { RunEvaluationOptions } from "@/api/evaluate";
import type { ApiError } from "@/lib/apiClient";
import { queryKeys } from "@/lib/queryClient";
import type {
  AnswerEvaluationResult,
  EvaluateAnswerRequest,
  EvaluationResult,
  TestCasesResponse,
} from "@/types/api";

export function useTestCases(enabled = true) {
  return useQuery<TestCasesResponse, ApiError>({
    queryKey: queryKeys.testCases,
    queryFn: getTestCases,
    enabled,
    retry: 0,
    staleTime: Infinity,
  });
}

export function useRunEvaluation() {
  return useMutation<EvaluationResult, ApiError, RunEvaluationOptions>({
    mutationFn: (options = {}) => runEvaluation(options),
  });
}

/** Reference-free scoring of a single answer the user just received. */
export function useEvaluateAnswer() {
  return useMutation<AnswerEvaluationResult, ApiError, EvaluateAnswerRequest>({
    mutationFn: evaluateAnswer,
  });
}
