import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev-time proxy so the browser never needs CORS at all: every request to
// /api/* is forwarded server-side (Vite's dev server) to the FastAPI
// backend. In production, set VITE_API_BASE_URL instead (see src/api/client.js)
// and point it at your deployed API host.
const BACKEND = process.env.VITE_BACKEND_PROXY_TARGET || "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: BACKEND,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
