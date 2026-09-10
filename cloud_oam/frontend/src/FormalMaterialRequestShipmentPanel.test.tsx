// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import FormalMaterialRequestShipmentPanel from "./FormalMaterialRequestShipmentPanel";
import { createShipmentStore, shipmentRequestHash } from "./materialRequestShipmentRecovery";
import { shipmentResult, shipmentPage, ID } from "./materialRequestShipmentTestFixtures";
import { afterOutbound } from "./materialRequestOutboundTestFixtures";
import { access, identity } from "./materialRequestReservationTestFixtures";
import { type ShipmentInput, shipmentUnits, shipmentQuantity } from "./materialRequestShipment";
beforeEach(async () => { const { webcrypto } = await vi.importActual<{ webcrypto: Crypto }>("node:crypto"); vi.stubGlobal("crypto", webcrypto); });
afterEach(() => { cleanup(); localStorage.clear(); vi.unstubAllGlobals(); });
function props(serial = false): any {
  const initial = shipmentPage(serial); let current = initial, command: any = null, submitted: ShipmentInput | null = null;
  const store = createShipmentStore();
  const adapter = { listShipmentOptions: vi.fn(async () => current), listShipments: vi.fn(async () => command ? [command] : []),
    loadIdentityNoReplay: vi.fn().mockResolvedValue(identity()), loadAccessNoReplay: vi.fn().mockResolvedValue(access()), detailNoReplay: vi.fn().mockResolvedValue(afterOutbound()),
    createShipment: vi.fn(async (_id: string, input: ShipmentInput) => {
      expect(store.read().kind).toBe("valid"); submitted = input; command = shipmentResult(input);
      const row = initial.items[0], remaining = shipmentUnits(row.shippable_qty) - shipmentUnits(input.lines[0].shipped_qty);
      current = { ...initial, items: remaining ? [{ ...row, shipped_qty: input.lines[0].shipped_qty, shippable_qty: shipmentQuantity(`${remaining / 1000n}.${String(remaining % 1000n).padStart(3, "0")}`),
        serial_ids: row.serial_ids.filter(s => !input.lines[0].serial_ids.includes(s)), serials: row.serials.filter(s => !input.lines[0].serial_ids.includes(s.serial_id)) }] : [] };
      return command;
    }),
    shipmentCommandStatusNoReplay: vi.fn(async () => ({ schema_version: "1.0", lookup_status: "confirmed", request_hash: await shipmentRequestHash(initial.request_id, submitted!), command: { ...command, idempotency_replayed: true } })),
  };
  return { adapter, store, detail: afterOutbound(), access: access(), otherWriteBusy: false, otherWriteBlocked: () => false, onBlocking: vi.fn(), onDetail: vi.fn() };
}
async function fill(quantity = "1") {
  const field = await screen.findByRole("textbox", { name: "本包数量 OUT-001" }); fireEvent.change(field, { target: { value: quantity } });
  for (const [name, value] of [["目标位置 ID", ID(1)], ["目标人员 ID（可选）", ID(2)], ["承运商", "人工承运"], ["本包运单号", "TRACK-01"]]) fireEvent.change(screen.getByRole("textbox", { name }), { target: { value } });
}
const submit = () => fireEvent.click(screen.getByRole("button", { name: "登记本包发运" }));
it("registers selected partial quantity then refreshes remaining quantity and package history", async () => {
  const p = props(); render(<FormalMaterialRequestShipmentPanel {...p} />); await fill("0.125"); submit();
  await waitFor(() => expect(p.onDetail).toHaveBeenCalled()); expect(p.adapter.createShipment).toHaveBeenCalledTimes(1);
  expect(p.adapter.createShipment.mock.calls[0][1].lines[0].shipped_qty).toBe("0.125");
  await waitFor(() => expect((screen.getByRole("textbox", { name: "本包数量 OUT-001" }) as HTMLInputElement).value).toBe("1.875"));
  expect(screen.getByText("SHP-001")).toBeTruthy(); expect(p.store.read().kind).toBe("missing");
});
it("rejects over-quantity and requires explicit SN selection for the partial package", async () => {
  const p = props(true); render(<FormalMaterialRequestShipmentPanel {...p} />); await fill("3"); submit();
  await screen.findByText(/不超过当前可发运数量/); expect(p.adapter.createShipment).not.toHaveBeenCalled();
  await fill("1"); submit(); await screen.findByText(/请选择与本包数量一致/); expect(p.adapter.createShipment).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("checkbox", { name: "本包 SN SN-B" })); submit();
  await waitFor(() => expect(p.onDetail).toHaveBeenCalled()); expect(p.adapter.createShipment.mock.calls[0][1].lines[0].serial_ids).toEqual(shipmentPage(true).items[0].serial_ids.slice(1));
});
it("persists a lost response across remount and recovers by original-key GET without a second POST", async () => {
  const p = props(); p.adapter.createShipment.mockRejectedValue(new Error("network interrupted"));
  const view = render(<FormalMaterialRequestShipmentPanel {...p} />); await fill(); submit(); await screen.findByText(/network interrupted/);
  expect(p.store.read().kind).toBe("valid"); const original = p.store.read().value; view.unmount();
  p.adapter.shipmentCommandStatusNoReplay.mockResolvedValue({ schema_version: "1.0", lookup_status: "not_observed", request_hash: null, command: null });
  render(<FormalMaterialRequestShipmentPanel {...p} detail={null} store={createShipmentStore()} />);
  fireEvent.click(screen.getByRole("button", { name: "只读核验原发运" })); await screen.findByText(/尚未观察到原发运结果/); expect(p.store.read().kind).toBe("valid");
  p.adapter.shipmentCommandStatusNoReplay.mockResolvedValue({ schema_version: "1.0", lookup_status: "confirmed", request_hash: await shipmentRequestHash(original.request_id, original.input), command: { ...shipmentResult(original.input), idempotency_replayed: true } });
  fireEvent.click(screen.getByRole("button", { name: "只读核验原发运" })); await waitFor(() => expect(p.onDetail).toHaveBeenCalled());
  expect(p.store.read().kind).toBe("missing"); expect(p.adapter.createShipment).toHaveBeenCalledTimes(1);
});
it("blocks stale quantity and storage failures before POST", async () => {
  const p = props(); render(<FormalMaterialRequestShipmentPanel {...p} />); await fill();
  p.adapter.listShipmentOptions.mockResolvedValue({ ...shipmentPage(), items: [{ ...shipmentPage().items[0], shipped_qty: "1.000", shippable_qty: "1.000" }] });
  submit(); await screen.findByText(/可发运余量或 SN 已变化/); expect(p.adapter.createShipment).not.toHaveBeenCalled();
  p.adapter.listShipmentOptions.mockResolvedValue(shipmentPage()); p.store.persist = () => { throw new Error("storage unavailable"); };
  submit(); await screen.findByText(/storage unavailable/); expect(p.adapter.createShipment).not.toHaveBeenCalled();
});
it("retains the old request on a late response after changing detail", async () => {
  const p = props(); let finish!: (value: any) => void; p.adapter.createShipment.mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  const view = render(<FormalMaterialRequestShipmentPanel {...p} />); await fill(); submit();
  await waitFor(() => expect(p.adapter.createShipment).toHaveBeenCalledTimes(1)); const saved = p.store.read();
  view.rerender(<FormalMaterialRequestShipmentPanel {...p} detail={{ ...afterOutbound(), request_id: ID(99) }} />);
  await act(async () => finish(shipmentResult(saved.value.input)));
  expect(p.onDetail).not.toHaveBeenCalled(); expect(p.store.read()).toEqual(saved); expect(p.adapter.shipmentCommandStatusNoReplay).not.toHaveBeenCalled();
});
it("rechecks other writes before persisting", async () => {
  const p = props(); let blocked = false; p.otherWriteBlocked = () => blocked; p.adapter.loadAccessNoReplay.mockImplementation(async () => { blocked = true; return access(); });
  render(<FormalMaterialRequestShipmentPanel {...p} />); await fill(); submit();
  await screen.findByText(/其他操作正在提交/); expect(p.store.read().kind).toBe("missing"); expect(p.adapter.createShipment).not.toHaveBeenCalled();
});
