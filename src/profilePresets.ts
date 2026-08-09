import type { ProfileRule } from "./types";

export interface ProfilePreset {
  id: string;
  label: string;
  description: string;
  rules: ProfileRule[];
}

export const profilePresets: ProfilePreset[] = [
  { id: "novice", label: "纯AI小白", description: "回答要通俗、先给结论、不堆术语", rules: [
    { id: "novice-role", preferenceType: "identity", label: "身份", value: "第一次使用 AI 的新手", confidence: 1, explicit: true },
    { id: "novice-tone", preferenceType: "style", label: "表达", value: "通俗口语化，先给结论再解释", confidence: 1, explicit: true },
    { id: "novice-depth", preferenceType: "task", label: "术语处理", value: "遇到术语给出简短定义", confidence: 1, explicit: true },
  ]},
  { id: "student", label: "学生", description: "符合作业要求、先讲解再给方案", rules: [
    { id: "student-role", preferenceType: "identity", label: "身份", value: "学生（作业或课程任务）", confidence: 1, explicit: true },
    { id: "student-code", preferenceType: "task", label: "代码任务", value: "先解释原理和步骤，再给代码", confidence: 1, explicit: true },
    { id: "student-explain", preferenceType: "style", label: "表达", value: "分步骤讲解，标注要点", confidence: 1, explicit: true },
  ]},
  { id: "office", label: "普通办公员工", description: "书面正式、结论先行、可执行", rules: [
    { id: "office-role", preferenceType: "identity", label: "身份", value: "职场办公场景", confidence: 1, explicit: true },
    { id: "office-tone", preferenceType: "style", label: "表达", value: "书面正式，结论先行，给出可执行步骤", confidence: 1, explicit: true },
    { id: "office-fmt", preferenceType: "format", label: "格式", value: "适合汇报或邮件的结构", confidence: 1, explicit: true },
  ]},
  { id: "programmer", label: "程序员", description: "重上下文与影响范围、给可运行代码", rules: [
    { id: "prog-role", preferenceType: "identity", label: "身份", value: "软件开发者", confidence: 1, explicit: true },
    { id: "prog-code", preferenceType: "task", label: "代码任务", value: "先理解项目和影响范围，再给修改步骤", confidence: 1, explicit: true },
    { id: "prog-facts", preferenceType: "safety", label: "事实边界", value: "数字和真实事件只使用用户提供的内容", confidence: 1, explicit: true },
  ]},
];
