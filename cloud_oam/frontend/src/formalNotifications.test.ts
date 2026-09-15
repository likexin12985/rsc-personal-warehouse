import { describe, expect, it } from "vitest";
import {
  notificationBody,
  notificationTitle,
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
});
