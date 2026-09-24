import { describe, expect, it } from "vitest";
import {
  notificationBody,
  notificationTitle,
  validateNotificationDeliveryPage,
  validateNotificationDeliveryRetry,
  validateNotificationPage,
  validateNotificationRead,
} from "./formalNotifications";

const item = {
  delivery_id: "delivery-1",
  event_id: "event-1",
  event_type: "shipment_handover_registered",
  business_type: "shipment",
  business_id: "shipment-1",
  channel: "wechat",
  status: "sent",
  payload: { title: "包裹已发运", body: "顺丰测试单" },
  occurred_at: "2026-09-15T02:00:00Z",
  created_at: "2026-09-15T02:00:00Z",
  sent_at: "2026-09-15T02:00:00Z",
  delivered_at: null,
  read_at: null,
};

describe("formal notification contract", () => {
  it("describes an inventory fact without presenting queueing as receipt or inbound", () => {
    const page = validateNotificationPage({ schema_version: "1.0", next_after_id: null, unread_count: 1,
      items: [{ ...item, event_type: "inventory_transaction_changed", status: "queued", sent_at: null, payload: {} }] });
    expect(notificationTitle(page.items[0])).toBe("库存变动已记录");
    expect(notificationBody(page.items[0])).toContain("库存流水已过账");
    expect(page.items[0].status).toBe("queued");
  });
  it("keeps the independent delivery state and safe display text", () => {
    const page = validateNotificationPage({
      schema_version: "1.0",
      items: [item],
      next_after_id: null,
      unread_count: 1,
    });
    expect(page.items[0].status).toBe("sent");
    expect(notificationTitle(page.items[0])).toBe("包裹已发运");
    expect(notificationBody(page.items[0])).toBe("顺丰测试单");
  });

  it("requires an exact read fact before accepting a mutation response", () => {
    expect(() => validateNotificationRead({ schema_version: "1.0", item })).toThrow("未形成已读事实");
    expect(validateNotificationRead({
      schema_version: "1.0",
      item: { ...item, status: "read", read_at: "2026-09-15T02:01:00Z" },
    }).status).toBe("read");
  });

  it("rejects an unknown status", () => {
    expect(() => validateNotificationPage({
      schema_version: "1.0",
      items: [{ ...item, status: "accepted" }],
      next_after_id: null,
      unread_count: 0,
    })).toThrow("通知状态无效");
  });

  it("accepts only the redacted operator delivery evidence needed for retry", () => {
    const page = validateNotificationDeliveryPage({
      schema_version: "1.0",
      next_after_id: null,
      items: [{
        delivery_id: "delivery-1",
        event_id: "event-1",
        event_type: "shipment_handover_registered",
        business_type: "shipment",
        business_id: "shipment-1",
        recipient_user_id: "user-1",
        channel: "wechat",
        status: "failed",
        attempts: 2,
        provider_message_id: null,
        last_error: "provider unavailable",
        created_at: "2026-09-15T02:00:00Z",
        updated_at: "2026-09-15T02:01:00Z",
        sent_at: null,
        delivered_at: null,
        read_at: null,
        latest_attempt_no: 2,
        latest_response_code: "503",
        latest_error: "provider unavailable",
        latest_attempted_at: "2026-09-15T02:01:00Z",
        retryable: true,
      }],
    });
    expect(page.items[0].retryable).toBe(true);
    expect(page.items[0].latest_response_code).toBe("503");
  });

  it("requires a queued fact before accepting a retry response", () => {
    expect(() => validateNotificationDeliveryRetry({
      schema_version: "1.0",
      delivery_id: "delivery-1",
      retry_attempt_no: 3,
      status: "failed",
      queued_at: "2026-09-15T02:02:00Z",
      replayed: false,
    })).toThrow("未形成排队事实");
    expect(validateNotificationDeliveryRetry({
      schema_version: "1.0",
      delivery_id: "delivery-1",
      retry_attempt_no: 3,
      status: "queued",
      queued_at: "2026-09-15T02:02:00Z",
      replayed: true,
    }).replayed).toBe(true);
  });
});
