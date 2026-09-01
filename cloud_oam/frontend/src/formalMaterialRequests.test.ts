import { beforeEach, describe, expect, it } from "vitest";

import {
  MATERIAL_REQUEST_STATE_AXIS_FIELDS,
  MaterialRequestContractError,
  MaterialRequestCreateIntentConflictError,
  MaterialRequestCreateIntentRegistry,
  MaterialRequestIntentConflictError,
  MaterialRequestIntentRegistry,
  createMaterialRequestWriteHeaders,
  validateMaterialRequestCreateResult,
  validateMaterialRequestDetail,
  validateMaterialRequestDraftInput,
  validateMaterialRequestMutationResult,
  validateMaterialRequestPage,
} from "./formalMaterialRequests";

const REQUEST_ID = "10000000-0000-4000-8000-000000000001";
const OTHER_REQUEST_ID = "10000000-0000-4000-8000-000000000002";
const LINE_ID = "20000000-0000-4000-8000-000000000001";
const PERSON_ID = "30000000-0000-4000-8000-000000000001";
const ORG_ID = "40000000-0000-4000-8000-000000000001";
const MATERIAL_ID = "50000000-0000-4000-8000-000000000001";
const INSTANCE_ID = "60000000-0000-4000-8000-000000000001";
const INSTANCE_ID_2 = "60000000-0000-4000-8000-000000000002";
const SUPPLY_TASK_ID = "80000000-0000-4000-8000-000000000001";
const REVISION_ID = "a0000000-0000-4000-8000-000000000001";
const REVISION_ID_2 = "a0000000-0000-4000-8000-000000000002";
const REGISTRATION_ID = "b0000000-0000-4000-8000-000000000001";

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function stateAxes(requestStatus = "draft"): any {
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

function approvalSteps(thirdStatus = "pending"): any[] {
  const decided = thirdStatus === "partially_approved" || thirdStatus === "approved";
  return [
    {
      step_id: "70000000-0000-4000-8000-000000000001",
      step_no: 1,
      attempt_no: 1,
      predecessor_step_id: null,
      supersedes_step_id: null,
      reopened_from_step_id: null,
      source_mode: "internal",
      status: "approved",
      assignee_snapshot: { name_masked: "区＊负责人", role_code: "provincial_manager" },
      candidate_pool_summary: null,
      opened_at: "2026-08-31T11:30:00+08:00",
      decided_at: "2026-08-31T11:40:00+08:00",
      version: 1,
      line_decisions: [decision("70000000-0000-4000-8000-000000000001")],
    },
    {
      step_id: "70000000-0000-4000-8000-000000000002",
      step_no: 2,
      attempt_no: 1,
      predecessor_step_id: "70000000-0000-4000-8000-000000000001",
      supersedes_step_id: null,
      reopened_from_step_id: null,
      source_mode: "internal",
      status: "approved",
      assignee_snapshot: null,
      candidate_pool_summary: { candidate_count: 4, candidate_kinds: ["assignee"] },
      opened_at: "2026-08-31T11:40:00+08:00",
      decided_at: "2026-08-31T11:50:00+08:00",
      version: 1,
      line_decisions: [decision("70000000-0000-4000-8000-000000000002")],
    },
    {
      step_id: "70000000-0000-4000-8000-000000000003",
      step_no: 3,
      attempt_no: 1,
      predecessor_step_id: "70000000-0000-4000-8000-000000000002",
      supersedes_step_id: null,
      reopened_from_step_id: null,
      source_mode: "external_registration",
      status: thirdStatus,
      assignee_snapshot: null,
      candidate_pool_summary: { candidate_count: 4, candidate_kinds: ["registrar", "verifier"] },
      opened_at: decided ? "2026-08-31T11:50:00+08:00" : null,
      decided_at: decided ? "2026-08-31T12:30:00+08:00" : null,
      version: 0,
      line_decisions: decided ? [decision(
        "70000000-0000-4000-8000-000000000003", "external_registration", REGISTRATION_ID,
      )] : [],
    },
  ];
}

function decision(stepId: string, source = "internal", registrationId: string | null = null): any {
  return {
    decision_id: `${stepId.slice(0, 8)}-1000-4000-8000-${stepId.slice(-12)}`,
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
    decided_at: "2026-08-31T12:30:00+08:00",
  };
}

function requestDetail(): any {
  return {
    schema_version: "1.0",
    request_id: REQUEST_ID,
    request_no: "MR-20260831-0001",
    request_version: 0,
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
      detail_masked: "江东中路***号",
    },
    contact_masked: {
      name_masked: "李*",
      mobile_masked: "138****0000",
    },
    note: "",
    attachment_refs: [],
    approval_mode: "external_registration",
    states: stateAxes(),
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
      note: "",
      final_approved_qty: "0.000",
      cancelled_qty: "0.000",
      status: "draft",
      version: 0,
    }],
    revision_history: [{
      revision_id: REVISION_ID,
      revision_no: 1,
      previous_revision_id: null,
      status: "draft",
      line_count: 1,
      attachment_count: 0,
      sealed_at: null,
      created_at: "2026-08-31T11:00:00+08:00",
    }],
    approval_history: [],
    supply_tasks: [],
    allowed_actions: ["update", "submit", "cancel"],
    created_at: "2026-08-31T11:00:00+08:00",
    updated_at: "2026-08-31T11:00:00+08:00",
    submitted_at: null,
  };
}

function requestDraft(): any {
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
      detail: "江东中路 100 号",
    },
    contact: { name: "李工程师", mobile: "138 0000 0000" },
    attachment_file_ids: ["90000000-0000-4000-8000-000000000001"],
    note: "请在期望日期前处理",
    lines: [{
      material_id: MATERIAL_ID,
      requested_qty: "12.345",
      required_date: "2026-09-05",
      suggested_substitute_material_id: null,
      note: "故障替换",
    }],
  };
}

function approvedDetail(): any {
  const detail = requestDetail();
  detail.request_version = 5;
  detail.states = stateAxes("partially_approved");
  detail.updated_at = "2026-08-31T13:00:00+08:00";
  detail.submitted_at = "2026-08-31T11:30:00+08:00";
  detail.approval_instance = {
    instance_id: INSTANCE_ID,
    request_revision_id: REVISION_ID,
    revision_no: 1,
    attempt_no: 1,
    status: "completed",
    current_step_no: null,
    current_step_id: null,
    version: 5,
    steps: approvalSteps("partially_approved"),
    external_evidence_summaries: [{
      registration_id: REGISTRATION_ID,
      registration_no: "EXT-20260831-0001",
      step_id: "70000000-0000-4000-8000-000000000003",
      external_action: "partial_approve",
      status: "accepted",
      evidence_file_id: "90000000-0000-4000-8000-000000000002",
      external_approver_name_masked: "星＊审批人",
      external_decided_at: "2026-08-31T12:20:00+08:00",
      registered_at: "2026-08-31T12:25:00+08:00",
      verified_at: "2026-08-31T12:30:00+08:00",
      version: 1,
    }],
    return_line_facts: [],
  };
  detail.approval_history = [detail.approval_instance];
  detail.revision_history[0].status = "sealed";
  detail.revision_history[0].sealed_at = "2026-08-31T11:30:00+08:00";
  detail.lines[0].final_approved_qty = "10.000";
  detail.lines[0].status = "partially_approved";
  detail.lines[0].version = 3;
  detail.supply_tasks = [{
    id: SUPPLY_TASK_ID,
    task_no: "SUP-20260831-0001",
    request_line_id: LINE_ID,
    substitution_decision_id: null,
    supply_type: "headquarters_replenishment",
    reference_no: null,
    expected_qty: "2.345",
    original_equivalent_qty: "2.345",
    expected_date: "2026-09-05",
    status: "open",
    version: 0,
    created_at: "2026-08-31T12:00:00+08:00",
    updated_at: "2026-08-31T12:00:00+08:00",
    allowed_actions: ["update_supply_task", "cancel_supply_task"],
  }];
  detail.allowed_actions = ["propose_substitution", "create_supply_task"];
  return detail;
}

function twoAttemptDetail(): any {
  const detail = approvedDetail();
  const previous = detail.approval_instance;
  const [returned, cancelled2, cancelled3] = previous.steps;
  returned.status = "returned";
  returned.line_decisions = [];
  cancelled2.status = "cancelled";
  cancelled2.opened_at = null;
  cancelled2.decided_at = null;
  cancelled2.line_decisions = [];
  cancelled3.status = "cancelled";
  cancelled3.opened_at = null;
  cancelled3.decided_at = null;
  cancelled3.line_decisions = [];
  previous.status = "superseded";
  previous.external_evidence_summaries = null;
  previous.return_line_facts = [{
    return_fact_id: "c0000000-0000-4000-8000-000000000001",
    return_action_id: "d0000000-0000-4000-8000-000000000001",
    instance_id: INSTANCE_ID,
    returned_from_step_id: returned.step_id,
    target_kind: "requester_revision",
    target_step_id: null,
    request_revision_id: REVISION_ID,
    revision_no: 1,
    request_line_id: LINE_ID,
    returned_step_input_qty: "12.345",
    target_step_max_qty: "12.345",
    required_review_qty: "12.345",
    reason: "补充现场证据",
    occurred_at: "2026-08-31T13:10:00+08:00",
  }];

  const step1Id = "70000000-0000-4000-8000-000000000011";
  const step2Id = "70000000-0000-4000-8000-000000000012";
  const step3Id = "70000000-0000-4000-8000-000000000013";
  const current = {
    instance_id: INSTANCE_ID_2,
    request_revision_id: REVISION_ID_2,
    revision_no: 2,
    attempt_no: 2,
    status: "active",
    current_step_no: 1,
    current_step_id: step1Id,
    version: 0,
    steps: [
      {
        step_id: step1Id,
        step_no: 1,
        attempt_no: 1,
        predecessor_step_id: null,
        supersedes_step_id: null,
        reopened_from_step_id: null,
        source_mode: "internal",
        status: "open",
        assignee_snapshot: { name_masked: "区＊负责人", role_code: "provincial_manager" },
        candidate_pool_summary: null,
        opened_at: "2026-08-31T13:30:00+08:00",
        decided_at: null,
        version: 0,
        line_decisions: [],
      },
      {
        step_id: step2Id,
        step_no: 2,
        attempt_no: 1,
        predecessor_step_id: step1Id,
        supersedes_step_id: null,
        reopened_from_step_id: null,
        source_mode: "internal",
        status: "pending",
        assignee_snapshot: null,
        candidate_pool_summary: { candidate_count: 4, candidate_kinds: ["assignee"] },
        opened_at: null,
        decided_at: null,
        version: 0,
        line_decisions: [],
      },
      {
        step_id: step3Id,
        step_no: 3,
        attempt_no: 1,
        predecessor_step_id: step2Id,
        supersedes_step_id: null,
        reopened_from_step_id: null,
        source_mode: "external_registration",
        status: "pending",
        assignee_snapshot: null,
        candidate_pool_summary: { candidate_count: 4, candidate_kinds: ["registrar", "verifier"] },
        opened_at: null,
        decided_at: null,
        version: 0,
        line_decisions: [],
      },
    ],
    external_evidence_summaries: null,
    return_line_facts: [],
  };
  detail.current_revision_id = REVISION_ID_2;
  detail.current_revision_no = 2;
  detail.lines[0].revision_id = REVISION_ID_2;
  detail.lines[0].revision_no = 2;
  detail.lines[0].final_approved_qty = "0.000";
  detail.lines[0].status = "approval_pending";
  detail.revision_history.push({
    revision_id: REVISION_ID_2,
    revision_no: 2,
    previous_revision_id: REVISION_ID,
    status: "sealed",
    line_count: 1,
    attachment_count: 0,
    sealed_at: "2026-08-31T13:30:00+08:00",
    created_at: "2026-08-31T13:20:00+08:00",
  });
  detail.approval_instance = current;
  detail.approval_history = [previous, current];
  detail.states = stateAxes("approval_in_progress");
  detail.allowed_actions = ["approve", "return", "reject"];
  detail.request_version = 6;
  detail.updated_at = "2026-08-31T13:30:00+08:00";
  return detail;
}

describe("formal material request response contracts", () => {
  it("validates the exact create/amend draft input without echoing private values in errors", () => {
    const parsed = validateMaterialRequestDraftInput(requestDraft());
    expect(parsed.lines[0].requested_qty).toBe("12.345");
    expect(Object.isFrozen(parsed)).toBe(true);
    expect(Object.isFrozen(parsed.contact)).toBe(true);

    const injected = requestDraft();
    injected.requester_person_id = PERSON_ID;
    expect(() => validateMaterialRequestDraftInput(injected)).toThrow(/精确包含正式字段/);

    const duplicate = requestDraft();
    duplicate.lines.push(clone(duplicate.lines[0]));
    expect(() => validateMaterialRequestDraftInput(duplicate)).toThrow(/重复明细维度/);

    const privateMobile = "13800000000";
    const badMobile = requestDraft();
    badMobile.contact.mobile = `${privateMobile}x`;
    try {
      validateMaterialRequestDraftInput(badMobile);
      throw new Error("expected validation failure");
    } catch (error) {
      expect(String(error)).not.toContain(privateMobile);
    }
  });

  it("validates the backend-aligned phase-one detail", () => {
    const parsed = validateMaterialRequestDetail(requestDetail(), REQUEST_ID.toUpperCase());
    expect(parsed.schema_version).toBe("1.0");
    expect(parsed.states.request_status).toBe("draft");
    expect(parsed.states.notification_status).toBe("not_started");
    expect(parsed.lines[0].requested_qty).toBe("12.345");
    expect(Object.keys(MATERIAL_REQUEST_STATE_AXIS_FIELDS)).toEqual([
      "application", "allocation", "reservation", "outbound", "shipment",
      "logistics_signature", "oam_receipt", "inbound", "notification", "reconciliation",
    ]);
  });

  it("accepts a three-stage external-registration approval projection", () => {
    const parsed = validateMaterialRequestDetail(approvedDetail());
    expect(parsed.approval_instance?.steps).toHaveLength(3);
    expect(parsed.approval_instance?.steps[2].source_mode).toBe("external_registration");
    expect(parsed.lines[0].final_approved_qty).toBe("10.000");
  });

  it("preserves ordered approval instances across revisions and attempts", () => {
    const parsed = validateMaterialRequestDetail(twoAttemptDetail());
    expect(parsed.approval_history.map((item) => item.attempt_no)).toEqual([1, 2]);
    expect(parsed.approval_history[0].status).toBe("superseded");
    expect(parsed.approval_history[0].steps[0].status).toBe("returned");
    expect(parsed.approval_history[0].return_line_facts).toHaveLength(1);
    expect(parsed.approval_instance?.instance_id).toBe(INSTANCE_ID_2);

    const missingOldAttempt = twoAttemptDetail();
    missingOldAttempt.approval_history = missingOldAttempt.approval_history.slice(1);
    expect(() => validateMaterialRequestDetail(missingOldAttempt)).toThrow(/尝试、修订或顺序无效/);

    const staleShortcut = twoAttemptDetail();
    staleShortcut.approval_instance = {
      ...clone(staleShortcut.approval_instance),
      instance_id: "60000000-0000-4000-8000-000000000003",
    };
    expect(() => validateMaterialRequestDetail(staleShortcut)).toThrow(/不是最近历史实例/);

    const hiddenVisibleFacts = twoAttemptDetail();
    hiddenVisibleFacts.approval_history[0].return_line_facts = [];
    expect(() => validateMaterialRequestDetail(hiddenVisibleFacts)).toThrow(/覆盖每个退回步骤/);
  });

  it("uses current_step_id across historical step attempts instead of array position", () => {
    const value = approvedDetail();
    const step1 = value.approval_instance.steps[0];
    const step2 = value.approval_instance.steps[1];
    const step3 = value.approval_instance.steps[2];
    const reopenedId = "70000000-0000-4000-8000-000000000004";
    const reopened = {
      ...clone(step1),
      step_id: reopenedId,
      attempt_no: 2,
      supersedes_step_id: step1.step_id,
      reopened_from_step_id: step2.step_id,
      status: "open",
      opened_at: "2026-08-31T12:00:00+08:00",
      decided_at: null,
      version: 0,
      line_decisions: [],
    };
    step2.status = "returned";
    step2.decided_at = "2026-08-31T12:00:00+08:00";
    step2.line_decisions = [];
    step3.status = "pending";
    step3.opened_at = null;
    step3.decided_at = null;
    step3.line_decisions = [];
    value.approval_instance.steps = [step1, reopened, step2, step3];
    value.approval_instance.status = "active";
    value.approval_instance.current_step_no = 1;
    value.approval_instance.current_step_id = reopenedId;
    value.approval_instance.external_evidence_summaries = null;
    value.approval_instance.return_line_facts = [{
      return_fact_id: "c0000000-0000-4000-8000-000000000001",
      return_action_id: "c0000000-0000-4000-8000-000000000002",
      instance_id: INSTANCE_ID,
      returned_from_step_id: step2.step_id,
      target_kind: "approval_step",
      target_step_id: reopenedId,
      request_revision_id: REVISION_ID,
      revision_no: 1,
      request_line_id: LINE_ID,
      returned_step_input_qty: "12.345",
      target_step_max_qty: "12.345",
      required_review_qty: "12.345",
      reason: "补充区域核验",
      occurred_at: "2026-08-31T12:00:00+08:00",
    }];
    value.states = stateAxes("approval_in_progress");
    value.allowed_actions = ["approve", "return", "reject"];
    value.lines[0].status = "approval_pending";

    const parsed = validateMaterialRequestDetail(value);
    expect(parsed.approval_instance?.current_step_id).toBe(reopenedId);
    expect(parsed.approval_instance?.steps).toHaveLength(4);

    value.approval_instance.current_step_id = step1.step_id;
    expect(() => validateMaterialRequestDetail(value)).toThrow(/当前步骤 ID/);
  });

  it("allows an unclaimed headquarters candidate pool and rejects sensitive nested fields", () => {
    const value = approvedDetail();
    const step2 = value.approval_instance.steps[1];
    step2.status = "open";
    step2.decided_at = null;
    step2.line_decisions = [];
    value.approval_instance.steps[2].status = "pending";
    value.approval_instance.steps[2].opened_at = null;
    value.approval_instance.steps[2].decided_at = null;
    value.approval_instance.steps[2].line_decisions = [];
    value.approval_instance.status = "active";
    value.approval_instance.current_step_no = 2;
    value.approval_instance.current_step_id = step2.step_id;
    value.approval_instance.external_evidence_summaries = [];
    value.states = stateAxes("approval_in_progress");
    value.allowed_actions = ["approve"];
    value.lines[0].status = "approval_pending";
    expect(validateMaterialRequestDetail(value).approval_instance?.steps[1].assignee_snapshot).toBeNull();

    const leaked = approvedDetail();
    leaked.approval_instance.steps[0].assignee_snapshot.permission_keys = ["all"];
    expect(() => validateMaterialRequestDetail(leaked)).toThrow(/精确包含正式字段/);
    const rawExternal = approvedDetail();
    rawExternal.approval_instance.external_evidence_summaries[0].external_approver_name_masked = "张三";
    expect(() => validateMaterialRequestDetail(rawExternal)).toThrow(/必须脱敏/);
  });

  it("fails closed on schema drift, extra fields, UUID and version errors", () => {
    const badSchema = requestDetail();
    badSchema.schema_version = "0.9";
    expect(() => validateMaterialRequestDetail(badSchema)).toThrow(/版本不受支持/);

    const extra = requestDetail();
    extra.unexpected_field = REQUEST_ID;
    expect(() => validateMaterialRequestDetail(extra)).toThrow(/精确包含正式字段/);

    const zeroUuid = requestDetail();
    zeroUuid.request_id = "00000000-0000-0000-0000-000000000000";
    expect(() => validateMaterialRequestDetail(zeroUuid)).toThrow(/request_id 无效/);

    const badVersion = requestDetail();
    badVersion.lines[0].version = 0.5;
    expect(() => validateMaterialRequestDetail(badVersion)).toThrow(/version 无效/);
  });

  it("requires fixed three-place Decimal(18,3) strings and quantity conservation", () => {
    for (const quantity of [12.345, "12", "01.000", "1.0000", "1e2", "1000000000000000.000"]) {
      const detail = requestDetail();
      detail.lines[0].requested_qty = quantity;
      expect(() => validateMaterialRequestDetail(detail)).toThrow(/Decimal\(18,3\)/);
    }

    const overApproved = requestDetail();
    overApproved.lines[0].final_approved_qty = "12.346";
    expect(() => validateMaterialRequestDetail(overApproved)).toThrow(/不能超过申请数量/);

    const overCancelled = requestDetail();
    overCancelled.lines[0].final_approved_qty = "1.000";
    overCancelled.lines[0].cancelled_qty = "1.001";
    expect(() => validateMaterialRequestDetail(overCancelled)).toThrow(/不能超过最终批准数量/);
  });

  it("requires exactly the ten backend state-axis fields and exact enums", () => {
    const missing = requestDetail();
    delete missing.states.logistics_signature_status;
    expect(() => validateMaterialRequestDetail(missing)).toThrow(/精确包含正式字段/);

    const oldUiShape = requestDetail();
    oldUiShape.states.application = oldUiShape.states.request_status;
    delete oldUiShape.states.request_status;
    expect(() => validateMaterialRequestDetail(oldUiShape)).toThrow(/精确包含正式字段/);

    const oldNotification = requestDetail();
    oldNotification.states.notification_status = "not_queued";
    expect(() => validateMaterialRequestDetail(oldNotification)).toThrow(/未知 通知状态/);

    const unknown = requestDetail();
    unknown.states.allocation_status = "fulfilled";
    expect(() => validateMaterialRequestDetail(unknown)).toThrow(/未知 分配状态/);
  });

  it("requires an explicit masked request header and the formal line fields", () => {
    const plaintextContact = requestDetail();
    plaintextContact.contact_masked.mobile_masked = "13800000000";
    expect(() => validateMaterialRequestDetail(plaintextContact)).toThrow(/必须脱敏/);

    const plaintextAddress = requestDetail();
    plaintextAddress.address_snapshot.detail_masked = "江东中路 100 号";
    expect(() => validateMaterialRequestDetail(plaintextAddress)).toThrow(/必须脱敏/);

    const badUrgency = requestDetail();
    badUrgency.urgency = "highest";
    expect(() => validateMaterialRequestDetail(badUrgency)).toThrow(/未知 紧急程度/);

    const nonDraftWithoutSubmitTime = approvedDetail();
    nonDraftWithoutSubmitTime.submitted_at = null;
    expect(() => validateMaterialRequestDetail(nonDraftWithoutSubmitTime)).toThrow(/状态与提交时间不一致/);
  });

  it("rejects direct-star phase activation and a nonconforming route", () => {
    const futureMode = requestDetail();
    futureMode.approval_mode = "direct_star";
    expect(() => validateMaterialRequestDetail(futureMode)).toThrow(/一期仅支持/);

    const badRoute = approvedDetail();
    badRoute.approval_instance.steps[2].source_mode = "internal";
    expect(() => validateMaterialRequestDetail(badRoute)).toThrow(/审批路由/);
  });

  it("validates allowed_actions against exact enum and current step facts", () => {
    const unknown = requestDetail();
    unknown.allowed_actions = ["unsafe_action"];
    expect(() => validateMaterialRequestDetail(unknown)).toThrow(/未知 allowed_action/);

    const duplicate = requestDetail();
    duplicate.allowed_actions = ["submit", "submit"];
    expect(() => validateMaterialRequestDetail(duplicate)).toThrow(/不能重复/);

    const incompatible = requestDetail();
    incompatible.allowed_actions = ["approve"];
    expect(() => validateMaterialRequestDetail(incompatible)).toThrow(/内部审批步骤不一致/);

    const external = approvedDetail();
    external.states.request_status = "approval_in_progress";
    external.approval_instance.status = "active";
    external.approval_instance.current_step_no = 3;
    external.approval_instance.current_step_id = external.approval_instance.steps[2].step_id;
    external.approval_instance.steps[2].status = "awaiting_external_evidence";
    external.approval_instance.steps[2].decided_at = null;
    external.approval_instance.steps[2].line_decisions = [];
    external.approval_instance.external_evidence_summaries = [];
    external.allowed_actions = ["register_external_approval"];
    expect(validateMaterialRequestDetail(external).allowed_actions).toEqual(["register_external_approval"]);
  });

  it("keeps shortage supply tasks as references, never fulfillment facts", () => {
    const parsed = validateMaterialRequestDetail(approvedDetail());
    expect(parsed.supply_tasks[0].status).toBe("open");
    expect(parsed.supply_tasks[0].allowed_actions).toEqual(["update_supply_task", "cancel_supply_task"]);

    const falseFulfillment = approvedDetail();
    falseFulfillment.supply_tasks[0].status = "fulfilled";
    expect(() => validateMaterialRequestDetail(falseFulfillment)).toThrow(/未知 供给任务状态/);

    const crossRequest = approvedDetail();
    crossRequest.supply_tasks[0].request_line_id = "20000000-0000-4000-8000-000000000002";
    expect(() => validateMaterialRequestDetail(crossRequest)).toThrow(/不属于当前需求明细/);

    const registeredWithoutReference = approvedDetail();
    registeredWithoutReference.supply_tasks[0].status = "reference_registered";
    expect(() => validateMaterialRequestDetail(registeredWithoutReference)).toThrow(/必须包含参考编号/);
  });

  it("validates page uniqueness and a cursor outside the current page", () => {
    const detail = requestDetail();
    const {
      schema_version: _schema,
      lines,
      revision_history: _revisions,
      approval_history: _approvalHistory,
      supply_tasks: _tasks,
      ...summary
    } = detail;
    const page: any = {
      schema_version: "1.0",
      items: [{ ...summary, line_count: lines.length }],
      next_after_id: OTHER_REQUEST_ID,
    };
    expect(validateMaterialRequestPage(page).items).toHaveLength(1);

    const duplicate = clone(page);
    duplicate.items.push(clone(duplicate.items[0]));
    expect(() => validateMaterialRequestPage(duplicate)).toThrow(/重复申请/);

    const selfCursor = clone(page);
    selfCursor.next_after_id = REQUEST_ID;
    expect(() => validateMaterialRequestPage(selfCursor)).toThrow(/游标指向当前页对象/);
  });

  it("anchors a mutation result to the object, action, next version and ten axes", () => {
    const result: any = {
      schema_version: "1.0",
      request_id: REQUEST_ID,
      action: "submit",
      request_version: 1,
      revision_id: REVISION_ID,
      revision_no: 1,
      approval_instance_id: INSTANCE_ID,
      approval_attempt_no: 1,
      current_step_id: "70000000-0000-4000-8000-000000000001",
      states: stateAxes("submitted"),
      idempotency_replayed: false,
    };
    expect(validateMaterialRequestMutationResult(result, {
      requestId: REQUEST_ID,
      action: "submit",
      previousVersion: 0,
    }).request_version).toBe(1);

    result.request_version = 2;
    expect(() => validateMaterialRequestMutationResult(result, {
      requestId: REQUEST_ID,
      action: "submit",
      previousVersion: 0,
    })).toThrow(/版本未精确递增/);
  });

  it("validates create independently at version zero and neutral ten-axis state", () => {
    const result: any = {
      schema_version: "1.0",
      request_id: REQUEST_ID,
      action: "create",
      request_version: 0,
      revision_id: REVISION_ID,
      revision_no: 1,
      states: stateAxes("draft"),
      idempotency_replayed: false,
    };
    expect(validateMaterialRequestCreateResult(result).request_id).toBe(REQUEST_ID);
    result.states.shipment_status = "shipped";
    expect(() => validateMaterialRequestCreateResult(result)).toThrow(/中性草稿状态/);
  });
});

describe("formal material request local create intent", () => {
  it("uses a non-PII client draft anchor and reuses the exact create coordinates", () => {
    const registry = new MaterialRequestCreateIntentRegistry();
    const first = registry.begin({ body: requestDraft() });
    const retry = registry.begin({ body: clone(requestDraft()) });

    expect(first.action).toBe("create");
    expect(first.path).toBe("/v1/material-requests");
    expect(first.client_draft_key).toMatch(/^draft-[A-Za-z0-9][A-Za-z0-9._:-]{15,159}$/);
    expect(first.client_draft_key).not.toContain(first.body.contact.mobile);
    expect(retry).toBe(first);
    expect(registry.size).toBe(1);
    expect(Object.isFrozen(first.body.address)).toBe(true);
  });

  it("blocks changed create content until confirmation or definitive rejection", () => {
    const registry = new MaterialRequestCreateIntentRegistry();
    const pending = registry.begin({ body: requestDraft() });
    const changed = requestDraft();
    changed.purpose = "另一个需求";
    expect(() => registry.begin({ body: changed })).toThrow(MaterialRequestCreateIntentConflictError);
    expect(() => registry.begin({ body: requestDraft(), path: "/api/v1/material-requests" }))
      .toThrow(/创建路径不在正式命名空间/);
    expect(() => registry.confirm(pending.client_draft_key, "wrong"))
      .toThrow(/不能确认不匹配/);
    registry.clearDefinitiveRejection(pending.client_draft_key, pending.signature);
    expect(registry.size).toBe(0);
  });
});

describe("formal material request local mutation intents", () => {
  let registry: MaterialRequestIntentRegistry;

  beforeEach(() => {
    registry = new MaterialRequestIntentRegistry();
  });

  it("generates independent safe formal write headers", () => {
    const create = createMaterialRequestWriteHeaders("create");
    const first = createMaterialRequestWriteHeaders("submit");
    const second = createMaterialRequestWriteHeaders("submit");
    expect(first["X-Request-ID"]).toMatch(/^[A-Za-z0-9][A-Za-z0-9._:-]{7,159}$/);
    expect(first["Idempotency-Key"]).toMatch(/^[A-Za-z0-9][A-Za-z0-9._:-]{7,159}$/);
    expect(first["Idempotency-Key"]).not.toBe(second["Idempotency-Key"]);
    expect(create["X-Request-ID"]).toMatch(/^[A-Za-z0-9][A-Za-z0-9._:-]{7,159}$/);
  });

  it("reuses one exact intent for one object and preserves its coordinates", () => {
    const first = registry.begin({
      requestId: REQUEST_ID,
      action: "submit",
      path: `/v1/material-requests/${REQUEST_ID}/submit`,
      body: { expected_version: 0, note: "提交" },
      expectedVersion: 0,
    });
    const retried = registry.begin({
      requestId: REQUEST_ID.toUpperCase(),
      action: "submit",
      path: `/v1/material-requests/${REQUEST_ID}/submit`,
      body: { note: "提交", expected_version: 0 },
      expectedVersion: 0,
    });
    expect(retried).toBe(first);
    expect(registry.size).toBe(1);
    expect(registry.get(REQUEST_ID)?.headers).toBe(first.headers);
    expect(Object.isFrozen(first.body)).toBe(true);
  });

  it("blocks a different write until the exact pending intent is confirmed", () => {
    const pending = registry.begin({
      requestId: REQUEST_ID,
      action: "submit",
      path: `/v1/material-requests/${REQUEST_ID}/submit`,
      body: { expected_version: 0 },
      expectedVersion: 0,
    });
    expect(() => registry.begin({
      requestId: REQUEST_ID,
      action: "update",
      path: `/v1/material-requests/${REQUEST_ID}`,
      body: { expected_version: 0 },
      expectedVersion: 0,
    })).toThrow(MaterialRequestIntentConflictError);

    expect(() => registry.confirm(REQUEST_ID, "wrong-signature")).toThrow(/不能确认不匹配/);
    registry.confirm(REQUEST_ID, pending.signature);
    expect(registry.size).toBe(0);
  });

  it("uses expected_request_version for approval-family intents", () => {
    const intent = registry.begin({
      requestId: REQUEST_ID,
      action: "approve",
      path: `/v1/material-requests/${REQUEST_ID}/approval-steps/70000000-0000-4000-8000-000000000001/decision`,
      body: { expected_request_version: 2, expected_step_version: 0, action: "approve" },
      expectedVersion: 2,
    });
    expect(intent.expected_version).toBe(2);
  });

  it("fails closed on namespace, object anchor and body/version mismatches", () => {
    expect(() => registry.begin({
      requestId: REQUEST_ID,
      action: "submit",
      path: `/requests/${REQUEST_ID}/submit`,
      body: { expected_version: 0 },
      expectedVersion: 0,
    })).toThrow(MaterialRequestContractError);

    expect(() => registry.begin({
      requestId: REQUEST_ID,
      action: "submit",
      path: `/v1/material-requests/${OTHER_REQUEST_ID}/submit`,
      body: { expected_version: 0 },
      expectedVersion: 0,
    })).toThrow(/目标对象不一致/);

    expect(() => registry.begin({
      requestId: REQUEST_ID,
      action: "submit",
      path: `/v1/material-requests/${REQUEST_ID}/submit`,
      body: { expected_version: 1 },
      expectedVersion: 0,
    })).toThrow(/写入内容与期望版本不一致/);
  });
});
