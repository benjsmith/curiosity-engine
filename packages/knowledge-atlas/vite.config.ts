// Dual-purpose config: `vite` (dev) serves the experiment harness;
// `vite build` produces the library ESM bundles. The IIFE bundle has
// its own config (vite.iife.config.ts) because lib-mode multi-entry
// builds cannot mix ESM and IIFE formats in one pass. Paths are
// relative to this file's directory (vite resolves them from root).
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ command }) => {
  if (command === "serve") {
    return {
      root: "harness",
      plugins: [react()],
      server: { port: 5199, strictPort: true },
    };
  }
  return {
    build: {
      lib: {
        entry: {
          "knowledge-atlas": "src/index.ts",
          "knowledge-atlas-react": "src/react.ts",
        },
        formats: ["es"],
      },
      rollupOptions: {
        external: ["react", "react-dom", "react/jsx-runtime"],
      },
      outDir: "dist",
      emptyOutDir: true,
      sourcemap: true,
    },
  };
});
