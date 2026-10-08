// @vitest-environment jsdom
import { beforeAll, afterEach, expect, it, vi } from "vitest";
import { closeInputFingerprint, validateClosureState, validateClosureResult, validateClosureStatus } from "./materialRequestClosure";
import { createClosureStore, recoverClosure } from "./materialRequestClosureRecovery";
import { createFormalMaterialRequestAdapter } from "./formalMaterialRequestAdapter";
import { identity } from "./materialRequestReservationTestFixtures";
import { closed, open, result, sentinel, props } from "./materialRequestClosureTestFixtures";
beforeAll(async () => { const { webcrypto } = await vi.importActual<{ webcrypto: Crypto }>("node:crypto"); Object.defineProperty(globalThis.crypto, "subtle", { value: webcrypto.subtle, configurable: true }); });
afterEach(() => localStorage.clear());
it("matches canonical UTF8 SHA256, including Chinese and escaped characters", async () => {
  const input = { expected_request_version: 12, reason: '全部核对完成 "引号" \\路径' };
  expect(await closeInputFingerprint(input)).toBe("01ac6a52854a08de236d444693345859872a607a812d4f605c0ebff0bc02a0f3");
});
it("requires a real closure with complete coverage and no close permission", () => {
  expect(validateClosureState(closed)).toEqual(closed);
  expect(validateClosureState({ ...open, request_version: 0 }).request_version).toBe(0);
  expect(() => validateClosureState({ ...open, business_status: "closed" })).toThrow();
  expect(() => validateClosureState({ ...closed, close_permitted: true })).toThrow();
  expect(() => validateClosureResult({ ...result, lines: [{ ...result.lines[0], posted_qty: "1.000", remaining_qty: "1.000" }] })).toThrow();
  expect(() => validateClosureStatus({ lookup_status: "confirmed", command: result })).toThrow();
});
it("preserves corrupt storage and refuses replacement or mismatched cleanup", async () => {
  const original = await sentinel(), store = createClosureStore(); store.persist(original);
  expect(() => store.persist(original)).toThrow(); expect(() => store.clear("different-trace")).toThrow();
  expect(store.read().kind).toBe("valid"); store.clear(original.trace);
  localStorage.setItem("cloud-oam-material-request-closure-v1", "broken");
  expect(store.read().kind).toBe("corrupt"); expect(() => store.persist(original)).toThrow();
  expect(localStorage.getItem("cloud-oam-material-request-closure-v1")).toBe("broken");
});
it("stops when persistence does not survive readback", async () => {
  const store = createClosureStore({ getItem: () => null, setItem: () => {}, removeItem: () => {} });
  const original = await sentinel(); expect(() => store.persist(original)).toThrow(); expect(store.read().kind).toBe("unavailable");
});
it("recovers with current read permission after authorization version changes", async () => {
  const p = props(), original = await sentinel(); p.store.persist(original);
  p.adapter.loadIdentityNoReplay.mockResolvedValue({ ...identity(), authorization_version: 8 });
  p.adapter.loadAccessNoReplay.mockResolvedValue({ ...p.access, authorization_version: 8 });
  p.adapter.closureState.mockResolvedValue(closed);
  await recoverClosure(p.adapter as unknown as Parameters<typeof recoverClosure>[0], p.store, original);
  expect(p.store.read().kind).toBe("missing"); expect(p.adapter.closeRequest).not.toHaveBeenCalled();
  expect(p.adapter.closureCommandStatusNoReplay).toHaveBeenCalledWith(original.request_id, original.key, original.fingerprint);
});
it.each(["missing", "fingerprint", "identity", "late", "changed_record"])("retains original on %s recovery uncertainty", async mode => {
  const p = props(), original = await sentinel(); p.store.persist(original); p.adapter.closureState.mockResolvedValue(closed);
  if (mode === "missing") p.adapter.closureCommandStatusNoReplay.mockResolvedValue({ lookup_status: "not_observed", command: null } as never);
  if (mode === "identity") p.adapter.loadIdentityNoReplay.mockResolvedValue({ ...identity(), person_id: "aaaaaaaa-1111-4111-8111-000000000001" });
  if (mode === "changed_record") p.adapter.loadAccessNoReplay.mockImplementation(async () => { localStorage.setItem("cloud-oam-material-request-closure-v1", JSON.stringify({ ...original, trace: "another-trace-1234" })); return p.access; });
  await expect(recoverClosure(p.adapter as unknown as Parameters<typeof recoverClosure>[0], p.store,
    mode === "fingerprint" ? { ...original, fingerprint: "ff".repeat(32) } : original, () => mode !== "late")).rejects.toThrow();
  expect(p.store.read().kind).toBe("valid"); expect(p.adapter.closeRequest).not.toHaveBeenCalled();
  if (["identity", "fingerprint"].includes(mode)) expect(p.adapter.closureCommandStatusNoReplay).not.toHaveBeenCalled();
});
it("uses only the non-replaying HTTP channel and binds recovery headers", async () => {
  const ordinary = vi.fn(), read = vi.fn().mockResolvedValueOnce(open).mockResolvedValueOnce({ lookup_status: "not_observed", command: null }).mockResolvedValueOnce(result);
  const adapter = createFormalMaterialRequestAdapter(identity(), ordinary as never, read as never), original = await sentinel();
  await adapter.closureState!(open.request_id);
  await adapter.closureCommandStatusNoReplay!(open.request_id, original.key, original.fingerprint);
  await adapter.closeRequest!(open.request_id, original.input, { "Idempotency-Key": original.key, "X-Request-ID": original.trace });
  expect(ordinary).not.toHaveBeenCalled();
  expect(read.mock.calls[1][0]).toBe(`/v1/material-requests/${open.request_id}/close-command-status`);
  expect(read.mock.calls[1][1]).toMatchObject({ cache: "no-store", headers: { "Idempotency-Key": original.key, "X-Request-Fingerprint": original.fingerprint } });
  expect(read.mock.calls[2][1]).toMatchObject({ method: "POST" });
});
