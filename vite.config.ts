import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import fs from "node:fs";
import path from "node:path";
import os from "node:os";

// 临时目录统一使用 %TEMP%\PromptCraft-* 前缀；进程退出时清理，
// 避免 vite/vitest 每次运行都在系统临时目录留下残留。
const cacheDir = path.join(os.tmpdir(), "PromptCraft-vite-" + process.pid);
process.on("exit", () => {
  try {
    fs.rmSync(cacheDir, { recursive: true, force: true });
  } catch {
    // 清理失败不影响构建结果
  }
});

export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  cacheDir,
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
