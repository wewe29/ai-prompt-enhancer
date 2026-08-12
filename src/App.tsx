import { useEffect, useMemo, useRef, useState } from "react";
import {
  applyChangeDecision, applySuggestionToText, cancelEnhancement, clearAllData, copyAndOpen, deleteHistoryRecord, extractAttachment,
  getLocalSettings, getProviderConfig, listHistoryRecords, normalizeResult, pickAttachments,
  pushUndoSnapshot, rebuildAfterKeepEssential, saveHistoryRecord, saveLocalSettings, saveProviderConfig, startEnhancement,
  targetModels, validateProvider,
} from "./lib";
import type {
  Attachment, EnhancementRequest, EnhancementResult, EnhancementState, HistoryRecord,
  ProfileRule, ProviderConfig, UsageRecord, Verbosity,
} from "./types";
import { defaultRules } from "./defaults";
import { Sidebar, type View } from "./components/Sidebar";
import { SecurityModal } from "./components/SecurityModal";
import { SuggestionModal } from "./components/SuggestionModal";
import { EnhanceView } from "./views/EnhanceView";
import { HistoryView } from "./views/HistoryView";
import { ProfileView } from "./views/ProfileView";
import { SettingsView } from "./views/SettingsView";

const SYSTEM_PROMPT_VERSION = "promptcraft-v2.1.0";

const defaultProvider: ProviderConfig = {
  baseUrl: "https://api.deepseek.com",
  hasApiKey: false,
  defaultModel: "deepseek-chat",
  v4FlashModelId: "deepseek-v4-flash",
  inputPrice: 0.001,
  outputPrice: 0.002,
  models: ["deepseek-chat"],
};

function localFindings(text: string): string[] {
  const findings: string[] = [];
  if (/\bsk-[A-Za-z0-9_-]{12,}\b/.test(text)) findings.push("疑似 API Key，将在发送前强制遮蔽");
  if (/(password|passwd|密码)\s*[:=：]\s*\S+/i.test(text)) findings.push("疑似密码字段，将在发送前强制遮蔽");
  if (/\b\d{17}[\dXx]\b/.test(text)) findings.push("疑似身份证号");
  if (/公司源码|商业机密|未公开|内部文档/.test(text)) findings.push("疑似商业或未公开信息");
  if (/全部删|删除所有|清空|格式化|reset\s+--hard/i.test(text)) findings.push("请求可能包含不可恢复操作");
  return findings;
}

export default function App() {
  const [view, setView] = useState<View>("enhance");
  const [collapsed, setCollapsed] = useState(false);
  const [original, setOriginal] = useState("");
  const [context, setContext] = useState("");
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [output, setOutput] = useState("");
  const [result, setResult] = useState<EnhancementResult | null>(null);
  const [notices, setNotices] = useState<string[]>([]);
  const [state, setState] = useState<EnhancementState>("idle");
  const [error, setError] = useState("");
  const [model, setModel] = useState("deepseek-chat");
  const [target, setTarget] = useState("doubao");
  const [verbosity, setVerbosity] = useState<Verbosity>("standard");
  const [customInstructions, setCustomInstructions] = useState("");
  const [provider, setProvider] = useState<ProviderConfig>(defaultProvider);
  const [apiKeyDraft, setApiKeyDraft] = useState("");
  const [savingProvider, setSavingProvider] = useState(false);
  const [providerMessage, setProviderMessage] = useState("");
  const [clarificationRound, setClarificationRound] = useState(0);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [usage, setUsage] = useState<UsageRecord | null>(null);
  const [securityFindings, setSecurityFindings] = useState<string[]>([]);
  const [showSecurity, setShowSecurity] = useState(false);
  const [selectedSuggestion, setSelectedSuggestion] = useState<string | null>(null);
  const [undoStack, setUndoStack] = useState<string[]>([]);
  const [redoStack, setRedoStack] = useState<string[]>([]);
  const [history, setHistory] = useState<HistoryRecord[]>([]);
  const [rules, setRules] = useState<ProfileRule[]>(defaultRules);
  const [profileEnabled, setProfileEnabled] = useState(true);
  const [customTargetUrl, setCustomTargetUrl] = useState("");
  const [clearClipboard, setClearClipboard] = useState(false);
  const [monthlyWarningLimit, setMonthlyWarningLimit] = useState(8);
  const [monthlyLimit, setMonthlyLimit] = useState(10);
  const [settingsLoaded, setSettingsLoaded] = useState(false);
  const generationRef = useRef(0);

  useEffect(() => {
    getProviderConfig().then((config) => {
      setProvider(config);
      const models = config.models.length ? config.models : ["deepseek-chat"];
      setModel(models.includes(config.defaultModel) ? config.defaultModel : models[0]);
    }).catch(() => undefined);
    listHistoryRecords().then(setHistory).catch(() => undefined);
    getLocalSettings({ clearClipboard: false, profileEnabled: true, customTargetUrl: "", monthlyWarningLimit: 8, monthlyLimit: 10, profileRules: defaultRules }).then((settings) => {
      setClearClipboard(settings.clearClipboard); setProfileEnabled(settings.profileEnabled); setCustomTargetUrl(settings.customTargetUrl); setMonthlyWarningLimit(settings.monthlyWarningLimit); setMonthlyLimit(settings.monthlyLimit); setRules(settings.profileRules); setSettingsLoaded(true);
    }).catch(() => setSettingsLoaded(true));
  }, []);
  useEffect(() => {
    if (settingsLoaded) saveLocalSettings({ clearClipboard, profileEnabled, customTargetUrl, monthlyWarningLimit, monthlyLimit, profileRules: rules }).catch(() => undefined);
  }, [settingsLoaded, clearClipboard, profileEnabled, customTargetUrl, monthlyWarningLimit, monthlyLimit, rules]);

  const totalChars = context.length + attachments.reduce((sum, item) => sum + item.chars, 0);
  const selectedTarget = targetModels.find((item) => item.id === target) ?? targetModels[0];
  const currentTarget = target === "custom" ? { ...selectedTarget, url: customTargetUrl.trim() } : selectedTarget;
  const selectedSuggestionData = result?.suggestions.find((item) => item.id === selectedSuggestion);

  const commitOutput = (next: string) => {
    setOutput((current) => {
      if (next !== current) { setUndoStack((stack) => pushUndoSnapshot(stack, current)); setRedoStack([]); }
      return next;
    });
  };

  const undo = () => {
    const previous = undoStack.at(-1);
    if (previous === undefined) return;
    setUndoStack((stack) => stack.slice(0, -1));
    setRedoStack((stack) => pushUndoSnapshot(stack, output));
    setOutput(previous);
  };

  const redo = () => {
    const next = redoStack.at(-1);
    if (next === undefined) return;
    setRedoStack((stack) => stack.slice(0, -1));
    setUndoStack((stack) => pushUndoSnapshot(stack, output));
    setOutput(next);
  };

  const addAttachments = async () => {
    setError("");
    try {
      const picked = await pickAttachments();
      if (attachments.length + picked.length > 5) throw new Error("一次最多添加 5 个文件");
      for (const file of picked) {
        if (attachments.some((item) => item.path === file.path)) continue;
        const extracted = await extractAttachment(file.path);
        setAttachments((items) => [...items, { id: crypto.randomUUID(), name: file.name, path: file.path, kind: extracted.kind, chars: extracted.chars, extractedText: extracted.text, sourceDeleted: true }]);
      }
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
  };

  const persistHistory = (enhanced: string, opts?: { deliveryStatus?: "complete" | "partial" | "fallback"; enhancementLevel?: string; promptVersion?: string }) => {
    const item: HistoryRecord = { id: crypto.randomUUID(), title: original.trim().slice(0, 32) || "未命名提示词", original, enhanced, createdAt: new Date().toISOString(), model, target };
    if (opts?.deliveryStatus) item.deliveryStatus = opts.deliveryStatus;
    if (opts?.enhancementLevel) item.enhancementLevel = opts.enhancementLevel;
    if (opts?.promptVersion) item.promptVersion = opts.promptVersion;
    setHistory((items) => [item, ...items].slice(0, 200));
    saveHistoryRecord(item).catch(() => undefined);
  };

  const runEnhance = async (confirmed = false, overrideContext?: string, overrideRound?: number, overrideAnswers?: Array<{ questionId: string; answer: string }>) => {
    if (!original.trim()) { setError("请先输入需要增强的提示词"); return; }
    const combined = [original, overrideContext ?? context, ...attachments.map((item) => item.extractedText)].join("\n");
    const findings = localFindings(combined);
    if (findings.length && !confirmed) { setSecurityFindings(findings); setShowSecurity(true); return; }
    const estimatedInputTokens = Math.ceil(combined.length / 1.8);
    const estimatedOutputTokens = verbosity === "concise" ? 400 : verbosity === "standard" ? 900 : 1_800;
    const estimatedRequestCost = estimatedInputTokens / 1_000 * provider.inputPrice + estimatedOutputTokens / 1_000 * provider.outputPrice;
    const projectedMonthTotal = (usage?.monthTotal ?? 0) + estimatedRequestCost;
    if (monthlyLimit > 0 && projectedMonthTotal > monthlyLimit) { setError(`预计本次请求会使本月费用达到约 ¥${projectedMonthTotal.toFixed(2)}，超过强制额度 ¥${monthlyLimit.toFixed(2)}，已阻止调用。`); return; }

    const generationId = ++generationRef.current;
    setShowSecurity(false); setError(monthlyWarningLimit > 0 && projectedMonthTotal >= monthlyWarningLimit ? `费用提醒：预计本次后本月累计约 ¥${projectedMonthTotal.toFixed(2)}，仍允许继续。` : ""); setState("streaming"); setResult(null); setOutput(""); setUndoStack([]); setRedoStack([]); setNotices([]);
    const request: EnhancementRequest = {
      originalText: original,
      contextText: overrideContext ?? context,
      attachments: attachments.map((item) => ({ name: item.name, text: item.extractedText })),
      model: model === "v4-flash" ? provider.v4FlashModelId : model,
      targetModel: currentTarget.label,
      verbosity,
      customInstructions: verbosity === "custom" ? customInstructions : undefined,
      clarificationRound: overrideRound ?? clarificationRound,
      clarificationAnswers: overrideAnswers ?? Object.entries(answers).map(([questionId, answer]) => ({ questionId, answer })),
      profileSummary: profileEnabled ? rules.filter((rule) => rule.explicit || rule.confidence >= 0.6).map((rule) => `${rule.label}：${rule.value}`) : [],
    };
    try {
      await startEnhancement(request, (event) => {
        if (generationId !== generationRef.current) return;
        if (event.type === "delta" && event.data) setOutput((value) => value + event.data);
        if (event.type === "result" && event.result) {
          let normalized: EnhancementResult;
          try {
            normalized = normalizeResult(event.result);
          } catch {
            setOutput((current) => current || original);
            setState("incomplete");
            setError("模型返回的字段超出预期，已保留你的输入；可点击重新生成。");
            return;
          }
          setResult(normalized);
          const nextNotices = [...new Set(normalized.notices ?? [])];
          if (normalized.delivery_status === "fallback" && nextNotices.length === 0) {
            nextNotices.push("已保留原文（增强服务未返回可用结构）");
          }
          setNotices(nextNotices);
          setOutput(normalized.primary_prompt);
          setState(normalized.status === "needs_clarification" ? "needs_clarification" : "ready");
          persistHistory(normalized.primary_prompt, { deliveryStatus: normalized.delivery_status, enhancementLevel: normalized.enhancement_level, promptVersion: SYSTEM_PROMPT_VERSION });
        }
        if (event.type === "status" && event.data === "retrying_structure") setOutput("");
        if (event.type === "usage" && event.usage) setUsage(event.usage);
      });
    } catch (cause) {
      if (generationId !== generationRef.current) return;
      setState("error"); setError(cause instanceof Error ? cause.message : String(cause));
    }
  };

  const stop = async () => {
    generationRef.current += 1;
    await cancelEnhancement().catch(() => undefined);
    setState("incomplete");
  };

  const submitClarification = () => {
    if (!result?.questions.length) return;
    const answered = result.questions.filter((q) => answers[q.id]?.trim()).map((q) => ({ questionId: q.id, answer: answers[q.id].trim() }));
    const block = answered.map((item) => `问题：${result.questions.find((q) => q.id === item.questionId)?.text}\n回答：${item.answer}`).join("\n\n");
    const nextContext = [context, `第 ${clarificationRound + 1} 轮澄清：\n${block || "用户选择跳过本轮。"}`].filter(Boolean).join("\n\n");
    const nextRound = Math.min(3, clarificationRound + 1);
    setContext(nextContext); setClarificationRound(nextRound); setAnswers({});
    runEnhance(true, nextContext, nextRound, answered);
  };

  const applySuggestion = () => {
    if (!selectedSuggestionData || selectedSuggestionData.applied) return;
    const next = applySuggestionToText(output, selectedSuggestionData);
    commitOutput(next);
    setResult((current) => current ? { ...current, suggestions: current.suggestions.map((item) => item.id === selectedSuggestionData.id ? { ...item, applied: true } : item) } : current);
    setSelectedSuggestion(null);
  };

  const changeState = (id: string, nextState: "accepted" | "rejected") => {
    const change = result?.changes.find((item) => item.id === id);
    if (!change) return;
    const next = applyChangeDecision(output, change, nextState);
    if (next !== output) commitOutput(next);
    setResult((current) => current ? { ...current, changes: current.changes.map((item) => item.id === id ? { ...item, state: nextState } : item) } : current);
  };

  const hasActionableChanges = result?.changes.some((change) => change.state === "pending" || change.state === "accepted") ?? false;

  const restoreOriginal = () => { if (output !== original) commitOutput(original); };

  const keepEssentialEdits = () => {
    if (!result) return;
    commitOutput(rebuildAfterKeepEssential(result.changes, original));
  };

  const regenerate = () => runEnhance();

  const handleCopyOpen = async () => {
    if (!output.trim()) return;
    if (!currentTarget.url) { setError("请先填写自定义目标网页地址"); return; }
    try { await copyAndOpen(output, currentTarget.url, clearClipboard); }
    catch (cause) { setError(`复制失败：${cause instanceof Error ? cause.message : String(cause)}`); }
  };

  const saveProvider = async (modelsOverride?: string[]) => {
    setSavingProvider(true); setProviderMessage("");
    try {
      if (apiKeyDraft) await validateProvider(apiKeyDraft, provider.baseUrl);
      const models = [...new Set(modelsOverride ?? provider.models)].filter((id) => id.trim());
      if (!models.length) throw new Error("自定义模型列表不能为空");
      if (!models.includes(provider.defaultModel)) models.unshift(provider.defaultModel);
      const next = { ...provider, models, hasApiKey: Boolean(apiKeyDraft || provider.hasApiKey), apiKey: apiKeyDraft || undefined };
      await saveProviderConfig(next); setProvider({ ...provider, models, hasApiKey: next.hasApiKey }); setApiKeyDraft(""); setProviderMessage("连接验证成功，配置已保存");
    } catch (cause) { setProviderMessage(cause instanceof Error ? cause.message : String(cause)); }
    finally { setSavingProvider(false); }
  };

  const mainContent = useMemo(() => {
    if (view === "history") return <HistoryView items={history} onRestore={(item) => { setOriginal(item.original); setOutput(item.enhanced); setModel(item.model); setTarget(item.target); setState("ready"); setView("enhance"); }} onDelete={(id) => { setHistory((items) => items.filter((item) => item.id !== id)); deleteHistoryRecord(id).catch(() => undefined); }} />;
    if (view === "profile") return <ProfileView rules={rules} setRules={setRules} enabled={profileEnabled} setEnabled={setProfileEnabled} />;
    if (view === "settings") return <SettingsView provider={provider} setProvider={setProvider} apiKeyDraft={apiKeyDraft} setApiKeyDraft={setApiKeyDraft} saveProvider={saveProvider} saving={savingProvider} message={providerMessage} clearClipboard={clearClipboard} setClearClipboard={setClearClipboard} monthlyWarningLimit={monthlyWarningLimit} setMonthlyWarningLimit={setMonthlyWarningLimit} monthlyLimit={monthlyLimit} setMonthlyLimit={setMonthlyLimit} onClearData={async () => { await clearAllData(); setHistory([]); setRules(defaultRules); setProfileEnabled(true); setCustomTargetUrl(""); setClearClipboard(false); setMonthlyWarningLimit(8); setMonthlyLimit(10); setProvider(defaultProvider); }} />;
    return null;
  }, [view, history, rules, profileEnabled, provider, apiKeyDraft, savingProvider, providerMessage, clearClipboard, monthlyWarningLimit, monthlyLimit]);

  return <div className="app-shell">
    <Sidebar view={view} onView={setView} collapsed={collapsed} onToggle={() => setCollapsed((value) => !value)} />
    {view !== "enhance" ? mainContent : <EnhanceView model={model} setModel={setModel} provider={provider} target={target} setTarget={setTarget} verbosity={verbosity} setVerbosity={setVerbosity} state={state} setState={setState} stop={stop} runEnhance={runEnhance} customInstructions={customInstructions} setCustomInstructions={setCustomInstructions} customTargetUrl={customTargetUrl} setCustomTargetUrl={setCustomTargetUrl} error={error} setError={setError} original={original} setOriginal={setOriginal} context={context} setContext={setContext} totalChars={totalChars} attachments={attachments} setAttachments={setAttachments} addAttachments={addAttachments} output={output} commitOutput={commitOutput} undo={undo} redo={redo} undoStack={undoStack} redoStack={redoStack} result={result} setResult={setResult} usage={usage} handleCopyOpen={handleCopyOpen} currentTarget={currentTarget} clarificationRound={clarificationRound} answers={answers} setAnswers={setAnswers} submitClarification={submitClarification} changeState={changeState} setSelectedSuggestion={setSelectedSuggestion} showSecurity={showSecurity} setShowSecurity={setShowSecurity} selectedSuggestionData={selectedSuggestionData ?? null} notices={notices} deliveryStatus={result?.delivery_status} restoreOriginal={restoreOriginal} keepEssentialEdits={keepEssentialEdits} hasActionableChanges={hasActionableChanges} regenerate={regenerate} />}
    {showSecurity && <SecurityModal findings={securityFindings} onCancel={() => setShowSecurity(false)} onConfirm={() => runEnhance(true)} />}
    {selectedSuggestionData && <SuggestionModal suggestion={selectedSuggestionData} onCancel={() => setSelectedSuggestion(null)} onApply={applySuggestion} />}
  </div>;
}
