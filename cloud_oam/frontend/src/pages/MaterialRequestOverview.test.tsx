// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import * as client from "../materialRequestOverviewClient";
import example from "../test-fixtures/overview-approved.json";
import MaterialRequestOverview from "./MaterialRequestOverview";

vi.mock("../materialRequestOverviewClient", async original => ({
  ...await original<typeof import("../materialRequestOverviewClient")>(), readMaterialRequestOverview: vi.fn(),
}));
beforeEach(() => { vi.mocked(client.readMaterialRequestOverview).mockReset(); });
afterEach(cleanup);

it("renders approval and inbound counts separately", async () => {
  vi.mocked(client.readMaterialRequestOverview).mockResolvedValue(client.parseMaterialRequestOverview(example));
  render(<MaterialRequestOverview />);
  await screen.findByText("1 单申请");
  expect(within(screen.getByRole("region", { name: "星星总部三级审批" })).getByText("1 单")).toBeTruthy();
  const inbound = screen.getByRole("region", { name: "个人仓入库" });
  expect(within(inbound).getByText("已入账").nextElementSibling?.textContent).toBe("0 单");
});

it("never renders a failed read as zero and clears previous data", async () => {
  vi.mocked(client.readMaterialRequestOverview).mockResolvedValueOnce(client.parseMaterialRequestOverview(example))
    .mockRejectedValueOnce(new Error("读取失败"));
  render(<MaterialRequestOverview />);
  await screen.findByText("1 单申请");
  fireEvent.click(screen.getByRole("button", { name: "查询" }));
  await screen.findByRole("alert");
  expect(screen.queryByText("1 单申请")).toBeNull();
  expect(screen.queryByText("0 单申请")).toBeNull();
});

it("queries the selected inclusive dates as a Beijing half-open interval", async () => {
  vi.mocked(client.readMaterialRequestOverview).mockResolvedValue(client.parseMaterialRequestOverview(example));
  render(<MaterialRequestOverview />);
  await screen.findByText("1 单申请");
  fireEvent.change(screen.getByLabelText("创建开始日期"), { target: { value: "2026-10-01" } });
  fireEvent.change(screen.getByLabelText("创建结束日期"), { target: { value: "2026-10-01" } });
  fireEvent.click(screen.getByRole("button", { name: "查询" }));
  await waitFor(() => expect(client.readMaterialRequestOverview).toHaveBeenLastCalledWith({
    created_from: "2026-09-30T16:00:00.000Z", created_before: "2026-10-01T16:00:00.000Z",
  }, expect.any(AbortSignal)));
});

it("ignores a late response after the identity-keyed page is unmounted", async () => {
  let resolve!: (value: client.MaterialRequestOverview) => void;
  vi.mocked(client.readMaterialRequestOverview).mockReturnValue(new Promise(done => { resolve = done; }));
  const view = render(<MaterialRequestOverview />);
  const signal = vi.mocked(client.readMaterialRequestOverview).mock.calls[0][1]!;
  view.unmount();
  expect(signal.aborted).toBe(true);
  resolve(client.parseMaterialRequestOverview(example));
  expect(screen.queryByText("1 单申请")).toBeNull();
});
