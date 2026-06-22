import { CircleCheck, CircleX, Gauge } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Alert } from "@/components/ui/Alert";
import { useEvaluateAnswer } from "@/hooks/useEvaluate";
import { RAGAS_METRIC_DESCRIPTIONS, RAGAS_REFERENCE_FREE_THRESHOLDS } from "@/config/constants";
import { cn, titleCase } from "@/lib/utils";

interface Props {
  question: string;
  answer: string;
  contexts: string[];
}

/**
 * Reference-free quality scoring for the answer the user just received.
 * No ground truth needed — judges faithfulness, answer_relevancy and
 * context_utilization against the question and the retrieved context.
 */
export function AnswerEvaluation({ question, answer, contexts }: Props) {
  const evaluation = useEvaluateAnswer();
  const data = evaluation.data;

  return (
    <div className="rounded-xl border border-line bg-surface p-4 shadow-sm">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-2 text-fg">
          <Gauge className="h-4 w-4" />
          <h3 className="text-sm font-semibold">Answer quality (reference-free)</h3>
        </div>
        <Button
          variant="outline"
          size="md"
          onClick={() => evaluation.mutate({ question, answer, contexts })}
          loading={evaluation.isPending}
          disabled={contexts.length === 0}
        >
          {data ? "Re-evaluate" : "Evaluate this answer"}
        </Button>
      </div>

      {!data && !evaluation.isPending && !evaluation.isError && (
        <p className="mt-2 text-xs text-faint">
          Scores this answer against the question and retrieved context — no gold answer required
          (~10–30s).
        </p>
      )}

      {evaluation.isError && (
        <Alert
          variant={evaluation.error.isNetwork ? "warning" : "error"}
          title="Evaluation failed"
          className="mt-3"
        >
          {evaluation.error.message}
        </Alert>
      )}

      {data && (
        <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-3">
          {Object.entries(data.metrics).map(([metric, result]) => {
            const threshold = RAGAS_REFERENCE_FREE_THRESHOLDS[metric] ?? 0;
            return (
              <div
                key={metric}
                className={cn(
                  "rounded-lg border-l-4 bg-surface-2 p-3",
                  result.pass ? "border-l-emerald-500" : "border-l-red-500",
                )}
              >
                <div className="flex items-center justify-between">
                  <span className="text-xs font-medium uppercase tracking-wide text-muted">
                    {titleCase(metric)}
                  </span>
                  {result.pass ? (
                    <CircleCheck className="h-4 w-4 text-emerald-500" />
                  ) : (
                    <CircleX className="h-4 w-4 text-red-500" />
                  )}
                </div>
                <p
                  className={cn(
                    "mt-1 text-2xl font-semibold tabular-nums",
                    result.pass ? "text-emerald-600 dark:text-emerald-400" : "text-red-600 dark:text-red-400",
                  )}
                >
                  {result.score.toFixed(3)}
                </p>
                <p className="mt-0.5 text-xs text-faint">threshold {threshold}</p>
                <p className="mt-1.5 text-xs leading-relaxed text-muted">
                  {RAGAS_METRIC_DESCRIPTIONS[metric]}
                </p>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
