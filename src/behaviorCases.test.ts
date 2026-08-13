import { describe, expect, it } from "vitest";
import { behaviorCases, hasDestructive } from "./behaviorCases";
import { classifyTask, preflight } from "./preflight";

const ALL_TASKS = ["code", "creative", "writing", "qa", "data", "translation", "other"] as const;

describe("behavior boundaries (spec §5.6)", () => {
  it("covers all 8 spec example requests", () => {
    expect(behaviorCases).toHaveLength(8);
  });

  it.each(behaviorCases)("$id: task classification boundary -> $expectTask", ({ text, expectTask }) => {
    expect(classifyTask(text)).toBe(expectTask);
  });

  it.each(behaviorCases)("$id: classifyTask never throws and returns one of the 7 tasks", ({ text }) => {
    const task = classifyTask(text);
    expect(ALL_TASKS).toContain(task);
  });

  it.each(behaviorCases.filter((c) => c.expectRisk))("$id: risk request contains a destructive keyword", ({ text }) => {
    expect(hasDestructive(text)).toBe(true);
  });

  it.each(behaviorCases.filter((c) => c.expectRisk === false))("$id: non-risk request has no destructive keyword", ({ text }) => {
    expect(hasDestructive(text)).toBe(false);
  });

  it.each(behaviorCases.filter((c) => c.expectClarify))("$id: missing-object request surfaces a warning finding", ({ text, expectTask }) => {
    const findings = preflight(text, false, expectTask);
    expect(findings.some((f) => f.level === "warning")).toBe(true);
  });
});
