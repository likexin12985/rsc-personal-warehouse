import { describe, expect, it, vi } from "vitest";

import type { api } from "./api";
import {
  createFormalMaterialRequestAdapter,
  validateFormalMaterialRequestEditableDraft,
} from "./formalMaterialRequestAdapter";
import {
  MaterialRequestCreateIntentRegistry,
  MaterialRequestIntentRegistry,
} from "./formalMaterialRequests";

const PERSON_ID = "10000000-0000-4000-8000-000000000001";
const REQUEST_ID = "20000000-0000-4000-8000-000000000001";
const STEP_ID = "30000000-0000-4000-8000-000000000001";
const REGISTRATION_ID = "40000000-0000-4000-8000-000000000001";
const WORK_ORDER_ID = "45000000-0000-4000-8000-000000000001";
const MATERIAL_ID = "50000000-0000-4000-8000-000000000001";

function accessContext() {
  return {
    person_id: PERSON_ID,
    account_status: "active",
    employment_status: "active",
    authorization_version: 7,
    access_mode: "active",
    role_codes: ["technician"],
    assignments: [],
    permissions: [
      { resource: "material_request", action: "read", field_code: "" },
      { resource: "material_request", action: "create", field_code: "" },
      { resource: "material_request", action: "update_draft", field_code: "" },
      { resource: "material_request", action: "withdraw", field_code: "" },
      { resource: "material_request", action: "cancel", field_code: "" },
      { resource: "inventory", action: "read", field_code: "" },
      { resource: "material_request", action: "approve_region", field_code: "approval_decision" },
      { resource: "material_request", action: "approve_headquarters", field_code: "approval_decision" },
      { resource: "material_request", action: "register_external", field_code: "approval_evidence" },
      { resource: "material_request", action: "verify_external", field_code: "approval_evidence" },
    ],
  };
}

function freshIdentity() {
  return {
    person_id: PERSON_ID,
    name: "工程师",
    employee_no: "E001",
    organization_code: "ORG-JS",
    organization_name: "江苏区域公司",
    account_status: "active",
    employment_status: "active",
    access_mode: "active",
    authorization_version: 7,
    role_codes: ["technician"],
  };
}

function draft() {
  return {
    work_order_id: null,
    purpose: "现场故障处理",
    urgency: "urgent" as const,
    expected_date: "2026-09-05",
    address: {
      province_code: "320000",
      province_name: "江苏省",
      city_name: "南京市",
      district_name: "建邺区",
      detail: "江东中路 100 号",
    },
    contact: { name: "李工程师", mobile: "138 0000 0000" },
    attachment_file_ids: [] as string[],
    note: "请及时处理",
    lines: [{
      material_id: MATERIAL_ID,
      requested_qty: "1.000",
      required_date: null,
      suggested_substitute_material_id: null,
      note: "故障替换",
    }],
  };
}

type MockRequester = typeof api & {
  mock: { calls: Array<[string, RequestInit?]> };
};

function makeRequester(
  handler: (path: string, init?: RequestInit) => Promise<unknown>,
): MockRequester {
  return vi.fn(handler) as unknown as MockRequester;
}

describe("formal material-request PC transport", () => {
  it("uses only the mounted supply paths and preserves uncertain-write coordinates", async () => {
    const requester = makeRequester(async () => { throw new Error("network uncertain"); });
    const adapter = createFormalMaterialRequestAdapter({ person_id: PERSON_ID, authorization_version: 7 }, requester);
    const registry = new MaterialRequestIntentRegistry();
    const intent = registry.begin({
      requestId: REQUEST_ID, action: "create_supply_task", path: `/v1/material-requests/${REQUEST_ID}/supply-tasks`,
      expectedVersion: 8, body: { expected_request_version: 8, request_line_id: STEP_ID,
        supply_type: "star_replenishment", reference_no: null, expected_qty: "1.000", expected_date: null, note: "" },
    });
    await expect(adapter.mutate(intent)).rejects.toThrow("network uncertain");
    await expect(adapter.mutate(intent)).rejects.toThrow("network uncertain");
    expect(requester.mock.calls[0]).toEqual(requester.mock.calls[1]);
    expect(requester.mock.calls[0][1]?.method).toBe("POST");
    expect(registry.get(REQUEST_ID)).toBe(intent);
    for (const status of ["awaiting_supply", "cancelled"] as const) {
      const update = new MaterialRequestIntentRegistry().begin({
        requestId: REQUEST_ID, action: status === "cancelled" ? "cancel_supply_task" : "update_supply_task",
        path: `/v1/material-requests/${REQUEST_ID}/supply-tasks/${REGISTRATION_ID}`, expectedVersion: 9,
        body: { expected_request_version: 9, expected_task_version: 0, status,
          reference_no: null, expected_date: null, comment: "跟进供给计划" },
      });
      await expect(adapter.mutate(update)).rejects.toThrow("network uncertain");
      expect(requester.mock.calls.at(-1)?.[1]?.method).toBe("POST");
    }
    await expect(adapter.mutate({ ...intent, path: `${intent.path}/cancel` })).rejects.toMatchObject({ status: 409 });
  });

  it("queries supply recovery without sending a key, contact or request body", async () => {
    const requester = makeRequester(async () => ({ schema_version: "1.0", lookup_status: "not_observed", command: null }));
    const adapter = createFormalMaterialRequestAdapter({ person_id: PERSON_ID, authorization_version: 7 }, requester);
    await adapter.supplyCommandStatus("web-supply-12345678");
    expect(requester.mock.calls).toEqual([["/v1/material-request-supply-command-status?trace_request_id=web-supply-12345678", {
      method: "GET", cache: "no-store", headers: { "Cache-Control": "no-store", Pragma: "no-cache" },
    }]]);
    await expect(adapter.supplyCommandStatus("bad?trace=1")).rejects.toMatchObject({ status: 409 });
    expect(requester.mock.calls).toHaveLength(1);
  });

  it("queries allocation recovery with the original request coordinate in a header", async () => {
    const requester = makeRequester(async () => ({
      schema_version: "1.0", lookup_status: "not_observed", command: null,
    }));
    const adapter = createFormalMaterialRequestAdapter({ person_id: PERSON_ID, authorization_version: 7 }, requester);
    await adapter.allocationCommandStatus("web-allocation-12345678");
    expect(requester.mock.calls).toEqual([["/v1/material-request-allocation-command-status", {
      method: "GET", cache: "no-store", headers: {
        "X-Request-ID": "web-allocation-12345678", "Cache-Control": "no-store", Pragma: "no-cache",
      },
    }]]);
    await expect(adapter.allocationCommandStatus("bad?trace=1")).rejects.toMatchObject({ status: 409 });
    expect(requester.mock.calls).toHaveLength(1);
  });

  it("posts allocation with exact projection coordinates and rejects unsafe payloads before transport", async () => {
    const requester = makeRequester(async () => ({
      request_id: REQUEST_ID, allocation_id: "60000000-0000-4000-8000-000000000001",
      allocation_no: "AL-20260907-ABC", request_version: 4, current_request_version: 4, revision_id: "70000000-0000-4000-8000-000000000001",
      revision_no: 1, request_line_id: STEP_ID, source_stock_account_id: MATERIAL_ID,
      source_balance_version: 8, source_ledger_cursor: 9, allocated_qty: "1.000",
      allocation_status: "allocated", request_status: "approved", state_axes: {
        request_status: "approved", allocation_status: "allocated", reservation_status: "not_reserved",
        outbound_status: "not_started", shipment_status: "not_started", logistics_signature_status: "not_signed",
        oam_receipt_status: "not_occurred", personal_inbound_status: "not_started",
        notification_status: "not_started", reconciliation_status: "not_started",
      }, idempotency_replayed: false,
    }));
    const adapter = createFormalMaterialRequestAdapter({ person_id: PERSON_ID, authorization_version: 7 }, requester);
    await adapter.createAllocation(REQUEST_ID, {
      expected_request_version: 3,
      request_line_id: STEP_ID,
      source_stock_account_id: MATERIAL_ID,
      allocated_qty: "1.000",
      source_balance_version: 8,
      source_ledger_cursor: 9,
      serial_ids: [],
    }, { "X-Request-ID": "web-allocation-12345678", "Idempotency-Key": "web-idempotency-12345678" });
    expect(requester.mock.calls[0]).toEqual([`/v1/material-requests/${REQUEST_ID}/allocations`, {
      method: "POST",
      headers: { "X-Request-ID": "web-allocation-12345678", "Idempotency-Key": "web-idempotency-12345678" },
      body: JSON.stringify({
        expected_request_version: 3, request_line_id: STEP_ID, source_stock_account_id: MATERIAL_ID,
        allocated_qty: "1.000", source_balance_version: 8, source_ledger_cursor: 9, serial_ids: [],
      }),
    }]);
  });

  it("fresh-reads the exact auth identity and command status without sending an idempotency key", async () => {
    const requester = makeRequester(async (path) => (
      path === "/auth/me"
        ? freshIdentity()
        : { schema_version: "1.0", lookup_status: "not_observed", command: null }
    ));
    const adapter = createFormalMaterialRequestAdapter({
      person_id: PERSON_ID,
      authorization_version: 7,
    }, requester);

    await expect(adapter.loadIdentity()).resolves.toEqual({
      schema_version: "1.0",
      person_id: PERSON_ID,
      authorization_version: 7,
    });
    await adapter.lifecycleCommandStatus("web-12345678");
    expect(requester.mock.calls).toEqual([
      ["/auth/me", {
        cache: "no-store",
        headers: { "Cache-Control": "no-store", Pragma: "no-cache" },
      }],
      ["/v1/material-request-lifecycle-command-status", {
        method: "GET",
        cache: "no-store",
        headers: {
          "X-Request-ID": "web-12345678",
          "Cache-Control": "no-store",
          Pragma: "no-cache",
        },
      }],
    ]);

    const leaked = { ...freshIdentity(), mobile: "13800000000" };
    const leakedAdapter = createFormalMaterialRequestAdapter({
      person_id: PERSON_ID,
      authorization_version: 7,
    }, makeRequester(async () => leaked));
    await expect(leakedAdapter.loadIdentity()).rejects.toThrow(/精确包含正式字段/);
    await expect(adapter.lifecycleCommandStatus("bad")).rejects.toMatchObject({ status: 409 });
  });

  it("projects only fresh read/create grants from the exact matching access context", async () => {
    const requester = makeRequester(async () => accessContext());
    const adapter = createFormalMaterialRequestAdapter({
      person_id: PERSON_ID,
      authorization_version: 7,
    }, requester);

    await expect(adapter.loadAccess()).resolves.toEqual({
      schema_version: "1.0",
      person_id: PERSON_ID,
      authorization_version: 7,
      can_read: true,
      can_create: true,
      can_withdraw: true,
      can_cancel: true,
      can_read_material_catalog: true,
      can_read_allocation_options: false,
      can_approve_region: true,
      can_approve_headquarters: true,
      can_register_external: true,
      can_verify_external: true,
    });
    expect(requester).toHaveBeenCalledWith("/access/context", {
      cache: "no-store",
      headers: { "Cache-Control": "no-store", Pragma: "no-cache" },
    });

    const noRead = accessContext();
    noRead.permissions = [{ resource: "material_request", action: "create", field_code: "" }];
    const noReadAdapter = createFormalMaterialRequestAdapter({
      person_id: PERSON_ID,
      authorization_version: 7,
    }, makeRequester(async () => noRead));
    await expect(noReadAdapter.loadAccess()).resolves.toMatchObject({
      can_read: false,
      can_create: false,
    });

    const managerContext = accessContext();
    managerContext.role_codes = ["provincial_manager"];
    await expect(createFormalMaterialRequestAdapter({
      person_id: PERSON_ID,
      authorization_version: 7,
    }, makeRequester(async () => managerContext)).loadAccess()).resolves.toMatchObject({
      can_read_allocation_options: true,
    });
  });

  it("fails closed on identity, authorization-version and permission-shape drift", async () => {
    const identityDrift = createFormalMaterialRequestAdapter({
      person_id: "10000000-0000-4000-8000-000000000002",
      authorization_version: 7,
    }, makeRequester(async () => accessContext()));
    await expect(identityDrift.loadAccess()).rejects.toMatchObject({ status: 409 });

    const versionDrift = createFormalMaterialRequestAdapter({
      person_id: PERSON_ID,
      authorization_version: 8,
    }, makeRequester(async () => accessContext()));
    await expect(versionDrift.loadAccess()).rejects.toMatchObject({ status: 409 });

    const malformed = accessContext();
    (malformed.permissions[0] as Record<string, unknown>).legacy = true;
    const malformedAdapter = createFormalMaterialRequestAdapter({
      person_id: PERSON_ID,
      authorization_version: 7,
    }, makeRequester(async () => malformed));
    await expect(malformedAdapter.loadAccess()).rejects.toMatchObject({ status: 409 });
  });

  it("uses only canonical formal reads and disables caching for plaintext editable drafts", async () => {
    const requester = makeRequester(async () => ({ ok: true }));
    const adapter = createFormalMaterialRequestAdapter({
      person_id: PERSON_ID,
      authorization_version: 7,
    }, requester);

    await adapter.list(null);
    await adapter.list(REQUEST_ID.toUpperCase());
    await adapter.detail(REQUEST_ID.toUpperCase());
    await adapter.loadDraftForEdit(REQUEST_ID.toUpperCase());
    await adapter.listWorkOrderOptions("WO A", null);
    await adapter.listWorkOrderOptions("WO A", WORK_ORDER_ID.toUpperCase());
    await adapter.workOrderOptionDetail(WORK_ORDER_ID.toUpperCase());
    await adapter.listMaterials("SKU A", null);
    await adapter.listMaterials("SKU A", MATERIAL_ID.toUpperCase());

    const noStore = {
      cache: "no-store",
      headers: { "Cache-Control": "no-store", Pragma: "no-cache" },
    };
    expect(requester.mock.calls[0]).toEqual(["/v1/material-requests?limit=50", noStore]);
    expect(requester.mock.calls[1]).toEqual([
      `/v1/material-requests?limit=50&after_id=${REQUEST_ID}`,
      noStore,
    ]);
    expect(requester.mock.calls[2]).toEqual([`/v1/material-requests/${REQUEST_ID}`, noStore]);
    expect(requester.mock.calls[3][0]).toBe(
      `/v1/material-requests/${REQUEST_ID}/editable-draft`,
    );
    expect(requester.mock.calls[3][1]).toEqual({
      cache: "no-store",
      headers: { "Cache-Control": "no-store", Pragma: "no-cache" },
    });
    expect(requester.mock.calls[4]).toEqual([
      "/v1/material-request-options/work-orders?limit=50&query=WO%20A",
      noStore,
    ]);
    expect(requester.mock.calls[5]).toEqual([
      `/v1/material-request-options/work-orders?limit=50&query=WO%20A&after_id=${WORK_ORDER_ID}`,
      noStore,
    ]);
    expect(requester.mock.calls[6]).toEqual([
      `/v1/material-request-options/work-orders/${WORK_ORDER_ID}`,
      noStore,
    ]);
    expect(requester.mock.calls[7]).toEqual([
      "/v1/materials?limit=50&query=SKU%20A",
      { cache: "no-store", headers: { "Cache-Control": "no-store", Pragma: "no-cache" } },
    ]);
    expect(requester.mock.calls[8][0]).toBe(
      `/v1/materials?limit=50&query=SKU%20A&after_id=${MATERIAL_ID}`,
    );
  });

  it("rejects invalid formal work-order queries and identifiers before transport", async () => {
    const requester = makeRequester(async () => ({ ok: true }));
    const adapter = createFormalMaterialRequestAdapter({
      person_id: PERSON_ID,
      authorization_version: 7,
    }, requester);

    expect(() => adapter.listWorkOrderOptions(" WO-A", null)).toThrow(/检索词无效/);
    expect(() => adapter.listWorkOrderOptions("WO-A\n", null)).toThrow(/检索词无效/);
    expect(() => adapter.listWorkOrderOptions(
      "WO-A",
      "00000000-0000-0000-0000-000000000000",
    )).toThrow(/after_id/);
    expect(() => adapter.workOrderOptionDetail(
      "00000000-0000-0000-0000-000000000000",
    )).toThrow(/work_order_id/);
    expect(requester).not.toHaveBeenCalled();
  });

  it("passes create and update intent paths, bodies and coordinates through unchanged", async () => {
    const requester = makeRequester(async () => ({ ok: true }));
    const adapter = createFormalMaterialRequestAdapter({
      person_id: PERSON_ID,
      authorization_version: 7,
    }, requester);
    const createIntent = new MaterialRequestCreateIntentRegistry().begin({ body: draft() });
    await adapter.createDraft(createIntent);

    expect(requester.mock.calls[0][0]).toBe(createIntent.path);
    const createInit = requester.mock.calls[0][1] as RequestInit;
    expect(createInit.method).toBe("POST");
    expect(createInit.headers).toBe(createIntent.headers);
    expect(JSON.parse(String(createInit.body))).toEqual(createIntent.body);

    const updateBody = { ...draft(), expected_version: 0 };
    const updateIntent = new MaterialRequestIntentRegistry().begin({
      requestId: REQUEST_ID,
      action: "update",
      path: `/v1/material-requests/${REQUEST_ID}`,
      body: updateBody,
      expectedVersion: 0,
    });
    await adapter.mutate(updateIntent);
    expect(requester.mock.calls[1][0]).toBe(updateIntent.path);
    const updateInit = requester.mock.calls[1][1] as RequestInit;
    expect(updateInit.method).toBe("PUT");
    expect(updateInit.headers).toBe(updateIntent.headers);
    expect(JSON.parse(String(updateInit.body))).toEqual(updateIntent.body);
  });

  it("accepts only implemented lifecycle, approval and external-evidence route shapes", async () => {
    const requester = makeRequester(async () => ({ ok: true }));
    const adapter = createFormalMaterialRequestAdapter({
      person_id: PERSON_ID,
      authorization_version: 7,
    }, requester);
    const approve = new MaterialRequestIntentRegistry().begin({
      requestId: REQUEST_ID,
      action: "approve",
      path: `/v1/material-requests/${REQUEST_ID}/approval-steps/${STEP_ID}/decision`,
      body: {
        expected_request_version: 3,
        expected_step_version: 0,
        action: "approve",
        lines: [{ request_line_id: MATERIAL_ID, approved_qty: "1.000", reason: "" }],
        return_lines: [],
        comment: "",
      },
      expectedVersion: 3,
    });
    await adapter.mutate(approve);

    const register = new MaterialRequestIntentRegistry().begin({
      requestId: REQUEST_ID,
      action: "register_external_approval",
      path: `/v1/material-requests/${REQUEST_ID}/approval-steps/${STEP_ID}/external-evidence`,
      body: {
        expected_request_version: 4,
        expected_step_version: 1,
        evidence_file_id: MATERIAL_ID,
        external_approver_name: "星星总部审批人",
        external_reference_no: "STAR-001",
        external_decided_at: "2026-09-01T08:00:00+08:00",
        action: "approve",
        lines: [{ request_line_id: MATERIAL_ID, approved_qty: "1.000", reason: "" }],
        return_lines: [],
        comment: "",
      },
      expectedVersion: 4,
    });
    await adapter.mutate(register);

    const verify = new MaterialRequestIntentRegistry().begin({
      requestId: REQUEST_ID,
      action: "verify_external_approval",
      path: `/v1/material-requests/${REQUEST_ID}/approval-steps/${STEP_ID}/external-evidence/${REGISTRATION_ID}/verification`,
      body: {
        expected_request_version: 5,
        expected_step_version: 2,
        decision: "accept",
        comment: "",
      },
      expectedVersion: 5,
    });
    await adapter.mutate(verify);
    expect(requester.mock.calls.map(([path]) => path)).toEqual([
      approve.path, register.path, verify.path,
    ]);

    const withdraw = new MaterialRequestIntentRegistry().begin({
      requestId: REQUEST_ID,
      action: "withdraw",
      path: `/v1/material-requests/${REQUEST_ID}/withdraw`,
      body: { expected_version: 6, reason: "申请信息需重新整理" },
      expectedVersion: 6,
    });
    await adapter.mutate(withdraw);

    const cancel = new MaterialRequestIntentRegistry().begin({
      requestId: "20000000-0000-4000-8000-000000000002",
      action: "cancel",
      path: "/v1/material-requests/20000000-0000-4000-8000-000000000002/cancel",
      body: {
        expected_version: 7,
        reason: "需求已不再需要",
        lines: [{ request_line_id: MATERIAL_ID, cancelled_qty: "1.000", reason: "本行不再需要" }],
      },
      expectedVersion: 7,
    });
    await adapter.mutate(cancel);

    const staleApprovalPath = new MaterialRequestIntentRegistry().begin({
      requestId: REQUEST_ID,
      action: "approve",
      path: `/v1/material-requests/${REQUEST_ID}/approval/decision`,
      body: {
        expected_request_version: 4,
        expected_step_version: 0,
        action: "approve",
        lines: [{ request_line_id: MATERIAL_ID, approved_qty: "1.000", reason: "" }],
        return_lines: [],
        comment: "",
      },
      expectedVersion: 4,
    });
    await expect(adapter.mutate(staleApprovalPath)).rejects.toMatchObject({ status: 409 });
    expect(requester).toHaveBeenCalledTimes(5);
    expect(requester.mock.calls.slice(3).map(([path, init]) => [path, init?.method])).toEqual([
      [withdraw.path, "POST"], [cancel.path, "POST"],
    ]);
  });

  it("rejects incomplete or inexact lifecycle bodies before any network call", async () => {
    const requester = makeRequester(async () => ({ ok: true }));
    const adapter = createFormalMaterialRequestAdapter({
      person_id: PERSON_ID,
      authorization_version: 7,
    }, requester);
    const missingReason = new MaterialRequestIntentRegistry().begin({
      requestId: REQUEST_ID,
      action: "withdraw",
      path: `/v1/material-requests/${REQUEST_ID}/withdraw`,
      body: { expected_version: 4 },
      expectedVersion: 4,
    });
    await expect(adapter.mutate(missingReason)).rejects.toMatchObject({ status: 409 });

    const duplicateLines = new MaterialRequestIntentRegistry().begin({
      requestId: "20000000-0000-4000-8000-000000000002",
      action: "cancel",
      path: "/v1/material-requests/20000000-0000-4000-8000-000000000002/cancel",
      body: {
        expected_version: 5,
        reason: "整单取消",
        lines: [
          { request_line_id: MATERIAL_ID, cancelled_qty: "1.000", reason: "取消" },
          { request_line_id: MATERIAL_ID, cancelled_qty: "1.000", reason: "重复" },
        ],
      },
      expectedVersion: 5,
    });
    await expect(adapter.mutate(duplicateLines)).rejects.toMatchObject({ status: 409 });
    expect(requester).not.toHaveBeenCalled();
  });

  it("rejects malformed approval bodies before any network call", async () => {
    const requester = makeRequester(async () => ({ ok: true }));
    const adapter = createFormalMaterialRequestAdapter({
      person_id: PERSON_ID,
      authorization_version: 7,
    }, requester);
    const malformed = new MaterialRequestIntentRegistry().begin({
      requestId: REQUEST_ID,
      action: "approve",
      path: `/v1/material-requests/${REQUEST_ID}/approval-steps/${STEP_ID}/decision`,
      body: { action: "approve", expected_request_version: 3 },
      expectedVersion: 3,
    });
    await expect(adapter.mutate(malformed)).rejects.toMatchObject({ status: 409 });
    expect(requester).not.toHaveBeenCalled();
  });

  it("reuses one uncertain intent without manufacturing replacement coordinates", async () => {
    const requester = makeRequester(async () => { throw new TypeError("network uncertain"); });
    const adapter = createFormalMaterialRequestAdapter({
      person_id: PERSON_ID,
      authorization_version: 7,
    }, requester);
    const intent = new MaterialRequestIntentRegistry().begin({
      requestId: REQUEST_ID,
      action: "submit",
      path: `/v1/material-requests/${REQUEST_ID}/submit`,
      body: { expected_version: 0 },
      expectedVersion: 0,
    });

    await expect(adapter.mutate(intent)).rejects.toThrow("network uncertain");
    await expect(adapter.mutate(intent)).rejects.toThrow("network uncertain");
    const first = requester.mock.calls[0][1] as RequestInit;
    const second = requester.mock.calls[1][1] as RequestInit;
    expect(first.headers).toBe(intent.headers);
    expect(second.headers).toBe(intent.headers);
    expect(first.body).toBe(second.body);
  });

  it("accepts version zero editable drafts while keeping the raw payload memory-only", () => {
    const raw = {
      schema_version: "1.0",
      request_id: REQUEST_ID,
      request_version: 0,
      draft: draft(),
    };
    const parsed = validateFormalMaterialRequestEditableDraft(raw, REQUEST_ID, 0);
    expect(parsed.draft.contact.mobile).toBe("138 0000 0000");
    expect(Object.isFrozen(parsed.draft)).toBe(true);
  });
});
