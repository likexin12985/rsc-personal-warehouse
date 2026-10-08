// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeAll, expect, it, vi } from "vitest";
import Panel from "./FormalMaterialRequestRejectionPanel";
import { approvedDetail, identity, access } from "./materialRequestReservationTestFixtures";
import { inbounds, receiptId, slid } from "./myFulfillmentTestFixtures";
import { createRejectionStore } from "./materialRequestRejectionRecovery";
import { type RejectionPage, type RejectionCommand, type RejectionResult, type ProgressResult, type ProgressAction, rejectionPage, rejectionInput, progressTime, progressInput, rejectionFingerprint } from "./materialRequestRejection";
import type { FormalMaterialRequestAdapter } from "./formalMaterialRequestAdapter";
beforeAll(async () => { const { webcrypto } = await vi.importActual<{ webcrypto: Crypto }>("node:crypto"); Object.defineProperty(globalThis.crypto, "subtle", { value: webcrypto.subtle, configurable: true }); });
afterEach(() => { cleanup(); localStorage.clear(); });
const detail = approvedDetail();
const rid = "99999999-1111-4111-8111-000000000001", eid = "99999999-1111-4111-8111-000000000002";
const base: RejectionPage = { schema_version: "1.0", request_id: detail.request_id, request_version: detail.request_version, next_after_id: null,
  items: [{ receipt_id: receiptId, status: "verified", message: "已核验", detail: { ...inbounds.items[0].detail!, lines: inbounds.items[0].detail!.lines.map(l => ({ ...l, accepted_qty: "0.000", rejected_qty: "1.000", condition: "rejected" })) },
    lines: [{ receipt_line_id: slid, available_qty: "1.000", available_serials: [], register_permitted: true, registrations: [] }] }] };
const registered: RejectionResult = { schema_version: "1.0", return_id: rid, return_no: "RJR-TEST-1", request_id: detail.request_id, request_version: detail.request_version,
  receipt_id: receiptId, receipt_line_id: slid, receipt_request_hash: base.items[0].detail!.receipt_request_hash, quantity: "1.000", serial_ids: [], status: "registered", registered_at: "2026-01-04T00:00:00Z", request_hash: "a".repeat(64), replayed: true };
function afterRegistration(): RejectionPage {
  const page = structuredClone(base), line = page.items[0].lines[0];
  line.available_qty = "0.000"; line.register_permitted = false;
  line.registrations = [{ registration: registered, progress: { return_id: rid, request_id: detail.request_id, registration_request_hash: registered.request_hash, status: "registered", events: [] }, permitted_actions: ["cancel_registration", "depart"] }];
  return page;
}
function props(initial = base) {
  const store = createRejectionStore(); let page = structuredClone(initial), result: RejectionResult | ProgressResult = registered;
  const adapter = { rejectionCandidates: vi.fn(async (_request?: string, _after?: string | null) => page),
    recordRejection: vi.fn(async (_request: string, command: RejectionCommand) => {
      expect(store.read().kind).toBe("valid");
      if (command.kind === "register") { result = { ...registered, replayed: false }; page = afterRegistration(); }
      else {
        const { expected_request_version, ...input } = command.input;
        result = { ...input, event_id: command.input.action === "handover" ? "99999999-1111-4111-8111-000000000003" : eid,
          return_id: rid, request_id: detail.request_id, request_version: expected_request_version,
          recorded_at: "2026-10-06T12:00:00Z", request_hash: "b".repeat(64), replayed: false };
        page = structuredClone(page); const line = page.items[0].lines[0], history = line.registrations[0];
        history.progress.events.push({ ...result, replayed: true });
        history.progress.status = { cancel_registration: "cancelled", depart: "departed", handover: "handed_over" }[input.action] as typeof history.progress.status;
        history.permitted_actions = input.action === "depart" ? ["handover"] : [];
        if (input.action === "cancel_registration") { line.available_qty = "1.000"; line.register_permitted = true; }
      }
      return result;
    }),
    rejectionStatusNoReplay: vi.fn(async () => ({ lookup_status: "confirmed" as const, command: { ...result, replayed: true } })),
    detailNoReplay: vi.fn(async () => detail), loadIdentityNoReplay: vi.fn(async () => identity()),
    loadAccessNoReplay: vi.fn(async () => ({ ...access(), schema_version: "1.0" as const })) };
  return { adapter, store, detail, access: { ...access(), schema_version: "1.0" as const }, onBlocking: vi.fn(), onDetail: vi.fn() };
}
function mount(p = props()) { return { p, view: render(<Panel {...p} adapter={p.adapter as unknown as FormalMaterialRequestAdapter} />) }; }
async function fill(action: "register" | ProgressAction = "register") {
  await waitFor(() => expect((screen.getByRole("combobox", { name: "退回操作" }) as HTMLSelectElement).disabled).toBe(false));
  fireEvent.change(screen.getByRole("combobox", { name: "退回操作" }), { target: { value: action } });
  fireEvent.change(screen.getByRole("combobox", { name: "选择拒收或退回记录" }), { target: { value: action === "register" ? slid : rid } });
  fireEvent.change(screen.getByRole("textbox", { name: "退回操作说明" }), { target: { value: "拒收退回操作" } });
  if (action === "register") fireEvent.change(screen.getByRole("textbox", { name: "退回数量" }), { target: { value: "1" } });
  if (action === "depart" || action === "handover") fireEvent.change(screen.getByLabelText("实际发生时间"), { target: { value: "2026-10-06T10:00" } });
  if (action === "handover") {
    fireEvent.change(screen.getByRole("textbox", { name: "承运商" }), { target: { value: "顺丰" } });
    fireEvent.change(screen.getByRole("textbox", { name: "运单号" }), { target: { value: "SF-RETURN-1" } });
  }
}
it("persists original registration before a single POST and reads exact source before clearing", async () => {
  const { p } = mount(); await fill(); fireEvent.click(screen.getByRole("button", { name: "登记拒收退回" })); fireEvent.click(screen.getByRole("button", { name: "登记拒收退回" }));
  await screen.findByText(/RJR-TEST-1.*已登记/); expect(p.adapter.recordRejection).toHaveBeenCalledTimes(1);
  expect(p.store.read().kind).toBe("missing"); expect(p.onDetail).toHaveBeenCalledWith(detail);
});
it("keeps unknown requests through unmount and uses only original-request reads", async () => {
  const p = props(); p.adapter.recordRejection.mockRejectedValue(new Error("连接中断"));
  const { view } = mount(p); await fill(); fireEvent.click(screen.getByRole("button", { name: "登记拒收退回" })); await screen.findByText("连接中断");
  view.unmount(); expect(p.store.read().kind).toBe("valid");
  p.adapter.rejectionCandidates.mockResolvedValue(afterRegistration()); mount(p);
  await waitFor(() => expect((screen.getByRole("button", { name: "只读核验原退回" }) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(screen.getByRole("button", { name: "只读核验原退回" }));
  await waitFor(() => expect(p.store.read().kind).toBe("missing")); expect(p.adapter.recordRejection).toHaveBeenCalledTimes(1);
});
it("records departure and handover separately with exact predecessor evidence", async () => {
  const { p } = mount(props(afterRegistration())); await fill("depart"); fireEvent.click(screen.getByRole("button", { name: "确认实物发出" }));
  await screen.findByText(/RJR-TEST-1.*实物已发出/); expect(p.store.read().kind).toBe("missing");
  await fill("handover"); fireEvent.click(screen.getByRole("button", { name: "确认承运交接" }));
  await screen.findByText(/RJR-TEST-1.*已交承运.*SF-RETURN-1/);
  expect(p.adapter.recordRejection).toHaveBeenCalledTimes(2);
  expect(p.adapter.recordRejection.mock.calls[1][1]).toMatchObject({ kind: "progress", input: { action: "handover", previous_event_id: eid, previous_request_hash: "b".repeat(64) } });
});
it("cancels registration without deleting history and restores available quantity", async () => {
  const { p } = mount(props(afterRegistration())); await fill("cancel_registration"); fireEvent.click(screen.getByRole("button", { name: "撤销退回登记" }));
  await screen.findByText(/RJR-TEST-1.*登记已撤销/); expect(p.store.read().kind).toBe("missing"); expect(screen.getByText(/可登记 1.000/)).toBeTruthy();
});
it("source permission change blocks a new POST", async () => {
  const { p } = mount(); await fill(); const changed = structuredClone(base); changed.items[0].lines[0].register_permitted = false;
  p.adapter.rejectionCandidates.mockResolvedValue(changed); fireEvent.click(screen.getByRole("button", { name: "登记拒收退回" }));
  await screen.findByRole("alert"); expect(p.adapter.recordRejection).not.toHaveBeenCalled(); expect(p.store.read().kind).toBe("missing");
});
it("mismatched returned source keeps the original request and never replays", async () => {
  const { p } = mount(); await fill(); p.adapter.rejectionStatusNoReplay.mockResolvedValue({ lookup_status: "not_observed", command: null } as never);
  fireEvent.click(screen.getByRole("button", { name: "登记拒收退回" })); await screen.findByText(/尚未查到原退回结果/);
  expect(p.store.read().kind).toBe("valid"); expect(p.adapter.recordRejection).toHaveBeenCalledTimes(1);
});
it("keeps request if page disappears before acknowledgement", async () => {
  const p = props(); let resolve!: (value: RejectionResult) => void; p.adapter.recordRejection.mockImplementation(() => new Promise(r => { resolve = r; }));
  const { view } = mount(p); await fill(); fireEvent.click(screen.getByRole("button", { name: "登记拒收退回" })); await waitFor(() => expect(p.store.read().kind).toBe("valid"));
  view.unmount(); await act(async () => resolve(registered)); expect(p.store.read().kind).toBe("valid"); expect(p.adapter.rejectionStatusNoReplay).not.toHaveBeenCalled();
});
it("rejects fabricated free quantity, duplicate SN, and unbound progress", () => {
  const valid = afterRegistration(); expect(rejectionPage(valid, detail.request_id)).toEqual(valid);
  const invalid = structuredClone(valid); invalid.items[0].lines[0].available_qty = "1.000";
  expect(() => rejectionPage(invalid, detail.request_id)).toThrow();
  expect(() => rejectionInput({ expected_request_version: detail.request_version, reason: "退回", receipt_id: receiptId, receipt_line_id: slid, receipt_request_hash: "a".repeat(64), quantity: "1.000", serial_ids: [rid, rid] })).toThrow();
  expect(() => progressInput({ expected_request_version: detail.request_version, reason: "交运", action: "handover", registration_request_hash: "a".repeat(64), previous_event_id: null, previous_request_hash: null, physical_at: "2026-10-06T00:00:00Z", carrier: "SF", tracking_no: "001" })).toThrow();
});
it("normalizes physical time exactly as the backend before fingerprinting", async () => {
  expect(progressTime("2026-10-06T08:00:00.000+08:00")).toBe("2026-10-06T00:00:00Z");
  expect(progressTime("2026-10-06T08:00:00.123456+08:00")).toBe("2026-10-06T00:00:00.123456Z");
  const input = { expected_request_version: detail.request_version, reason: "实物发出", action: "depart" as const, registration_request_hash: "a".repeat(64), previous_event_id: null, previous_request_hash: null, physical_at: "2026-10-06T08:00:00.123+08:00", carrier: null, tracking_no: null };
  expect(await rejectionFingerprint({ kind: "progress", return_id: rid, input })).toBe(await rejectionFingerprint({ kind: "progress", return_id: rid, input: { ...input, physical_at: "2026-10-06T00:00:00.123000Z" } }));
});
it("submits only the selected rejected SN with its exact quantity", async () => {
  const source = structuredClone(base), original = source.items[0].detail!.lines[0], line = source.items[0].lines[0];
  original.rejected_qty = "2.000"; original.tracking_mode = "serial";
  original.rejected_serials = [{ serial_id: rid, serial_no: "SN-A" }, { serial_id: eid, serial_no: "SN-B" }];
  line.available_qty = "2.000"; line.available_serials = original.rejected_serials;
  const p = props(source); p.adapter.recordRejection.mockRejectedValue(new Error("请求结果待核验")); mount(p);
  await waitFor(() => expect((screen.getByRole("combobox", { name: "退回操作" }) as HTMLSelectElement).disabled).toBe(false));
  fireEvent.change(screen.getByRole("combobox", { name: "选择拒收或退回记录" }), { target: { value: slid } });
  fireEvent.click(screen.getByRole("checkbox", { name: "SN-B" }));
  fireEvent.change(screen.getByRole("textbox", { name: "退回操作说明" }), { target: { value: "仅退回第二件" } });
  fireEvent.click(screen.getByRole("button", { name: "登记拒收退回" })); await screen.findByText("请求结果待核验");
  expect(p.adapter.recordRejection.mock.calls[0][1]).toMatchObject({ kind: "register", input: { quantity: "1.000", serial_ids: [eid] } });
  expect(p.store.read().kind).toBe("valid");
});
