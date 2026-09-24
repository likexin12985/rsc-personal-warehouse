export type InventoryNotificationSource = {
  outbox_id: string;
  failure_audit_id: string;
  latest_audit_id: string;
  source_sha256: string;
  status: "blocked" | "projected";
  code: "inventory_notification_source_invalid" | "inventory_notification_event_conflict" | "inventory_notification_source_changed" | null;
  isolated_at: string;
  last_checked_at: string | null;
  event_id: string | null;
  recipient_count: number | null;
};

const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const codes = ["inventory_notification_source_invalid", "inventory_notification_event_conflict", "inventory_notification_source_changed"];
function require(condition: unknown): asserts condition {
  if (!condition) throw new Error("通知来源复核响应无效，请刷新后核对");
}
function object(value: unknown): Record<string, unknown> {
  require(value && typeof value === "object" && !Array.isArray(value));
  return value as Record<string, unknown>;
}
function id(value: unknown): string {
  require(typeof value === "string" && uuid.test(value));
  return value;
}
function time(value: unknown): string {
  require(typeof value === "string" && /(?:Z|[+-]\d\d:\d\d)$/.test(value) && Number.isFinite(Date.parse(value)));
  return value;
}

export function validateInventoryNotificationSource(value: unknown): InventoryNotificationSource {
  const row = object(value);
  require(row.status === "blocked" || row.status === "projected");
  require(typeof row.source_sha256 === "string" && /^[0-9a-f]{64}$/.test(row.source_sha256));
  if (row.status === "projected") {
    require(row.code === null && typeof row.recipient_count === "number" && Number.isSafeInteger(row.recipient_count) && row.recipient_count >= 0);
    id(row.event_id);
    time(row.last_checked_at);
  } else {
    require(typeof row.code === "string" && codes.includes(row.code) && row.event_id === null && row.recipient_count === null);
  }
  return {
    outbox_id: id(row.outbox_id), failure_audit_id: id(row.failure_audit_id), latest_audit_id: id(row.latest_audit_id),
    source_sha256: row.source_sha256, status: row.status, code: row.code as InventoryNotificationSource["code"],
    isolated_at: time(row.isolated_at), last_checked_at: row.last_checked_at === null ? null : time(row.last_checked_at),
    event_id: row.event_id === null ? null : id(row.event_id), recipient_count: row.recipient_count as number | null,
  };
}

export function validateInventoryNotificationSourcePage(value: unknown) {
  const row = object(value);
  require(row.schema_version === "1.0" && Array.isArray(row.items));
  return { items: row.items.map(validateInventoryNotificationSource), next_after_id: row.next_after_id === null ? null : id(row.next_after_id) };
}

export function validateInventoryNotificationSourceRecheck(value: unknown, expectedOutboxId: string) {
  const row = object(value);
  require(row.schema_version === "1.0" && typeof row.replayed === "boolean");
  const item = validateInventoryNotificationSource(row.item);
  require(item.outbox_id === expectedOutboxId && item.last_checked_at !== null);
  return { item, replayed: row.replayed };
}

export function inventorySourceIssue(item: InventoryNotificationSource): string {
  if (item.status === "projected") return `已生成通知，渠道接收项 ${item.recipient_count} 条；送达情况待投递记录确认`;
  if (item.code === "inventory_notification_source_changed") return "来源内容发生变化，需先核查原始记录";
  if (item.code === "inventory_notification_event_conflict") return "通知记录存在冲突，需先核对对应事实";
  return "来源证据尚未通过校验";
}
