import { expect, it, vi } from "vitest";
import { createShipmentStore, recoverShipment, shipmentRequestHash, validateShipmentCommandStatus, matchShipmentResult } from "./materialRequestShipmentRecovery";
import { shipmentInput, shipmentResult, shipmentSentinel, shipmentPage, ID } from "./materialRequestShipmentTestFixtures";
import { afterOutbound } from "./materialRequestOutboundTestFixtures";
import { access, identity } from "./materialRequestReservationTestFixtures";
import { shipmentLineSelection, shipmentUnits, validateShipmentOptions } from "./materialRequestShipment";
import { createFormalMaterialRequestAdapter } from "./formalMaterialRequestAdapter";
function setup() {
  const items = new Map<string, string>();
  const storage = { getItem: (key: string) => items.get(key) ?? null, setItem: (key: string, value: string) => { items.set(key, value); }, removeItem: (key: string) => { items.delete(key); } };
  const store = createShipmentStore(storage), sentinel = shipmentSentinel(); store.persist(sentinel);
  const adapter = { loadIdentityNoReplay: vi.fn().mockResolvedValue(identity()), loadAccessNoReplay: vi.fn().mockResolvedValue(access()), detailNoReplay: vi.fn().mockResolvedValue(afterOutbound()),
    shipmentCommandStatusNoReplay: vi.fn().mockImplementation(async () => ({ schema_version: "1.0", lookup_status: "confirmed", request_hash: await shipmentRequestHash(sentinel.request_id, sentinel.input), command: { ...shipmentResult(), idempotency_replayed: true } })), createShipment: vi.fn() } as any;
  return { store, sentinel, adapter, storage };
}
it("preserves original intent across a new store instance and recovers only by GET", async () => {
  const p = setup(), store = createShipmentStore(p.storage);
  const result = await recoverShipment(p.adapter, store, p.sentinel);
  expect(result.command.shipment_id).toBe(shipmentResult().shipment_id); expect(store.read().kind).toBe("missing");
  expect(p.adapter.shipmentCommandStatusNoReplay).toHaveBeenCalledWith(p.sentinel.request_id, p.sentinel.key);
  expect(p.adapter.createShipment).not.toHaveBeenCalled();
});
it("not observed never clears a potentially in-flight command", async () => {
  const p = setup(); p.adapter.shipmentCommandStatusNoReplay.mockResolvedValue({ schema_version: "1.0", lookup_status: "not_observed", request_hash: null, command: null });
  await expect(recoverShipment(p.adapter, p.store, p.sentinel)).rejects.toThrow(/尚未观察/);
  expect(p.store.read()).toEqual({ kind: "valid", value: p.sentinel }); expect(p.adapter.createShipment).not.toHaveBeenCalled();
});
it.each(["request", "quantity", "destination", "hash", "version"])("retains intent when recovered %s does not match", async kind => {
  const p = setup(), command: any = shipmentResult();
  if (kind === "request") command.request_id = ID(99);
  if (kind === "destination") command.target_location_id = ID(99);
  if (kind === "quantity") command.lines = [{ ...command.lines[0], shipped_qty: "2.000" }];
  const input = kind === "version" ? { ...p.sentinel.input, expected_request_version: 7 } : p.sentinel.input;
  p.adapter.shipmentCommandStatusNoReplay.mockResolvedValue({ schema_version: "1.0", lookup_status: "confirmed", request_hash: kind === "hash" ? "0".repeat(64) : await shipmentRequestHash(p.sentinel.request_id, input), command: { ...command, idempotency_replayed: true } });
  await expect(recoverShipment(p.adapter, p.store, p.sentinel)).rejects.toThrow(); expect(p.store.read().kind).toBe("valid");
});
it("rechecks identity after reads and rejects late-page completion", async () => {
  const p = setup(); p.adapter.loadAccessNoReplay.mockResolvedValueOnce(access()).mockResolvedValue({ ...access(), authorization_version: 8 });
  await expect(recoverShipment(p.adapter, p.store, p.sentinel)).rejects.toThrow(/权限或页面/); expect(p.store.read().kind).toBe("valid");
  const q = setup(); await expect(recoverShipment(q.adapter, q.store, q.sentinel, () => false)).rejects.toThrow(); expect(q.store.read().kind).toBe("valid");
});
it("rejects an inconsistent POST versus GET shipment before clearing", async () => {
  const p = setup(); await expect(recoverShipment(p.adapter, p.store, p.sentinel, () => true, { ...shipmentResult(), shipment_id: ID(99) })).rejects.toThrow(/包裹绑定/);
  expect(p.store.read().kind).toBe("valid");
});
it("refuses overwrite, wrong-trace clear, corrupt storage and persistent storage failure", () => {
  const p = setup(); expect(() => p.store.persist(p.sentinel)).toThrow(); expect(() => p.store.clear("unrelated-trace-001")).toThrow();
  const faulty = createShipmentStore({ getItem() { throw new Error("unavailable"); }, setItem: vi.fn(), removeItem: vi.fn() });
  expect(faulty.read().kind).toBe("unavailable"); expect(() => faulty.persist(p.sentinel)).toThrow();
  const corrupt = createShipmentStore({ getItem: () => "{", setItem: vi.fn(), removeItem: vi.fn() }); expect(corrupt.read().kind).toBe("corrupt");
});
it("compares timestamps by instant and SNs by set", () => {
  const sentinel = shipmentSentinel(shipmentInput(true));
  expect(() => matchShipmentResult(sentinel, { ...shipmentResult(sentinel.input), shipped_at: "2026-09-11T18:00:00+08:00" })).not.toThrow();
  expect(() => matchShipmentResult(sentinel, { ...shipmentResult(sentinel.input), lines: [{ ...shipmentResult(sentinel.input).lines[0], serial_ids: [] }] })).toThrow();
});
it("rejects invalid status envelopes and enforces the no-replay GET transport", async () => {
  expect(() => validateShipmentCommandStatus({ schema_version: "1.0", lookup_status: "not_observed", request_hash: "f".repeat(64), command: null })).toThrow();
  const request = vi.fn().mockRejectedValue(new Error("must not refresh")), read = vi.fn().mockResolvedValue({ schema_version: "1.0", lookup_status: "not_observed", request_hash: null, command: null });
  const adapter = createFormalMaterialRequestAdapter(access(), request as any, read as any);
  await adapter.shipmentCommandStatusNoReplay!(afterOutbound().request_id, "shipment-key-test-001");
  expect(request).not.toHaveBeenCalled(); expect(read).toHaveBeenCalledTimes(1);
  expect(read.mock.calls[0][0]).toContain("/shipment-command-status"); expect(read.mock.calls[0][1]).toMatchObject({ cache: "no-store", headers: { "Idempotency-Key": "shipment-key-test-001", "Cache-Control": "no-store" } });
});
it("selects decimal partial packages without floating point and rejects invalid remaining/SN bindings", () => {
  const option = shipmentPage().items[0]; expect(shipmentLineSelection(option, "0.125", []).shipped_qty).toBe("0.125");
  expect(shipmentUnits("999999999999999.999")).toBe(999999999999999999n);
  for (const value of ["0", "3", "-1", "0.0001"]) expect(() => shipmentLineSelection(option, value, [])).toThrow();
  const serial = shipmentPage(true).items[0]; expect(() => shipmentLineSelection(serial, "1", [])).toThrow();
  expect(shipmentLineSelection(serial, "1", [serial.serial_ids[1]]).serial_ids).toEqual([serial.serial_ids[1]]);
  expect(() => validateShipmentOptions({ ...shipmentPage(), items: [{ ...option, shippable_qty: "3.000" }] })).toThrow();
  expect(() => validateShipmentOptions({ ...shipmentPage(true), items: [{ ...serial, serials: [{ ...serial.serials[0], serial_id: ID(99) }, serial.serials[1]] }] })).toThrow();
});
it("matches a fixed Python request hash including Chinese and a surrogate-pair emoji", async () => {
  const input = { ...shipmentInput(), carrier: "人工承运🚚", tracking_no: "SF-中文-001" };
  expect(await shipmentRequestHash(afterOutbound().request_id, input)).toBe("b462072b63372636539ffc0cc8b67a04d9b539605e5f3bd7891d5853b9710d83");
});
