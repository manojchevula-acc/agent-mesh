import { useMemo, useState } from "react";
import { Database, Eraser, FileSearch, Search, Timer, Zap } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { Alert } from "@/components/ui/Alert";
import { Metric } from "@/components/ui/Metric";
import { Toggle } from "@/components/ui/Toggle";
import { Slider } from "@/components/ui/Slider";
import { Select, TextArea, Label } from "@/components/ui/Field";
import { CenteredSpinner } from "@/components/ui/Spinner";
import { EmptyState } from "@/components/ui/EmptyState";
import { AnswerBox } from "@/components/search/AnswerBox";
import { AnswerEvaluation } from "@/components/search/AnswerEvaluation";
import { ChunkCard } from "@/components/search/ChunkCard";
import { SampleQuestions } from "@/components/search/SampleQuestions";
import { useRetrieve } from "@/hooks/useRetrieve";
import {
  DOC_TYPE_OPTIONS,
  TOP_K_DEFAULT,
  TOP_K_MAX,
  TOP_K_MIN,
} from "@/config/constants";
import { formatLatency } from "@/lib/utils";
import type { RetrieveRequest } from "@/types/api";

export function SearchPage() {
  const [query, setQuery] = useState("");
  const [submittedQuery, setSubmittedQuery] = useState("");
  const [topK, setTopK] = useState(TOP_K_DEFAULT);
  const [generateAnswer, setGenerateAnswer] = useState(true);
  const [docTypeIdx, setDocTypeIdx] = useState(0);

  const retrieve = useRetrieve();
  const data = retrieve.data;

  const docTypeOptions = useMemo(
    () => DOC_TYPE_OPTIONS.map((o, i) => ({ label: o.label, value: String(i) })),
    [],
  );

  const canSearch = query.trim().length >= 3 && !retrieve.isPending;

  function runSearch() {
    const trimmed = query.trim();
    if (trimmed.length < 3) return;

    const docType = DOC_TYPE_OPTIONS[docTypeIdx]?.value ?? null;
    const request: RetrieveRequest = {
      query: trimmed,
      top_k: topK,
      generate_answer: generateAnswer,
      include_parent: true,
      filters: docType ? { document_type: [docType] } : {},
    };
    setSubmittedQuery(trimmed);
    retrieve.mutate(request);
  }

  function clearAll() {
    setQuery("");
    setSubmittedQuery("");
    retrieve.reset();
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    // Cmd/Ctrl+Enter submits.
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
      e.preventDefault();
      runSearch();
    }
  }

  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-[1fr_300px]">
      {/* ── Main column ──────────────────────────────────────────────── */}
      <div className="order-2 space-y-6 lg:order-1">
        <Card>
          <CardBody className="space-y-3">
            <Label htmlFor="query">Your question</Label>
            <TextArea
              id="query"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={onKeyDown}
              rows={3}
              placeholder="e.g. What is the pricing floor for BB-rated AED corporate loans?"
            />
            <div className="flex items-center justify-between gap-3">
              <p className="text-xs text-faint">
                Press <kbd className="rounded border border-line bg-surface-2 px-1">⌘/Ctrl</kbd> +{" "}
                <kbd className="rounded border border-line bg-surface-2 px-1">Enter</kbd> to search
              </p>
              <div className="flex gap-2">
                <Button variant="outline" size="md" onClick={clearAll} disabled={retrieve.isPending}>
                  <Eraser className="h-4 w-4" /> Clear
                </Button>
                <Button onClick={runSearch} loading={retrieve.isPending} disabled={!canSearch}>
                  <Search className="h-4 w-4" /> Search
                </Button>
              </div>
            </div>
          </CardBody>
        </Card>

        {/* Error */}
        {retrieve.isError && (
          <Alert variant={retrieve.error.isNetwork ? "warning" : "error"} title="Search failed">
            {retrieve.error.message}
            {retrieve.error.isNetwork && (
              <p className="mt-1 font-mono text-xs">
                uvicorn gernas_rag.main:app --reload --app-dir src
              </p>
            )}
          </Alert>
        )}

        {/* Loading */}
        {retrieve.isPending && <CenteredSpinner label="Searching policy documents…" />}

        {/* Results */}
        {!retrieve.isPending && data && (
          <div className="space-y-6">
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              <Metric label="Chunks" value={data.chunks.length} hint={<><FileSearch className="mr-1 inline h-3 w-3" />retrieved</>} />
              <Metric label="Latency" value={formatLatency(data.latency_ms)} hint={<><Timer className="mr-1 inline h-3 w-3" />end-to-end</>} />
              <Metric
                label="Cache"
                value={data.cache_hit ? "HIT" : "MISS"}
                tone={data.cache_hit ? "good" : "default"}
                hint={<><Zap className="mr-1 inline h-3 w-3" />Redis</>}
              />
              <Metric
                label="Freshness"
                value={data.freshness_warning_global ? "Stale" : "OK"}
                tone={data.freshness_warning_global ? "warn" : "good"}
                hint={<><Database className="mr-1 inline h-3 w-3" />context</>}
              />
            </div>

            {generateAnswer &&
              (data.answer ? (
                <>
                  <AnswerBox answer={data.answer} />
                  <AnswerEvaluation
                    question={submittedQuery}
                    answer={data.answer}
                    contexts={data.chunks.map((c) => c.parent_text || c.text)}
                  />
                </>
              ) : (
                <Alert variant="info" title="No answer generated">
                  Ensure <code>RAG__LLM__GROQ_API_KEY</code> is set in the backend <code>.env</code>{" "}
                  and the server was restarted.
                </Alert>
              ))}

            <div>
              <h2 className="mb-3 text-sm font-semibold text-fg">
                Retrieved chunks ({data.chunks.length})
              </h2>
              {data.chunks.length === 0 ? (
                <Alert variant="warning">No chunks retrieved. Try rephrasing your query.</Alert>
              ) : (
                <div className="space-y-4">
                  {data.chunks.map((chunk, i) => (
                    <ChunkCard key={`${chunk.source}-${i}`} chunk={chunk} rank={i + 1} />
                  ))}
                </div>
              )}
            </div>
          </div>
        )}

        {/* Idle empty state */}
        {!retrieve.isPending && !data && !retrieve.isError && (
          <EmptyState
            icon={<FileSearch className="h-12 w-12" />}
            title="Ask a policy question to get started"
            description="Enter a question above, or pick a sample question from the panel. Results include the LLM answer and the source chunks that grounded it."
          />
        )}
      </div>

      {/* ── Settings sidebar ─────────────────────────────────────────── */}
      <div className="order-1 space-y-6 lg:order-2">
        <Card>
          <CardHeader title="Settings" />
          <CardBody className="space-y-5">
            <Slider
              label="Chunks to retrieve"
              value={topK}
              min={TOP_K_MIN}
              max={TOP_K_MAX}
              onChange={setTopK}
            />
            <Toggle
              checked={generateAnswer}
              onChange={setGenerateAnswer}
              label="Generate LLM answer"
            />
            <div>
              <Label htmlFor="docType">Filter by document type</Label>
              <Select
                id="docType"
                options={docTypeOptions}
                value={String(docTypeIdx)}
                onChange={(e) => setDocTypeIdx(Number(e.target.value))}
              />
            </div>
          </CardBody>
        </Card>

        <Card>
          <CardBody>
            <SampleQuestions
              onPick={(q) => {
                setQuery(q);
                retrieve.reset();
              }}
            />
          </CardBody>
        </Card>
      </div>
    </div>
  );
}
