import { Plus, RotateCcw, X } from "lucide-react";
import { defaultRules } from "../defaults";
import type { ProfileRule } from "../types";

export function ProfileView({ rules, setRules, enabled, setEnabled }: { rules: ProfileRule[]; setRules: (rules: ProfileRule[]) => void; enabled: boolean; setEnabled: (enabled: boolean) => void }) {
  return <main className="page"><header className="page-header"><div><h1>本地偏好画像</h1><p>只保存结构化习惯，不训练模型，也不建立内容向量</p></div><label className="toggle"><input type="checkbox" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} /><span />{enabled ? "已启用" : "已暂停"}</label></header>
    <div className="profile-summary"><div><strong>{rules.length}</strong><span>条有效偏好</span></div><div><strong>{rules.filter((r) => r.explicit).length}</strong><span>条由你明确设置</span></div><div><strong>3 次</strong><span>形成稳定偏好的最低证据</span></div></div>
    <section className="settings-section"><div className="settings-heading"><div><h2>当前偏好</h2><p>当前请求始终高于这些偏好。</p></div><button className="secondary" onClick={() => setRules(defaultRules)}><RotateCcw size={16} />重置画像</button></div>
      <div className="rule-list">{rules.map((rule) => <div key={rule.id}><div><span className="rule-label">{rule.label}</span><input value={rule.value} onChange={(event) => setRules(rules.map((item) => item.id === rule.id ? { ...item, value: event.target.value } : item))} /></div><span>{rule.explicit ? "明确设置" : `${Math.round(rule.confidence * 100)}% 置信度`}</span><button onClick={() => setRules(rules.filter((item) => item.id !== rule.id))} title="删除偏好"><X size={16} /></button></div>)}</div>
      <button className="quiet-command" onClick={() => setRules([...rules, { id: crypto.randomUUID(), preferenceType: "custom", label: "自定义", value: "", confidence: 1, explicit: true }])}><Plus size={16} />添加明确偏好</button>
    </section>
  </main>;
}
