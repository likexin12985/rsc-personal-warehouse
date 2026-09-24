// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { loadControlBatches } from "../formalOpeningControlDirectory";
import OpeningControlDirectory from "./OpeningControlDirectory";

vi.mock("../formalOpeningControlDirectory", async (original) => ({ ...await original<typeof import("../formalOpeningControlDirectory")>(), loadControlBatches: vi.fn() }));
const id = (n: number) => `10000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
const actor = { person_id: id(1), authorization_version: 7 };
const item = { publicationId: id(3), sourceId: id(4), sourceName: "控制来源",
  capturedAt: "2026-09-20T01:00:00Z", publishedAt: "2026-09-20T01:01:00Z", validUntil: "2026-09-20T01:45:00Z",
  recordCount: 0, isLatest: true };
afterEach(() => { cleanup(); vi.mocked(loadControlBatches).mockReset(); });
it("shows the batch separately from starting and clears it before refresh failure", async () => {
  vi.mocked(loadControlBatches).mockResolvedValueOnce({ items: [item], nextAfterId: null }).mockRejectedValueOnce(new Error("network"));
  render(<OpeningControlDirectory actor={actor} regionId={id(2)} />);
  const field = screen.getByRole("combobox") as HTMLSelectElement;
  await waitFor(() => expect(field.disabled).toBe(false));
  fireEvent.change(field, { target: { value: id(3) } });
  expect(screen.getByText("0 条（不是库存数量）")).toBeTruthy();
  expect(screen.getByText(/当前不能启动盘点/)).toBeTruthy();
  expect(screen.queryByRole("button", { name: /启动|创建/ })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "刷新控制批次" }));
  expect(screen.queryByText("0 条（不是库存数量）")).toBeNull();
  await screen.findByRole("alert");
  expect(screen.queryAllByRole("option")).toHaveLength(1);
});
it.each(["region", "actor"])("discards an old request when %s changes", async (change) => {
  let resolve!: (value: any) => void;
  vi.mocked(loadControlBatches).mockReturnValueOnce(new Promise((yes) => { resolve = yes; }))
    .mockResolvedValueOnce({ items: [], nextAfterId: null });
  const { rerender } = render(<OpeningControlDirectory actor={actor} regionId={id(2)} />);
  rerender(<OpeningControlDirectory actor={change === "actor" ? { ...actor, authorization_version: 8 } : actor}
    regionId={change === "region" ? id(5) : id(2)} />);
  await screen.findByText(/本区域暂无已发布控制批次/);
  await act(async () => { resolve({ items: [item], nextAfterId: null }); });
  expect(screen.queryAllByRole("option")).toHaveLength(1);
  expect(screen.queryByText(/控制来源/)).toBeNull();
});
