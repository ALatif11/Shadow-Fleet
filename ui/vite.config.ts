/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Static app: the Python side writes JSON into public/ui_data (ADR-18). No API server.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, host: "127.0.0.1" },
  // The Natural Earth land chunk is ~3 MB and loads lazily.
  build: { chunkSizeWarningLimit: 3500 },
  test: { environment: "node", include: ["src/**/*.test.ts"] },
});
