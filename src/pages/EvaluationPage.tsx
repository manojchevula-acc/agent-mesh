import { ChevronDown, CircleCheck, CircleX, Play } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/Button";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Metric } from "@/components/ui/Metric";
import { CenteredSpinner } from "@/components/ui/Spinner";
import { EmptyState } from "@/components/ui/EmptyState";
import { useRunEvaluation, useTestCases } from "@/hooks/useEvaluate";
import {
  RAGAS_METRIC_DESCRIPTIONS,
  RAGAS_REFERENCE_FREE_THRESHOLDS,
  RAGAS_THRESHOLDS,
} from "@/config/constants";
import { cn, titleCase } from "@/lib/utils";

export function EvaluationPage() {
  const testCases = useTestCases();
  const evaluation = useRunEvaluation();
  const data = evaluation.data;
  const [showCases, setShowCases] = useState(false);
  const [openQ, setOpenQ] = useState<number | null>(null);
  const [referenceFree, setReferenceFree] = useState(false);
  const [numTests, setNumTests] = useState<number | "">("");
  const [topK, setTopK] = useState<number>(3);

  const totalCases = testCases.data?.count;

  // Thresholds shown in the preview follow the selected mode; once results are
  // in, follow whatever mode the backend actually ran (data.reference_free).
  const resultReferenceFree = data?.reference_free ?? referenceFree;
  const previewThresholds = referenceFree ? RAGAS_REFERENCE_FREE_THRESHOLDS : RAGAS_THRESHOLDS;
  const resultThresholds = resultReferenceFree
    ? RAGAS_REFERENCE_FREE_THRESHOLDS
    : RAGAS_THRESHOLDS;

  return (
    <div className="space-y-6">
      {/* Intro + thresholds */}
      <Card>
        <CardHeader
          title="RAGAS evaluation"
          subtitle="Runs the FAB test set through the full pipeline and scores it with RAGAS. Takes 5–10 min on CPU."
        />
        <CardBody className="space-y-5">
          {/* Mode toggle */}
          <div className="inline-flex rounded-lg border border-line p-1 text-sm">
            <button
              onClick={() => setReferenceFree(false)}
              className={cn(
                "rounded-md px-3 py-1.5 font-medium transition-colors",
                !referenceFree ? "bg-brand-600 text-white" : "text-muted hover:bg-surface-2",
              )}
            >
              Full (with ground truth)
            </button>
            <button
              onClick={() => setReferenceFree(true)}
              className={cn(
                "rounded-md px-3 py-1.5 font-medium transition-colors",
                referenceFree ? "bg-brand-600 text-white" : "text-muted hover:bg-surface-2",
              )}
            >
              Reference-free (no ground truth)
            </button>
          </div>
          <p className="text-xs text-muted">
            {referenceFree
              ? "Judges the answer against the question and retrieved context only — no gold answer required. Runs faithfulness, answer_relevancy and context_utilization."
              : "Compares answers and retrieved context against gold answers. Runs faithfulness, answer_relevancy, context_precision and context_recall."}
          </p>

          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            {Object.entries(previewThresholds).map(([metric, threshold]) => (
              <Metric
                key={metric}
                label={titleCase(metric)}
                value={`≥ ${Math.round(threshold * 100)}%`}
                hint="pass threshold"
              />
            ))}
          </div>

          <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
            <div className="grid grid-cols-2 gap-3 sm:max-w-md">
              <label className="flex flex-col gap-1 text-sm">
                <span className="font-medium text-fg">Number of tests</span>
                <input
                  type="number"
                  min={1}
                  max={totalCases}
                  value={numTests}
                  onChange={(e) => {
                    const v = e.target.value;
                    if (v === "") return setNumTests("");
                    const n = Math.max(1, Math.min(totalCases ?? Infinity, Number(v)));
                    setNumTests(Number.isNaN(n) ? "" : n);
                  }}
                  placeholder={totalCases ? `All (${totalCases})` : "All"}
                  className="rounded-md border border-line bg-surface px-3 py-1.5 text-fg outline-none focus:border-brand-500"
                />
                <span className="text-xs text-faint">
                  Leave empty to run {totalCases ? `all ${totalCases}` : "all"} cases.
                </span>
              </label>
              <label className="flex flex-col gap-1 text-sm">
                <span className="font-medium text-fg">Chunks per question</span>
                <input
                  type="number"
                  min={1}
                  max={20}
                  value={topK}
                  onChange={(e) => {
                    const n = Math.max(1, Math.min(20, Number(e.target.value)));
                    setTopK(Number.isNaN(n) ? 1 : n);
                  }}
                  className="rounded-md border border-line bg-surface px-3 py-1.5 text-fg outline-none focus:border-brand-500"
                />
                <span className="text-xs text-faint">Top-k retrieved &amp; tested.</span>
              </label>
            </div>
            <Button
              onClick={() =>
                evaluation.mutate({
                  referenceFree,
                  limit: numTests === "" ? undefined : numTests,
                  topK,
                })
              }
              loading={evaluation.isPending}
            >
              <Play className="h-4 w-4" /> Run evaluation
            </Button>
          </div>
          <p className="text-sm text-muted">
            Uses Groq as the RAGAS judge LLM. Make sure the backend is running.
          </p>

          {/* Test cases */}
          <div className="rounded-lg border border-line">
            <button
              onClick={() => setShowCases((s) => !s)}
              className="flex w-full items-center justify-between px-4 py-2.5 text-sm font-medium text-fg transition-colors hover:bg-surface-2"
            >
              View test cases {testCases.data ? `(${testCases.data.count})` : ""}
              <ChevronDown className={cn("h-4 w-4 transition-transform", showCases && "rotate-180")} />
            </button>
            {showCases && (
              <div className="animate-fade-in border-t border-line px-4 py-3">
                {testCases.isLoading && <p className="text-sm text-faint">Loading…</p>}
                {testCases.isError && (
                  <p className="text-sm text-faint">Start the API server to load test cases.</p>
                )}
                <ul className="space-y-3">
                  {testCases.data?.test_cases.map((tc, i) => (
                    <li key={i} className="text-sm">
                      <p className="font-medium text-fg">
                        {i + 1}. {tc.question}
                      </p>
                      <p className="mt-0.5 text-xs text-muted">Ground truth: {tc.ground_truth}</p>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        </CardBody>
      </Card>

      {/* Error */}
      {evaluation.isError && (
        <Alert variant={evaluation.error.isNetwork ? "warning" : "error"} title="Evaluation failed">
          {evaluation.error.message}
        </Alert>
      )}

      {/* Running */}
      {evaluation.isPending && (
        <Card>
          <CardBody>
            <CenteredSpinner label="Running RAGAS evaluation — this can take several minutes…" />
          </CardBody>
        </Card>
      )}

      {/* Results */}
      {!evaluation.isPending && data && (
        <div className="space-y-6">
          {data.all_pass ? (
            <Alert variant="success" title="All metrics pass">
              The system meets every quality threshold.
            </Alert>
          ) : (
            <Alert variant="error" title="Some metrics fail">
              {Object.entries(data.metrics)
                .filter(([, m]) => !m.pass)
                .map(([k]) => titleCase(k))
                .join(", ")}{" "}
              below threshold.
            </Alert>
          )}

          {/* Metric scorecards */}
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {Object.entries(data.metrics).map(([metric, result]) => {
              const threshold = resultThresholds[metric] ?? 0;
              return (
                <div
                  key={metric}
                  className={cn(
                    "rounded-xl border-l-4 bg-surface p-4 shadow-sm",
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
                      "mt-1 text-3xl font-semibold tabular-nums",
                      result.pass ? "text-emerald-600 dark:text-emerald-400" : "text-red-600 dark:text-red-400",
                    )}
                  >
                    {result.score.toFixed(3)}
                  </p>
                  <p className="mt-1 text-xs text-faint">
                    threshold {threshold} ·{" "}
                    <span className={result.pass ? "text-emerald-600 dark:text-emerald-400" : "text-red-600 dark:text-red-400"}>
                      {result.score - threshold >= 0 ? "+" : ""}
                      {(result.score - threshold).toFixed(3)}
                    </span>
                  </p>
                  <p className="mt-2 text-xs leading-relaxed text-muted">
                    {RAGAS_METRIC_DESCRIPTIONS[metric]}
                  </p>
                </div>
              );
            })}
          </div>

          {/* Per-question */}
          {data.per_question.length > 0 && (
            <Card>
              <CardHeader
                title={`Per-question results (${data.test_cases_count ?? data.per_question.length})`}
              />
              <CardBody className="space-y-2">
                {data.per_question.map((qa, i) => {
                  const open = openQ === i;
                  return (
                    <div key={i} className="overflow-hidden rounded-lg border border-line">
                      <button
                        onClick={() => setOpenQ(open ? null : i)}
                        className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left text-sm font-medium text-fg transition-colors hover:bg-surface-2"
                      >
                        <span className="min-w-0 truncate">
                          Q{i + 1}: {qa.question}
                        </span>
                        <ChevronDown className={cn("h-4 w-4 shrink-0 text-faint transition-transform", open && "rotate-180")} />
                      </button>
                      {open && (
                        <div className="animate-fade-in space-y-3 border-t border-line px-4 py-3 text-sm">
                          <div>
                            <p className="text-xs font-semibold uppercase text-faint">Question</p>
                            <p className="text-fg/90">{qa.question}</p>
                          </div>
                          {qa.ground_truth && (
                            <div>
                              <p className="text-xs font-semibold uppercase text-faint">Ground truth</p>
                              <p className="text-fg/90">{qa.ground_truth}</p>
                            </div>
                          )}
                          <div>
                            <p className="text-xs font-semibold uppercase text-faint">Generated answer</p>
                            <p className="rounded-md bg-brand-50 p-2 text-fg/90 dark:bg-brand-500/10">{qa.answer || "—"}</p>
                          </div>
                          <div className="flex flex-wrap items-center gap-2">
                            <Badge tone="slate">Chunks: {qa.chunks_retrieved ?? "—"}</Badge>
                            {qa.sources?.map((s) => (
                              <Badge key={s} tone="brand">
                                {s}
                              </Badge>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  );
                })}
              </CardBody>
            </Card>
          )}
        </div>
      )}

      {/* Idle */}
      {!evaluation.isPending && !data && !evaluation.isError && (
        <EmptyState
          title="No evaluation run yet"
          description="Click “Run evaluation” to score the pipeline against the FAB test set."
        />
      )}
    </div>
  );
}
