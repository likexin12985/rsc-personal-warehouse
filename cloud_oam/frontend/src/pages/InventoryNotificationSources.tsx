import { useEffect, useRef, useState } from "react";
import { apiNoReplay, jsonBody, mutationHeaders } from "../api";
import { type InventoryNotificationSource, inventorySourceIssue, validateInventoryNotificationSourcePage,
  validateInventoryNotificationSourceRecheck } from "../inventoryNotificationSources";
import { Button, Loading, showError } from "../ui";

const path = "/v1/notifications/inventory-sources";
const displayTime = (value: string) => new Date(value).toLocaleString("zh-CN", { hour12: false });

export default function InventoryNotificationSources({ canRecheck }: { canRecheck: boolean }) {
  const [items, setItems] = useState<InventoryNotificationSource[]>([]);
  const [next, setNext] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [target, setTarget] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [mustRefresh, setMustRefresh] = useState(false);
  const epoch = useRef(0);
  const writing = useRef(false);

  async function load(after: string | null = null) {
    const requestedEpoch = ++epoch.current;
    setLoading(true);
    setError("");
    setTarget(null);
    setReason("");
    try {
      const suffix = after ? `&after_id=${encodeURIComponent(after)}` : "";
      const page = validateInventoryNotificationSourcePage(await apiNoReplay(`${path}?limit=50${suffix}`));
      if (requestedEpoch !== epoch.current) return;
      setItems((old) => after ? [...old, ...page.items] : page.items);
      setNext(page.next_after_id);
      if (!after) setMustRefresh(false);
    } catch (cause) {
      if (requestedEpoch !== epoch.current) return;
      setError(showError(cause));
      setMustRefresh(true);
      if (!after) { setItems([]); setNext(null); }
    } finally {
      if (requestedEpoch === epoch.current) setLoading(false);
    }
  }

  useEffect(() => { void load(); return () => { epoch.current += 1; }; }, []);

  async function recheck(item: InventoryNotificationSource) {
    if (!canRecheck || mustRefresh || loading || writing.current || !reason.trim() || item.status !== "blocked") return;
    writing.current = true;
    setBusy(true);
    setError("");
    setNotice("");
    const requestEpoch = ++epoch.current;
    try {
      const result = validateInventoryNotificationSourceRecheck(await apiNoReplay(`${path}/${encodeURIComponent(item.outbox_id)}/recheck`, {
        method: "POST", ...mutationHeaders("inventory-source-recheck"),
        ...jsonBody({ expected_audit_id: item.latest_audit_id, expected_source_sha256: item.source_sha256, reason: reason.trim() }),
      }), item.outbox_id);
      if (requestEpoch !== epoch.current) return;
      // The reply is an immutable recheck result, not delivery confirmation.
      setItems((old) => old.map((row) => row.outbox_id === item.outbox_id ? result.item : row));
      setTarget(null);
      setReason("");
      setNotice(`${result.replayed ? "已回读原复核结果。" : "复核结果已记录。"}${inventorySourceIssue(result.item)}`);
    } catch (cause) {
      if (requestEpoch !== epoch.current) return;
      setError(`复核结果未确认，请先刷新来源记录：${showError(cause)}`);
      setMustRefresh(true);
      setTarget(null);
    } finally {
      writing.current = false;
      setBusy(false);
    }
  }

  return <section className="content-section" aria-label="库存通知来源异常">
    <div className="content-title"><div><h2>库存通知来源异常</h2><p>按原来源复核通知生成情况。排队、发送和送达请分别查看投递记录。</p></div>
      <Button tone="secondary" disabled={loading || busy} onClick={() => { setNotice(""); void load(); }}>刷新来源记录</Button></div>
    {error && <div className="alert alert-error" role="alert">{error}</div>}
    {notice && <div className="form-notice" role="status">{notice}</div>}
    {loading ? <Loading label="正在读取来源异常" /> : items.length === 0 ? <div className="empty-state">暂无来源异常记录</div> : <div className="notification-list">
      {items.map((item) => <article className="content-section" key={item.outbox_id}>
        <div className="content-title"><h3>{item.status === "projected" ? "已生成通知" : "待复核来源"}</h3><span className="status">{item.status === "projected" ? "已复核" : "仍隔离"}</span></div>
        <p>{inventorySourceIssue(item)}</p><p className="cell-subtitle">来源编号 {item.outbox_id}</p>
        <p className="cell-subtitle">首次隔离 {displayTime(item.isolated_at)}{item.last_checked_at ? ` · 最近复核 ${displayTime(item.last_checked_at)}` : " · 尚未复核"}</p>
        {canRecheck && item.status === "blocked" && (target === item.outbox_id ? <div className="notification-retry-form">
          <input aria-label="来源复核原因" maxLength={500} value={reason} disabled={busy} placeholder="填写本次核查情况" onChange={(event) => setReason(event.target.value)} />
          <div className="form-actions"><Button tone="secondary" disabled={busy} onClick={() => { setTarget(null); setReason(""); }}>取消复核</Button>
            <Button disabled={busy || mustRefresh || !reason.trim()} onClick={() => void recheck(item)}>{busy ? "正在复核" : "确认复核"}</Button></div>
        </div> : <Button tone="secondary" disabled={busy || mustRefresh} onClick={() => { setTarget(item.outbox_id); setReason(""); setNotice(""); }}>复核来源</Button>)}
      </article>)}
      {next && <Button tone="secondary" disabled={busy || loading || mustRefresh} onClick={() => void load(next)}>加载更多来源</Button>}
    </div>}
  </section>;
}
