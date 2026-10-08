// @vitest-environment jsdom
import { beforeAll, afterEach, expect, it, vi } from "vitest";
import { cancellationInputFromRemainder, remainingCancelFingerprint, validateRemainingCancelInput, validateRemainingCancellationState } from "./materialRequestRemainingCancellation";
import { recoverRemainingCancellation } from "./materialRequestRemainingCancellationRecovery";
import { createFormalMaterialRequestAdapter } from "./formalMaterialRequestAdapter";
import { identity } from "./materialRequestReservationTestFixtures";
import { props, open, cancelled, result, detail, remaining } from "./materialRequestRemainingCancellationTestFixtures";
beforeAll(async () => { const { webcrypto } = await vi.importActual<{ webcrypto: Crypto }>("node:crypto"); Object.defineProperty(globalThis.crypto, "subtle", { value: webcrypto.subtle, configurable: true }); });
afterEach(() => localStorage.clear());
function combined() {
  const current = { ...detail, lines: detail.lines.map(line => ({ ...line, final_approved_qty: "3.000", cancelled_qty: "1.000" })) };
  const view = { ...remaining, schema_version: "2.0", lines: remaining.lines.map(line => ({ ...line,
    approved_qty: "3.000", cancelled_qty: "1.000", posted_qty: "1.000", unreserved_qty: "1.000",
    unfulfilled_cancelled_qty: "0.000", return_compensated_qty: "1.000", returned_pending_compensation_qty: "0.000" })) };
  return { current, view };
}
it("cancels only unfulfilled demand while preserving prior returned compensation", () => {
  const { current, view } = combined();
  const input = cancellationInputFromRemainder(view, current, "剩余不再需要");
  expect(input.lines.every(line => line.cancelled_qty === "1.000")).toBe(true);
  expect(input).not.toHaveProperty("return_compensated_qty");
});
it.each(["uncompensated", "prior_cancel", "pending_personal"])("blocks combined remaining cancellation for %s", kind => {
  const { current, view } = combined();
  for (const line of view.lines) {
    if (kind === "uncompensated") Object.assign(line, { returned_pending_compensation_qty: "1.000", return_compensated_qty: "0.000", cancelled_qty: "0.000" });
    if (kind === "prior_cancel") Object.assign(line, { unfulfilled_cancelled_qty: "1.000", cancelled_qty: "2.000", unreserved_qty: "0.000" });
    if (kind === "pending_personal") Object.assign(line, { posted_qty: "0.000", accepted_unposted_qty: "1.000" });
  }
  const bound = { ...current, lines: current.lines.map((line, i) => ({ ...line, cancelled_qty: view.lines[i].cancelled_qty })) };
  expect(() => cancellationInputFromRemainder(view, bound, "不应提交")).toThrow("请先处理");
});
async function original() {
  const input = { expected_request_version: open.request_version, reason: "剩余不再需要", lines: result.lines };
  return { v: 1 as const, key: "remaining-test-key-0001", trace: "remaining-test-trace-0001", person_id: identity().person_id,
    authorization_version: identity().authorization_version, request_id: open.request_id, input, fingerprint: await remainingCancelFingerprint(input) };
}
it("matches backend canonical UTF8 hashing including nested decimal lines", async () => {
  expect(await remainingCancelFingerprint({ expected_request_version: 12, reason: '取消剩余 "引号" \\路径',
    lines: [{ request_line_id: "10000000-0000-4000-8000-000000000001", cancelled_qty: "0.125" }] })).toBe("41a6d484a6b698f0cfe282d39ec2f5db6605d5ff13f445f04b514d8e8280eb7a");
});
it("rejects duplicate quantities and impossible current permissions", async () => {
  const saved = await original();
  expect(() => validateRemainingCancelInput({ ...saved.input, lines: [...saved.input.lines, saved.input.lines[0]] })).toThrow();
  expect(() => validateRemainingCancelInput({ ...saved.input, lines: [] })).toThrow("取消明细不完整");
  expect(() => validateRemainingCancelInput({ ...saved.input, lines: [{ ...saved.input.lines[0], cancelled_qty: "0.000" }] })).toThrow();
  expect(() => validateRemainingCancellationState({ ...cancelled, cancel_permitted: true })).toThrow();
});
it("recovers after permission-version changes using current read authority", async () => {
  const p = props(), saved = await original(); p.store.persist(saved);
  p.adapter.remainingCancellationState.mockResolvedValue(cancelled);
  p.adapter.loadIdentityNoReplay.mockResolvedValue({ ...identity(), authorization_version: 8 });
  p.adapter.loadAccessNoReplay.mockResolvedValue({ ...p.access, authorization_version: 8 });
  await recoverRemainingCancellation(p.adapter as unknown as Parameters<typeof recoverRemainingCancellation>[0], p.store, saved);
  expect(p.store.read().kind).toBe("missing"); expect(p.adapter.cancelRemaining).not.toHaveBeenCalled();
});
it.each([false, true])("recovers combined totals only when the original unfulfilled cancellation matches (corrupt=%s)", async corrupt => {
  const p = props();
  const base = await original();
  const input = { ...base.input, lines: base.input.lines.map(line => ({ ...line, cancelled_qty: "0.500" })) };
  const saved = { ...base, input, fingerprint: await remainingCancelFingerprint(input) };
  const command = { ...result, replayed: true, lines: input.lines };
  const current = { ...detail, allowed_actions: [], lines: detail.lines.map(line => ({ ...line, cancelled_qty: "1.000" })) };
  const view = { ...remaining, schema_version: "2.0", lines: remaining.lines.map(line => ({ ...line,
    cancelled_qty: "1.000", posted_qty: "1.000", unreserved_qty: "0.000", returned_pending_compensation_qty: "0.000",
    unfulfilled_cancelled_qty: corrupt ? "0.250" : "0.500", return_compensated_qty: corrupt ? "0.750" : "0.500" })) };
  p.adapter.detailNoReplay.mockResolvedValue(current);
  p.adapter.remainingFulfillment.mockResolvedValue(view as never);
  p.adapter.remainingCancellationStatusNoReplay.mockResolvedValue({ lookup_status: "confirmed", command });
  p.adapter.remainingCancellationState.mockResolvedValue({ ...cancelled, cancellation: command });
  p.store.persist(saved);
  const recovering = recoverRemainingCancellation(p.adapter as unknown as Parameters<typeof recoverRemainingCancellation>[0], p.store, saved);
  if (corrupt) {
    await expect(recovering).rejects.toThrow("原取消与退回补偿数量不一致");
    expect(p.store.read().kind).toBe("valid");
  } else {
    await recovering;
    expect(p.store.read().kind).toBe("missing");
  }
  expect(p.adapter.cancelRemaining).not.toHaveBeenCalled();
  expect(p.adapter.remainingFulfillment).toHaveBeenCalledTimes(1);
});
it.each(["identity", "fingerprint", "late"])("keeps the exact original on %s uncertainty", async mode => {
  const p = props(), saved = await original(); p.store.persist(saved); p.adapter.remainingCancellationState.mockResolvedValue(cancelled);
  if (mode === "identity") p.adapter.loadIdentityNoReplay.mockResolvedValue({ ...identity(), person_id: "aaaaaaaa-1111-4111-8111-000000000001" });
  await expect(recoverRemainingCancellation(p.adapter as unknown as Parameters<typeof recoverRemainingCancellation>[0], p.store,
    mode === "fingerprint" ? { ...saved, fingerprint: "f".repeat(64) } : saved, () => mode !== "late")).rejects.toThrow();
  expect(p.store.read().kind).toBe("valid"); expect(p.adapter.cancelRemaining).not.toHaveBeenCalled();
});
it("binds recovery headers and uses only the non-replaying HTTP channel", async () => {
  const ordinary = vi.fn(), read = vi.fn().mockResolvedValueOnce(open).mockResolvedValueOnce({ lookup_status: "not_observed", command: null }).mockResolvedValueOnce(result);
  const adapter = createFormalMaterialRequestAdapter(identity(), ordinary as never, read as never), saved = await original();
  await adapter.remainingCancellationState!(open.request_id);
  await adapter.remainingCancellationStatusNoReplay!(open.request_id, saved.key, saved.fingerprint);
  await adapter.cancelRemaining!(open.request_id, saved.input, { "Idempotency-Key": saved.key, "X-Request-ID": saved.trace });
  expect(ordinary).not.toHaveBeenCalled();
  expect(read.mock.calls[1][0]).toBe(`/v1/material-requests/${open.request_id}/cancel-remaining-command-status`);
  expect(read.mock.calls[1][1]).toMatchObject({ cache: "no-store", headers: { "Idempotency-Key": saved.key, "X-Request-Fingerprint": saved.fingerprint } });
  expect(read.mock.calls[2][1]).toMatchObject({ method: "POST" });
});
