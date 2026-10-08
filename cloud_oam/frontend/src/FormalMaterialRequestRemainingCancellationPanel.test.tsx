// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeAll, expect, it, vi } from "vitest";
import Panel from "./FormalMaterialRequestRemainingCancellationPanel";
import { props, cancelled, result, remaining } from "./materialRequestRemainingCancellationTestFixtures";
import type { FormalMaterialRequestAdapter } from "./formalMaterialRequestAdapter";
beforeAll(async () => { const { webcrypto } = await vi.importActual<{ webcrypto: Crypto }>("node:crypto"); Object.defineProperty(globalThis.crypto, "subtle", { value: webcrypto.subtle, configurable: true }); });
afterEach(() => { cleanup(); localStorage.clear(); });
function mounted(p = props(), extra = {}) { return { p, view: render(<Panel {...p} adapter={p.adapter as unknown as FormalMaterialRequestAdapter} {...extra} />) }; }
async function fill() {
  await waitFor(() => expect((screen.getByRole("textbox", { name: "取消原因" }) as HTMLTextAreaElement).disabled).toBe(false));
  fireEvent.change(screen.getByRole("textbox", { name: "取消原因" }), { target: { value: "剩余物料不再需要" } });
}
const submit = () => fireEvent.click(screen.getByRole("button", { name: "确认取消全部剩余需求" }));
it("persists exact quantities before POST and confirms only from original readback", async () => {
  const p = props(); p.adapter.cancelRemaining.mockImplementation(async () => { expect(p.store.read().kind).toBe("valid"); p.adapter.remainingCancellationState.mockResolvedValue(cancelled); return result; });
  mounted(p); await fill(); expect(screen.getByLabelText("待取消数量").textContent).toContain(result.lines[0].cancelled_qty); submit(); submit();
  await screen.findByText("剩余需求已取消");
  expect(p.adapter.cancelRemaining).toHaveBeenCalledTimes(1); expect(p.store.read().kind).toBe("missing");
  expect(p.onBlocking).toHaveBeenLastCalledWith(false); expect(p.onCancelled).toHaveBeenLastCalledWith(true);
});
it("keeps unknown POST and never resends it during read-only recovery", async () => {
  const p = props(); p.adapter.cancelRemaining.mockRejectedValue(new Error("连接中断"));
  p.adapter.remainingCancellationStatusNoReplay.mockResolvedValue({ lookup_status: "not_observed", command: null } as never);
  mounted(p); await fill(); submit(); await screen.findByText("连接中断");
  fireEvent.click(screen.getByRole("button", { name: "只读核验原取消" })); await screen.findByText(/尚未查到原取消结果/); expect(p.store.read().kind).toBe("valid");
  p.adapter.remainingCancellationStatusNoReplay.mockResolvedValue({ lookup_status: "confirmed", command: { ...result, replayed: true } }); p.adapter.remainingCancellationState.mockResolvedValue(cancelled);
  fireEvent.click(screen.getByRole("button", { name: "只读核验原取消" })); await screen.findByText("剩余需求已取消");
  expect(p.adapter.cancelRemaining).toHaveBeenCalledTimes(1); expect(p.store.read().kind).toBe("missing");
});
it("does not cancel unsettled stock or supply tasks", async () => {
  const p = props(); p.adapter.remainingFulfillment.mockResolvedValue({ ...remaining, open_supply_tasks: 1 });
  mounted(p); await screen.findByText(/请先处理占用/); expect(screen.queryByRole("button", { name: "确认取消全部剩余需求" })).toBeNull();
  expect(p.adapter.cancelRemaining).not.toHaveBeenCalled();
});
it("shows no remaining quantity after full inbound without offering an empty cancellation", async () => {
  const p = props();
  p.adapter.remainingFulfillment.mockResolvedValue({ ...remaining, lines: remaining.lines.map(line => ({
    ...line, posted_qty: line.approved_qty, unreserved_qty: "0.000",
  })) });
  mounted(p);
  await screen.findByText("没有待取消的剩余数量，业务关闭另行确认。");
  expect(screen.queryByText("取消明细不完整")).toBeNull();
  expect(screen.queryByRole("textbox", { name: "取消原因" })).toBeNull();
  expect(screen.queryByRole("button", { name: "确认取消全部剩余需求" })).toBeNull();
  expect(p.adapter.cancelRemaining).not.toHaveBeenCalled();
  expect(p.onBlocking).toHaveBeenLastCalledWith(false);
});
it("rejects quantities changing after preview", async () => {
  const p = props(); mounted(p); await fill();
  p.adapter.remainingFulfillment.mockResolvedValue({ ...remaining, open_supply_tasks: 1 }); submit();
  await screen.findByRole("alert"); expect(p.adapter.cancelRemaining).not.toHaveBeenCalled(); expect(p.store.read().kind).toBe("missing");
});
it("keeps pending request after leaving before acknowledgement", async () => {
  const p = props(); let resolve!: (value: typeof result) => void;
  p.adapter.cancelRemaining.mockImplementation(() => new Promise(done => { resolve = done; }));
  const { view } = mounted(p); await fill(); submit(); await waitFor(() => expect(p.adapter.cancelRemaining).toHaveBeenCalledTimes(1));
  view.unmount(); await act(async () => resolve(result)); expect(p.store.read().kind).toBe("valid"); expect(p.adapter.remainingCancellationStatusNoReplay).not.toHaveBeenCalled();
});
it("never clears the saved request when detail quantities disagree", async () => {
  const p = props(); p.adapter.detailNoReplay.mockResolvedValue(p.detail as never);
  mounted(p); await fill(); submit(); await screen.findByText(/原取消与退回补偿数量不一致/); expect(p.store.read().kind).toBe("valid");
});
it("submits only the unfulfilled remainder and recovers the combined cancellation total", async () => {
  const p = props();
  p.detail = { ...p.detail, lines: p.detail.lines.map(line => ({ ...line, cancelled_qty: "0.500" })) };
  const before = { ...remaining, schema_version: "2.0", lines: remaining.lines.map(line => ({ ...line,
    cancelled_qty: "0.500", posted_qty: "1.000", unreserved_qty: "0.500",
    unfulfilled_cancelled_qty: "0.000", return_compensated_qty: "0.500", returned_pending_compensation_qty: "0.000" })) };
  const after = { ...before, lines: before.lines.map(line => ({ ...line,
    cancelled_qty: "1.000", unreserved_qty: "0.000", unfulfilled_cancelled_qty: "0.500" })) };
  const command = { ...result, lines: result.lines.map(line => ({ ...line, cancelled_qty: "0.500" })) };
  const current = { ...p.detail, allowed_actions: [], lines: p.detail.lines.map(line => ({ ...line, cancelled_qty: "1.000" })) };
  p.adapter.remainingFulfillment.mockResolvedValue(before as never);
  p.adapter.detailNoReplay.mockResolvedValue(current);
  p.adapter.remainingCancellationStatusNoReplay.mockResolvedValue({ lookup_status: "confirmed", command: { ...command, replayed: true } });
  p.adapter.cancelRemaining.mockImplementation(async () => {
    const saved = p.store.read();
    expect(saved.kind).toBe("valid");
    if (saved.kind === "valid") expect(saved.value.input.lines).toEqual(command.lines);
    p.adapter.remainingFulfillment.mockResolvedValue(after as never);
    p.adapter.remainingCancellationState.mockResolvedValue({ ...cancelled, cancellation: command });
    return command;
  });
  mounted(p); await fill();
  expect(screen.getByLabelText("待取消数量").textContent).toContain("0.500");
  submit(); await screen.findByText("剩余需求已取消");
  expect(p.adapter.cancelRemaining).toHaveBeenCalledTimes(1);
  expect(p.onDetail).toHaveBeenLastCalledWith(current);
  expect(p.store.read().kind).toBe("missing");
});
