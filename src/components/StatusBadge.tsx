import type { EnhancementState } from "../types";

export function StatusBadge({ state }: { state: EnhancementState }) {
  const labels: Record<EnhancementState, string> = { idle: "等待输入", streaming: "正在增强", needs_clarification: "需要补充", ready: "增强完成", incomplete: "生成已停止", error: "生成失败" };
  return <span className={`status-badge ${state}`}><i />{labels[state]}</span>;
}
