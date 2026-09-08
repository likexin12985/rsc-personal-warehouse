// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { createPickStore, pickUnits, validatePickPage, validatePickResult, validatePickStatus } from "./materialRequestReservationPick";
import { recoverPick } from "./materialRequestPickRecovery";
import { pickPage, pickResult, pickSentinel, afterPick } from "./materialRequestPickTestFixtures";
import { access, identity } from "./materialRequestReservationTestFixtures";

afterEach(() => localStorage.clear());
function recoveryAdapter(overrides: object = {}): any {
  return { loadIdentityNoReplay: vi.fn().mockResolvedValue(identity()), loadAccessNoReplay: vi.fn().mockResolvedValue(access()),
    pickCommandStatusNoReplay: vi.fn().mockResolvedValue({ ...pickResult(), idempotency_replayed: true }),
    detailNoReplay: vi.fn().mockResolvedValue(afterPick()), createPick: vi.fn(), ...overrides };
}
it("uses exact decimal arithmetic", () => { expect(pickUnits("999999999999999.999")).toBe(999999999999999999n); expect(pickUnits("0.001")).toBe(1n); });
it.each(["1e0", "0.0001", "-1", 1, true, "NaN", "1000000000000000"])("rejects invalid quantity %s", value => expect(() => pickUnits(value)).toThrow());
it("validates exact candidate ownership, quantity and serial limits", () => {
  expect(validatePickPage(pickPage(true)).items[0].serials).toHaveLength(2);
  const wrong = pickPage(); expect(() => validatePickPage({ ...wrong, items: [{ ...wrong.items[0], pickable_qty: "2.001" }] })).toThrow();
  expect(() => validatePickPage({ ...wrong, items: [wrong.items[0], wrong.items[0]] })).toThrow();
  expect(() => validatePickPage({ ...wrong, extra: true })).toThrow();
});
it.each(["source_stock_account_id", "picked_qty", "pick_transaction_id", "request_version"])("rejects a missing result coordinate %s", field => {
  const result = { ...pickResult() } as Record<string, unknown>; delete result[field]; expect(() => validatePickResult(result)).toThrow();
});
it("validates lookup evidence and forbids a false historical marker", () => {
  expect(validatePickStatus({ schema_version: "1.0", lookup_status: "not_observed", command: null })).toBeNull();
  expect(() => validatePickStatus({ schema_version: "1.0", lookup_status: "confirmed", command: pickResult() })).toThrow();
});
it("persists the full original intent and will not overwrite another unresolved pick", () => {
  const store = createPickStore(); const original = pickSentinel(); store.persist(original);
  expect(store.read()).toEqual({ kind: "valid", value: original }); expect(() => store.persist(original)).toThrow();
  expect(() => store.clear("another-trace")).toThrow(); store.clear(original.trace); expect(store.read().kind).toBe("missing");
});
it("fails closed on storage failure and corrupt records", () => {
  const unavailable = createPickStore({ getItem() { throw new Error("denied"); }, setItem: vi.fn(), removeItem: vi.fn() });
  expect(unavailable.read().kind).toBe("unavailable"); expect(() => unavailable.persist(pickSentinel())).toThrow();
  const corrupt = createPickStore({ getItem: () => '{"v":0}', setItem: vi.fn(), removeItem: vi.fn() }); expect(corrupt.read().kind).toBe("corrupt");
});
it("recovers only by reading exact original facts", async () => {
  const store = createPickStore(), original = pickSentinel(), adapter = recoveryAdapter(); store.persist(original);
  const outcome = await recoverPick(adapter, store, original);
  expect(outcome.detail).toEqual(afterPick()); expect(store.read().kind).toBe("missing"); expect(adapter.createPick).not.toHaveBeenCalled();
});
it("keeps an earlier partial-pick result when later pick advances the request", async () => {
  const store = createPickStore(), original = pickSentinel(); store.persist(original);
  const result = { ...pickResult(), state_axes: { ...pickResult().state_axes, outbound_status: "pending_pick" }, current_request_version: 6, idempotency_replayed: true };
  const detail = { ...afterPick(), request_version: 6 };
  const outcome = await recoverPick(recoveryAdapter({ pickCommandStatusNoReplay: vi.fn().mockResolvedValue(result), detailNoReplay: vi.fn().mockResolvedValue(detail) }), store, original);
  expect(outcome.command.request_version).toBe(5); expect(outcome.detail.request_version).toBe(6);
});
it("recovers the original picking command after independent physical outbound", async () => {
  const store = createPickStore(), original = pickSentinel(); store.persist(original);
  const result = { ...pickResult(), current_request_version: 6, idempotency_replayed: true };
  const detail = { ...afterPick(), request_version: 6, states: { ...afterPick().states, outbound_status: "outbound" } };
  const adapter = recoveryAdapter({ pickCommandStatusNoReplay: vi.fn().mockResolvedValue(result), detailNoReplay: vi.fn().mockResolvedValue(detail) });
  const outcome = await recoverPick(adapter, store, original);
  expect(outcome.command.state_axes.outbound_status).toBe("picked");
  expect(outcome.detail.states.outbound_status).toBe("outbound");
  expect(store.read().kind).toBe("missing");
  expect(adapter.createPick).not.toHaveBeenCalled();
});
it.each(["missing", "identity", "permission", "quantity", "account", "serial", "axis", "route"])("retains the original trace on %s mismatch", async kind => {
  const store = createPickStore(), original = pickSentinel(); store.persist(original);
  const adapter = recoveryAdapter();
  if (kind === "missing") adapter.pickCommandStatusNoReplay.mockResolvedValue(null);
  if (kind === "identity") adapter.loadIdentityNoReplay.mockResolvedValue({ ...identity(), authorization_version: 8 });
  if (kind === "permission") adapter.loadAccessNoReplay.mockResolvedValue({ ...access(), can_read_allocation_options: false });
  if (kind === "quantity") adapter.pickCommandStatusNoReplay.mockResolvedValue({ ...pickResult(), picked_qty: "1.000", idempotency_replayed: true });
  if (kind === "account") adapter.pickCommandStatusNoReplay.mockResolvedValue({ ...pickResult(), target_stock_account_id: pickResult().pick_id, idempotency_replayed: true });
  if (kind === "serial") adapter.pickCommandStatusNoReplay.mockResolvedValue({ ...pickResult(true), idempotency_replayed: true });
  if (kind === "axis") adapter.detailNoReplay.mockResolvedValue({ ...afterPick(), states: { ...afterPick().states, shipment_status: "shipped" } });
  await expect(recoverPick(adapter, store, original, () => kind !== "route")).rejects.toThrow();
  expect(store.read()).toEqual({ kind: "valid", value: original }); expect(adapter.createPick).not.toHaveBeenCalled();
});
