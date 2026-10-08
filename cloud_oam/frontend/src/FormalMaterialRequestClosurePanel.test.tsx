// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeAll, expect, it, vi } from "vitest";
import Panel from "./FormalMaterialRequestClosurePanel";
import { props, closed, result, open, sentinel } from "./materialRequestClosureTestFixtures";
import type { FormalMaterialRequestAdapter } from "./formalMaterialRequestAdapter";
beforeAll(async () => { const { webcrypto } = await vi.importActual<{ webcrypto: Crypto }>("node:crypto"); Object.defineProperty(globalThis.crypto, "subtle", { value: webcrypto.subtle, configurable: true }); });
afterEach(() => { cleanup(); localStorage.clear(); });
function mounted(p = props(), extra = {}) {
  return { p, view: render(<Panel {...p} adapter={p.adapter as unknown as FormalMaterialRequestAdapter} {...extra} />) };
}
async function fill() {
  await waitFor(() => expect((screen.getByRole("textbox", {name:"关闭说明"}) as HTMLTextAreaElement).disabled).toBe(false));
  fireEvent.change(screen.getByRole("textbox", {name:"关闭说明"}), {target:{value:"全部核对完成"}});
}
const submit = () => fireEvent.click(screen.getByRole("button", {name:"核验并关闭业务"}));
it("persists before POST and confirms only by original readback", async () => {
  const p = props(); p.adapter.closeRequest.mockImplementation(async () => {
    expect(p.store.read().kind).toBe("valid"); p.adapter.closureState.mockResolvedValue(closed); return result;
  });
  mounted(p); await fill(); submit(); submit();
  await screen.findByText("业务已关闭");
  expect(p.adapter.closeRequest).toHaveBeenCalledTimes(1); expect(p.store.read().kind).toBe("missing");
  expect(p.onBlocking).toHaveBeenLastCalledWith(true); expect(p.onClosed).toHaveBeenLastCalledWith(true);
  expect(p.onDetail).toHaveBeenCalledWith(p.detail);
});
it("preserves an unknown POST and manual recovery never sends it again", async () => {
  const p = props(); p.adapter.closeRequest.mockRejectedValue(new Error("连接中断"));
  p.adapter.closureCommandStatusNoReplay.mockResolvedValue({ lookup_status: "not_observed", command: null } as never);
  mounted(p); await fill(); submit(); await screen.findByText("连接中断");
  fireEvent.click(screen.getByRole("button", {name:"只读核验原关闭"}));
  await screen.findByText(/尚未查到原关闭结果/); expect(p.store.read().kind).toBe("valid");
  p.adapter.closureCommandStatusNoReplay.mockResolvedValue({lookup_status:"confirmed",command:{...result,replayed:true}}); p.adapter.closureState.mockResolvedValue(closed);
  fireEvent.click(screen.getByRole("button", {name:"只读核验原关闭"})); await screen.findByText("业务已关闭");
  expect(p.adapter.closeRequest).toHaveBeenCalledTimes(1); expect(p.store.read().kind).toBe("missing");
});
it("keeps the original after a success response if query evidence is missing", async () => {
  const p = props(); p.adapter.closureCommandStatusNoReplay.mockResolvedValue({ lookup_status: "not_observed", command: null } as never);
  mounted(p); await fill(); submit(); await screen.findByText(/尚未查到原关闭结果/);
  expect(p.store.read().kind).toBe("valid"); expect(screen.queryByText("业务已关闭")).toBeNull();
});
it("prevents submission before quantity coverage and other writers settle", async () => {
  const p = props(); p.adapter.completionQuantities.mockResolvedValue({ ...await p.adapter.completionQuantities(), pending_inbound_orders: 1 });
  mounted(p); await fill(); submit(); await screen.findByText(/需求尚不能关闭/);
  expect(p.adapter.closeRequest).not.toHaveBeenCalled(); expect(p.store.read().kind).toBe("missing");
});
it("preserves a late acknowledgement after leaving the request", async () => {
  const p = props(); let resolve!: (value: typeof result) => void;
  p.adapter.closeRequest.mockImplementation(() => new Promise(done => {resolve = done;}));
  const {view} = mounted(p); await fill(); submit(); await waitFor(() => expect(p.adapter.closeRequest).toHaveBeenCalledTimes(1));
  view.unmount(); await act(async () => resolve(result));
  expect(p.store.read().kind).toBe("valid"); expect(p.adapter.closureCommandStatusNoReplay).not.toHaveBeenCalled();
});
it("blocks submission if another write starts during preflight", async () => {
  const p = props(); let blocked = false;
  const { coverage } = await import("./materialRequestClosureTestFixtures");
  p.adapter.completionQuantities.mockImplementation(async () => {blocked = true; return coverage;});
  mounted(p, {otherWriteBlocked: () => blocked}); await fill(); submit(); await screen.findByText(/其他操作尚未完成/);
  expect(p.adapter.closeRequest).not.toHaveBeenCalled(); expect(p.store.read().kind).toBe("missing");
});
it("recovers a stored command on reopening without needing a close permission", async () => {
  const p = props(); p.store.persist(await sentinel()); p.adapter.closureState.mockResolvedValue(closed);
  mounted(p); await screen.findByText("业务已关闭");
  expect(p.store.read().kind).toBe("valid"); fireEvent.click(screen.getByRole("button", {name:"只读核验原关闭"}));
  await waitFor(() => expect(p.store.read().kind).toBe("missing")); expect(p.adapter.closeRequest).not.toHaveBeenCalled();
});
it("keeps writes blocked if the initial state cannot be verified", async () => {
  const p = props(); p.adapter.closureState.mockRejectedValue(new Error("读取失败"));
  mounted(p); await screen.findByText("读取失败"); expect(p.onBlocking).toHaveBeenLastCalledWith(true);
  p.adapter.closureState.mockResolvedValue(open); fireEvent.click(screen.getByRole("button", {name:"刷新关闭状态"}));
  await fill(); expect(p.onBlocking).toHaveBeenLastCalledWith(false);
});
