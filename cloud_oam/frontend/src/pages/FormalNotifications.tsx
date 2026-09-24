import { useEffect, useRef, useState } from "react";
import { api, apiNoReplay, jsonBody, mutationHeaders } from "../api";
import {
  type NotificationDeliveryRecord,
  type NotificationItem,
  notificationBody,
  notificationTitle,
  validateNotificationDeliveryPage,
  validateNotificationDeliveryRetry,
  validateNotificationPage,
  validateNotificationRead,
} from "../formalNotifications";
import { Button, Loading, showError } from "../ui";
import InventoryNotificationSources from "./InventoryNotificationSources";
import NotificationTargets from "./NotificationTargets";

type FormalNotificationsPageProps = {
  canReadDeliveryRecords?: boolean;
  canRetryDelivery?: boolean;
};

const DELIVERY_STATUS_OPTIONS = [
  ["", "全部状态"],
  ["queued", "排队中"],
  ["sending", "发送中"],
  ["sent", "已发送"],
  ["delivered", "已送达"],
  ["read", "已读"],
  ["failed", "失败"],
  ["cancelled", "已取消"],
] as const;

const DELIVERY_CHANNEL_OPTIONS = [
  ["", "全部渠道"],
  ["wechat", "微信"],
  ["sms", "短信"],
  ["feishu", "飞书"],
] as const;

const DELIVERY_STATUS_LABELS: Record<string, string> = Object.fromEntries(
  DELIVERY_STATUS_OPTIONS.filter(([value]) => value).map(([value, label]) => [value, label]),
);
const DELIVERY_CHANNEL_LABELS: Record<string, string> = Object.fromEntries(
  DELIVERY_CHANNEL_OPTIONS.filter(([value]) => value).map(([value, label]) => [value, label]),
);

function displayTime(value: string): string {
  const parsed = new Date(value);
  return Number.isFinite(parsed.getTime()) ? parsed.toLocaleString("zh-CN", { hour12: false }) : "时间无效";
}

function deliveryQuery(status: string, channel: string, after: string | null = null): string {
  const params = new URLSearchParams({ limit: "50" });
  if (status) params.set("status", status);
  if (channel) params.set("channel", channel);
  if (after) params.set("after_id", after);
  return `/v1/notifications/deliveries?${params.toString()}`;
}

export default function FormalNotificationsPage({
  canReadDeliveryRecords = false,
  canRetryDelivery = false,
}: FormalNotificationsPageProps) {
  const [items, setItems] = useState<NotificationItem[]>([]);
  const [next, setNext] = useState<string | null>(null);
  const [unread, setUnread] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [busyId, setBusyId] = useState("");
  const [error, setError] = useState("");
  const [deliveryItems, setDeliveryItems] = useState<NotificationDeliveryRecord[]>([]);
  const [deliveryNext, setDeliveryNext] = useState<string | null>(null);
  const [deliveryLoading, setDeliveryLoading] = useState(false);
  const [deliveryLoadingMore, setDeliveryLoadingMore] = useState(false);
  const [deliveryError, setDeliveryError] = useState("");
  const [deliveryStatus, setDeliveryStatus] = useState("");
  const [deliveryChannel, setDeliveryChannel] = useState("");
  const [retryTarget, setRetryTarget] = useState<string | null>(null);
  const [retryReason, setRetryReason] = useState("");
  const [retryBusyId, setRetryBusyId] = useState("");
  const [retryNotice, setRetryNotice] = useState("");
  const deliveryReadEpoch = useRef(0);

  async function load(after: string | null = null) {
    after ? setLoadingMore(true) : setLoading(true);
    setError("");
    try {
      const suffix = after ? `?limit=50&after_id=${encodeURIComponent(after)}` : "?limit=50";
      const page = validateNotificationPage(await apiNoReplay(`/v1/notifications${suffix}`));
      setItems((current) => after ? [...current, ...page.items] : page.items);
      setNext(page.next_after_id);
      setUnread(page.unread_count);
    } catch (cause) {
      setError(showError(cause));
      if (!after) { setItems([]); setNext(null); setUnread(0); }
    } finally {
      setLoading(false);
      setLoadingMore(false);
    }
  }

  useEffect(() => { void load(); }, []);

  async function loadDeliveries(
    after: string | null = null,
    requestedStatus = deliveryStatus,
    requestedChannel = deliveryChannel,
  ) {
    if (!canReadDeliveryRecords) return;
    const epoch = ++deliveryReadEpoch.current;
    after ? setDeliveryLoadingMore(true) : setDeliveryLoading(true);
    setDeliveryError("");
    setRetryNotice("");
    try {
      const page = validateNotificationDeliveryPage(await apiNoReplay(deliveryQuery(requestedStatus, requestedChannel, after)));
      if (epoch !== deliveryReadEpoch.current) return;
      setDeliveryItems((current) => after ? [...current, ...page.items] : page.items);
      setDeliveryNext(page.next_after_id);
    } catch (cause) {
      if (epoch !== deliveryReadEpoch.current) return;
      setDeliveryError(showError(cause));
      if (!after) { setDeliveryItems([]); setDeliveryNext(null); }
    } finally {
      if (epoch === deliveryReadEpoch.current) {
        setDeliveryLoading(false);
        setDeliveryLoadingMore(false);
      }
    }
  }

  useEffect(() => {
    if (canReadDeliveryRecords) void loadDeliveries();
    return () => { deliveryReadEpoch.current += 1; };
  }, [canReadDeliveryRecords]);

  function resetDeliveryFilters(nextStatus: string, nextChannel: string) {
    setDeliveryStatus(nextStatus);
    setDeliveryChannel(nextChannel);
    setDeliveryNext(null);
    // The selected values are passed explicitly so the request cannot race a
    // state update and accidentally load the previous filter.
    if (!canReadDeliveryRecords) return;
    void loadDeliveries(null, nextStatus, nextChannel);
  }

  async function markRead(item: NotificationItem) {
    if (!["sent", "delivered"].includes(item.status) || busyId) return;
    setBusyId(item.delivery_id);
    setError("");
    try {
      const confirmed = validateNotificationRead(await api(`/v1/notifications/${encodeURIComponent(item.delivery_id)}/read`, {
        method: "POST",
        ...mutationHeaders("notification-read"),
        ...jsonBody({}),
      }));
      setItems((current) => current.map((row) => row.delivery_id === confirmed.delivery_id ? confirmed : row));
      setUnread((current) => Math.max(0, current - 1));
    } catch (cause) {
      setError(`已读状态未确认：${showError(cause)}`);
    } finally {
      setBusyId("");
    }
  }

  async function retryDelivery(item: NotificationDeliveryRecord) {
    const reason = retryReason.trim();
    if (!reason || retryBusyId) return;
    setRetryBusyId(item.delivery_id);
    setDeliveryError("");
    setRetryNotice("");
    try {
      const result = validateNotificationDeliveryRetry(await apiNoReplay(`/v1/notifications/deliveries/${encodeURIComponent(item.delivery_id)}/retry`, {
        method: "POST",
        ...mutationHeaders("notification-retry"),
        ...jsonBody({ expected_attempt_no: item.attempts, reason }),
      }));
      setRetryTarget(null);
      setRetryReason("");
      await loadDeliveries();
      setRetryNotice(result.replayed ? "重试命令已按原结果回放。" : "重试命令已排队，等待通知 worker 处理。 ");
    } catch (cause) {
      // A failed/timeout command is never replayed in the browser.  The
      // operator must reread the record before deciding whether to try again.
      setDeliveryError(`重试结果未确认，请先刷新投递记录：${showError(cause)}`);
    } finally {
      setRetryBusyId("");
    }
  }

  return <section>
    <div className="section-header"><div><h1>消息中心</h1><p>只展示当前正式身份绑定的通知；业务状态与通知送达分别记录。</p></div><span className="status status-pending">未读 {unread}</span></div>
    {error && <div className="alert alert-error" role="alert">{error}</div>}
    {loading ? <Loading label="正在读取通知" /> : items.length === 0 ? <div className="empty-state">暂无通知</div> : <div className="notification-list">
      {items.map((item) => <article key={item.delivery_id} className={`content-section notification-card ${item.status === "read" ? "notification-read" : ""}`}>
        <div className="content-title"><div><h2>{notificationTitle(item)}</h2><p>{notificationBody(item)}</p></div><span className="status">{item.status}</span></div>
        <div className="notification-meta"><span>{item.channel}</span><span>{item.business_type} · {item.business_id}</span><span>{displayTime(item.occurred_at)}</span></div>
        {["sent", "delivered"].includes(item.status) && <Button disabled={Boolean(busyId)} onClick={() => void markRead(item)}>{busyId === item.delivery_id ? "正在确认" : "标为已读"}</Button>}
      </article>)}
      {next && <Button tone="secondary" disabled={loadingMore} onClick={() => void load(next)}>{loadingMore ? "正在加载" : "加载更多"}</Button>}
    </div>}

    {canReadDeliveryRecords && <section className="content-section notification-delivery-operations" aria-label="通知投递运维">
      <div className="content-title">
        <div><h2>通知投递记录</h2><p>仅展示脱敏后的送达证据；业务履约状态与通知状态相互独立。</p></div>
        <Button tone="secondary" disabled={deliveryLoading} onClick={() => void loadDeliveries()}>{deliveryLoading ? "正在刷新" : "刷新"}</Button>
      </div>
      <div className="filter-bar notification-delivery-filters">
        <label className="field"><span className="field-label">投递状态</span><select aria-label="投递状态筛选" value={deliveryStatus} onChange={(event) => resetDeliveryFilters(event.target.value, deliveryChannel)}>{DELIVERY_STATUS_OPTIONS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <label className="field"><span className="field-label">通知渠道</span><select aria-label="通知渠道筛选" value={deliveryChannel} onChange={(event) => resetDeliveryFilters(deliveryStatus, event.target.value)}>{DELIVERY_CHANNEL_OPTIONS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      </div>
      {deliveryError && <div className="alert alert-error" role="alert">{deliveryError}</div>}
      {retryNotice && <div className="form-notice" role="status">{retryNotice}</div>}
      {deliveryLoading ? <Loading label="正在读取投递记录" /> : deliveryItems.length === 0 ? <div className="empty-state">暂无符合条件的投递记录</div> : <div className="table-wrap notification-delivery-table-wrap">
        <table className="notification-delivery-table">
          <thead><tr><th>业务</th><th>渠道</th><th>状态</th><th>尝试</th><th>最近响应</th><th>更新时间</th><th className="actions-col">操作</th></tr></thead>
          <tbody>{deliveryItems.map((item) => <tr key={item.delivery_id}>
            <td><strong>{item.business_type}</strong><span className="cell-subtitle">{item.business_id}</span>{item.recipient_user_id && <span className="cell-subtitle">用户 {item.recipient_user_id}</span>}</td>
            <td>{DELIVERY_CHANNEL_LABELS[item.channel] || item.channel}</td>
            <td><span className={`status status-${item.status}`}>{DELIVERY_STATUS_LABELS[item.status] || item.status}</span>{item.last_error && <span className="cell-subtitle" title={item.last_error}>{item.last_error}</span>}</td>
            <td>{item.attempts}{item.latest_attempt_no ? ` · 最近第${item.latest_attempt_no}次` : ""}</td>
            <td>{item.latest_response_code || "未确认"}{item.provider_message_id && <span className="cell-subtitle">{item.provider_message_id}</span>}{item.latest_error && <span className="cell-subtitle" title={item.latest_error}>{item.latest_error}</span>}</td>
            <td>{displayTime(item.updated_at)}</td>
            <td>{canRetryDelivery && item.retryable ? (retryTarget === item.delivery_id ? <div className="notification-retry-form">
              <input aria-label={`重试原因-${item.delivery_id}`} value={retryReason} onChange={(event) => setRetryReason(event.target.value)} placeholder="填写重试原因" maxLength={500} />
              <div className="form-actions"><Button tone="secondary" disabled={retryBusyId === item.delivery_id} onClick={() => { setRetryTarget(null); setRetryReason(""); }}>取消</Button><Button disabled={!retryReason.trim() || Boolean(retryBusyId)} onClick={() => void retryDelivery(item)}>{retryBusyId === item.delivery_id ? "正在排队" : "确认重试"}</Button></div>
            </div> : <Button tone="secondary" onClick={() => { setRetryTarget(item.delivery_id); setRetryReason(""); setRetryNotice(""); }}>重试</Button>) : <span className="cell-subtitle">{item.status === "failed" ? "需明确失败证据" : "不可重试"}</span>}</td>
          </tr>)}</tbody>
        </table>
        {deliveryNext && <Button tone="secondary" disabled={deliveryLoadingMore} onClick={() => void loadDeliveries(deliveryNext)}>{deliveryLoadingMore ? "正在加载" : "加载更多"}</Button>}
      </div>}
    </section>}
      {canReadDeliveryRecords && <InventoryNotificationSources canRecheck={canRetryDelivery} />}
      {canReadDeliveryRecords && <NotificationTargets canRecover={canRetryDelivery} />}
  </section>;
}
