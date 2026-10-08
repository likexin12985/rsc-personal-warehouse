// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeAll, expect, it, vi } from "vitest";
import Panel from "./FormalMaterialRequestReturnCompensationPanel";
import { detail, remaining } from "./materialRequestRemainingCancellationTestFixtures";
import { identity, access } from "./materialRequestReservationTestFixtures";
import { createReturnCompensationStore } from "./materialRequestReturnCompensationRecovery";
import { type ReturnCompensation, type ReturnCompensationCandidates, validateReturnCompensationCandidates, validateReturnCompensationInput } from "./materialRequestReturnCompensation";
import type { FormalMaterialRequestAdapter } from "./formalMaterialRequestAdapter";
beforeAll(async () => { const { webcrypto } = await vi.importActual<{ webcrypto: Crypto }>("node:crypto"); Object.defineProperty(globalThis.crypto, "subtle", { value: webcrypto.subtle, configurable: true }); });
afterEach(() => { cleanup(); localStorage.clear(); });
const inbound = "99999999-1111-4111-8111-000000000002";
const result: ReturnCompensation = { schema_version: "1.0", cancellation_scope: "posted_return_compensation", compensation_id: "99999999-1111-4111-8111-000000000003",
  inbound_id: inbound, request_id: detail.request_id, revision_id: detail.current_revision_id, request_line_id: detail.lines[0].request_line_id,
  request_version: detail.request_version, cancelled_qty: "1.000", request_hash: "a".repeat(64), evidence_sha256: "b".repeat(64), cancelled_at: "2026-10-06T00:00:00Z", replayed: false };
const before: ReturnCompensationCandidates = { schema_version: "1.0", request_id: detail.request_id, request_version: detail.request_version, items: [{
  inbound_id: inbound, request_line_id: result.request_line_id, inbound_request_hash: "c".repeat(64), inbound_plan_hash: "d".repeat(64),
  quantity: "1.000", sku_code: "SKU-RETURN", material_name: "退回物料", posted_at: "2026-10-06T00:00:00Z", compensate_permitted: true, compensation: null }] };
const after: ReturnCompensationCandidates = { ...before, items: before.items.map(row => ({ ...row, compensate_permitted: false, compensation: { ...result, replayed: true } })) };
const current = { ...detail, allowed_actions: [], lines: detail.lines.map(line => ({ ...line, cancelled_qty: line.request_line_id === result.request_line_id ? "1.000" : "0.000" })) };
const quantities = { ...remaining, schema_version: "2.0", lines: remaining.lines.map(line => ({ ...line,
  cancelled_qty: line.request_line_id === result.request_line_id ? "1.000" : "0.000", unreserved_qty: line.request_line_id === result.request_line_id ? "1.000" : line.approved_qty,
  unfulfilled_cancelled_qty: "0.000", return_compensated_qty: line.request_line_id === result.request_line_id ? "1.000" : "0.000", returned_pending_compensation_qty: "0.000" })) };
function props() {
  const store = createReturnCompensationStore();
  const adapter = { returnCompensationCandidates: vi.fn(async () => before), compensateReturned: vi.fn(async () => result),
    returnCompensationStatusNoReplay: vi.fn(async () => ({ lookup_status: "confirmed" as const, command: { ...result, replayed: true } })),
    detailNoReplay: vi.fn(async () => current), remainingFulfillment: vi.fn(async () => quantities),
    loadIdentityNoReplay: vi.fn(async () => identity()), loadAccessNoReplay: vi.fn(async () => ({ ...access(), schema_version: "1.0" as const })) };
  adapter.compensateReturned.mockImplementation(async () => { expect(store.read().kind).toBe("valid"); adapter.returnCompensationCandidates.mockResolvedValue(after); return result; });
  return { adapter, store, detail, access: { ...access(), schema_version: "1.0" as const }, onBlocking: vi.fn(), onDetail: vi.fn() };
}
function mounted(p = props()) { return { p, view: render(<Panel {...p} adapter={p.adapter as unknown as FormalMaterialRequestAdapter} />) }; }
async function fill() {
  await waitFor(() => expect((screen.getByRole("combobox", { name: "选择退回入账" }) as HTMLSelectElement).disabled).toBe(false));
  fireEvent.change(screen.getByRole("combobox", { name: "选择退回入账" }), { target: { value: inbound } });
  fireEvent.change(screen.getByRole("textbox", { name: "补偿原因" }), { target: { value: "已退回入账，不再补发" } });
}
const submit = () => fireEvent.click(screen.getByRole("button", { name: "确认本笔不再补发" }));
it("saves exact source before one POST and clears only after full readback", async () => {
  const { p } = mounted(); await fill(); submit(); submit(); await screen.findByText(/已补偿取消/);
  expect(p.adapter.compensateReturned).toHaveBeenCalledTimes(1); expect(p.store.read().kind).toBe("missing"); expect(p.onDetail).toHaveBeenCalledWith(current); expect(p.onBlocking).toHaveBeenLastCalledWith(false);
});
it("preserves unknown submission and only reads the original result", async () => {
  const p = props(); p.adapter.compensateReturned.mockRejectedValue(new Error("连接中断"));
  p.adapter.returnCompensationStatusNoReplay.mockResolvedValue({ lookup_status: "not_observed", command: null } as never);
  mounted(p); await fill(); submit(); await screen.findByText("连接中断"); fireEvent.click(screen.getByRole("button", { name: "只读核验原补偿" })); await screen.findByText(/尚未查到原补偿结果/); expect(p.store.read().kind).toBe("valid");
  p.adapter.returnCompensationStatusNoReplay.mockResolvedValue({ lookup_status: "confirmed", command: { ...result, replayed: true } }); p.adapter.returnCompensationCandidates.mockResolvedValue(after);
  fireEvent.click(screen.getByRole("button", { name: "只读核验原补偿" })); await screen.findByText(/已补偿取消/);
  expect(p.adapter.compensateReturned).toHaveBeenCalledTimes(1); expect(p.store.read().kind).toBe("missing");
});
it("refuses changed source permission before POST", async () => {
  const p = props(); mounted(p); await fill(); p.adapter.returnCompensationCandidates.mockResolvedValue({ ...before, items: before.items.map(row => ({ ...row, compensate_permitted: false })) });
  submit(); await screen.findByRole("alert"); expect(p.adapter.compensateReturned).not.toHaveBeenCalled(); expect(p.store.read().kind).toBe("missing");
});
it("keeps request when current quantities contradict its compensation", async () => {
  const p = props(); p.adapter.detailNoReplay.mockResolvedValue(detail as never); mounted(p); await fill(); submit(); await screen.findByRole("alert"); expect(p.store.read().kind).toBe("valid");
});
it("keeps pending request after unmount before acknowledgement", async () => {
  const p = props(); let resolve!: (value: ReturnCompensation) => void; p.adapter.compensateReturned.mockImplementation(() => new Promise(done => { resolve = done; }));
  const { view } = mounted(p); await fill(); submit(); await waitFor(() => expect(p.adapter.compensateReturned).toHaveBeenCalledTimes(1)); view.unmount(); await act(async () => resolve(result));
  expect(p.store.read().kind).toBe("valid"); expect(p.adapter.returnCompensationStatusNoReplay).not.toHaveBeenCalled();
});
it("rejects duplicate sources, stale versions, numeric quantities and extra fields", () => {
  expect(() => validateReturnCompensationCandidates({ ...before, items: [...before.items, ...before.items] }, detail)).toThrow();
  expect(() => validateReturnCompensationCandidates({ ...before, request_version: detail.request_version + 1 }, detail)).toThrow();
  const input = { expected_request_version: detail.request_version, reason: "不再补发", inbound_id: inbound, inbound_request_hash: "c".repeat(64), inbound_plan_hash: "d".repeat(64), cancelled_qty: "1.000" };
  expect(validateReturnCompensationInput(input)).toEqual(input); expect(() => validateReturnCompensationInput({ ...input, cancelled_qty: 1 })).toThrow(); expect(() => validateReturnCompensationInput({ ...input, force: true })).toThrow();
});
it("allows an empty draft candidate page at version zero without enabling compensation", () => {
  const draft = { ...detail, status: "draft" as const, request_version: 0 };
  const empty = { ...before, request_version: 0, items: [] };
  expect(validateReturnCompensationCandidates(empty, draft)).toEqual(empty);
  for (const request_version of [-1, true, "0", 0.5]) {
    expect(() => validateReturnCompensationCandidates({ ...empty, request_version })).toThrow();
  }
  expect(() => validateReturnCompensationCandidates({ ...before, request_version: 0 })).toThrow();
  expect(() => validateReturnCompensationCandidates(empty, detail)).toThrow();
  expect(() => validateReturnCompensationInput({ expected_request_version: 0, reason: "草稿不能补偿", inbound_id: inbound,
    inbound_request_hash: "c".repeat(64), inbound_plan_hash: "d".repeat(64), cancelled_qty: "1.000" })).toThrow();
});
