// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import FormalMaterialRequestFulfillmentPreparationPanel from "./FormalMaterialRequestFulfillmentPreparationPanel";
import { preparationPage } from "./materialRequestFulfillmentPreparationTestFixtures";
import { afterReservation, access, identity } from "./materialRequestReservationTestFixtures";

afterEach(cleanup);
function props(serial = false): any {
  return { adapter: { listFulfillmentPreparation: vi.fn().mockResolvedValue(preparationPage(serial)), loadIdentityNoReplay: vi.fn().mockResolvedValue(identity()),
    loadAccessNoReplay: vi.fn().mockResolvedValue(access()), detailNoReplay: vi.fn().mockResolvedValue(afterReservation()), createReservation: vi.fn(), createRelease: vi.fn() },
    access: access(), detail: afterReservation(), otherWriteBusy: false, otherWriteBlocked: () => false };
}
function load() { fireEvent.click(screen.getByRole("button", { name: "查看明细 1 履约准备" })); }

it.each([false, true])("shows immutable quantities and exact original SNs without writing (%s)", async serial => {
  const p = props(serial); render(<FormalMaterialRequestFulfillmentPreparationPanel {...p} />); load();
  await screen.findByRole("table", { name: "原占用履约准备" });
  expect(screen.getByText("可核对实物：2.000")).toBeTruthy();
  if (serial) { expect(screen.getByText("SN-A")).toBeTruthy(); expect(screen.getByText("SN-B")).toBeTruthy(); }
  expect(p.adapter.createReservation).not.toHaveBeenCalled(); expect(p.adapter.createRelease).not.toHaveBeenCalled();
  expect(p.adapter.loadIdentityNoReplay).toHaveBeenCalledTimes(2);
  expect(p.adapter.detailNoReplay).toHaveBeenCalledTimes(2);
});

it("displays released and blocked originals and filters without treating them as completed outbound", async () => {
  const p = props(); const page: any = preparationPage();
  page.items[0] = { ...page.items[0], preparation_status: "blocked", verified_held_qty: "0.000", blockers: ["pool_shortfall"] };
  p.adapter.listFulfillmentPreparation.mockResolvedValue(page);
  render(<FormalMaterialRequestFulfillmentPreparationPanel {...p} />); load();
  await screen.findByText("占用账户余额不足以覆盖现有占用，请核对库存账");
  expect(screen.queryByText(/可核对实物：/)).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "只看可核对实物" }));
  expect(screen.getByText(/当前没有可核对实物的占用/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "显示全部占用" }));
  expect(screen.getByText("待处理")).toBeTruthy();
  p.adapter.listFulfillmentPreparation.mockResolvedValue({ ...page, items: [{ ...page.items[0], preparation_status: "released", released_qty: "2.000", remaining_reserved_qty: "0.000", blockers: [] }] });
  fireEvent.click(screen.getByRole("button", { name: "刷新履约清单" }));
  await screen.findByText("已全部释放");
  expect(screen.queryByText("已出库")).toBeNull();
});

it.each(["version", "identity", "busy"])("discards late results after %s changes", async kind => {
  const p = props(); let resolve!: (value: unknown) => void;
  p.adapter.listFulfillmentPreparation.mockImplementation(() => new Promise(done => { resolve = done; }));
  const view = render(<FormalMaterialRequestFulfillmentPreparationPanel {...p} />); load();
  await waitFor(() => expect(p.adapter.listFulfillmentPreparation).toHaveBeenCalledTimes(1));
  const changed = kind === "version" ? { detail: { ...p.detail, request_version: p.detail.request_version + 1 } }
    : kind === "identity" ? { access: { ...p.access, authorization_version: p.access.authorization_version + 1 } } : { otherWriteBusy: true };
  view.rerender(<FormalMaterialRequestFulfillmentPreparationPanel {...p} {...changed} />);
  await act(async () => { resolve(preparationPage()); });
  expect(screen.queryByRole("table", { name: "原占用履约准备" })).toBeNull();
  expect(screen.queryByText(/可核对实物：/)).toBeNull();
});

it("rechecks permissions after the response and refuses a revoked read", async () => {
  const p = props(); p.adapter.loadAccessNoReplay.mockResolvedValueOnce(access()).mockResolvedValue({ ...access(), can_read_allocation_options: false });
  render(<FormalMaterialRequestFulfillmentPreparationPanel {...p} />); load();
  await screen.findByRole("alert"); expect(screen.queryByRole("table")).toBeNull();
});

it("clears the old successful snapshot when refresh fails", async () => {
  const p = props(); render(<FormalMaterialRequestFulfillmentPreparationPanel {...p} />); load();
  await screen.findByRole("table"); p.adapter.listFulfillmentPreparation.mockRejectedValue(new Error("network interrupted"));
  fireEvent.click(screen.getByRole("button", { name: "刷新履约清单" }));
  await screen.findByRole("alert"); expect(screen.queryByRole("table")).toBeNull();
});

it("blocks duplicate clicks and stops reading while a write result is unknown", async () => {
  const p = props(); let blocked = true; p.otherWriteBlocked = () => blocked;
  render(<FormalMaterialRequestFulfillmentPreparationPanel {...p} />); load();
  expect(p.adapter.listFulfillmentPreparation).not.toHaveBeenCalled(); blocked = false;
  load(); load(); await screen.findByRole("table"); expect(p.adapter.listFulfillmentPreparation).toHaveBeenCalledTimes(1);
});
