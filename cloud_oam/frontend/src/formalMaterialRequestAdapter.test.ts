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
      { resource: "inventory", action: "read", field_code: "" },
      { resource: "material_request", action: "approve_region", field_code: "approval_decision" },
      { resource: "material_request", action: "approve_headquarters", field_code: "approval_decision" },
      { resource: "material_request", action: "register_external", field_code: "approval_evidence" },
      { resource: "material_request", action: "verify_external", field_code: "approval_evidence" },
    ],
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
      can_read_material_catalog: true,
      can_approve_region: true,
      can_approve_headquarters: true,
      can_register_external: true,
      can_verify_external: true,
    });
    expect(requester).toHaveBeenCalledWith("/access/context");

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
    await adapter.listMaterials("SKU A", null);
    await adapter.listMaterials("SKU A", MATERIAL_ID.toUpperCase());

    expect(requester.mock.calls[0]).toEqual(["/v1/material-requests?limit=50"]);
    expect(requester.mock.calls[1]).toEqual([
      `/v1/material-requests?limit=50&after_id=${REQUEST_ID}`,
    ]);
    expect(requester.mock.calls[2]).toEqual([`/v1/material-requests/${REQUEST_ID}`]);
    expect(requester.mock.calls[3][0]).toBe(
      `/v1/material-requests/${REQUEST_ID}/editable-draft`,
    );
    expect(requester.mock.calls[3][1]).toEqual({
      cache: "no-store",
      headers: { "Cache-Control": "no-store", Pragma: "no-cache" },
    });
    expect(requester.mock.calls[4]).toEqual([
      "/v1/materials?limit=50&query=SKU%20A",
      { cache: "no-store", headers: { "Cache-Control": "no-store", Pragma: "no-cache" } },
    ]);
    expect(requester.mock.calls[5][0]).toBe(
      `/v1/materials?limit=50&query=SKU%20A&after_id=${MATERIAL_ID}`,
    );
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

  it("accepts only the implemented approval and external-evidence route shapes", async () => {
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

    const unsupported = new MaterialRequestIntentRegistry().begin({
      requestId: REQUEST_ID,
      action: "withdraw",
      path: `/v1/material-requests/${REQUEST_ID}/withdraw`,
      body: { expected_version: 4 },
      expectedVersion: 4,
    });
    await expect(adapter.mutate(unsupported)).rejects.toMatchObject({ status: 409 });

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
    expect(requester).toHaveBeenCalledTimes(3);
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
