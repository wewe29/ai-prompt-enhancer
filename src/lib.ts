import { invoke } from "@tauri-apps/api/core";
import { Channel } from "@tauri-apps/api/core";
import { open as openDialog, save as saveDialog } from "@tauri-apps/plugin-dialog";
import { openUrl } from "@tauri-apps/plugin-opener";
import type { BackendEvent, EnhancementRequest, EnhancementResult, HistoryRecord, LocalSettings, PromptChange, ProviderConfig, Suggestion, UsageRecord } from "./types";
import { EnhancementResultSchema } from "./schemas";

export const isTauri = typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

export async function command<T>(name: string, args?: Record<string, unknown>): Promise<T> {
  if (!isTauri) throw new Error("TAURI_UNAVAILABLE");
  return invoke<T>(name, args);
}

export function blankResult(text: string): EnhancementResult {
  return {
    status: "ready",
    delivery_status: "complete",
    enhancement_level: "light",
    notices: [],
    primary_prompt: text,
    assumptions: [],
    questions: [],
    changes: [],
    suggestions: [],
    risk_flags: [],
  };
}

function mockResult(request: EnhancementRequest): EnhancementResult {
  const text = request.originalText.trim();
  return {
    status: "needs_clarification",
    delivery_status: "complete",
    enhancement_level: "clarify",
    notices: [],
    task_type: "other",
    primary_prompt: `「${text || "我的需求"}」\n\n请先确认我提供的对象或上下文。若信息不足，先列出你需要的具体信息，并基于明确假设给出临时方案。`,
    assumptions: [{ id: "a1", text: "临时版本不替用户猜测对象和上下文。", confirmed: false }],
    questions: [{ id: "q1", text: "请提供需要处理的具体对象、原文或上下文。", why_needed: "没有对象时无法保留你的真实意图。" }],
    changes: [{ id: "c1", type: "clarify", before: text, after: text, reason: "浏览器预览模式使用固定示例，不模拟真实增强。", state: "pending" }],
    suggestions: [
      { id: "s1", kind: "goal", title: "补充完成标准", purpose: "让结果更容易判断是否完成。", content: "请在结尾列出可验证的完成标准。", operation: "insert", anchor: "", applied: false },
      { id: "s2", kind: "context", title: "补充受众", purpose: "让表达深度与读者背景匹配。", content: "请按目标读者的知识水平解释，遇到术语时给出简短定义。", operation: "insert", anchor: "", applied: false },
      { id: "s3", kind: "format", title: "固定输出结构", purpose: "减少最终模型的追问和格式漂移。", content: "请先给结论，再给关键依据，最后列出下一步行动。", operation: "insert", anchor: "", applied: false },
      { id: "s4", kind: "constraint", title: "避免无关扩写", purpose: "控制长度并保持重点。", content: "只保留直接影响任务结果的信息，避免泛泛而谈。", operation: "insert", anchor: "", applied: false },
      { id: "s5", kind: "alternate_intent", title: "列出歧义分支", purpose: "适合输入可能对应多个目标的情况。", content: "如果目标存在多种合理理解，请先列出差异，再分别给出最短可行方案。", operation: "insert", anchor: "", applied: false },
    ],
    risk_flags: [],
  };
}

export async function startEnhancement(
  request: EnhancementRequest,
  onEvent: (event: BackendEvent) => void,
): Promise<void> {
  if (!isTauri) {
    const result = mockResult(request);
    const stream = result.primary_prompt;
    for (let i = 0; i < stream.length; i += 8) {
      await new Promise((resolve) => setTimeout(resolve, 18));
      onEvent({ type: "delta", data: stream.slice(i, i + 8) });
    }
    onEvent({ type: "result", result });
    onEvent({ type: "usage", usage: { inputTokens: Math.ceil((request.originalText.length + request.contextText.length) / 2), outputTokens: Math.ceil(stream.length / 2), estimatedCost: 0, monthTotal: 0 } });
    return;
  }
  const channel = new Channel<BackendEvent>();
  const done = new Promise<void>((resolve, reject) => {
    channel.onmessage = (event) => {
      if (event.type === "error") { reject(new Error(event.message ?? "增强失败")); return; }
      onEvent(event);
    };
    command<void>("enhance_prompt", { request, onEvent: channel }).then(resolve, reject);
  });
  await done;
}

export async function cancelEnhancement(): Promise<void> {
  if (isTauri) await command("cancel_enhancement");
}

export async function getProviderConfig(): Promise<ProviderConfig> {
  if (isTauri) return command<ProviderConfig>("get_provider_config");
  const stored = localStorage.getItem("promptcraft.provider");
  return stored ? JSON.parse(stored) : { baseUrl: "https://api.deepseek.com", hasApiKey: false, defaultModel: "deepseek-chat", v4FlashModelId: "deepseek-v4-flash", inputPrice: 0.001, outputPrice: 0.002, models: ["deepseek-chat"] };
}

export async function saveProviderConfig(config: ProviderConfig & { apiKey?: string }): Promise<void> {
  if (isTauri) {
    await command("save_provider_config", { config });
    return;
  }
  localStorage.setItem("promptcraft.provider", JSON.stringify({ ...config, apiKey: undefined, hasApiKey: Boolean(config.apiKey || config.hasApiKey) }));
}

export async function validateProvider(apiKey: string, baseUrl: string): Promise<void> {
  if (isTauri) return command("validate_provider", { apiKey, baseUrl });
  if (!apiKey.trim()) throw new Error("请输入 API Key");
}

export async function pickAttachments(): Promise<Array<{ name: string; path: string }>> {
  if (!isTauri) return [];
  const picked = await openDialog({
    multiple: true,
    directory: false,
    filters: [{ name: "支持的文本文件", extensions: ["txt", "md", "json", "csv", "py", "js", "ts", "tsx", "jsx", "java", "c", "cpp", "h", "rs", "go", "html", "css", "sql", "xml", "yaml", "yml", "toml", "ini", "pdf", "docx"] }],
  });
  if (!picked) return [];
  const paths = Array.isArray(picked) ? picked : [picked];
  return paths.map((path) => ({ path, name: path.split(/[\\/]/).pop() ?? path }));
}

export async function extractAttachment(path: string): Promise<{ text: string; kind: string; chars: number }> {
  if (isTauri) return command("extract_attachment", { path });
  return { text: "浏览器预览模式未启用本地文件读取。请在 Tauri 桌面版中添加附件。", kind: "text", chars: 0 };
}

export async function copyAndOpen(text: string, targetUrl: string, clearClipboard: boolean): Promise<void> {
  if (isTauri) await command("copy_and_open", { text, targetUrl, clearClipboard });
  else {
    await navigator.clipboard.writeText(text);
    window.open(targetUrl, "_blank", "noopener,noreferrer");
  }
}

export async function copyText(text: string): Promise<void> {
  if (isTauri) return command("copy_text", { text });
  return navigator.clipboard.writeText(text);
}

export async function listHistoryRecords(): Promise<HistoryRecord[]> {
  if (isTauri) return command("list_history", { query: null });
  try { return JSON.parse(localStorage.getItem("promptcraft.history") ?? "[]"); } catch { return []; }
}

export async function saveHistoryRecord(record: HistoryRecord): Promise<void> {
  if (isTauri) return command("save_history", { record });
  const existing = await listHistoryRecords();
  localStorage.setItem("promptcraft.history", JSON.stringify([record, ...existing].slice(0, 200)));
}

export async function deleteHistoryRecord(id: string): Promise<void> {
  if (isTauri) return command("delete_history", { id });
  const existing = await listHistoryRecords();
  localStorage.setItem("promptcraft.history", JSON.stringify(existing.filter((item) => item.id !== id)));
}

export async function getLocalSettings(defaults: LocalSettings): Promise<LocalSettings> {
  if (isTauri) {
    const stored = await command<LocalSettings>("get_app_settings");
    return { ...stored, profileRules: stored.profileRules.length ? stored.profileRules : defaults.profileRules };
  }
  try { return { ...defaults, ...JSON.parse(localStorage.getItem("promptcraft.localSettings") ?? "{}") }; } catch { return defaults; }
}

export async function saveLocalSettings(settings: LocalSettings): Promise<void> {
  if (isTauri) return command("save_app_settings", { settings });
  localStorage.setItem("promptcraft.localSettings", JSON.stringify(settings));
}

export async function clearAllData(): Promise<void> {
  if (isTauri) return command("clear_all_data");
  for (const key of Object.keys(localStorage)) if (key.startsWith("promptcraft.")) localStorage.removeItem(key);
}

export async function exportLocalData(): Promise<{ path: string; records: number } | null> {
  if (!isTauri) throw new Error("数据导出仅在桌面版中可用");
  const path = await saveDialog({
    title: "导出 PromptCraft 数据",
    defaultPath: `PromptCraft-backup-${new Date().toISOString().slice(0, 10)}.zip`,
    filters: [{ name: "PromptCraft 数据包", extensions: ["zip"] }],
  });
  if (!path) return null;
  const records = await command<number>("export_data", { path });
  return { path, records };
}

export async function importLocalData(): Promise<{ path: string; records: number } | null> {
  if (!isTauri) throw new Error("数据导入仅在桌面版中可用");
  const picked = await openDialog({
    title: "导入 PromptCraft 数据",
    multiple: false,
    directory: false,
    filters: [{ name: "PromptCraft 数据包", extensions: ["zip"] }],
  });
  if (!picked || Array.isArray(picked)) return null;
  const records = await command<number>("import_data", { path: picked });
  return { path: picked, records };
}

export async function openExternal(url: string): Promise<void> {
  if (isTauri) await openUrl(url);
  else window.open(url, "_blank", "noopener,noreferrer");
}

export function extractStreamedPrompt(buffer: string): string {
  const match = buffer.match(/"primary_prompt"\s*:\s*"((?:\\.|[^"\\])*)/s);
  if (!match) return buffer;
  try { return JSON.parse(`"${match[1]}"`); } catch { return match[1].replace(/\\n/g, "\n").replace(/\\"/g, '"'); }
}

export function safeParseResult(buffer: string): EnhancementResult | null {
  try {
    const parsed = JSON.parse(buffer) as EnhancementResult;
    return EnhancementResultSchema.safeParse(parsed).success ? normalizeResult(parsed) : null;
  } catch { return null; }
}

export function normalizeResult(result: EnhancementResult): EnhancementResult {
  const legacy = {
    ...result,
    delivery_status: result.delivery_status ?? "complete",
    enhancement_level: result.enhancement_level ?? (result.status === "needs_clarification" ? "clarify" : "light"),
    notices: result.notices ?? [],
  };
  return EnhancementResultSchema.parse(legacy) as EnhancementResult;
}

export function rebuildAfterKeepEssential(changes: PromptChange[], original: string): string {
  let text = original;
  for (const change of changes) {
    if (change.type === "safety" || change.state === "accepted") {
      text = applyChangeDecision(text, change, "accepted");
    }
  }
  return text;
}

export function applySuggestionToText(text: string, suggestion: Suggestion): string {
  if (suggestion.operation === "replace" && suggestion.anchor && text.includes(suggestion.anchor)) {
    return text.replace(suggestion.anchor, suggestion.content);
  }
  return [text.trim(), suggestion.content.trim()].filter(Boolean).join("\n\n");
}

export function applyChangeDecision(text: string, change: PromptChange, state: "accepted" | "rejected"): string {
  if (state === "rejected" && change.after && text.includes(change.after)) {
    return text.replace(change.after, change.before);
  }
  if (state === "accepted" && change.before && !text.includes(change.after) && text.includes(change.before)) {
    return text.replace(change.before, change.after);
  }
  return text;
}

export function pushUndoSnapshot(stack: string[], value: string, limit = 100): string[] {
  return [...stack, value].slice(-limit);
}

export const targetModels = [
  { id: "doubao", label: "豆包", url: "https://www.doubao.com/" },
  { id: "deepseek-web", label: "DeepSeek 网页版", url: "https://chat.deepseek.com/" },
  { id: "qwen", label: "千问", url: "https://tongyi.aliyun.com/" },
  { id: "minimax", label: "MiniMax", url: "https://chat.minimaxi.com/" },
  { id: "custom", label: "自定义网页", url: "" },
];
