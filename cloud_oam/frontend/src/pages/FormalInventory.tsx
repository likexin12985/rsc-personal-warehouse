import { useEffect, useState } from "react";
import { CircleAlert, RefreshCw } from "lucide-react";

import { api } from "../api";
import { validateInventoryAccountPage } from "../formalInventory";
import type { InventoryAccountPage } from "../types";
import { Button, Empty, Loading, SectionHeader, showError } from "../ui";


const CONDITION_LABELS: Record<string, string> = {
  new: "新件",
  used: "旧件",
  damaged: "坏件",
  scrapped: "已报废",
};
const AVAILABILITY_LABELS: Record<string, string> = {
  available: "可用",
  reserved: "已占用",
  picking: "待拣货",
  outbound: "待出库",
  in_transit: "在途",
  arrived_pending: "到货待验",
  frozen: "冻结",
  return_pending: "待退回",
  scrap_pending: "待报废",
};


export default function FormalInventoryPage() {
  const [page, setPage] = useState<InventoryAccountPage | null>(null);
  const [afterId, setAfterId] = useState<string | null>(null);
  const [history, setHistory] = useState<Array<string | null>>([]);
  const [reloadToken, setReloadToken] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    const query = new URLSearchParams({ limit: "100" });
    if (afterId) query.set("after_id", afterId);
    setLoading(true);
    setError("");
    api<unknown>(`/v1/inventory/accounts?${query.toString()}`)
      .then(validateInventoryAccountPage)
      .then((result) => { if (!cancelled) setPage(result); })
      .catch((reason) => {
        if (!cancelled) {
          setPage(null);
          setError(showError(reason));
        }
      })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [afterId, reloadToken]);

  const established = page?.opening_balance_status === "established";
  function nextPage() {
    if (!page?.next_after_id) return;
    setHistory((values) => [...values, afterId]);
    setAfterId(page.next_after_id);
  }
  function previousPage() {
    if (!history.length) return;
    const previous = history[history.length - 1];
    setHistory((values) => values.slice(0, -1));
    setAfterId(previous);
  }

  return <section>
    <SectionHeader
      title="正式库存账户"
      subtitle="按正式权限读取 owner、物理库位、物料、状态和批次维度；不读取 v0.9 余额"
      actions={<Button tone="secondary" icon={<RefreshCw size={16} />} disabled={loading} onClick={() => setReloadToken((value) => value + 1)}>刷新</Button>}
    />
    {loading && <Loading label="正在校验期初证据并读取库存账户" />}
    {error && <>
      <div className="alert alert-error" role="alert"><CircleAlert size={18} />正式库存响应未通过校验：{error}</div>
      {history.length > 0 && <div className="section-actions"><Button tone="secondary" onClick={previousPage}>返回上一页</Button></div>}
    </>}
    {page && <>
      <section className="content-section formal-inventory-status">
        <div className="content-title">
          <div><h2>当前账本快照</h2><p>第 {history.length + 1} 页 · 游标 {page.ledger_cursor}</p></div>
          <span className={`status ${established ? "status-approved" : "status-pending"}`}>
            {established ? "期初已建立" : "期初未完整建立"}
          </span>
        </div>
        <div className={`alert ${established ? "alert-info" : "alert-warning"}`}>
          {established
            ? "本页数量来自已完成实盘、区域复核和总部复核的不可变账本；不同 SKU 不做跨物料求和。"
            : "当前授权范围存在未成立库存范围，整页数量已失败关闭；账户维度仅作盘点准备信息。"}
        </div>
      </section>
      <section className="content-section">
        <div className="content-title"><div><h2>账户明细</h2><p>最多 100 条；每次翻页均重新取得并校验服务端账本快照</p></div></div>
        {page.items.length === 0
          ? <Empty title="当前范围没有库存账户" detail={established ? "可信期初为零，未生成虚拟库存行。" : "期初完成前不会制造零库存事实。"} />
          : <div className="table-wrap"><table><thead><tr>
            <th>物料</th><th>物理库位</th><th>资产归属</th><th>状态</th><th>批次</th><th>数量</th>
          </tr></thead><tbody>{page.items.map((row) => <tr key={row.stock_account_id}>
            <td><strong className="mono">{row.sku_code}</strong><br /><span className="muted small">{row.material_name} · {row.base_unit}</span></td>
            <td>{row.location_name}<br /><span className="muted small mono">{row.location_code}</span></td>
            <td>{row.owner_org_name}<br /><span className="muted small">{row.custodian_person_name || "组织仓"}</span></td>
            <td>{CONDITION_LABELS[row.condition_code]} · {AVAILABILITY_LABELS[row.availability_bucket]}</td>
            <td className="mono">{row.lot_no || "—"}</td>
            <td className="mono">{row.quantity_status === "available" ? `${row.quantity} ${row.base_unit}` : "未开放"}</td>
          </tr>)}</tbody></table></div>}
        <div className="section-actions">
          <Button tone="secondary" disabled={loading || history.length === 0} onClick={previousPage}>上一页</Button>
          <Button tone="secondary" disabled={loading || !page.next_after_id} onClick={nextPage}>下一页</Button>
        </div>
      </section>
    </>}
  </section>;
}
