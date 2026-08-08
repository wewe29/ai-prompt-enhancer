import { Plus } from "lucide-react";
import type { Suggestion } from "../types";

export function SuggestionModal({ suggestion, onCancel, onApply }: { suggestion: Suggestion; onCancel: () => void; onApply: () => void }) {
  return <div className="modal-backdrop"><div className="modal suggestion-modal"><div className="modal-icon"><Plus size={22} /></div><h2>{suggestion.title}</h2><p>{suggestion.purpose}</p><div className="preview-text">{suggestion.content}</div><div className="modal-actions"><button className="secondary" onClick={onCancel}>取消</button><button className="primary" onClick={onApply}>加入提示词</button></div></div></div>;
}
