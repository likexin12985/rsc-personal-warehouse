// @vitest-environment jsdom
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import FormalMaterialRequestSupplyPanel from "./FormalMaterialRequestSupplyPanel";

const REQUEST_ID = "20000000-0000-4000-8000-000000000001";
const LINE_ID = "30000000-0000-4000-8000-000000000001";
const MATERIAL_ID = "40000000-0000-4000-8000-000000000001";

const page = {
  schema_version: "1.0" as const, request_id: REQUEST_ID, request_line_id: LINE_ID, request_version: 3,
  current_revision_id: LINE_ID, current_revision_no: 2, material_id: MATERIAL_ID,
  final_approved_qty: "2.000", cancelled_qty: "0.000", allocatable_qty: "2.000",
  projection_status: "ready" as const, opening_balance_status: "established" as const,
  projected_at: null, ledger_cursor: 7, items: [],
};

function detail() {
  return {
    request_id: REQUEST_ID, request_no: "MR-001", request_version: 3,
    lines: [{ request_line_id: LINE_ID, line_no: 1, material_id: MATERIAL_ID, status: "approved", final_approved_qty: "2.000", cancelled_qty: "0.000" }],
    supply_tasks: [], allowed_actions: [],
  } as any;
}

describe("material request source candidate panel", () => {
  it("reads candidates without creating a fulfilment write", async () => {
    const listAllocationOptions = vi.fn().mockResolvedValue(page);
    const mutate = vi.fn();
    const adapter = { listAllocationOptions, mutate, loadIdentity: vi.fn(), loadAccess: vi.fn(), lifecycleCommandStatus: vi.fn(), supplyCommandStatus: vi.fn(), list: vi.fn(), detail: vi.fn(), loadDraftForEdit: vi.fn(), listWorkOrderOptions: vi.fn(), workOrderOptionDetail: vi.fn(), listMaterials: vi.fn(), createDraft: vi.fn() } as any;
    const store = { read: () => ({ kind: "missing" }), persist: vi.fn(), clear: vi.fn() } as any;
    render(<FormalMaterialRequestSupplyPanel adapter={adapter} access={{ can_read_allocation_options: true } as any} detail={detail()} store={store} registry={{ get: () => undefined } as any} otherWriteBusy={false} onBlocking={vi.fn()} onDetail={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "查看可用货源" }));
    await waitFor(() => expect(listAllocationOptions).toHaveBeenCalledWith(REQUEST_ID, LINE_ID));
    expect(await screen.findByText("当前没有满足条件的可用正余额货源。")).toBeTruthy();
    expect(mutate).not.toHaveBeenCalled();
  });
});
