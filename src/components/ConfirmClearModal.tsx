import { useState } from "react";
import { Trash2 } from "lucide-react";

/**
 * 清空确认弹窗。
 *
 * 清空是**可能部分成功**的操作：本地数据被删除，但 Windows 凭据管理器里的
 * API Key 删不掉（src-tauri/src/storage.rs:364-376 —— Entry::new 失败时
 * 整块被跳过，却仍然返回 Ok）。
 * 后端会为此返回 Err，界面必须把它显示出来：不能让它变成一个未处理的
 * Promise rejection（那样用户既看不到原因，弹窗也不会关闭）。
 */
export function ConfirmClearModal({ onCancel, onConfirm }: { onCancel: () => void; onConfirm: () => void | Promise<void> }) {
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const run = async () => {
    setBusy(true);
    setError("");
    try {
      await onConfirm();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  };
  return <div className="modal-backdrop"><div className="modal">
    <div className="modal-icon warning"><Trash2 size={22} /></div>
    <h2>彻底清空应用数据</h2>
    <p>这会删除历史、画像、费用记录、供应商配置和 Windows 凭据管理器中的 API Key。该操作不能撤销。</p>
    {error ? <div className="error-banner"><span>{error}</span></div> : null}
    <div className="modal-actions"><button className="secondary" onClick={onCancel} disabled={busy}>取消</button><button className="primary danger" onClick={run} disabled={busy}>{busy ? "正在清空…" : "确认全部清空"}</button></div>
  </div></div>;
}
