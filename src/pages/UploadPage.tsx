import { useEffect, useRef, useState } from "react";
import { CheckCircle2, Rocket, RotateCcw, XCircle } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { Alert } from "@/components/ui/Alert";
import { Metric } from "@/components/ui/Metric";
import { Label, Select, TextInput } from "@/components/ui/Field";
import { Spinner } from "@/components/ui/Spinner";
import { FileDropzone } from "@/components/upload/FileDropzone";
import { useIngestStatus, useIngestUpload } from "@/hooks/useIngest";
import { UPLOAD_DOC_TYPES } from "@/config/constants";
import type { JobStatus } from "@/types/api";

const PIPELINE_STEPS = [
  { title: "Document extraction", desc: "Docling parses the PDF/DOCX: text, tables, layout structure." },
  { title: "Hierarchical chunking", desc: "Parent chunks (~1500 tok) for context + child chunks (~400 tok) for search." },
  { title: "Embedding", desc: "BGE-M3 builds dense + sparse vectors for every child chunk." },
  { title: "Storage", desc: "Chunks and vectors stored in Qdrant — immediately searchable." },
];

export function UploadPage() {
  const [file, setFile] = useState<File | null>(null);
  const [docType, setDocType] = useState("");
  const [effectiveDate, setEffectiveDate] = useState("");
  const [products, setProducts] = useState("");
  const [jobId, setJobId] = useState<string | null>(null);
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const [elapsed, setElapsed] = useState(0);

  const upload = useIngestUpload();
  const status = useIngestStatus(jobId);
  const job = status.data;
  const activeStatuses: JobStatus[] = ["running", "pending"];
  const isRunning =
    Boolean(jobId) && ((job ? activeStatuses.includes(job.status) : status.isLoading));

  // Elapsed timer while a job runs.
  const timerRef = useRef<number | null>(null);
  useEffect(() => {
    if (isRunning && startedAt) {
      timerRef.current = window.setInterval(() => setElapsed(Math.floor((Date.now() - startedAt) / 1000)), 1000);
      return () => {
        if (timerRef.current) window.clearInterval(timerRef.current);
      };
    }
  }, [isRunning, startedAt]);

  function submit() {
    if (!file) return;
    upload.mutate(
      { file, documentType: docType, productApplicability: products.trim(), effectiveDate: effectiveDate.trim() },
      {
        onSuccess: (res) => {
          setJobId(res.job_id);
          setStartedAt(Date.now());
          setElapsed(0);
        },
      },
    );
  }

  function reset() {
    setFile(null);
    setDocType("");
    setEffectiveDate("");
    setProducts("");
    setJobId(null);
    setStartedAt(null);
    setElapsed(0);
    upload.reset();
  }

  const completed = job?.status === "success";
  const errored = job?.status === "error";
  const formDisabled = isRunning || upload.isPending;

  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-[1fr_320px]">
      <div className="space-y-6">
        <Card>
          <CardHeader title="Select file & metadata" subtitle="Upload a PDF or DOCX to ingest into the knowledge base." />
          <CardBody className="space-y-5">
            <FileDropzone file={file} onSelect={setFile} disabled={formDisabled} />

            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <div>
                <Label htmlFor="docType">Document type</Label>
                <Select
                  id="docType"
                  options={UPLOAD_DOC_TYPES}
                  value={docType}
                  onChange={(e) => setDocType(e.target.value)}
                  disabled={formDisabled}
                />
              </div>
              <div>
                <Label htmlFor="effDate">Effective date (optional)</Label>
                <TextInput
                  id="effDate"
                  placeholder="YYYY-MM-DD"
                  value={effectiveDate}
                  onChange={(e) => setEffectiveDate(e.target.value)}
                  disabled={formDisabled}
                />
              </div>
            </div>

            <div>
              <Label htmlFor="products">Product applicability (optional)</Label>
              <TextInput
                id="products"
                placeholder="e.g. Corporate Loans, Trade Finance"
                value={products}
                onChange={(e) => setProducts(e.target.value)}
                disabled={formDisabled}
              />
            </div>

            <Button onClick={submit} loading={upload.isPending} disabled={!file || formDisabled} className="w-full">
              <Rocket className="h-4 w-4" /> Upload & Ingest
            </Button>

            {upload.isError && (
              <Alert variant={upload.error.isNetwork ? "warning" : "error"} title="Upload rejected">
                {upload.error.message}
              </Alert>
            )}
          </CardBody>
        </Card>

        {/* Progress */}
        {jobId && (
          <Card>
            <CardHeader title="Ingestion progress" />
            <CardBody className="space-y-4">
              {isRunning && (
                <Alert variant="info">
                  <div className="flex items-center gap-2">
                    <Spinner className="h-4 w-4" />
                    <span>
                      Processing <strong>{file?.name}</strong> — {elapsed}s elapsed. Extraction +
                      chunking + embedding runs on CPU (1–5 min).
                    </span>
                  </div>
                </Alert>
              )}

              {status.isError && (
                <Alert variant="error" title="Lost track of the job">
                  {status.error.message}
                </Alert>
              )}

              {completed && (
                <>
                  <Alert variant="success" title="Ingested successfully">
                    <strong>{file?.name}</strong> is now searchable.
                  </Alert>
                  <div className="grid grid-cols-3 gap-3">
                    <Metric label="Chunks" value={job?.chunks_created ?? "—"} tone="good" />
                    <Metric label="Status" value="Completed" tone="good" />
                    <Metric label="Job" value={jobId.slice(0, 8) + "…"} />
                  </div>
                  <Button variant="outline" onClick={reset}>
                    <RotateCcw className="h-4 w-4" /> Upload another
                  </Button>
                </>
              )}

              {errored && (
                <>
                  <Alert variant="error" title="Ingestion failed">
                    {job?.error || "Unknown error. Check the server logs."}
                  </Alert>
                  <Button variant="outline" onClick={reset}>
                    <RotateCcw className="h-4 w-4" /> Try again
                  </Button>
                </>
              )}
            </CardBody>
          </Card>
        )}
      </div>

      {/* Pipeline explainer */}
      <div>
        <Card>
          <CardHeader title="What happens after upload?" />
          <CardBody>
            <ol className="space-y-3">
              {PIPELINE_STEPS.map((step, i) => (
                <li key={step.title} className="flex gap-3">
                  <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-brand-100 text-xs font-semibold text-brand-700 dark:bg-brand-500/15 dark:text-brand-200">
                    {i + 1}
                  </span>
                  <div>
                    <p className="text-sm font-medium text-fg">{step.title}</p>
                    <p className="text-xs leading-relaxed text-muted">{step.desc}</p>
                  </div>
                </li>
              ))}
            </ol>
            <div className="mt-4 flex items-center gap-2 rounded-lg bg-surface-2 px-3 py-2 text-xs text-muted">
              {completed ? (
                <CheckCircle2 className="h-4 w-4 text-emerald-500" />
              ) : errored ? (
                <XCircle className="h-4 w-4 text-red-500" />
              ) : (
                <Rocket className="h-4 w-4 text-brand-500" />
              )}
              Once ingested, head to Search to query the new document.
            </div>
          </CardBody>
        </Card>
      </div>
    </div>
  );
}
