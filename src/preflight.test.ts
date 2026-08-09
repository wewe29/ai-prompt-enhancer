import { describe, expect, it } from "vitest";
import { preflight } from "./preflight";

describe("preflight", () => {
  it("returns no findings for empty or whitespace input", () => {
    expect(preflight("", false)).toEqual([]);
    expect(preflight("   \n\t ", false)).toEqual([]);
  });

  it("warns when text is short and lacks request keywords", () => {
    const ids = preflight("内容很短", false).map((f) => f.id);
    expect(ids).toContain("too-short");
    const finding = preflight("内容很短", false).find((f) => f.id === "too-short");
    expect(finding?.level).toBe("warning");
  });

  it("does not flag audience when audience keywords are present", () => {
    const ids = preflight("这份文档面向新手读者，请写得浅显一些。", true).map((f) => f.id);
    expect(ids).not.toContain("audience");
  });

  it("does not flag format when format keywords are present", () => {
    const ids = preflight("请用表格列出结果，字数控制在 200 字以内。", true).map((f) => f.id);
    expect(ids).not.toContain("format");
  });

  it("does not flag constraint when constraint keywords are present", () => {
    const ids = preflight("不要使用术语，必须保留原始结论。", true).map((f) => f.id);
    expect(ids).not.toContain("constraint");
  });

  it("flags missing context only when hasContext is false", () => {
    const text = "这是一段足够长的需求描述，用于触发上下文检查。";
    const without = preflight(text, false).map((f) => f.id);
    const withContext = preflight(text, true).map((f) => f.id);
    expect(without).toContain("context");
    expect(withContext).not.toContain("context");
  });

  it("flags audience, format, constraint and context for keyword-free long text", () => {
    const ids = preflight("今天天气很好我们出去散步吧", false).map((f) => f.id);
    expect(ids).toEqual(expect.arrayContaining(["audience", "format", "constraint", "context"]));
  });
});
