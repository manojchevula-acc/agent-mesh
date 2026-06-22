/**
 * Runtime configuration derived from Vite env vars.
 *
 * In development we default to the Vite dev-server proxy (same-origin paths),
 * so `baseURL` is empty and requests go to `/api/...`. Set VITE_USE_PROXY=false
 * to hit VITE_API_BASE_URL directly (the backend must then allow this origin
 * via CORS — it allows "*" by default).
 */

const useProxy = (import.meta.env.VITE_USE_PROXY ?? "true") !== "false";

export const config = {
  /** Axios baseURL. Empty string => same-origin (dev proxy). */
  apiBaseURL: useProxy ? "" : (import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000"),
  /** Sent as X-API-Key on every request. */
  apiKey: import.meta.env.VITE_API_KEY ?? "dev-secret-key-change-in-production",
  useProxy,
} as const;
