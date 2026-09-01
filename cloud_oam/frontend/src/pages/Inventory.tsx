import { useCallback, useEffect, useMemo, useState } from "react";
import { CircleAlert, Download, RefreshCw, Search, SlidersHorizontal } from "lucide-react";
import { api } from "../api";
import type { InventoryRow, User, Warehouse } from "../types";
import { Button, Empty, Loading, SectionHeader, showError } from "../ui";

const CONDITION: Record<string, string> = { good: "好件", old: "旧件", bad: "坏件" };

export default function InventoryPage({ personalOnly = false }: { user: User; personalOnly?: boolean }) {
  const [rows, setRows] = useState<InventoryRow[]>([]);
  const [warehouses, setWarehouses] = useState<Warehouse[]>([]);
  const [q, setQ] = useState("");
  const [warehouse, setWarehouse] = useState("");
  const [condition, setCondition] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true); setError("");
    try {
      const params = new URLSearchParams();
      if (q.trim()) params.set("q", q.trim());
      if (warehouse) params.set("warehouse_id", warehouse);
      if (condition) params.set("condition", condition);
      if (personalOnly) params.set("mine", "true");
      setRows(await api<InventoryRow[]>(`/inventory?${params}`));
    } catch (err) { setError(showError(err)); } finally { setLoading(false); }
  }, [condition, personalOnly, q, warehouse]);

  useEffect(() => { api<Warehouse[]>("/warehouses").then(setWarehouses); }, []);
  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const totals = useMemo(() => rows.reduce((acc, row) => ({ onHand: acc.onHand + row.onHand, occupied: acc.occupied + row.occupied, transit: acc.transit + row.inTransit, available: acc.available + row.available }), { onHand: 0, occupied: 0, transit: 0, available: 0 }), [rows]);

  function exportCsv() {
    const header = ["省份", "仓库", "持有人", "物料编码", "物料名称", "类型", "现有量", "占用", "在途", "可用量"];
    const body = rows.map((row) => [row.warehouse.province, row.warehouse.name, row.holder?.name || "", row.material.code, row.material.name, CONDITION[row.condition] || row.condition, row.onHand, row.occupied, row.inTransit, row.available]);
    const csv = "\ufeff" + [header, ...body].map((line) => line.map((cell) => `"${String(cell).replaceAll('"', '""')}"`).join(",")).join("\n");
    const link = document.createElement("a"); link.href = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" })); link.download = `${personalOnly ? "个人仓" : "库存明细"}_${new Date().toISOString().slice(0, 10)}.csv`; link.click(); URL.revokeObjectURL(link.href);
  }

  return <>
    <SectionHeader title={personalOnly ? "个人仓" : "库存查询"} subtitle={personalOnly ? "按当前人员聚合现有、占用、在途和可用库存" : "库存只通过正式业务单与不可变流水变化，不提供直接余额调整"} actions={<><Button tone="secondary" icon={<Download size={17} />} onClick={exportCsv}>导出</Button><Button icon={<RefreshCw size={17} />} onClick={load}>刷新</Button></>} />
    <section className="filter-bar">
      <label className="search-box"><Search size={18} /><input value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === "Enter" && load()} placeholder="物料编码或名称" /></label>
      {!personalOnly && <select value={warehouse} onChange={(e) => setWarehouse(e.target.value)}><option value="">全部仓库</option>{warehouses.map((row) => <option key={row.id} value={row.id}>{row.province} · {row.name}</option>)}</select>}
      <select value={condition} onChange={(e) => setCondition(e.target.value)}><option value="">全部类型</option><option value="good">好件</option><option value="old">旧件</option><option value="bad">坏件</option></select>
      <Button icon={<SlidersHorizontal size={17} />} onClick={load}>查询</Button>
    </section>
    <section className="inventory-summary"><span>明细 <strong>{rows.length}</strong></span><span>现有 <strong>{totals.onHand}</strong></span><span>占用 <strong>{totals.occupied}</strong></span><span>在途 <strong>{totals.transit}</strong></span><span>可用 <strong>{totals.available}</strong></span></section>
    {error && <div className="alert alert-error"><CircleAlert size={18} />{error}</div>}
    {loading ? <Loading /> : rows.length === 0 ? <Empty title="没有命中库存" detail="请调整物料、仓库或类型条件" /> : <section className="content-section table-section"><div className="table-wrap"><table className="inventory-table"><thead><tr><th>物料</th><th>仓库 / 持有人</th><th>类型</th><th className="num">现有量</th><th className="num">占用</th><th className="num">在途</th><th className="num">可用量</th></tr></thead><tbody>{rows.map((row) => <tr key={row.id}><td><strong className="mono">{row.material.code}</strong><span className="cell-subtitle">{row.material.name}</span></td><td><strong>{row.warehouse.name}</strong><span className="cell-subtitle">{row.holder ? `持有人：${row.holder.name}` : row.warehouse.province}</span></td><td><span className={`condition condition-${row.condition}`}>{CONDITION[row.condition] || row.condition}</span></td><td className="num">{row.onHand}</td><td className="num muted-num">{row.occupied}</td><td className="num transit-num">{row.inTransit}</td><td className="num available-num">{row.available}</td></tr>)}</tbody></table></div></section>}
  </>;
}
