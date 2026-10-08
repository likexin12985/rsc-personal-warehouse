// @vitest-environment jsdom
import { beforeAll, afterEach, expect, it, vi } from "vitest";
import { buildReceipt, commandHash, commandResult, inboundPage, receiptCandidate, receivingPage, canonical } from "./myFulfillmentContract";
import { createMyFulfillmentAdapter } from "./myFulfillmentAdapter";
import { createPersonalStore, recoverPersonal } from "./myFulfillmentRecovery";
import { candidate, detail, personId, sid, slid, packages, inbounds, receiptCommand, inboundCommand, result, marker, props } from "./myFulfillmentTestFixtures";
import type { FormalMaterialRequestAdapter } from "./formalMaterialRequestAdapter";
beforeAll(async () => { const { webcrypto } = await vi.importActual<{ webcrypto: Crypto }>("node:crypto"); Object.defineProperty(globalThis.crypto, "subtle", { value: webcrypto.subtle, configurable: true }); });
afterEach(() => localStorage.clear());
it("validates recipient projections and stable cursor ordering", () => {
  expect(receivingPage(packages, detail.request_id, personId).packages).toHaveLength(1);
  expect(receiptCandidate(candidate, detail.request_id, sid, personId).can_receive).toBe(true);
  expect(inboundPage(inbounds, detail.request_id, personId).can_post).toBe(true);
  expect(() => receivingPage(packages, detail.request_id, sid)).toThrow();
  expect(() => receivingPage(packages, detail.request_id, personId, sid)).toThrow();
});
it.each(["source_account", "quantity", "serial", "precision", "cursor", "posted"])('rejects recipient data corruption: %s', kind => {
  if (kind === "cursor") { expect(() => receivingPage({ ...packages, next_after_id: slid }, detail.request_id, personId)).toThrow(); return; }
  if (kind === "posted") { expect(() => inboundPage({ ...inbounds, items: [{ ...inbounds.items[0], status: "posted" }] }, detail.request_id, personId)).toThrow(); return; }
  const c = structuredClone(candidate);
  if (kind === "source_account") Object.assign(c.lines[0], { source_stock_account_id: sid });
  if (kind === "quantity") c.lines[0].accepted_qty = "1.000";
  if (kind === "serial") c.lines[0].tracking_mode = "serial";
  if (kind === "precision") { c.lines[0].shipped_qty = c.lines[0].unconfirmed_qty = "0.500"; }
  expect(() => receiptCandidate(c, detail.request_id, sid, personId)).toThrow();
});
it("checks SN choices, exact quantities and abnormal evidence before preparing a receipt", () => {
  const drafts = { [slid]: { accepted: "1", rejected: "0", condition: "normal" as const, serials: {}, evidenceFileId: null } };
  expect(buildReceipt(candidate, drafts, "2026-01-03T00:00:00Z").lines[0].accepted_qty).toBe("1.000");
  expect(() => buildReceipt(candidate, { [slid]: { ...drafts[slid], accepted: "3" } }, "2026-01-03T00:00:00Z")).toThrow();
  expect(() => buildReceipt(candidate, { [slid]: { ...drafts[slid], condition: "shortage" } }, "2026-01-03T00:00:00Z")).toThrow();
  expect(() => buildReceipt(candidate, { [slid]: { ...drafts[slid], serials: { [sid]: "accepted" } } }, "2026-01-03T00:00:00Z")).toThrow();
});
it.each([receiptCommand, inboundCommand])("validates result hash, identity and exact input for $kind", async command => {
  const r = await result(command), fp = await commandHash(detail.request_id, personId, command);
  await expect(commandResult(r, detail.request_id, personId, command, fp)).resolves.toEqual(r);
  await expect(commandResult({ ...r, person_id: sid }, detail.request_id, personId, command, fp)).rejects.toThrow();
  await expect(commandResult({ ...r, request_hash: "ff".repeat(32) }, detail.request_id, personId, command, fp)).rejects.toThrow();
});
it("only uses the injected no-replay transport and exact original headers", async () => {
  const calls: { path: string; init?: RequestInit }[] = []; const api = createMyFulfillmentAdapter(async <T,>(path: string, init?: RequestInit): Promise<T> => { calls.push({ path, init }); return {} as T; });
  await api.packages(detail.request_id); await api.receiptCandidate(detail.request_id, sid); await api.inbounds(detail.request_id); await api.submit(detail.request_id, receiptCommand, "original-key", "original-trace"); await api.status(detail.request_id, "receipt", "original-key"); await api.trace(detail.request_id, "receipt", "original-trace");
  expect(calls.filter(x => x.init?.method === "POST")).toHaveLength(1);
  expect(calls[4].init?.headers).toMatchObject({ "Idempotency-Key": "original-key", "Cache-Control": "no-store" }); expect(calls[5].init?.headers).toMatchObject({ "X-Original-Request-ID": "original-trace" });
  expect(calls[0].path).toContain("/my-receiving?limit=5");
});
it("refuses to overwrite or clear a changed original", async () => {
  const store = createPersonalStore(), original = await marker(); store.persist(original); expect(() => store.persist(original)).toThrow(); expect(() => store.clear({ ...original, trace: "another-original-trace" })).toThrow(); expect(store.read().kind).toBe("valid");
});
it.each(["missing", "changed_trace", "tampered", "page_changed", "identity_changed"])("retains original on failed recovery: %s", async mode => {
  const p = props(), original = await marker(); p.store.persist(original);
  if (mode === "missing") p.adapter.personalFulfillment.status.mockResolvedValue({ schema_version: "1.0", lookup_status: "not_observed", command: null } as never);
  if (mode === "changed_trace") p.adapter.personalFulfillment.trace.mockResolvedValue({ schema_version: "1.0", lookup_status: "confirmed", command: { ...await result(receiptCommand), receipt_id: sid, idempotency_replayed: true } } as never);
  if (mode === "identity_changed") p.adapter.loadIdentityNoReplay.mockImplementation(async () => ({ ...await props().adapter.loadIdentityNoReplay(), person_id: sid }));
  if (mode === "tampered") original.fingerprint = "ff".repeat(32);
  await expect(recoverPersonal(p.adapter as unknown as FormalMaterialRequestAdapter, p.adapter.personalFulfillment, p.store, original, () => mode !== "page_changed")).rejects.toThrow(); expect(p.store.read().kind).toBe("valid"); expect(p.adapter.personalFulfillment.submit).not.toHaveBeenCalled();
});
it("clears only after both exact readbacks and unchanged current authority", async () => {
  const p = props(), original = await marker(); p.store.persist(original);
  const recovered = await recoverPersonal(p.adapter as unknown as FormalMaterialRequestAdapter, p.adapter.personalFulfillment, p.store, original);
  expect(recovered.result.receipt_id).toBe(inbounds.items[0].receipt_id); expect(p.store.read().kind).toBe("missing"); expect(p.adapter.personalFulfillment.submit).not.toHaveBeenCalled();
});

it("preserves both accepted and rejected SN groups with the original condition", () => {
  const page = structuredClone(inbounds), line = page.items[0].detail!.lines[0];
  Object.assign(line, { condition: "shortage", tracking_mode: "serial", rejected_qty: "1.000",
    accepted_serials: [{ serial_id: sid, serial_no: "SN-ACCEPTED" }], rejected_serials: [{ serial_id: slid, serial_no: "SN-REJECTED" }] });
  expect(inboundPage(page, detail.request_id, personId).items[0].detail!.lines[0].rejected_serials[0].serial_no).toBe("SN-REJECTED");
  for (const corrupt of [() => { line.rejected_serials = []; }, () => { line.accepted_serials = []; },
    () => { line.rejected_serials[0].serial_id = sid; }, () => { line.condition = "normal"; },
    () => { line.condition = "rejected"; }, () => { line.tracking_mode = "none"; },
    () => { Object.assign(line.rejected_serials[0], { source_account_id: sid }); }]) {
    const saved = structuredClone(line); corrupt(); expect(() => inboundPage(page, detail.request_id, personId)).toThrow(); Object.keys(line).forEach(k => delete (line as unknown as Record<string, unknown>)[k]); Object.assign(line, saved);
  }
  Object.assign(line, { condition: "rejected", accepted_qty: "0.000", accepted_serials: [] }); page.items[0].status = "no_accepted";
  expect(inboundPage(page, detail.request_id, personId).items[0].status).toBe("no_accepted");
});
