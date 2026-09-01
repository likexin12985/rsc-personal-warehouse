// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../api";
import type { FormalFileUploadClient } from "../FormalFileUploadField";
import type { FormalFilePurpose, FormalUploadFile } from "../formalFileUpload";
import type { FormalMaterialRequestAdapter } from "../formalMaterialRequestAdapter";
import FormalMaterialRequestsPage from "./FormalMaterialRequests";

const REQUEST_ID = "10000000-0000-4000-8000-000000000001";
const LINE_ID = "20000000-0000-4000-8000-000000000001";
const PERSON_ID = "30000000-0000-4000-8000-000000000001";
const ORG_ID = "40000000-0000-4000-8000-000000000001";
const MATERIAL_ID = "50000000-0000-4000-8000-000000000001";
const MATERIAL_2_ID = "50000000-0000-4000-8000-000000000002";
const ATTACHMENT_ID = "90000000-0000-4000-8000-000000000001";
const EVIDENCE_ID = "90000000-0000-4000-8000-000000000002";
const FILE_SHA = "ab".repeat(32);
const REVISION_ID = "a0000000-0000-4000-8000-000000000001";
const INSTANCE_ID = "60000000-0000-4000-8000-000000000001";
const STEP_1_ID = "70000000-0000-4000-8000-000000000001";
const STEP_2_ID = "70000000-0000-4000-8000-000000000002";
const STEP_3_ID = "70000000-0000-4000-8000-000000000003";
const REGISTRATION_ID = "80000000-0000-4000-8000-000000000001";
const RAW_MOBILE = "138 0000 0000";
const RAW_ADDRESS = "江东中路 100 号";

function axes(requestStatus = "draft") {
  return {
    request_status: requestStatus,
    allocation_status: "not_allocated",
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

function detail(version = 0): any {
  return {
    schema_version: "1.0",
    request_id: REQUEST_ID,
    request_no: "MR-20260901-0001",
    request_version: version,
    current_revision_id: REVISION_ID,
    current_revision_no: 1,
    work_order_id: null,
    requester_person_id: PERSON_ID,
    requester_org_id: ORG_ID,
    purpose: "现场故障处理",
    urgency: "urgent",
    expected_date: "2026-09-05",
    address_snapshot: {
      province_code: "320000",
      province_name: "江苏省",
      city_name: "南京市",
      district_name: "建邺区",
      detail_masked: "******",
    },
    contact_masked: { name_masked: "李*", mobile_masked: "*******0000" },
    note: "请及时处理",
    attachment_refs: [{
      revision_id: REVISION_ID,
      revision_no: 1,
      request_line_id: null,
      file_id: ATTACHMENT_ID,
      display_name: "故障照片.jpg",
      purpose: "request_attachment",
    }],
    approval_mode: "external_registration",
    states: axes(),
    approval_instance: null,
    lines: [{
      request_line_id: LINE_ID,
      revision_id: REVISION_ID,
      revision_no: 1,
      line_no: 1,
      material_id: MATERIAL_ID,
      requested_qty: "12.345",
      required_date: "2026-09-05",
      suggested_substitute_material_id: null,
      note: "故障替换",
      final_approved_qty: "0.000",
      cancelled_qty: "0.000",
      status: "draft",
      version,
    }],
    revision_history: [{
      revision_id: REVISION_ID,
      revision_no: 1,
      previous_revision_id: null,
      status: "draft",
      line_count: 1,
      attachment_count: 1,
      sealed_at: null,
      created_at: "2026-09-01T08:00:00+08:00",
    }],
    approval_history: [],
    supply_tasks: [],
    allowed_actions: ["update", "submit"],
    created_at: "2026-09-01T08:00:00+08:00",
    updated_at: version ? "2026-09-01T08:10:00+08:00" : "2026-09-01T08:00:00+08:00",
    submitted_at: null,
  };
}

function submittedDetail(): any {
  const value = detail(1);
  value.states = axes("approval_in_progress");
  value.submitted_at = "2026-09-01T08:10:00+08:00";
  value.revision_history[0].status = "sealed";
  value.revision_history[0].sealed_at = "2026-09-01T08:10:00+08:00";
  value.approval_instance = {
    instance_id: INSTANCE_ID,
    request_revision_id: REVISION_ID,
    revision_no: 1,
    attempt_no: 1,
    status: "active",
    current_step_no: 1,
    current_step_id: STEP_1_ID,
    version: 0,
    steps: [
      {
        step_id: STEP_1_ID, step_no: 1, attempt_no: 1,
        predecessor_step_id: null, supersedes_step_id: null, reopened_from_step_id: null,
        source_mode: "internal", status: "open",
        assignee_snapshot: { name_masked: "区＊＊＊＊", role_code: "provincial_manager" },
        candidate_pool_summary: null, opened_at: "2026-09-01T08:10:00+08:00",
        decided_at: null, version: 0, line_decisions: [],
      },
      {
        step_id: STEP_2_ID, step_no: 2, attempt_no: 1,
        predecessor_step_id: STEP_1_ID,
        supersedes_step_id: null, reopened_from_step_id: null,
        source_mode: "internal", status: "pending", assignee_snapshot: null,
        candidate_pool_summary: { candidate_count: 4, candidate_kinds: ["assignee"] },
        opened_at: null, decided_at: null, version: 0, line_decisions: [],
      },
      {
        step_id: STEP_3_ID, step_no: 3, attempt_no: 1,
        predecessor_step_id: STEP_2_ID,
        supersedes_step_id: null, reopened_from_step_id: null,
        source_mode: "external_registration", status: "pending", assignee_snapshot: null,
        candidate_pool_summary: { candidate_count: 4, candidate_kinds: ["registrar", "verifier"] },
        opened_at: null, decided_at: null, version: 0, line_decisions: [],
      },
    ],
    external_evidence_summaries: null,
    return_line_facts: [],
  };
  value.approval_history = [value.approval_instance];
  value.lines[0].status = "approval_pending";
  value.allowed_actions = ["withdraw"];
  return value;
}

function returnedDetail(): any {
  const value = submittedDetail();
  value.request_version = 2;
  value.states = axes("returned");
  value.approval_instance.status = "returned";
  value.approval_instance.current_step_no = null;
  value.approval_instance.current_step_id = null;
  value.approval_instance.version = 1;
  value.approval_instance.steps[0].status = "returned";
  value.approval_instance.steps[0].decided_at = "2026-09-01T08:20:00+08:00";
  value.approval_instance.return_line_facts = [{
    return_fact_id: "b0000000-0000-4000-8000-000000000001",
    return_action_id: "b0000000-0000-4000-8000-000000000002",
    instance_id: INSTANCE_ID,
    returned_from_step_id: STEP_1_ID,
    target_kind: "requester_revision",
    target_step_id: null,
    request_revision_id: REVISION_ID,
    revision_no: 1,
    request_line_id: LINE_ID,
    returned_step_input_qty: "12.345",
    target_step_max_qty: "12.345",
    required_review_qty: "12.345",
    reason: "补充故障照片",
    occurred_at: "2026-09-01T08:20:00+08:00",
  }];
  value.updated_at = "2026-09-01T08:20:00+08:00";
  value.allowed_actions = ["update", "submit"];
  return value;
}

function lineDecision(
  stepId: string,
  decisionId: string,
  source: "internal" | "external_registration" = "internal",
  registrationId: string | null = null,
) {
  return {
    decision_id: decisionId,
    step_id: stepId,
    request_revision_id: REVISION_ID,
    revision_no: 1,
    request_line_id: LINE_ID,
    input_qty: "12.345",
    approved_qty: "12.345",
    rejected_qty: "0.000",
    reason: "",
    decision_source: source,
    external_registration_id: registrationId,
    decided_at: stepId === STEP_1_ID
      ? "2026-09-01T08:20:00+08:00"
      : "2026-09-01T08:30:00+08:00",
  };
}

function internalApprovalDetail(): any {
  const value = submittedDetail();
  value.allowed_actions = ["approve", "return", "reject"];
  return value;
}

function headquartersApprovalDetail(): any {
  const value = submittedDetail();
  value.request_version = 2;
  value.updated_at = "2026-09-01T08:20:00+08:00";
  value.approval_instance.current_step_no = 2;
  value.approval_instance.current_step_id = STEP_2_ID;
  value.approval_instance.version = 1;
  value.approval_instance.steps[0] = {
    ...value.approval_instance.steps[0],
    status: "approved",
    decided_at: "2026-09-01T08:20:00+08:00",
    version: 1,
    line_decisions: [lineDecision(STEP_1_ID, "71000000-0000-4000-8000-000000000001")],
  };
  value.approval_instance.steps[1] = {
    ...value.approval_instance.steps[1],
    status: "open",
    opened_at: "2026-09-01T08:20:00+08:00",
    version: 1,
  };
  value.allowed_actions = ["approve", "return", "reject"];
  return value;
}

function externalRegistrationDetail(): any {
  const value = submittedDetail();
  value.request_version = 3;
  value.updated_at = "2026-09-01T08:30:00+08:00";
  value.approval_instance.current_step_no = 3;
  value.approval_instance.current_step_id = STEP_3_ID;
  value.approval_instance.version = 2;
  value.approval_instance.steps[0] = {
    ...value.approval_instance.steps[0],
    status: "approved",
    decided_at: "2026-09-01T08:20:00+08:00",
    version: 1,
    line_decisions: [lineDecision(STEP_1_ID, "71000000-0000-4000-8000-000000000001")],
  };
  value.approval_instance.steps[1] = {
    ...value.approval_instance.steps[1],
    status: "approved",
    opened_at: "2026-09-01T08:20:00+08:00",
    decided_at: "2026-09-01T08:30:00+08:00",
    version: 1,
    line_decisions: [lineDecision(STEP_2_ID, "72000000-0000-4000-8000-000000000001")],
  };
  value.approval_instance.steps[2] = {
    ...value.approval_instance.steps[2],
    status: "awaiting_external_evidence",
    opened_at: "2026-09-01T08:30:00+08:00",
  };
  value.allowed_actions = ["register_external_approval"];
  return value;
}

function externalVerificationDetail(): any {
  const value = externalRegistrationDetail();
  value.request_version = 4;
  value.updated_at = "2026-09-01T08:40:00+08:00";
  value.approval_instance.version = 3;
  value.approval_instance.steps[2].status = "evidence_pending_verification";
  value.approval_instance.steps[2].version = 1;
  value.approval_instance.external_evidence_summaries = [{
    registration_id: REGISTRATION_ID,
    registration_no: "STAR-20260901-001",
    step_id: STEP_3_ID,
    external_action: "approve",
    status: "pending_verification",
    evidence_file_id: ATTACHMENT_ID,
    external_approver_name_masked: "王*",
    external_decided_at: "2026-09-01T08:35:00+08:00",
    registered_at: "2026-09-01T08:40:00+08:00",
    verified_at: null,
    version: 0,
  }];
  value.allowed_actions = ["verify_external_approval"];
  return value;
}

function withdrawnDetail(): any {
  const value = submittedDetail();
  value.request_version = 2;
  value.states = axes("withdrawn");
  value.updated_at = "2026-09-01T08:20:00+08:00";
  value.approval_instance.status = "withdrawn";
  value.approval_instance.current_step_no = null;
  value.approval_instance.current_step_id = null;
  value.approval_instance.version = 1;
  value.approval_instance.steps = value.approval_instance.steps.map((step: any) => ({
    ...step,
    status: "cancelled",
    decided_at: null,
    version: step.version + 1,
  }));
  value.allowed_actions = [];
  return value;
}

function approvedDetail(): any {
  const value = externalVerificationDetail();
  value.request_version = 5;
  value.states = axes("approved");
  value.updated_at = "2026-09-01T08:50:00+08:00";
  value.approval_instance.status = "completed";
  value.approval_instance.current_step_no = null;
  value.approval_instance.current_step_id = null;
  value.approval_instance.version = 4;
  value.approval_instance.steps[2] = {
    ...value.approval_instance.steps[2],
    status: "approved",
    decided_at: "2026-09-01T08:50:00+08:00",
    version: 2,
    line_decisions: [lineDecision(
      STEP_3_ID,
      "73000000-0000-4000-8000-000000000001",
      "external_registration",
      REGISTRATION_ID,
    )],
  };
  value.approval_instance.external_evidence_summaries[0] = {
    ...value.approval_instance.external_evidence_summaries[0],
    status: "accepted",
    verified_at: "2026-09-01T08:50:00+08:00",
    version: 1,
  };
  value.lines[0].status = "approved";
  value.lines[0].final_approved_qty = "12.345";
  value.lines[0].version = 1;
  value.allowed_actions = ["cancel"];
  return value;
}

function cancelledDetail(): any {
  const value = approvedDetail();
  value.request_version = 6;
  value.states = axes("cancelled");
  value.updated_at = "2026-09-01T09:00:00+08:00";
  value.lines[0].status = "cancelled";
  value.lines[0].cancelled_qty = "12.345";
  value.lines[0].version = 2;
  value.allowed_actions = [];
  return value;
}

function draft() {
  return {
    work_order_id: null,
    purpose: "现场故障处理",
    urgency: "urgent",
    expected_date: "2026-09-05",
    address: {
      province_code: "320000",
      province_name: "江苏省",
      city_name: "南京市",
      district_name: "建邺区",
      detail: RAW_ADDRESS,
    },
    contact: { name: "李工程师", mobile: RAW_MOBILE },
    attachment_file_ids: [ATTACHMENT_ID],
    note: "请及时处理",
    lines: [{
      material_id: MATERIAL_ID,
      requested_qty: "12.345",
      required_date: "2026-09-05",
      suggested_substitute_material_id: null,
      note: "故障替换",
    }],
  };
}

function page(detailValue = detail()) {
  const {
    schema_version: _schema,
    lines,
    revision_history: _revisions,
    approval_history: _approvalHistory,
    supply_tasks: _tasks,
    ...summary
  } = detailValue;
  return { schema_version: "1.0", items: [{ ...summary, line_count: lines.length }], next_after_id: null };
}

function access(canCreate = true) {
  return {
    schema_version: "1.0",
    person_id: PERSON_ID,
    authorization_version: 1,
    can_read: true,
    can_create: canCreate,
    can_withdraw: false,
    can_cancel: false,
    can_read_material_catalog: true,
    can_approve_region: false,
    can_approve_headquarters: false,
    can_register_external: false,
    can_verify_external: false,
  };
}

function catalogPage() {
  return {
    schema_version: "1.0",
    items: [{
      material_id: MATERIAL_ID,
      sku_code: "SKU-A",
      name: "交流接触器",
      specification: "32A",
      base_unit: "件",
      tracking_mode: "serial",
      quantity_scale: 0,
      allow_fraction: false,
      source_updated_at: "2026-09-01T07:00:00+08:00",
    }],
    next_after_id: null,
  };
}

function catalogItem2() {
  return {
    ...catalogPage().items[0],
    material_id: MATERIAL_2_ID,
    sku_code: "SKU-B",
    name: "直流接触器",
    specification: "64A",
    tracking_mode: "lot",
  };
}

function adapter(overrides: Partial<FormalMaterialRequestAdapter> = {}): FormalMaterialRequestAdapter {
  return {
    loadAccess: vi.fn().mockResolvedValue(access()),
    list: vi.fn().mockResolvedValue(page()),
    detail: vi.fn().mockResolvedValue(detail()),
    loadDraftForEdit: vi.fn().mockResolvedValue({
      schema_version: "1.0",
      request_id: REQUEST_ID,
      request_version: 0,
      draft: draft(),
    }),
    listMaterials: vi.fn().mockResolvedValue(catalogPage()),
    createDraft: vi.fn(),
    mutate: vi.fn(),
    ...overrides,
  };
}

function uploadClient(expectedPurpose: FormalFilePurpose, fileId = ATTACHMENT_ID): FormalFileUploadClient {
  return {
    prepare: vi.fn(async (selected: FormalUploadFile, purpose: FormalFilePurpose) => {
      expect(purpose).toBe(expectedPurpose);
      return Object.freeze({
        file: selected,
        purpose,
        original_filename: selected.name,
        size_bytes: selected.size,
        mime_type: selected.type,
        sha256: FILE_SHA,
        intent_headers: { "Idempotency-Key": `file-intent-${"a".repeat(36)}`, "X-Request-ID": `web-${"b".repeat(36)}` },
        complete_headers: { "X-Request-ID": `web-${"c".repeat(36)}` },
      });
    }),
    execute: vi.fn(async (prepared) => Object.freeze({
      file_id: fileId,
      purpose: prepared.purpose,
      status: "available" as const,
      verified_at: "2026-09-01T08:01:00Z",
      sha256: prepared.sha256,
      size_bytes: prepared.size_bytes,
      mime_type: prepared.mime_type,
    })),
  };
}

function selectedFile(name = "现场照片.jpg"): File {
  return new File([new Uint8Array([1, 2, 3])], name, { type: "image/jpeg" });
}

async function renderReady(client: FormalMaterialRequestAdapter, uploads?: FormalFileUploadClient): Promise<void> {
  render(<FormalMaterialRequestsPage adapter={client} fileUploadClient={uploads} />);
  expect(await screen.findByText("MR-20260901-0001")).toBeTruthy();
}

async function openDetail(): Promise<HTMLElement> {
  fireEvent.click(screen.getByRole("button", { name: "查看" }));
  return screen.findByRole("dialog", { name: "正式需求详情" });
}

async function fillCreateForm(uploadAttachment = false): Promise<void> {
  fireEvent.change(screen.getByLabelText("用途"), { target: { value: "现场故障处理" } });
  fireEvent.change(screen.getByLabelText("紧急程度"), { target: { value: "urgent" } });
  fireEvent.change(screen.getByLabelText("期望日期（可空）"), { target: { value: "2026-09-05" } });
  fireEvent.change(screen.getByLabelText("联系人"), { target: { value: "李工程师" } });
  fireEvent.change(screen.getByLabelText("联系电话"), { target: { value: RAW_MOBILE } });
  fireEvent.change(screen.getByLabelText("省代码"), { target: { value: "320000" } });
  fireEvent.change(screen.getByLabelText("省"), { target: { value: "江苏省" } });
  fireEvent.change(screen.getByLabelText("市"), { target: { value: "南京市" } });
  fireEvent.change(screen.getByLabelText("区县"), { target: { value: "建邺区" } });
  fireEvent.change(screen.getByLabelText("详细地址"), { target: { value: RAW_ADDRESS } });
  if (uploadAttachment) {
    fireEvent.change(screen.getByLabelText("选择并上传需求附件"), { target: { files: [selectedFile()] } });
    expect(await screen.findByText("状态：available（已完成严格确认）")).toBeTruthy();
  }
  fireEvent.change(screen.getByLabelText("申请备注"), { target: { value: "请及时处理" } });
  fireEvent.click(screen.getByRole("button", { name: "选择正式物料 1" }));
  const picker = await screen.findByRole("dialog", { name: "选择正式物料" });
  expect(within(picker).getByText("SKU-A")).toBeTruthy();
  expect(picker.textContent).toContain("交流接触器");
  expect(picker.textContent).toContain("32A");
  expect(picker.textContent).toContain("序列号");
  fireEvent.click(within(picker).getByRole("button", { name: "选择" }));
  fireEvent.change(screen.getByLabelText("申请数量 1"), { target: { value: "12.345" } });
  fireEvent.change(screen.getByLabelText("需用日期 1"), { target: { value: "2026-09-05" } });
  fireEvent.change(screen.getByLabelText("明细备注 1"), { target: { value: "故障替换" } });
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("formal material request PC vertical slice", () => {
  it("renders only masked list/detail projections and keeps approval/fulfillment axes separate", async () => {
    const client = adapter();
    await renderReady(client);
    expect(document.body.textContent).not.toContain(RAW_MOBILE);
    expect(document.body.textContent).not.toContain(RAW_ADDRESS);
    const panel = await openDetail();
    expect(within(panel).getByText(/\*{7}0000/)).toBeTruthy();
    expect(within(panel).getByLabelText("需求十个独立状态轴").children).toHaveLength(10);
    expect(within(panel).getByText("供给计划（只读）")).toBeTruthy();
    expect(panel.textContent).not.toContain(RAW_MOBILE);
    expect(panel.textContent).not.toContain(RAW_ADDRESS);
  });

  it("creates a complete draft through an independent memory-only create intent", async () => {
    const setItem = vi.spyOn(Storage.prototype, "setItem");
    const createDraft = vi.fn().mockResolvedValue({
      schema_version: "1.0",
      request_id: REQUEST_ID,
      action: "create",
      request_version: 0,
      revision_id: REVISION_ID,
      revision_no: 1,
      states: axes(),
      idempotency_replayed: false,
    });
    const client = adapter({ createDraft });
    const uploads = uploadClient("request_attachment");
    await renderReady(client, uploads);
    fireEvent.click(screen.getByRole("button", { name: "新建需求" }));
    await fillCreateForm(true);
    expect(screen.queryByLabelText(/附件 file_id/)).toBeNull();
    expect(screen.getByText("现场照片.jpg")).toBeTruthy();
    expect(screen.getByText(/3 B · SHA-256/).textContent).toContain(FILE_SHA);
    fireEvent.click(screen.getByRole("button", { name: "保存草稿" }));

    await waitFor(() => expect(createDraft).toHaveBeenCalledTimes(1));
    const intent = createDraft.mock.calls[0][0];
    expect(intent.action).toBe("create");
    expect(intent.path).toBe("/v1/material-requests");
    expect(intent.body).toEqual(draft());
    expect(intent.headers["X-Request-ID"]).toMatch(/^[A-Za-z0-9][A-Za-z0-9._:-]{7,159}$/);
    expect(intent.headers["Idempotency-Key"]).toMatch(/^[A-Za-z0-9][A-Za-z0-9._:-]{7,159}$/);
    expect(uploads.prepare).toHaveBeenCalledTimes(1);
    expect(uploads.execute).toHaveBeenCalledTimes(1);
    expect(setItem).not.toHaveBeenCalled();
    const panel = await screen.findByRole("dialog", { name: "正式需求详情" });
    expect(panel.textContent).not.toContain(RAW_MOBILE);
    expect(panel.textContent).not.toContain(RAW_ADDRESS);
  });

  it("searches and paginates only the formal material catalog", async () => {
    const listMaterials = vi.fn()
      .mockResolvedValueOnce({ ...catalogPage(), next_after_id: MATERIAL_2_ID })
      .mockResolvedValueOnce({ schema_version: "1.0", items: [catalogItem2()], next_after_id: null })
      .mockResolvedValueOnce({ schema_version: "1.0", items: [catalogItem2()], next_after_id: null });
    await renderReady(adapter({ listMaterials }));
    fireEvent.click(screen.getByRole("button", { name: "新建需求" }));
    fireEvent.click(screen.getByRole("button", { name: "选择正式物料 1" }));
    const picker = await screen.findByRole("dialog", { name: "选择正式物料" });
    expect(await within(picker).findByText("SKU-A")).toBeTruthy();
    fireEvent.click(within(picker).getByRole("button", { name: "加载更多正式物料" }));
    expect(await within(picker).findByText("SKU-B")).toBeTruthy();
    fireEvent.change(within(picker).getByLabelText("物料目录检索词"), {
      target: { value: "直流接触器" },
    });
    fireEvent.click(within(picker).getByRole("button", { name: "搜索正式目录" }));
    await waitFor(() => expect(listMaterials).toHaveBeenCalledTimes(3));
    expect(listMaterials.mock.calls).toEqual([
      ["", null],
      ["", MATERIAL_2_ID],
      ["直流接触器", null],
    ]);
    expect(within(picker).queryByText("SKU-A")).toBeNull();
    expect(within(picker).getByText("SKU-B")).toBeTruthy();
  });

  it("loads raw editable fields only after explicit edit and uses update intent plus exact reread", async () => {
    const after = detail(1);
    const mutate = vi.fn().mockResolvedValue({
      schema_version: "1.0",
      request_id: REQUEST_ID,
      action: "update",
      request_version: 1,
      revision_id: REVISION_ID,
      revision_no: 1,
      approval_instance_id: null,
      approval_attempt_no: null,
      current_step_id: null,
      states: axes(),
      idempotency_replayed: false,
    });
    const loadDraftForEdit = vi.fn().mockResolvedValue({
      schema_version: "1.0", request_id: REQUEST_ID, request_version: 0, draft: draft(),
    });
    const client = adapter({
      loadDraftForEdit,
      mutate,
      detail: vi.fn().mockResolvedValueOnce(detail()).mockResolvedValueOnce(after),
    });
    await renderReady(client);
    expect(loadDraftForEdit).not.toHaveBeenCalled();
    const panel = await openDetail();
    fireEvent.click(within(panel).getByRole("button", { name: "编辑草稿" }));
    expect(await screen.findByDisplayValue(RAW_MOBILE)).toBeTruthy();
    expect(loadDraftForEdit).toHaveBeenCalledWith(REQUEST_ID);
    fireEvent.click(screen.getByRole("button", { name: "保存修改" }));

    await waitFor(() => expect(mutate).toHaveBeenCalledTimes(1));
    const intent = mutate.mock.calls[0][0];
    expect(intent.action).toBe("update");
    expect(intent.path).toBe(`/v1/material-requests/${REQUEST_ID}`);
    expect(intent.body.expected_version).toBe(0);
    expect(intent.body.contact.mobile).toBe(RAW_MOBILE);
    expect((await screen.findByRole("dialog", { name: "正式需求详情" })).textContent).not.toContain(RAW_MOBILE);
  });

  it("requires an explicit submit confirmation and preserves independent status wording", async () => {
    const mutate = vi.fn().mockResolvedValue({
      schema_version: "1.0",
      request_id: REQUEST_ID,
      action: "submit",
      request_version: 1,
      revision_id: REVISION_ID,
      revision_no: 1,
      approval_instance_id: INSTANCE_ID,
      approval_attempt_no: 1,
      current_step_id: STEP_1_ID,
      states: axes("approval_in_progress"),
      idempotency_replayed: false,
    });
    const client = adapter({
      mutate,
      detail: vi.fn().mockResolvedValueOnce(detail()).mockResolvedValueOnce(submittedDetail()),
    });
    await renderReady(client);
    const panel = await openDetail();
    fireEvent.click(within(panel).getByRole("button", { name: "提交前确认" }));
    expect(mutate).not.toHaveBeenCalled();
    const confirmation = screen.getByLabelText("需求提交确认摘要");
    expect(confirmation.textContent).toContain("审批通过不等于分配、占用、出库、发货、物流签收、OAM收货、RSC/个人仓入库、通知送达或对账同步完成");
    fireEvent.click(within(confirmation).getByRole("button", { name: "确认提交" }));
    await waitFor(() => expect(mutate).toHaveBeenCalledTimes(1));
    expect(mutate.mock.calls[0][0].body).toEqual({ expected_version: 0 });
    expect(await screen.findByText(/分配、履约和通知仍为独立状态/)).toBeTruthy();
  });

  it("withdraws only after permission, allowed action, reason confirmation and exact terminal reread", async () => {
    const before = submittedDetail();
    const after = withdrawnDetail();
    const mutate = vi.fn().mockResolvedValue({
      schema_version: "1.0",
      request_id: REQUEST_ID,
      action: "withdraw",
      request_version: 2,
      revision_id: REVISION_ID,
      revision_no: 1,
      approval_instance_id: INSTANCE_ID,
      approval_attempt_no: 1,
      current_step_id: null,
      states: axes("withdrawn"),
      idempotency_replayed: false,
    });
    const withoutPermission = adapter({
      list: vi.fn().mockResolvedValue(page(before)),
      detail: vi.fn().mockResolvedValue(before),
    });
    await renderReady(withoutPermission);
    let panel = await openDetail();
    expect(within(panel).queryByRole("button", { name: "撤回申请" })).toBeNull();
    cleanup();

    const client = adapter({
      loadAccess: vi.fn().mockResolvedValue({ ...access(), can_withdraw: true }),
      list: vi.fn().mockResolvedValue(page(before)),
      detail: vi.fn().mockResolvedValueOnce(before).mockResolvedValueOnce(after),
      mutate,
    });
    await renderReady(client);
    panel = await openDetail();
    fireEvent.click(within(panel).getByRole("button", { name: "撤回申请" }));
    const confirmation = await screen.findByRole("dialog", { name: "撤回需求" });
    expect(confirmation.textContent).toContain("撤回只终止当前申请与审批轴");
    fireEvent.click(within(confirmation).getByRole("button", { name: "确认撤回" }));
    expect(mutate).not.toHaveBeenCalled();
    expect(within(confirmation).getByText(/必须填写不超过 4000 字的整单原因/)).toBeTruthy();
    fireEvent.change(within(confirmation).getByLabelText("撤回原因"), {
      target: { value: "申请信息\n需重新整理" },
    });
    fireEvent.click(within(confirmation).getByRole("button", { name: "确认撤回" }));
    expect(mutate).not.toHaveBeenCalled();
    expect(within(confirmation).getByText(/不能包含换行或控制字符/)).toBeTruthy();
    fireEvent.change(within(confirmation).getByLabelText("撤回原因"), {
      target: { value: "申请信息需重新整理" },
    });
    fireEvent.click(within(confirmation).getByRole("button", { name: "确认撤回" }));
    await waitFor(() => expect(mutate).toHaveBeenCalledTimes(1));
    expect(mutate.mock.calls[0][0]).toMatchObject({
      action: "withdraw",
      path: `/v1/material-requests/${REQUEST_ID}/withdraw`,
      expected_version: 1,
      body: { expected_version: 1, reason: "申请信息需重新整理" },
    });
    expect(await screen.findByText(/其他九个状态轴未被合并/)).toBeTruthy();
  });

  it("keeps a lifecycle intent pending when reread advances another independent axis", async () => {
    const before = submittedDetail();
    const after = withdrawnDetail();
    after.states.allocation_status = "allocated";
    const responseStates = { ...axes("withdrawn"), allocation_status: "allocated" };
    const mutate = vi.fn().mockResolvedValue({
      schema_version: "1.0",
      request_id: REQUEST_ID,
      action: "withdraw",
      request_version: 2,
      revision_id: REVISION_ID,
      revision_no: 1,
      approval_instance_id: INSTANCE_ID,
      approval_attempt_no: 1,
      current_step_id: null,
      states: responseStates,
      idempotency_replayed: false,
    });
    const client = adapter({
      loadAccess: vi.fn().mockResolvedValue({ ...access(), can_withdraw: true }),
      list: vi.fn().mockResolvedValue(page(before)),
      detail: vi.fn().mockResolvedValueOnce(before).mockResolvedValueOnce(after),
      mutate,
    });
    await renderReady(client);
    const panel = await openDetail();
    fireEvent.click(within(panel).getByRole("button", { name: "撤回申请" }));
    const confirmation = await screen.findByRole("dialog", { name: "撤回需求" });
    fireEvent.change(within(confirmation).getByLabelText("撤回原因"), {
      target: { value: "申请信息需重新整理" },
    });
    fireEvent.click(within(confirmation).getByRole("button", { name: "确认撤回" }));
    await waitFor(() => expect(mutate).toHaveBeenCalledTimes(1));
    expect(within(confirmation).getByText(/仍待人工核验/)).toBeTruthy();
    expect(within(confirmation).getByText(/禁止生成新坐标或执行其他动作/)).toBeTruthy();
  });

  it("keeps withdrawal pending when reread changes request content or leaves an approval step active", async () => {
    const scenarios = [
      {
        mutateAfter: (after: any) => { after.purpose = "被错误改写的需求用途"; },
        expectedError: /仍待人工核验/,
      },
      {
        mutateAfter: (after: any) => {
          after.approval_instance.steps[0].status = "open";
          after.approval_instance.steps[0].version = 0;
        },
        expectedError: /不能保留活动审批步骤/,
      },
    ];
    for (const { mutateAfter, expectedError } of scenarios) {
      const before = submittedDetail();
      const after = withdrawnDetail();
      mutateAfter(after);
      const mutate = vi.fn().mockResolvedValue({
        schema_version: "1.0",
        request_id: REQUEST_ID,
        action: "withdraw",
        request_version: 2,
        revision_id: REVISION_ID,
        revision_no: 1,
        approval_instance_id: INSTANCE_ID,
        approval_attempt_no: 1,
        current_step_id: null,
        states: axes("withdrawn"),
        idempotency_replayed: false,
      });
      const client = adapter({
        loadAccess: vi.fn().mockResolvedValue({ ...access(), can_withdraw: true }),
        list: vi.fn().mockResolvedValue(page(before)),
        detail: vi.fn().mockResolvedValueOnce(before).mockResolvedValueOnce(after),
        mutate,
      });
      await renderReady(client);
      const panel = await openDetail();
      fireEvent.click(within(panel).getByRole("button", { name: "撤回申请" }));
      const confirmation = await screen.findByRole("dialog", { name: "撤回需求" });
      fireEvent.change(within(confirmation).getByLabelText("撤回原因"), {
        target: { value: "申请信息需重新整理" },
      });
      fireEvent.click(within(confirmation).getByRole("button", { name: "确认撤回" }));
      await waitFor(() => expect(mutate).toHaveBeenCalledTimes(1));
      expect(within(confirmation).getByText(expectedError)).toBeTruthy();
      expect(within(confirmation).getByText(/禁止生成新坐标或执行其他动作/)).toBeTruthy();
      cleanup();
    }
  });

  it("safe-cancels the exact full approved line manifest and verifies line facts on reread", async () => {
    const before = approvedDetail();
    const after = cancelledDetail();
    const mutate = vi.fn().mockResolvedValue({
      schema_version: "1.0",
      request_id: REQUEST_ID,
      action: "cancel",
      request_version: 6,
      revision_id: REVISION_ID,
      revision_no: 1,
      approval_instance_id: INSTANCE_ID,
      approval_attempt_no: 1,
      current_step_id: null,
      states: axes("cancelled"),
      idempotency_replayed: false,
    });
    const client = adapter({
      loadAccess: vi.fn().mockResolvedValue({ ...access(), can_cancel: true }),
      list: vi.fn().mockResolvedValue(page(before)),
      detail: vi.fn().mockResolvedValueOnce(before).mockResolvedValueOnce(after),
      mutate,
    });
    await renderReady(client);
    const panel = await openDetail();
    fireEvent.click(within(panel).getByRole("button", { name: "安全取消" }));
    const confirmation = await screen.findByRole("dialog", { name: "安全取消需求" });
    expect(confirmation.textContent).toContain("不存在分配、占用、出库、发货、物流、入库、通知、对账或其他补偿事实");
    expect(confirmation.textContent).toContain("12.345");
    fireEvent.change(within(confirmation).getByLabelText("整单取消原因"), {
      target: { value: "现场需求已取消" },
    });
    fireEvent.click(within(confirmation).getByRole("button", { name: "确认安全取消" }));
    expect(mutate).not.toHaveBeenCalled();
    expect(within(confirmation).getByText(/每条有批准数量的明细都必须填写/)).toBeTruthy();
    fireEvent.change(within(confirmation).getByLabelText("取消明细原因 1"), {
      target: { value: "本行已无需求" },
    });
    fireEvent.click(within(confirmation).getByRole("button", { name: "确认安全取消" }));
    await waitFor(() => expect(mutate).toHaveBeenCalledTimes(1));
    expect(mutate.mock.calls[0][0]).toMatchObject({
      action: "cancel",
      path: `/v1/material-requests/${REQUEST_ID}/cancel`,
      expected_version: 5,
      body: {
        expected_version: 5,
        reason: "现场需求已取消",
        lines: [{
          request_line_id: LINE_ID,
          cancelled_qty: "12.345",
          reason: "本行已无需求",
        }],
      },
    });
    expect(await screen.findByText(/逐行事实与十个状态轴精确回读/)).toBeTruthy();
  });

  it("keeps cancellation pending when reread rewrites immutable approval history", async () => {
    const before = approvedDetail();
    const after = cancelledDetail();
    after.approval_instance.steps[2].line_decisions[0].reason = "被错误改写的审批事实";
    const mutate = vi.fn().mockResolvedValue({
      schema_version: "1.0",
      request_id: REQUEST_ID,
      action: "cancel",
      request_version: 6,
      revision_id: REVISION_ID,
      revision_no: 1,
      approval_instance_id: INSTANCE_ID,
      approval_attempt_no: 1,
      current_step_id: null,
      states: axes("cancelled"),
      idempotency_replayed: false,
    });
    const client = adapter({
      loadAccess: vi.fn().mockResolvedValue({ ...access(), can_cancel: true }),
      list: vi.fn().mockResolvedValue(page(before)),
      detail: vi.fn().mockResolvedValueOnce(before).mockResolvedValueOnce(after),
      mutate,
    });
    await renderReady(client);
    const panel = await openDetail();
    fireEvent.click(within(panel).getByRole("button", { name: "安全取消" }));
    const confirmation = await screen.findByRole("dialog", { name: "安全取消需求" });
    fireEvent.change(within(confirmation).getByLabelText("整单取消原因"), {
      target: { value: "现场需求已取消" },
    });
    fireEvent.change(within(confirmation).getByLabelText("取消明细原因 1"), {
      target: { value: "本行已无需求" },
    });
    fireEvent.click(within(confirmation).getByRole("button", { name: "确认安全取消" }));
    await waitFor(() => expect(mutate).toHaveBeenCalledTimes(1));
    expect(within(confirmation).getByText(/仍待人工核验/)).toBeTruthy();
    expect(within(confirmation).getByText(/禁止生成新坐标或执行其他动作/)).toBeTruthy();
  });

  it("reuses one lifecycle intent after an uncertain result and blocks changing the confirmation", async () => {
    const before = submittedDetail();
    const mutate = vi.fn().mockRejectedValue(new TypeError("network uncertain"));
    const client = adapter({
      loadAccess: vi.fn().mockResolvedValue({ ...access(), can_withdraw: true }),
      list: vi.fn().mockResolvedValue(page(before)),
      detail: vi.fn().mockResolvedValue(before),
      mutate,
    });
    await renderReady(client);
    const panel = await openDetail();
    fireEvent.click(within(panel).getByRole("button", { name: "撤回申请" }));
    const confirmation = await screen.findByRole("dialog", { name: "撤回需求" });
    fireEvent.change(within(confirmation).getByLabelText("撤回原因"), {
      target: { value: "申请信息需重整" },
    });
    fireEvent.click(within(confirmation).getByRole("button", { name: "确认撤回" }));
    await waitFor(() => expect(mutate).toHaveBeenCalledTimes(1));
    expect(within(confirmation).getByText(/禁止生成新坐标或执行其他动作/)).toBeTruthy();
    expect(within(confirmation).getByLabelText("撤回原因").hasAttribute("disabled")).toBe(true);
    fireEvent.click(within(confirmation).getByRole("button", { name: "按原请求坐标重试" }));
    await waitFor(() => expect(mutate).toHaveBeenCalledTimes(2));
    expect(mutate.mock.calls[1][0]).toBe(mutate.mock.calls[0][0]);
  });

  it("sends exact region approve/return/reject bodies only when permission and allowed_actions agree", async () => {
    const actionable = internalApprovalDetail();
    const mutate = vi.fn().mockRejectedValue(new ApiError(422, "明确拒绝测试", {}));
    const client = adapter({
      loadAccess: vi.fn().mockResolvedValue({ ...access(), can_approve_region: true }),
      list: vi.fn().mockResolvedValue(page(actionable)),
      detail: vi.fn().mockResolvedValue(actionable),
      mutate,
    });
    await renderReady(client);
    const panel = await openDetail();
    fireEvent.click(within(panel).getByRole("button", { name: "处理当前内部审批" }));
    const process = await screen.findByRole("dialog", { name: "处理当前内部审批" });

    fireEvent.click(within(process).getByRole("button", { name: "确认处理" }));
    await waitFor(() => expect(mutate).toHaveBeenCalledTimes(1));
    expect(mutate.mock.calls[0][0]).toMatchObject({
      action: "approve",
      path: `/v1/material-requests/${REQUEST_ID}/approval-steps/${STEP_1_ID}/decision`,
      expected_version: 1,
      body: {
        expected_request_version: 1,
        expected_step_version: 0,
        action: "approve",
        lines: [{ request_line_id: LINE_ID, approved_qty: "12.345", reason: "" }],
        return_lines: [],
        comment: "",
      },
    });

    fireEvent.click(within(process).getByRole("button", { name: "逐行退回" }));
    fireEvent.change(within(process).getByLabelText("审批明细原因 1"), { target: { value: "补充核验依据" } });
    fireEvent.change(within(process).getByLabelText("审批处理意见"), { target: { value: "退回申请人补充" } });
    fireEvent.click(within(process).getByRole("button", { name: "确认处理" }));
    await waitFor(() => expect(mutate).toHaveBeenCalledTimes(2));
    expect(mutate.mock.calls[1][0]).toMatchObject({
      action: "return",
      body: {
        expected_request_version: 1,
        expected_step_version: 0,
        action: "return",
        lines: [],
        return_lines: [{
          request_line_id: LINE_ID,
          requested_reapproval_qty: "12.345",
          reason: "补充核验依据",
        }],
        comment: "退回申请人补充",
      },
    });

    fireEvent.click(within(process).getByRole("button", { name: "整单驳回" }));
    fireEvent.change(within(process).getByLabelText("审批处理意见"), { target: { value: "不符合申请范围" } });
    fireEvent.click(within(process).getByRole("button", { name: "确认处理" }));
    await waitFor(() => expect(mutate).toHaveBeenCalledTimes(3));
    expect(mutate.mock.calls[2][0]).toMatchObject({
      action: "reject",
      body: {
        expected_request_version: 1,
        expected_step_version: 0,
        action: "reject",
        lines: [],
        return_lines: [],
        comment: "不符合申请范围",
      },
    });
  });

  it("reuses the exact approval intent after an uncertain result and blocks replacement coordinates", async () => {
    const actionable = internalApprovalDetail();
    const mutate = vi.fn().mockRejectedValue(new TypeError("network uncertain"));
    const client = adapter({
      loadAccess: vi.fn().mockResolvedValue({ ...access(), can_approve_region: true }),
      list: vi.fn().mockResolvedValue(page(actionable)),
      detail: vi.fn().mockResolvedValue(actionable),
      mutate,
    });
    await renderReady(client);
    const panel = await openDetail();
    fireEvent.click(within(panel).getByRole("button", { name: "处理当前内部审批" }));
    const process = await screen.findByRole("dialog", { name: "处理当前内部审批" });
    fireEvent.click(within(process).getByRole("button", { name: "确认处理" }));
    await waitFor(() => expect(mutate).toHaveBeenCalledTimes(1));
    expect(within(process).getByText(/禁止生成新坐标或执行其他动作/)).toBeTruthy();
    fireEvent.click(within(process).getByRole("button", { name: "确认处理" }));
    await waitFor(() => expect(mutate).toHaveBeenCalledTimes(2));
    expect(mutate.mock.calls[1][0]).toBe(mutate.mock.calls[0][0]);
  });

  it("opens headquarters processing only from the headquarters grant and predecessor line facts", async () => {
    const actionable = headquartersApprovalDetail();
    const mutate = vi.fn().mockRejectedValue(new ApiError(422, "明确拒绝测试", {}));
    const client = adapter({
      loadAccess: vi.fn().mockResolvedValue({ ...access(), can_approve_headquarters: true }),
      list: vi.fn().mockResolvedValue(page(actionable)),
      detail: vi.fn().mockResolvedValue(actionable),
      mutate,
    });
    await renderReady(client);
    const panel = await openDetail();
    fireEvent.click(within(panel).getByRole("button", { name: "处理当前内部审批" }));
    const process = await screen.findByRole("dialog", { name: "处理当前内部审批" });
    expect((within(process).getByLabelText("批准数量 1") as HTMLInputElement).value).toBe("12.345");
    fireEvent.click(within(process).getByRole("button", { name: "确认处理" }));
    await waitFor(() => expect(mutate).toHaveBeenCalledTimes(1));
    expect(mutate.mock.calls[0][0]).toMatchObject({
      path: `/v1/material-requests/${REQUEST_ID}/approval-steps/${STEP_2_ID}/decision`,
      body: { expected_request_version: 2, expected_step_version: 1 },
    });
  });

  it("uploads a purpose-isolated Star headquarters evidence file before registration", async () => {
    const actionable = externalRegistrationDetail();
    const mutate = vi.fn().mockRejectedValue(new ApiError(422, "明确拒绝测试", {}));
    const client = adapter({
      loadAccess: vi.fn().mockResolvedValue({ ...access(), can_register_external: true }),
      list: vi.fn().mockResolvedValue(page(actionable)),
      detail: vi.fn().mockResolvedValue(actionable),
      mutate,
    });
    const uploads = uploadClient("external_approval_evidence", EVIDENCE_ID);
    await renderReady(client, uploads);
    const panel = await openDetail();
    fireEvent.click(within(panel).getByRole("button", { name: "登记星星总部审批证据" }));
    const process = await screen.findByRole("dialog", { name: "登记星星总部审批证据" });
    expect(within(process).getByText(/不能复用需求附件、手填 file_id/)).toBeTruthy();
    expect(within(process).queryByLabelText(/正式证据 file_id/)).toBeNull();
    fireEvent.change(within(process).getByLabelText("选择并上传星星总部审批证据"), { target: { files: [selectedFile("星星审批截图.jpg")] } });
    expect(await within(process).findByText("状态：available（已完成严格确认）")).toBeTruthy();
    fireEvent.change(within(process).getByLabelText("星星总部审批人"), { target: { value: "星星总部王审批员" } });
    fireEvent.change(within(process).getByLabelText("外部审批参考号"), { target: { value: "STAR-APPROVAL-001" } });
    fireEvent.change(within(process).getByLabelText("外部决定时间（含时区 ISO8601）"), { target: { value: "2026-09-01T08:35:00+08:00" } });
    fireEvent.click(within(process).getByRole("button", { name: "确认处理" }));
    await waitFor(() => expect(mutate).toHaveBeenCalledTimes(1));
    expect(mutate.mock.calls[0][0]).toMatchObject({
      action: "register_external_approval",
      path: `/v1/material-requests/${REQUEST_ID}/approval-steps/${STEP_3_ID}/external-evidence`,
      expected_version: 3,
      body: {
        expected_request_version: 3,
        expected_step_version: 0,
        evidence_file_id: EVIDENCE_ID,
        external_approver_name: "星星总部王审批员",
        external_reference_no: "STAR-APPROVAL-001",
        external_decided_at: "2026-09-01T08:35:00+08:00",
        action: "approve",
        lines: [{ request_line_id: LINE_ID, approved_qty: "12.345", reason: "" }],
        return_lines: [],
        comment: "",
      },
    });
    expect(mutate.mock.calls[0][0].body.evidence_file_id).not.toBe(ATTACHMENT_ID);
    expect(uploads.prepare).toHaveBeenCalledTimes(1);
    expect(uploads.execute).toHaveBeenCalledTimes(1);
  });

  it("verifies one uniquely anchored external registration with an independent admin action", async () => {
    const actionable = externalVerificationDetail();
    const mutate = vi.fn().mockRejectedValue(new ApiError(422, "明确拒绝测试", {}));
    const client = adapter({
      loadAccess: vi.fn().mockResolvedValue({ ...access(), can_verify_external: true }),
      list: vi.fn().mockResolvedValue(page(actionable)),
      detail: vi.fn().mockResolvedValue(actionable),
      mutate,
    });
    await renderReady(client);
    const panel = await openDetail();
    fireEvent.click(within(panel).getByRole("button", { name: "独立复核外部审批证据" }));
    const process = await screen.findByRole("dialog", { name: "独立复核外部审批证据" });
    expect(process.textContent).toContain("STAR-20260901-001");
    expect(process.textContent).toContain("必须由不同的蔚来总部管理员执行");
    fireEvent.click(within(process).getByRole("button", { name: "复核拒绝" }));
    fireEvent.change(within(process).getByLabelText("审批处理意见"), { target: { value: "证据不可核验" } });
    fireEvent.click(within(process).getByRole("button", { name: "确认处理" }));
    await waitFor(() => expect(mutate).toHaveBeenCalledTimes(1));
    expect(mutate.mock.calls[0][0]).toMatchObject({
      action: "verify_external_approval",
      path: `/v1/material-requests/${REQUEST_ID}/approval-steps/${STEP_3_ID}/external-evidence/${REGISTRATION_ID}/verification`,
      expected_version: 4,
      body: {
        expected_request_version: 4,
        expected_step_version: 1,
        decision: "reject",
        comment: "证据不可核验",
      },
    });
  });

  it("allows returned requests to amend and resubmit through immutable revision transport", async () => {
    const returned = returnedDetail();
    const client = adapter({
      list: vi.fn().mockResolvedValue(page(returned)),
      detail: vi.fn().mockResolvedValue(returned),
      loadDraftForEdit: vi.fn().mockResolvedValue({
        schema_version: "1.0",
        request_id: REQUEST_ID,
        request_version: 2,
        draft: draft(),
      }),
    });
    await renderReady(client);
    const panel = await openDetail();
    expect(within(panel).getByText(/按退回意见修改后重新提交/)).toBeTruthy();
    expect(within(panel).getByRole("button", { name: "提交前确认" })).toBeTruthy();
    fireEvent.click(within(panel).getByRole("button", { name: "编辑草稿" }));
    expect(await screen.findByRole("dialog", { name: "编辑需求草稿" })).toBeTruthy();
    expect((screen.getByLabelText("联系电话") as HTMLInputElement).value).toBe(RAW_MOBILE);
  });

  it("states the reviewed transport and memory-only plaintext boundary", async () => {
    await renderReady(adapter());
    expect(screen.getByText(/正式 V1\.0 客户端路由已接线/)).toBeTruthy();
    expect(screen.getByText(/生产写入仍受服务端写 gate 与 runtime ACL 控制/)).toBeTruthy();
    expect(screen.getByText(/明文草稿仅驻留当前页面内存/)).toBeTruthy();
    expect(document.body.textContent).not.toContain("transport 尚未安全接入");
  });
});
