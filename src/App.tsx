import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle, ArrowLeft, ArrowRight, Check, ChevronDown, Clipboard, Clock3,
  FileCode2, FilePlus2, History, KeyRound, LoaderCircle, MessageSquareText,
  PanelLeftClose, Plus, RefreshCw, RotateCcw, RotateCw, Send, Settings,
  ShieldCheck, Sparkles, Square, Trash2, Undo2, UserRoundCog, WandSparkles, X,
} from "lucide-react";
import {
  applyChangeDecision, applySuggestionToText, cancelEnhancement, clearAllData, copyAndOpen, copyText, deleteHistoryRecord, extractAttachment,
  exportLocalData, getLocalSettings, getProviderConfig, importLocalData, listHistoryRecords, pickAttachments,
  normalizeResult, pushUndoSnapshot, saveHistoryRecord, saveLocalSettings, saveProviderConfig, startEnhancement,
  targetModels, validateProvider,
} from "./lib";
import type {
  Attachment, EnhancementRequest, EnhancementResult, EnhancementState, HistoryRecord,
  ProfileRule, ProviderConfig, UsageRecord, Verbosity,
} from "./types";

type View = "enhance" | "history" | "profile" | "settings";
const defaultProvider: ProviderConfig = {
  baseUrl: "https://api.deepseek.com",
  hasApiKey: false,
  defaultModel: "deepseek-chat",
  v4FlashModelId: "deepseek-v4-flash",
  inputPrice: 0.001,
  outputPrice: 0.002,
};

const defaultRules: ProfileRule[] = [
  { id: "role", preferenceType: "identity", label: "身份", value: "学生、程序开发、办公", confidence: 1, explicit: true },
  { id: "code", preferenceType: "task", label: "代码任务", value: "先理解项目和影响范围，再给修改步骤", confidence: 1, explicit: true },
  { id: "facts", preferenceType: "safety", label: "事实边界", value: "数字和真实事件只使用用户提供的内容", confidence: 1, explicit: true },
];

const detailLabels: Record<Verbosity, string> = { concise: "简洁", standard: "标准", deep: "深入", custom: "自定义" };

function localFindings(text: string): string[] {
  const findings: string[] = [];
  if (/\bsk-[A-Za-z0-9_-]{12,}\b/.test(text)) findings.push("疑似 API Key，将在发送前强制遮蔽");
  if (/(password|passwd|密码)\s*[:=：]\s*\S+/i.test(text)) findings.push("疑似密码字段，将在发送前强制遮蔽");
  if (/\b\d{17}[\dXx]\b/.test(text)) findings.push("疑似身份证号");
  if (/公司源码|商业机密|未公开|内部文档/.test(text)) findings.push("疑似商业或未公开信息");
  if (/全部删|删除所有|清空|格式化|reset\s+--hard/i.test(text)) findings.push("请求可能包含不可恢复操作");
  return findings;
}

function Sidebar({ view, onView, collapsed, onToggle }: { view: View; onView: (v: View) => void; collapsed: boolean; onToggle: () => void }) {
  const items: Array<{ id: View; label: string; icon: typeof WandSparkles }> = [
    { id: "enhance", label: "增强", icon: WandSparkles },
    { id: "history", label: "历史", icon: History },
    { id: "profile", label: "画像", icon: UserRoundCog },
    { id: "settings", label: "设置", icon: Settings },
  ];
  return <aside className={`sidebar ${collapsed ? "collapsed" : ""}`}>
    <div className="brand"><div className="brand-mark"><Sparkles size={18} /></div>{!collapsed && <span>PromptCraft</span>}</div>
    <nav>{items.map(({ id, label, icon: Icon }) => <button key={id} className={view === id ? "active" : ""} onClick={() => onView(id)} title={label}><Icon size={19} />{!collapsed && <span>{label}</span>}</button>)}</nav>
    <button className="collapse-button" onClick={onToggle} title={collapsed ? "展开导航" : "收起导航"}><PanelLeftClose size={18} className={collapsed ? "flip" : ""} />{!collapsed && <span>收起</span>}</button>
  </aside>;
}

function StatusBadge({ state }: { state: EnhancementState }) {
  const labels: Record<EnhancementState, string> = { idle: "等待输入", streaming: "正在增强", needs_clarification: "需要补充", ready: "增强完成", incomplete: "生成已停止", error: "生成失败" };
  return <span className={`status-badge ${state}`}><i />{labels[state]}</span>;
}

export default function App() {
  const [view, setView] = useState<View>("enhance");
  const [collapsed, setCollapsed] = useState(false);
  const [original, setOriginal] = useState("");
  const [context, setContext] = useState("");
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [output, setOutput] = useState("");
  const [result, setResult] = useState<EnhancementResult | null>(null);
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
    getProviderConfig().then((config) => { setProvider(config); setModel(config.defaultModel); }).catch(() => undefined);
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

  const persistHistory = (enhanced: string) => {
    const item: HistoryRecord = { id: crypto.randomUUID(), title: original.trim().slice(0, 32) || "未命名提示词", original, enhanced, createdAt: new Date().toISOString(), model, target };
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
    setShowSecurity(false); setError(monthlyWarningLimit > 0 && projectedMonthTotal >= monthlyWarningLimit ? `费用提醒：预计本次后本月累计约 ¥${projectedMonthTotal.toFixed(2)}，仍允许继续。` : ""); setState("streaming"); setResult(null); setOutput(""); setUndoStack([]); setRedoStack([]);
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
          const normalized = normalizeResult(event.result);
          setResult(normalized);
          setOutput(normalized.primary_prompt);
          setState(normalized.status === "needs_clarification" ? "needs_clarification" : "ready");
          persistHistory(normalized.primary_prompt);
        }
        if (event.type === "status" && event.data === "retrying_structure") setOutput("");
        if (event.type === "usage" && event.usage) setUsage(event.usage);
        if (event.type === "error") throw new Error(event.message ?? "增强失败");
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

  const handleCopyOpen = async () => {
    if (!output.trim()) return;
    if (!currentTarget.url) { setError("请先填写自定义目标网页地址"); return; }
    try { await copyAndOpen(output, currentTarget.url, clearClipboard); }
    catch (cause) { setError(`复制失败：${cause instanceof Error ? cause.message : String(cause)}`); }
  };

  const saveProvider = async () => {
    setSavingProvider(true); setProviderMessage("");
    try {
      if (apiKeyDraft) await validateProvider(apiKeyDraft, provider.baseUrl);
      const next = { ...provider, hasApiKey: Boolean(apiKeyDraft || provider.hasApiKey), apiKey: apiKeyDraft || undefined };
      await saveProviderConfig(next); setProvider({ ...provider, hasApiKey: next.hasApiKey }); setApiKeyDraft(""); setProviderMessage("连接验证成功，配置已保存");
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
    {view !== "enhance" ? mainContent : <main className="workspace">
      <header className="topbar">
        <div><h1>提示词增强</h1><p>保留原意，只补充真正影响结果的信息</p></div>
        <div className="toolbar-controls">
          <label>增强模型<select value={model} onChange={(event) => setModel(event.target.value)}><option value="deepseek-chat">DeepSeek Chat</option><option value="v4-flash" disabled={!provider.v4FlashModelId}>V4-Flash{provider.v4FlashModelId ? "" : "（需配置 ID）"}</option></select><ChevronDown size={15} /></label>
          <label>目标网页<select value={target} onChange={(event) => setTarget(event.target.value)}>{targetModels.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}</select><ChevronDown size={15} /></label>
          <label>详细程度<select value={verbosity} onChange={(event) => setVerbosity(event.target.value as Verbosity)}>{Object.entries(detailLabels).map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select><ChevronDown size={15} /></label>
          {state === "streaming" ? <button className="primary danger" onClick={stop}><Square size={16} fill="currentColor" />停止</button> : <button className="primary" onClick={() => runEnhance()}><Sparkles size={17} />增强</button>}
        </div>
      </header>

      {verbosity === "custom" && <div className="custom-strip"><label>自定义要求<input value={customInstructions} onChange={(event) => setCustomInstructions(event.target.value)} placeholder="例如：控制在 500 字内；必须包含验收标准；不要使用表格" /></label></div>}
      {target === "custom" && <div className="custom-strip"><label>自定义目标网页<input type="url" value={customTargetUrl} onChange={(event) => setCustomTargetUrl(event.target.value)} placeholder="https://..." /></label></div>}
      {error && <div className="error-banner"><AlertTriangle size={17} /><span>{error}</span><button onClick={() => setError("")} title="关闭"><X size={16} /></button></div>}

      <section className="editor-grid">
        <article className="editor-panel">
          <div className="panel-heading"><div><span className="step-index">01</span><h2>原始需求</h2></div><span>{original.length} 字</span></div>
          <textarea value={original} onChange={(event) => setOriginal(event.target.value)} placeholder="输入一句还不够清楚的需求，例如：中暑症状表现" />
          <div className="context-area">
            <div className="context-title"><span>上下文</span><span>{totalChars.toLocaleString()} / 100,000 字</span></div>
            <textarea value={context} onChange={(event) => setContext(event.target.value)} placeholder="可粘贴聊天记录、项目背景或需要参考的文字" />
            {attachments.length > 0 && <div className="attachment-list">{attachments.map((item) => <div key={item.id}><FileCode2 size={15} /><span title={item.name}>{item.name}</span><small>{item.chars.toLocaleString()} 字</small><button onClick={() => setAttachments((items) => items.filter((entry) => entry.id !== item.id))} title="移除附件"><X size={14} /></button></div>)}</div>}
            <button className="quiet-command" onClick={addAttachments} disabled={attachments.length >= 5}><FilePlus2 size={16} />添加文件 <span>最多 5 个</span></button>
          </div>
        </article>

        <article className="editor-panel output-panel">
          <div className="panel-heading"><div><span className="step-index">02</span><h2>增强结果</h2></div><div className="panel-actions"><StatusBadge state={state} /><button onClick={undo} disabled={!undoStack.length} title="撤销"><Undo2 size={16} /></button><button onClick={redo} disabled={!redoStack.length} title="重做"><RotateCw size={16} /></button></div></div>
          <div className="output-wrap">
            {state === "streaming" && !output && <div className="generating"><LoaderCircle className="spin" size={20} />正在理解意图并检查缺失信息</div>}
            <textarea value={output} onChange={(event) => commitOutput(event.target.value)} placeholder="增强后的提示词会显示在这里" />
          </div>
          <div className="result-footer">
            <div>{usage ? <span>{usage.inputTokens + usage.outputTokens} tokens · 约 ¥{usage.estimatedCost.toFixed(4)} · 本月 ¥{usage.monthTotal.toFixed(2)}</span> : <span>API 费用由你的供应商账户承担</span>}</div>
            <button className="secondary" onClick={() => copyText(output).catch((cause) => setError(`复制失败：${cause instanceof Error ? cause.message : String(cause)}`))} disabled={!output}><Clipboard size={16} />复制</button>
            <button className="primary" onClick={handleCopyOpen} disabled={!output}><ArrowRight size={16} />复制并打开 {currentTarget.label}</button>
          </div>
        </article>
      </section>

      {result?.assumptions.length ? <section className="assumption-strip"><AlertTriangle size={17} /><div><strong>当前假设</strong>{result.assumptions.map((item) => <span key={item.id}>{item.text}</span>)}</div></section> : null}

      {state === "needs_clarification" && result && <section className="clarification-band">
        <div className="section-title"><div><MessageSquareText size={19} /><span>需要补充的信息</span><b>{clarificationRound + 1}/3 轮</b></div><p>临时版本已生成。回答会直接用于下一版提示词。</p></div>
        <div className="question-grid">{result.questions.map((question) => <label key={question.id}><span>{question.text}</span><small>{question.why_needed}</small><input value={answers[question.id] ?? ""} onChange={(event) => setAnswers((current) => ({ ...current, [question.id]: event.target.value }))} placeholder="输入回答，也可以留空跳过" /></label>)}</div>
        <div className="band-actions"><button className="secondary" onClick={() => { setState("ready"); setResult({ ...result, status: "ready", questions: [] }); }}>结束澄清</button><button className="primary" onClick={submitClarification}><Send size={16} />提交并继续增强</button></div>
      </section>}

      {result?.changes.length ? <section className="changes-section">
        <div className="section-title"><div><RefreshCw size={18} /><span>修改明细</span><b>{result.changes.length} 项</b></div><p>逐项决定哪些改动保留在最终提示词中。</p></div>
        <div className="change-list">{result.changes.map((change) => <div className={`change-row ${change.state}`} key={change.id}><div className="change-copy"><span className="change-type">{change.type}</span><strong>{change.reason}</strong><div className="diff-line"><del>{change.before || "无"}</del><ArrowRight size={14} /><ins>{change.after}</ins></div></div><div className="change-actions"><button className={change.state === "rejected" ? "selected reject" : ""} onClick={() => changeState(change.id, "rejected")} title="拒绝修改"><X size={16} /></button><button className={change.state === "accepted" ? "selected accept" : ""} onClick={() => changeState(change.id, "accepted")} title="接受修改"><Check size={16} /></button></div></div>)}</div>
      </section> : null}

      {result?.suggestions.length ? <section className="suggestions-section">
        <div className="section-title"><div><Plus size={18} /><span>可选补充</span><b>5 项</b></div><p>只在确实符合你的目标时加入。</p></div>
        <div className="suggestion-grid">{result.suggestions.map((suggestion) => <button key={suggestion.id} disabled={suggestion.applied} onClick={() => setSelectedSuggestion(suggestion.id)}><span className="suggestion-kind">{suggestion.kind}</span><strong>{suggestion.applied ? "已加入" : suggestion.title}</strong><p>{suggestion.purpose}</p><Plus size={17} /></button>)}</div>
      </section> : null}
    </main>}

    {showSecurity && <div className="modal-backdrop"><div className="modal"><div className="modal-icon warning"><ShieldCheck size={22} /></div><h2>发送前需要确认</h2><p>本地检查发现以下风险。API Key、密码和私钥会被强制遮蔽，其余内容只在本次确认后发送。</p><ul>{securityFindings.map((item) => <li key={item}>{item}</li>)}</ul><div className="modal-actions"><button className="secondary" onClick={() => setShowSecurity(false)}>返回检查</button><button className="primary danger" onClick={() => runEnhance(true)}>确认本次发送</button></div></div></div>}
    {selectedSuggestionData && <div className="modal-backdrop"><div className="modal suggestion-modal"><div className="modal-icon"><Plus size={22} /></div><h2>{selectedSuggestionData.title}</h2><p>{selectedSuggestionData.purpose}</p><div className="preview-text">{selectedSuggestionData.content}</div><div className="modal-actions"><button className="secondary" onClick={() => setSelectedSuggestion(null)}>取消</button><button className="primary" onClick={applySuggestion}>加入提示词</button></div></div></div>}
  </div>;
}

function HistoryView({ items, onRestore, onDelete }: { items: HistoryRecord[]; onRestore: (item: HistoryRecord) => void; onDelete: (id: string) => void }) {
  const [query, setQuery] = useState("");
  const filtered = items.filter((item) => `${item.title}${item.original}${item.enhanced}`.toLowerCase().includes(query.toLowerCase()));
  return <main className="page"><header className="page-header"><div><h1>历史记录</h1><p>原文、增强结果和模型配置仅保存在本机</p></div><input className="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索历史" /></header>
    {filtered.length ? <div className="history-list">{filtered.map((item) => <article key={item.id}><button className="history-main" onClick={() => onRestore(item)}><div><Clock3 size={15} /><span>{new Date(item.createdAt).toLocaleString("zh-CN")}</span></div><h3>{item.title}</h3><p>{item.enhanced}</p><small>{item.model} · {targetModels.find((target) => target.id === item.target)?.label ?? item.target}</small></button><button className="icon-danger" onClick={() => onDelete(item.id)} title="删除记录"><Trash2 size={17} /></button></article>)}</div> : <div className="empty-state"><History size={28} /><h2>没有匹配的历史记录</h2><p>完成一次提示词增强后会自动保存版本。</p></div>}
  </main>;
}

function ProfileView({ rules, setRules, enabled, setEnabled }: { rules: ProfileRule[]; setRules: (rules: ProfileRule[]) => void; enabled: boolean; setEnabled: (enabled: boolean) => void }) {
  return <main className="page"><header className="page-header"><div><h1>本地偏好画像</h1><p>只保存结构化习惯，不训练模型，也不建立内容向量</p></div><label className="toggle"><input type="checkbox" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} /><span />{enabled ? "已启用" : "已暂停"}</label></header>
    <div className="profile-summary"><div><strong>{rules.length}</strong><span>条有效偏好</span></div><div><strong>{rules.filter((r) => r.explicit).length}</strong><span>条由你明确设置</span></div><div><strong>3 次</strong><span>形成稳定偏好的最低证据</span></div></div>
    <section className="settings-section"><div className="settings-heading"><div><h2>当前偏好</h2><p>当前请求始终高于这些偏好。</p></div><button className="secondary" onClick={() => setRules(defaultRules)}><RotateCcw size={16} />重置画像</button></div>
      <div className="rule-list">{rules.map((rule) => <div key={rule.id}><div><span className="rule-label">{rule.label}</span><input value={rule.value} onChange={(event) => setRules(rules.map((item) => item.id === rule.id ? { ...item, value: event.target.value } : item))} /></div><span>{rule.explicit ? "明确设置" : `${Math.round(rule.confidence * 100)}% 置信度`}</span><button onClick={() => setRules(rules.filter((item) => item.id !== rule.id))} title="删除偏好"><X size={16} /></button></div>)}</div>
      <button className="quiet-command" onClick={() => setRules([...rules, { id: crypto.randomUUID(), preferenceType: "custom", label: "自定义", value: "", confidence: 1, explicit: true }])}><Plus size={16} />添加明确偏好</button>
    </section>
  </main>;
}

function SettingsView(props: { provider: ProviderConfig; setProvider: (value: ProviderConfig) => void; apiKeyDraft: string; setApiKeyDraft: (value: string) => void; saveProvider: () => void; saving: boolean; message: string; clearClipboard: boolean; setClearClipboard: (value: boolean) => void; monthlyWarningLimit: number; setMonthlyWarningLimit: (value: number) => void; monthlyLimit: number; setMonthlyLimit: (value: number) => void; onClearData: () => Promise<void> }) {
  const { provider, setProvider, apiKeyDraft, setApiKeyDraft, saveProvider, saving, message, clearClipboard, setClearClipboard, monthlyWarningLimit, setMonthlyWarningLimit, monthlyLimit, setMonthlyLimit, onClearData } = props;
  const [confirmClear, setConfirmClear] = useState(false);
  const [dataMessage, setDataMessage] = useState("");
  return <main className="page"><header className="page-header"><div><h1>设置</h1><p>供应商、费用、隐私和本地数据</p></div></header>
    <section className="settings-section"><div className="settings-heading"><div><h2><KeyRound size={18} />DeepSeek 供应商</h2><p>API Key 将存入 Windows 凭据管理器。</p></div><span className={`connection ${provider.hasApiKey ? "ok" : ""}`}>{provider.hasApiKey ? "已配置" : "未配置"}</span></div>
      <div className="form-grid"><label>API Base URL<input value={provider.baseUrl} onChange={(event) => setProvider({ ...provider, baseUrl: event.target.value })} /></label><label>API Key<input type="password" value={apiKeyDraft} onChange={(event) => setApiKeyDraft(event.target.value)} placeholder={provider.hasApiKey ? "已保存，留空表示不修改" : "sk-..."} /></label><label>V4-Flash 模型 ID<input value={provider.v4FlashModelId} onChange={(event) => setProvider({ ...provider, v4FlashModelId: event.target.value })} placeholder="deepseek-v4-flash" /></label><label>默认模型<select value={provider.defaultModel} onChange={(event) => setProvider({ ...provider, defaultModel: event.target.value })}><option value="deepseek-chat">deepseek-chat</option><option value="v4-flash" disabled={!provider.v4FlashModelId}>V4-Flash</option></select></label></div>
      <div className="settings-actions">{message && <span>{message}</span>}<button className="primary" onClick={saveProvider} disabled={saving}>{saving ? <LoaderCircle className="spin" size={16} /> : <ShieldCheck size={16} />}验证并保存</button></div>
    </section>
    <section className="settings-section"><div className="settings-heading"><div><h2>费用控制</h2><p>按本机记录估算；供应商账单仍是最终依据。设为 0 表示关闭对应额度。</p></div></div><div className="form-grid"><label>月度提醒额度（元）<input type="number" min="0" step="1" value={monthlyWarningLimit} onChange={(event) => setMonthlyWarningLimit(Number(event.target.value))} /></label><label>月度强制额度（元）<input type="number" min="0" step="1" value={monthlyLimit} onChange={(event) => setMonthlyLimit(Number(event.target.value))} /></label><label>输入价格（元/千 token）<input type="number" min="0" step="0.0001" value={provider.inputPrice} onChange={(event) => setProvider({ ...provider, inputPrice: Number(event.target.value) })} /></label><label>输出价格（元/千 token）<input type="number" min="0" step="0.0001" value={provider.outputPrice} onChange={(event) => setProvider({ ...provider, outputPrice: Number(event.target.value) })} /></label></div></section>
    <section className="settings-section"><div className="settings-heading"><div><h2>剪贴板与隐私</h2><p>软件不收集遥测，不上传崩溃报告。</p></div></div><label className="setting-row"><div><strong>2 分钟后清理本软件复制的内容</strong><span>只有剪贴板仍是本软件写入的内容时才会清理。</span></div><label className="toggle"><input type="checkbox" checked={clearClipboard} onChange={(event) => setClearClipboard(event.target.checked)} /><span /></label></label><div className="setting-row"><div><strong>历史保留 90 天，最大 500 MB</strong><span>附件原件不保存；导出包不包含 API Key、附件原件或操作日志。</span>{dataMessage && <span>{dataMessage}</span>}</div><div className="inline-actions"><button className="secondary" onClick={async () => { try { const exported = await exportLocalData(); if (exported) setDataMessage(`已导出 ${exported.records} 条记录：${exported.path}`); } catch (cause) { setDataMessage(cause instanceof Error ? cause.message : String(cause)); } }}>导出数据</button><button className="secondary" onClick={async () => { try { const imported = await importLocalData(); if (imported) setDataMessage(`已导入 ${imported.records} 条记录；重新打开历史页面即可查看。`); } catch (cause) { setDataMessage(cause instanceof Error ? cause.message : String(cause)); } }}>导入数据</button><button className="secondary danger-outline" onClick={() => setConfirmClear(true)}>清空全部本地数据</button></div></div></section>
    {confirmClear && <div className="modal-backdrop"><div className="modal"><div className="modal-icon warning"><Trash2 size={22} /></div><h2>彻底清空应用数据</h2><p>这会删除历史、画像、费用记录、供应商配置和 Windows 凭据管理器中的 API Key。该操作不能撤销。</p><div className="modal-actions"><button className="secondary" onClick={() => setConfirmClear(false)}>取消</button><button className="primary danger" onClick={async () => { await onClearData(); setConfirmClear(false); }}>确认全部清空</button></div></div></div>}
  </main>;
}
