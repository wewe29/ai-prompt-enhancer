import { ShieldCheck } from "lucide-react";

export function SecurityModal({ findings, onCancel, onConfirm }: { findings: string[]; onCancel: () => void; onConfirm: () => void }) {
  return <div className="modal-backdrop"><div className="modal"><div className="modal-icon warning"><ShieldCheck size={22} /></div><h2>发送前需要确认</h2><p>本地检查发现以下风险。API Key、密码和私钥会被强制遮蔽，其余内容只在本次确认后发送。</p><ul>{findings.map((item) => <li key={item}>{item}</li>)}</ul><div className="modal-actions"><button className="secondary" onClick={onCancel}>返回检查</button><button className="primary danger" onClick={onConfirm}>确认本次发送</button></div></div></div>;
}
