import { afterEach, describe, expect, it, vi } from "vitest";
import { buildProviderSaveArgs } from "./lib";
import type { ProviderConfig } from "./types";

const config: ProviderConfig = {
  baseUrl: "https://api.deepseek.com",
  hasApiKey: false,
  defaultModel: "deepseek-chat",
  v4FlashModelId: "deepseek-v4-flash",
  inputPrice: 0.001,
  outputPrice: 0.002,
  models: ["deepseek-chat"],
};

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
  vi.doUnmock("@tauri-apps/api/core");
  vi.doUnmock("@tauri-apps/plugin-dialog");
  vi.doUnmock("@tauri-apps/plugin-opener");
});

describe("provider save payload", () => {
  it("sends the api key as a separate argument, never inside config", () => {
    // 回归测试（v0.4.0 架构审查 P0-1）：后端 ProviderConfig.api_key 带
    // skip_deserializing，Key 塞进 config 会被静默丢弃 ⇒ 填了存不进去，
    // UI 却显示"配置已保存"。Key 必须走独立参数。
    const args = buildProviderSaveArgs(config, "sk-test-key");
    expect(args.apiKey).toBe("sk-test-key");
    expect("apiKey" in args.config).toBe(false);
  });

  it("sends null when no key is typed so the stored key is left untouched", () => {
    expect(buildProviderSaveArgs(config).apiKey).toBeNull();
    expect(buildProviderSaveArgs(config, "   ").apiKey).toBeNull();
  });

  it("trims the key before sending it", () => {
    expect(buildProviderSaveArgs(config, "  sk-test-key \n").apiKey).toBe("sk-test-key");
  });
});

describe("saveProviderConfig in the desktop app", () => {
  it("invokes save_provider_config with the key as its own argument", async () => {
    const calls: Array<{ cmd: string; args: Record<string, unknown> }> = [];
    vi.stubGlobal("window", { __TAURI_INTERNALS__: {} });
    vi.doMock("@tauri-apps/api/core", () => ({
      invoke: (cmd: string, args: Record<string, unknown>) => {
        calls.push({ cmd, args });
        return Promise.resolve(undefined);
      },
      Channel: class {},
    }));
    vi.doMock("@tauri-apps/plugin-dialog", () => ({ open: vi.fn(), save: vi.fn() }));
    vi.doMock("@tauri-apps/plugin-opener", () => ({ openUrl: vi.fn() }));

    const { saveProviderConfig } = await import("./lib");
    await saveProviderConfig(config, "sk-desktop-key");

    expect(calls).toHaveLength(1);
    expect(calls[0].cmd).toBe("save_provider_config");
    expect(calls[0].args.apiKey).toBe("sk-desktop-key");
    expect(calls[0].args.config).toEqual(config);
    expect("apiKey" in (calls[0].args.config as object)).toBe(false);
  });
});
