// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  approveOpeningReconciliation,
  explainOpeningReconciliation,
  loadOpeningReconciliationDetail,
  loadOpeningReconciliations,
  startOpeningReconciliation,
} from "../formalOpeningReconciliation";
import FormalOpeningReconciliationsPage from "./FormalOpeningReconciliations";


vi.mock("../formalOpeningReconciliation", async (loadOriginal) => {
  const original = await loadOriginal<typeof import("../formalOpeningReconciliation")>();
  return {
    ...original,
    loadOpeningReconciliations: vi.fn(),
    loadOpeningReconciliationDetail: vi.fn(),
    startOpeningReconciliation: vi.fn(),
    explainOpeningReconciliation: vi.fn(),
    approveOpeningReconciliation: vi.fn(),
  };
});

const RUN_ID = "10000000-0000-4000-8000-000000000001";
const TASK_ID = "20000000-0000-4000-8000-000000000002";
const ITEM_ID = "70000000-0000-4000-8000-000000000007";
const FILE_ID = "b0000000-0000-4000-8000-00000000000b";

function detail(allowedActions: string[] = ["explain"]) {
  return {
    schema_version: "1.0",
    reconciliation_run_id: RUN_ID,
    task_id: TASK_ID,
    task_no: "OPENING-JS-2026",
    region_org_id: "30000000-0000-4000-8000-000000000003",
    status: "differences",
    version: 0,
    item_count: 1,
    explained_item_count: 0,
    resolved_item_count: 0,
    external_snapshot_at: "2026-08-31T08:00:00Z",
    local_ledger_cursor: "23",
    created_at: "2026-08-31T09:00:00Z",
    approved_at: null,
    allowed_actions: allowedActions,
    source_system_id: "40000000-0000-4000-8000-000000000004",
    round_id: "50000000-0000-4000-8000-000000000005",
    posting_id: "60000000-0000-4000-8000-000000000006",
    difference_manifest_sha256: "a".repeat(64),
    approval_comment: "",
    items: [{
      reconciliation_item_id: ITEM_ID,
      stocktake_difference_id: "80000000-0000-4000-8000-000000000008",
      control_snapshot_line_id: "90000000-0000-4000-8000-000000000009",
      business_key: "OAM-JS-SKU-001",
      material_id: "a0000000-0000-4000-8000-00000000000a",
      external_qty: "5.000",
      local_qty: "3.000",
      difference: "2.000",
      status: "difference",
      version: 0,
      explanation: "",
      evidence_reference: "",
      evidence_file_id: null,
      explained_at: null,
    }],
  } as any;
}

function explainedDetail() {
  const source = detail(["approve"]);
  return {
    ...source,
    version: 1,
    explained_item_count: 1,
    items: [{
      ...source.items[0],
      status: "explained",
      version: 1,
      explanation: "省仓交接单数量尚未在控制账完成归属",
      evidence_reference: "EVIDENCE-JS-2026-001",
      evidence_file_id: FILE_ID,
      explained_at: "2026-08-31T09:30:00Z",
    }],
  } as any;
}

function approvedDetail() {
  const source = explainedDetail();
  return {
    ...source,
    status: "approved",
    version: 2,
    explained_item_count: 0,
    resolved_item_count: 1,
    approved_at: "2026-08-31T10:00:00Z",
    allowed_actions: [],
    approval_comment: "总部复核证据完整，同意解决控制账差异",
    items: [{ ...source.items[0], status: "resolved", version: 2 }],
  } as any;
}

function listPage() {
  const source = detail();
  return {
    schema_version: "1.0",
    items: [{
      reconciliation_run_id: source.reconciliation_run_id,
      task_id: source.task_id,
      task_no: source.task_no,
      region_org_id: source.region_org_id,
      status: source.status,
      version: source.version,
      item_count: source.item_count,
      explained_item_count: source.explained_item_count,
      resolved_item_count: source.resolved_item_count,
      external_snapshot_at: source.external_snapshot_at,
      local_ledger_cursor: source.local_ledger_cursor,
      created_at: source.created_at,
      approved_at: source.approved_at,
      allowed_actions: source.allowed_actions,
    }],
    next_after_id: null,
  } as any;
}

async function renderAndOpen(source = detail(), canCreate = false): Promise<void> {
  vi.mocked(loadOpeningReconciliations).mockResolvedValue(listPage());
  vi.mocked(loadOpeningReconciliationDetail).mockResolvedValue(source);
  render(<FormalOpeningReconciliationsPage canCreate={canCreate} />);
  fireEvent.click(await screen.findByRole("button", { name: "查看" }));
  expect(await screen.findByLabelText("对账详情")).toBeTruthy();
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("formal opening reconciliation PC page", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(loadOpeningReconciliations).mockResolvedValue(listPage());
  });

  it("shows independent left/right facts and only the server-allowed action", async () => {
    await renderAndOpen(detail(["explain"]));

    const section = screen.getByLabelText("对账详情");
    expect(within(section).getByText("OAM-JS-SKU-001")).toBeTruthy();
    expect(within(section).getByText("5.000")).toBeTruthy();
    expect(within(section).getByText("3.000")).toBeTruthy();
    expect(within(section).getByText("2.000")).toBeTruthy();
    expect(within(section).getByRole("button", { name: "逐项解释差异" })).toBeTruthy();
    expect(within(section).queryByRole("button", { name: "总部批准对账" })).toBeNull();
    expect(within(section).queryByRole("button", { name: /关闭/ })).toBeNull();
    expect(screen.queryByLabelText("期初任务标识")).toBeNull();
  });

  it("submits one task-wide explanation batch with a reason and evidence for every item", async () => {
    const after = explainedDetail();
    vi.mocked(explainOpeningReconciliation).mockResolvedValue({
      before: detail(),
      result: { explained_item_count: 1 } as any,
      detail: after,
    });
    await renderAndOpen(detail(["explain"]));

    fireEvent.click(screen.getByRole("button", { name: "逐项解释差异" }));
    fireEvent.change(screen.getByLabelText("OAM-JS-SKU-001 差异原因"), {
      target: { value: "省仓交接单数量尚未在控制账完成归属" },
    });
    fireEvent.change(screen.getByLabelText("OAM-JS-SKU-001 证据引用"), {
      target: { value: "EVIDENCE-JS-2026-001" },
    });
    fireEvent.change(screen.getByLabelText("OAM-JS-SKU-001 证据文件标识"), {
      target: { value: FILE_ID },
    });
    fireEvent.click(screen.getByRole("button", { name: "提交全部解释" }));

    await waitFor(() => expect(explainOpeningReconciliation).toHaveBeenCalledWith(
      RUN_ID,
      [{
        reconciliation_item_id: ITEM_ID,
        explanation: "省仓交接单数量尚未在控制账完成归属",
        evidence_reference: "EVIDENCE-JS-2026-001",
        evidence_file_id: FILE_ID,
      }],
    ));
    expect(await screen.findByText(/总部批准与期初关闭仍为独立动作/)).toBeTruthy();
  });

  it("shows approval only when returned by allowed_actions and never auto-closes", async () => {
    vi.mocked(approveOpeningReconciliation).mockResolvedValue({
      before: explainedDetail(),
      result: { resolved_item_count: 1 } as any,
      detail: approvedDetail(),
    });
    await renderAndOpen(explainedDetail());

    expect(screen.queryByRole("button", { name: "逐项解释差异" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "总部批准对账" }));
    fireEvent.change(screen.getByLabelText("批准说明"), {
      target: { value: "总部复核证据完整，同意解决控制账差异" },
    });
    fireEvent.click(screen.getByRole("button", { name: "确认总部批准" }));

    await waitFor(() => expect(approveOpeningReconciliation).toHaveBeenCalledWith(
      RUN_ID,
      "总部复核证据完整，同意解决控制账差异",
    ));
    expect(await screen.findByText(/期初任务不会在本页自动关闭/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /独立关闭/ })).toBeNull();
    expect(screen.getByText("已完整解释")).toBeTruthy();
  });

  it("prefills preserved evidence when the server permits re-explanation", async () => {
    await renderAndOpen({ ...explainedDetail(), allowed_actions: ["explain", "approve"] });

    fireEvent.click(screen.getByRole("button", { name: "逐项解释差异" }));

    expect((screen.getByLabelText("OAM-JS-SKU-001 差异原因") as HTMLTextAreaElement).value)
      .toBe("省仓交接单数量尚未在控制账完成归属");
    expect((screen.getByLabelText("OAM-JS-SKU-001 证据引用") as HTMLInputElement).value)
      .toBe("EVIDENCE-JS-2026-001");
    expect((screen.getByLabelText("OAM-JS-SKU-001 证据文件标识") as HTMLInputElement).value)
      .toBe(FILE_ID);
  });

  it("creates only through the explicit task endpoint when create permission is present", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    vi.mocked(startOpeningReconciliation).mockResolvedValue({
      before: { task_id: TASK_ID, task_version: 9 } as any,
      result: { item_count: 1 } as any,
      detail: detail(),
    });
    render(<FormalOpeningReconciliationsPage canCreate />);

    fireEvent.change(await screen.findByLabelText("期初任务标识"), {
      target: { value: TASK_ID },
    });
    fireEvent.click(screen.getByRole("button", { name: "创建独立对账" }));

    await waitFor(() => expect(startOpeningReconciliation).toHaveBeenCalledWith(TASK_ID));
    expect(await screen.findByText(/未执行期初关闭/)).toBeTruthy();
  });

  it("does not manufacture actions when the detail returns none", async () => {
    await renderAndOpen(detail([]));

    const section = screen.getByLabelText("对账详情");
    expect(within(section).getByText("当前没有服务端授权操作")).toBeTruthy();
    expect(within(section).queryByRole("button", { name: "逐项解释差异" })).toBeNull();
    expect(within(section).queryByRole("button", { name: "总部批准对账" })).toBeNull();
  });
});
