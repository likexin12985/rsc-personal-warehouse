// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { apiNoReplay } from "../api";
import { validateInventoryNotificationSourceRecheck } from "../inventoryNotificationSources";
import InventoryNotificationSources from "./InventoryNotificationSources";
import FormalNotifications from "./FormalNotifications";

vi.mock("../api", async (loadOriginal) => ({
  ...await loadOriginal<typeof import("../api")>(), apiNoReplay: vi.fn(),
  mutationHeaders: vi.fn(() => ({ headers: { "Idempotency-Key": "source-recheck-test", "X-Request-ID": "source-trace-test" } })),
}));
const source = {
  outbox_id: "11111111-1111-4111-8111-111111111111",
  failure_audit_id: "22222222-2222-4222-8222-222222222222",
  latest_audit_id: "22222222-2222-4222-8222-222222222222",
  source_sha256: "a".repeat(64), status: "blocked", code: "inventory_notification_source_invalid",
  isolated_at: "2026-09-19T00:00:00Z", last_checked_at: null, event_id: null, recipient_count: null,
};
const projected = { ...source, status: "projected", code: null, recipient_count: 0,
  event_id: "33333333-3333-4333-8333-333333333333", latest_audit_id: "44444444-4444-4444-8444-444444444444",
  last_checked_at: "2026-09-20T00:00:00Z" };
const page = (item = source) => ({ schema_version: "1.0", items: [item], next_after_id: null });
const result = { schema_version: "1.0", item: projected, replayed: false };

async function submit() {
  fireEvent.click(await screen.findByRole("button", { name: "复核来源" }));
  fireEvent.change(screen.getByLabelText("来源复核原因"), { target: { value: "已核查原始记录" } });
  fireEvent.click(screen.getByRole("button", { name: "确认复核" }));
}

describe("inventory source operations", () => {
  afterEach(cleanup);
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(apiNoReplay).mockImplementation(async (_path, init) => init?.method === "POST" ? result : page());
  });

  it("sends exact source version and displays zero channel entries without a delivery claim", async () => {
    render(<InventoryNotificationSources canRecheck />);
    fireEvent.click(await screen.findByRole("button", { name: "复核来源" }));
    expect((screen.getByRole("button", { name: "确认复核" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("来源复核原因"), { target: { value: "已核查原始记录" } });
    fireEvent.click(screen.getByRole("button", { name: "确认复核" }));
    await waitFor(() => expect(apiNoReplay).toHaveBeenCalledWith(`/v1/notifications/inventory-sources/${source.outbox_id}/recheck`, {
      method: "POST", headers: { "Idempotency-Key": "source-recheck-test", "X-Request-ID": "source-trace-test" },
      body: JSON.stringify({ expected_audit_id: source.latest_audit_id, expected_source_sha256: source.source_sha256, reason: "已核查原始记录" }),
    }));
    expect((await screen.findByRole("status")).textContent).toContain("渠道接收项 0 条；送达情况待投递记录确认");
    expect(screen.queryByRole("button", { name: "复核来源" })).toBeNull();
  });

  it("requires a successful refresh after an uncertain POST and never automatically replays it", async () => {
    vi.mocked(apiNoReplay).mockImplementation(async (_path, init) => {
      if (init?.method === "POST") throw new Error("network timeout");
      return page();
    });
    render(<InventoryNotificationSources canRecheck />);
    await submit();
    expect((await screen.findByRole("alert")).textContent).toContain("请先刷新来源记录");
    expect((screen.getByRole("button", { name: "复核来源" }) as HTMLButtonElement).disabled).toBe(true);
    expect(vi.mocked(apiNoReplay).mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "刷新来源记录" }));
    await waitFor(() => expect((screen.getByRole("button", { name: "复核来源" }) as HTMLButtonElement).disabled).toBe(false));
    expect(vi.mocked(apiNoReplay).mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1);
  });

  it("keeps still-blocked recheck results separate from successful notification creation", async () => {
    vi.mocked(apiNoReplay).mockImplementation(async (_path, init) => init?.method === "POST"
      ? { ...result, item: { ...projected, status: "blocked", code: "inventory_notification_source_changed", event_id: null, recipient_count: null } }
      : page());
    render(<InventoryNotificationSources canRecheck />);
    await submit();
    expect((await screen.findByRole("status")).textContent).toContain("来源内容发生变化");
    expect(screen.getByText("仍隔离")).toBeTruthy();
  });

  it("supports inspection without write permission", async () => {
    render(<InventoryNotificationSources canRecheck={false} />);
    expect(await screen.findByText("待复核来源")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "复核来源" })).toBeNull();
  });

  it("does not fetch or display source operations for an ordinary message-center user", async () => {
    vi.mocked(apiNoReplay).mockResolvedValue({ schema_version: "1.0", items: [], next_after_id: null, unread_count: 0 });
    render(<FormalNotifications />);
    expect(await screen.findByText("暂无通知")).toBeTruthy();
    expect(screen.queryByRole("region", { name: "库存通知来源异常" })).toBeNull();
    expect(vi.mocked(apiNoReplay).mock.calls.every(([path]) => !String(path).includes("inventory-sources"))).toBe(true);
  });

  it.each([
    { ...result, item: { ...projected, recipient_count: -1 } },
    { ...result, item: { ...projected, status: "delivered" } },
    { ...result, item: { ...projected, outbox_id: projected.event_id } },
    { ...result, item: { ...projected, last_checked_at: null } },
  ])("rejects inconsistent or cross-object success responses", (value) => {
    expect(() => validateInventoryNotificationSourceRecheck(value, source.outbox_id)).toThrow();
  });
});
