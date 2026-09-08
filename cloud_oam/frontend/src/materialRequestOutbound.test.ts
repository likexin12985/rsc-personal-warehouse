// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { createOutboundStore, outboundUnits, validateOutboundPage, validateOutboundResult, validateOutboundStatus } from "./materialRequestOutbound";
import { recoverOutbound } from "./materialRequestOutboundRecovery";
import { outboundPage, outboundResult, outboundSentinel, afterOutbound } from "./materialRequestOutboundTestFixtures";
import { access, identity } from "./materialRequestReservationTestFixtures";

afterEach(() => localStorage.clear());
function recoveryAdapter(overrides: object = {}): any {
  return { loadIdentityNoReplay: vi.fn().mockResolvedValue(identity()), loadAccessNoReplay: vi.fn().mockResolvedValue(access()),
    outboundCommandStatusNoReplay: vi.fn().mockResolvedValue({ ...outboundResult(), idempotency_replayed: true }),
    detailNoReplay: vi.fn().mockResolvedValue(afterOutbound()), createOutbound: vi.fn(), ...overrides };
}
it("uses exact decimal arithmetic", () => { expect(outboundUnits("999999999999999.999")).toBe(999999999999999999n); expect(outboundUnits("0.001")).toBe(1n); });
it.each(["1e0", "0.0001", "-1", 1, true, "NaN", "1000000000000000"])("rejects invalid quantity %s", value => expect(() => outboundUnits(value)).toThrow());
it("validates exact candidate ownership, quantity and serial limits", () => {
  expect(validateOutboundPage(outboundPage(true)).items[0].serials).toHaveLength(2);
  const wrong = outboundPage(); expect(() => validateOutboundPage({ ...wrong, items: [{ ...wrong.items[0], outboundable_qty: "2.001" }] })).toThrow();
  expect(() => validateOutboundPage({ ...wrong, items: [wrong.items[0], wrong.items[0]] })).toThrow();
  expect(() => validateOutboundPage({ ...wrong, extra: true })).toThrow();
});
it.each(["source_stock_account_id", "outbound_qty", "outbound_transaction_id", "request_version"])("rejects a missing result coordinate %s", field => {
  const result = { ...outboundResult() } as Record<string, unknown>; delete result[field]; expect(() => validateOutboundResult(result)).toThrow();
});
it("validates lookup evidence and forbids a false historical marker", () => {
  expect(validateOutboundStatus({ schema_version: "1.0", lookup_status: "not_observed", command: null })).toBeNull();
  expect(() => validateOutboundStatus({ schema_version: "1.0", lookup_status: "confirmed", command: outboundResult() })).toThrow();
});
it("persists the full original intent and will not overwrite another unresolved pick", () => {
  const store = createOutboundStore(); const original = outboundSentinel(); store.persist(original);
  expect(store.read()).toEqual({ kind: "valid", value: original }); expect(() => store.persist(original)).toThrow();
  expect(() => store.clear("another-trace")).toThrow(); store.clear(original.trace); expect(store.read().kind).toBe("missing");
});
it("fails closed on storage failure and corrupt records", () => {
  const unavailable = createOutboundStore({ getItem() { throw new Error("denied"); }, setItem: vi.fn(), removeItem: vi.fn() });
  expect(unavailable.read().kind).toBe("unavailable"); expect(() => unavailable.persist(outboundSentinel())).toThrow();
  const corrupt = createOutboundStore({ getItem: () => '{"v":0}', setItem: vi.fn(), removeItem: vi.fn() }); expect(corrupt.read().kind).toBe("corrupt");
});
it("recovers only by reading exact original facts", async () => {
  const store = createOutboundStore(), original = outboundSentinel(), adapter = recoveryAdapter(); store.persist(original);
  const outcome = await recoverOutbound(adapter, store, original);
  expect(outcome.detail).toEqual(afterOutbound()); expect(store.read().kind).toBe("missing"); expect(adapter.createOutbound).not.toHaveBeenCalled();
});
it("keeps an earlier partial-pick result when later pick advances the request", async () => {
  const store = createOutboundStore(), original = outboundSentinel(); store.persist(original);
  const result = { ...outboundResult(), state_axes: { ...outboundResult().state_axes, outbound_status: "pending_pick" }, current_request_version: 7, idempotency_replayed: true };
  const detail = { ...afterOutbound(), request_version: 7 };
  const outcome = await recoverOutbound(recoveryAdapter({ outboundCommandStatusNoReplay: vi.fn().mockResolvedValue(result), detailNoReplay: vi.fn().mockResolvedValue(detail) }), store, original);
  expect(outcome.command.request_version).toBe(6); expect(outcome.detail.request_version).toBe(7);
});
it.each(["missing", "identity", "permission", "quantity", "account", "serial", "axis", "route"])("retains the original trace on %s mismatch", async kind => {
  const store = createOutboundStore(), original = outboundSentinel(); store.persist(original);
  const adapter = recoveryAdapter();
  if (kind === "missing") adapter.outboundCommandStatusNoReplay.mockResolvedValue(null);
  if (kind === "identity") adapter.loadIdentityNoReplay.mockResolvedValue({ ...identity(), authorization_version: 8 });
  if (kind === "permission") adapter.loadAccessNoReplay.mockResolvedValue({ ...access(), can_read_allocation_options: false });
  if (kind === "quantity") adapter.outboundCommandStatusNoReplay.mockResolvedValue({ ...outboundResult(), outbound_qty: "1.000", idempotency_replayed: true });
  if (kind === "account") adapter.outboundCommandStatusNoReplay.mockResolvedValue({ ...outboundResult(), target_stock_account_id: outboundResult().pick_id, idempotency_replayed: true });
  if (kind === "serial") adapter.outboundCommandStatusNoReplay.mockResolvedValue({ ...outboundResult(true), idempotency_replayed: true });
  if (kind === "axis") adapter.detailNoReplay.mockResolvedValue({ ...afterOutbound(), states: { ...afterOutbound().states, shipment_status: "shipped" } });
  await expect(recoverOutbound(adapter, store, original, () => kind !== "route")).rejects.toThrow();
  expect(store.read()).toEqual({ kind: "valid", value: original }); expect(adapter.createOutbound).not.toHaveBeenCalled();
});
