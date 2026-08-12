import { describe, expect, it } from "vitest";
import { applyChangeDecision, applySuggestionToText, normalizeResult, pushUndoSnapshot, rebuildAfterKeepEssential, safeParseResult } from "./lib";
import type { EnhancementResult, PromptChange, Suggestion } from "./types";

const suggestion: Suggestion = {
  id: "s1",
  kind: "goal",
  title: "补充目标",
  purpose: "明确交付物",
  content: "请给出可以直接执行的下一步。",
  operation: "insert",
  anchor: "",
  applied: false,
};

describe("prompt result helpers", () => {
  it("normalizes model-owned UI state", () => {
    const raw = {
      status: "ready",
      primary_prompt: "结果",
      suggestions: [{ ...suggestion, applied: undefined }],
      changes: [{ id: "c1", type: "clarify", before: "旧", after: "新", reason: "更清楚", state: undefined }],
    } as unknown as EnhancementResult;
    const result = normalizeResult(raw);
    expect(result.changes[0].state).toBe("pending");
    expect(result.suggestions[0].applied).toBe(false);
    expect(result.assumptions).toEqual([]);
    expect(result.risk_flags).toEqual([]);
  });

  it("applies insert and anchored replacement suggestions", () => {
    expect(applySuggestionToText("原提示词", suggestion)).toBe("原提示词\n\n请给出可以直接执行的下一步。");
    expect(applySuggestionToText("面向新手解释", { ...suggestion, operation: "replace", anchor: "新手", content: "大三学生" }))
      .toBe("面向大三学生解释");
  });

  it("accepts and rejects an atomic change without duplicating text", () => {
    const change: PromptChange = { id: "c1", type: "clarify", before: "解释代码", after: "解释这段代码的用途", reason: "明确范围", state: "pending" };
    expect(applyChangeDecision("请解释代码", change, "accepted")).toBe("请解释这段代码的用途");
    expect(applyChangeDecision("请解释这段代码的用途", change, "rejected")).toBe("请解释代码");
    expect(applyChangeDecision("请解释这段代码的用途", change, "accepted")).toBe("请解释这段代码的用途");
  });

  it("caps undo history at 100 snapshots", () => {
    const stack = Array.from({ length: 100 }, (_, index) => String(index));
    const next = pushUndoSnapshot(stack, "100");
    expect(next).toHaveLength(100);
    expect(next[0]).toBe("1");
    expect(next.at(-1)).toBe("100");
  });

  it("rejects malformed streamed JSON", () => {
    expect(safeParseResult("not json")).toBeNull();
  });

  it("fills legacy results without new fields into complete/light with empty notices", () => {
    const legacy = {
      status: "ready",
      primary_prompt: "旧版结果",
      assumptions: [],
      questions: [],
      changes: [],
      suggestions: [],
      risk_flags: [],
    } as unknown as EnhancementResult;
    const result = normalizeResult(legacy);
    expect(result.delivery_status).toBe("complete");
    expect(result.enhancement_level).toBe("light");
    expect(result.notices).toEqual([]);
  });

  it("derives clarify enhancement level for legacy needs_clarification results", () => {
    const legacy = {
      status: "needs_clarification",
      primary_prompt: "需要澄清",
      assumptions: [],
      questions: [{ id: "q1", text: "对象是什么？", why_needed: "缺少对象" }],
      changes: [],
      suggestions: [],
      risk_flags: [],
    } as unknown as EnhancementResult;
    const result = normalizeResult(legacy);
    expect(result.delivery_status).toBe("complete");
    expect(result.enhancement_level).toBe("clarify");
  });

  it("keeps primary_prompt and notices for partial results", () => {
    const partial = {
      status: "ready",
      delivery_status: "partial",
      enhancement_level: "light",
      notices: ["模型未返回完整建议，主提示词仍可使用。"],
      primary_prompt: "部分增强结果",
      assumptions: [],
      questions: [],
      changes: [],
      suggestions: [],
      risk_flags: [],
    } as unknown as EnhancementResult;
    const result = normalizeResult(partial);
    expect(result.delivery_status).toBe("partial");
    expect(result.enhancement_level).toBe("light");
    expect(result.primary_prompt).toBe("部分增强结果");
    expect(result.notices).toEqual(["模型未返回完整建议，主提示词仍可使用。"]);
  });

  it("normalizes fallback results to the original prompt with a notice", () => {
    const fallback = {
      status: "ready",
      delivery_status: "fallback",
      enhancement_level: "none",
      notices: ["增强服务未返回可用结构，本次已保留原始提示词"],
      primary_prompt: "我的原始提示词",
      assumptions: [],
      questions: [],
      changes: [],
      suggestions: [],
      risk_flags: [],
    } as unknown as EnhancementResult;
    const result = normalizeResult(fallback);
    expect(result.delivery_status).toBe("fallback");
    expect(result.enhancement_level).toBe("none");
    expect(result.primary_prompt).toBe("我的原始提示词");
    expect(result.notices).toHaveLength(1);
  });

  it("rebuilds text keeping only safety and accepted changes", () => {
    const changes: PromptChange[] = [
      { id: "c1", type: "clarify", before: "解释代码", after: "解释这段代码的用途", reason: "明确范围", state: "pending" },
      { id: "c2", type: "format", before: "输出格式", after: "输出格式（列表）", reason: "固定结构", state: "accepted" },
      { id: "c3", type: "safety", before: "删除文件", after: "确认后再删除文件", reason: "防止误删", state: "pending" },
    ];
    const original = "请解释代码，输出格式，删除文件。";
    expect(rebuildAfterKeepEssential(changes, original)).toBe("请解释代码，输出格式（列表），确认后再删除文件。");
  });

  it("returns the original text when no changes are applicable", () => {
    const changes: PromptChange[] = [
      { id: "c1", type: "clarify", before: "不存在的片段", after: "新内容", reason: "r", state: "pending" },
      { id: "c2", type: "format", before: "也不存在", after: "新内容2", reason: "r2", state: "rejected" },
    ];
    expect(rebuildAfterKeepEssential(changes, "原文")).toBe("原文");
  });
});
