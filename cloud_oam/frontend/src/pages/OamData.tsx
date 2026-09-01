import { useCallback, useEffect, useState } from "react";
import { RefreshCw, Search, ShieldCheck } from "lucide-react";
import { api } from "../api";
import { roleLabel } from "../clientPolicy";
import type { OamOrder, OamPersonnel, User } from "../types";
import { Button, Empty, Field, Loading, SectionHeader, formatDate, showError } from "../ui";

const ORDER_STATUS: Record<string, string> = {
  waitingApproval: "待审批",
  waitingDelivery: "待发货",
  waitingReceive: "待收货",
  finished: "已完成",
  invalided: "已作废",
  refused: "已驳回",
};

const ORDER_TYPE: Record<number, string> = {
  1: "标准调拨",
  2: "区域调拨",
  4: "服务商申请",
};

type View = "orders" | "personnel";
type PersonnelResponse = {
  summary: { total: number; sourceActive: number; loginEligible: number; loginEnabled: number };
  items: OamPersonnel[];
};
type OrderResponse = {
  summary: { total: number; active: number; activeLines: number; statuses: Record<string, number> };
  total: number;
  items: OamOrder[];
};

export default function OamDataPage({ user }: { user: User }) {
  const canAuditAll = user.role === "admin";
  const [view, setView] = useState<View>("orders");
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [personnelState, setPersonnelState] = useState("all");
  const [orderStatus, setOrderStatus] = useState("");
  const [personnel, setPersonnel] = useState<PersonnelResponse | null>(null);
  const [orders, setOrders] = useState<OrderResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      if (view === "personnel") {
        const params = new URLSearchParams({ search: query, state: personnelState });
        setPersonnel(await api<PersonnelResponse>(`/integrations/oam/personnel?${params}`));
      } else {
        const params = new URLSearchParams({ search: query, limit: "200" });
        if (orderStatus) params.set("status", orderStatus);
        setOrders(await api<OrderResponse>(`/integrations/oam/orders?${params}`));
      }
    } catch (err) {
      setError(showError(err));
    } finally {
      setLoading(false);
    }
  }, [orderStatus, personnelState, query, view]);

  useEffect(() => { load(); }, [load]);

  function submitSearch(event: React.FormEvent) {
    event.preventDefault();
    setQuery(search.trim());
  }

  return <>
    <SectionHeader
      title="OAM 数据"
      subtitle={canAuditAll ? "星星调拨申请与蔚来人员的 v0.9 只读投影；正式身份映射尚未开放" : "与当前账号关联的星星调拨申请只读查询"}
      actions={<Button tone="secondary" icon={<RefreshCw size={17} />} onClick={load}>刷新</Button>}
    />
    <div className="segmented">
      <button className={view === "orders" ? "active" : ""} onClick={() => { setView("orders"); setSearch(""); setQuery(""); }}>调拨申请<span>{orders?.summary.total ?? 0}</span></button>
      {canAuditAll && <button className={view === "personnel" ? "active" : ""} onClick={() => { setView("personnel"); setSearch(""); setQuery(""); }}>人员映射<span>{personnel?.summary.total ?? 0}</span></button>}
    </div>
    <form className="oam-data-toolbar" onSubmit={submitSearch}>
      <Field label={view === "orders" ? "单号 / 申请人" : "姓名 / 手机号 / 工号"}>
        <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="输入关键词" />
      </Field>
      {view === "orders" ? <Field label="状态">
        <select value={orderStatus} onChange={(event) => setOrderStatus(event.target.value)}>
          <option value="">全部状态</option>
          {Object.entries(ORDER_STATUS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
      </Field> : <Field label="登录状态">
        <select value={personnelState} onChange={(event) => setPersonnelState(event.target.value)}>
          <option value="all">全部人员</option>
          <option value="eligible">待开通</option>
          <option value="enabled">已开通</option>
          <option value="blocked">不可开通</option>
        </select>
      </Field>}
      <Button type="submit" icon={<Search size={17} />}>查询</Button>
    </form>
    {error && <div className="alert alert-error">{error}</div>}
    {loading ? <Loading /> : view === "orders" ? <OrdersView data={orders} /> : <PersonnelView data={personnel} />}
  </>;
}

function OrdersView({ data }: { data: OrderResponse | null }) {
  if (!data?.items.length) return <Empty title="没有命中调拨申请" />;
  return <>
    <div className="inventory-summary">
      <span>全部<strong>{data.summary.total}</strong></span>
      <span>当前待处理<strong>{data.summary.active}</strong></span>
      <span>待处理物料行<strong>{data.summary.activeLines}</strong></span>
      <span>本次命中<strong>{data.total}</strong></span>
    </div>
    <section className="content-section table-section"><div className="table-wrap"><table><thead><tr><th>申请单号</th><th>类型 / 状态</th><th>申请人</th><th>目标仓库</th><th>物料明细</th><th>创建时间</th></tr></thead><tbody>{data.items.map((row) => { const status = row.transferStatus || row.status; return <tr key={row.materialApplyId}><td><strong className="mono">{row.materialApplyId}</strong><span className="cell-subtitle">{row.principalName || "-"}</span></td><td>{ORDER_TYPE[row.type] || `类型${row.type}`}<span className="cell-subtitle">{ORDER_STATUS[status] || status}</span></td><td>{row.applicantName || "-"}</td><td>{row.warehouseLocationName || "-"}<span className="cell-subtitle">{row.warehousePositionName || ""}</span></td><td>{row.lines.length ? <div className="oam-line-list">{row.lines.map((line, index) => <span key={line.id || `${line.materialCode}-${index}`}><strong className="mono">{line.materialCode || "-"}</strong> × {line.applyNum ?? 0}{line.remark ? ` · ${line.remark}` : ""}</span>)}</div> : "-"}</td><td>{formatDate(row.createTime)}</td></tr>; })}</tbody></table></div></section>
  </>;
}

function PersonnelView({ data }: { data: PersonnelResponse | null }) {
  if (!data?.items.length) return <Empty title="没有命中人员" />;
  return <>
    <div className="inventory-summary">
      <span>OAM人员<strong>{data.summary.total}</strong></span>
      <span>在职启用<strong>{data.summary.sourceActive}</strong></span>
      <span>可开通<strong>{data.summary.loginEligible}</strong></span>
      <span>已映射登录<strong>{data.summary.loginEnabled}</strong></span>
    </div>
    <div className="alert alert-info">当前人员与账号信息仅供迁移核对，不提供开通、停用或角色变更。</div>
    <section className="content-section table-section"><div className="table-wrap"><table><thead><tr><th>姓名 / 工号</th><th>OAM账号</th><th>手机号</th><th>OAM状态</th><th>旧映射状态</th></tr></thead><tbody>{data.items.map((row) => <tr key={row.id}><td><strong>{row.name}</strong><span className="cell-subtitle mono">{row.jobNo || row.oamEmployeeId || "-"}</span></td><td className="mono">{row.account || row.oamAccountId}</td><td className="mono">{row.mobile || "-"}</td><td>{row.sourceActive ? <span className="status status-received">在职启用</span> : <span className="status status-cancelled">已停用</span>}<span className="cell-subtitle">{row.eligibilityReason}</span></td><td>{row.loginEnabled && row.user ? <><span className="session-device"><ShieldCheck size={16} />{roleLabel(row.user.role)}</span><span className="cell-subtitle">{row.user.province || "全国"}</span></> : "未开通"}</td></tr>)}</tbody></table></div></section>
  </>;
}
