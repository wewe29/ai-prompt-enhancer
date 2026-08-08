import type { ProfileRule } from "./types";

export const defaultRules: ProfileRule[] = [
  { id: "role", preferenceType: "identity", label: "身份", value: "学生、程序开发、办公", confidence: 1, explicit: true },
  { id: "code", preferenceType: "task", label: "代码任务", value: "先理解项目和影响范围，再给修改步骤", confidence: 1, explicit: true },
  { id: "facts", preferenceType: "safety", label: "事实边界", value: "数字和真实事件只使用用户提供的内容", confidence: 1, explicit: true },
];
