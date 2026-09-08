// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { StrictMode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../api";
import type { FormalFileUploadClient } from "../FormalFileUploadField";
import type { FormalFilePurpose, FormalUploadFile } from "../formalFileUpload";
import type { FormalMaterialRequestAdapter } from "../formalMaterialRequestAdapter";
import {
  createSupplyRecoveryStore,
  recoverSupplyCommand,
  type SupplySentinel,
} from "../materialRequestSupplyRecovery";
import {
  createMaterialRequestLifecycleRecoveryStore,
  type MaterialRequestLifecycleRecoveryStore,
} from "../materialRequestLifecycleRecovery";
import {
  createAllocationRecoveryStore,
  type AllocationRecoveryStore,
} from "../materialRequestAllocationRecovery";
import { createReservationRecoveryStore } from "../materialRequestReservationRecovery";
import { reservationSentinel } from "../materialRequestReservationTestFixtures";
import FormalMaterialRequestsPage from "./FormalMaterialRequests";

const REQUEST_ID = "10000000-0000-4000-8000-000000000001";
const LINE_ID = "20000000-0000-4000-8000-000000000001";
const PERSON_ID = "30000000-0000-4000-8000-000000000001";
const OTHER_PERSON_ID = "30000000-0000-4000-8000-000000000002";
const ORG_ID = "40000000-0000-4000-8000-000000000001";
const WORK_ORDER_ID = "45000000-0000-4000-8000-000000000001";
const WORK_ORDER_2_ID = "45000000-0000-4000-8000-000000000002";
const WORK_ORDER_3_ID = "45000000-0000-4000-8000-000000000003";
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
const TRACE_ID = "web-12345678";

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

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

const SUPPLY_ID = "d0000000-0000-4000-8000-000000000001";
function supplyReadyDetail(): any {
  const value = approvedDetail();
  value.allowed_actions = ["create_supply_task"];
  return value;
}
function withSupplyPlan(before: any, status = "open", taskVersion = 0): any {
  return {
    ...before, request_version: before.request_version + 1,
    supply_tasks: [{ id: SUPPLY_ID, task_no: "SUPPLY-20260905-001", request_line_id: before.lines[0].request_line_id,
      substitution_decision_id: null, supply_type: "star_replenishment", reference_no: null,
      expected_qty: "1.001", original_equivalent_qty: "1.001", expected_date: null,
      status, version: taskVersion, created_at: "2026-09-05T08:00:00Z", updated_at: "2026-09-05T08:00:00Z",
      allowed_actions: ["cancelled", "closed_no_supply"].includes(status) ? [] : ["update_supply_task", "cancel_supply_task"] }],
  };
}
function supplyResponse(value: any, action = "create_supply_task"): any {
  const task = value.supply_tasks[0];
  return {
    schema_version: "1.0", request_id: value.request_id, action, request_version: value.request_version,
    revision_id: value.current_revision_id, revision_no: value.current_revision_no,
    approval_instance_id: value.approval_instance.instance_id, approval_attempt_no: value.approval_instance.attempt_no,
    current_step_id: null, states: value.states, idempotency_replayed: false,
    supply_task_id: task.id, task_no: task.task_no, task_status: task.status, task_version: task.version,
  };
}

function supplyRecoveryFixture() {
  const before = supplyReadyDetail();
  const after = withSupplyPlan(before);
  const pending: SupplySentinel = {
    v: 1, kind: "material_request_supply", x_request_id: "supply-recovery-guard-1234",
    person_id: PERSON_ID, authorization_version: 1, request_id: REQUEST_ID,
    action: "create_supply_task", request_version: before.request_version,
    task_id: null, task_version: null,
  };
  const store = createSupplyRecoveryStore(sessionStorage);
  store.persist(pending);
  const { schema_version: _schema, idempotency_replayed: _replay, ...command } = supplyResponse(after);
  const confirmed = {
    schema_version: "1.0", lookup_status: "confirmed",
    command: { ...command, occurred_at: "2026-09-05T08:00:00Z" },
  };
  const client = adapter({
    list: vi.fn().mockResolvedValue(page(after)),
    detail: vi.fn().mockResolvedValue(after),
    supplyCommandStatus: vi.fn().mockResolvedValue(confirmed),
  });
  return { pending, store, before, after, client, confirmed };
}

function allocationRecoveryFixture(): { store: AllocationRecoveryStore; client: FormalMaterialRequestAdapter } {
  const before = supplyReadyDetail();
  const store = createAllocationRecoveryStore(sessionStorage);
  store.persist({
    v: 1,
    kind: "material_request_allocation",
    x_request_id: "allocation-recovery-1234",
    person_id: PERSON_ID,
    authorization_version: 1,
    request_id: REQUEST_ID,
    request_line_id: LINE_ID,
    request_version: before.request_version,
    source_stock_account_id: MATERIAL_2_ID,
    allocated_qty: "1.000",
    source_balance_version: 3,
    source_ledger_cursor: 8,
  });
  const client = adapter({
    loadAccess: vi.fn().mockResolvedValue({ ...access(), can_read_allocation_options: true }),
    list: vi.fn().mockResolvedValue(page(before)),
    detail: vi.fn().mockResolvedValue(before),
    loadIdentityNoReplay: vi.fn().mockResolvedValue({
      schema_version: "1.0", person_id: PERSON_ID, authorization_version: 1,
    }),
    loadAccessNoReplay: vi.fn().mockResolvedValue({ ...access(), can_read_allocation_options: true }),
    detailNoReplay: vi.fn().mockResolvedValue(before),
    allocationCommandStatusNoReplay: vi.fn().mockResolvedValue({
      schema_version: "1.0", lookup_status: "not_observed", command: null,
    }),
  });
  return { store, client };
}

function confirmedLifecycleStatus(action: "withdraw" | "cancel", value: any) {
  return {
    schema_version: "1.0",
    lookup_status: "confirmed",
    command: {
      action,
      request_id: value.request_id,
      request_version: value.request_version,
      revision_id: value.current_revision_id,
      revision_no: value.current_revision_no,
      approval_instance_id: value.approval_instance.instance_id,
      approval_attempt_no: value.approval_instance.attempt_no,
      current_step_id: value.approval_instance.current_step_id,
      states: value.states,
      occurred_at: value.updated_at,
    },
  };
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
    can_read_allocation_options: false,
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

function workOrderOption(
  workOrderId = WORK_ORDER_ID,
  workOrderNo = "WO-20260901-0001",
) {
  return {
    work_order_id: workOrderId,
    work_order_no: workOrderNo,
    status: "active",
    source_system_code: "starcharge_oam",
    source_external_id: `external-${workOrderNo}`,
    source_version: "v17",
    source_updated_at: "2026-09-01T07:30:00+08:00",
    synced_at: "2026-09-01T07:31:00+08:00",
    freshness_status: "fresh",
  };
}

function workOrderPage(
  items = [workOrderOption()],
  nextAfterId: string | null = null,
) {
  return {
    schema_version: "1.0",
    person_id: PERSON_ID,
    authorization_version: 1,
    items,
    next_after_id: nextAfterId,
  };
}

function workOrderDetail(item = workOrderOption()) {
  return {
    schema_version: "1.0",
    person_id: PERSON_ID,
    authorization_version: 1,
    item,
  };
}

function adapter(overrides: Partial<FormalMaterialRequestAdapter> = {}): FormalMaterialRequestAdapter {
  return {
    loadIdentity: vi.fn().mockResolvedValue({
      schema_version: "1.0",
      person_id: PERSON_ID,
      authorization_version: 1,
    }),
    loadAccess: vi.fn().mockResolvedValue(access()),
    lifecycleCommandStatus: vi.fn().mockResolvedValue({
      schema_version: "1.0",
      lookup_status: "not_observed",
      command: null,
    }),
    supplyCommandStatus: vi.fn().mockResolvedValue({
      schema_version: "1.0", lookup_status: "not_observed", command: null,
    }),
    allocationCommandStatus: vi.fn().mockResolvedValue({
      schema_version: "1.0", lookup_status: "not_observed", command: null,
    }),
    list: vi.fn().mockResolvedValue(page()),
    detail: vi.fn().mockResolvedValue(detail()),
    loadDraftForEdit: vi.fn().mockResolvedValue({
      schema_version: "1.0",
      request_id: REQUEST_ID,
      request_version: 0,
      draft: draft(),
    }),
    listWorkOrderOptions: vi.fn().mockResolvedValue(workOrderPage()),
    workOrderOptionDetail: vi.fn().mockResolvedValue(workOrderDetail()),
    listMaterials: vi.fn().mockResolvedValue(catalogPage()),
    listAllocationOptions: vi.fn().mockResolvedValue({
      schema_version: "1.0",
      request_id: REQUEST_ID,
      request_line_id: STEP_1_ID,
      request_version: 1,
      current_revision_id: STEP_1_ID,
      current_revision_no: 1,
      material_id: MATERIAL_ID,
      final_approved_qty: "0",
      cancelled_qty: "0",
      allocatable_qty: "0",
      projection_status: "ready",
      opening_balance_status: "established",
      projected_at: null,
      ledger_cursor: 0,
      items: [],
    }),
    createAllocation: vi.fn(),
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

async function renderReady(
  client: FormalMaterialRequestAdapter,
  uploads?: FormalFileUploadClient,
  lifecycleRecoveryStore?: MaterialRequestLifecycleRecoveryStore,
): Promise<void> {
  render(<FormalMaterialRequestsPage
    adapter={client}
    fileUploadClient={uploads}
    lifecycleRecoveryStore={lifecycleRecoveryStore}
  />);
  expect((await screen.findAllByText("MR-20260901-0001")).length).toBeGreaterThan(0);
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

beforeEach(() => {
  sessionStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("formal material request PC vertical slice", () => {
  it("keeps the page write gate closed while an allocation sentinel is not observed", async () => {
    const { store, client } = allocationRecoveryFixture();
    await renderReady(client);

    expect(await screen.findByText(/暂未查到分配操作的确定结果/)).toBeTruthy();
    expect(client.allocationCommandStatusNoReplay).toHaveBeenCalledWith("allocation-recovery-1234");
    expect(store.read().kind).toBe("valid");
    expect((screen.getByRole("button", { name: "新建需求" }) as HTMLButtonElement).disabled).toBe(true);
    expect(client.createDraft).not.toHaveBeenCalled();
  });

  it("renders only masked list/detail projections and keeps approval/fulfillment axes separate", async () => {
    const client = adapter();
    await renderReady(client);
    expect(document.body.textContent).not.toContain(RAW_MOBILE);
    expect(document.body.textContent).not.toContain(RAW_ADDRESS);
    const panel = await openDetail();
    expect(within(panel).getByText(/\*{7}0000/)).toBeTruthy();
    expect(within(panel).getByLabelText("需求十个独立状态轴").children).toHaveLength(10);
    expect(within(panel).getByRole("heading", { name: "供给计划" })).toBeTruthy();
    expect(within(panel).queryByRole("button", { name: "新建供给计划" })).toBeNull();
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

  it("keeps the create intent pending when the exact reread loses the selected work-order binding", async () => {
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
    const client = adapter({ createDraft, detail: vi.fn().mockResolvedValue(detail()) });
    await renderReady(client);
    fireEvent.click(screen.getByRole("button", { name: "新建需求" }));
    fireEvent.click(screen.getByRole("button", { name: "选择关联 OAM 工单" }));
    const picker = await screen.findByRole("dialog", { name: "选择本人当前有效 OAM 工单" });
    fireEvent.click(await within(picker).findByRole("button", {
      name: "选择工单 WO-20260901-0001",
    }));
    await fillCreateForm();
    fireEvent.click(screen.getByRole("button", { name: "保存草稿" }));

    expect(await screen.findByText(/工单绑定与详情回读不一致/)).toBeTruthy();
    expect(screen.getByRole("dialog", { name: "新建需求草稿" })).toBeTruthy();
    expect(createDraft).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "保存草稿" }));
    await waitFor(() => expect(createDraft).toHaveBeenCalledTimes(2));
    expect(createDraft.mock.calls[1][0]).toBe(createDraft.mock.calls[0][0]);
  });

  it("searches, paginates, selects and explicitly clears only formal work-order options", async () => {
    const second = workOrderOption(WORK_ORDER_2_ID, "WO-20260901-0002");
    const listWorkOrderOptions = vi.fn()
      .mockResolvedValueOnce(workOrderPage([workOrderOption()], WORK_ORDER_2_ID))
      .mockResolvedValueOnce(workOrderPage([second]))
      .mockResolvedValueOnce(workOrderPage([second]));
    await renderReady(adapter({ listWorkOrderOptions }));
    fireEvent.click(screen.getByRole("button", { name: "新建需求" }));

    expect(screen.queryByLabelText("关联工单 UUID（可空）")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "选择关联 OAM 工单" }));
    const picker = await screen.findByRole("dialog", { name: "选择本人当前有效 OAM 工单" });
    expect(await within(picker).findByText("WO-20260901-0001")).toBeTruthy();
    fireEvent.click(within(picker).getByRole("button", { name: "加载更多正式工单" }));
    expect(await within(picker).findByText("WO-20260901-0002")).toBeTruthy();

    fireEvent.change(within(picker).getByLabelText("OAM 工单检索词"), {
      target: { value: "WO-20260901-0002" },
    });
    fireEvent.click(within(picker).getByRole("button", { name: "搜索正式工单投影" }));
    await waitFor(() => expect(listWorkOrderOptions).toHaveBeenCalledTimes(3));
    expect(listWorkOrderOptions.mock.calls).toEqual([
      ["", null],
      ["", WORK_ORDER_2_ID],
      ["WO-20260901-0002", null],
    ]);
    expect(within(picker).queryByText("WO-20260901-0001")).toBeNull();
    fireEvent.click(within(picker).getByRole("button", { name: "选择工单 WO-20260901-0002" }));

    const selected = screen.getByRole("button", { name: "选择关联 OAM 工单" });
    expect(selected.textContent).toContain("WO-20260901-0002");
    expect(document.body.textContent).not.toContain(WORK_ORDER_2_ID);
    fireEvent.click(screen.getByRole("button", { name: "清除关联 OAM 工单" }));
    expect(selected.textContent).toBe("从本人当前有效工单中选择");
  });

  it("never selects a stale row while a newer work-order search is loading", async () => {
    const pendingSearch = deferred<unknown>();
    const second = workOrderOption(WORK_ORDER_2_ID, "WO-20260901-0002");
    const listWorkOrderOptions = vi.fn()
      .mockResolvedValueOnce(workOrderPage())
      .mockImplementationOnce(() => pendingSearch.promise);
    await renderReady(adapter({ listWorkOrderOptions }));
    fireEvent.click(screen.getByRole("button", { name: "新建需求" }));
    fireEvent.click(screen.getByRole("button", { name: "选择关联 OAM 工单" }));
    const picker = await screen.findByRole("dialog", { name: "选择本人当前有效 OAM 工单" });
    const oldChoice = await within(picker).findByRole("button", {
      name: "选择工单 WO-20260901-0001",
    });
    fireEvent.change(within(picker).getByLabelText("OAM 工单检索词"), {
      target: { value: "WO-20260901-0002" },
    });
    fireEvent.click(within(picker).getByRole("button", { name: "搜索正式工单投影" }));

    await waitFor(() => expect(within(picker).queryByRole("button", {
      name: "选择工单 WO-20260901-0001",
    })).toBeNull());
    fireEvent.click(oldChoice);
    expect(screen.getByRole("button", { name: "选择关联 OAM 工单" }).textContent)
      .toBe("从本人当前有效工单中选择");
    await act(async () => pendingSearch.resolve(workOrderPage([second])));
    const newChoice = await within(picker).findByRole("button", {
      name: "选择工单 WO-20260901-0002",
    });
    fireEvent.click(newChoice);
    expect(screen.getByRole("button", { name: "选择关联 OAM 工单" }).textContent)
      .toContain("WO-20260901-0002");
  });

  it("invalidates results and every cursor as soon as the work-order query changes", async () => {
    const staleSearch = deferred<unknown>();
    const listWorkOrderOptions = vi.fn()
      .mockResolvedValueOnce(workOrderPage([workOrderOption()], WORK_ORDER_2_ID))
      .mockImplementationOnce(() => staleSearch.promise)
      .mockResolvedValueOnce(workOrderPage([
        workOrderOption(WORK_ORDER_3_ID, "WO-20260901-0003"),
      ]));
    await renderReady(adapter({ listWorkOrderOptions }));
    fireEvent.click(screen.getByRole("button", { name: "新建需求" }));
    fireEvent.click(screen.getByRole("button", { name: "选择关联 OAM 工单" }));
    const picker = await screen.findByRole("dialog", { name: "选择本人当前有效 OAM 工单" });
    expect(await within(picker).findByText("WO-20260901-0001")).toBeTruthy();
    const staleLoadMore = within(picker).getByRole("button", { name: "加载更多正式工单" });

    fireEvent.change(within(picker).getByLabelText("OAM 工单检索词"), {
      target: { value: "WO-NEW" },
    });
    expect(within(picker).queryByText("WO-20260901-0001")).toBeNull();
    expect(within(picker).queryByRole("button", { name: "加载更多正式工单" })).toBeNull();
    fireEvent.click(staleLoadMore);
    expect(listWorkOrderOptions).toHaveBeenCalledTimes(1);

    fireEvent.click(within(picker).getByRole("button", { name: "搜索正式工单投影" }));
    await waitFor(() => expect(listWorkOrderOptions).toHaveBeenCalledTimes(2));
    fireEvent.change(within(picker).getByLabelText("OAM 工单检索词"), {
      target: { value: "WO-LATEST" },
    });
    await act(async () => staleSearch.resolve(workOrderPage([
      workOrderOption(WORK_ORDER_2_ID, "WO-20260901-0002"),
    ], WORK_ORDER_3_ID)));
    expect(within(picker).queryByText("WO-20260901-0002")).toBeNull();
    expect(within(picker).queryByRole("button", { name: "加载更多正式工单" })).toBeNull();

    fireEvent.click(within(picker).getByRole("button", { name: "搜索正式工单投影" }));
    expect(await within(picker).findByText("WO-20260901-0003")).toBeTruthy();
    expect(listWorkOrderOptions.mock.calls).toEqual([
      ["", null],
      ["WO-NEW", null],
      ["WO-LATEST", null],
    ]);
  });

  it("ignores reverse-order work-order responses after close and reopen", async () => {
    const oldRead = deferred<unknown>();
    const newRead = deferred<unknown>();
    const listWorkOrderOptions = vi.fn()
      .mockImplementationOnce(() => oldRead.promise)
      .mockImplementationOnce(() => newRead.promise);
    await renderReady(adapter({ listWorkOrderOptions }));
    fireEvent.click(screen.getByRole("button", { name: "新建需求" }));
    fireEvent.click(screen.getByRole("button", { name: "选择关联 OAM 工单" }));
    const oldPicker = await screen.findByRole("dialog", { name: "选择本人当前有效 OAM 工单" });
    await waitFor(() => expect(listWorkOrderOptions).toHaveBeenCalledTimes(1));
    fireEvent.click(within(oldPicker).getByRole("button", { name: "关闭" }));
    fireEvent.click(screen.getByRole("button", { name: "选择关联 OAM 工单" }));
    const currentPicker = await screen.findByRole("dialog", { name: "选择本人当前有效 OAM 工单" });
    await waitFor(() => expect(listWorkOrderOptions).toHaveBeenCalledTimes(2));

    const second = workOrderOption(WORK_ORDER_2_ID, "WO-20260901-0002");
    await act(async () => newRead.resolve(workOrderPage([second])));
    expect(await within(currentPicker).findByText("WO-20260901-0002")).toBeTruthy();
    await act(async () => oldRead.resolve(workOrderPage()));
    expect(within(currentPicker).queryByText("WO-20260901-0001")).toBeNull();
    expect(within(currentPicker).getByText("WO-20260901-0002")).toBeTruthy();
  });

  it("explicit clear closes the picker and invalidates its in-flight response", async () => {
    const pendingRead = deferred<unknown>();
    const listWorkOrderOptions = vi.fn()
      .mockResolvedValueOnce(workOrderPage())
      .mockImplementationOnce(() => pendingRead.promise);
    await renderReady(adapter({ listWorkOrderOptions }));
    fireEvent.click(screen.getByRole("button", { name: "新建需求" }));
    fireEvent.click(screen.getByRole("button", { name: "选择关联 OAM 工单" }));
    let picker = await screen.findByRole("dialog", { name: "选择本人当前有效 OAM 工单" });
    fireEvent.click(await within(picker).findByRole("button", {
      name: "选择工单 WO-20260901-0001",
    }));
    fireEvent.click(screen.getByRole("button", { name: "选择关联 OAM 工单" }));
    picker = await screen.findByRole("dialog", { name: "选择本人当前有效 OAM 工单" });
    await waitFor(() => expect(listWorkOrderOptions).toHaveBeenCalledTimes(2));

    fireEvent.click(screen.getByRole("button", { name: "清除关联 OAM 工单" }));
    expect(screen.queryByRole("dialog", { name: "选择本人当前有效 OAM 工单" })).toBeNull();
    expect(screen.getByRole("button", { name: "选择关联 OAM 工单" }).textContent)
      .toBe("从本人当前有效工单中选择");
    await act(async () => pendingRead.resolve(workOrderPage([
      workOrderOption(WORK_ORDER_2_ID, "WO-20260901-0002"),
    ])));
    expect(screen.queryByRole("dialog", { name: "选择本人当前有效 OAM 工单" })).toBeNull();
    expect(document.body.textContent).not.toContain("WO-20260901-0002");
  });

  it("fails closed immediately when a work-order cursor returns to its history", async () => {
    const listWorkOrderOptions = vi.fn()
      .mockResolvedValueOnce(workOrderPage([workOrderOption()], WORK_ORDER_2_ID))
      .mockResolvedValueOnce(workOrderPage([], WORK_ORDER_3_ID))
      .mockResolvedValueOnce(workOrderPage([], WORK_ORDER_2_ID));
    await renderReady(adapter({ listWorkOrderOptions }));
    fireEvent.click(screen.getByRole("button", { name: "新建需求" }));
    fireEvent.click(screen.getByRole("button", { name: "选择关联 OAM 工单" }));
    const picker = await screen.findByRole("dialog", { name: "选择本人当前有效 OAM 工单" });
    await within(picker).findByText("WO-20260901-0001");
    fireEvent.click(within(picker).getByRole("button", { name: "加载更多正式工单" }));
    await waitFor(() => expect(listWorkOrderOptions).toHaveBeenCalledTimes(2));
    fireEvent.click(within(picker).getByRole("button", { name: "加载更多正式工单" }));

    expect(await within(picker).findByText(/游标循环，已失败关闭/)).toBeTruthy();
    expect(within(picker).queryByRole("button", { name: "加载更多正式工单" })).toBeNull();
    expect(listWorkOrderOptions).toHaveBeenCalledTimes(3);
  });

  it("maps a selected formal work order to the exact draft write contract", async () => {
    const linked = detail();
    linked.work_order_id = WORK_ORDER_ID;
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
    const uploads = uploadClient("request_attachment");
    await renderReady(adapter({
      createDraft,
      detail: vi.fn().mockResolvedValue(linked),
    }), uploads);
    fireEvent.click(screen.getByRole("button", { name: "新建需求" }));
    fireEvent.click(screen.getByRole("button", { name: "选择关联 OAM 工单" }));
    const picker = await screen.findByRole("dialog", { name: "选择本人当前有效 OAM 工单" });
    fireEvent.click(await within(picker).findByRole("button", { name: "选择工单 WO-20260901-0001" }));
    await fillCreateForm(true);
    fireEvent.click(screen.getByRole("button", { name: "保存草稿" }));

    await waitFor(() => expect(createDraft).toHaveBeenCalledTimes(1));
    expect(createDraft.mock.calls[0][0].body).toEqual({
      ...draft(),
      work_order_id: WORK_ORDER_ID,
    });
  });

  it("locks work-order selection and clear while a draft write remains unconfirmed", async () => {
    const createDraft = vi.fn().mockRejectedValue(new TypeError("network uncertain"));
    const listWorkOrderOptions = vi.fn().mockResolvedValue(workOrderPage());
    const uploads = uploadClient("request_attachment");
    await renderReady(adapter({ createDraft, listWorkOrderOptions }), uploads);
    fireEvent.click(screen.getByRole("button", { name: "新建需求" }));
    fireEvent.click(screen.getByRole("button", { name: "选择关联 OAM 工单" }));
    const picker = await screen.findByRole("dialog", { name: "选择本人当前有效 OAM 工单" });
    fireEvent.click(await within(picker).findByRole("button", {
      name: "选择工单 WO-20260901-0001",
    }));
    await fillCreateForm(true);
    fireEvent.click(screen.getByRole("button", { name: "保存草稿" }));
    expect(await screen.findByText(/创建结果仍未确认/)).toBeTruthy();

    const choose = screen.getByRole("button", { name: "选择关联 OAM 工单" }) as HTMLButtonElement;
    const clear = screen.getByRole("button", { name: "清除关联 OAM 工单" }) as HTMLButtonElement;
    expect(choose.disabled).toBe(true);
    expect(clear.disabled).toBe(true);
    fireEvent.click(clear);
    fireEvent.click(choose);
    expect(choose.textContent).toContain("WO-20260901-0001");
    expect(screen.queryByRole("dialog", { name: "选择本人当前有效 OAM 工单" })).toBeNull();
    expect(listWorkOrderOptions).toHaveBeenCalledTimes(1);
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

  it("keeps the update intent pending when reread changes the selected work-order binding", async () => {
    const linkedDraft = { ...draft(), work_order_id: WORK_ORDER_ID };
    const linkedDetail = detail();
    linkedDetail.work_order_id = WORK_ORDER_ID;
    const wrongReread = detail(1);
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
    const client = adapter({
      list: vi.fn().mockResolvedValue(page(linkedDetail)),
      detail: vi.fn()
        .mockResolvedValueOnce(linkedDetail)
        .mockResolvedValue(wrongReread),
      loadDraftForEdit: vi.fn().mockResolvedValue({
        schema_version: "1.0",
        request_id: REQUEST_ID,
        request_version: 0,
        draft: linkedDraft,
      }),
      mutate,
    });
    await renderReady(client);
    const panel = await openDetail();
    fireEvent.click(within(panel).getByRole("button", { name: "编辑草稿" }));
    const editor = await screen.findByRole("dialog", { name: "编辑需求草稿" });
    fireEvent.click(within(editor).getByRole("button", { name: "保存修改" }));

    expect(await screen.findByText(/工单绑定与详情回读不一致/)).toBeTruthy();
    expect(screen.getByRole("dialog", { name: "编辑需求草稿" })).toBeTruthy();
    expect(mutate).toHaveBeenCalledTimes(1);
    fireEvent.click(within(editor).getByRole("button", { name: "保存修改" }));
    await waitFor(() => expect(mutate).toHaveBeenCalledTimes(2));
    expect(mutate.mock.calls[1][0]).toBe(mutate.mock.calls[0][0]);
  });

  it("restores a linked work order only through its exact formal detail projection", async () => {
    const linkedDraft = { ...draft(), work_order_id: WORK_ORDER_ID };
    const linkedDetail = detail();
    linkedDetail.work_order_id = WORK_ORDER_ID;
    const workOrderOptionDetail = vi.fn().mockResolvedValue(workOrderDetail());
    const client = adapter({
      loadAccess: vi.fn().mockResolvedValue(access(false)),
      list: vi.fn().mockResolvedValue(page(linkedDetail)),
      detail: vi.fn().mockResolvedValue(linkedDetail),
      loadDraftForEdit: vi.fn().mockResolvedValue({
        schema_version: "1.0",
        request_id: REQUEST_ID,
        request_version: 0,
        draft: linkedDraft,
      }),
      workOrderOptionDetail,
    });
    await renderReady(client);
    const panel = await openDetail();
    fireEvent.click(within(panel).getByRole("button", { name: "编辑草稿" }));

    const editor = await screen.findByRole("dialog", { name: "编辑需求草稿" });
    expect(workOrderOptionDetail).toHaveBeenCalledWith(WORK_ORDER_ID);
    expect(within(editor).getByRole("button", { name: "选择关联 OAM 工单" }).textContent)
      .toContain("WO-20260901-0001");
    expect(within(editor).getByLabelText("已选工单最小来源证据").textContent)
      .toContain("starcharge_oam · 外部锚点 external-WO-20260901-0001 · 版本 v17");
    expect(within(editor).getByLabelText("已选工单最小来源证据").textContent)
      .toContain("fresh");
    expect(within(editor).queryByLabelText("关联工单 UUID（可空）")).toBeNull();
  });

  it("opens a server-404 work-order reference as unavailable and blocks save until explicit clear", async () => {
    const linkedDraft = { ...draft(), work_order_id: WORK_ORDER_ID };
    const linkedDetail = detail();
    linkedDetail.work_order_id = WORK_ORDER_ID;
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
    const client = adapter({
      list: vi.fn().mockResolvedValue(page(linkedDetail)),
      detail: vi.fn().mockResolvedValueOnce(linkedDetail).mockResolvedValueOnce(after),
      loadDraftForEdit: vi.fn().mockResolvedValue({
        schema_version: "1.0",
        request_id: REQUEST_ID,
        request_version: 0,
        draft: linkedDraft,
      }),
      workOrderOptionDetail: vi.fn().mockRejectedValue(
        new ApiError(404, "当前可选 OAM 工单不存在", {}),
      ),
      mutate,
    });
    await renderReady(client);
    const panel = await openDetail();
    fireEvent.click(within(panel).getByRole("button", { name: "编辑草稿" }));
    const editor = await screen.findByRole("dialog", { name: "编辑需求草稿" });
    expect(within(editor).getByRole("alert").textContent).toContain("本人草稿原有关联工单当前不可选");
    expect(editor.textContent).not.toContain(WORK_ORDER_ID);
    expect(editor.textContent).not.toContain("WO-20260901-0001");

    const blockedSave = within(editor).getByRole("button", {
      name: "请先处理不可选工单",
    }) as HTMLButtonElement;
    expect(blockedSave.disabled).toBe(true);
    fireEvent.click(blockedSave);
    expect(mutate).not.toHaveBeenCalled();

    fireEvent.click(within(editor).getByRole("button", { name: "清除关联 OAM 工单" }));
    expect(within(editor).queryByRole("alert")).toBeNull();
    expect(within(editor).getByRole("button", { name: "选择关联 OAM 工单" }).textContent)
      .toBe("从本人当前有效工单中选择");
    fireEvent.click(within(editor).getByRole("button", { name: "保存修改" }));
    await waitFor(() => expect(mutate).toHaveBeenCalledTimes(1));
    expect(mutate.mock.calls[0][0].body.work_order_id).toBeNull();
  });

  it("replaces an unavailable draft reference only from the formal option list", async () => {
    const linkedDraft = { ...draft(), work_order_id: WORK_ORDER_ID };
    const linkedDetail = detail();
    linkedDetail.work_order_id = WORK_ORDER_ID;
    const replacement = workOrderOption(WORK_ORDER_2_ID, "WO-20260901-0002");
    const mutate = vi.fn().mockRejectedValue(new ApiError(422, "明确拒绝测试", {}));
    const listWorkOrderOptions = vi.fn().mockResolvedValue(workOrderPage([replacement]));
    const client = adapter({
      loadAccess: vi.fn().mockResolvedValue(access(false)),
      list: vi.fn().mockResolvedValue(page(linkedDetail)),
      detail: vi.fn().mockResolvedValue(linkedDetail),
      loadDraftForEdit: vi.fn().mockResolvedValue({
        schema_version: "1.0",
        request_id: REQUEST_ID,
        request_version: 0,
        draft: linkedDraft,
      }),
      workOrderOptionDetail: vi.fn().mockRejectedValue(
        new ApiError(404, "当前可选 OAM 工单不存在", {}),
      ),
      listWorkOrderOptions,
      mutate,
    });
    await renderReady(client);
    const panel = await openDetail();
    fireEvent.click(within(panel).getByRole("button", { name: "编辑草稿" }));
    const editor = await screen.findByRole("dialog", { name: "编辑需求草稿" });
    fireEvent.click(within(editor).getByRole("button", { name: "选择关联 OAM 工单" }));
    const picker = await screen.findByRole("dialog", { name: "选择本人当前有效 OAM 工单" });
    fireEvent.click(await within(picker).findByRole("button", {
      name: "选择工单 WO-20260901-0002",
    }));
    expect(within(editor).queryByRole("alert")).toBeNull();
    fireEvent.click(within(editor).getByRole("button", { name: "保存修改" }));

    await waitFor(() => expect(mutate).toHaveBeenCalledTimes(1));
    expect(mutate.mock.calls[0][0].body.work_order_id).toBe(WORK_ORDER_2_ID);
    expect(listWorkOrderOptions).toHaveBeenCalledWith("", null);
  });

  it("fails edit closed when a 404 was not received from the server", async () => {
    const linkedDraft = { ...draft(), work_order_id: WORK_ORDER_ID };
    const linkedDetail = detail();
    linkedDetail.work_order_id = WORK_ORDER_ID;
    const listWorkOrderOptions = vi.fn();
    const workOrderOptionDetail = vi.fn().mockRejectedValue(new ApiError(404, "关联工单已不可用"));
    const client = adapter({
      list: vi.fn().mockResolvedValue(page(linkedDetail)),
      detail: vi.fn().mockResolvedValue(linkedDetail),
      loadDraftForEdit: vi.fn().mockResolvedValue({
        schema_version: "1.0",
        request_id: REQUEST_ID,
        request_version: 0,
        draft: linkedDraft,
      }),
      listWorkOrderOptions,
      workOrderOptionDetail,
    });
    await renderReady(client);
    const panel = await openDetail();
    fireEvent.click(within(panel).getByRole("button", { name: "编辑草稿" }));

    expect(await screen.findByText("关联工单已不可用")).toBeTruthy();
    expect(screen.queryByRole("dialog", { name: "编辑需求草稿" })).toBeNull();
    expect(screen.getByRole("dialog", { name: "正式需求详情" })).toBeTruthy();
    expect(workOrderOptionDetail).toHaveBeenCalledWith(WORK_ORDER_ID);
    expect(listWorkOrderOptions).not.toHaveBeenCalled();
  });

  it.each([
    ["401", new ApiError(401, "登录身份已失效", {})],
    ["403", new ApiError(403, "没有 update_draft 权限", {})],
    ["409", new ApiError(409, "授权版本已变化", {})],
    ["5xx", new ApiError(503, "正式工单投影暂不可用", {})],
    ["transport", new TypeError("network unavailable")],
  ])("does not convert a %s exact-detail failure into unavailable", async (_kind, failure) => {
    const linkedDraft = { ...draft(), work_order_id: WORK_ORDER_ID };
    const linkedDetail = detail();
    linkedDetail.work_order_id = WORK_ORDER_ID;
    const client = adapter({
      list: vi.fn().mockResolvedValue(page(linkedDetail)),
      detail: vi.fn().mockResolvedValue(linkedDetail),
      loadDraftForEdit: vi.fn().mockResolvedValue({
        schema_version: "1.0",
        request_id: REQUEST_ID,
        request_version: 0,
        draft: linkedDraft,
      }),
      workOrderOptionDetail: vi.fn().mockRejectedValue(failure),
    });
    await renderReady(client);
    const panel = await openDetail();
    fireEvent.click(within(panel).getByRole("button", { name: "编辑草稿" }));

    expect(await screen.findByText(failure.message)).toBeTruthy();
    expect(screen.queryByRole("dialog", { name: "编辑需求草稿" })).toBeNull();
    expect(screen.getByRole("dialog", { name: "正式需求详情" })).toBeTruthy();
  });

  it("does not convert an exact-detail contract error into unavailable", async () => {
    const linkedDraft = { ...draft(), work_order_id: WORK_ORDER_ID };
    const linkedDetail = detail();
    linkedDetail.work_order_id = WORK_ORDER_ID;
    const client = adapter({
      list: vi.fn().mockResolvedValue(page(linkedDetail)),
      detail: vi.fn().mockResolvedValue(linkedDetail),
      loadDraftForEdit: vi.fn().mockResolvedValue({
        schema_version: "1.0",
        request_id: REQUEST_ID,
        request_version: 0,
        draft: linkedDraft,
      }),
      workOrderOptionDetail: vi.fn().mockResolvedValue({
        ...workOrderDetail(),
        authorization_version: 2,
      }),
    });
    await renderReady(client);
    const panel = await openDetail();
    fireEvent.click(within(panel).getByRole("button", { name: "编辑草稿" }));

    expect(await screen.findByText(/工单选项身份或授权版本与当前页面不一致/)).toBeTruthy();
    expect(screen.queryByRole("dialog", { name: "编辑需求草稿" })).toBeNull();
    expect(screen.getByRole("dialog", { name: "正式需求详情" })).toBeTruthy();
  });

  it("discards an edit recovery response after the user closes its detail", async () => {
    const pendingDraft = deferred<unknown>();
    const loadDraftForEdit = vi.fn().mockImplementation(() => pendingDraft.promise);
    await renderReady(adapter({ loadDraftForEdit }));
    const panel = await openDetail();
    fireEvent.click(within(panel).getByRole("button", { name: "编辑草稿" }));
    await waitFor(() => expect(loadDraftForEdit).toHaveBeenCalledTimes(1));
    fireEvent.click(within(panel).getByRole("button", { name: "关闭" }));
    expect(screen.queryByRole("dialog", { name: "正式需求详情" })).toBeNull();

    await act(async () => pendingDraft.resolve({
      schema_version: "1.0",
      request_id: REQUEST_ID,
      request_version: 0,
      draft: draft(),
    }));
    expect(screen.queryByRole("dialog", { name: "编辑需求草稿" })).toBeNull();
    expect(document.body.textContent).not.toContain(RAW_MOBILE);
    expect(document.body.textContent).not.toContain(RAW_ADDRESS);
  });

  it("discards an edit recovery response after the adapter and access generation refresh", async () => {
    const pendingDraft = deferred<unknown>();
    const oldClient = adapter({
      loadDraftForEdit: vi.fn().mockImplementation(() => pendingDraft.promise),
    });
    const currentClient = adapter();
    const view = render(<FormalMaterialRequestsPage adapter={oldClient} />);
    expect((await screen.findAllByText("MR-20260901-0001")).length).toBeGreaterThan(0);
    const panel = await openDetail();
    fireEvent.click(within(panel).getByRole("button", { name: "编辑草稿" }));
    await waitFor(() => expect(oldClient.loadDraftForEdit).toHaveBeenCalledTimes(1));
    view.rerender(<FormalMaterialRequestsPage adapter={currentClient} />);
    await waitFor(() => expect(currentClient.loadAccess).toHaveBeenCalled());

    await act(async () => pendingDraft.resolve({
      schema_version: "1.0",
      request_id: REQUEST_ID,
      request_version: 0,
      draft: draft(),
    }));
    expect(screen.queryByRole("dialog", { name: "编辑需求草稿" })).toBeNull();
    expect(document.body.textContent).not.toContain(RAW_MOBILE);
    expect(document.body.textContent).not.toContain(RAW_ADDRESS);
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
    const response = {
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
    };
    const lifecycleStore = createMaterialRequestLifecycleRecoveryStore(sessionStorage, () => 1);
    const mutate = vi.fn().mockImplementation(async (intent) => {
      expect(lifecycleStore.read()).toMatchObject({
        kind: "valid",
        value: { x_request_id: intent.headers["X-Request-ID"] },
      });
      expect(sessionStorage.length).toBe(1);
      const rawSentinel = sessionStorage.getItem("cloud-oam-material-request-lifecycle-sentinel-v1") ?? "";
      expect(rawSentinel).not.toContain(intent.headers["Idempotency-Key"]);
      expect(rawSentinel).not.toContain(REQUEST_ID);
      expect(rawSentinel).not.toContain("申请信息需重新整理");
      return response;
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
    await renderReady(client, undefined, lifecycleStore);
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
    expect(lifecycleStore.read()).toEqual({ kind: "missing" });
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
      // Each loop entry models a separate browser tab; an unresolved sentinel from the prior
      // scenario must not be erased by component cleanup in production.
      sessionStorage.clear();
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

  it("recovers a confirmed hard-refresh sentinel only after fresh identity, access, status and exact detail", async () => {
    const after = cancelledDetail();
    const events: string[] = [];
    const lifecycleStore = createMaterialRequestLifecycleRecoveryStore(sessionStorage, () => 1);
    lifecycleStore.persist(TRACE_ID);
    const client = adapter({
      loadIdentity: vi.fn(async () => {
        events.push("GET /auth/me");
        return { schema_version: "1.0", person_id: PERSON_ID, authorization_version: 1 };
      }),
      loadAccess: vi.fn(async () => {
        events.push("GET /access/context");
        return { ...access(), can_cancel: true };
      }),
      lifecycleCommandStatus: vi.fn(async (xRequestId) => {
        events.push(`GET command-status ${xRequestId}`);
        return confirmedLifecycleStatus("cancel", after);
      }),
      detail: vi.fn(async (requestId) => {
        events.push(`GET detail ${requestId}`);
        return after;
      }),
      list: vi.fn(async () => {
        events.push("GET list");
        return page(after);
      }),
    });

    await renderReady(client, undefined, lifecycleStore);
    expect(await screen.findByText(/取消命令已从服务端事实恢复/)).toBeTruthy();
    expect(events).toEqual([
      "GET /auth/me",
      "GET /access/context",
      `GET command-status ${TRACE_ID}`,
      `GET detail ${REQUEST_ID}`,
      "GET list",
    ]);
    expect(lifecycleStore.read()).toEqual({ kind: "missing" });
    const panel = await screen.findByRole("dialog", { name: "正式需求详情" });
    expect(within(panel).getByLabelText("需求十个独立状态轴").textContent).toContain("cancelled");
  });

  it("single-flights hard-refresh recovery when React StrictMode replays effects", async () => {
    const after = withdrawnDetail();
    const lifecycleStore = createMaterialRequestLifecycleRecoveryStore(sessionStorage, () => 1);
    lifecycleStore.persist(TRACE_ID);
    const loadIdentity = vi.fn().mockResolvedValue({
      schema_version: "1.0",
      person_id: PERSON_ID,
      authorization_version: 1,
    });
    const loadAccess = vi.fn().mockResolvedValue({ ...access(), can_withdraw: true });
    const lifecycleCommandStatus = vi.fn().mockResolvedValue(confirmedLifecycleStatus("withdraw", after));
    const readDetail = vi.fn().mockResolvedValue(after);
    const client = adapter({
      loadIdentity,
      loadAccess,
      lifecycleCommandStatus,
      detail: readDetail,
      list: vi.fn().mockResolvedValue(page(after)),
    });

    render(<StrictMode><FormalMaterialRequestsPage
      adapter={client}
      lifecycleRecoveryStore={lifecycleStore}
    /></StrictMode>);
    expect(await screen.findByText(/撤回命令已从服务端事实恢复/)).toBeTruthy();
    expect(loadIdentity).toHaveBeenCalledTimes(1);
    expect(loadAccess).toHaveBeenCalledTimes(1);
    expect(lifecycleCommandStatus).toHaveBeenCalledTimes(1);
    expect(readDetail).toHaveBeenCalledTimes(1);
    expect(lifecycleStore.read()).toEqual({ kind: "missing" });
  });

  it("retains the hard-refresh sentinel when fresh identity and authorization drift", async () => {
    const before = submittedDetail();
    const lifecycleStore = createMaterialRequestLifecycleRecoveryStore(sessionStorage, () => 1);
    lifecycleStore.persist(TRACE_ID);
    const loadIdentity = vi.fn().mockResolvedValue({
      schema_version: "1.0",
      person_id: OTHER_PERSON_ID,
      authorization_version: 2,
    });
    const loadAccess = vi.fn().mockResolvedValue({ ...access(), can_withdraw: true });
    const lifecycleCommandStatus = vi.fn();
    const client = adapter({
      loadIdentity,
      loadAccess,
      lifecycleCommandStatus,
      list: vi.fn().mockResolvedValue(page(before)),
      detail: vi.fn().mockResolvedValue(before),
    });

    await renderReady(client, undefined, lifecycleStore);
    expect(loadIdentity).toHaveBeenCalledTimes(1);
    expect(loadAccess).toHaveBeenCalledTimes(1);
    expect(lifecycleCommandStatus).not.toHaveBeenCalled();
    expect(await screen.findByText(/新鲜登录身份与访问授权不一致/)).toBeTruthy();
    expect(lifecycleStore.read().kind).toBe("valid");
    const panel = await openDetail();
    expect(within(panel).getByRole("button", { name: "撤回申请" }).hasAttribute("disabled")).toBe(true);
  });

  it("retains not_observed across finite backoff and disables new lifecycle coordinates", async () => {
    const before = submittedDetail();
    const lifecycleStore = createMaterialRequestLifecycleRecoveryStore(sessionStorage, () => 1);
    lifecycleStore.persist(TRACE_ID);
    const lifecycleCommandStatus = vi.fn().mockResolvedValue({
      schema_version: "1.0",
      lookup_status: "not_observed",
      command: null,
    });
    const client = adapter({
      loadAccess: vi.fn().mockResolvedValue({ ...access(), can_withdraw: true }),
      lifecycleCommandStatus,
      list: vi.fn().mockResolvedValue(page(before)),
      detail: vi.fn().mockResolvedValue(before),
    });

    await renderReady(client, undefined, lifecycleStore);
    expect(lifecycleCommandStatus).toHaveBeenCalledTimes(3);
    expect(lifecycleCommandStatus).toHaveBeenNthCalledWith(1, TRACE_ID);
    expect(await screen.findByText(/服务端尚未观察到该请求坐标/)).toBeTruthy();
    expect(lifecycleStore.read().kind).toBe("valid");
    const panel = await openDetail();
    const withdraw = within(panel).getByRole("button", { name: "撤回申请" });
    expect(withdraw.hasAttribute("disabled")).toBe(true);
    fireEvent.click(withdraw);
    expect(screen.queryByRole("dialog", { name: "撤回需求" })).toBeNull();
  });

  it("retains a corrupt sentinel visibly and fails lifecycle buttons closed", async () => {
    sessionStorage.setItem("cloud-oam-material-request-lifecycle-sentinel-v1", "{bad-json");
    const loadIdentity = vi.fn();
    const lifecycleCommandStatus = vi.fn();
    const before = submittedDetail();
    const client = adapter({
      loadIdentity,
      lifecycleCommandStatus,
      loadAccess: vi.fn().mockResolvedValue({ ...access(), can_withdraw: true }),
      list: vi.fn().mockResolvedValue(page(before)),
      detail: vi.fn().mockResolvedValue(before),
    });

    await renderReady(client);
    expect(await screen.findByText(/生命周期恢复标记已损坏/)).toBeTruthy();
    expect(loadIdentity).not.toHaveBeenCalled();
    expect(lifecycleCommandStatus).not.toHaveBeenCalled();
    const panel = await openDetail();
    expect(within(panel).getByRole("button", { name: "撤回申请" }).hasAttribute("disabled")).toBe(true);
    expect(sessionStorage.getItem("cloud-oam-material-request-lifecycle-sentinel-v1")).toBe("{bad-json");
  });

  it("retains lifecycle coordinates on explicit authentication or authorization rejection", async () => {
    const before = submittedDetail();
    const lifecycleStore = createMaterialRequestLifecycleRecoveryStore(sessionStorage, () => 1);
    const rejected = vi.fn().mockRejectedValue(new ApiError(403, "授权版本已变化", {}));
    const client = adapter({
      loadAccess: vi.fn().mockResolvedValue({ ...access(), can_withdraw: true }),
      list: vi.fn().mockResolvedValue(page(before)),
      detail: vi.fn().mockResolvedValue(before),
      mutate: rejected,
    });

    await renderReady(client, undefined, lifecycleStore);
    const panel = await openDetail();
    fireEvent.click(within(panel).getByRole("button", { name: "撤回申请" }));
    const confirmation = await screen.findByRole("dialog", { name: "撤回需求" });
    fireEvent.change(within(confirmation).getByLabelText("撤回原因"), {
      target: { value: "申请信息需重新整理" },
    });
    fireEvent.click(within(confirmation).getByRole("button", { name: "确认撤回" }));

    await waitFor(() => expect(rejected).toHaveBeenCalledTimes(1));
    expect(lifecycleStore.read().kind).toBe("valid");
    expect(within(confirmation).getByText(/禁止生成新坐标或执行其他动作/)).toBeTruthy();
    expect(screen.getByText(/生命周期命令待核验/)).toBeTruthy();
  });

  it("clears the sentinel only for a definite server rejection and never sends when persistence fails", async () => {
    const before = submittedDetail();
    const lifecycleStore = createMaterialRequestLifecycleRecoveryStore(sessionStorage, () => 1);
    const rejected = vi.fn().mockRejectedValue(new ApiError(422, "明确拒绝测试", {}));
    let client = adapter({
      loadAccess: vi.fn().mockResolvedValue({ ...access(), can_withdraw: true }),
      list: vi.fn().mockResolvedValue(page(before)),
      detail: vi.fn().mockResolvedValue(before),
      mutate: rejected,
    });
    await renderReady(client, undefined, lifecycleStore);
    let panel = await openDetail();
    fireEvent.click(within(panel).getByRole("button", { name: "撤回申请" }));
    let confirmation = await screen.findByRole("dialog", { name: "撤回需求" });
    fireEvent.change(within(confirmation).getByLabelText("撤回原因"), {
      target: { value: "申请信息需重新整理" },
    });
    fireEvent.click(within(confirmation).getByRole("button", { name: "确认撤回" }));
    await waitFor(() => expect(rejected).toHaveBeenCalledTimes(1));
    expect(await within(confirmation).findByText("明确拒绝测试")).toBeTruthy();
    expect(lifecycleStore.read()).toEqual({ kind: "missing" });
    expect(screen.queryByText(/生命周期命令待核验/)).toBeNull();

    cleanup();
    const failedStore: MaterialRequestLifecycleRecoveryStore = {
      read: () => ({ kind: "missing" }),
      persist: () => { throw new ApiError(409, "storage denied"); },
      clear: () => { throw new Error("must not clear"); },
    };
    const neverSent = vi.fn();
    client = adapter({
      loadAccess: vi.fn().mockResolvedValue({ ...access(), can_withdraw: true }),
      list: vi.fn().mockResolvedValue(page(before)),
      detail: vi.fn().mockResolvedValue(before),
      mutate: neverSent,
    });
    await renderReady(client, undefined, failedStore);
    panel = await openDetail();
    fireEvent.click(within(panel).getByRole("button", { name: "撤回申请" }));
    confirmation = await screen.findByRole("dialog", { name: "撤回需求" });
    fireEvent.change(within(confirmation).getByLabelText("撤回原因"), {
      target: { value: "申请信息需重新整理" },
    });
    fireEvent.click(within(confirmation).getByRole("button", { name: "确认撤回" }));
    await waitFor(() => expect(within(confirmation).getByText(/生命周期 POST 未发送/)).toBeTruthy());
    expect(neverSent).not.toHaveBeenCalled();
    expect(screen.getByText(/当前页仍保留原内存意图并禁止生成新坐标/)).toBeTruthy();
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

  it("creates a supply plan with exact quantities and clears its sentinel only after task reread", async () => {
    const before = supplyReadyDetail();
    let current = before;
    const after = withSupplyPlan(before);
    const mutate = vi.fn(async (_intent: unknown) => { current = after; return supplyResponse(after); });
    const client = adapter({ list: vi.fn().mockResolvedValue(page(before)), detail: vi.fn(async () => current), mutate });
    await renderReady(client);
    const panel = await openDetail();
    fireEvent.click(within(panel).getByRole("button", { name: "新建供给计划" }));
    const form = await screen.findByRole("dialog", { name: "新建供给计划" });
    fireEvent.change(within(form).getByLabelText("供给计划数量"), { target: { value: "1.001" } });
    fireEvent.click(within(form).getByRole("button", { name: "确认保存供给计划" }));
    expect(await screen.findByText(/已保存并核验/)).toBeTruthy();
    expect(mutate).toHaveBeenCalledTimes(1);
    expect(mutate.mock.calls[0][0]).toMatchObject({
      action: "create_supply_task", path: `/v1/material-requests/${REQUEST_ID}/supply-tasks`,
      body: { expected_request_version: 5, expected_qty: "1.001", reference_no: null, expected_date: null },
    });
    expect(sessionStorage.getItem("cloud-oam-material-request-supply-sentinel-v1")).toBeNull();
    expect(current.states).toEqual(before.states);
    expect(client.supplyCommandStatus).not.toHaveBeenCalled();
  });

  it("clears the exact coordinate after a whitelisted supply POST rejection", async () => {
    const before = supplyReadyDetail();
    const rejection = new ApiError(409, "需求单版本已变化，请重新读取后再操作", {
      code: "material_request_version_conflict",
      category: "conflict",
    });
    const mutate = vi.fn().mockRejectedValue(rejection);
    const client = adapter({
      list: vi.fn().mockResolvedValue(page(before)),
      detail: vi.fn().mockResolvedValue(before),
      mutate,
    });
    await renderReady(client);
    const panel = await openDetail();
    fireEvent.click(within(panel).getByRole("button", { name: "新建供给计划" }));
    const form = await screen.findByRole("dialog", { name: "新建供给计划" });
    fireEvent.change(within(form).getByLabelText("供给计划数量"), { target: { value: "1.001" } });
    fireEvent.click(within(form).getByRole("button", { name: "确认保存供给计划" }));

    expect((await screen.findAllByText(/本地请求坐标已安全解除/)).length).toBeGreaterThan(0);
    expect(mutate).toHaveBeenCalledTimes(1);
    expect(sessionStorage.getItem("cloud-oam-material-request-supply-sentinel-v1")).toBeNull();
    expect((screen.getByRole("button", { name: "新建需求" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it.each([
    ["permission 403", new ApiError(403, "没有供给权限", {
      code: "material_request_supply_manage_forbidden", category: "forbidden",
    })],
    ["unknown 409", new ApiError(409, "未知冲突", {
      code: "unknown_conflict", category: "conflict",
    })],
  ])("retains the coordinate after a non-whitelisted supply POST rejection: %s", async (_name, rejection) => {
    const before = supplyReadyDetail();
    const mutate = vi.fn().mockRejectedValue(rejection);
    const client = adapter({
      list: vi.fn().mockResolvedValue(page(before)),
      detail: vi.fn().mockResolvedValue(before),
      mutate,
    });
    await renderReady(client);
    const panel = await openDetail();
    fireEvent.click(within(panel).getByRole("button", { name: "新建供给计划" }));
    const form = await screen.findByRole("dialog", { name: "新建供给计划" });
    fireEvent.change(within(form).getByLabelText("供给计划数量"), { target: { value: "1.001" } });
    fireEvent.click(within(form).getByRole("button", { name: "确认保存供给计划" }));

    await waitFor(() => expect(mutate).toHaveBeenCalledTimes(1));
    expect(sessionStorage.getItem("cloud-oam-material-request-supply-sentinel-v1")).not.toBeNull();
    expect((screen.getByRole("button", { name: "新建需求" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("retains a whitelisted rejected coordinate when identity changes before cleanup", async () => {
    const before = supplyReadyDetail();
    let switched = false;
    const loadIdentity = vi.fn(async () => ({
      schema_version: "1.0",
      person_id: switched ? OTHER_PERSON_ID : PERSON_ID,
      authorization_version: 1,
    }));
    const mutate = vi.fn(async () => {
      switched = true;
      throw new ApiError(409, "需求单版本已变化，请重新读取后再操作", {
        code: "material_request_version_conflict",
        category: "conflict",
      });
    });
    const client = adapter({
      loadIdentity,
      list: vi.fn().mockResolvedValue(page(before)),
      detail: vi.fn().mockResolvedValue(before),
      mutate,
    });
    await renderReady(client);
    const panel = await openDetail();
    fireEvent.click(within(panel).getByRole("button", { name: "新建供给计划" }));
    const form = await screen.findByRole("dialog", { name: "新建供给计划" });
    fireEvent.change(within(form).getByLabelText("供给计划数量"), { target: { value: "1.001" } });
    fireEvent.click(within(form).getByRole("button", { name: "确认保存供给计划" }));

    expect((await screen.findAllByText(/身份或权限已变化/)).length).toBeGreaterThan(0);
    expect(loadIdentity).toHaveBeenCalledTimes(2);
    expect(sessionStorage.getItem("cloud-oam-material-request-supply-sentinel-v1")).not.toBeNull();
    expect((screen.getByRole("button", { name: "新建需求" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("keeps an uncertain supply operation blocked across remount and confirms its historical result", async () => {
    const before = supplyReadyDetail();
    const mutate = vi.fn().mockRejectedValue(new Error("network uncertain"));
    const first = adapter({ list: vi.fn().mockResolvedValue(page(before)), detail: vi.fn().mockResolvedValue(before), mutate });
    const view = render(<FormalMaterialRequestsPage adapter={first} />);
    await screen.findByRole("button", { name: "查看" });
    const panel = await openDetail();
    fireEvent.click(within(panel).getByRole("button", { name: "新建供给计划" }));
    const form = await screen.findByRole("dialog", { name: "新建供给计划" });
    fireEvent.change(within(form).getByLabelText("供给计划数量"), { target: { value: "1.001" } });
    fireEvent.change(within(form).getByLabelText("供给参考号"), { target: { value: "PRIVATE-REFERENCE-001" } });
    fireEvent.change(within(form).getByLabelText("供给处理说明"), { target: { value: "private supply reason" } });
    fireEvent.click(within(form).getByRole("button", { name: "确认保存供给计划" }));
    await waitFor(() => expect(mutate).toHaveBeenCalledTimes(1));
    await screen.findAllByText(/network uncertain/);
    const raw = sessionStorage.getItem("cloud-oam-material-request-supply-sentinel-v1")!;
    const sentinel = JSON.parse(raw);
    expect(raw).not.toContain("PRIVATE-REFERENCE");
    expect(raw).not.toContain("private supply reason");
    expect(raw).not.toContain("Idempotency-Key");
    expect(raw).not.toContain(mutate.mock.calls[0][0].headers["Idempotency-Key"]);
    expect((screen.getByRole("button", { name: "新建需求" }) as HTMLButtonElement).disabled).toBe(true);
    view.unmount();

    const original = withSupplyPlan(before, "reference_registered");
    original.supply_tasks[0].reference_no = "PRIVATE-REFERENCE-001";
    const later = withSupplyPlan(original, "cancelled", 1);
    later.supply_tasks[0].reference_no = "PRIVATE-REFERENCE-001";
    const { schema_version: _schema, idempotency_replayed: _replay, ...command } = supplyResponse(original);
    const status = vi.fn().mockResolvedValue({ schema_version: "1.0", lookup_status: "confirmed",
      command: { ...command, occurred_at: "2026-09-05T08:00:00Z" } });
    const next = adapter({ list: vi.fn().mockResolvedValue(page(later)), detail: vi.fn().mockResolvedValue(later), supplyCommandStatus: status });
    render(<FormalMaterialRequestsPage adapter={next} />);
    await waitFor(() => expect(sessionStorage.getItem("cloud-oam-material-request-supply-sentinel-v1")).toBeNull());
    expect(status).toHaveBeenCalledWith(sentinel.x_request_id);
    expect(next.mutate).not.toHaveBeenCalled();
    expect(await screen.findByText("计划已取消")).toBeTruthy();
  });

  it("updates and cancels a supply task without editing its material quantity or advancing inventory", async () => {
    let current = withSupplyPlan(supplyReadyDetail());
    const originalStates = current.states;
    const mutate = vi.fn(async (intent: any) => {
      const task = current.supply_tasks[0];
      current = { ...current, request_version: current.request_version + 1, supply_tasks: [{
        ...task, reference_no: intent.body.reference_no, expected_date: intent.body.expected_date,
        status: intent.body.status, version: task.version + 1,
        allowed_actions: intent.body.status === "cancelled" ? [] : ["update_supply_task", "cancel_supply_task"],
      }] };
      return supplyResponse(current, intent.action);
    });
    const client = adapter({ list: vi.fn().mockResolvedValue(page(current)), detail: vi.fn(async () => current), mutate });
    await renderReady(client);
    const panel = await openDetail();
    fireEvent.click(within(panel).getByRole("button", { name: "更新计划" }));
    const updateForm = await screen.findByRole("dialog", { name: "更新供给计划" });
    expect((within(updateForm).getByLabelText("供给计划数量") as HTMLInputElement).disabled).toBe(true);
    expect((within(updateForm).getByLabelText("供给类型") as HTMLSelectElement).disabled).toBe(true);
    fireEvent.change(within(updateForm).getByLabelText("供给参考号"), { target: { value: "STAR-SUPPLY-002" } });
    fireEvent.change(within(updateForm).getByLabelText("供给计划状态"), { target: { value: "reference_registered" } });
    fireEvent.click(within(updateForm).getByRole("button", { name: "确认保存供给计划" }));
    await screen.findByText(/已保存并核验，当前为已登记参考号/);
    fireEvent.click(within(panel).getByRole("button", { name: "取消计划" }));
    const cancelForm = await screen.findByRole("dialog", { name: "取消供给计划" });
    fireEvent.click(within(cancelForm).getByRole("button", { name: "确认保存供给计划" }));
    await screen.findAllByText(/处理原因无效/);
    expect(mutate).toHaveBeenCalledTimes(1);
    fireEvent.change(within(cancelForm).getByLabelText("供给处理说明"), { target: { value: "改由已有库存解决" } });
    fireEvent.click(within(cancelForm).getByRole("button", { name: "确认保存供给计划" }));
    await screen.findByText(/已保存并核验，当前为计划已取消/);
    expect(mutate).toHaveBeenCalledTimes(2);
    expect(mutate.mock.calls[1][0]).toMatchObject({ action: "cancel_supply_task",
      path: `/v1/material-requests/${REQUEST_ID}/supply-tasks/${SUPPLY_ID}`,
      body: { expected_request_version: 7, expected_task_version: 1, status: "cancelled", comment: "改由已有库存解决" } });
    expect(current.states).toEqual(originalStates);
    expect(current.supply_tasks[0].expected_qty).toBe("1.001");
    expect(within(panel).queryByRole("button", { name: "更新计划" })).toBeNull();
    expect(sessionStorage.getItem("cloud-oam-material-request-supply-sentinel-v1")).toBeNull();
  });

  it("does not turn not-observed or changed identity into a replacement supply write", async () => {
    const pending = { v: 1, kind: "material_request_supply", x_request_id: "supply-unobserved-1234", person_id: PERSON_ID,
      authorization_version: 1, request_id: REQUEST_ID, action: "create_supply_task", request_version: 5, task_id: null, task_version: null };
    sessionStorage.setItem("cloud-oam-material-request-supply-sentinel-v1", JSON.stringify(pending));
    const before = supplyReadyDetail();
    const client = adapter({ list: vi.fn().mockResolvedValue(page(before)), detail: vi.fn().mockResolvedValue(before) });
    const view = render(<FormalMaterialRequestsPage adapter={client} />);
    expect(await screen.findByText(/暂未查到供给操作的确定结果/)).toBeTruthy();
    expect((screen.getByRole("button", { name: "新建需求" }) as HTMLButtonElement).disabled).toBe(true);
    expect(sessionStorage.getItem("cloud-oam-material-request-supply-sentinel-v1")).not.toBeNull();
    expect(client.mutate).not.toHaveBeenCalled();
    view.unmount();
    const changed = adapter({ loadIdentity: vi.fn().mockResolvedValue({ schema_version: "1.0", person_id: PERSON_ID, authorization_version: 2 }) });
    render(<FormalMaterialRequestsPage adapter={changed} />);
    expect(await screen.findByText(/登录身份或权限已变化，原供给操作/)).toBeTruthy();
    expect(changed.supplyCommandStatus).not.toHaveBeenCalled();
    expect(changed.mutate).not.toHaveBeenCalled();
    expect(sessionStorage.getItem("cloud-oam-material-request-supply-sentinel-v1")).not.toBeNull();
  });

  it.each(["identity_person", "identity_version", "access_person", "access_version", "access_revoked", "generation"])(
    "retains original supply coordinates if the final recovery guard changes: %s", async (change) => {
      const { pending, store, client } = supplyRecoveryFixture();
      const identity = { schema_version: "1.0", person_id: PERSON_ID, authorization_version: 1 };
      const afterIdentity = { ...identity,
        ...(change === "identity_person" ? { person_id: OTHER_PERSON_ID } : {}),
        ...(change === "identity_version" ? { authorization_version: 2 } : {}),
      };
      const afterAccess = { ...access(),
        ...(change === "access_person" ? { person_id: OTHER_PERSON_ID } : {}),
        ...(change === "access_version" ? { authorization_version: 2 } : {}),
        ...(change === "access_revoked" ? { can_read: false, can_create: false } : {}),
      };
      const loadIdentity = vi.fn().mockResolvedValueOnce(identity).mockResolvedValue(afterIdentity);
      const loadAccess = vi.fn().mockResolvedValueOnce(access()).mockResolvedValue(afterAccess);
      const clear = vi.fn((trace: string) => store.clear(trace));
      const guarded = { ...store, clear };
      const current = { ...client, loadIdentity, loadAccess };
      await expect(recoverSupplyCommand(current, guarded, pending, () => change !== "generation")).rejects.toThrow(/核验期间|核验页面/);
      expect(loadIdentity).toHaveBeenCalledTimes(2);
      expect(client.supplyCommandStatus).toHaveBeenCalledWith(pending.x_request_id);
      expect(client.detail).toHaveBeenCalledWith(REQUEST_ID);
      expect(clear).not.toHaveBeenCalled();
      expect(store.read()).toEqual({ kind: "valid", value: pending });
      expect(client.mutate).not.toHaveBeenCalled();
      expect(client.createDraft).not.toHaveBeenCalled();
    },
  );

  it("does not clear a recovery sentinel when its panel unmounts before the status read returns", async () => {
    const { pending, store, client, confirmed } = supplyRecoveryFixture();
    const status = deferred<unknown>();
    const supplyCommandStatus = vi.fn().mockReturnValue(status.promise);
    const clear = vi.fn((trace: string) => store.clear(trace));
    const current = { ...client, supplyCommandStatus };
    const view = render(<FormalMaterialRequestsPage adapter={current} supplyRecoveryStore={{ ...store, clear }} />);
    await waitFor(() => expect(supplyCommandStatus).toHaveBeenCalledTimes(1));
    view.unmount();
    await act(async () => { status.resolve(confirmed); await status.promise; });
    await waitFor(() => expect(client.detail).toHaveBeenCalledWith(REQUEST_ID));
    expect(clear).not.toHaveBeenCalled();
    expect(store.read()).toEqual({ kind: "valid", value: pending });
    expect(client.mutate).not.toHaveBeenCalled();
  });

  it("clears only after final identity, access and generation checks while retaining historical and current states", async () => {
    const { pending, store, client, after } = supplyRecoveryFixture();
    const later = cancelledDetail();
    later.request_version = after.request_version + 2;
    later.supply_tasks = [{ ...after.supply_tasks[0], status: "cancelled", version: 1, allowed_actions: [] }];
    const events: string[] = [];
    const current = {
      ...client,
      loadIdentity: vi.fn(async () => { events.push("identity"); return { schema_version: "1.0", person_id: PERSON_ID, authorization_version: 1 }; }),
      loadAccess: vi.fn(async () => { events.push("access"); return access(); }),
      supplyCommandStatus: vi.fn(async (trace: string) => { events.push("status"); return client.supplyCommandStatus(trace); }),
      detail: vi.fn(async () => { events.push("detail"); return later; }),
    };
    const guarded = { ...store, clear: (trace: string) => { events.push("clear"); store.clear(trace); } };
    const recovered = await recoverSupplyCommand(current, guarded, pending, () => {
      events.push("generation");
      expect(store.read().kind).toBe("valid");
      return true;
    });
    expect(events).toEqual(["identity", "access", "status", "detail", "identity", "access", "generation", "clear"]);
    expect(recovered.command.states.request_status).toBe("approved");
    expect(recovered.command.task_status).toBe("open");
    expect(recovered.detail.states.request_status).toBe("cancelled");
    expect(recovered.detail.supply_tasks[0].status).toBe("cancelled");
    expect(store.read()).toEqual({ kind: "missing" });
    expect(current.mutate).not.toHaveBeenCalled();
  });
  it.each(["corrupt", "unavailable"] as const)("blocks all page writes for %s reservation recovery storage", async (kind) => {
    const store = { read: () => ({ kind }), persist: vi.fn(), clear: vi.fn() };
    const client = adapter();
    render(<FormalMaterialRequestsPage adapter={client} reservationRecoveryStore={store} />);
    await waitFor(() => expect(client.list).toHaveBeenCalled());
    expect((screen.getByRole("button", { name: "新建需求" }) as HTMLButtonElement).disabled).toBe(true);
    expect(await screen.findByText(/预留操作结果待核验/)).toBeTruthy();
    expect(client.createDraft).not.toHaveBeenCalled(); expect(client.mutate).not.toHaveBeenCalled(); expect(store.clear).not.toHaveBeenCalled();
  });
  it("consults the durable reservation coordinate synchronously before opening a new write", async () => {
    const store = createReservationRecoveryStore(); const client = adapter();
    render(<FormalMaterialRequestsPage adapter={client} reservationRecoveryStore={store} />);
    const button = await screen.findByRole("button", { name: "新建需求" });
    await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false));
    store.persist(reservationSentinel());
    fireEvent.click(button);
    expect(screen.queryByRole("dialog", { name: "新建需求草稿" })).toBeNull();
    expect(client.createDraft).not.toHaveBeenCalled(); expect(client.mutate).not.toHaveBeenCalled();
    expect(store.read().kind).toBe("valid");
  });

});
