// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { api, apiNoReplay, ApiError } from "../api";
import { newInventoryReportKey, saveInventoryReportKey } from "../inventoryReportClient";
import InventoryReportPanel from "./InventoryReportPanel";

vi.mock("../api", async original => ({
  ...await original<typeof import("../api")>(), api: vi.fn(), apiNoReplay: vi.fn(),
}));

const personId = "10000000-0000-4000-8000-000000000001";

beforeEach(() => {
  sessionStorage.clear();
  vi.mocked(api).mockReset();
  vi.mocked(apiNoReplay).mockReset();
});
afterEach(cleanup);

it("does not issue another export POST after an unknown result", async () => {
  vi.mocked(api).mockImplementation(async path => {
    if (path.endsWith("/capabilities")) return { available: true };
    if (path.endsWith("/recovery")) throw new ApiError(404, "未找到任务");
    throw new Error(`unexpected GET: ${path}`);
  });
  vi.mocked(apiNoReplay).mockRejectedValue(new Error("connection lost"));
  render(<InventoryReportPanel personId={personId} authorizationVersion={1} />);
  fireEvent.click(await screen.findByRole("button", { name: "申请导出" }));
  expect(await screen.findByRole("alert")).toHaveProperty("textContent", expect.stringContaining("申请结果未确认"));
  expect(apiNoReplay).toHaveBeenCalledTimes(1);
  expect(sessionStorage.length).toBe(1);
  fireEvent.click(screen.getByRole("button", { name: "按原申请找回" }));
  await waitFor(() => expect(vi.mocked(api).mock.calls.filter(([path]) => path.endsWith("/recovery"))).toHaveLength(2));
  expect(apiNoReplay).toHaveBeenCalledTimes(1);
});

it("does not offer an export action while the server capability is disabled", async () => {
  vi.mocked(api).mockResolvedValue({ available: false });
  render(<InventoryReportPanel personId={personId} authorizationVersion={1} />);
  await waitFor(() => expect(api).toHaveBeenCalledTimes(1));
  expect(screen.queryByRole("button", { name: "申请导出" })).toBeNull();
  expect(apiNoReplay).not.toHaveBeenCalled();
});

it("recovers the saved request after a page remount without a new POST", async () => {
  const key = newInventoryReportKey();
  saveInventoryReportKey(personId, 1, key);
  const jobId = "20000000-0000-4000-8000-000000000001";
  vi.mocked(api).mockImplementation(async path => path.endsWith("/capabilities")
    ? { available: true }
    : { job_id: jobId, status: "queued", created_at: "2026-09-24T00:00:00Z",
        completed_at: null, download_count: 0, file_available: false });
  render(<InventoryReportPanel personId={personId} authorizationVersion={1} />);
  expect(await screen.findByText(/排队中/)).toBeTruthy();
  expect(vi.mocked(api).mock.calls.some(([path, init]) => path.endsWith("/recovery")
    && new Headers(init?.headers).get("Idempotency-Key") === key)).toBe(true);
  expect(apiNoReplay).not.toHaveBeenCalled();
});
