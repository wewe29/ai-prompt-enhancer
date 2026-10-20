import { describe, expect, it } from "vitest";
import { resolveResultTruth, UNJUDGEABLE_LABEL } from "./resultTruth";
import type { EnhancementResult, EnhancementState } from "../types";

function result(overrides: Partial<EnhancementResult> = {}): EnhancementResult {
  return {
    status: "ready",
    delivery_status: "complete",
    enhancement_level: "light",
    notices: [],
    task_type: "other",
    primary_prompt: "帮我写一封请假邮件",
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

function truth(resultValue: EnhancementResult | null, state: EnhancementState = "ready") {
  return resolveResultTruth({ result: resultValue, state, deliveryStatus: resultValue?.delivery_status });
}

describe("UI 真值：交付状态决定档位能不能被陈述", () => {
  it("fallback：后端把档位硬编码成 none，界面不得说模型判断过", () => {
    // 后端两个构造器把档位写死成 none：
    //   src-tauri/src/provider.rs:876 partial_result
    //   src-tauri/src/provider.rs:894 fallback_result
    const t = truth(result({ delivery_status: "fallback", enhancement_level: "none" }));
    expect(t.level.label).toBe(UNJUDGEABLE_LABEL);
    expect(t.level.fromModel).toBe(false);
    expect(t.status).toBe("failed");
    expect(t.chip).toBeNull();
  });

  it("fallback：即使档位是 light/clarify 也不许陈述（fallback 永远不会产出这两个值）", () => {
    // 防的是"后端哪天把 fallback 改成带档位"时前端无条件放行。
    for (const level of ["light", "clarify"] as const) {
      const t = truth(result({ delivery_status: "fallback", enhancement_level: level }));
      expect(t.level.fromModel).toBe(false);
      expect(t.status).toBe("failed");
    }
  });

  it("partial 且档位为 none：无法区分占位值与真实判断，一律不可判定", () => {
    // 两条 partial 来源，前端分不出来：
    //   provider.rs:192-200 保留模型档位（可能是真的 none）
    //   provider.rs:218 partial_result 档位被硬编码成 none（不是判断）
    const t = truth(result({ delivery_status: "partial", enhancement_level: "none" }));
    expect(t.level.label).toBe(UNJUDGEABLE_LABEL);
    expect(t.level.fromModel).toBe(false);
    expect(t.status).toBe("partial");
  });

  it("partial 且档位为 light/clarify：必然来自模型，可以陈述", () => {
    // 两个构造器都只产出 none，所以 light/clarify 一定是模型返回值。
    const t = truth(result({ delivery_status: "partial", enhancement_level: "light" }));
    expect(t.level.label).toBe("轻度增强");
    expect(t.level.fromModel).toBe(true);
    expect(t.status).toBe("partial");
  });

  it("complete：档位来自模型，可以陈述判断", () => {
    const none = truth(result({ delivery_status: "complete", enhancement_level: "none" }));
    expect(none.level.label).toBe("无需明显修改");
    expect(none.level.fromModel).toBe(true);
    expect(none.status).toBe("ready");
    expect(none.chip).toBe("增强器判断无需修改，建议直接使用原文");

    const light = truth(result({ delivery_status: "complete", enhancement_level: "light" }));
    expect(light.level.label).toBe("轻度增强");
    expect(light.chip).toBeNull();
  });

  it("交付状态缺失：fail-closed，不得默认成完整交付", () => {
    const t = truth(result({ delivery_status: undefined }));
    expect(t.level.label).toBe(UNJUDGEABLE_LABEL);
    expect(t.status).toBe("unknown");
  });

  it("档位字段缺失：不可判定，但交付状态仍然生效", () => {
    const t = truth(result({ delivery_status: "fallback", enhancement_level: undefined }));
    expect(t.level.label).toBe(UNJUDGEABLE_LABEL);
    expect(t.status).toBe("failed");
  });

  it("没有结果对象时保留流程状态，不凭空产生交付判断", () => {
    // 从历史记录恢复时 result 为 null（App.tsx:306 只回填原文与结果文本）。
    const t = truth(null, "ready");
    expect(t.status).toBe("ready");
    expect(t.level.label).toBe(UNJUDGEABLE_LABEL);
    expect(t.chip).toBeNull();
  });

  it("非 ready 的流程状态优先于交付状态", () => {
    // 正在流式时不能把状态改成 failed/partial。
    expect(truth(result({ delivery_status: "fallback" }), "streaming").status).toBe("streaming");
    expect(truth(result({ delivery_status: "partial" }), "streaming").status).toBe("streaming");
    // 其他错误态也不被覆盖。
    expect(truth(result({ delivery_status: "fallback" }), "error").status).toBe("error");
    expect(truth(result({ delivery_status: "fallback" }), "needs_clarification").status).toBe("needs_clarification");
  });

  it("流式过程中不出 chip", () => {
    const t = truth(result({ delivery_status: "complete", enhancement_level: "none" }), "streaming");
    expect(t.chip).toBeNull();
  });
});
