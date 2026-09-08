// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import FormalMaterialRequestReleasePanel from "./FormalMaterialRequestReleasePanel";
import { createReleaseStore } from "./materialRequestReservationRelease";
import { releasePage, releaseResult, afterRelease } from "./materialRequestReleaseTestFixtures";
import { afterReservation, access, identity } from "./materialRequestReservationTestFixtures";

afterEach(() => { cleanup(); localStorage.clear(); });
function props(serial = false): any {
  return { adapter: { listReleaseOptions: vi.fn().mockResolvedValue(releasePage(serial)), loadIdentityNoReplay: vi.fn().mockResolvedValue(identity()), loadAccessNoReplay: vi.fn().mockResolvedValue(access()),
    detailNoReplay: vi.fn().mockResolvedValueOnce(afterReservation()).mockResolvedValue(afterRelease()), createRelease: vi.fn().mockResolvedValue(releaseResult(serial)),
    releaseCommandStatusNoReplay: vi.fn().mockResolvedValue({ ...releaseResult(serial), idempotency_replayed: true }) },
    access: access(), detail: afterReservation(), store: createReleaseStore(), otherWriteBusy: false, otherWriteBlocked: () => false, onBlocking: vi.fn(), onDetail: vi.fn() };
}
async function select() {
  fireEvent.click(screen.getByRole("button", { name: "查看明细 1 可释放占用" }));
  fireEvent.click(await screen.findByRole("button", { name: "选择释放" }));
  fireEvent.change(screen.getByRole("textbox", { name: "释放原因" }), { target: { value: "未使用退回" } });
}
it("shows the original reservation, persists intent before one POST, and confirms by readback", async () => {
  const p = props(); p.adapter.createRelease.mockImplementation(async (_id: string, body: unknown) => {
    const stored = p.store.read(); expect(stored.kind).toBe("valid"); expect(stored.value.input).toEqual(body); return releaseResult();
  });
  render(<FormalMaterialRequestReleasePanel {...p} />); await select();
  expect(p.adapter.createRelease).not.toHaveBeenCalled(); fireEvent.click(screen.getByRole("button", { name: "确认释放" }));
  await waitFor(() => expect(p.onDetail).toHaveBeenCalledWith(afterRelease()));
  expect(p.adapter.createRelease).toHaveBeenCalledTimes(1); expect(p.store.read().kind).toBe("missing");
});
it("selects exact original SNs and rejects an incomplete selection", async () => {
  const p = props(true); render(<FormalMaterialRequestReleasePanel {...p} />); await select();
  fireEvent.click(screen.getByRole("button", { name: "确认释放" }));
  await waitFor(() => expect(screen.getAllByText(/请选择与释放数量一致/).length).toBeGreaterThan(0));
  expect(p.adapter.createRelease).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("checkbox", { name: "释放 SN SN-A" })); fireEvent.click(screen.getByRole("checkbox", { name: "释放 SN SN-B" }));
  fireEvent.click(screen.getByRole("button", { name: "确认释放" }));
  await waitFor(() => expect(p.onDetail).toHaveBeenCalled()); expect(p.adapter.createRelease).toHaveBeenCalledTimes(1);
});
it("never repeats a POST after an ambiguous network result", async () => {
  const p = props(); p.adapter.createRelease.mockRejectedValue(new Error("network interrupted"));
  render(<FormalMaterialRequestReleasePanel {...p} />); await select(); fireEvent.click(screen.getByRole("button", { name: "确认释放" }));
  await waitFor(() => expect(p.store.read().kind).toBe("valid"));
  const recover = await screen.findByRole("button", { name: "核验原释放操作" });
  await waitFor(() => expect((recover as HTMLButtonElement).disabled).toBe(false)); fireEvent.click(recover);
  await waitFor(() => expect(p.store.read().kind).toBe("missing")); expect(p.adapter.createRelease).toHaveBeenCalledTimes(1);
});
it("stops before POST when another write blocks during fresh preflight", async () => {
  const p = props(); let blocked = false; p.otherWriteBlocked = () => blocked;
  p.adapter.loadAccessNoReplay.mockImplementation(async () => { blocked = true; return access(); });
  render(<FormalMaterialRequestReleasePanel {...p} />); await select(); fireEvent.click(screen.getByRole("button", { name: "确认释放" }));
  await waitFor(() => expect(screen.getAllByText(/其他写入正在进行/).length).toBeGreaterThan(0));
  expect(p.adapter.createRelease).not.toHaveBeenCalled(); expect(p.store.read().kind).toBe("missing");
});
it("does not POST if saving the recovery trace fails", async () => {
  const p = props(); p.store = { read: () => ({ kind: "missing" }), persist() { throw new Error("disk unavailable"); }, clear: vi.fn() };
  render(<FormalMaterialRequestReleasePanel {...p} />); await select(); fireEvent.click(screen.getByRole("button", { name: "确认释放" }));
  await waitFor(() => expect(screen.getAllByText(/disk unavailable/).length).toBeGreaterThan(0)); expect(p.adapter.createRelease).not.toHaveBeenCalled();
});

it("keeps the original trace when the same request advances before a late POST response", async () => {
  const p = props(); let resolve!: (value: unknown) => void;
  p.adapter.createRelease.mockImplementation(() => new Promise(done => { resolve = done; }));
  const view = render(<FormalMaterialRequestReleasePanel {...p} />);
  await select(); fireEvent.click(screen.getByRole("button", { name: "确认释放" }));
  await waitFor(() => expect(p.adapter.createRelease).toHaveBeenCalledTimes(1));
  const saved = p.store.read();
  view.rerender(<FormalMaterialRequestReleasePanel {...p} detail={{ ...afterRelease(), request_version: 6 }} />);
  await act(async () => { resolve(releaseResult()); });
  expect(p.onDetail).not.toHaveBeenCalled();
  expect(p.store.read()).toEqual(saved);
  expect(p.adapter.releaseCommandStatusNoReplay).not.toHaveBeenCalled();
  expect(p.adapter.createRelease).toHaveBeenCalledTimes(1);
});
