import { useEffect, useState } from "react";
import { ArrowRight, Box, CircleAlert, ClipboardCheck, PackageOpen, Truck } from "lucide-react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { Empty, Loading, SectionHeader, StatusPill, formatDate, showError } from "../ui";

type DashboardData = {
  inventory: { onHand: number; occupied: number; inTransit: number; available: number };
  pendingTransfers: number;
  activeWorkOrderMaterials: number;
  pendingStocktakes: number;
  recentTransfers: Array<{ id: string; number: string; status: string; targetWarehouse: string; createdAt: string; itemCount: number }>;
};

export default function DashboardPage() {
  const [data, setData] = useState<DashboardData | null>(null);
  const [error, setError] = useState("");
  useEffect(() => { api<DashboardData>("/dashboard").then(setData).catch((err) => setError(showError(err))); }, []);
  if (!data && !error) return <Loading label="正在汇总库存" />;
  return <>
    <SectionHeader title="物资总览" subtitle="v0.9 只读投影；正式库存流水与业务待办尚未开放" />
    {error && <div className="alert alert-error"><CircleAlert size={18} />{error}</div>}
    {data && <>
      <section className="metric-grid">
        <div className="metric metric-primary"><span className="metric-icon"><Box size={20} /></span><div><span>现有量</span><strong>{data.inventory.onHand}</strong></div></div>
        <div className="metric"><span className="metric-icon metric-icon-green"><PackageOpen size={20} /></span><div><span>可用量</span><strong>{data.inventory.available}</strong></div></div>
        <div className="metric"><span className="metric-icon metric-icon-amber"><Truck size={20} /></span><div><span>在途量</span><strong>{data.inventory.inTransit}</strong></div></div>
        <div className="metric"><span className="metric-icon metric-icon-red"><ClipboardCheck size={20} /></span><div><span>占用量</span><strong>{data.inventory.occupied}</strong></div></div>
      </section>
      <section className="dashboard-band dashboard-band-three">
        <div className="todo-summary"><div><strong>{data.pendingTransfers}</strong><span>v0.9 调拨历史</span></div><Link to="/transfers">只读查看<ArrowRight size={16} /></Link></div>
        <div className="todo-summary"><div><strong>{data.activeWorkOrderMaterials}</strong><span>v0.9 工单占用</span></div><span className="muted small">原型入口已隔离</span></div>
        <div className="todo-summary"><div><strong>{data.pendingStocktakes}</strong><span>v0.9 未完成盘点</span></div><span className="muted small">正式盘点待开发</span></div>
      </section>
      <section className="content-section">
        <div className="content-title"><div><h2>v0.9 最近调拨历史</h2><p>仅展示旧原型记录，不作为正式履约或入库状态</p></div><Link to="/transfers" className="text-link">只读查看<ArrowRight size={15} /></Link></div>
        {data.recentTransfers.length === 0 ? <Empty title="暂无最近调拨" /> : <div className="table-wrap"><table><thead><tr><th>调拨单号</th><th>目标仓库</th><th>物料种类</th><th>创建时间</th><th>状态</th></tr></thead><tbody>{data.recentTransfers.map((row) => <tr key={row.id}><td className="mono">{row.number}</td><td>{row.targetWarehouse}</td><td>{row.itemCount}</td><td>{formatDate(row.createdAt)}</td><td><StatusPill status={row.status} /></td></tr>)}</tbody></table></div>}
      </section>
    </>}
  </>;
}
