// Single-file harness build for artifact/demo distribution: one JS
// chunk (no code splitting, no dynamic-import chunks) so the output
// can be inlined into a self-contained HTML page.
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  root: "harness",
  plugins: [react()],
  build: {
    outDir: "../dist-harness",
    emptyOutDir: true,
    sourcemap: false,
    rollupOptions: {
      output: { inlineDynamicImports: true, manualChunks: undefined },
    },
  },
});
