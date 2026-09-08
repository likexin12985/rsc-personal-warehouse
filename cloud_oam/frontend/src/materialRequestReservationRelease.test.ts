// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { createReleaseStore, releaseUnits, validateReleasePage, validateReleaseResult, validateReleaseStatus } from "./materialRequestReservationRelease";
import { recoverRelease } from "./materialRequestReleaseRecovery";
import { releasePage, releaseResult, releaseSentinel, afterRelease } from "./materialRequestReleaseTestFixtures";
import { access, identity } from "./materialRequestReservationTestFixtures";

afterEach(() => localStorage.clear());
function recoveryAdapter(overrides: object = {}): any {
  return { loadIdentityNoReplay: vi.fn().mockResolvedValue(identity()), loadAccessNoReplay: vi.fn().mockResolvedValue(access()),
    releaseCommandStatusNoReplay: vi.fn().mockResolvedValue({ ...releaseResult(), idempotency_replayed: true }),
    detailNoReplay: vi.fn().mockResolvedValue(afterRelease()), createRelease: vi.fn(), ...overrides };
}
it("uses exact decimal arithmetic", () => { expect(releaseUnits("999999999999999.999")).toBe(999999999999999999n); expect(releaseUnits("0.001")).toBe(1n); });
it.each(["1e0", "0.0001", "-1", 1, true, "NaN", "1000000000000000"])("rejects invalid quantity %s", value => expect(() => releaseUnits(value)).toThrow());
it("validates exact candidate ownership, quantity and serial limits", () => {
  expect(validateReleasePage(releasePage(true)).items[0].serials).toHaveLength(2);
  const wrong = releasePage(); expect(() => validateReleasePage({ ...wrong, items: [{ ...wrong.items[0], releasable_qty: "2.001" }] })).toThrow();
  expect(() => validateReleasePage({ ...wrong, items: [wrong.items[0], wrong.items[0]] })).toThrow();
  expect(() => validateReleasePage({ ...wrong, extra: true })).toThrow();
});
it.each(["source_stock_account_id", "released_qty", "release_transaction_id", "request_version"])("rejects a missing result coordinate %s", field => {
  const result = { ...releaseResult() } as Record<string, unknown>; delete result[field]; expect(() => validateReleaseResult(result)).toThrow();
});
it("validates lookup evidence and forbids a false historical marker", () => {
  expect(validateReleaseStatus({ schema_version: "1.0", lookup_status: "not_observed", command: null })).toBeNull();
  expect(() => validateReleaseStatus({ schema_version: "1.0", lookup_status: "confirmed", command: releaseResult() })).toThrow();
});
it("persists the full original intent and will not overwrite another unresolved release", () => {
  const store = createReleaseStore(); const original = releaseSentinel(); store.persist(original);
  expect(store.read()).toEqual({ kind: "valid", value: original }); expect(() => store.persist(original)).toThrow();
  expect(() => store.clear("another-trace")).toThrow(); store.clear(original.trace); expect(store.read().kind).toBe("missing");
});
it("fails closed on storage failure and corrupt records", () => {
  const unavailable = createReleaseStore({ getItem() { throw new Error("denied"); }, setItem: vi.fn(), removeItem: vi.fn() });
  expect(unavailable.read().kind).toBe("unavailable"); expect(() => unavailable.persist(releaseSentinel())).toThrow();
  const corrupt = createReleaseStore({ getItem: () => '{"v":0}', setItem: vi.fn(), removeItem: vi.fn() }); expect(corrupt.read().kind).toBe("corrupt");
});
it("recovers only by reading exact original facts", async () => {
  const store = createReleaseStore(), original = releaseSentinel(), adapter = recoveryAdapter(); store.persist(original);
  const outcome = await recoverRelease(adapter, store, original);
  expect(outcome.detail).toEqual(afterRelease()); expect(store.read().kind).toBe("missing"); expect(adapter.createRelease).not.toHaveBeenCalled();
});
it("keeps an earlier partial-release result when later release advances the request", async () => {
  const store = createReleaseStore(), original = releaseSentinel(); store.persist(original);
  const result = { ...releaseResult(), state_axes: { ...releaseResult().state_axes, reservation_status: "partially_released" }, current_request_version: 6, idempotency_replayed: true };
  const detail = { ...afterRelease(), request_version: 6 };
  const outcome = await recoverRelease(recoveryAdapter({ releaseCommandStatusNoReplay: vi.fn().mockResolvedValue(result), detailNoReplay: vi.fn().mockResolvedValue(detail) }), store, original);
  expect(outcome.command.request_version).toBe(5); expect(outcome.detail.request_version).toBe(6);
});
it.each(["missing", "identity", "permission", "quantity", "account", "serial", "axis", "route"])("retains the original trace on %s mismatch", async kind => {
  const store = createReleaseStore(), original = releaseSentinel(); store.persist(original);
  const adapter = recoveryAdapter();
  if (kind === "missing") adapter.releaseCommandStatusNoReplay.mockResolvedValue(null);
  if (kind === "identity") adapter.loadIdentityNoReplay.mockResolvedValue({ ...identity(), authorization_version: 8 });
  if (kind === "permission") adapter.loadAccessNoReplay.mockResolvedValue({ ...access(), can_read_allocation_options: false });
  if (kind === "quantity") adapter.releaseCommandStatusNoReplay.mockResolvedValue({ ...releaseResult(), released_qty: "1.000", idempotency_replayed: true });
  if (kind === "account") adapter.releaseCommandStatusNoReplay.mockResolvedValue({ ...releaseResult(), target_stock_account_id: releaseResult().release_id, idempotency_replayed: true });
  if (kind === "serial") adapter.releaseCommandStatusNoReplay.mockResolvedValue({ ...releaseResult(true), idempotency_replayed: true });
  if (kind === "axis") adapter.detailNoReplay.mockResolvedValue({ ...afterRelease(), states: { ...afterRelease().states, shipment_status: "shipped" } });
  await expect(recoverRelease(adapter, store, original, () => kind !== "route")).rejects.toThrow();
  expect(store.read()).toEqual({ kind: "valid", value: original }); expect(adapter.createRelease).not.toHaveBeenCalled();
});
