import { describe, expect, it } from "vitest";
import { applyChangeDecision, applySuggestionToText, normalizeResult, pushUndoSnapshot, safeParseResult } from "./lib";
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
});
