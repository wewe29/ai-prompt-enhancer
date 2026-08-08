import { Trash2 } from "lucide-react";

export function ConfirmClearModal({ onCancel, onConfirm }: { onCancel: () => void; onConfirm: () => void }) {
  return <div className="modal-backdrop"><div className="modal"><div className="modal-icon warning"><Trash2 size={22} /></div><h2>彻底清空应用数据</h2><p>这会删除历史、画像、费用记录、供应商配置和 Windows 凭据管理器中的 API Key。该操作不能撤销。</p><div className="modal-actions"><button className="secondary" onClick={onCancel}>取消</button><button className="primary danger" onClick={onConfirm}>确认全部清空</button></div></div></div>;
}
