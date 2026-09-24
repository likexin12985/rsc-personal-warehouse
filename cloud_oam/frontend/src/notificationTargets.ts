export type TargetChannel = "sms" | "wechat";
export const targetStates = {
  evidence_invalid: "目标证据需核查", cancelled: "通知已取消", bound: "已有接收项",
  account_inactive: "账号或人员已停用", needs_account: "待建立人员账号",
  configuration_unavailable: "身份核验配置未就绪", ready: "可恢复接收项", needs_verified_channel: "待核实渠道身份",
} as const;
export type NotificationTarget = {
  target_id: string; event_id: string; event_type: string; business_type: string; business_id: string;
  person_id: string; person_name: string; created_at: string; event_status: "pending" | "expanded" | "cancelled";
  state: keyof typeof targetStates; bound_count: number; available_channels: TargetChannel[];
  latest_recovery_audit_id: string | null; snapshot_sha256: string;
};
export type LegacyNotificationEvent = {
  event_id: string; event_type: string; business_type: string; business_id: string; created_at: string; recipient_count: number;
};
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
function require(condition: unknown): asserts condition {
  if (!condition) throw new Error("通知目标响应无效，请刷新后核对");
}
function object(value: unknown): Record<string, unknown> {
  require(value && typeof value === "object" && !Array.isArray(value));
  return value as Record<string, unknown>;
}
function id(value: unknown): string { require(typeof value === "string" && uuid.test(value)); return value; }
function text(value: unknown): string { require(typeof value === "string" && value.length > 0); return value; }
function count(value: unknown): number { require(typeof value === "number" && Number.isSafeInteger(value) && value >= 0); return value; }
function time(value: unknown): string {
  require(typeof value === "string" && /(?:Z|[+-]\d\d:\d\d)$/.test(value) && Number.isFinite(Date.parse(value)));
  return value;
}
function channel(value: unknown): TargetChannel { require(value === "sms" || value === "wechat"); return value; }
function target(value: unknown): NotificationTarget {
  const row = object(value);
  require(typeof row.state === "string" && Object.hasOwn(targetStates, row.state));
  require(row.event_status === "pending" || row.event_status === "expanded" || row.event_status === "cancelled");
  require(typeof row.snapshot_sha256 === "string" && /^[0-9a-f]{64}$/.test(row.snapshot_sha256));
  require(Array.isArray(row.available_channels));
  const channels = row.available_channels.map(channel);
  const boundCount = count(row.bound_count);
  require(new Set(channels).size === channels.length);
  require(row.state === "ready" ? channels.length > 0 && boundCount === 0 : channels.length === 0);
  require(row.state !== "bound" || boundCount > 0);
  require(row.state !== "cancelled" || row.event_status === "cancelled");
  require(row.event_status !== "cancelled" || row.state === "cancelled" || row.state === "evidence_invalid");
  return { target_id: id(row.target_id), event_id: id(row.event_id), event_type: text(row.event_type),
    business_type: text(row.business_type), business_id: text(row.business_id), person_id: id(row.person_id),
    person_name: text(row.person_name), created_at: time(row.created_at), event_status: row.event_status,
    state: row.state as NotificationTarget["state"], bound_count: boundCount, available_channels: channels,
    latest_recovery_audit_id: row.latest_recovery_audit_id === null ? null : id(row.latest_recovery_audit_id), snapshot_sha256: row.snapshot_sha256 };
}
function page<T>(value: unknown, parse: (value: unknown) => T, key: (item: T) => string) {
  const row = object(value);
  require(row.schema_version === "1.0" && Array.isArray(row.items) && row.items.length <= 100);
  const items = row.items.map(parse);
  require(new Set(items.map(key)).size === items.length);
  const next = row.next_after_id === null ? null : id(row.next_after_id);
  require(next === null || items.length > 0 && key(items[items.length - 1]) === next);
  return { items, next_after_id: next };
}
export const validateNotificationTargetPage = (value: unknown) => page(value, target, (row) => row.target_id);
export const validateLegacyNotificationEventPage = (value: unknown) => page(value, (value): LegacyNotificationEvent => {
  const row = object(value);
  return { event_id: id(row.event_id), event_type: text(row.event_type), business_type: text(row.business_type),
    business_id: text(row.business_id), created_at: time(row.created_at), recipient_count: count(row.recipient_count) };
}, (row) => row.event_id);
export function validateTargetRecovery(value: unknown, expected: NotificationTarget, expectedChannel: TargetChannel) {
  const row = object(value);
  require(row.schema_version === "1.0" && typeof row.replayed === "boolean");
  require(id(row.target_id) === expected.target_id && id(row.event_id) === expected.event_id && channel(row.channel) === expectedChannel);
  id(row.audit_id); time(row.checked_at);
  require(row.outcome === "bound" || row.outcome === "blocked");
  if (row.outcome === "bound") { require(row.code === null); id(row.recipient_id); }
  else require(row.recipient_id === null && typeof row.code === "string" && row.code !== "ready"
    && (Object.hasOwn(targetStates, row.code) || row.code === "channel_unavailable"));
  return { outcome: row.outcome, replayed: row.replayed, code: row.code as Exclude<NotificationTarget["state"], "ready"> | "channel_unavailable" | null };
}
