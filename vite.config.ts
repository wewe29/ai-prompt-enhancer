import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";
import os from "node:os";

export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  cacheDir: path.join(os.tmpdir(), "promptcraft-vite-" + process.pid),
  server: {
    host: "127.0.0.1",
    port: 1420,
    strictPort: true,
  },
  envPrefix: ["VITE_", "TAURI_"],
  build: {
    target: "chrome105",
    minify: "esbuild",
    sourcemap: true,
  },
});
