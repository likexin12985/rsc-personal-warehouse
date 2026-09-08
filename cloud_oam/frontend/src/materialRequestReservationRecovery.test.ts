import { describe, expect, it, vi } from "vitest";
import { createReservationRecoveryStore, recoverReservationCommand } from "./materialRequestReservationRecovery";
import { validateMaterialRequestReservationCommandStatus, validateMaterialRequestReservationMutationResult } from "./formalMaterialRequestReservationCommandStatus";
import { afterReservation, access, identity, reservationResult, reservationSentinel, SERIAL_ID, SERIAL_2_ID } from "./materialRequestReservationTestFixtures";
function storage() { const values = new Map<string, string>(); return { values, getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => { values.set(key, value); }, removeItem: (key: string) => { values.delete(key); } }; }
function setup() {
  const memory = storage(); const store = createReservationRecoveryStore(memory); const sentinel = reservationSentinel(); store.persist(sentinel);
  const adapter = { loadIdentityNoReplay: vi.fn().mockResolvedValue(identity()), loadAccessNoReplay: vi.fn().mockResolvedValue(access()),
    detailNoReplay: vi.fn().mockResolvedValue(afterReservation()), reservationCommandStatusNoReplay: vi.fn().mockResolvedValue({ schema_version: "1.0", lookup_status: "confirmed", command: { ...reservationResult(), idempotency_replayed: true } }),
    createReservation: vi.fn() } as any;
  return { memory, store, sentinel, adapter };
}
describe("durable reservation recovery", () => {
  it("keeps old and unknown-field records blocked and rejects zero quantity", () => {
    const memory = storage(); const store = createReservationRecoveryStore(memory);
    memory.setItem("cloud-oam-material-request-reservation-sentinel-v1", JSON.stringify({ ...reservationSentinel(), v: 1 }));
    expect(store.read().kind).toBe("corrupt"); expect(() => store.persist(reservationSentinel())).toThrow(); expect(memory.values.size).toBe(1);
    memory.values.clear(); expect(() => store.persist({ ...reservationSentinel(), reserved_qty: "0.000" })).toThrow();
    memory.setItem("cloud-oam-material-request-reservation-sentinel-v1", JSON.stringify({ ...reservationSentinel(), extra: 1 })); expect(store.read().kind).toBe("corrupt");
  });
  it("returns unavailable if the original coordinate cannot be read", () => {
    const store = createReservationRecoveryStore({ getItem: () => { throw new Error("denied"); }, setItem: vi.fn(), removeItem: vi.fn() });
    expect(store.read().kind).toBe("unavailable"); expect(() => store.persist(reservationSentinel())).toThrow();
  });
  it("clears only after exact command, detail and fresh identity checks", async () => {
    const { adapter, store, sentinel } = setup();
    const outcome = await recoverReservationCommand(adapter, store, sentinel);
    expect(outcome.command.reservation_no).toBe("RS-001"); expect(store.read().kind).toBe("missing");
    expect(adapter.loadIdentityNoReplay).toHaveBeenCalledTimes(2); expect(adapter.createReservation).not.toHaveBeenCalled();
  });
  it.each([5, 6])("recovers the original partial fact after later reservation progress to version %s", async (currentVersion) => {
    const { adapter, store, sentinel } = setup();
    store.clear(sentinel.x_request_id); sentinel.reserved_qty = "1.000"; store.persist(sentinel);
    const historical = { ...reservationResult(), reserved_qty: "1.000", current_request_version: 5,
      state_axes: { ...reservationResult().state_axes, reservation_status: "pending" }, idempotency_replayed: true };
    adapter.reservationCommandStatusNoReplay.mockResolvedValue({ schema_version: "1.0", lookup_status: "confirmed", command: historical });
    adapter.detailNoReplay.mockResolvedValue({ ...afterReservation(), request_version: currentVersion });
    const outcome = await recoverReservationCommand(adapter, store, sentinel);
    expect(outcome.command.request_version).toBe(4); expect(outcome.command.state_axes.reservation_status).toBe("pending");
    expect(outcome.detail.request_version).toBe(currentVersion); expect(outcome.detail.states.reservation_status).toBe("reserved");
    expect(store.read().kind).toBe("missing"); expect(adapter.createReservation).not.toHaveBeenCalled();
  });
  it.each(["same_version_change", "not_reserved", "reserved_to_pending", "other_axis", "revision", "stale_detail"])(
    "retains recovery coordinates for an unsafe historical-to-current relation: %s", async (kind) => {
      const { adapter, store, sentinel } = setup();
      const historical = { ...reservationResult(), current_request_version: 5,
        state_axes: { ...reservationResult().state_axes, reservation_status: "pending" }, idempotency_replayed: true };
      const current = { ...afterReservation(), request_version: 5 };
      if (kind === "same_version_change") { historical.current_request_version = 4; current.request_version = 4; }
      if (kind === "not_reserved") current.states.reservation_status = "not_reserved";
      if (kind === "reserved_to_pending") { historical.state_axes.reservation_status = "reserved"; current.states.reservation_status = "pending"; }
      if (kind === "other_axis") current.states.notification_status = "sent";
      if (kind === "revision") historical.revision_no = 2;
      if (kind === "stale_detail") current.request_version = 4;
      adapter.reservationCommandStatusNoReplay.mockResolvedValue({ schema_version: "1.0", lookup_status: "confirmed", command: historical });
      adapter.detailNoReplay.mockResolvedValue(current);
      await expect(recoverReservationCommand(adapter, store, sentinel)).rejects.toThrow();
      expect(store.read().kind).toBe("valid"); expect(adapter.createReservation).not.toHaveBeenCalled();
    },
  );
  it.each(["not_observed", "serial", "revision", "cursor", "identity", "access", "route", "detail", "replaced"])("keeps %s unknown with no POST", async (kind) => {
    const { adapter, store, sentinel, memory } = setup(); const result = { ...reservationResult(), idempotency_replayed: true };
    if (kind === "not_observed") adapter.reservationCommandStatusNoReplay.mockResolvedValue({ schema_version: "1.0", lookup_status: "not_observed", command: null });
    if (kind === "serial") result.serial_ids = [SERIAL_ID, SERIAL_2_ID];
    if (kind === "revision") result.revision_no = 2;
    if (kind === "cursor") result.source_ledger_cursor = 9;
    if (["serial", "revision", "cursor"].includes(kind)) adapter.reservationCommandStatusNoReplay.mockResolvedValue({ schema_version: "1.0", lookup_status: "confirmed", command: result });
    if (kind === "identity") adapter.loadIdentityNoReplay.mockResolvedValueOnce(identity()).mockResolvedValue({ ...identity(), authorization_version: 8 });
    if (kind === "access") adapter.loadAccessNoReplay.mockResolvedValueOnce(access()).mockResolvedValue({ ...access(), can_read_material_catalog: false });
    if (kind === "detail") adapter.detailNoReplay.mockResolvedValue({ ...afterReservation(), request_version: 3 });
    if (kind === "replaced") memory.setItem("cloud-oam-material-request-reservation-sentinel-v1", JSON.stringify({ ...sentinel, source_balance_version: 12 }));
    await expect(recoverReservationCommand(adapter, store, sentinel, () => kind !== "route")).rejects.toThrow();
    expect(store.read().kind).toBe("valid"); expect(adapter.createReservation).not.toHaveBeenCalled();
  });
  it("requires new command evidence fields and enforces SN counts and positive quantity", () => {
    for (const key of ["source_balance_version", "source_ledger_cursor", "serial_ids"]) {
      const result = reservationResult(); delete result[key]; expect(() => validateMaterialRequestReservationMutationResult(result)).toThrow();
    }
    expect(() => validateMaterialRequestReservationMutationResult({ ...reservationResult(), reserved_qty: "0.000" })).toThrow();
    expect(() => validateMaterialRequestReservationMutationResult({ ...reservationResult(), serial_ids: [SERIAL_ID] })).toThrow();
    expect(() => validateMaterialRequestReservationCommandStatus({ schema_version: "1.0", lookup_status: "not_observed", command: reservationResult() })).toThrow();
  });
});
