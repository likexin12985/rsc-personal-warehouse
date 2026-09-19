export const NOTIFICATION_STATUSES = [
  "queued", "sending", "sent", "delivered", "read", "failed", "cancelled",
] as const;
export type NotificationStatus = typeof NOTIFICATION_STATUSES[number];
export type NotificationChannel = "wechat" | "sms" | "feishu";

export type NotificationItem = {
  delivery_id: string;
  event_id: string;
  event_type: string;
  business_type: string;
  business_id: string;
  channel: NotificationChannel;
  status: NotificationStatus;
  payload: Record<string, unknown>;
  occurred_at: string;
  created_at: string;
  sent_at: string | null;
  delivered_at: string | null;
  read_at: string | null;
};

export type NotificationPage = {
  schema_version: "1.0";
  items: NotificationItem[];
  next_after_id: string | null;
  unread_count: number;
};

/**
 * Redacted operator evidence for one provider delivery.  The API deliberately
 * does not return recipient keys or provider response bodies; this contract
 * mirrors the safe fields that an administrator may inspect and retry.
 */
export type NotificationDeliveryRecord = {
  delivery_id: string;
  event_id: string;
  event_type: string;
  business_type: string;
  business_id: string;
  recipient_user_id: string | null;
  channel: NotificationChannel;
  status: NotificationStatus;
  attempts: number;
  provider_message_id: string | null;
  last_error: string | null;
  created_at: string;
  updated_at: string;
  sent_at: string | null;
  delivered_at: string | null;
  read_at: string | null;
  latest_attempt_no: number | null;
  latest_response_code: string | null;
  latest_error: string | null;
  latest_attempted_at: string | null;
  retryable: boolean;
};

export type NotificationDeliveryPage = {
  schema_version: "1.0";
  items: NotificationDeliveryRecord[];
  next_after_id: string | null;
};

export type NotificationDeliveryRetry = {
  schema_version: "1.0";
  delivery_id: string;
  retry_attempt_no: number;
  status: "queued";
  queued_at: string;
  replayed: boolean;
};

function object(value: unknown, message: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error(message);
  return value as Record<string, unknown>;
}

function stringField(row: Record<string, unknown>, key: string): string {
  const value = row[key];
  if (typeof value !== "string" || value.length === 0) throw new Error("通知响应缺少必要字段");
  return value;
}

function nullableString(row: Record<string, unknown>, key: string): string | null {
  const value = row[key];
  if (value === null) return null;
  if (typeof value !== "string" || value.length === 0) throw new Error("通知时间字段无效");
  return value;
}

function nullableNumber(row: Record<string, unknown>, key: string, minimum = 0): number | null {
  const value = row[key];
  if (value === null) return null;
  if (!Number.isSafeInteger(value) || Number(value) < minimum) throw new Error("通知数字字段无效");
  return Number(value);
}

function nonNegativeInteger(row: Record<string, unknown>, key: string): number {
  const value = row[key];
  if (!Number.isSafeInteger(value) || Number(value) < 0) throw new Error("通知尝试次数无效");
  return Number(value);
}

function nullableBoolean(row: Record<string, unknown>, key: string): boolean {
  const value = row[key];
  if (typeof value !== "boolean") throw new Error("通知布尔字段无效");
  return value;
}

function nullableText(row: Record<string, unknown>, key: string): string | null {
  const value = row[key];
  if (value === null) return null;
  if (typeof value !== "string") throw new Error("通知文本字段无效");
  return value;
}

export function validateNotificationItem(value: unknown): NotificationItem {
  const row = object(value, "通知响应契约无效");
  const channel = stringField(row, "channel");
  const status = stringField(row, "status");
  if (!["wechat", "sms", "feishu"].includes(channel)) throw new Error("通知渠道无效");
  if (!(NOTIFICATION_STATUSES as readonly string[]).includes(status)) throw new Error("通知状态无效");
  return {
    delivery_id: stringField(row, "delivery_id"),
    event_id: stringField(row, "event_id"),
    event_type: stringField(row, "event_type"),
    business_type: stringField(row, "business_type"),
    business_id: stringField(row, "business_id"),
    channel: channel as NotificationChannel,
    status: status as NotificationStatus,
    payload: object(row.payload, "通知内容无效"),
    occurred_at: stringField(row, "occurred_at"),
    created_at: stringField(row, "created_at"),
    sent_at: nullableString(row, "sent_at"),
    delivered_at: nullableString(row, "delivered_at"),
    read_at: nullableString(row, "read_at"),
  };
}

export function validateNotificationPage(value: unknown): NotificationPage {
  const row = object(value, "通知列表响应契约无效");
  if (row.schema_version !== "1.0" || !Array.isArray(row.items)) throw new Error("通知列表响应契约无效");
  if (!Number.isSafeInteger(row.unread_count) || Number(row.unread_count) < 0) throw new Error("通知未读数量无效");
  if (row.next_after_id !== null && typeof row.next_after_id !== "string") throw new Error("通知分页游标无效");
  return {
    schema_version: "1.0",
    items: row.items.map(validateNotificationItem),
    next_after_id: row.next_after_id as string | null,
    unread_count: Number(row.unread_count),
  };
}

export function validateNotificationDeliveryRecord(value: unknown): NotificationDeliveryRecord {
  const row = object(value, "通知投递响应契约无效");
  const channel = stringField(row, "channel");
  const status = stringField(row, "status");
  if (!( ["wechat", "sms", "feishu"] as readonly string[]).includes(channel)) throw new Error("通知渠道无效");
  if (!(NOTIFICATION_STATUSES as readonly string[]).includes(status)) throw new Error("通知状态无效");
  return {
    delivery_id: stringField(row, "delivery_id"),
    event_id: stringField(row, "event_id"),
    event_type: stringField(row, "event_type"),
    business_type: stringField(row, "business_type"),
    business_id: stringField(row, "business_id"),
    recipient_user_id: nullableText(row, "recipient_user_id"),
    channel: channel as NotificationChannel,
    status: status as NotificationStatus,
    attempts: nonNegativeInteger(row, "attempts"),
    provider_message_id: nullableText(row, "provider_message_id"),
    last_error: nullableText(row, "last_error"),
    created_at: stringField(row, "created_at"),
    updated_at: stringField(row, "updated_at"),
    sent_at: nullableString(row, "sent_at"),
    delivered_at: nullableString(row, "delivered_at"),
    read_at: nullableString(row, "read_at"),
    latest_attempt_no: nullableNumber(row, "latest_attempt_no", 1),
    latest_response_code: nullableText(row, "latest_response_code"),
    latest_error: nullableText(row, "latest_error"),
    latest_attempted_at: nullableString(row, "latest_attempted_at"),
    retryable: nullableBoolean(row, "retryable"),
  };
}

export function validateNotificationDeliveryPage(value: unknown): NotificationDeliveryPage {
  const row = object(value, "通知投递列表响应契约无效");
  if (row.schema_version !== "1.0" || !Array.isArray(row.items)) throw new Error("通知投递列表响应契约无效");
  if (row.next_after_id !== null && typeof row.next_after_id !== "string") throw new Error("通知投递分页游标无效");
  return {
    schema_version: "1.0",
    items: row.items.map(validateNotificationDeliveryRecord),
    next_after_id: row.next_after_id as string | null,
  };
}

export function validateNotificationDeliveryRetry(value: unknown): NotificationDeliveryRetry {
  const row = object(value, "通知重试响应契约无效");
  if (row.schema_version !== "1.0" || row.status !== "queued") throw new Error("通知重试未形成排队事实");
  const retryAttemptNo = row.retry_attempt_no;
  if (!Number.isSafeInteger(retryAttemptNo) || Number(retryAttemptNo) < 1) throw new Error("通知重试次数无效");
  if (typeof row.replayed !== "boolean") throw new Error("通知重试回放标记无效");
  return {
    schema_version: "1.0",
    delivery_id: stringField(row, "delivery_id"),
    retry_attempt_no: Number(retryAttemptNo),
    status: "queued",
    queued_at: stringField(row, "queued_at"),
    replayed: row.replayed,
  };
}

export function validateNotificationRead(value: unknown): NotificationItem {
  const row = object(value, "通知已读响应契约无效");
  if (row.schema_version !== "1.0") throw new Error("通知已读响应契约无效");
  const item = validateNotificationItem(row.item);
  if (item.status !== "read" || !item.read_at) throw new Error("通知未形成已读事实");
  return item;
}

export function notificationTitle(item: NotificationItem): string {
  const title = item.payload.title ?? item.payload.subject;
  return typeof title === "string" && title.trim() ? title : "RSC个人仓通知";
}

export function notificationBody(item: NotificationItem): string {
  const body = item.payload.body ?? item.payload.summary;
  return typeof body === "string" && body.trim() ? body : "请进入对应业务页面查看详情。";
}
