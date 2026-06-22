import { apiClient, toApiError } from "@/lib/apiClient";
import type { AdminResponse, HealthResponse, ReadyResponse } from "@/types/api";

/** GET /health — liveness probe (no auth required). */
export async function getHealth(): Promise<HealthResponse> {
  try {
    const { data } = await apiClient.get<HealthResponse>("/health", { timeout: 5_000 });
    return data;
  } catch (err) {
    throw toApiError(err);
  }
}

/** GET /ready — readiness probe; reports vector DB reachability. */
export async function getReady(): Promise<ReadyResponse> {
  try {
    const { data } = await apiClient.get<ReadyResponse>("/ready", { timeout: 8_000 });
    return data;
  } catch (err) {
    throw toApiError(err);
  }
}

/** POST /api/v1/admin/reindex — drop + recreate the collection. */
export async function reindex(): Promise<AdminResponse> {
  try {
    const { data } = await apiClient.post<AdminResponse>("/api/v1/admin/reindex", undefined, {
      timeout: 60_000,
    });
    return data;
  } catch (err) {
    throw toApiError(err);
  }
}

/** DELETE /api/v1/admin/collection — delete the configured collection. */
export async function deleteCollection(): Promise<AdminResponse> {
  try {
    const { data } = await apiClient.delete<AdminResponse>("/api/v1/admin/collection", {
      timeout: 30_000,
    });
    return data;
  } catch (err) {
    throw toApiError(err);
  }
}
