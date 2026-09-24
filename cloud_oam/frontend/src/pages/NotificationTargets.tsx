import { useEffect, useRef, useState } from "react";
import { apiNoReplay, jsonBody, mutationHeaders } from "../api";
import { type NotificationTarget, type LegacyNotificationEvent, type TargetChannel, targetStates,
  validateNotificationTargetPage, validateLegacyNotificationEventPage, validateTargetRecovery } from "../notificationTargets";
import { Button, Loading, showError } from "../ui";

const path = "/v1/notifications/person-targets";
type View = "unbound" | "all" | "legacy";
const displayTime = (value: string) => new Date(value).toLocaleString("zh-CN", { hour12: false });

export default function NotificationTargets({ canRecover }: { canRecover: boolean }) {
  const [view, setView] = useState<View>("unbound");
  const [items, setItems] = useState<NotificationTarget[]>([]);
  const [legacy, setLegacy] = useState<LegacyNotificationEvent[]>([]);
  const [next, setNext] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [target, setTarget] = useState<string | null>(null);
  const [selectedChannel, setSelectedChannel] = useState<TargetChannel>("sms");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [mustRefresh, setMustRefresh] = useState(false);
  const epoch = useRef(0);
  const writing = useRef(false);

  async function load(selectedView: View, after: string | null = null, recoveryCompleted = false) {
    const requestEpoch = ++epoch.current;
    setLoading(true); setError(""); setTarget(null); setReason("");
    try {
      const suffix = after ? `&after_id=${encodeURIComponent(after)}` : "";
      if (selectedView === "legacy") {
        const page = validateLegacyNotificationEventPage(await apiNoReplay(`${path}/legacy-events?limit=50${suffix}`));
        if (requestEpoch !== epoch.current) return;
        if (after && page.items.some((row) => legacy.some((old) => old.event_id === row.event_id))) throw new Error("历史通知分页重复，请刷新");
        setLegacy((old) => after ? [...old, ...page.items] : page.items); setItems([]); setNext(page.next_after_id);
      } else {
        const page = validateNotificationTargetPage(await apiNoReplay(`${path}?limit=50&unbound_only=${selectedView === "unbound"}${suffix}`));
        if (requestEpoch !== epoch.current) return;
        if (after && page.items.some((row) => items.some((old) => old.target_id === row.target_id))) throw new Error("通知目标分页重复，请刷新");
        setItems((old) => after ? [...old, ...page.items] : page.items); setLegacy([]); setNext(page.next_after_id);
      }
      // Only a fresh target query can release an uncertain recovery latch.
      if (!after && selectedView !== "legacy" && (!writing.current || recoveryCompleted)) setMustRefresh(false);
    } catch (cause) {
      if (requestEpoch !== epoch.current) return;
      setError(showError(cause)); setMustRefresh(true);
      if (!after) { setItems([]); setLegacy([]); setNext(null); }
    } finally {
      if (requestEpoch === epoch.current) setLoading(false);
    }
  }

  useEffect(() => {
    void load(view);
    return () => { epoch.current += 1; };
    // A permission change invalidates in-flight results and reloads observations.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view, canRecover]);

  async function recover(item: NotificationTarget) {
    if (!canRecover || mustRefresh || loading || writing.current || !reason.trim() || item.state !== "ready"
      || !item.available_channels.includes(selectedChannel)) return;
    writing.current = true; setBusy(true); setMustRefresh(true); setError(""); setNotice("");
    const requestEpoch = ++epoch.current;
    const channel = selectedChannel;
    try {
      const result = validateTargetRecovery(await apiNoReplay(`${path}/${encodeURIComponent(item.target_id)}/recover`, {
        method: "POST", ...mutationHeaders("notification-target-recovery"),
        ...jsonBody({ expected_snapshot_sha256: item.snapshot_sha256, channel, reason: reason.trim() }),
      }), item, channel);
      if (requestEpoch !== epoch.current) return;
      setTarget(null); setReason(""); setMustRefresh(true);
      setNotice(`${result.replayed ? "已回读原恢复结果。" : ""}${result.outcome === "bound"
        ? "已恢复一条接收项，待扩展及投递记录确认。"
        : `未建立接收项：${result.code === "channel_unavailable" ? "所选渠道当前不可用" : targetStates[result.code!] }。`}`);
      // An audit result is immutable; fetch current facts before further writes.
      await load(view, null, true);
    } catch (cause) {
      if (requestEpoch !== epoch.current) return;
      setError(`恢复结果未确认，请先刷新通知目标：${showError(cause)}`);
      setMustRefresh(true); setTarget(null);
    } finally {
      writing.current = false; setBusy(false);
    }
  }

  return <section className="content-section" aria-label="通知接收目标">
    <div className="content-title"><div><h2>通知接收目标</h2><p>核查原通知保留的目标人员。恢复接收项后，还需分别确认扩展、发送与送达。</p></div>
      <Button tone="secondary" disabled={loading || busy} onClick={() => { setNotice(""); void load(view); }}>刷新通知目标</Button></div>
    <label>目标范围 <select aria-label="通知目标范围" value={view} disabled={busy} onChange={(event) => {
      setView(event.target.value as View); setNotice(""); setItems([]); setLegacy([]); setNext(null);
    }}><option value="unbound">待解析目标</option><option value="all">全部目标</option><option value="legacy">历史目标未留存</option></select></label>
    {error && <div className="alert alert-error" role="alert">{error}</div>}
    {notice && <div className="form-notice" role="status">{notice}</div>}
    {view === "legacy" && <p>这些历史事件未留存完整目标人员，不能按当前账号关系推测原接收人。已有接收项数量不代表完整目标人数。</p>}
    {loading ? <Loading label="正在读取通知目标" /> : view === "legacy" ? legacy.length === 0 ? <div className="empty-state">暂无历史目标缺失记录</div> : legacy.map((item) =>
      <article className="content-section" key={item.event_id}><h3>历史目标未知</h3><p>{item.business_type} · {item.business_id}</p>
        <p className="cell-subtitle">事件编号 {item.event_id} · {displayTime(item.created_at)}</p><p>已有接收项 {item.recipient_count} 条</p></article>)
      : items.length === 0 ? <div className="empty-state">暂无符合条件的通知目标</div> : items.map((item) =>
        <article className="content-section" key={item.target_id}>
          <div className="content-title"><h3>{item.person_name}</h3><span className="status">{targetStates[item.state]}</span></div>
          <p>{item.business_type} · {item.business_id}</p><p className="cell-subtitle">人员编号 {item.person_id}</p>
          <p className="cell-subtitle">事件编号 {item.event_id} · {displayTime(item.created_at)}</p><p>已有接收项 {item.bound_count} 条</p>
          {canRecover && item.state === "ready" && (target === item.target_id ? <div className="notification-retry-form">
            <label>恢复渠道 <select aria-label="恢复渠道" value={selectedChannel} disabled={busy} onChange={(event) => setSelectedChannel(event.target.value as TargetChannel)}>
              {item.available_channels.map((channel) => <option key={channel} value={channel}>{channel === "sms" ? "短信" : "微信"}</option>)}</select></label>
            <input aria-label="接收目标恢复原因" maxLength={500} disabled={busy} value={reason} placeholder="填写本次身份核查情况" onChange={(event) => setReason(event.target.value)} />
            <div className="form-actions"><Button tone="secondary" disabled={busy} onClick={() => { setTarget(null); setReason(""); }}>取消恢复</Button>
              <Button disabled={busy || mustRefresh || !reason.trim()} onClick={() => void recover(item)}>{busy ? "正在恢复" : "确认恢复接收项"}</Button></div>
          </div> : <Button tone="secondary" disabled={busy || mustRefresh} onClick={() => {
            setTarget(item.target_id); setSelectedChannel(item.available_channels[0]); setReason(""); setNotice("");
          }}>恢复接收项</Button>)}
        </article>)}
    {!loading && next && <Button tone="secondary" disabled={busy || mustRefresh} onClick={() => void load(view, next)}>加载更多通知目标</Button>}
  </section>;
}
