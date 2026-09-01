import { useEffect, useState } from "react";
import { RefreshCw } from "lucide-react";
import { api } from "../api";
import { Button, Empty, Loading, SectionHeader, formatDate, showError } from "../ui";

type AuditRow = { id: string; actor: string; action: string; entityType: string; entityId: string; detail: string; ipAddress: string; createdAt: string };

export default function AuditPage() {
  const [rows, setRows] = useState<AuditRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  async function load() { setLoading(true); setError(""); try { setRows(await api<AuditRow[]>("/audit?limit=300")); } catch (err) { setError(showError(err)); } finally { setLoading(false); } }
  useEffect(() => { load(); }, []);
  return <>
    <SectionHeader title="审计日志" subtitle="关键写操作、操作人、来源地址与时间不可覆盖" actions={<Button icon={<RefreshCw size={17} />} onClick={load}>刷新</Button>} />
    {error && <div className="alert alert-error">{error}</div>}
    {loading ? <Loading /> : rows.length === 0 ? <Empty title="暂无审计记录" /> : <section className="content-section table-section"><div className="table-wrap"><table><thead><tr><th>时间</th><th>操作人</th><th>动作</th><th>业务对象</th><th>详情</th><th>来源IP</th></tr></thead><tbody>{rows.map((row) => <tr key={row.id}><td>{formatDate(row.createdAt)}</td><td>{row.actor}</td><td className="mono">{row.action}</td><td>{row.entityType}<span className="cell-subtitle mono">{row.entityId}</span></td><td className="audit-detail">{row.detail}</td><td className="mono">{row.ipAddress || "-"}</td></tr>)}</tbody></table></div></section>}
  </>;
}
