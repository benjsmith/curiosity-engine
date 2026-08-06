// IIFE bundle for the Curiosity Engine wiki-view embedding: a single
// self-contained file exposing `window.KnowledgeAtlas`, vendorable the
// same way d3.min.js is (see RELEASE_CHECKLIST.md).
import { defineConfig } from "vite";

export default defineConfig({
  build: {
    lib: {
      entry: "src/iife.ts",
      name: "KnowledgeAtlas",
      formats: ["iife"],
      fileName: () => "knowledge-atlas.iife.js",
    },
    outDir: "dist",
    emptyOutDir: false,
    sourcemap: false,
    minify: true,
  },
});
