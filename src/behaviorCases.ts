import type { PreflightTask } from "./preflight";

export interface BehaviorCase {
  id: string;
  text: string;
  expectTask: PreflightTask;
  expectRisk?: boolean;
  expectClarify?: boolean;
}

const DESTRUCTIVE_KEYWORDS = ["删除", "删掉", "移除", "清除", "清空", "覆盖", "格式化"];

export function hasDestructive(text: string): boolean {
  return DESTRUCTIVE_KEYWORDS.some((keyword) => text.includes(keyword));
}

export const behaviorCases: BehaviorCase[] = [
  { id: "heatstroke-symptoms", text: "中暑症状表现", expectTask: "qa", expectRisk: false },
  { id: "what-does-this-mean", text: "这是什么意思", expectTask: "qa", expectClarify: true, expectRisk: false },
  { id: "student-coder-office", text: "学生，码农，办公，了解项目和代码", expectTask: "code", expectRisk: false },
  { id: "scan-installed-skills", text: "扫描我目前装的 skill", expectTask: "other", expectRisk: false },
  { id: "keep-brainstorming-delete-others", text: "只保留 brainstorming，删除其他 superpowers skill", expectTask: "other", expectRisk: true },
  { id: "opencode-right-panel-close", text: "opencode 右侧窗口怎么关", expectTask: "qa", expectRisk: false },
  { id: "real-love-posts", text: "再找几篇真实的爱情帖子", expectTask: "other", expectRisk: false },
  { id: "hebei-college-course", text: "河北科技学院大三课程", expectTask: "other", expectRisk: false },
];
