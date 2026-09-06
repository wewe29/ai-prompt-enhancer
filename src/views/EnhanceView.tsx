import { AlertTriangle, ArrowRight, Ban, Check, CheckCheck, ChevronDown, Clipboard, FileCode2, FilePlus2, FileText, LoaderCircle, MessageSquareText, Plus, RefreshCw, RotateCw, Send, ShieldAlert, Sparkles, Square, Undo2, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import type { Dispatch, SetStateAction } from "react";
import { copyText, targetModels } from "../lib";
import { classifyTask, preflight } from "../preflight";
import type { Attachment, EnhancementResult, EnhancementState, ProviderConfig, Suggestion, UsageRecord, Verbosity } from "../types";
import { StatusBadge } from "../components/StatusBadge";

const detailLabels: Record<Verbosity, string> = { concise: "简洁", standard: "标准", deep: "深入", custom: "自定义" };

const taskTypeLabels: Record<string, string> = { code: "代码", creative: "创意", writing: "写作", qa: "问答解释", data: "数据分析", translation: "翻译", other: "其他" };

const modelLabel = (id: string) => id === "deepseek-chat" ? "DeepSeek Chat" : id === "v4-flash" ? "V4-Flash" : id;

export interface EnhanceViewProps {
  model: string;
  setModel: Dispatch<SetStateAction<string>>;
  provider: ProviderConfig;
  target: string;
  setTarget: Dispatch<SetStateAction<string>>;
  verbosity: Verbosity;
  setVerbosity: Dispatch<SetStateAction<Verbosity>>;
  state: EnhancementState;
  setState: Dispatch<SetStateAction<EnhancementState>>;
  stop: () => Promise<void>;
  runEnhance: (confirmed?: boolean) => void;
  customInstructions: string;
  setCustomInstructions: Dispatch<SetStateAction<string>>;
  customTargetUrl: string;
  setCustomTargetUrl: Dispatch<SetStateAction<string>>;
  error: string;
  setError: Dispatch<SetStateAction<string>>;
  original: string;
  setOriginal: Dispatch<SetStateAction<string>>;
  context: string;
  setContext: Dispatch<SetStateAction<string>>;
  totalChars: number;
  attachments: Attachment[];
  setAttachments: Dispatch<SetStateAction<Attachment[]>>;
  addAttachments: () => Promise<void>;
  output: string;
  commitOutput: (next: string) => void;
  undo: () => void;
  redo: () => void;
  undoStack: string[];
  redoStack: string[];
  result: EnhancementResult | null;
  setResult: Dispatch<SetStateAction<EnhancementResult | null>>;
  usage: UsageRecord | null;
  handleCopyOpen: () => Promise<void>;
  currentTarget: { id: string; label: string; url: string };
  clarificationRound: number;
  answers: Record<string, string>;
  setAnswers: Dispatch<SetStateAction<Record<string, string>>>;
  submitClarification: () => void;
  changeState: (id: string, nextState: "accepted" | "rejected") => void;
  setSelectedSuggestion: Dispatch<SetStateAction<string | null>>;
  showSecurity: boolean;
  setShowSecurity: (v: boolean) => void;
  selectedSuggestionData: Suggestion | null;
  notices: string[];
  deliveryStatus?: "complete" | "partial" | "fallback";
  restoreOriginal: () => void;
  keepEssentialEdits: () => void;
  hasActionableChanges: boolean;
  regenerate: () => void;
  activeCandidate: number;
  switchCandidate: (index: number) => void;
  acceptAllChanges: () => void;
  rejectAllChanges: () => void;
  hasPendingChanges: boolean;
}

export function EnhanceView(props: EnhanceViewProps) {
  const {
    model, setModel, provider, target, setTarget, verbosity, setVerbosity, state, setState, stop, runEnhance,
    customInstructions, setCustomInstructions, customTargetUrl, setCustomTargetUrl, error, setError,
    original, setOriginal, context, setContext, totalChars, attachments, setAttachments, addAttachments,
    output, commitOutput, undo, redo, undoStack, redoStack, result, setResult, usage, handleCopyOpen,
    currentTarget, clarificationRound, answers, setAnswers, submitClarification, changeState, setSelectedSuggestion,
    showSecurity, setShowSecurity, selectedSuggestionData,
    notices, deliveryStatus, restoreOriginal, keepEssentialEdits, hasActionableChanges, regenerate,
    activeCandidate, switchCandidate, acceptAllChanges, rejectAllChanges, hasPendingChanges,
  } = props;
  const [preflightDismissed, setPreflightDismissed] = useState(false);
  const findings = useMemo(() => preflight(original, context.length > 0, classifyTask(original)), [original, context]);
  const changeSummaryLabels: Record<string, string> = {
    add_context: "补充背景",
    add_constraint: "补充约束",
    format: "明确输出形式",
    safety: "添加风险保护",
    clarify: "澄清表达",
    remove_redundancy: "精简重复内容",
  };
  const enhancementLevelLabels: Record<string, string> = { none: "无需明显修改", light: "轻度增强", clarify: "需要澄清" };
  const deliveryLabels: Record<string, string> = { complete: "完整交付", partial: "部分交付", fallback: "原文回退" };
  const suggestionKindLabels: Record<string, string> = { goal: "目标", context: "背景", format: "格式", constraint: "约束", alternate_intent: "备选意图" };
  const riskCategoryLabels: Record<string, string> = { destructive: "破坏性操作", medical: "医疗", legal: "法律", financial: "金融", credential: "凭据", privacy: "隐私", factual: "事实性" };
  const displayCandidates = useMemo(() => {
    if (!result) return [];
    return result.candidates?.length ? result.candidates : [{ index: 1, text: result.primary_prompt, note: "" }];
  }, [result]);
  const activeShown = displayCandidates.some((item) => item.index === activeCandidate) ? activeCandidate : 1;
  const missingFields = useMemo(() => {
    if (!result || result.delivery_status !== "partial") return [] as string[];
    const checks: Array<[boolean, string]> = [
      [result.assumptions.length === 0, "假设"],
      [result.changes.length === 0, "修改明细"],
      [result.suggestions.length === 0, "可选建议"],
      [result.risk_flags.length === 0, "风险提示"],
      [(result.facts?.length ?? 0) === 0, "用户原始事实"],
      [(result.candidates?.length ?? 0) === 0, "候选提示词"],
    ];
    return checks.filter(([empty]) => empty).map(([, label]) => label);
  }, [result]);
  const resultSummary = useMemo(() => {
    if (!result) return null;
    const level = enhancementLevelLabels[result.enhancement_level ?? "light"] ?? "轻度增强";
    const outputLength = result.primary_prompt.length;
    const ratio = original.length > 0 ? `（${(outputLength / original.length).toFixed(2)} 倍）` : "";
    const lengthChange = `原文 ${original.length} 字 -> 结果 ${outputLength} 字${ratio}`;
    const changesText = [...new Set(result.changes.map((change) => changeSummaryLabels[change.type]).filter(Boolean))].join("、") || "无实质修改";
    const source = result.assumptions.length > 0 ? "包含明确假设" : "仅使用用户提供内容";
    return { level, lengthChange, changesText, source };
  }, [result, original]);
  useEffect(() => {
    const isTyping = () => {
      const el = document.activeElement;
      return !!el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || (el as HTMLElement).isContentEditable);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.ctrlKey && event.key === "Enter") {
        event.preventDefault();
        if (state === "streaming" || state === "needs_clarification" || showSecurity) return;
        runEnhance();
        return;
      }
      if (event.ctrlKey && event.key === "z") {
        if (isTyping()) return;
        event.preventDefault();
        if (event.shiftKey) redo();
        else undo();
        return;
      }
      if (event.ctrlKey && event.key === "y") {
        if (isTyping()) return;
        event.preventDefault();
        redo();
        return;
      }
      if (event.key === "Escape") {
        if (showSecurity) { setShowSecurity(false); return; }
        if (selectedSuggestionData) { setSelectedSuggestion(null); return; }
        if (error) { setError(""); return; }
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [state, error, showSecurity, selectedSuggestionData, runEnhance, undo, redo, setError, setSelectedSuggestion, setShowSecurity]);
  return <main className="workspace">
    <header className="topbar">
      <div><h1>提示词增强</h1><p>保留原意，只补充真正影响结果的信息</p></div>
      <div className="toolbar-controls">
        <label>增强模型<select value={model} onChange={(event) => setModel(event.target.value)}>{(provider.models.length ? provider.models : ["deepseek-chat"]).map((id) => <option key={id} value={id} disabled={id === "v4-flash" && !provider.v4FlashModelId}>{modelLabel(id)}{id === "v4-flash" && !provider.v4FlashModelId ? "（需配置 ID）" : ""}</option>)}</select><ChevronDown size={15} /></label>
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
        {state !== "streaming" && !preflightDismissed && findings.length > 0 && <div className="preflight-card">
          <div className="preflight-title">发送前快速检查<span className="preflight-hint">本地检查，不发送内容</span><button className="preflight-close" onClick={() => setPreflightDismissed(true)} title="关闭"><X size={14} /></button></div>
          <ul>{findings.map((f) => <li key={f.id} className={`level-${f.level}`}>{f.level === "warning" ? "建议补充：" : ""}{f.message}</li>)}</ul>
        </div>}
        <div className="context-area">
          <div className="context-title"><span>上下文</span><span>{totalChars.toLocaleString()} / 100,000 字</span></div>
          <textarea value={context} onChange={(event) => setContext(event.target.value)} placeholder="可粘贴聊天记录、项目背景或需要参考的文字" />
          {attachments.length > 0 && <div className="attachment-list">{attachments.map((item) => <div key={item.id}><FileCode2 size={15} /><span title={item.name}>{item.name}</span><small>{item.chars.toLocaleString()} 字</small><button onClick={() => setAttachments((items) => items.filter((entry) => entry.id !== item.id))} title="移除附件"><X size={14} /></button></div>)}</div>}
          <button className="quiet-command" onClick={addAttachments} disabled={attachments.length >= 5}><FilePlus2 size={16} />添加文件 <span>最多 5 个</span></button>
        </div>
      </article>

      <article className="editor-panel output-panel">
        <div className="panel-heading"><div><span className="step-index">02</span><h2>增强结果</h2></div><div className="panel-actions">{deliveryStatus && result ? <span className={`delivery-badge ${deliveryStatus}`}>{deliveryLabels[deliveryStatus] ?? deliveryStatus}</span> : null}{result?.enhancement_level === "none" && state !== "streaming" ? <span className="none-hint">增强器判断无需修改，建议直接使用原文</span> : null}<StatusBadge state={state} /><button onClick={undo} disabled={!undoStack.length} title="撤销"><Undo2 size={16} /></button><button onClick={redo} disabled={!redoStack.length} title="重做"><RotateCw size={16} /></button></div></div>
        {notices.length > 0 && <div className={`notices-strip${deliveryStatus ? ` ${deliveryStatus}` : ""}`}><AlertTriangle size={15} /><ul>{deliveryStatus === "partial" && missingFields.length > 0 && <li className="missing-line" key="missing-fields">本次交付缺失：{missingFields.join("、")}</li>}{[...new Set(notices)].map((notice) => <li key={notice}>{notice}</li>)}</ul></div>}
        {result && state !== "streaming" && <div className="candidate-strip"><span>候选 {activeShown}/{displayCandidates.length}</span>{displayCandidates.length > 1 && displayCandidates.map((item) => <button key={item.index} className={item.index === activeShown ? "selected" : ""} onClick={() => switchCandidate(item.index)} title={item.note || `切换到候选 ${item.index}`}>{item.index}</button>)}{displayCandidates.length > 1 && <small>切换候选会替换当前输出，可用撤销恢复</small>}</div>}
        <div className="output-wrap">
          {state === "streaming" && !output && <div className="generating"><LoaderCircle className="spin" size={20} />正在理解意图并检查缺失信息</div>}
          <textarea value={output} onChange={(event) => commitOutput(event.target.value)} placeholder="增强后的提示词会显示在这里" />
        </div>
        <div className="result-footer">
          {result?.task_type ? <span className={`task-type-badge ${result.task_type}`}>任务类型：{taskTypeLabels[result.task_type] ?? result.task_type}</span> : null}
          <div>{usage ? <span>{usage.inputTokens + usage.outputTokens} tokens · 约 ¥{usage.estimatedCost.toFixed(4)} · 本月 ¥{usage.monthTotal.toFixed(2)}</span> : <span>API 费用由你的供应商账户承担</span>}</div>
          <button className="secondary" onClick={restoreOriginal} disabled={state === "streaming" || output === original} title="恢复为本次请求开始时的原始提示词"><Undo2 size={16} />恢复原文</button>
          {hasActionableChanges && <button className="secondary" onClick={keepEssentialEdits} disabled={state === "streaming"} title="拒绝非必要的未确认修改，仅保留安全与已接受的改动"><Check size={16} />仅保留必要修改</button>}
          <button className="secondary" onClick={regenerate} disabled={state === "streaming"} title="沿用当前输入和配置重新生成"><RefreshCw size={16} />重新生成</button>
          <button className="secondary" onClick={() => copyText(output).catch((cause) => setError(`复制失败：${cause instanceof Error ? cause.message : String(cause)}`))} disabled={!output}><Clipboard size={16} />复制</button>
          <button className="primary" onClick={handleCopyOpen} disabled={!output}><ArrowRight size={16} />复制并打开 {currentTarget.label}</button>
        </div>
      </article>
    </section>

    {resultSummary && <section className="result-summary">
      <div className="summary-row"><span>增强等级</span><strong>{resultSummary.level}</strong></div>
      <div className="summary-row"><span>交付状态</span><strong>{deliveryStatus ? deliveryLabels[deliveryStatus] ?? deliveryStatus : "完整交付"}</strong></div>
      <div className="summary-row"><span>长度变化</span><strong>{resultSummary.lengthChange}</strong></div>
      <div className="summary-row"><span>修改摘要</span><strong>{resultSummary.changesText}</strong></div>
      <div className="summary-row"><span>事实来源</span><strong>{resultSummary.source}</strong></div>
    </section>}

    {result && <section className="facts-section">
      <div className="section-title"><div><FileText size={18} /><span>用户原始事实</span></div><p>逐字或近逐字摘自你的输入；增强只允许使用这些事实。</p></div>
      {result.facts?.length ? <ul className="facts-list">{result.facts.map((fact, index) => <li key={index}>{fact}</li>)}</ul> : <p className="facts-empty">未从输入中提取到明确事实</p>}
    </section>}

    {result?.risk_flags.length ? <section className="risk-section">
      <div className="section-title"><div><ShieldAlert size={18} /><span>风险提示</span></div><p>涉及不可逆或敏感操作时，先确认保护措施再执行。</p></div>
      <div className="risk-grid">{result.risk_flags.map((flag, index) => <div className={`risk-card ${flag.category}`} key={index}><strong>{riskCategoryLabels[flag.category] ?? flag.category}</strong><p>{flag.message}</p>{flag.required_protection ? <small>需要保护：{flag.required_protection}</small> : null}</div>)}</div>
    </section> : null}

    {result?.assumptions.length ? <section className="assumption-strip"><AlertTriangle size={17} /><div><strong>当前假设</strong>{result.assumptions.map((item) => <span key={item.id}>{item.text}</span>)}</div></section> : null}

    {state === "needs_clarification" && result && <section className="clarification-band">
      <div className="section-title"><div><MessageSquareText size={19} /><span>需要补充的信息</span><b>当前第 {clarificationRound + 1}/3 轮 · 剩余 {Math.max(0, 3 - clarificationRound - 1)} 轮</b></div><p>临时版本已生成。回答会直接用于下一版提示词，也可以留空跳过。</p></div>
      <div className="question-grid">{result.questions.map((question) => <label key={question.id}><span>{question.text}</span><small>{question.why_needed}</small><input value={answers[question.id] ?? ""} onChange={(event) => setAnswers((current) => ({ ...current, [question.id]: event.target.value }))} placeholder="输入回答，也可以留空跳过" /></label>)}</div>
      <div className="band-actions"><button className="secondary" onClick={() => { setState("ready"); setResult({ ...result, status: "ready", questions: [] }); }}>结束澄清</button><button className="primary" onClick={submitClarification}><Send size={16} />提交并继续增强</button></div>
    </section>}

    {result?.changes.length ? <section className="changes-section">
      <div className="section-title"><div><RefreshCw size={18} /><span>修改明细</span><b>{result.changes.length} 项</b><div className="batch-actions">{hasPendingChanges && <button onClick={acceptAllChanges} disabled={state === "streaming"} title="依次应用全部未决修改，未命中锚点的项会跳过并提示"><CheckCheck size={14} />接受全部</button>}{hasPendingChanges && <button onClick={rejectAllChanges} disabled={state === "streaming"} title="拒绝全部未决修改，不影响已接受的项"><Ban size={14} />拒绝全部</button>}</div></div><p>逐项决定哪些改动保留在最终提示词中。</p></div>
      <div className="change-list">{result.changes.map((change) => <div className={`change-row ${change.state}`} key={change.id}><div className="change-copy"><span className="change-type">{change.type}</span><strong>{change.reason}</strong><div className="diff-line"><del>{change.before || "无"}</del><ArrowRight size={14} /><ins>{change.after}</ins></div></div><div className="change-actions"><button className={change.state === "rejected" ? "selected reject" : ""} onClick={() => changeState(change.id, "rejected")} title="拒绝修改"><X size={16} /></button><button className={change.state === "accepted" ? "selected accept" : ""} onClick={() => changeState(change.id, "accepted")} title="接受修改"><Check size={16} /></button></div></div>)}</div>
    </section> : null}

    {result?.suggestions.length ? <section className="suggestions-section">
      <div className="section-title"><div><Plus size={18} /><span>可选补充</span><b>{result.suggestions.length} 项</b></div><p>只在确实符合你的目标时加入。</p></div>
      <div className="suggestion-grid">{result.suggestions.map((suggestion) => <button key={suggestion.id} disabled={suggestion.applied} onClick={() => setSelectedSuggestion(suggestion.id)}><span className="suggestion-kind">{suggestionKindLabels[suggestion.kind] ?? suggestion.kind}</span><strong>{suggestion.applied ? "已加入" : suggestion.title}</strong><p>{suggestion.purpose}</p><Plus size={17} /></button>)}</div>
    </section> : null}
  </main>;
}
