import { useState } from "react";
import { Clock3, History, Trash2 } from "lucide-react";
import { targetModels } from "../lib";
import type { HistoryRecord } from "../types";

export function HistoryView({ items, onRestore, onDelete }: { items: HistoryRecord[]; onRestore: (item: HistoryRecord) => void; onDelete: (id: string) => void }) {
  const [query, setQuery] = useState("");
  const filtered = items.filter((item) => `${item.title}${item.original}${item.enhanced}`.toLowerCase().includes(query.toLowerCase()));
  return <main className="page"><header className="page-header"><div><h1>历史记录</h1><p>原文、增强结果和模型配置仅保存在本机</p></div><input className="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索历史" /></header>
    {filtered.length ? <div className="history-list">{filtered.map((item) => <article key={item.id}><button className="history-main" onClick={() => onRestore(item)}><div><Clock3 size={15} /><span>{new Date(item.createdAt).toLocaleString("zh-CN")}</span></div><h3>{item.title}</h3><p>{item.enhanced}</p><small>{item.model} · {targetModels.find((target) => target.id === item.target)?.label ?? item.target}</small></button><button className="icon-danger" onClick={() => onDelete(item.id)} title="删除记录"><Trash2 size={17} /></button></article>)}</div> : <div className="empty-state"><History size={28} /><h2>没有匹配的历史记录</h2><p>完成一次提示词增强后会自动保存版本。</p></div>}
  </main>;
}
