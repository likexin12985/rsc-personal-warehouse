// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeAll, expect, it, vi } from "vitest";
import Panel from "./FormalPersonalFulfillmentPanel";
import { props, candidate, result, sid, slid } from "./myFulfillmentTestFixtures";
import type { FormalMaterialRequestAdapter } from "./formalMaterialRequestAdapter";
beforeAll(async () => { const { webcrypto } = await vi.importActual<{ webcrypto: Crypto }>("node:crypto"); Object.defineProperty(globalThis.crypto, "subtle", { value: webcrypto.subtle, configurable: true }); });
afterEach(() => { cleanup(); localStorage.clear(); });
function mount(p = props()) { return { p, view: render(<Panel {...p} adapter={p.adapter as unknown as FormalMaterialRequestAdapter} />) }; }
async function prepareReceipt() { await waitFor(() => expect((screen.getByRole("button", { name: "核对包裹 SHP-TEST-1" }) as HTMLButtonElement).disabled).toBe(false)); fireEvent.click(screen.getByRole("button", { name: "核对包裹 SHP-TEST-1" })); const input = await screen.findByRole("textbox", { name: "合格数量 SKU-1" }); await waitFor(() => expect((input as HTMLInputElement).disabled).toBe(false)); fireEvent.change(input, { target: { value: "1" } }); fireEvent.click(screen.getByRole("button", { name: "预览本次验收" })); }
it("persists receipt before a single POST and clears through both original readbacks", async () => {
  const p = props(); const original = p.adapter.personalFulfillment.submit.getMockImplementation()!; p.adapter.personalFulfillment.submit.mockImplementation(async (...args) => { expect(p.store.read().kind).toBe("valid"); return original(...args); }); mount(p); await prepareReceipt();
  fireEvent.click(screen.getByRole("button", { name: "确认验收" })); fireEvent.click(screen.getByRole("button", { name: "确认验收" })); await waitFor(() => expect(p.onDetail).toHaveBeenCalled()); expect(p.store.read().kind).toBe("missing"); expect(p.adapter.personalFulfillment.submit).toHaveBeenCalledTimes(1); expect(p.adapter.personalFulfillment.trace).toHaveBeenCalledTimes(1);
});
it("keeps unknown receipt and only reads its original result", async () => {
  const p = props(); p.adapter.personalFulfillment.submit.mockRejectedValue(new Error("网络中断")); mount(p); await prepareReceipt(); fireEvent.click(screen.getByRole("button", { name: "确认验收" })); await screen.findByText("网络中断"); const saved = p.store.read(); expect(saved.kind).toBe("valid"); if (saved.kind !== "valid") throw new Error("missing original"); const confirmed = { schema_version: "1.0", lookup_status: "confirmed", command: { ...await result(saved.value.command), idempotency_replayed: true } }; p.adapter.personalFulfillment.status.mockResolvedValue(confirmed as never); p.adapter.personalFulfillment.trace.mockResolvedValue(confirmed as never);
  fireEvent.click(screen.getByRole("button", { name: "只读核验原收货或入账" })); await waitFor(() => expect(p.onDetail).toHaveBeenCalled()); expect(p.adapter.personalFulfillment.submit).toHaveBeenCalledTimes(1); expect(p.store.read().kind).toBe("missing");
});
it("rechecks the candidate before posting", async () => { const p = props(); mount(p); await prepareReceipt(); p.adapter.personalFulfillment.receiptCandidate.mockResolvedValue({ ...candidate, can_receive: false, blocked_reason: "permission_required" }); fireEvent.click(screen.getByRole("button", { name: "确认验收" })); await screen.findByText(/包裹待验收数量或权限已变化/); expect(p.adapter.personalFulfillment.submit).not.toHaveBeenCalled(); });
it("performs independent posting from the selected immutable receipt", async () => { const p = props(); mount(p); const button = await screen.findByRole("button", { name: "核对入账 RCT-TEST-1" }); await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false)); fireEvent.click(button); fireEvent.click(screen.getByRole("button", { name: "确认本人入账" })); await waitFor(() => expect(p.onDetail).toHaveBeenCalled()); expect(p.adapter.personalFulfillment.submit.mock.calls[0][1].kind).toBe("inbound"); expect(p.store.read().kind).toBe("missing"); });
it("selects only the package's exact SN and never guesses an unmatched scan", async () => { const p = props(); p.adapter.personalFulfillment.receiptCandidate.mockResolvedValue({ ...candidate, lines: [{ ...candidate.lines[0], tracking_mode: "serial", shipped_qty: "1.000", unconfirmed_qty: "1.000", remaining_serials: [{ serial_id: sid, serial_no: "SN-ONLY", qr_code: "QR-ONLY" }] }] }); mount(p); const button = await screen.findByRole("button", { name: "核对包裹 SHP-TEST-1" }); await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false)); fireEvent.click(button); const scan = await screen.findByRole("textbox", { name: "扫描SN或二维码" }); await waitFor(() => expect((scan as HTMLInputElement).disabled).toBe(false)); fireEvent.change(scan, { target: { value: "OTHER" } }); fireEvent.click(screen.getByRole("button", { name: "匹配并标记合格" })); await screen.findByText(/扫描结果不唯一/); expect((screen.getByRole("combobox", { name: "SN SN-ONLY" }) as HTMLSelectElement).value).toBe(""); fireEvent.change(scan, { target: { value: "QR-ONLY" } }); fireEvent.click(screen.getByRole("button", { name: "匹配并标记合格" })); expect((screen.getByRole("combobox", { name: "SN SN-ONLY" }) as HTMLSelectElement).value).toBe("accepted"); });
it("leaves a late POST original for recovery after unmount", async () => { const p = props(); let resolve!: (value: Awaited<ReturnType<typeof result>>) => void; p.adapter.personalFulfillment.submit.mockImplementation(() => new Promise(r => { resolve = r; })); const { view } = mount(p); await prepareReceipt(); fireEvent.click(screen.getByRole("button", { name: "确认验收" })); await waitFor(() => expect(p.adapter.personalFulfillment.submit).toHaveBeenCalledOnce()); const saved = p.store.read(); if (saved.kind !== "valid") throw new Error("missing"); view.unmount(); await act(async () => resolve(await result(saved.value.command))); expect(p.store.read().kind).toBe("valid"); expect(p.adapter.personalFulfillment.status).not.toHaveBeenCalled(); });

it("keeps failed exception uploads retryable while blocking receipt submission", async () => {
  const p = props();
  const uploadClient: import("./FormalFileUploadField").FormalFileUploadClient = {
    prepare: vi.fn(async (file, purpose) => ({ file, purpose, original_filename: file.name, size_bytes: file.size, mime_type: file.type, sha256: "ab".repeat(32), intent_headers: { "Idempotency-Key": "file-intent-12345678", "X-Request-ID": "file-intent-trace" }, complete_headers: { "X-Request-ID": "file-complete-trace" } })),
    execute: vi.fn().mockRejectedValueOnce(new Error("上传结果待确认")).mockImplementationOnce(async value => ({ file_id: sid, purpose: value.purpose, status: "available", verified_at: "2026-01-04T00:00:00Z", sha256: value.sha256, size_bytes: value.size_bytes, mime_type: value.mime_type })),
  };
  render(<Panel {...p} adapter={p.adapter as unknown as FormalMaterialRequestAdapter} uploadClient={uploadClient} />);
  await prepareReceipt(); fireEvent.change(screen.getByRole("combobox", { name: "验收条件 SKU-1" }), { target: { value: "shortage" } });
  fireEvent.change(screen.getByLabelText("异常验收凭证"), { target: { files: [new File([new Uint8Array([1, 2, 3])], "evidence.jpg", { type: "image/jpeg" })] } });
  await screen.findByText("上传结果待确认"); const retry = screen.getByRole("button", { name: "按原上传坐标重试" }) as HTMLButtonElement;
  expect(retry.closest("fieldset")?.disabled).toBe(false); expect(retry.disabled).toBe(false); expect((screen.getByRole("button", { name: "预览本次验收" }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(retry); await screen.findByText("状态：available（已完成严格确认）");
  await waitFor(() => expect((screen.getByRole("button", { name: "预览本次验收" }) as HTMLButtonElement).disabled).toBe(false)); expect(uploadClient.prepare).toHaveBeenCalledTimes(1); expect(uploadClient.execute).toHaveBeenCalledTimes(2);
});

it("shows rejected SN after refresh without offering an inbound for rejected-only acceptance", async () => {
  const p = props();
  const { inbounds } = await import("./myFulfillmentTestFixtures");
  const page = structuredClone(inbounds), row = page.items[0]; row.status = "no_accepted";
  Object.assign(row.detail!.lines[0], { condition: "rejected", tracking_mode: "serial", accepted_qty: "0.000", rejected_qty: "1.000", rejected_serials: [{ serial_id: sid, serial_no: "REJECTED-ONLY-SN" }] });
  p.adapter.personalFulfillment.inbounds.mockResolvedValue(page);
  const { view } = mount(p); await screen.findByText(/拒收SN（不入账）：REJECTED-ONLY-SN/);
  expect(screen.queryByRole("button", { name: "核对入账 RCT-TEST-1" })).toBeNull();
  view.unmount(); mount(p); await screen.findByText(/验收条件：拒收/);
  expect(p.adapter.personalFulfillment.submit).not.toHaveBeenCalled();
});
