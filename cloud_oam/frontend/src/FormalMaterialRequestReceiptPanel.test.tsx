// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import Panel from "./FormalMaterialRequestReceiptPanel";
import { approvedDetail, access, identity, REQUEST_ID } from "./materialRequestReservationTestFixtures";
import { createReceiptStore, receiptRequestHash } from "./materialRequestReceiptRecovery";
import type { ReceiptInput } from "./materialRequestShipment";

beforeEach(async () => { const { webcrypto } = await vi.importActual<{ webcrypto: Crypto }>("node:crypto"); vi.stubGlobal("crypto", webcrypto); });
afterEach(() => { cleanup(); localStorage.clear(); vi.unstubAllGlobals(); });
const id = (n: number) => `11111111-1111-4111-8111-${String(n).padStart(12, "0")}`;
function props(): any {
  const shipment = { schema_version: "1.0", shipment_id: id(1), shipment_no: "SHP-001", request_id: REQUEST_ID,
    status: "shipped", target_location_id: id(2), target_person_id: id(3), carrier: "承运商", tracking_no: "TRACK-1",
    shipped_at: "2026-09-01T10:00:00Z", lines: [{ shipment_line_id: id(4), outbound_posting_id: id(5), shipped_qty: "2.000", serial_ids: [] }], idempotency_replayed: false };
  const store = createReceiptStore(); let submitted: ReceiptInput | null = null;
  const result = { schema_version: "1.0", receipt_id: id(6), receipt_no: "RCT-001", shipment_id: id(1), status: "accepted", lines: [], exceptions: [], idempotency_replayed: false };
  return { shipment, result, store, detail: approvedDetail(), access: access(), onDetail: vi.fn(), adapter: {
    listShipments: vi.fn(async () => [shipment]), listReceipts: vi.fn(async () => []),
    loadIdentity: vi.fn(async () => identity()), loadIdentityNoReplay: vi.fn(async () => identity()),
    loadAccessNoReplay: vi.fn(async () => access()), detailNoReplay: vi.fn(async () => approvedDetail(4)),
    createReceipt: vi.fn(async (_request: string, input: ReceiptInput) => { expect(store.read().kind).toBe("valid"); submitted = input; return result; }),
    receiptCommandStatusNoReplay: vi.fn(async () => ({ schema_version: "1.0", lookup_status: "confirmed",
      request_hash: await receiptRequestHash(REQUEST_ID, submitted!), command: { ...result, idempotency_replayed: true } })),
  } };
}
async function fill() {
  await screen.findByRole("option", { name: /SHP-001/ });
  await waitFor(() => expect((screen.getByRole("combobox", { name: "发运明细" }) as HTMLSelectElement).disabled).toBe(false));
  fireEvent.change(screen.getByRole("combobox", { name: "发运明细" }), { target: { value: id(4) } });
  fireEvent.change(screen.getByRole("textbox", { name: "合格数量" }), { target: { value: "1.000" } });
}
const submit = () => fireEvent.click(screen.getByRole("button", { name: "登记收货验收" }));

it("derives the recipient from the selected shipment and confirms by exact readback", async () => {
  const p = props(); render(<Panel {...p} />); await fill();
  const recipient = screen.getByRole("textbox", { name: "发运指定收货人" }) as HTMLInputElement;
  expect(recipient.value).toBe(id(3)); expect(recipient.readOnly).toBe(true);
  fireEvent.change(screen.getByRole("textbox", { name: "合格数量" }), { target: { value: "1" } });
  fireEvent.change(screen.getByRole("textbox", { name: "拒收数量" }), { target: { value: "0" } });
  submit(); submit();
  await waitFor(() => expect(p.onDetail).toHaveBeenCalledWith(approvedDetail(4)));
  expect(p.adapter.createReceipt).toHaveBeenCalledTimes(1);
  expect(p.adapter.createReceipt.mock.calls[0][1]).toMatchObject({ receiver_person_id: id(3), lines: [{ shipment_line_id: id(4), condition: "normal", accepted_qty: "1.000", rejected_qty: "0.000" }] });
  expect(p.adapter.receiptCommandStatusNoReplay).toHaveBeenCalledTimes(1);
  expect(p.store.read().kind).toBe("missing"); expect(screen.getByText("已验收")).toBeTruthy();
});

it("keeps an acknowledged write blocked until its exact recovery is observable", async () => {
  const p = props(); p.adapter.receiptCommandStatusNoReplay.mockResolvedValueOnce({ schema_version: "1.0", lookup_status: "not_observed", request_hash: null, command: null });
  render(<Panel {...p} />); await fill(); submit();
  await screen.findByRole("alert"); expect(p.store.read().kind).toBe("valid"); submit();
  expect(p.adapter.createReceipt).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("button", { name: "只读核验原收货" }));
  await waitFor(() => expect(p.store.read().kind).toBe("missing"));
  expect(p.adapter.createReceipt).toHaveBeenCalledTimes(1);
});

it("rejects a changed recipient before persisting or sending a write", async () => {
  const p = props(); render(<Panel {...p} />); await fill();
  p.adapter.listShipments.mockResolvedValueOnce([{ ...p.shipment, target_person_id: id(20) }]); submit();
  await screen.findByText(/包裹收货绑定已变化/);
  expect(p.adapter.createReceipt).not.toHaveBeenCalled(); expect(p.store.read().kind).toBe("missing");
});

it("does not expose a shipment from another request", async () => {
  const p = props(); p.adapter.listShipments.mockResolvedValue([{ ...p.shipment, request_id: id(90) }]);
  render(<Panel {...p} />); await screen.findByRole("alert");
  expect(screen.queryByRole("option", { name: /SHP-001/ })).toBeNull();
  expect(p.adapter.createReceipt).not.toHaveBeenCalled();
});

it("drops a late identity response when the open request changes", async () => {
  const p = props(); let resolve!: (value: unknown) => void;
  p.adapter.loadIdentity.mockImplementationOnce(() => new Promise(r => { resolve = r; }));
  const view = render(<Panel {...p} />); await fill(); submit();
  await waitFor(() => expect(p.adapter.loadIdentity).toHaveBeenCalled());
  view.rerender(<Panel {...p} detail={{ ...p.detail, request_id: id(90) }} />);
  await act(async () => { resolve(identity()); });
  expect(p.adapter.createReceipt).not.toHaveBeenCalled(); expect(p.store.read().kind).toBe("missing");
});

it("preserves a sent request if its acknowledgement arrives after unmount", async () => {
  const p = props(); let resolve!: (value: unknown) => void;
  p.adapter.createReceipt.mockImplementationOnce(() => new Promise(r => { resolve = r; }));
  const view = render(<Panel {...p} />); await fill(); submit();
  await waitFor(() => expect(p.adapter.createReceipt).toHaveBeenCalledTimes(1));
  view.unmount(); await act(async () => { resolve(p.result); });
  expect(p.store.read().kind).toBe("valid"); expect(p.adapter.receiptCommandStatusNoReplay).not.toHaveBeenCalled();
});

it("blocks a corrupt saved command rather than overwriting it", async () => {
  const p = props(); p.store = createReceiptStore({ getItem: () => "broken", setItem: vi.fn(), removeItem: vi.fn() });
  render(<Panel {...p} />); await screen.findByRole("alert"); submit();
  expect(p.adapter.createReceipt).not.toHaveBeenCalled(); expect(p.store.read().kind).toBe("corrupt");
});

it("reports the lock before identity lookup and retains it for an uncertain result", async () => {
  const p = props(); p.onBlocking = vi.fn();
  p.adapter.loadIdentity.mockImplementationOnce(async () => { expect(p.onBlocking).toHaveBeenLastCalledWith(true); return identity(); });
  p.adapter.createReceipt.mockRejectedValueOnce(new Error("网络中断"));
  const view = render(<Panel {...p} />); await fill(); submit();
  await screen.findByText("网络中断"); expect(p.onBlocking).toHaveBeenLastCalledWith(true);
  view.unmount(); expect(p.onBlocking).toHaveBeenLastCalledWith(true);
});

it("abandons an unsent receipt if another operation becomes pending during preflight", async () => {
  const p = props(); let pending = false; p.otherWriteBlocked = () => pending; p.onBlocking = vi.fn();
  let resolve!: (value: unknown) => void;
  p.adapter.loadIdentity.mockImplementationOnce(() => new Promise(r => { resolve = r; }));
  render(<Panel {...p} />); await fill(); submit();
  pending = true; await act(async () => { resolve(identity()); });
  await screen.findByText(/其他操作尚未完成/);
  expect(p.adapter.createReceipt).not.toHaveBeenCalled(); expect(p.store.read().kind).toBe("missing");
  expect(p.onBlocking).toHaveBeenLastCalledWith(false);
});

it("discards an unsent receipt when read permission changes without a version change", async () => {
  const p = props(); let resolve!: (value: unknown) => void;
  p.adapter.loadIdentity.mockImplementationOnce(() => new Promise(r => { resolve = r; }));
  const view = render(<Panel {...p} />); await fill(); submit();
  view.rerender(<Panel {...p} access={{ ...p.access, can_read: false }} />);
  await act(async () => { resolve(identity()); });
  expect(p.adapter.createReceipt).not.toHaveBeenCalled(); expect(p.store.read().kind).toBe("missing");
});

function received(p: any, accepted: string, rejected = "0.000", serials: string[] = []) {
  return { ...p.result, lines: [{ receipt_line_id: id(41), shipment_line_id: id(4), accepted_qty: accepted, rejected_qty: rejected, serial_ids: serials }] };
}

it("keeps a fully received line visible but prevents another receipt", async () => {
  const p = props(); p.shipment.lines[0].shipped_qty = "1.000"; p.adapter.listReceipts.mockResolvedValue([received(p, "1.000")]);
  render(<Panel {...p} />);
  const option = await screen.findByRole("option", { name: /SHP-001.*可收 0.000/ }) as HTMLOptionElement;
  expect(option.disabled).toBe(true);
  expect((screen.getByRole("button", { name: "登记收货验收" }) as HTMLButtonElement).disabled).toBe(true);
  expect(p.adapter.createReceipt).not.toHaveBeenCalled();
});

it("subtracts accepted and rejected partial quantities before allowing the next receipt", async () => {
  const p = props(); p.shipment.lines[0].shipped_qty = "1.000"; p.adapter.listReceipts.mockResolvedValue([received(p, "0.200", "0.300")]);
  render(<Panel {...p} />); await fill();
  expect(screen.getByRole("option", { name: /SHP-001.*可收 0.500/ })).toBeTruthy();
  submit(); await screen.findByText(/不能超过当前剩余可收数量/);
  expect(p.adapter.createReceipt).not.toHaveBeenCalled(); expect(p.store.read().kind).toBe("missing");
  fireEvent.change(screen.getByRole("textbox", { name: "合格数量" }), { target: { value: "0.5" } }); submit();
  await waitFor(() => expect(p.adapter.createReceipt).toHaveBeenCalledTimes(1));
  expect(p.adapter.createReceipt.mock.calls[0][1].lines[0].accepted_qty).toBe("0.500");
});

it("rereads concurrent receipts before persisting the write coordinates", async () => {
  const p = props(); p.shipment.lines[0].shipped_qty = "1.000"; render(<Panel {...p} />); await fill();
  p.adapter.listReceipts.mockResolvedValueOnce([received(p, "1.000")]); submit();
  await screen.findByText(/不能超过当前剩余可收数量/);
  expect(p.adapter.createReceipt).not.toHaveBeenCalled(); expect(p.store.read().kind).toBe("missing");
  expect((screen.getByRole("option", { name: /SHP-001.*可收 0.000/ }) as HTMLOptionElement).disabled).toBe(true);
});

it("blocks malformed receipt line bindings instead of assuming no prior receipts", async () => {
  const p = props(); const old = received(p, "1.000"); old.lines[0].shipment_line_id = id(99);
  p.adapter.listReceipts.mockResolvedValue([old]); render(<Panel {...p} />);
  await screen.findByText(/收货历史明细绑定无效/);
  expect(screen.queryByRole("option", { name: /SHP-001/ })).toBeNull();
  expect(p.adapter.createReceipt).not.toHaveBeenCalled(); expect(p.store.read().kind).toBe("missing");
});

it("prevents already accepted SN from entering a new request", async () => {
  const p = props(); p.shipment.lines[0].shipped_qty = "2.000"; p.shipment.lines[0].serial_ids = [id(70), id(71)];
  p.adapter.listReceipts.mockResolvedValue([received(p, "1.000", "0.000", [id(70)])]);
  render(<Panel {...p} />); await fill();
  fireEvent.change(screen.getByRole("textbox", { name: "SN（逗号分隔，可选）" }), { target: { value: id(70) } }); submit();
  await screen.findByText(/SN 须属于本包尚未验收/);
  expect(p.adapter.createReceipt).not.toHaveBeenCalled(); expect(p.store.read().kind).toBe("missing");
  fireEvent.change(screen.getByRole("textbox", { name: "SN（逗号分隔，可选）" }), { target: { value: id(71) } }); submit();
  await waitFor(() => expect(p.adapter.createReceipt).toHaveBeenCalledTimes(1));
  expect(p.adapter.createReceipt.mock.calls[0][1].lines[0].serial_ids).toEqual([id(71)]);
});

it("asks for exception evidence before persisting a rejected receipt", async () => {
  const p = props(); render(<Panel {...p} />); await fill();
  fireEvent.change(screen.getByRole("textbox", { name: "合格数量" }), { target: { value: "0" } });
  fireEvent.change(screen.getByRole("textbox", { name: "拒收数量" }), { target: { value: "1" } });
  fireEvent.change(screen.getByRole("combobox", { name: "验收条件" }), { target: { value: "damaged" } }); submit();
  await screen.findByText(/异常验收需要提供证据文件/);
  expect(p.adapter.createReceipt).not.toHaveBeenCalled(); expect(p.store.read().kind).toBe("missing");
});


it("does not treat an empty recorded receipt as zero accepted quantity", async () => {
  const p = props(); p.adapter.listReceipts.mockResolvedValue([p.result]); render(<Panel {...p} />);
  await screen.findByText(/收货历史与发运记录不一致/);
  expect(screen.queryByRole("option", { name: /SHP-001/ })).toBeNull();
  expect(p.adapter.createReceipt).not.toHaveBeenCalled(); expect(p.store.read().kind).toBe("missing");
});
