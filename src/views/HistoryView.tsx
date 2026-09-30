import { useState } from "react";
import { Clock3, History, Pin, PinOff, Trash2 } from "lucide-react";
import { targetModels } from "../lib";
import type { HistoryRecord } from "../types";

export function HistoryView({ items, onRestore, onDelete, onTogglePin }: { items: HistoryRecord[]; onRestore: (item: HistoryRecord) => void; onDelete: (id: string) => void; onTogglePin: (id: string, pinned: boolean) => void }) {
  const [query, setQuery] = useState("");
  const filtered = items.filter((item) => `${item.title}${item.original}${item.enhanced}`.toLowerCase().includes(query.toLowerCase()));
  const sorted = [...filtered].sort((a, b) => Number(Boolean(b.pinned)) - Number(Boolean(a.pinned)));
  return <main className="page"><header className="page-header"><div><h1>历史记录</h1><p>原文、增强结果和模型配置仅保存在本机</p></div><input className="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索历史" /></header>
    {sorted.length ? <div className="history-list">{sorted.map((item) => <article key={item.id} className={item.pinned ? "pinned" : undefined}><button className="history-main" onClick={() => onRestore(item)}><div><Clock3 size={15} /><span>{new Date(item.createdAt).toLocaleString("zh-CN")}</span>{item.pinned && <span className="delivery-badge">已置顶</span>}{item.deliveryStatus === "fallback" && <span className="delivery-badge">原文回退</span>}</div><h3>{item.title}</h3><p>{item.enhanced}</p><small>{item.model} · {targetModels.find((target) => target.id === item.target)?.label ?? item.target}</small></button><button className={item.pinned ? "icon-pin active" : "icon-pin"} onClick={() => onTogglePin(item.id, !item.pinned)} title={item.pinned ? "取消置顶" : "置顶（不会被自动清理）"}>{item.pinned ? <PinOff size={17} /> : <Pin size={17} />}</button><button className="icon-danger" onClick={() => onDelete(item.id)} title="删除记录"><Trash2 size={17} /></button></article>)}</div> : <div className="empty-state"><History size={28} /><h2>没有匹配的历史记录</h2><p>完成一次提示词增强后会自动保存版本。</p></div>}
  </main>;
}
