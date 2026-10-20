import type { TruthStatus } from "./resultTruth";

const labels: Record<TruthStatus, string> = {
  idle: "等待输入",
  streaming: "正在增强",
  needs_clarification: "需要补充",
  ready: "增强完成",
  partial: "交付不完整",
  failed: "增强失败",
  incomplete: "生成已停止",
  error: "生成失败",
  unknown: "交付状态未知",
};

/**
 * CSS 类复用既有样式。
 *
 * src/styles.css 不在本工作包的所有权内，不能新增类，
 * 因此 partial / unknown 借用 needs_clarification 的琥珀色，failed 借用 error 的红色。
 * 颜色语义与 src/styles.css:107-112 一致：
 * ready=绿（成功）、needs_clarification=琥珀（不完整）、error/incomplete=红（失败）。
 */
const cssClasses: Record<TruthStatus, string> = {
  idle: "idle",
  streaming: "streaming",
  needs_clarification: "needs_clarification",
  ready: "ready",
  partial: "needs_clarification",
  failed: "error",
  incomplete: "incomplete",
  error: "error",
  unknown: "needs_clarification",
};

/**
 * 状态徽标。
 *
 * 入参是**真值状态**而不是流程状态：结构解析彻底失败时后端仍返回 status="ready"
 * （src-tauri/src/provider.rs:190-228），直接渲染流程状态会把一次失败的增强
 * 显示成"增强完成"。真值状态由 resolveResultTruth 决定（src/components/resultTruth.ts）。
 */
export function StatusBadge({ status }: { status: TruthStatus }) {
  return <span className={`status-badge ${cssClasses[status]}`}><i />{labels[status]}</span>;
}
