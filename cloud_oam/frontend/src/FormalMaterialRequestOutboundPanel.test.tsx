// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import FormalMaterialRequestOutboundPanel from "./FormalMaterialRequestOutboundPanel";
import { createOutboundStore } from "./materialRequestOutbound";
import { outboundPage, outboundResult, afterOutbound } from "./materialRequestOutboundTestFixtures";
import { afterPick } from "./materialRequestPickTestFixtures";
import { access, identity } from "./materialRequestReservationTestFixtures";

afterEach(() => { cleanup(); localStorage.clear(); });
function props(serial = false): any {
  return { adapter: { listOutboundOptions: vi.fn().mockResolvedValue(outboundPage(serial)), loadIdentityNoReplay: vi.fn().mockResolvedValue(identity()), loadAccessNoReplay: vi.fn().mockResolvedValue(access()),
    detailNoReplay: vi.fn().mockResolvedValueOnce(afterPick()).mockResolvedValue(afterOutbound()), createOutbound: vi.fn().mockResolvedValue(outboundResult(serial)),
    outboundCommandStatusNoReplay: vi.fn().mockResolvedValue({ ...outboundResult(serial), idempotency_replayed: true }) },
    access: access(), detail: afterPick(), store: createOutboundStore(), otherWriteBusy: false, otherWriteBlocked: () => false, onBlocking: vi.fn(), onDetail: vi.fn() };
}
async function select() {
  fireEvent.click(screen.getByRole("button", { name: "查看明细 1 可出库明细" }));
  fireEvent.click(await screen.findByRole("button", { name: "选择出库" }));
  fireEvent.change(screen.getByRole("textbox", { name: "出库备注" }), { target: { value: "实物核对一致" } });
}
it("shows the original reservation, persists intent before one POST, and confirms by readback", async () => {
  const p = props(); p.adapter.createOutbound.mockImplementation(async (_id: string, body: unknown) => {
    const stored = p.store.read(); expect(stored.kind).toBe("valid"); expect(stored.value.input).toEqual(body); return outboundResult();
  });
  render(<FormalMaterialRequestOutboundPanel {...p} />); await select();
  expect(p.adapter.createOutbound).not.toHaveBeenCalled(); fireEvent.click(screen.getByRole("button", { name: "确认出库" }));
  await waitFor(() => expect(p.onDetail).toHaveBeenCalledWith(afterOutbound()));
  expect(p.adapter.createOutbound).toHaveBeenCalledTimes(1); expect(p.store.read().kind).toBe("missing");
});
it("selects exact original SNs and rejects an incomplete selection", async () => {
  const p = props(true); render(<FormalMaterialRequestOutboundPanel {...p} />); await select();
  fireEvent.click(screen.getByRole("button", { name: "确认出库" }));
  await waitFor(() => expect(screen.getAllByText(/请选择与出库数量一致/).length).toBeGreaterThan(0));
  expect(p.adapter.createOutbound).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("checkbox", { name: "出库 SN SN-A" })); fireEvent.click(screen.getByRole("checkbox", { name: "出库 SN SN-B" }));
  fireEvent.click(screen.getByRole("button", { name: "确认出库" }));
  await waitFor(() => expect(p.onDetail).toHaveBeenCalled()); expect(p.adapter.createOutbound).toHaveBeenCalledTimes(1);
});
it("never repeats a POST after an ambiguous network result", async () => {
  const p = props(); p.adapter.createOutbound.mockRejectedValue(new Error("network interrupted"));
  render(<FormalMaterialRequestOutboundPanel {...p} />); await select(); fireEvent.click(screen.getByRole("button", { name: "确认出库" }));
  await waitFor(() => expect(p.store.read().kind).toBe("valid"));
  const recover = await screen.findByRole("button", { name: "核验原出库操作" });
  await waitFor(() => expect((recover as HTMLButtonElement).disabled).toBe(false)); fireEvent.click(recover);
  await waitFor(() => expect(p.store.read().kind).toBe("missing")); expect(p.adapter.createOutbound).toHaveBeenCalledTimes(1);
});
it("stops before POST when another write blocks during fresh preflight", async () => {
  const p = props(); let blocked = false; p.otherWriteBlocked = () => blocked;
  p.adapter.loadAccessNoReplay.mockImplementation(async () => { blocked = true; return access(); });
  render(<FormalMaterialRequestOutboundPanel {...p} />); await select(); fireEvent.click(screen.getByRole("button", { name: "确认出库" }));
  await waitFor(() => expect(screen.getAllByText(/其他写入正在进行/).length).toBeGreaterThan(0));
  expect(p.adapter.createOutbound).not.toHaveBeenCalled(); expect(p.store.read().kind).toBe("missing");
});
it("does not POST if saving the recovery trace fails", async () => {
  const p = props(); p.store = { read: () => ({ kind: "missing" }), persist() { throw new Error("disk unavailable"); }, clear: vi.fn() };
  render(<FormalMaterialRequestOutboundPanel {...p} />); await select(); fireEvent.click(screen.getByRole("button", { name: "确认出库" }));
  await waitFor(() => expect(screen.getAllByText(/disk unavailable/).length).toBeGreaterThan(0)); expect(p.adapter.createOutbound).not.toHaveBeenCalled();
});

it("keeps the original trace when the same request advances before a late POST response", async () => {
  const p = props(); let resolve!: (value: unknown) => void;
  p.adapter.createOutbound.mockImplementation(() => new Promise(done => { resolve = done; }));
  const view = render(<FormalMaterialRequestOutboundPanel {...p} />);
  await select(); fireEvent.click(screen.getByRole("button", { name: "确认出库" }));
  await waitFor(() => expect(p.adapter.createOutbound).toHaveBeenCalledTimes(1));
  const saved = p.store.read();
  view.rerender(<FormalMaterialRequestOutboundPanel {...p} detail={{ ...afterOutbound(), request_version: 7 }} />);
  await act(async () => { resolve(outboundResult()); });
  expect(p.onDetail).not.toHaveBeenCalled();
  expect(p.store.read()).toEqual(saved);
  expect(p.adapter.outboundCommandStatusNoReplay).not.toHaveBeenCalled();
  expect(p.adapter.createOutbound).toHaveBeenCalledTimes(1);
});
