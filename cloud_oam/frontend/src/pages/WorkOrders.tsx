import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ChevronLeft,
  ChevronRight,
  ExternalLink,
  FileClock,
  ReceiptText,
  RefreshCw,
  Search,
  ShieldCheck,
  Wrench,
} from "lucide-react";

import { api, ApiError } from "../api";
import type {
  WorkOrderCheckItem,
  WorkOrderDetail,
  WorkOrderListResponse,
  WorkOrderListRow,
} from "../types";
import { Button, Empty, Field, Loading, Modal, SectionHeader, showError } from "../ui";
import { listWorkOrderMaterialOperations, type WorkOrderMaterialOperationHistory } from "../workOrderMaterialOperations";

const PAGE_SIZE = 50;
const STATUS_LABELS: Record<string, string> = {
  to_be_create: "待创建",
  create: "已创建",
  wait_receive: "待接单",
  wait_connect: "待联络",
  wait_process: "待处理",
  processing: "处理中",
  process_finish: "处理完成",
  transferring: "转派待接收",
  wait_client_accept: "待客户验收",
  client_accept_pass: "客户验收通过",
  wait_platform_accept: "待平台验收",
  platform_accept_pass: "平台验收通过",
  wait_source_accept: "待甲方验收",
  end: "已完结",
  stopping: "终止中",
  stopped: "已终止",
  rejected: "已拒绝",
  closed: "已关闭",
  hang: "挂起",
};
const COLUMN_TYPE_LABELS: Record<string, string> = {
  "1": "是/否",
  "2": "文本",
  "3": "单选",
  "4": "多选",
  "5": "日期/时间",
  "6": "图片",
  "7": "数值",
  "8": "附件",
};
const TERMINAL_STATUSES = new Set(["end", "stopped", "closed", "rejected"]);

type DetailTab = "basic" | "work-items" | "cost" | "timeline" | "material-operations";

export function formatOamDate(value?: string | null): string {
  if (!value) return "-";
  const parsed = new Date(value.includes("T") ? value : value.replace(" ", "T"));
  if (Number.isNaN(parsed.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(parsed);
}

export function workItemText(item: Pick<WorkOrderCheckItem, "columnType" | "result">): string {
  const { result } = item;
  if (result === null || result === undefined || result === "") return "未填写";
  if (item.columnType === "1") {
    const normalized = String(result).trim().toUpperCase();
    if (["Y", "YES", "TRUE", "1"].includes(normalized)) return "是";
    if (["N", "NO", "FALSE", "0"].includes(normalized)) return "否";
  }
  if (Array.isArray(result)) {
    return result.length ? result.map((value) => String(value)).join("、") : "未填写";
  }
  if (typeof result === "object") return JSON.stringify(result);
  return String(result);
}

export function materialOperationLabel(operationType: string): string {
  return ({ consume: "消耗", release: "释放", occupy: "占用", recover: "收回", reverse: "冲销" } as Record<string, string>)[operationType] || operationType;
}

export function normalizeWorkOrderDetail(value: WorkOrderDetail): WorkOrderDetail {
  return {
    ...value,
    counts: value.counts || {},
    targets: Array.isArray(value.targets) ? value.targets : [],
    materials: Array.isArray(value.materials) ? value.materials : [],
    fees: Array.isArray(value.fees) ? value.fees : [],
    workItems: Array.isArray(value.workItems) ? value.workItems : [],
    quotations: Array.isArray(value.quotations) ? value.quotations : [],
    relatedOrders: Array.isArray(value.relatedOrders) ? value.relatedOrders : [],
    timeline: Array.isArray(value.timeline) ? value.timeline : [],
    errors: Array.isArray(value.errors) ? value.errors : [],
  };
}

function statusClass(code: string): string {
  if (TERMINAL_STATUSES.has(code)) return "work-order-status terminal";
  if (code.includes("wait") || code === "hang") return "work-order-status waiting";
  return "work-order-status active";
}

export default function WorkOrdersPage() {
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("");
  const [province, setProvince] = useState("");
  const [warranty, setWarranty] = useState("");
  const [page, setPage] = useState(0);
  const [data, setData] = useState<WorkOrderListResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<WorkOrderListRow | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const params = new URLSearchParams({
        search: query,
        offset: String(page * PAGE_SIZE),
        limit: String(PAGE_SIZE),
      });
      if (status) params.set("status", status);
      if (province) params.set("province", province);
      if (warranty) params.set("warranty", warranty);
      setData(await api<WorkOrderListResponse>(`/work-orders?${params}`));
    } catch (err) {
      setError(showError(err));
    } finally {
      setLoading(false);
    }
  }, [page, province, query, status, warranty]);

  useEffect(() => { load(); }, [load]);

  function submit(event: React.FormEvent) {
    event.preventDefault();
    setPage(0);
    setQuery(search.trim());
  }

  function changeFilter(setter: (value: string) => void, value: string) {
    setPage(0);
    setter(value);
  }

  const pageCount = Math.max(1, Math.ceil((data?.total || 0) / PAGE_SIZE));
  const statusOptions = Object.entries(data?.summary.statuses || {})
    .sort((a, b) => b[1] - a[1]);
  const provinceOptions = Object.entries(data?.summary.provinces || {})
    .sort((a, b) => b[1] - a[1]);

  return <>
    <SectionHeader
      title="工单管理"
      subtitle="星星 OAM 工单只读镜像，按人员和省份隔离查看"
      actions={<Button tone="secondary" icon={<RefreshCw size={17} />} onClick={load}>刷新</Button>}
    />
    <div className="work-order-source-note">
      <ShieldCheck size={17} />
      <span>只读数据源</span>
      <strong>{data?.snapshot ? formatOamDate(data.snapshot.completedAt || data.snapshot.at) : "等待首次同步"}</strong>
      {data?.snapshot && <code>{data.snapshot.id}</code>}
    </div>
    <form className="work-order-toolbar" onSubmit={submit}>
      <Field label="工单 / 服务单 / 设备 / 执行人">
        <div className="search-box"><Search size={18} /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="输入编号或关键词" /></div>
      </Field>
      <Field label="状态">
        <select value={status} onChange={(event) => changeFilter(setStatus, event.target.value)}>
          <option value="">全部状态</option>
          {statusOptions.map(([value, count]) => <option key={value} value={value}>{STATUS_LABELS[value] || value} ({count})</option>)}
        </select>
      </Field>
      <Field label="省份">
        <select value={province} onChange={(event) => changeFilter(setProvince, event.target.value)}>
          <option value="">全部省份</option>
          {provinceOptions.map(([value, count]) => <option key={value} value={value}>{value} ({count})</option>)}
        </select>
      </Field>
      <Field label="质保">
        <select value={warranty} onChange={(event) => changeFilter(setWarranty, event.target.value)}>
          <option value="">全部</option>
          <option value="保内">保内</option>
          <option value="保外">保外</option>
          <option value="待确认">待确认</option>
        </select>
      </Field>
      <Button type="submit" icon={<Search size={17} />}>查询</Button>
    </form>
    {data && <div className="inventory-summary work-order-summary">
      <span>可见工单<strong>{data.summary.available}</strong></span>
      <span>当前进行中<strong>{data.summary.active}</strong></span>
      <span>详情已同步<strong>{data.summary.withDetail}</strong></span>
      <span>本次命中<strong>{data.total}</strong></span>
    </div>}
    {error && <div className="alert alert-error">{error}</div>}
    {loading ? <Loading label="正在读取工单镜像" /> : !data?.items.length ? <Empty title="没有命中工单" detail="请调整编号、状态、省份或质保条件" /> : <>
      <section className="content-section table-section work-order-table-section">
        <div className="table-wrap"><table className="work-order-table"><thead><tr><th>工单编号</th><th>状态</th><th>工单信息</th><th>执行人</th><th>设备 / 品牌</th><th>地区</th><th>更新时间</th><th>详情</th></tr></thead><tbody>
          {data.items.map((row) => <tr key={row.code}>
            <td><button className="work-order-code mono" onClick={() => setSelected(row)}>{row.code}</button><span className="cell-subtitle mono">{row.serviceCode || "-"}</span></td>
            <td><span className={statusClass(row.statusCode)}>{row.status}</span><span className="cell-subtitle">{row.warranty || "待确认"}</span></td>
            <td><strong>{row.type}</strong><span className="cell-subtitle" title={row.title}>{row.title || "-"}</span></td>
            <td>{row.executor || "未指派"}<span className="cell-subtitle">{row.company || ""}</span></td>
            <td>{row.deviceCodes?.join("、") || "-"}<span className="cell-subtitle">{row.brandName || row.deviceModels?.join("、") || ""}</span></td>
            <td>{row.province || "-"}<span className="cell-subtitle">{[row.city, row.area].filter(Boolean).join(" · ")}</span></td>
            <td>{formatOamDate(row.updateTime || row.createTime)}</td>
            <td><button className="table-action" onClick={() => setSelected(row)}>{row.detailAvailable ? "查看详情" : "同步中"}</button></td>
          </tr>)}
        </tbody></table></div>
        <div className="work-order-mobile-list">{data.items.map((row) => <button key={row.code} className="work-order-mobile-row" onClick={() => setSelected(row)}>
          <span className="work-order-mobile-head"><strong className="mono">{row.code}</strong><i className={statusClass(row.statusCode)}>{row.status}</i></span>
          <span>{row.type} · {row.title || "无标题"}</span>
          <small>{row.executor || "未指派"} · {row.province || "地区未返回"}</small>
          <small>{row.deviceCodes?.join("、") || "设备未返回"} · {formatOamDate(row.updateTime || row.createTime)}</small>
        </button>)}</div>
      </section>
      <div className="pagination"><span>第 {page + 1} / {pageCount} 页，共 {data.total} 条</span><div><Button tone="secondary" icon={<ChevronLeft size={17} />} disabled={page === 0} onClick={() => setPage((value) => Math.max(0, value - 1))}>上一页</Button><Button tone="secondary" icon={<ChevronRight size={17} />} disabled={page + 1 >= pageCount} onClick={() => setPage((value) => value + 1)}>下一页</Button></div></div>
    </>}
    {selected && <WorkOrderDetailModal row={selected} onClose={() => setSelected(null)} />}
  </>;
}

function WorkOrderDetailModal({ row, onClose }: { row: WorkOrderListRow; onClose: () => void }) {
  const [detail, setDetail] = useState<WorkOrderDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [tab, setTab] = useState<DetailTab>("basic");

  useEffect(() => {
    let active = true;
    setLoading(true);
    api<WorkOrderDetail>(`/work-orders/${encodeURIComponent(row.code)}`)
      .then((value) => { if (active) setDetail(normalizeWorkOrderDetail(value)); })
      .catch((err) => {
        if (!active) return;
        setError(err instanceof ApiError && err.status === 409 ? "该工单已进入列表，完整详情正在分批同步。当前不会回源临时抓取，请稍后刷新。" : showError(err));
      })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [row.code]);

  return <Modal title={`工单详情 · ${row.code}`} onClose={onClose} wide>
    {loading ? <Loading label="正在读取工单详情" /> : error ? <div className="work-order-detail-blocked"><FileClock size={32} /><strong>详情暂不可用</strong><p>{error}</p></div> : detail && <>
      <div className="work-order-detail-heading">
        <div><span className={statusClass(detail.summary.statusCode)}>{detail.summary.status}</span><strong>{detail.summary.type}</strong><span>{detail.summary.warranty}</span></div>
        <small>镜像时间 {formatOamDate(detail.snapshot?.completedAt || detail.queriedAt)}</small>
      </div>
      <div className="segmented work-order-detail-tabs" role="tablist">
        <button className={tab === "basic" ? "active" : ""} onClick={() => setTab("basic")}>基本信息</button>
        <button className={tab === "work-items" ? "active" : ""} onClick={() => setTab("work-items")}>工作项<span>{detail.counts.checkItems || 0}</span></button>
        <button className={tab === "cost" ? "active" : ""} onClick={() => setTab("cost")}>费用 / 报价<span>{detail.counts.quotations || 0}</span></button>
        <button className={tab === "timeline" ? "active" : ""} onClick={() => setTab("timeline")}>操作日志<span>{detail.counts.timeline || 0}</span></button>
        <button className={tab === "material-operations" ? "active" : ""} onClick={() => setTab("material-operations")}>物料履约</button>
      </div>
      {tab === "basic" && <BasicTab detail={detail} />}
      {tab === "work-items" && <WorkItemsTab detail={detail} />}
      {tab === "cost" && <CostTab detail={detail} />}
      {tab === "timeline" && <TimelineTab detail={detail} />}
      {tab === "material-operations" && <MaterialOperationsTab workOrderId={detail.summary.id} />}
    </>}
  </Modal>;
}

function MaterialOperationsTab({ workOrderId }: { workOrderId: string }) {
  const [history, setHistory] = useState<WorkOrderMaterialOperationHistory | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const load = useCallback(() => {
    let active = true;
    setLoading(true); setError("");
    listWorkOrderMaterialOperations(workOrderId)
      .then((value) => { if (active) setHistory(value); })
      .catch((err) => { if (active) setError(showError(err)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [workOrderId]);
  useEffect(() => load(), [load]);
  if (loading) return <Loading label="正在读取工单物料履约记录" />;
  if (error) return <div className="work-order-detail-blocked"><FileClock size={32} /><strong>物料履约记录暂不可用</strong><p>{error}</p></div>;
  if (!history?.items.length) return <div className="work-order-tab-panel"><Empty title="暂无正式物料履约记录" detail="分配、占用、消耗、释放或收回完成后会显示在这里" /><div className="form-actions"><Button tone="secondary" icon={<RefreshCw size={16} />} disabled={loading} onClick={load}>刷新履约记录</Button></div></div>;
  return <div className="work-order-tab-panel"><div className="form-actions"><Button tone="secondary" icon={<RefreshCw size={16} />} disabled={loading} onClick={load}>刷新履约记录</Button></div><div className="table-wrap"><table><thead><tr><th>操作号</th><th>操作类型</th><th>过账事务</th><th>状态</th></tr></thead><tbody>{history.items.map((item) => <tr key={item.operation_id}><td className="mono">{item.operation_no}</td><td>{materialOperationLabel(item.operation_type)}</td><td className="mono">{item.posting_transaction_id}</td><td>{item.status === "posted" ? "已过账" : item.status}</td></tr>)}</tbody></table></div></div>;
}

function BasicTab({ detail }: { detail: WorkOrderDetail }) {
  const summary = detail.summary;
  return <div className="work-order-tab-panel">
    <dl className="detail-grid work-order-basic-grid">
      <DetailField label="工单编号" value={summary.code} mono />
      <DetailField label="服务单号" value={summary.serviceCode} mono />
      <DetailField label="外联单号" value={summary.externalCode} mono />
      <DetailField label="工单标题" value={summary.title} />
      <DetailField label="优先级" value={summary.priority} />
      <DetailField label="工单来源" value={[summary.source, summary.sourceSecondary].filter(Boolean).join(" · ")} />
      <DetailField label="执行人" value={summary.executor} />
      <DetailField label="联系电话" value={summary.executorPhone} mono />
      <DetailField label="执行企业" value={summary.company} />
      <DetailField label="客户" value={summary.customerName} />
      <DetailField label="客户电话" value={summary.customerPhone} mono />
      <DetailField label="地区" value={[summary.province, summary.city, summary.area].filter(Boolean).join(" · ")} />
      <DetailField label="服务地址" value={summary.address} wide />
      <DetailField label="创建时间" value={formatOamDate(summary.createTime)} />
      <DetailField label="完结时间" value={formatOamDate(summary.completedAt)} />
      <DetailField label="工单描述" value={summary.overview} wide />
    </dl>
    <Subsection title="服务设备" count={detail.targets.length}>
      {detail.targets.length ? <div className="table-wrap"><table><thead><tr><th>设备编号 / SN</th><th>型号</th><th>品牌 / 站点</th><th>质保</th><th>质保区间</th></tr></thead><tbody>{detail.targets.map((target, index) => <tr key={`${target.deviceCode}-${index}`}><td><strong className="mono">{target.deviceCode || "-"}</strong><span className="cell-subtitle mono">{target.deviceSn || "-"}</span></td><td>{target.model || "-"}</td><td>{target.manufacturer || detail.summary.brandName || "-"}<span className="cell-subtitle">{target.stationName || ""}</span></td><td>{target.warranty || "待确认"}</td><td>{[target.warrantyStart, target.warrantyEnd].filter(Boolean).join(" 至 ") || "-"}</td></tr>)}</tbody></table></div> : <Empty title="OAM 未返回服务设备" />}
    </Subsection>
    {detail.relatedOrders.length > 0 && <Subsection title="关联工单" count={detail.relatedOrders.length}><div className="related-work-orders">{detail.relatedOrders.map((related) => <div key={related.code}><strong className="mono">{related.code}</strong><span>{related.type} · {related.status}</span><small>{related.title || "-"}</small></div>)}</div></Subsection>}
  </div>;
}

function WorkItemsTab({ detail }: { detail: WorkOrderDetail }) {
  if (!detail.workItems.length) return <Empty title="没有动态工作项结果" detail="该工单未配置工作项，或结果尚未同步" />;
  return <div className="work-item-groups">{detail.workItems.map((group, groupIndex) => <section className="work-item-group" key={group.id || `${group.workItem}-${groupIndex}`}>
    <header><div><Wrench size={18} /><strong>工作项 {group.workItem || groupIndex + 1}</strong></div><span>{group.targetId ? `设备 ${group.targetId}` : "通用工作项"}</span></header>
    <div className="work-item-rows">{group.items.map((item) => <div className="work-item-row" key={item.id}>
      <div><strong>{item.title}</strong><span>{COLUMN_TYPE_LABELS[item.columnType] || `类型 ${item.columnType}`}{item.required ? " · 必填" : ""}</span></div>
      <div className={workItemText(item) === "未填写" ? "work-item-empty" : ""}>{item.columnType === "4" && Array.isArray(item.result) ? <div className="work-item-tags">{item.result.map((value, index) => <span key={`${String(value)}-${index}`}>{String(value)}</span>)}</div> : workItemText(item)}{item.remark && <small>备注：{item.remark}</small>}{item.media.length > 0 && <div className="work-item-media">{item.media.map((media, index) => <a key={`${media.url}-${index}`} href={media.url} target="_blank" rel="noreferrer"><ExternalLink size={14} />{media.name || `附件 ${index + 1}`}</a>)}</div>}</div>
    </div>)}</div>
  </section>)}</div>;
}

function CostTab({ detail }: { detail: WorkOrderDetail }) {
  return <div className="work-order-tab-panel">
    <Subsection title="报价单" count={detail.quotations.length} icon={<ReceiptText size={18} />}>
      {detail.quotations.length ? <div className="table-wrap"><table><thead><tr><th>报价单号</th><th>含税总价</th><th>支付状态</th><th>回款状态</th><th>付款方 / 收款方</th><th>支付时间</th></tr></thead><tbody>{detail.quotations.map((quotation) => <tr key={quotation.id || quotation.code}><td><strong className="mono">{quotation.code || "-"}</strong></td><td>¥ {quotation.totalPrice.toFixed(2)}</td><td><span className={`quotation-status ${quotation.statusCode === "1" ? "paid" : ""}`}>{quotation.status}</span></td><td><span className={`quotation-status ${quotation.collectionStatusCode === "paymentCollection" ? "collected" : ""}`}>{quotation.collectionStatus}</span></td><td>{quotation.payer || "-"}<span className="cell-subtitle">{quotation.payee || ""}</span></td><td>{formatOamDate(quotation.paymentTime)}</td></tr>)}</tbody></table></div> : <Empty title="没有报价单" />}
    </Subsection>
    <Subsection title="费用明细" count={detail.fees.length}>
      {detail.fees.length ? <div className="table-wrap"><table><thead><tr><th>费用类型</th><th>内容 / 物料</th><th className="num">数量</th><th className="num">单价</th><th className="num">金额</th><th>操作人 / 时间</th></tr></thead><tbody>{detail.fees.map((fee, index) => <tr key={fee.id || `${fee.kindCode}-${index}`}><td>{fee.kind}</td><td>{fee.title || "-"}<span className="cell-subtitle mono">{fee.materialCode || fee.remark || ""}</span></td><td className="num">{fee.quantity}{fee.unit || ""}</td><td className="num">¥ {fee.unitPrice.toFixed(2)}</td><td className="num"><strong>¥ {fee.totalPrice.toFixed(2)}</strong></td><td>{fee.operator || "-"}<span className="cell-subtitle">{formatOamDate(fee.time)}</span></td></tr>)}</tbody></table></div> : <Empty title="没有费用明细" />}
    </Subsection>
  </div>;
}

function TimelineTab({ detail }: { detail: WorkOrderDetail }) {
  if (!detail.timeline.length) return <Empty title="没有操作日志" />;
  return <ol className="work-order-timeline">{detail.timeline.map((event, index) => <li key={event.recordId || `${event.time}-${event.title}-${index}`}>
    <span className={`timeline-dot tone-${event.tone}`} />
    <div className="timeline-time">{formatOamDate(event.time)}</div>
    <div className="timeline-main"><div><span>{event.category}</span><strong>{event.title}</strong></div>{event.content && <p>{event.content}</p>}<small>{[event.actor, event.subtitle, event.source].filter(Boolean).join(" · ")}</small></div>
  </li>)}</ol>;
}

function DetailField({ label, value, mono = false, wide = false }: { label: string; value?: string; mono?: boolean; wide?: boolean }) {
  return <div className={wide ? "detail-field-wide" : ""}><dt>{label}</dt><dd className={mono ? "mono" : ""}>{value || "-"}</dd></div>;
}

function Subsection({ title, count, icon, children }: { title: string; count: number; icon?: React.ReactNode; children: React.ReactNode }) {
  return <section className="work-order-subsection"><header><div>{icon}<strong>{title}</strong></div><span>{count}</span></header>{children}</section>;
}
