import { apiClient, toApiError } from "@/lib/apiClient";
import type { IngestAccepted, IngestJobStatus, IngestParams } from "@/types/api";

/** POST /api/v1/ingest — multipart upload, returns a job id (202 Accepted). */
export async function ingestDocument(params: IngestParams): Promise<IngestAccepted> {
  const form = new FormData();
  form.append("file", params.file, params.file.name);
  form.append("document_type", params.documentType);
  form.append("product_applicability", params.productApplicability);
  form.append("effective_date", params.effectiveDate);

  try {
    // The request interceptor strips the JSON Content-Type for FormData so the
    // browser sets the multipart boundary itself.
    const { data } = await apiClient.post<IngestAccepted>("/api/v1/ingest", form, {
      timeout: 60_000,
    });
    return data;
  } catch (err) {
    throw toApiError(err);
  }
}

/** GET /api/v1/ingest/{job_id} — poll ingestion job status. */
export async function getIngestStatus(jobId: string): Promise<IngestJobStatus> {
  try {
    const { data } = await apiClient.get<IngestJobStatus>(`/api/v1/ingest/${jobId}`, {
      timeout: 15_000,
    });
    return data;
  } catch (err) {
    throw toApiError(err);
  }
}
