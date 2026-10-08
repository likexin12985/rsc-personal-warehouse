// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import FormalMaterialRequestCompletionPanel from "./FormalMaterialRequestCompletionPanel";
import { afterOutbound } from "./materialRequestOutboundTestFixtures";
import { access as fixtureAccess } from "./materialRequestReservationTestFixtures";
import type { MaterialRequestDetail } from "./formalMaterialRequests";
import type { FormalMaterialRequestAdapter } from "./formalMaterialRequestAdapter";

afterEach(cleanup);
const access = () => ({ ...fixtureAccess(), schema_version: "1.0" as const });
function response(complete = false) {
  const detail: MaterialRequestDetail = afterOutbound();
  return { schema_version: "1.0", request_id: detail.request_id, request_version: detail.request_version,
    revision_id: detail.current_revision_id, assessment: "final_approved_quantity_coverage",
    quantity_coverage_complete: complete, pending_inbound_orders: 0,
    lines: detail.lines.map(line => ({ request_line_id: line.request_line_id,
      approved_qty: line.final_approved_qty, cancelled_qty: "0.000",
      posted_qty: complete ? line.final_approved_qty : "0.000", remaining_qty: complete ? "0.000" : line.final_approved_qty })) };
}
it("shows exact coverage without presenting a closing action", async () => {
  const method = vi.fn(async () => response(true));
  render(<FormalMaterialRequestCompletionPanel adapter={{ completionQuantities: method } as unknown as FormalMaterialRequestAdapter}
    access={access()} detail={afterOutbound()} />);
  expect(await screen.findByText("批准数量已全部入账或取消，尚未完成结单。")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "关闭业务单" })).toBeNull();
  expect(method).toHaveBeenCalledTimes(1);
});
it("clears previous evidence on a failed manual refresh", async () => {
  const method = vi.fn().mockResolvedValueOnce(response(true)).mockRejectedValueOnce(new Error("核验失败"));
  render(<FormalMaterialRequestCompletionPanel adapter={{ completionQuantities: method } as unknown as FormalMaterialRequestAdapter}
    access={access()} detail={afterOutbound()} />);
  await screen.findByText("批准数量已全部入账或取消，尚未完成结单。");
  fireEvent.click(screen.getByRole("button", { name: "重新核对" }));
  expect((await screen.findByRole("alert")).textContent).toContain("本次核对未完成");
  expect(screen.queryByText("批准数量已全部入账或取消，尚未完成结单。")).toBeNull();
});
it("does not display a late response for an older request version", async () => {
  let resolve!: (value: unknown) => void;
  const method = vi.fn().mockImplementationOnce(() => new Promise(done => { resolve = done; }))
    .mockRejectedValueOnce(new Error("请重新读取"));
  const adapter = { completionQuantities: method } as unknown as FormalMaterialRequestAdapter;
  const view = render(<FormalMaterialRequestCompletionPanel adapter={adapter} access={access()} detail={afterOutbound()} />);
  const detail = afterOutbound(); detail.request_version++;
  view.rerender(<FormalMaterialRequestCompletionPanel adapter={adapter} access={access()} detail={detail} />);
  await screen.findByRole("alert");
  resolve(response(true));
  await waitFor(() => expect(method).toHaveBeenCalledTimes(2));
  expect(screen.queryByText("批准数量已全部入账或取消，尚未完成结单。")).toBeNull();
});

function remainderFixture() {
  const value = response();
  return { schema_version: "1.0", request_id: value.request_id, request_version: value.request_version,
    revision_id: value.revision_id, assessment: "remaining_fulfillment_quantities", open_supply_tasks: 2,
    pending_substitutions: 1, lines: value.lines.map(line => ({ request_line_id: line.request_line_id,
      approved_qty: line.approved_qty, cancelled_qty: line.cancelled_qty, posted_qty: line.posted_qty,
      unreserved_qty: "0.000", reserved_unpicked_qty: "0.000", picked_unoutbound_qty: "0.000",
      outbound_unshipped_qty: line.remaining_qty, shipped_unreceived_qty: "0.000", accepted_unposted_qty: "0.000",
      rejected_unsettled_qty: "0.000" })) };
}
it("shows remaining stages and unresolved tasks inside existing coverage cards", async () => {
  render(<FormalMaterialRequestCompletionPanel adapter={{ completionQuantities: vi.fn(async () => response()),
    remainingFulfillment: vi.fn(async () => remainderFixture()) } as unknown as FormalMaterialRequestAdapter}
    access={access()} detail={afterOutbound()} />);
  expect(await screen.findByText(/未结束补货任务：2/)).toBeTruthy();
  expect(screen.getAllByText("已出库待交运").length).toBeGreaterThan(0);
  expect(screen.queryByRole("button", { name: /取消/ })).toBeNull();
});
it("discards both reads if a stage result contradicts posted coverage", async () => {
  const remaining = remainderFixture();
  remaining.lines[0].posted_qty = remaining.lines[0].approved_qty;
  remaining.lines[0].outbound_unshipped_qty = "0.000";
  render(<FormalMaterialRequestCompletionPanel adapter={{ completionQuantities: vi.fn(async () => response()),
    remainingFulfillment: vi.fn(async () => remaining) } as unknown as FormalMaterialRequestAdapter}
    access={access()} detail={afterOutbound()} />);
  expect((await screen.findByRole("alert")).textContent).toContain("核对期间履约数量已变化");
  expect(screen.queryByText("已出库待交运")).toBeNull();
});

it("shows warehouse-return compensation separately from personal inbound without closing automatically", async () => {
  const original: MaterialRequestDetail = afterOutbound();
  const detail: MaterialRequestDetail = { ...original,
    lines: original.lines.map(line => ({ ...line, cancelled_qty: line.final_approved_qty })) };
  const coverage = response(true);
  coverage.lines = coverage.lines.map(line => ({ ...line, posted_qty: "0.000", cancelled_qty: line.approved_qty }));
  const old = remainderFixture();
  const returned = { ...old, schema_version: "2.0", lines: old.lines.map(line => ({ ...line,
    cancelled_qty: line.approved_qty, outbound_unshipped_qty: "0.000", unfulfilled_cancelled_qty: "0.000",
    return_compensated_qty: line.approved_qty, returned_pending_compensation_qty: "0.000" })) };
  render(<FormalMaterialRequestCompletionPanel adapter={{ completionQuantities: vi.fn(async () => coverage),
    remainingFulfillment: vi.fn(async () => returned) } as unknown as FormalMaterialRequestAdapter}
    access={access()} detail={detail} />);
  expect(await screen.findByText("批准数量已全部入账或取消，尚未完成结单。")).toBeTruthy();
  expect(screen.getAllByText("个人仓已入账").length).toBeGreaterThan(0);
  expect(screen.getAllByText("已退回入账待补偿").length).toBeGreaterThan(0);
  expect(screen.getAllByText("退回后已补偿取消").length).toBeGreaterThan(0);
  expect(screen.queryByRole("button", { name: "关闭业务单" })).toBeNull();
});
