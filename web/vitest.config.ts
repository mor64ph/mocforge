import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    // Path contains a space ("personal projects") which breaks Vitest's default
    // fork-based pool on Windows — the subprocess argv gets mis-tokenised.
    // vmThreads runs workers in the same process via node:worker_threads,
    // which is immune to the quoting issue.
    pool: "vmThreads",
  },
});
