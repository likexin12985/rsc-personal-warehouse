// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { apiNoReplay } from "../api";
import { validateNotificationTargetPage, validateTargetRecovery } from "../notificationTargets";
import NotificationTargets from "./NotificationTargets";
import FormalNotifications from "./FormalNotifications";

vi.mock("../api", async (original) => ({ ...await original<typeof import("../api")>(), apiNoReplay: vi.fn(),
  mutationHeaders: vi.fn(() => ({ headers: { "Idempotency-Key": "target-test-command", "X-Request-ID": "target-test-trace" } })) }));
const target = {
  target_id: "11111111-1111-4111-8111-111111111111", event_id: "22222222-2222-4222-8222-222222222222",
  event_type: "inventory_transaction_changed", business_type: "inventory_transaction", business_id: "test-transaction",
  person_id: "33333333-3333-4333-8333-333333333333", person_name: "原目标人员", created_at: "2026-09-20T00:00:00Z",
  event_status: "expanded", state: "ready", bound_count: 0, available_channels: ["sms", "wechat"],
  latest_recovery_audit_id: null, snapshot_sha256: "a".repeat(64),
};
const page = (items: unknown[] = [target], next: string | null = null) => ({ schema_version: "1.0", items, next_after_id: next });
const result = { schema_version: "1.0", target_id: target.target_id, event_id: target.event_id,
  audit_id: "44444444-4444-4444-8444-444444444444", outcome: "bound", code: null, channel: "sms",
  recipient_id: "55555555-5555-4555-8555-555555555555", checked_at: "2026-09-20T00:01:00Z", replayed: false };
const bound = { ...target, state: "bound", bound_count: 1, available_channels: [] };
const deferred = () => { let resolve!: (value: unknown) => void; const promise = new Promise<unknown>((done) => { resolve = done; }); return { promise, resolve }; };
const disabled = (name: string) => (screen.getByRole("button", { name }) as HTMLButtonElement).disabled;
const posts = () => vi.mocked(apiNoReplay).mock.calls.filter(([, init]) => init?.method === "POST");
async function begin() {
  fireEvent.click(await screen.findByRole("button", { name: "恢复接收项" }));
  expect(disabled("确认恢复接收项")).toBe(true);
  fireEvent.change(screen.getByLabelText("接收目标恢复原因"), { target: { value: "已核对正式身份" } });
}
async function submit() { await begin(); fireEvent.click(screen.getByRole("button", { name: "确认恢复接收项" })); }

describe("retained notification targets", () => {
  afterEach(cleanup);
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(apiNoReplay).mockImplementation(async (_path, init) => init?.method === "POST" ? result : page());
  });
  it("recovers only the chosen verified channel and rereads current facts", async () => {
    let recovered = false;
    vi.mocked(apiNoReplay).mockImplementation(async (_path, init) => {
      if (init?.method === "POST") { recovered = true; return { ...result, channel: "wechat" }; }
      return page([recovered ? bound : target]);
    });
    render(<NotificationTargets canRecover />); await begin();
    fireEvent.change(screen.getByLabelText("恢复渠道"), { target: { value: "wechat" } });
    fireEvent.click(screen.getByRole("button", { name: "确认恢复接收项" }));
    expect((await screen.findByRole("status")).textContent).toBe("已恢复一条接收项，待扩展及投递记录确认。");
    expect(await screen.findByText("已有接收项")).toBeTruthy();
    expect(posts()).toEqual([[`/v1/notifications/person-targets/${target.target_id}/recover`, {
      method: "POST", headers: { "Idempotency-Key": "target-test-command", "X-Request-ID": "target-test-trace" },
      body: JSON.stringify({ expected_snapshot_sha256: target.snapshot_sha256, channel: "wechat", reason: "已核对正式身份" }),
    }]]);
    expect(screen.queryByRole("button", { name: "恢复接收项" })).toBeNull();
  });
  it("latches uncertain results through failed refresh and never replays POST", async () => {
    render(<NotificationTargets canRecover />); await screen.findByText("原目标人员");
    vi.mocked(apiNoReplay).mockRejectedValue(new Error("network timeout")); await submit();
    expect((await screen.findByRole("alert")).textContent).toContain("请先刷新通知目标");
    expect(disabled("恢复接收项")).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "刷新通知目标" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "恢复接收项" })).toBeNull());
    expect(posts()).toHaveLength(1);
    vi.mocked(apiNoReplay).mockResolvedValue(page());
    fireEvent.click(screen.getByRole("button", { name: "刷新通知目标" }));
    await waitFor(() => expect(disabled("恢复接收项")).toBe(false));
    expect(posts()).toHaveLength(1);
  });
  it("requires fresh facts even after a confirmed POST if its follow-up GET fails", async () => {
    let recovered = false;
    vi.mocked(apiNoReplay).mockImplementation(async (_path, init) => {
      if (init?.method === "POST") { recovered = true; return result; }
      if (recovered) throw new Error("refresh unavailable");
      return page();
    });
    render(<NotificationTargets canRecover />); await submit();
    expect((await screen.findByRole("status")).textContent).toContain("已恢复一条接收项");
    expect((await screen.findByRole("alert")).textContent).toContain("refresh unavailable");
    expect(screen.queryByRole("button", { name: "恢复接收项" })).toBeNull(); expect(posts()).toHaveLength(1);
  });
  it("treats a blocked audit as a blocked recovery", async () => {
    vi.mocked(apiNoReplay).mockImplementation(async (_path, init) => init?.method === "POST"
      ? { ...result, outcome: "blocked", code: "channel_unavailable", recipient_id: null } : page());
    render(<NotificationTargets canRecover />); await submit();
    expect((await screen.findByRole("status")).textContent).toContain("未建立接收项：所选渠道当前不可用");
  });
  it("ignores old mutation results after permission changes and requires a later refresh", async () => {
    const pending = deferred();
    vi.mocked(apiNoReplay).mockImplementation(async (_path, init) => init?.method === "POST" ? pending.promise : page());
    const rendered = render(<NotificationTargets canRecover />); await submit();
    rendered.rerender(<NotificationTargets canRecover={false} />);
    await screen.findByText("原目标人员");
    rendered.rerender(<NotificationTargets canRecover />);
    await screen.findByText("原目标人员");
    await act(async () => { pending.resolve(result); });
    expect(screen.queryByRole("status")).toBeNull(); expect(disabled("恢复接收项")).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "刷新通知目标" }));
    await waitFor(() => expect(disabled("恢复接收项")).toBe(false)); expect(posts()).toHaveLength(1);
  });
  it("suppresses a duplicate click while a request is in flight", async () => {
    const pending = deferred();
    vi.mocked(apiNoReplay).mockImplementation(async (_path, init) => init?.method === "POST" ? pending.promise : page());
    render(<NotificationTargets canRecover />); await submit();
    fireEvent.click(screen.getByRole("button", { name: "正在恢复" })); expect(posts()).toHaveLength(1);
    await act(async () => { pending.resolve(result); });
  });
  it("supports read-only inspection and keeps unknown legacy audiences separate", async () => {
    vi.mocked(apiNoReplay).mockImplementation(async (url) => String(url).includes("legacy-events")
      ? page([{ event_id: target.event_id, event_type: "legacy", business_type: "legacy", business_id: "old-event", created_at: target.created_at, recipient_count: 2 }]) : page());
    render(<NotificationTargets canRecover={false} />); await screen.findByText("原目标人员");
    expect(screen.queryByRole("button", { name: "恢复接收项" })).toBeNull();
    fireEvent.change(screen.getByLabelText("通知目标范围"), { target: { value: "legacy" } });
    expect(await screen.findByText("历史目标未知")).toBeTruthy();
    expect(screen.getByText("已有接收项 2 条")).toBeTruthy(); expect(screen.queryByText("原目标人员")).toBeNull();
    expect(posts()).toHaveLength(0);
  });
  it("does not load target operations for ordinary message-center users", async () => {
    vi.mocked(apiNoReplay).mockResolvedValue({ ...page([]), unread_count: 0 });
    render(<FormalNotifications />); await screen.findByText("暂无通知");
    expect(screen.queryByRole("region", { name: "通知接收目标" })).toBeNull();
    expect(vi.mocked(apiNoReplay).mock.calls.every(([url]) => !String(url).includes("person-targets"))).toBe(true);
  });
  it("ignores a late previous-view GET and preserves keyset pages", async () => {
    const initial = deferred();
    const older = { ...target, target_id: result.audit_id, person_name: "较早目标人员" };
    vi.mocked(apiNoReplay).mockImplementation(async (url) => String(url).includes("unbound_only=true") ? initial.promise
      : String(url).includes("after_id=") ? page([older]) : page([target], target.target_id));
    render(<NotificationTargets canRecover />);
    fireEvent.change(screen.getByLabelText("通知目标范围"), { target: { value: "all" } });
    await screen.findByText("原目标人员");
    fireEvent.click(screen.getByRole("button", { name: "加载更多通知目标" })); await screen.findByText("较早目标人员");
    await act(async () => { initial.resolve(page([])); });
    expect(screen.getByText("原目标人员")).toBeTruthy(); expect(screen.getByText("较早目标人员")).toBeTruthy();
  });
  it("fails closed on a cross-object POST result", async () => {
    vi.mocked(apiNoReplay).mockImplementation(async (_path, init) => init?.method === "POST" ? { ...result, event_id: result.audit_id } : page());
    render(<NotificationTargets canRecover />); await submit();
    expect((await screen.findByRole("alert")).textContent).toContain("恢复结果未确认");
    expect(disabled("恢复接收项")).toBe(true);
  });
  it.each([
    { ...target, bound_count: -1 }, { ...target, state: "ready", available_channels: [] },
    { ...target, state: "bound", bound_count: 1 }, { ...target, available_channels: ["sms", "sms"] },
    { ...target, event_status: "cancelled" }, { ...target, snapshot_sha256: "bad" },
  ])("rejects inconsistent target observations", (item) => {
    expect(() => validateNotificationTargetPage(page([item]))).toThrow();
  });
  it.each([
    { ...result, outcome: "delivered" }, { ...result, code: "bound" }, { ...result, recipient_id: null },
    { ...result, channel: "wechat" }, { ...result, checked_at: "2026-09-20T00:01:00" },
  ])("rejects unproven recovery results", (value) => {
    const valid = validateNotificationTargetPage(page()).items[0];
    expect(() => validateTargetRecovery(value, valid, "sms")).toThrow();
  });
});
