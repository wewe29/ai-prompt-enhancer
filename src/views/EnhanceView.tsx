import { AlertTriangle, ArrowRight, Check, ChevronDown, Clipboard, FileCode2, FilePlus2, LoaderCircle, MessageSquareText, Plus, RefreshCw, RotateCw, Send, Sparkles, Square, Undo2, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import type { Dispatch, SetStateAction } from "react";
import { copyText, targetModels } from "../lib";
import { preflight } from "../preflight";
import type { Attachment, EnhancementResult, EnhancementState, ProviderConfig, Suggestion, UsageRecord, Verbosity } from "../types";
import { StatusBadge } from "../components/StatusBadge";

const detailLabels: Record<Verbosity, string> = { concise: "简洁", standard: "标准", deep: "深入", custom: "自定义" };

const taskTypeLabels: Record<string, string> = { code: "代码", creative: "创意", writing: "写作", qa: "问答解释", data: "数据分析", translation: "翻译", other: "其他" };

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
}

export function EnhanceView(props: EnhanceViewProps) {
  const {
    model, setModel, provider, target, setTarget, verbosity, setVerbosity, state, setState, stop, runEnhance,
    customInstructions, setCustomInstructions, customTargetUrl, setCustomTargetUrl, error, setError,
    original, setOriginal, context, setContext, totalChars, attachments, setAttachments, addAttachments,
    output, commitOutput, undo, redo, undoStack, redoStack, result, setResult, usage, handleCopyOpen,
    currentTarget, clarificationRound, answers, setAnswers, submitClarification, changeState, setSelectedSuggestion,
    showSecurity, setShowSecurity, selectedSuggestionData,
  } = props;
  const [preflightDismissed, setPreflightDismissed] = useState(false);
  const findings = useMemo(() => preflight(original, context.length > 0), [original, context]);
  useEffect(() => {
    const isTyping = () => {
      const el = document.activeElement;
      return !!el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || (el as HTMLElement).isContentEditable);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.ctrlKey && event.key === "Enter") {
        event.preventDefault();
        if (state === "streaming" || showSecurity) return;
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
        <div className="panel-heading"><div><span className="step-index">02</span><h2>增强结果</h2></div><div className="panel-actions"><StatusBadge state={state} /><button onClick={undo} disabled={!undoStack.length} title="撤销"><Undo2 size={16} /></button><button onClick={redo} disabled={!redoStack.length} title="重做"><RotateCw size={16} /></button></div></div>
        <div className="output-wrap">
          {state === "streaming" && !output && <div className="generating"><LoaderCircle className="spin" size={20} />正在理解意图并检查缺失信息</div>}
          <textarea value={output} onChange={(event) => commitOutput(event.target.value)} placeholder="增强后的提示词会显示在这里" />
        </div>
        <div className="result-footer">
          {result?.task_type ? <span className={`task-type-badge ${result.task_type}`}>任务类型：{taskTypeLabels[result.task_type] ?? result.task_type}</span> : null}
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
  </main>;
}
