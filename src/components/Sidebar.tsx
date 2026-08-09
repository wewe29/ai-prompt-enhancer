import { History, PanelLeftClose, Settings, Sparkles, UserRoundCog, WandSparkles } from "lucide-react";

export type View = "enhance" | "history" | "profile" | "settings";

export function Sidebar({ view, onView, collapsed, onToggle }: { view: View; onView: (v: View) => void; collapsed: boolean; onToggle: () => void }) {
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
