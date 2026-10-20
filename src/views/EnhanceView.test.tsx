import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { EnhanceView, type EnhanceViewProps } from "./EnhanceView";
import type { EnhancementResult, ProviderConfig } from "../types";

/**
 * DOM 级回归：增强彻底失败时，界面上不能出现"增强器判断无需修改"。
 *
 * 缺陷来源（v0.4.0 架构审查 E1 / E2）：
 *   src-tauri/src/provider.rs:190-228 结构解析彻底失败 → 构造 fallback → 命令仍返回 Ok(())
 *   src-tauri/src/provider.rs:894       fallback_result 把 enhancement_level 硬编码成 "none"
 *   src/views/EnhanceView.tsx:192      旧实现只看 enhancement_level，于是显示"增强器判断无需修改"
 *
 * 修复：档位与状态的呈现统一由 src/components/resultTruth.ts 决定。
 */

const provider: ProviderConfig = {
  baseUrl: "https://api.deepseek.com",
  hasApiKey: true,
  defaultModel: "deepseek-chat",
  v4FlashModelId: "deepseek-v4-flash",
  inputPrice: 0.001,
  outputPrice: 0.002,
  models: ["deepseek-chat"],
};

const noop = () => undefined;

function makeProps(overrides: Partial<EnhanceViewProps> = {}): EnhanceViewProps {
  const base = {
    model: "deepseek-chat",
    setModel: noop,
    provider,
    target: "doubao",
    setTarget: noop,
    verbosity: "standard",
    setVerbosity: noop,
    state: "ready",
    setState: noop,
    stop: async () => undefined,
    runEnhance: noop,
    customInstructions: "",
    setCustomInstructions: noop,
    customTargetUrl: "",
    setCustomTargetUrl: noop,
    error: "",
    setError: noop,
    original: "帮我写一封请假邮件",
    setOriginal: noop,
    context: "",
    setContext: noop,
    totalChars: 0,
    attachments: [],
    setAttachments: noop,
    addAttachments: async () => undefined,
    output: "帮我写一封正式的请假邮件（注明事由与日期）",
    commitOutput: noop,
    undo: noop,
    redo: noop,
    undoStack: [],
    redoStack: [],
    result: null,
    setResult: noop,
    usage: null,
    handleCopyOpen: async () => undefined,
    currentTarget: { id: "doubao", label: "豆包", url: "https://www.doubao.com/" },
    clarificationRound: 0,
    answers: {},
    setAnswers: noop,
    submitClarification: noop,
    changeState: noop,
    setSelectedSuggestion: noop,
    showSecurity: false,
    setShowSecurity: noop,
    selectedSuggestionData: null,
    notices: [],
    restoreOriginal: noop,
    keepEssentialEdits: noop,
    hasActionableChanges: false,
    regenerate: noop,
    activeCandidate: 1,
    switchCandidate: noop,
    acceptAllChanges: noop,
    rejectAllChanges: noop,
    hasPendingChanges: false,
  };
  return { ...base, ...overrides } as unknown as EnhanceViewProps;
}

function makeResult(overrides: Partial<EnhancementResult> = {}): EnhancementResult {
  return {
    status: "ready",
    delivery_status: "complete",
    enhancement_level: "light",
    notices: [],
    task_type: "other",
    primary_prompt: "帮我写一封正式的请假邮件（注明事由与日期）",
    facts: [],
    assumptions: [],
    questions: [],
    changes: [],
    suggestions: [],
    risk_flags: [],
    candidates: [],
    ...overrides,
  };
}

function render(overrides: Partial<EnhanceViewProps> = {}): string {
  return renderToStaticMarkup(<EnhanceView {...makeProps(overrides)} />);
}

describe("P2-1 增强彻底失败时不得陈述一个没发生过的判断", () => {
  it("fallback：DOM 里出现『增强失败』，不出现『增强器判断无需修改』", () => {
    const result = makeResult({
      delivery_status: "fallback",
      enhancement_level: "none",
      notices: ["增强服务未返回可用结构，本次已保留原始提示词"],
    });
    const html = render({ result, deliveryStatus: "fallback", notices: result.notices });
    expect(html).toContain("增强失败");
    expect(html).not.toContain("增强器判断无需修改");
  });

  it("fallback：档位一行显示『不可判定』，不显示『无需明显修改』", () => {
    const result = makeResult({ delivery_status: "fallback", enhancement_level: "none" });
    const html = render({ result, deliveryStatus: "fallback" });
    expect(html).toContain("不可判定");
    expect(html).not.toContain("无需明显修改");
  });

  it("fallback：不再显示『增强完成』", () => {
    // 后端失败路径仍返回 status="ready"，直接渲染流程状态会说"增强完成"。
    const result = makeResult({ delivery_status: "fallback", enhancement_level: "none" });
    const html = render({ result, deliveryStatus: "fallback" });
    expect(html).not.toContain("增强完成");
  });

  it("partial：档位为占位值 none 时不可判定，但交付状态仍如实显示", () => {
    const result = makeResult({ delivery_status: "partial", enhancement_level: "none" });
    const html = render({ result, deliveryStatus: "partial" });
    expect(html).toContain("不可判定");
    expect(html).not.toContain("无需明显修改");
    expect(html).toContain("部分交付");
  });

  it("complete + none：真实判断仍然照说，不回归", () => {
    const result = makeResult({ delivery_status: "complete", enhancement_level: "none" });
    const html = render({ result, deliveryStatus: "complete" });
    expect(html).toContain("增强器判断无需修改，建议直接使用原文");
    expect(html).toContain("无需明显修改");
    expect(html).toContain("增强完成");
  });

  it("complete + light：正常路径不受影响", () => {
    const result = makeResult({ delivery_status: "complete", enhancement_level: "light" });
    const html = render({ result, deliveryStatus: "complete" });
    expect(html).toContain("轻度增强");
    expect(html).toContain("增强完成");
    expect(html).not.toContain("增强器判断无需修改");
  });

  it("G12：结果与原文逐字相同，档位一行改为事实陈述，不再说『轻度增强』", () => {
    // 模型可能原样返回了用户的提示词却标成 light —— 「看起来成功了但其实没干活」，
    // 恰恰是本产品核心价值主张失效时的表现。此时不得展示与屏幕内容矛盾的断言。
    const identical = "帮我写一封请假邮件";
    const result = makeResult({
      delivery_status: "complete",
      enhancement_level: "light",
      primary_prompt: identical,
    });
    const html = render({ result, deliveryStatus: "complete", original: identical, output: identical });
    expect(html).toContain("这一版与你的原文完全相同（没有做出改动）");
    expect(html).not.toContain("轻度增强");
    expect(html).toContain("未改动任何内容");
  });
});
