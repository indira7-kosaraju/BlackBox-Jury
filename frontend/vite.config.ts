/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Served by the FastAPI backend at /ui (same origin). In `npm run dev`, API calls
// are proxied to the local backend so the browser never needs CORS.
export default defineConfig({
  base: "/ui/",
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
    chunkSizeWarningLimit: 1200,
    // Never inline assets as data: URIs - the /ui CSP allows fonts and images only from self.
    assetsInlineLimit: 0,
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    css: false,
  },
});
