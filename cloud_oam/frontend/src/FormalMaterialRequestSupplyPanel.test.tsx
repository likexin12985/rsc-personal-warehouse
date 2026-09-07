// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import FormalMaterialRequestSupplyPanel from "./FormalMaterialRequestSupplyPanel";

const REQUEST_ID = "20000000-0000-4000-8000-000000000001";
const LINE_ID = "30000000-0000-4000-8000-000000000001";
const MATERIAL_ID = "40000000-0000-4000-8000-000000000001";
const PERSON_ID = "50000000-0000-4000-8000-000000000001";
const ORG_ID = "60000000-0000-4000-8000-000000000001";
const REVISION_ID = "70000000-0000-4000-8000-000000000001";
const INSTANCE_ID = "80000000-0000-4000-8000-000000000001";
const STEP_1_ID = "81000000-0000-4000-8000-000000000001";
const STEP_2_ID = "81000000-0000-4000-8000-000000000002";
const STEP_3_ID = "81000000-0000-4000-8000-000000000003";
const DECISION_1_ID = "82000000-0000-4000-8000-000000000001";
const DECISION_2_ID = "82000000-0000-4000-8000-000000000002";
const DECISION_3_ID = "82000000-0000-4000-8000-000000000003";
const REGISTRATION_ID = "83000000-0000-4000-8000-000000000001";
const EVIDENCE_FILE_ID = "84000000-0000-4000-8000-000000000001";
const STOCK_ACCOUNT_ID = "90000000-0000-4000-8000-000000000001";
const LOCATION_ID = "90000000-0000-4000-8000-000000000002";
const ALLOCATION_ID = "90000000-0000-4000-8000-000000000003";

afterEach(cleanup);

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
    states: axes(),
    lines: [{ request_line_id: LINE_ID, line_no: 1, material_id: MATERIAL_ID, status: "approved", final_approved_qty: "2.000", cancelled_qty: "0.000" }],
    supply_tasks: [], allowed_actions: [],
  } as any;
}

function axes(allocation_status = "not_allocated") {
  return {
    request_status: "approved",
    allocation_status,
    reservation_status: "not_reserved",
    outbound_status: "not_started",
    shipment_status: "not_started",
    logistics_signature_status: "not_signed",
    oam_receipt_status: "not_occurred",
    personal_inbound_status: "not_started",
    notification_status: "not_started",
    reconciliation_status: "not_started",
  };
}

function decision(stepId: string, decisionId: string, source: "internal" | "external_registration" = "internal") {
  return {
    decision_id: decisionId,
    step_id: stepId,
    request_revision_id: REVISION_ID,
    revision_no: 1,
    request_line_id: LINE_ID,
    input_qty: "2.000",
    approved_qty: "2.000",
    rejected_qty: "0.000",
    reason: "",
    decision_source: source,
    external_registration_id: source === "external_registration" ? REGISTRATION_ID : null,
    decided_at: "2026-09-01T08:30:00+08:00",
  };
}

function approvedDetail(requestVersion = 3, allocationStatus = "not_allocated"): any {
  const step1 = {
    step_id: STEP_1_ID, step_no: 1, attempt_no: 1,
    predecessor_step_id: null, supersedes_step_id: null, reopened_from_step_id: null,
    source_mode: "internal", status: "approved",
    assignee_snapshot: { name_masked: "区＊＊＊＊", role_code: "provincial_manager" },
    candidate_pool_summary: null, opened_at: "2026-09-01T08:10:00+08:00",
    decided_at: "2026-09-01T08:30:00+08:00", version: 1,
    line_decisions: [decision(STEP_1_ID, DECISION_1_ID)],
  };
  const step2 = {
    step_id: STEP_2_ID, step_no: 2, attempt_no: 1,
    predecessor_step_id: STEP_1_ID, supersedes_step_id: null, reopened_from_step_id: null,
    source_mode: "internal", status: "approved",
    assignee_snapshot: { name_masked: "总＊＊＊＊", role_code: "admin" },
    candidate_pool_summary: null, opened_at: "2026-09-01T08:10:00+08:00",
    decided_at: "2026-09-01T08:30:00+08:00", version: 1,
    line_decisions: [decision(STEP_2_ID, DECISION_2_ID)],
  };
  const step3 = {
    step_id: STEP_3_ID, step_no: 3, attempt_no: 1,
    predecessor_step_id: STEP_2_ID, supersedes_step_id: null, reopened_from_step_id: null,
    source_mode: "external_registration", status: "approved",
    assignee_snapshot: null,
    candidate_pool_summary: { candidate_count: 2, candidate_kinds: ["registrar", "verifier"] },
    opened_at: "2026-09-01T08:10:00+08:00", decided_at: "2026-09-01T08:30:00+08:00", version: 1,
    line_decisions: [decision(STEP_3_ID, DECISION_3_ID, "external_registration")],
  };
  const instance = {
    instance_id: INSTANCE_ID, request_revision_id: REVISION_ID, revision_no: 1, attempt_no: 1,
    status: "completed", current_step_no: null, current_step_id: null, version: 3,
    steps: [step1, step2, step3],
    external_evidence_summaries: [{
      registration_id: REGISTRATION_ID, registration_no: "EXT-001", step_id: STEP_3_ID,
      external_action: "approve", status: "accepted", evidence_file_id: EVIDENCE_FILE_ID,
      external_approver_name_masked: "星＊＊＊＊", external_decided_at: "2026-09-01T08:20:00+08:00",
      registered_at: "2026-09-01T08:25:00+08:00", verified_at: "2026-09-01T08:30:00+08:00", version: 1,
    }],
    return_line_facts: [],
  };
  return {
    schema_version: "1.0", request_id: REQUEST_ID, request_no: "MR-001", request_version: requestVersion,
    current_revision_id: REVISION_ID, current_revision_no: 1, work_order_id: null,
    requester_person_id: PERSON_ID, requester_org_id: ORG_ID, purpose: "现场故障处理", urgency: "normal",
    expected_date: null,
    address_snapshot: { province_code: "320000", province_name: "江苏省", city_name: "南京市", district_name: "建邺区", detail_masked: "******" },
    contact_masked: { name_masked: "李*", mobile_masked: "*******0000" }, note: "",
    attachment_refs: [], approval_mode: "external_registration", states: axes(allocationStatus),
    approval_instance: instance, lines: [{
      request_line_id: LINE_ID, revision_id: REVISION_ID, revision_no: 1, line_no: 1, material_id: MATERIAL_ID,
      requested_qty: "2.000", required_date: null, suggested_substitute_material_id: null, note: "",
      final_approved_qty: "2.000", cancelled_qty: "0.000", status: "approved", version: requestVersion,
    }],
    revision_history: [{ revision_id: REVISION_ID, revision_no: 1, previous_revision_id: null, status: "sealed", line_count: 1, attachment_count: 0, sealed_at: "2026-09-01T08:30:00+08:00", created_at: "2026-09-01T08:00:00+08:00" }],
    approval_history: [instance], supply_tasks: [], allowed_actions: ["create_supply_task"],
    created_at: "2026-09-01T08:00:00+08:00", updated_at: "2026-09-01T08:30:00+08:00", submitted_at: "2026-09-01T08:10:00+08:00",
  };
}

function access() {
  return {
    schema_version: "1.0", person_id: PERSON_ID, authorization_version: 7,
    can_read: true, can_create: false, can_withdraw: false, can_cancel: false,
    can_read_material_catalog: true, can_read_allocation_options: true,
    can_approve_region: false, can_approve_headquarters: false,
    can_register_external: false, can_verify_external: false,
  };
}

function identity() {
  return { schema_version: "1.0", person_id: PERSON_ID, authorization_version: 7 };
}

function optionPage(quantity = "1.500") {
  return {
    schema_version: "1.0", request_id: REQUEST_ID, request_line_id: LINE_ID, request_version: 3,
    current_revision_id: REVISION_ID, current_revision_no: 1, material_id: MATERIAL_ID,
    final_approved_qty: "2.000", cancelled_qty: "0.000", allocatable_qty: "2.000",
    projection_status: "ready", opening_balance_status: "established", projected_at: null, ledger_cursor: 8,
    items: [{
      stock_account_id: STOCK_ACCOUNT_ID, owner_org_id: ORG_ID, owner_org_code: "ORG-1", owner_org_name: "区域公司",
      location_owner_org_id: ORG_ID, location_owner_org_code: "ORG-1", location_owner_org_name: "区域公司",
      location_id: LOCATION_ID, location_code: "WH-001", location_name: "南京区域仓", location_type: "regional",
      location_parent_id: null, custodian_person_id: null, custodian_person_name: null,
      material_id: MATERIAL_ID, sku_code: "SKU-001", material_name: "测试物料", base_unit: "件",
      condition_code: "new", availability_bucket: "available", lot_id: null, lot_no: null,
      quantity, quantity_scale: 3, balance_version: 11, ledger_cursor: 8,
    }],
  };
}

function allocationResult(beforeVersion = 3, quantity = "1.000", state = axes("partially_allocated")): any {
  return {
    request_id: REQUEST_ID, allocation_id: ALLOCATION_ID, allocation_no: "AL-001",
    request_version: beforeVersion + 1, current_request_version: beforeVersion + 1,
    revision_id: REVISION_ID, revision_no: 1, request_line_id: LINE_ID,
    source_stock_account_id: STOCK_ACCOUNT_ID, source_balance_version: 11, source_ledger_cursor: 8,
    allocated_qty: quantity, allocation_status: "allocated", request_status: "approved", state_axes: state,
    idempotency_replayed: false,
  };
}

function allocationStore(initial: any = { kind: "missing" }) {
  let value: any = initial;
  const events: string[] = [];
  return {
    read: () => value,
    persist: vi.fn((next: unknown) => { events.push("persist"); value = { kind: "valid", value: next }; }),
    clear: vi.fn(() => { events.push("clear"); value = { kind: "missing" }; }),
    events,
  } as any;
}

function allocationAdapter(overrides: Record<string, unknown> = {}) {
  return {
    listAllocationOptions: vi.fn().mockResolvedValue(optionPage()),
    loadIdentity: vi.fn().mockResolvedValue(identity()),
    loadAccess: vi.fn().mockResolvedValue(access()),
    detail: vi.fn().mockResolvedValue(approvedDetail()),
    createAllocation: vi.fn().mockResolvedValue(allocationResult()),
    mutate: vi.fn(), loadIdentityNoReplay: vi.fn().mockResolvedValue(identity()),
    loadAccessNoReplay: vi.fn().mockResolvedValue(access()), detailNoReplay: vi.fn().mockResolvedValue(approvedDetail()),
    allocationCommandStatus: vi.fn(), allocationCommandStatusNoReplay: vi.fn(),
    lifecycleCommandStatus: vi.fn(), supplyCommandStatus: vi.fn(), list: vi.fn(), loadDraftForEdit: vi.fn(),
    listWorkOrderOptions: vi.fn(), workOrderOptionDetail: vi.fn(), listMaterials: vi.fn(), createDraft: vi.fn(),
    ...overrides,
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

  it("recovers one exact allocation coordinate before releasing the page write gate", async () => {
    const sentinel = {
      v: 1, kind: "material_request_allocation", x_request_id: "web-allocation-12345678",
      person_id: PERSON_ID, authorization_version: 7, request_id: REQUEST_ID,
      request_line_id: LINE_ID, request_version: 3, source_stock_account_id: STOCK_ACCOUNT_ID,
      allocated_qty: "1.000", source_balance_version: 11, source_ledger_cursor: 8,
    };
    const after = approvedDetail(4, "partially_allocated");
    const command = { ...allocationResult(), idempotency_replayed: true };
    const store = allocationStore({ kind: "valid", value: sentinel });
    const onAllocationBlocking = vi.fn();
    const onDetail = vi.fn();
    const adapter = allocationAdapter({
      allocationCommandStatusNoReplay: vi.fn().mockResolvedValue({
        schema_version: "1.0", lookup_status: "confirmed", command,
      }),
      detailNoReplay: vi.fn().mockResolvedValue(after),
    });
    render(<FormalMaterialRequestSupplyPanel
      adapter={adapter} access={access() as any} detail={approvedDetail()}
      store={{ read: () => ({ kind: "missing" }), persist: vi.fn(), clear: vi.fn() } as any}
      allocationRecoveryStore={store} registry={{ get: () => undefined } as any}
      otherWriteBusy={false} onBlocking={vi.fn()} onAllocationBlocking={onAllocationBlocking}
      onDetail={onDetail}
    />);

    await waitFor(() => expect(store.clear).toHaveBeenCalledWith(sentinel.x_request_id));
    expect(onDetail).toHaveBeenCalledWith(after);
    expect(onAllocationBlocking.mock.calls.some(([blocked]) => blocked === true)).toBe(true);
    expect(onAllocationBlocking).toHaveBeenLastCalledWith(false);
    expect(await screen.findByText(/此前分配操作已确认：AL-001/)).toBeTruthy();
  });

  it("selects one source, persists the sentinel before POST, and exact-reads the allocation", async () => {
    const before = approvedDetail();
    const after = approvedDetail(4, "partially_allocated");
    const store = allocationStore();
    const events = store.events as string[];
    const createAllocation = vi.fn(async (..._args: unknown[]) => {
      events.push("create");
      expect(store.read().kind).toBe("valid");
      return allocationResult();
    });
    const adapter = allocationAdapter({
      listAllocationOptions: vi.fn().mockResolvedValue(optionPage("1.000")),
      detailNoReplay: vi.fn().mockResolvedValueOnce(before).mockResolvedValueOnce(after),
      detail: vi.fn().mockResolvedValue(after),
      createAllocation,
    });
    const onAllocationBlocking = vi.fn();
    const onDetail = vi.fn();
    render(<FormalMaterialRequestSupplyPanel
      adapter={adapter} access={access() as any} detail={before}
      store={{ read: () => ({ kind: "missing" }), persist: vi.fn(), clear: vi.fn() } as any}
      allocationRecoveryStore={store} registry={{ get: () => undefined } as any}
      otherWriteBusy={false} onBlocking={vi.fn()} onAllocationBlocking={onAllocationBlocking}
      onDetail={onDetail}
    />);

    fireEvent.click(screen.getByRole("button", { name: "查看可用货源" }));
    await waitFor(() => expect(adapter.listAllocationOptions).toHaveBeenCalledWith(REQUEST_ID, LINE_ID));
    fireEvent.click(await screen.findByRole("button", { name: "选择并分配" }));
    const quantity = await screen.findByLabelText("分配数量");
    fireEvent.change(quantity, { target: { value: "1.000" } });
    fireEvent.click(screen.getByRole("button", { name: "确认分配" }));

    await waitFor(() => expect(createAllocation).toHaveBeenCalledTimes(1));
    expect(createAllocation).toHaveBeenCalledWith(
      REQUEST_ID,
      {
        expected_request_version: 3,
        request_line_id: LINE_ID,
        source_stock_account_id: STOCK_ACCOUNT_ID,
        allocated_qty: "1.000",
        source_balance_version: 11,
        source_ledger_cursor: 8,
        serial_ids: [],
      },
      expect.objectContaining({
        "X-Request-ID": expect.any(String),
        "Idempotency-Key": expect.any(String),
      }),
    );
    expect(events.indexOf("persist")).toBeGreaterThanOrEqual(0);
    expect(events.indexOf("persist")).toBeLessThan(events.indexOf("create"));
    expect(events.indexOf("clear")).toBeGreaterThan(events.indexOf("create"));
    expect(onDetail).toHaveBeenCalledWith(after);
    expect(onAllocationBlocking).toHaveBeenLastCalledWith(false);
    expect(await screen.findByText(/分配单 AL-001 已保存并精确回读/)).toBeTruthy();
  });

  it("rejects a quantity above the selected source before creating an allocation", async () => {
    const store = allocationStore();
    const createAllocation = vi.fn();
    const adapter = allocationAdapter({
      listAllocationOptions: vi.fn().mockResolvedValue(optionPage("1.000")),
      createAllocation,
    });
    render(<FormalMaterialRequestSupplyPanel
      adapter={adapter} access={access() as any} detail={approvedDetail()}
      store={{ read: () => ({ kind: "missing" }), persist: vi.fn(), clear: vi.fn() } as any}
      allocationRecoveryStore={store} registry={{ get: () => undefined } as any}
      otherWriteBusy={false} onBlocking={vi.fn()} onAllocationBlocking={vi.fn()}
      onDetail={vi.fn()}
    />);

    fireEvent.click(screen.getByRole("button", { name: "查看可用货源" }));
    fireEvent.click(await screen.findByRole("button", { name: "选择并分配" }));
    fireEvent.change(await screen.findByLabelText("分配数量"), { target: { value: "1.001" } });
    fireEvent.click(screen.getByRole("button", { name: "确认分配" }));

    await waitFor(async () => {
      expect(await screen.findAllByText("分配数量超过当前货源或明细可分配余量")).not.toHaveLength(0);
    });
    expect(createAllocation).not.toHaveBeenCalled();
    expect(store.persist).not.toHaveBeenCalled();
  });

  it("fails closed when the source projection cursor changes during preflight", async () => {
    const initial = optionPage("1.000");
    const drifted = optionPage("1.000");
    drifted.ledger_cursor = 9;
    drifted.items[0].ledger_cursor = 9;
    const store = allocationStore();
    const createAllocation = vi.fn();
    const adapter = allocationAdapter({
      listAllocationOptions: vi.fn().mockResolvedValueOnce(initial).mockResolvedValueOnce(drifted),
      createAllocation,
    });
    const onAllocationBlocking = vi.fn();
    render(<FormalMaterialRequestSupplyPanel
      adapter={adapter} access={access() as any} detail={approvedDetail()}
      store={{ read: () => ({ kind: "missing" }), persist: vi.fn(), clear: vi.fn() } as any}
      allocationRecoveryStore={store} registry={{ get: () => undefined } as any}
      otherWriteBusy={false} onBlocking={vi.fn()} onAllocationBlocking={onAllocationBlocking}
      onDetail={vi.fn()}
    />);

    fireEvent.click(screen.getByRole("button", { name: "查看可用货源" }));
    fireEvent.click(await screen.findByRole("button", { name: "选择并分配" }));
    fireEvent.click(screen.getByRole("button", { name: "确认分配" }));

    await waitFor(async () => {
      expect(await screen.findAllByText("货源余额、投影游标或批准余量已变化，请重新选择货源")).not.toHaveLength(0);
    });
    expect(createAllocation).not.toHaveBeenCalled();
    expect(store.persist).not.toHaveBeenCalled();
    expect(onAllocationBlocking).toHaveBeenLastCalledWith(false);
  });

  it("keeps the sentinel and write gate when the POST result is unknown", async () => {
    const store = allocationStore();
    const onAllocationBlocking = vi.fn();
    const createAllocation = vi.fn().mockRejectedValue(new Error("网络中断"));
    const adapter = allocationAdapter({ createAllocation });
    render(<FormalMaterialRequestSupplyPanel
      adapter={adapter} access={access() as any} detail={approvedDetail()}
      store={{ read: () => ({ kind: "missing" }), persist: vi.fn(), clear: vi.fn() } as any}
      allocationRecoveryStore={store} registry={{ get: () => undefined } as any}
      otherWriteBusy={false} onBlocking={vi.fn()} onAllocationBlocking={onAllocationBlocking}
      onDetail={vi.fn()}
    />);

    fireEvent.click(screen.getByRole("button", { name: "查看可用货源" }));
    fireEvent.click(await screen.findByRole("button", { name: "选择并分配" }));
    fireEvent.click(screen.getByRole("button", { name: "确认分配" }));

    await waitFor(() => expect(createAllocation).toHaveBeenCalledTimes(1));
    expect(store.persist).toHaveBeenCalledTimes(1);
    expect(store.read().kind).toBe("valid");
    expect(store.clear).not.toHaveBeenCalled();
    expect(onAllocationBlocking).toHaveBeenLastCalledWith(true);
    expect(await screen.findAllByText(/网络中断.*结果核验完成前/)).not.toHaveLength(0);
  });
});
