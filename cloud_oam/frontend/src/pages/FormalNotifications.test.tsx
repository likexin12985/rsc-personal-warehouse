// @vitest-environment jsdom

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { apiNoReplay, mutationHeaders } from "../api";
import FormalNotificationsPage from "./FormalNotifications";

vi.mock("../api", async (loadOriginal) => {
  const original = await loadOriginal<typeof import("../api")>();
  return {
    ...original,
    api: vi.fn(),
    apiNoReplay: vi.fn(),
    mutationHeaders: vi.fn(() => ({
      headers: {
        "Idempotency-Key": "notification-retry-test-request",
        "X-Request-ID": "web-notification-retry-test",
      },
    })),
  };
});

const deliveryId = "11111111-1111-4111-8111-111111111111";
const deliveryPage = {
  schema_version: "1.0",
  next_after_id: null,
  items: [{
    delivery_id: deliveryId,
    event_id: "22222222-2222-4222-8222-222222222222",
    event_type: "shipment_handover_registered",
    business_type: "shipment",
    business_id: "shipment-1",
    recipient_user_id: "user-1",
    channel: "wechat",
    status: "failed",
    attempts: 1,
    provider_message_id: null,
    last_error: "provider unavailable",
    created_at: "2026-09-18T02:00:00Z",
    updated_at: "2026-09-18T02:01:00Z",
    sent_at: null,
    delivered_at: null,
    read_at: null,
    latest_attempt_no: 1,
    latest_response_code: "503",
    latest_error: "provider unavailable",
    latest_attempted_at: "2026-09-18T02:01:00Z",
    retryable: true,
  }],
};

describe("formal notification delivery operations", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(apiNoReplay).mockImplementation(async (path, init) => {
      if (String(path).endsWith(`/deliveries/${deliveryId}/retry`) && init?.method === "POST") {
        return {
          schema_version: "1.0",
          delivery_id: deliveryId,
          retry_attempt_no: 2,
          status: "queued",
          queued_at: "2026-09-18T02:02:00Z",
          replayed: false,
        };
      }
      return String(path).startsWith("/v1/notifications/deliveries")
        ? deliveryPage
        : { schema_version: "1.0", items: [], next_after_id: null, unread_count: 0 };
    });
  });

  it("shows redacted delivery evidence and queues an explicit retry", async () => {
    render(<FormalNotificationsPage canReadDeliveryRecords canRetryDelivery />);

    expect(await screen.findByText("shipment-1")).toBeTruthy();
    expect(screen.getByText("通知投递记录")).toBeTruthy();
    expect(document.body.textContent).not.toContain("recipient_key");
    expect(document.body.textContent).not.toContain("openid:");

    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    fireEvent.change(screen.getByLabelText(`重试原因-${deliveryId}`), {
      target: { value: "已确认供应商 503，可安全重试" },
    });
    fireEvent.click(screen.getByRole("button", { name: "确认重试" }));

    await waitFor(() => expect(apiNoReplay).toHaveBeenCalledWith(
      `/v1/notifications/deliveries/${deliveryId}/retry`,
      expect.objectContaining({
        method: "POST",
        headers: {
          "Idempotency-Key": "notification-retry-test-request",
          "X-Request-ID": "web-notification-retry-test",
        },
        body: JSON.stringify({
          expected_attempt_no: 1,
          reason: "已确认供应商 503，可安全重试",
        }),
      }),
    ));
    expect(mutationHeaders).toHaveBeenCalledWith("notification-retry");
    expect((await screen.findByRole("status")).textContent).toContain("重试命令已排队");
  });
});
