import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

// https://vitejs.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const apiTarget = env.VITE_API_BASE_URL || "http://127.0.0.1:8000";

  return {
    plugins: [react()],
    resolve: {
      alias: {
        "@": path.resolve(__dirname, "./src"),
      },
    },
    server: {
      port: 5173,
      // Proxy /api and /health to the Starlette API server (api_server.py)
      // so the browser talks same-origin and CORS is never an issue.
      proxy: {
        "/api": {
          target: apiTarget,
          changeOrigin: true,
          // LLM responses can take 30-90s; raise proxy timeouts to match.
          proxyTimeout: 500_000,
          timeout: 500_000,
        },
        "/health": { target: apiTarget, changeOrigin: true },
      },
    },
  };
});
