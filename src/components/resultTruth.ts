import type { EnhancementResult, EnhancementState } from "../types";

/** 后端 delivery_status 的取值域（src/types.ts:52）。 */
export type DeliveryStatus = "complete" | "partial" | "fallback";

/**
 * 面板标题栏状态徽标的真值状态。
 *
 * 与 EnhancementState 分开，是因为两者描述的是**不同的东西**：
 * EnhancementState 描述流程走到哪一步，delivery_status 描述**这一版交付物是什么**。
 *
 * 合并成一个字段就会说谎：结构解析彻底失败时后端仍然返回 status="ready"
 * （src-tauri/src/provider.rs:190-228，命令最终 Ok(())），
 * 于是状态徽标会把一次彻底失败的增强渲染成"增强完成"。
 */
export type TruthStatus =
  | "idle"
  | "streaming"
  | "needs_clarification"
  | "ready"
  | "partial"
  | "failed"
  | "incomplete"
  | "error"
  | "unknown";

/**
 * 档位这一行的事实。
 *
 * fromModel = true 表示这一行陈述的是**模型真实做过的判断**；
 * false 表示本次交付没有可靠的档位来源，必须显示为"不可判定"。
 *
 * 依据：docs/v040-缺失语义约定.md R6（呈现层必须显示"不可判定"）与 §4.5
 * （失败 → 显示失败原因，禁止显示"无需修改"这类判断性文案）。
 */
export type LevelTruth = {
  /** 展示文案。 */
  label: string;
  /** 这一行是不是模型真实判断的结果。 */
  fromModel: boolean;
};

export type ResultTruth = {
  /** 状态徽标的真值状态。 */
  status: TruthStatus;
  /** 摘要区"增强等级"这一行。 */
  level: LevelTruth;
  /** 标题栏那枚 chip 的文案；null = 不显示。 */
  chip: string | null;
};

/**
 * 档位文案表。**只允许在模型真的做过判断时使用这张表。**
 *
 * 后端两个构造器把档位硬编码成 "none"：
 *   src-tauri/src/provider.rs:876  partial_result
 *   src-tauri/src/provider.rs:894  fallback_result
 * 那两个 "none" 是占位值，不是判断，不能进这张表。
 */
const levelLabels: Record<string, string> = { none: "无需明显修改", light: "轻度增强", clarify: "需要澄清" };

/** 档位不可判定时的统一文案。 */
export const UNJUDGEABLE_LABEL = "不可判定";

function hasLevelLabel(value: unknown): value is keyof typeof levelLabels {
  return typeof value === "string" && Object.prototype.hasOwnProperty.call(levelLabels, value);
}

/**
 * 把一次增强的真实结果解析成界面可以照着说的三件事。
 *
 * 规则只有四条，全部有 代码依据：
 *
 * 1. **fallback** —— 两个构造器都把档位硬编码成 none，模型从未做过判断。
 *    ⇒ 档位不可判定；状态 = 增强失败。
 * 2. **partial** —— 来源有两种且前端无法区分：
 *      a) provider.rs:192-200：模型返回了结果，只是没过完整校验，**档位是模型的真实判断**；
 *      b) provider.rs:218 partial_result：档位被硬编码成 none，**不是判断**。
 *    ⇒ 只有 light / clarify 可断言（两个构造器都不会产出它们）；none 一律不可判定。
 * 3. **complete** —— 档位来自模型，可以陈述判断。
 * 4. **delivery_status 缺失** —— fail-closed，不许当成完整交付（R3）。
 */
export function resolveResultTruth(input: {
  result: EnhancementResult | null;
  state: EnhancementState;
  deliveryStatus?: DeliveryStatus;
}): ResultTruth {
  const { result, state, deliveryStatus } = input;

  // 没有结果对象（例如从历史记录恢复：App.tsx:306 只回填了原文与结果文本）。
  // 这时没有交付物可评，保留流程状态，不引入交付判断。
  if (!result) {
    return { status: state, level: { label: UNJUDGEABLE_LABEL, fromModel: false }, chip: null };
  }

  const raw = result.enhancement_level;
  const levelKnown = hasLevelLabel(raw);
  const label = levelKnown ? levelLabels[raw] : UNJUDGEABLE_LABEL;

  // 交付状态：优先取结果对象上的字段。
  const delivery: DeliveryStatus | undefined = result.delivery_status ?? deliveryStatus;

  // 状态徽标只覆盖"ready"这一种情况：其余状态下流程状态才是更有信息量的那个。
  const statusOf = (fallbackStatus: TruthStatus): TruthStatus => (state === "ready" ? fallbackStatus : state);

  if (delivery === "fallback") {
    // 模型从未做过判断：一个字都不能提"判断"。
    return {
      status: statusOf("failed"),
      level: { label: UNJUDGEABLE_LABEL, fromModel: false },
      chip: null,
    };
  }

  if (delivery === "partial") {
    const fromModel = levelKnown && raw !== "none";
    return {
      status: statusOf("partial"),
      level: fromModel ? { label, fromModel: true } : { label: UNJUDGEABLE_LABEL, fromModel: false },
      chip: null,
    };
  }

  if (delivery === undefined) {
    // 交付状态缺失：宁可说"不可判定"，也不许默认成"完整交付"。
    return {
      status: statusOf("unknown"),
      level: { label: UNJUDGEABLE_LABEL, fromModel: false },
      chip: null,
    };
  }

  // complete：档位来自模型，可以陈述判断。
  return {
    status: statusOf("ready"),
    level: { label, fromModel: levelKnown },
    chip: state === "streaming" || !levelKnown || raw !== "none"
      ? null
      : "增强器判断无需修改，建议直接使用原文",
  };
}
