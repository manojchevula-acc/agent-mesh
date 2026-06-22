import { apiClient, toApiError } from "@/lib/apiClient";
import type { RetrieveRequest, RetrieveResponse } from "@/types/api";

/** POST /api/v1/retrieve — hybrid retrieval (+ optional LLM answer). */
export async function retrieve(request: RetrieveRequest): Promise<RetrieveResponse> {
  try {
    const { data } = await apiClient.post<RetrieveResponse>("/api/v1/retrieve", request);
    return data;
  } catch (err) {
    throw toApiError(err);
  }
}
