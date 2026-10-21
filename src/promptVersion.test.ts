import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

// 前端把 SYSTEM_PROMPT_VERSION 写进历史记录的 promptVersion。
// 它一旦和 Rust 侧漂了，历史记录就在写一个不存在的提示词版本号，
// 而没有任何东西会报警 —— 所以这里读对方源码把它钉住。
const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "..");
const read = (relative: string) => readFileSync(path.join(root, relative), "utf8");

const RUST_PROMPTS = "src-tauri/src/prompts.rs";
const APP_TSX = "src/App.tsx";

function rustVersion(): string | undefined {
  return read(RUST_PROMPTS).match(/const SYSTEM_PROMPT_VERSION: &str = "([^"]+)"/)?.[1];
}

function tsVersion(): string | undefined {
  return read(APP_TSX).match(/const SYSTEM_PROMPT_VERSION = "([^"]+)"/)?.[1];
}

describe("SYSTEM_PROMPT_VERSION 跨源一致", () => {
  it("Rust 与 TS 的版本号必须相同", () => {
    const rust = rustVersion();
    const ts = tsVersion();
    expect(rust, "prompts.rs 里找不到 SYSTEM_PROMPT_VERSION").toBeTruthy();
    expect(ts, "App.tsx 里找不到 SYSTEM_PROMPT_VERSION").toBeTruthy();
    expect(ts).toBe(rust);
  });

  it("版本号必须是已发布的 promptcraft 语义版本", () => {
    expect(rustVersion()).toMatch(/^promptcraft-v\d+\.\d+\.\d+$/);
  });

  it("提示词正文里必须带问题自检段（v2.5.0 起）", () => {
    // 只追加不改写：自检段出现且第 8 条仍在。
    const rust = read(RUST_PROMPTS);
    expect(rust).toContain("问题自检（每个问题输出前逐条过，任一条不通过就删掉该问题）：");
    expect(rust).toContain("典型反例（以下问题一律禁止出现）：");
    expect(rust).toContain("最多提出 3 个真正影响结果的问题");
  });
});
