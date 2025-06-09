/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In development the API runs on :8080 (docker compose up). Proxying keeps the browser on one origin, so the
// API's CORS list does not matter locally.
export default defineConfig({
  plugins: [react()],
  // amazon-cognito-identity-js (our own sign-in page) refers to Node's `global`; browsers call it globalThis
  define: { global: "globalThis" },
  optimizeDeps: { esbuildOptions: { define: { global: "globalThis" } } },
  server: { port: 5173, proxy: { "/v1": "http://localhost:8080", "/dev": "http://localhost:8080" } },
  test: { include: ["src/**/*.test.{ts,tsx}"], environment: "happy-dom", globals: true, setupFiles: ["./src/test/setup.ts"], css: false },
});
