import { describe, expect, it } from "vitest";
import { buildProviderSaveMessage, staleResultNotice } from "./providerMessage";

describe("buildProviderSaveMessage（G9）", () => {
  it("没跑验证时不得出现「验证成功」", () => {
    // 回归测试（v0.4.0 清单 G9）：App.tsx:295 只在 apiKeyDraft 非空时才
    // validateProvider，而 :300 曾经无条件显示「连接验证成功，配置已保存」。
    // Key 留空时一次网络请求都没发。
    const message = buildProviderSaveMessage({ validated: false, keyWritten: false });
    expect(message).not.toContain("验证成功");
    expect(message).toContain("未验证连接");
    expect(message).toContain("没有测试过能否连通");
  });

  it("没跑验证且写了 Key：也必须说清「未验证」", () => {
    const message = buildProviderSaveMessage({ validated: false, keyWritten: true });
    expect(message).not.toContain("验证成功");
    expect(message).toContain("未验证连接");
    expect(message).toContain("已保存");
  });

  it("跑过验证时，两个事实分别陈述", () => {
    const message = buildProviderSaveMessage({ validated: true, keyWritten: true });
    expect(message).toContain("已验证");
    expect(message).toContain("已保存");
    expect(message).not.toContain("未验证连接");
  });

  it("跑过验证但没改 Key：说明本次没有改写已保存的 Key", () => {
    const message = buildProviderSaveMessage({ validated: true, keyWritten: false });
    expect(message).toContain("已验证");
    expect(message).toContain("没有改写已保存的 API Key");
  });
});

describe("staleResultNotice（G11）", () => {
  it("必须说清这是上一次的结果，并带上时间", () => {
    const notice = staleResultNotice("14:32:05");
    expect(notice).toContain("上一次的结果");
    expect(notice).toContain("14:32:05");
    expect(notice).toContain("本次请求失败");
  });

  it("没有时间戳时也不得含糊", () => {
    const notice = staleResultNotice("");
    expect(notice).toContain("上一次的结果");
    expect(notice).not.toContain("（）");
  });
});
