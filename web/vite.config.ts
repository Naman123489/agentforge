/// <reference types="vitest/config" />
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

// The dashboard talks to the AgentForge Control Plane API (contracts/agentforge-openapi.yaml).
// In development, /api is proxied to the orchestrator/API server (default http://localhost:8000).
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ".", "");
  return {
    plugins: [react(), tailwindcss()],
    // Demo assets (static preview of the reference arcade) are served only in the labelled demo mode.
    publicDir: mode === "demo" ? "demo-public" : "public",
    server: {
      port: 5173,
      proxy: { "/api": { target: env.AGENTFORGE_API_PROXY ?? "http://localhost:8000", changeOrigin: true } },
    },
    test: {
      environment: "jsdom",
      setupFiles: ["./src/test/setup.ts"],
      css: false,
    },
  };
});
