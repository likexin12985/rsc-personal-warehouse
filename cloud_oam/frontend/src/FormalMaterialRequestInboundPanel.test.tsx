// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import FormalMaterialRequestInboundPanel from "./FormalMaterialRequestInboundPanel";

afterEach(() => cleanup());
const ID = (n:number) => `11111111-1111-4111-8111-${String(n).padStart(12,"0")}`;
const receipt = { schema_version:"1.0", receipt_id:ID(1), receipt_no:"RCT-1", shipment_id:ID(2), status:"accepted", lines:[], exceptions:[], idempotency_replayed:false } as const;
const shipment = { schema_version:"1.0", shipment_id:ID(2), shipment_no:"SHP-1", request_id:ID(6), status:"shipped", target_location_id:ID(7), target_person_id:ID(8), carrier:"人工承运", tracking_no:"TRK-1", shipped_at:"2026-09-09T10:00:00Z", lines:[], idempotency_replayed:false } as const;
const pending = { schema_version:"1.0", inbound_order_id:ID(3), inbound_no:"INB-1", receipt_id:ID(1), target_location_id:ID(4), target_person_id:ID(5), status:"pending", posting_transaction_id:null } as const;
const detail = { request_id:ID(6), request_version:2 } as any;
function props(overrides:any = {}) { return { detail, adapter: { listReceipts:vi.fn().mockResolvedValue([receipt]), listInboundOrders:vi.fn().mockResolvedValue([pending]), createInboundOrder:vi.fn().mockResolvedValue(pending), postInboundOrder:vi.fn(), ...overrides } as any }; }

it("reloads a pending order and continues it without recreating the order", async () => {
  const p = props(); render(<FormalMaterialRequestInboundPanel {...p} />);
  fireEvent.click(await screen.findByRole("button", {name:"继续处理"}));
  expect((screen.getByRole("combobox", {name:"收货单"}) as HTMLSelectElement).value).toBe(ID(1));
  expect((screen.getByRole("textbox", {name:"目标位置 ID"}) as HTMLInputElement).value).toBe(ID(4));
  expect(p.adapter.createInboundOrder).not.toHaveBeenCalled();
});

it("blocks repost after an unknown result and only verifies by GET", async () => {
  let current = pending as any;
  const p = props({listInboundOrders:vi.fn().mockImplementation(async()=>[current]), postInboundOrder:vi.fn().mockRejectedValue(new Error("network interrupted"))});
  render(<FormalMaterialRequestInboundPanel {...p} />);
  fireEvent.click(await screen.findByRole("button", {name:"继续处理"}));
  fireEvent.click(screen.getByRole("button", {name:"确认个人仓入账"}));
  await waitFor(() => expect(screen.getByRole("button", {name:"只读核验原入账"})).toBeTruthy());
  expect(p.adapter.postInboundOrder).toHaveBeenCalledTimes(1);
  current = {...pending, status:"posted"};
  fireEvent.click(screen.getByRole("button", {name:"只读核验原入账"}));
  await waitFor(() => expect(screen.getByText(/只读回读确认/)).toBeTruthy());
  expect(p.adapter.postInboundOrder).toHaveBeenCalledTimes(1);
  expect(p.adapter.listInboundOrders).toHaveBeenCalledTimes(2);
});

it("derives the inbound destination from the bound shipment", async () => {
  const p = props({listShipments:vi.fn().mockResolvedValue([shipment])}); render(<FormalMaterialRequestInboundPanel {...p} />);
  await waitFor(() => expect(p.adapter.listShipments).toHaveBeenCalled());
  fireEvent.change(await screen.findByRole("combobox", {name:"收货单"}), {target:{value:ID(1)}});
  expect((screen.getByRole("textbox", {name:"目标位置 ID"}) as HTMLInputElement).value).toBe(ID(7));
  expect((screen.getByRole("textbox", {name:"目标人员 ID"}) as HTMLInputElement).value).toBe(ID(8));
  expect((screen.getByRole("textbox", {name:"目标位置 ID"}) as HTMLInputElement).readOnly).toBe(true);
});
