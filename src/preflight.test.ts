import { describe, expect, it } from "vitest";
import { classifyTask, preflight, type PreflightTask } from "./preflight";

describe("classifyTask", () => {
  it("classifies the seven task types", () => {
    const cases: Array<[string, PreflightTask]> = [
      ["帮我看看这段 python 代码为什么报错", "code"],
      ["请把这段话翻译成英文", "translation"],
      ["帮我写一首诗", "creative"],
      ["统计本月销售数据并计算环比", "data"],
      ["帮我写一封周报邮件", "writing"],
      ["介绍一下量子计算是什么", "qa"],
      ["今天天气很好我们出去散步吧", "other"],
    ];
    for (const [input, expected] of cases) expect(classifyTask(input)).toBe(expected);
  });
});

describe("preflight", () => {
  it("returns no findings for empty or whitespace input", () => {
    expect(preflight("", false, "other")).toEqual([]);
    expect(preflight("   \n\t ", false, "other")).toEqual([]);
  });

  it("warns when text is short and lacks request keywords", () => {
    const findings = preflight("内容很短", false, "other");
    expect(findings.map((f) => f.id)).toContain("too-short");
    expect(findings.find((f) => f.id === "too-short")?.level).toBe("warning");
  });

  it("flags code inputs for missing code/error, never for audience", () => {
    const ids = preflight("帮我看看哪里出错了", false, "code").map((f) => f.id);
    expect(ids).toContain("code-source");
    expect(ids).not.toContain("audience");
  });

  it("does not flag the code-source check when an error is present", () => {
    const ids = preflight("下面是报错信息，python 3.12 运行环境，预期输出如下", false, "code").map((f) => f.id);
    expect(ids).not.toContain("code-source");
  });

  it("flags translation inputs for missing target language", () => {
    const ids = preflight("This paragraph needs translating into another language, please translate it for me.", false, "translation").map((f) => f.id);
    expect(ids).toContain("trans-target");
  });

  it("flags a very short translation request as missing the source text", () => {
    const ids = preflight("请翻译", false, "translation").map((f) => f.id);
    expect(ids).toContain("trans-source");
  });

  it("flags creative inputs for missing purpose and style boundary, never acceptance criteria", () => {
    const findings = preflight("帮我写一首诗", false, "creative");
    const ids = findings.map((f) => f.id);
    expect(ids).toContain("creative-audience");
    expect(ids).toContain("creative-style");
    expect(ids).not.toContain("acceptance");
  });

  it("does not flag the reader check when readers are mentioned", () => {
    const ids = preflight("面向新手读者，请写得浅显一些。", true, "writing").map((f) => f.id);
    expect(ids).not.toContain("writing-reader");
  });

  it("caps findings at 3 and puts warnings first", () => {
    const findings = preflight("写一份材料", false, "writing");
    expect(findings.length).toBe(3);
    expect(findings[0].level).toBe("warning");
  });

  it("caps generic findings at 2 for the other bucket", () => {
    const findings = preflight("今天天气很好我们出去散步吧我们一起去公园", false, "other");
    expect(findings.length).toBeLessThanOrEqual(2);
  });

  it("suppresses the context finding for translation and creative", () => {
    expect(preflight("请把这段翻译成英文", false, "translation").map((f) => f.id)).not.toContain("context");
    expect(preflight("帮我写一首诗", false, "creative").map((f) => f.id)).not.toContain("context");
  });

  it("adds the context finding only when context is missing", () => {
    const cases: Array<[PreflightTask, string]> = [
      ["code", "下面是报错信息，python 3.12 运行环境，预期输出如下"],
      ["writing", "写给新读者，语气正式，篇幅 500 字"],
      ["data", "按日期统计销售额字段，计算环比指标"],
      ["qa", "介绍一下量子计算和机器学习的主要区别"],
      ["other", "这是一段足够长的需求描述，用于触发上下文检查"],
    ];
    for (const [taskType, input] of cases) {
      expect(preflight(input, false, taskType).map((f) => f.id)).toContain("context");
      expect(preflight(input, true, taskType).map((f) => f.id)).not.toContain("context");
    }
  });
});
