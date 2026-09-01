import { useCallback, useEffect, useMemo, useState } from "react";
import { CircleAlert, Eye, RefreshCw } from "lucide-react";
import { api } from "../api";
import { LEGACY_READ_ONLY_LABEL } from "../clientPolicy";
import type { Transfer, User } from "../types";
import { Button, Empty, Loading, Modal, SectionHeader, StatusPill, formatDate, showError } from "../ui";

const CONDITION: Record<string, string> = { good: "好件", old: "旧件", bad: "坏件" };
const TRANSFER_TYPE: Record<string, string> = {
  internal: "内部调拨",
  standard_transfer: "标准调拨",
  external_inbound: "外部入库",
  oam_inbound: "星星OAM入库",
  personal_request: "个人需求申请",
  regional_request: "区域备件申请",
  provider_request: "服务商申请",
  bad_return: "坏件退回",
  stagnant_return: "呆滞件退回",
};

export function TransferDetail({ transfer, onClose }: { transfer: Transfer; onClose: () => void }) {
  const sourceText = transfer.sourceWarehouse?.name || (transfer.status === "pending_approval" ? "审批后确定" : "星星OAM / 外部");
  return <Modal title="v0.9 物料单历史详情" onClose={onClose} wide>
    <div className="alert alert-info"><strong>{LEGACY_READ_ONLY_LABEL}</strong>：仅展示原始历史字段与旧状态，不提供审批、驳回、派发、收货、撤销或凭证写入，也不映射为正式版十条状态轴。</div>
    <div className="detail-heading"><div><strong className="mono detail-number">{transfer.number}</strong><StatusPill status={transfer.status} /></div><span>{TRANSFER_TYPE[transfer.transferType] || transfer.transferType} · {formatDate(transfer.createdAt)} · {transfer.createdBy}</span></div>
    <dl className="detail-grid">
      <div><dt>来源仓库</dt><dd>{sourceText}{transfer.sourceHolder ? ` · ${transfer.sourceHolder.name}` : ""}</dd></div>
      <div><dt>目标仓库</dt><dd>{transfer.targetWarehouse.name}</dd></div>
      <div><dt>接收人</dt><dd>{transfer.recipient?.name || "仓库公共库存"}</dd></div>
      <div><dt>申请人</dt><dd>{transfer.requester?.name || "-"}</dd></div>
      <div><dt>关联工单</dt><dd className="mono">{transfer.workOrderNumber || "-"}</dd></div>
      <div><dt>旧原型审批人</dt><dd>{transfer.approvedBy || "-"}</dd></div>
      <div><dt>外部单号</dt><dd className="mono">{transfer.externalReference || "-"}</dd></div>
      <div><dt>物流信息</dt><dd>{transfer.logisticsCompany || "-"} {transfer.trackingNumber && <span className="mono">{transfer.trackingNumber}</span>}</dd></div>
      <div><dt>旧原型派发时间</dt><dd>{formatDate(transfer.dispatchedAt)}</dd></div>
      <div><dt>旧原型收货时间</dt><dd>{formatDate(transfer.receivedAt)}</dd></div>
      <div><dt>备注</dt><dd>{transfer.note || "-"}</dd></div>
    </dl>
    <div className="table-wrap"><table><thead><tr><th>物料</th><th>类型</th><th className="num">数量</th><th>备注</th></tr></thead><tbody>{transfer.items.map((item) => <tr key={item.id}><td><strong className="mono">{item.code}</strong><span className="cell-subtitle">{item.name}</span></td><td>{CONDITION[item.condition] || item.condition}</td><td className="num">{item.quantity} {item.unit}</td><td>{item.remark || "-"}</td></tr>)}</tbody></table></div>
  </Modal>;
}

export default function TransfersPage({ personalOnly = false }: { user: User; personalOnly?: boolean }) {
  const [rows, setRows] = useState<Transfer[]>([]);
  const [status, setStatus] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<Transfer | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const query = personalOnly ? "/transfers?transfer_type=personal_request&limit=500" : "/transfers?limit=500";
      setRows(await api<Transfer[]>(query));
    } catch (err) {
      setError(showError(err));
    } finally {
      setLoading(false);
    }
  }, [personalOnly]);

  useEffect(() => { load(); }, [load]);
  const counts = useMemo(() => rows.reduce<Record<string, number>>((acc, row) => ({ ...acc, [row.status]: (acc[row.status] || 0) + 1 }), {}), [rows]);
  const visibleRows = useMemo(() => status ? rows.filter((row) => row.status === status) : rows, [rows, status]);

  return <>
    <SectionHeader
      title={personalOnly ? "v0.9 个人需求历史" : "v0.9 调拨历史"}
      subtitle="历史原型只读查询；旧状态按原值展示，不代表正式生产版申请、履约或入库状态"
      actions={<Button tone="secondary" icon={<RefreshCw size={17} />} onClick={load}>刷新</Button>}
    />
    <div className="alert alert-info"><strong>{LEGACY_READ_ONLY_LABEL}</strong>：所有旧 Transfer 写入口已隔离。</div>
    <div className="segmented">{[["", "全部"], ["pending_approval", "旧：待审批"], ["draft", "旧：待派发"], ["dispatched", "旧：运输中"], ["received", "旧：已入库"], ["rejected", "旧：已驳回"], ["cancelled", "旧：已取消"]].map(([value, label]) => <button key={value} className={status === value ? "active" : ""} onClick={() => setStatus(value)}>{label}{value && counts[value] ? <span>{counts[value]}</span> : null}</button>)}</div>
    {error && <div className="alert alert-error"><CircleAlert size={18} />{error}</div>}
    {loading ? <Loading /> : visibleRows.length === 0 ? <Empty title="没有命中历史记录" detail="当前筛选条件下没有 v0.9 物料单" /> : <section className="content-section table-section"><div className="table-wrap"><table className="transfer-table"><thead><tr><th>单号 / 类型</th><th>流向</th><th>物料</th><th>物流</th><th>时间</th><th>旧状态</th><th className="actions-col">操作</th></tr></thead><tbody>{visibleRows.map((row) => <tr key={row.id}><td><strong className="mono">{row.number}</strong><span className="cell-subtitle">{TRANSFER_TYPE[row.transferType] || row.transferType}{row.externalReference ? ` · ${row.externalReference}` : ""}</span></td><td>{row.sourceWarehouse?.name || (row.status === "pending_approval" ? "审批后确定" : "星星OAM / 外部")}<span className="cell-subtitle">至 {row.targetWarehouse.name}{row.recipient ? ` · ${row.recipient.name}` : ""}</span></td><td>{row.items.length} 种<span className="cell-subtitle">{row.items.slice(0, 2).map((item) => item.code).join("、")}{row.items.length > 2 ? "…" : ""}</span></td><td>{row.trackingNumber ? <span className="mono">{row.trackingNumber}</span> : "-"}<span className="cell-subtitle">{row.logisticsCompany || "未登记"}</span></td><td>{formatDate(row.createdAt)}</td><td><StatusPill status={row.status} /></td><td><button className="table-action" onClick={() => setSelected(row)}><Eye size={16} />只读查看</button></td></tr>)}</tbody></table></div></section>}
    {selected && <TransferDetail transfer={selected} onClose={() => setSelected(null)} />}
  </>;
}
