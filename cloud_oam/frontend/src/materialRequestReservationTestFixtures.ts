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

function axes(allocation_status = "allocated") {
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

function approvedDetail(requestVersion = 3, allocationStatus = "allocated"): any {
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


const SERIAL_ID = "91000000-0000-4000-8000-000000000001";
const SERIAL_2_ID = "91000000-0000-4000-8000-000000000002";
function reservationPage(serial = false): any {
  const base = optionPage("2.000");
  const { final_approved_qty, cancelled_qty, allocatable_qty, ...page } = base;
  void final_approved_qty; void cancelled_qty; void allocatable_qty;
  return { ...page, items: [{ ...base.items[0], allocation_id: ALLOCATION_ID, allocation_no: "AL-001",
    allocated_qty: "2.000", reserved_qty: "0.000", remaining_qty: "2.000", reservable_qty: "2.000",
    tracking_mode: serial ? "serial" : "none", serial_options: serial ? [
      { serial_id: SERIAL_ID, serial_no: "SN-A", qr_code: "QR-A", lot_id: null },
      { serial_id: SERIAL_2_ID, serial_no: "SN-B", qr_code: "QR-B", lot_id: null },
    ] : [],
  }] };
}
function afterReservation(): any {
  const detail = approvedDetail(4); detail.lines = approvedDetail().lines;
  detail.states.reservation_status = "reserved";
  return detail;
}
function reservationResult(): any {
  return {
    request_id: REQUEST_ID, reservation_id: "92000000-0000-4000-8000-000000000001", reservation_no: "RS-001",
    request_version: 4, current_request_version: 4, revision_id: REVISION_ID, revision_no: 1,
    request_line_id: LINE_ID, allocation_id: ALLOCATION_ID, source_stock_account_id: STOCK_ACCOUNT_ID,
    source_balance_version: 11, source_ledger_cursor: 8, serial_ids: [],
    stock_account_id: "93000000-0000-4000-8000-000000000001",
    reserve_transaction_id: "94000000-0000-4000-8000-000000000001", reserve_transaction_no: "TX-001",
    reserved_qty: "2.000", reservation_status: "reserved", request_status: "approved",
    state_axes: afterReservation().states, idempotency_replayed: false,
  };
}
function reservationSentinel(): any {
  return { v: 2, kind: "material_request_reservation", x_request_id: "reservation-test-request-001", person_id: PERSON_ID,
    authorization_version: 7, request_id: REQUEST_ID, request_line_id: LINE_ID, allocation_id: ALLOCATION_ID,
    request_version: 3, revision_id: REVISION_ID, revision_no: 1, reserved_qty: "2.000",
    source_stock_account_id: STOCK_ACCOUNT_ID, source_balance_version: 11, source_ledger_cursor: 8,
    serial_ids: [], state_axes: approvedDetail().states };
}
export { REQUEST_ID, LINE_ID, MATERIAL_ID, PERSON_ID, STOCK_ACCOUNT_ID, ALLOCATION_ID, REVISION_ID,
  SERIAL_ID, SERIAL_2_ID, approvedDetail, access, identity, reservationPage, afterReservation, reservationResult, reservationSentinel };
