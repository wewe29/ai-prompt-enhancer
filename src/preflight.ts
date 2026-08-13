export type PreflightTask = "code" | "creative" | "writing" | "qa" | "data" | "translation" | "other";

export interface PreflightFinding { id: string; level: "info" | "warning"; message: string; target: "original" | "context"; }

const TRANSLATION_KEYWORDS = ["翻译", "译成", "翻成", "英文", "translate"];
const CREATIVE_KEYWORDS = ["写一首", "诗", "文案", "广告", "创意", "写个故事", "小说", "slogan", "创作"];
const DATA_KEYWORDS = ["数据", "报表", "销售额", "统计", "指标", "环比", "sql"];
const CODE_KEYWORDS = ["```", "代码", "函数", "报错", "error", "bug", "debug", "python", "js", "sql", "编译", "运行", "内存", "git"];
const WRITING_KEYWORDS = ["邮件", "周报", "公告", "文档", "汇报", "总结", "文章", "说明书", "改写", "报告"];
const QA_KEYWORDS = ["是什么意思", "什么区别", "怎么", "为什么", "介绍", "解释", "查", "了解", "对比", "症状", "?"];

// Precedence: translation -> creative -> data -> code -> writing -> qa -> other.
// Data is checked before code so "用 python 统计销售额" is data while "python 报错" is code;
// code is checked before qa so "为什么代码报错" is code, not qa.
export function classifyTask(text: string): PreflightTask {
  const lower = text.toLowerCase();
  if (TRANSLATION_KEYWORDS.some((keyword) => lower.includes(keyword))) return "translation";
  if (CREATIVE_KEYWORDS.some((keyword) => lower.includes(keyword))) return "creative";
  if (DATA_KEYWORDS.some((keyword) => lower.includes(keyword))) return "data";
  if (CODE_KEYWORDS.some((keyword) => lower.includes(keyword))) return "code";
  if (WRITING_KEYWORDS.some((keyword) => lower.includes(keyword))) return "writing";
  if (QA_KEYWORDS.some((keyword) => lower.includes(keyword))) return "qa";
  return "other";
}

const CONTEXT_FINDING: PreflightFinding = { id: "context", level: "info", message: "没有粘贴上下文；如果任务依赖背景信息，补充后效果更稳定。", target: "context" };

export function preflight(text: string, hasContext: boolean, taskType: PreflightTask = "other"): PreflightFinding[] {
  const findings: PreflightFinding[] = [];
  const trimmed = text.trim();
  if (!trimmed) return [];
  const chars = trimmed.length;
  // Only the most impactful items are shown, sorted warning-first then info. "other" is capped tighter.
  const cap = taskType === "other" ? 2 : 3;
  const push = (finding: PreflightFinding) => { if (findings.length < cap) findings.push(finding); };

  switch (taskType) {
    case "code":
      if (!/```|报错|error/i.test(trimmed)) push({ id: "code-source", level: "warning", message: "缺少代码或错误信息", target: "original" });
      if (!/运行环境|环境|版本|预期|输入|输出|结果/i.test(trimmed)) push({ id: "code-env", level: "info", message: "请说明运行环境和预期行为", target: "original" });
      break;
    case "translation":
      if (chars < 15) push({ id: "trans-source", level: "warning", message: "缺少要翻译的原文", target: "original" });
      if (!/译成|翻译成|成英文|成中文|成日文|成法文|成德文|成韩文|中文|英文|日文|法文|德文|韩文/i.test(trimmed)) push({ id: "trans-target", level: "info", message: "请说明目标语言", target: "original" });
      break;
    case "creative":
      if (!/用途|受众|给.*看|写给|面向|宣传|推广|目标用户/i.test(trimmed)) push({ id: "creative-audience", level: "warning", message: "请补充用途或目标受众", target: "original" });
      if (!/风格|语气|正式|口语|轻松|严肃|诙谐|幽默|简约|华丽/i.test(trimmed)) push({ id: "creative-style", level: "info", message: "请说明风格或语气边界", target: "original" });
      break;
    case "writing":
      if (!/读者|写给|面向|给.*看|针对|目标受众/i.test(trimmed)) push({ id: "writing-reader", level: "warning", message: "请说明目标读者", target: "original" });
      if (!/用途|语气|正式|口语|亲切|严肃|目的|场景/i.test(trimmed)) push({ id: "writing-tone", level: "info", message: "请说明用途或语气", target: "original" });
      if (!/字数|篇幅|不超过|控制在|多少字|长短|页|简洁|详细/i.test(trimmed)) push({ id: "writing-length", level: "info", message: "请补充篇幅要求", target: "original" });
      break;
    case "qa":
      if (chars < 10) push({ id: "qa-object", level: "warning", message: "缺少要解释或查询的具体对象", target: "original" });
      if (/数字|数据|价格|多少|日期|最新|人数|成本|规模|真实|事件/i.test(trimmed)) push({ id: "qa-factual", level: "info", message: "如涉及数字/事实，请注明是否需要确认真实性", target: "original" });
      break;
    case "data":
      if (!/字段|口径|维度|指标|列|行|统计口径|分组|明细/i.test(trimmed)) push({ id: "data-fields", level: "warning", message: "缺少数据字段或统计口径", target: "original" });
      if (!/时间|最近|今年|本月|去年|季度|环比|同比|期间|截至|日期/i.test(trimmed)) push({ id: "data-time", level: "info", message: "请说明数据时间范围", target: "original" });
      if (!/指标|期望|目标|结果|要看|得到|需求/i.test(trimmed)) push({ id: "data-metric", level: "info", message: "请补充期望的结果指标", target: "original" });
      break;
    default:
      if (chars < 10 && !/请|帮我|怎么/.test(trimmed)) push({ id: "too-short", level: "warning", message: "内容很短，只补充一句目标模型就能给出更有用的回答。", target: "original" });
      if (!/字数|篇幅|不超过|限制在|表格|列出|结构|格式|分段|大纲/.test(trimmed)) push({ id: "format", level: "info", message: "没有输出要求（格式、篇幅、结构），目标模型可能按自己的习惯组织。", target: "original" });
  }

  // Context matters for code/writing/qa/data and the generic "other" bucket; not for translation/creative.
  if (taskType !== "translation" && taskType !== "creative" && !hasContext) push(CONTEXT_FINDING);
  return findings;
}
