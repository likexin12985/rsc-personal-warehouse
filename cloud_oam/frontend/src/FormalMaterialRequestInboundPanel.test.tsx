// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import Panel from "./FormalMaterialRequestInboundPanel";
import { identity, access, approvedDetail } from "./materialRequestReservationTestFixtures";
import { createInboundStore } from "./materialRequestInboundRecovery";

afterEach(() => { cleanup(); localStorage.clear(); });
const ID = (n:number) => `11111111-1111-4111-8111-${String(n).padStart(12,"0")}`;
const receipt = { schema_version:"1.0", receipt_id:ID(1), receipt_no:"RCT-1", shipment_id:ID(2), status:"accepted", lines:[], exceptions:[], idempotency_replayed:false } as const;
const shipment = { schema_version:"1.0", shipment_id:ID(2), shipment_no:"SHP-1", request_id:ID(6), status:"shipped", target_location_id:ID(4), target_person_id:ID(5), carrier:"人工承运", tracking_no:"TRK-1", shipped_at:"2026-09-09T10:00:00Z", lines:[], idempotency_replayed:false } as const;
const pending = { schema_version:"1.0", inbound_order_id:ID(3), inbound_no:"INB-1", receipt_id:ID(1), target_location_id:ID(4), target_person_id:ID(5), status:"pending", posting_transaction_id:null } as const;
const posted = { ...pending, status: "posted", posting_transaction_id: ID(9) };
const detail = { ...approvedDetail(), request_id:ID(6) };
function props(overrides:any = {}): any { return { detail, access: access(), store: createInboundStore(), onBlocking: vi.fn(), onDetail: vi.fn(), adapter: {
  listReceipts:vi.fn().mockResolvedValue([receipt]), listShipments: vi.fn().mockResolvedValue([shipment]), listInboundOrders:vi.fn().mockResolvedValue([pending]),
  createInboundOrder:vi.fn().mockResolvedValue(pending), postInboundOrder:vi.fn(), loadIdentity:vi.fn().mockResolvedValue(identity()),
  loadIdentityNoReplay: vi.fn().mockResolvedValue(identity()), loadAccessNoReplay: vi.fn().mockResolvedValue(access()),
  detailNoReplay: vi.fn().mockResolvedValue(detail), ...overrides } }; }
async function selectPending() {
  const button = await screen.findByRole("button", {name:"继续处理"});
  await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false)); fireEvent.click(button);
}
const post = () => fireEvent.click(screen.getByRole("button", {name:"确认个人仓入账"}));

it("reloads a pending order and continues it without recreating the order", async () => {
  const p = props(); render(<Panel {...p} />); await selectPending();
  expect((screen.getByRole("combobox", {name:"收货单"}) as HTMLSelectElement).value).toBe(ID(1));
  expect((screen.getByRole("textbox", {name:"目标位置 ID"}) as HTMLInputElement).value).toBe(ID(4));
  expect(p.adapter.createInboundOrder).not.toHaveBeenCalled();
});

it("keeps an uncertain post blocked while pending and only clears after a posted fact", async () => {
  let current: any = pending;
  const p = props({ listInboundOrders:vi.fn().mockImplementation(async()=>[current]), postInboundOrder:vi.fn().mockRejectedValue(new Error("network interrupted")) });
  render(<Panel {...p} />); await selectPending(); post(); post();
  await screen.findByText("network interrupted");
  expect(p.adapter.postInboundOrder).toHaveBeenCalledTimes(1); expect(p.onBlocking).toHaveBeenLastCalledWith(true);
  fireEvent.click(screen.getByRole("button", {name:"只读核验原入账"}));
  await screen.findByText(/原入账单尚未过账/); expect(p.store.read().kind).toBe("valid");
  expect((screen.getByRole("button", {name:"继续处理"}) as HTMLButtonElement).disabled).toBe(true);
  current = posted;
  fireEvent.click(screen.getByRole("button", {name:"只读核验原入账"}));
  await screen.findByText("已通过只读回读确认个人仓入账");
  expect(p.store.read().kind).toBe("missing"); expect(p.onBlocking).toHaveBeenLastCalledWith(false);
  expect(p.adapter.postInboundOrder).toHaveBeenCalledTimes(1); expect(p.onDetail).toHaveBeenCalledWith(detail);
});

it("derives the inbound destination from the bound shipment", async () => {
  const p = props(); render(<Panel {...p} />); await selectPending();
  expect((screen.getByRole("textbox", {name:"目标人员 ID"}) as HTMLInputElement).value).toBe(ID(5));
  expect((screen.getByRole("textbox", {name:"目标位置 ID"}) as HTMLInputElement).readOnly).toBe(true);
});

it("confirms order creation separately without posting inventory", async () => {
  let rows: any[] = [];
  const p = props({ listInboundOrders: vi.fn(async () => rows), createInboundOrder: vi.fn(async () => { rows = [pending]; return pending; }) });
  render(<Panel {...p} />);
  await screen.findByRole("option", { name: /RCT-1/ });
  fireEvent.change(screen.getByRole("combobox", {name:"收货单"}), {target:{value:ID(1)}});
  fireEvent.click(screen.getByRole("button", {name:"创建待入账单"}));
  await screen.findByText(/已通过只读回读确认待入账单/);
  expect(p.adapter.createInboundOrder).toHaveBeenCalledTimes(1); expect(p.adapter.postInboundOrder).not.toHaveBeenCalled();
  expect(p.store.read().kind).toBe("missing"); expect(screen.getByRole("button", {name:"确认个人仓入账"})).toBeTruthy();
});

it("does not clear a post just because its POST response succeeds", async () => {
  const p = props({ postInboundOrder: vi.fn().mockResolvedValue({ schema_version: "1.0", inbound_order_id: ID(3), inventory_transaction_id: ID(9), replayed: false }) });
  render(<Panel {...p} />); await selectPending(); post();
  await screen.findByText(/原入账单尚未过账/);
  expect(p.store.read().kind).toBe("valid"); expect(p.adapter.postInboundOrder).toHaveBeenCalledTimes(1);
});

it("refuses foreign or mismatched shipment targets before showing actionable history", async () => {
  const p = props({ listShipments: vi.fn().mockResolvedValue([{ ...shipment, target_person_id: ID(20) }]) });
  render(<Panel {...p} />); await screen.findByText(/入账目标与原发运绑定不一致/);
  expect(screen.queryByRole("button", {name:"继续处理"})).toBeNull(); expect(p.adapter.postInboundOrder).not.toHaveBeenCalled();
});

it("ignores late identity after switching to another request", async () => {
  let resolve!: (v: unknown) => void;
  const p = props({ loadIdentity: vi.fn(() => new Promise(r => { resolve = r; })) });
  const view = render(<Panel {...p} />); await selectPending(); post();
  view.rerender(<Panel {...p} detail={{ ...detail, request_id: ID(90) }} />);
  await act(async () => { resolve(identity()); });
  expect(p.adapter.postInboundOrder).not.toHaveBeenCalled(); expect(p.store.read().kind).toBe("missing");
});

it("preserves the original request when acknowledgement arrives after unmount", async () => {
  let resolve!: (v: unknown) => void;
  const p = props({ postInboundOrder: vi.fn(() => new Promise(r => { resolve = r; })) });
  const view = render(<Panel {...p} />); await selectPending(); post();
  await waitFor(() => expect(p.adapter.postInboundOrder).toHaveBeenCalledTimes(1)); view.unmount();
  await act(async () => { resolve({ schema_version: "1.0", inbound_order_id: ID(3), inventory_transaction_id: ID(9), replayed: false }); });
  expect(p.store.read().kind).toBe("valid"); expect(p.onBlocking).toHaveBeenLastCalledWith(true);
  expect(p.adapter.loadIdentityNoReplay).not.toHaveBeenCalled();
});
