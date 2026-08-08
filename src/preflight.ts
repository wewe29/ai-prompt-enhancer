export interface PreflightFinding { id: string; level: "info" | "warning"; message: string; target: "original" | "context"; }

export function preflight(text: string, hasContext: boolean): PreflightFinding[] {
  const findings: PreflightFinding[] = [];
  const trimmed = text.trim();
  if (!trimmed) return [];
  const chars = trimmed.length;
  if (chars < 10 && !/请|帮我|怎么/.test(trimmed)) findings.push({ id: "too-short", level: "warning", message: "内容很短，只补充一句目标模型就能给出更有用的回答。", target: "original" });
  if (!/面向|给.*看|写给|针对|对.*说|目标受众|读者/.test(trimmed)) findings.push({ id: "audience", level: "info", message: "没有提到受众（给谁看/谁使用），补充后表达深度会更合适。", target: "original" });
  if (!/字数|篇幅|不超过|限制在|表格|列出|结构|格式|分段|大纲/.test(trimmed)) findings.push({ id: "format", level: "info", message: "没有输出要求（格式、篇幅、结构），目标模型可能按自己的习惯组织。", target: "original" });
  if (!/不要|必须|只能|禁止|保留|排除|避免|约束|限制/.test(trimmed)) findings.push({ id: "constraint", level: "info", message: "没有约束或禁止项，如果存在一定不能出现的内容，建议补充。", target: "original" });
  if (!hasContext && chars >= 10) findings.push({ id: "context", level: "info", message: "没有粘贴上下文；如果任务依赖背景信息，补充后效果更稳定。", target: "context" });
  return findings;
}
