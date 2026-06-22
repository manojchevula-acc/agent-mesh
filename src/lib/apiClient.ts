import axios, { AxiosError } from "axios";
import { config } from "./config";

/**
 * Shared axios instance. Injects the X-API-Key header and normalises errors
 * into a predictable shape for the UI / React Query.
 */
export const apiClient = axios.create({
  baseURL: config.apiBaseURL,
  timeout: 120_000, // retrieval + generation can be slow on CPU
  headers: {
    "Content-Type": "application/json",
  },
});

apiClient.interceptors.request.use((req) => {
  if (config.apiKey) {
    req.headers.set("X-API-Key", config.apiKey);
  }
  // For multipart uploads, let the browser set Content-Type (with the boundary)
  // by removing the JSON default.
  if (req.data instanceof FormData) {
    req.headers.delete("Content-Type");
  }
  return req;
});

/** A normalised error the UI can render directly. */
export class ApiError extends Error {
  status?: number;
  detail?: string;
  isNetwork: boolean;

  constructor(message: string, opts: { status?: number; detail?: string; isNetwork?: boolean } = {}) {
    super(message);
    this.name = "ApiError";
    this.status = opts.status;
    this.detail = opts.detail;
    this.isNetwork = opts.isNetwork ?? false;
  }
}

/** Pull a human-readable message out of a FastAPI error body. */
function extractDetail(data: unknown): string | undefined {
  if (!data || typeof data !== "object") return typeof data === "string" ? data : undefined;
  const detail = (data as { detail?: unknown }).detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    // FastAPI validation errors: [{loc, msg, type}, ...]
    return detail
      .map((d) => (typeof d === "object" && d && "msg" in d ? String((d as { msg: unknown }).msg) : String(d)))
      .join("; ");
  }
  return undefined;
}

export function toApiError(err: unknown): ApiError {
  if (err instanceof ApiError) return err;

  if (axios.isAxiosError(err)) {
    const axErr = err as AxiosError;
    if (axErr.response) {
      const detail = extractDetail(axErr.response.data);
      return new ApiError(
        detail || `Request failed with status ${axErr.response.status}`,
        { status: axErr.response.status, detail },
      );
    }
    // No response => network / connection problem.
    return new ApiError("Cannot reach the API server. Is the backend running?", {
      isNetwork: true,
    });
  }

  return new ApiError(err instanceof Error ? err.message : "Unexpected error");
}
