import { useEffect, useState } from "react";
import { api, apiNoReplay, jsonBody, mutationHeaders } from "../api";
import {
  type NotificationItem,
  notificationBody,
  notificationTitle,
  validateNotificationPage,
  validateNotificationRead,
} from "../formalNotifications";
import { Button, Loading, showError } from "../ui";

function displayTime(value: string): string {
  const parsed = new Date(value);
  return Number.isFinite(parsed.getTime()) ? parsed.toLocaleString("zh-CN", { hour12: false }) : "时间无效";
}

export default function FormalNotificationsPage() {
  const [items, setItems] = useState<NotificationItem[]>([]);
  const [next, setNext] = useState<string | null>(null);
  const [unread, setUnread] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [busyId, setBusyId] = useState("");
  const [error, setError] = useState("");

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
  </section>;
}
