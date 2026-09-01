const assert = require('node:assert/strict')
const test = require('node:test')

const contract = require('../utils/material-request-contract')

const REQUEST_ID = '10000000-0000-4000-8000-000000000001'
const OTHER_REQUEST_ID = '10000000-0000-4000-8000-000000000002'
const LINE_ID = '20000000-0000-4000-8000-000000000001'
const PERSON_ID = '30000000-0000-4000-8000-000000000001'
const ORG_ID = '40000000-0000-4000-8000-000000000001'
const MATERIAL_ID = '50000000-0000-4000-8000-000000000001'
const INSTANCE_ID = '60000000-0000-4000-8000-000000000001'
const INSTANCE_ID_2 = '60000000-0000-4000-8000-000000000002'
const SUPPLY_TASK_ID = '80000000-0000-4000-8000-000000000001'
const REVISION_ID = 'a0000000-0000-4000-8000-000000000001'
const REVISION_ID_2 = 'a0000000-0000-4000-8000-000000000002'
const REGISTRATION_ID = 'b0000000-0000-4000-8000-000000000001'

function clone(value) {
  return JSON.parse(JSON.stringify(value))
}

function states(requestStatus = 'draft') {
  return {
    request_status: requestStatus,
    allocation_status: 'not_allocated',
    reservation_status: 'not_reserved',
    outbound_status: 'not_started',
    shipment_status: 'not_started',
    logistics_signature_status: 'not_signed',
    oam_receipt_status: 'not_occurred',
    personal_inbound_status: 'not_started',
    notification_status: 'not_started',
    reconciliation_status: 'not_started'
  }
}

function approvalSteps(thirdStatus = 'pending') {
  const decided = ['approved', 'partially_approved'].includes(thirdStatus)
  return [
    {
      step_id: '70000000-0000-4000-8000-000000000001',
      step_no: 1,
      attempt_no: 1,
      predecessor_step_id: null,
      supersedes_step_id: null,
      reopened_from_step_id: null,
      source_mode: 'internal',
      status: 'approved',
      assignee_snapshot: { name_masked: '区＊负责人', role_code: 'provincial_manager' },
      candidate_pool_summary: null,
      opened_at: '2026-08-31T11:30:00+08:00',
      decided_at: '2026-08-31T11:40:00+08:00',
      version: 1,
      line_decisions: [decision('70000000-0000-4000-8000-000000000001')]
    },
    {
      step_id: '70000000-0000-4000-8000-000000000002',
      step_no: 2,
      attempt_no: 1,
      predecessor_step_id: '70000000-0000-4000-8000-000000000001',
      supersedes_step_id: null,
      reopened_from_step_id: null,
      source_mode: 'internal',
      status: 'approved',
      assignee_snapshot: null,
      candidate_pool_summary: { candidate_count: 4, candidate_kinds: ['assignee'] },
      opened_at: '2026-08-31T11:40:00+08:00',
      decided_at: '2026-08-31T11:50:00+08:00',
      version: 1,
      line_decisions: [decision('70000000-0000-4000-8000-000000000002')]
    },
    {
      step_id: '70000000-0000-4000-8000-000000000003',
      step_no: 3,
      attempt_no: 1,
      predecessor_step_id: '70000000-0000-4000-8000-000000000002',
      supersedes_step_id: null,
      reopened_from_step_id: null,
      source_mode: 'external_registration',
      status: thirdStatus,
      assignee_snapshot: null,
      candidate_pool_summary: { candidate_count: 4, candidate_kinds: ['registrar', 'verifier'] },
      opened_at: decided ? '2026-08-31T11:50:00+08:00' : null,
      decided_at: decided ? '2026-08-31T12:30:00+08:00' : null,
      version: 0,
      line_decisions: decided
        ? [decision(
            '70000000-0000-4000-8000-000000000003',
            'external_registration',
            REGISTRATION_ID
          )]
        : []
    }
  ]
}

function decision(stepId, source = 'internal', registrationId = null) {
  return {
    decision_id: `${stepId.slice(0, 8)}-1000-4000-8000-${stepId.slice(-12)}`,
    step_id: stepId,
    request_revision_id: REVISION_ID,
    revision_no: 1,
    request_line_id: LINE_ID,
    input_qty: '12.345',
    approved_qty: '12.345',
    rejected_qty: '0.000',
    reason: '',
    decision_source: source,
    external_registration_id: registrationId,
    decided_at: '2026-08-31T12:30:00+08:00'
  }
}

function detail() {
  return {
    schema_version: '1.0',
    request_id: REQUEST_ID,
    request_no: 'MR-20260831-0001',
    request_version: 0,
    current_revision_id: REVISION_ID,
    current_revision_no: 1,
    work_order_id: null,
    requester_person_id: PERSON_ID,
    requester_org_id: ORG_ID,
    purpose: '现场故障处理',
    urgency: 'urgent',
    expected_date: '2026-09-05',
    address_snapshot: {
      province_code: '320000',
      province_name: '江苏省',
      city_name: '南京市',
      district_name: '建邺区',
      detail_masked: '江东中路***号'
    },
    contact_masked: {
      name_masked: '李*',
      mobile_masked: '138****0000'
    },
    note: '',
    attachment_refs: [],
    approval_mode: 'external_registration',
    states: states(),
    approval_instance: null,
    lines: [{
      request_line_id: LINE_ID,
      revision_id: REVISION_ID,
      revision_no: 1,
      line_no: 1,
      material_id: MATERIAL_ID,
      requested_qty: '12.345',
      required_date: '2026-09-05',
      suggested_substitute_material_id: null,
      note: '',
      final_approved_qty: '0.000',
      cancelled_qty: '0.000',
      status: 'draft',
      version: 0
    }],
    revision_history: [{
      revision_id: REVISION_ID,
      revision_no: 1,
      previous_revision_id: null,
      status: 'draft',
      line_count: 1,
      attachment_count: 0,
      sealed_at: null,
      created_at: '2026-08-31T11:00:00+08:00'
    }],
    approval_history: [],
    supply_tasks: [],
    allowed_actions: ['update', 'submit', 'cancel'],
    created_at: '2026-08-31T11:00:00+08:00',
    updated_at: '2026-08-31T11:00:00+08:00',
    submitted_at: null
  }
}

function draft() {
  return {
    work_order_id: null,
    purpose: '现场故障处理',
    urgency: 'urgent',
    expected_date: '2026-09-05',
    address: {
      province_code: '320000',
      province_name: '江苏省',
      city_name: '南京市',
      district_name: '建邺区',
      detail: '江东中路 100 号'
    },
    contact: { name: '李工程师', mobile: '138 0000 0000' },
    attachment_file_ids: ['90000000-0000-4000-8000-000000000001'],
    note: '请在期望日期前处理',
    lines: [{
      material_id: MATERIAL_ID,
      requested_qty: '12.345',
      required_date: '2026-09-05',
      suggested_substitute_material_id: null,
      note: '故障替换'
    }]
  }
}

function approvedDetail() {
  const value = detail()
  value.request_version = 5
  value.states = states('partially_approved')
  value.updated_at = '2026-08-31T13:00:00+08:00'
  value.submitted_at = '2026-08-31T11:30:00+08:00'
  value.approval_instance = {
    instance_id: INSTANCE_ID,
    request_revision_id: REVISION_ID,
    revision_no: 1,
    attempt_no: 1,
    status: 'completed',
    current_step_no: null,
    current_step_id: null,
    version: 5,
    steps: approvalSteps('partially_approved'),
    external_evidence_summaries: [{
      registration_id: REGISTRATION_ID,
      registration_no: 'EXT-20260831-0001',
      step_id: '70000000-0000-4000-8000-000000000003',
      external_action: 'partial_approve',
      status: 'accepted',
      evidence_file_id: '90000000-0000-4000-8000-000000000002',
      external_approver_name_masked: '星＊审批人',
      external_decided_at: '2026-08-31T12:20:00+08:00',
      registered_at: '2026-08-31T12:25:00+08:00',
      verified_at: '2026-08-31T12:30:00+08:00',
      version: 1
    }],
    return_line_facts: []
  }
  value.approval_history = [value.approval_instance]
  value.revision_history[0].status = 'sealed'
  value.revision_history[0].sealed_at = '2026-08-31T11:30:00+08:00'
  value.lines[0].final_approved_qty = '10.000'
  value.lines[0].status = 'partially_approved'
  value.lines[0].version = 3
  value.supply_tasks = [{
    id: SUPPLY_TASK_ID,
    task_no: 'SUP-20260831-0001',
    request_line_id: LINE_ID,
    substitution_decision_id: null,
    supply_type: 'headquarters_replenishment',
    reference_no: null,
    expected_qty: '2.345',
    original_equivalent_qty: '2.345',
    expected_date: '2026-09-05',
    status: 'open',
    version: 0,
    created_at: '2026-08-31T12:00:00+08:00',
    updated_at: '2026-08-31T12:00:00+08:00',
    allowed_actions: ['update_supply_task', 'cancel_supply_task']
  }]
  value.allowed_actions = ['propose_substitution', 'create_supply_task']
  return value
}

function twoAttemptDetail() {
  const value = approvedDetail()
  const previous = value.approval_instance
  const [returned, cancelled2, cancelled3] = previous.steps
  returned.status = 'returned'
  returned.line_decisions = []
  cancelled2.status = 'cancelled'
  cancelled2.opened_at = null
  cancelled2.decided_at = null
  cancelled2.line_decisions = []
  cancelled3.status = 'cancelled'
  cancelled3.opened_at = null
  cancelled3.decided_at = null
  cancelled3.line_decisions = []
  previous.status = 'superseded'
  previous.external_evidence_summaries = null
  previous.return_line_facts = [{
    return_fact_id: 'c0000000-0000-4000-8000-000000000001',
    return_action_id: 'd0000000-0000-4000-8000-000000000001',
    instance_id: INSTANCE_ID,
    returned_from_step_id: returned.step_id,
    target_kind: 'requester_revision',
    target_step_id: null,
    request_revision_id: REVISION_ID,
    revision_no: 1,
    request_line_id: LINE_ID,
    returned_step_input_qty: '12.345',
    target_step_max_qty: '12.345',
    required_review_qty: '12.345',
    reason: '补充现场证据',
    occurred_at: '2026-08-31T13:10:00+08:00'
  }]

  const step1Id = '70000000-0000-4000-8000-000000000011'
  const step2Id = '70000000-0000-4000-8000-000000000012'
  const step3Id = '70000000-0000-4000-8000-000000000013'
  const current = {
    instance_id: INSTANCE_ID_2,
    request_revision_id: REVISION_ID_2,
    revision_no: 2,
    attempt_no: 2,
    status: 'active',
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
        source_mode: 'internal',
        status: 'open',
        assignee_snapshot: { name_masked: '区＊负责人', role_code: 'provincial_manager' },
        candidate_pool_summary: null,
        opened_at: '2026-08-31T13:30:00+08:00',
        decided_at: null,
        version: 0,
        line_decisions: []
      },
      {
        step_id: step2Id,
        step_no: 2,
        attempt_no: 1,
        predecessor_step_id: step1Id,
        supersedes_step_id: null,
        reopened_from_step_id: null,
        source_mode: 'internal',
        status: 'pending',
        assignee_snapshot: null,
        candidate_pool_summary: { candidate_count: 4, candidate_kinds: ['assignee'] },
        opened_at: null,
        decided_at: null,
        version: 0,
        line_decisions: []
      },
      {
        step_id: step3Id,
        step_no: 3,
        attempt_no: 1,
        predecessor_step_id: step2Id,
        supersedes_step_id: null,
        reopened_from_step_id: null,
        source_mode: 'external_registration',
        status: 'pending',
        assignee_snapshot: null,
        candidate_pool_summary: { candidate_count: 4, candidate_kinds: ['registrar', 'verifier'] },
        opened_at: null,
        decided_at: null,
        version: 0,
        line_decisions: []
      }
    ],
    external_evidence_summaries: null,
    return_line_facts: []
  }
  value.current_revision_id = REVISION_ID_2
  value.current_revision_no = 2
  value.lines[0].revision_id = REVISION_ID_2
  value.lines[0].revision_no = 2
  value.lines[0].final_approved_qty = '0.000'
  value.lines[0].status = 'approval_pending'
  value.revision_history.push({
    revision_id: REVISION_ID_2,
    revision_no: 2,
    previous_revision_id: REVISION_ID,
    status: 'sealed',
    line_count: 1,
    attachment_count: 0,
    sealed_at: '2026-08-31T13:30:00+08:00',
    created_at: '2026-08-31T13:20:00+08:00'
  })
  value.approval_instance = current
  value.approval_history = [previous, current]
  value.states = states('approval_in_progress')
  value.allowed_actions = ['approve', 'return', 'reject']
  value.request_version = 6
  value.updated_at = '2026-08-31T13:30:00+08:00'
  return value
}

function coordinateFactory() {
  let sequence = 0
  return {
    createRequestId() {
      sequence += 1
      return `wxreq-${sequence.toString(16).padStart(36, '0')}`
    },
    createIdempotencyKey() {
      sequence += 1
      return `wxidem-${sequence.toString(16).padStart(36, '0')}`
    }
  }
}

test('validates backend-aligned phase-one detail and exact axis mapping', () => {
  const parsed = contract.validateMaterialRequestDetail(detail(), REQUEST_ID.toUpperCase())
  assert.equal(parsed.schema_version, '1.0')
  assert.equal(parsed.states.request_status, 'draft')
  assert.equal(parsed.states.notification_status, 'not_started')
  assert.equal(parsed.lines[0].requested_qty, '12.345')
  assert.deepEqual(Object.keys(contract.STATE_AXIS_FIELDS), [
    'application', 'allocation', 'reservation', 'outbound', 'shipment',
    'logistics_signature', 'oam_receipt', 'inbound', 'notification', 'reconciliation'
  ])
})

test('validates exact draft input and never includes private input values in errors', () => {
  const parsed = contract.validateMaterialRequestDraftInput(draft())
  assert.equal(parsed.lines[0].requested_qty, '12.345')
  assert.equal(Object.isFrozen(parsed.contact), true)

  const injected = draft()
  injected.requester_person_id = PERSON_ID
  assert.throws(
    () => contract.validateMaterialRequestDraftInput(injected),
    (error) => error.code === 'material_request_contract_shape_invalid'
  )

  const duplicate = draft()
  duplicate.lines.push(clone(duplicate.lines[0]))
  assert.throws(
    () => contract.validateMaterialRequestDraftInput(duplicate),
    (error) => error.code === 'material_request_draft_lines_duplicate'
  )

  const privateMobile = '13800000000'
  const invalidMobile = draft()
  invalidMobile.contact.mobile = `${privateMobile}x`
  assert.throws(
    () => contract.validateMaterialRequestDraftInput(invalidMobile),
    (error) => !String(error).includes(privateMobile)
  )
})

test('accepts the exact three-stage external-registration projection', () => {
  const parsed = contract.validateMaterialRequestDetail(approvedDetail())
  assert.equal(parsed.approval_instance.steps.length, 3)
  assert.equal(parsed.approval_instance.steps[2].source_mode, 'external_registration')
  assert.equal(parsed.lines[0].final_approved_qty, '10.000')
})

test('preserves ordered approval instances across revisions and attempts', () => {
  const parsed = contract.validateMaterialRequestDetail(twoAttemptDetail())
  assert.deepEqual(parsed.approval_history.map((item) => item.attempt_no), [1, 2])
  assert.equal(parsed.approval_history[0].status, 'superseded')
  assert.equal(parsed.approval_history[0].steps[0].status, 'returned')
  assert.equal(parsed.approval_history[0].return_line_facts.length, 1)
  assert.equal(parsed.approval_instance.instance_id, INSTANCE_ID_2)

  const missingOldAttempt = twoAttemptDetail()
  missingOldAttempt.approval_history = missingOldAttempt.approval_history.slice(1)
  assert.throws(
    () => contract.validateMaterialRequestDetail(missingOldAttempt),
    (error) => error.code === 'material_request_contract_approval_history_invalid'
  )

  const staleShortcut = twoAttemptDetail()
  staleShortcut.approval_instance = Object.assign(
    clone(staleShortcut.approval_instance),
    { instance_id: '60000000-0000-4000-8000-000000000003' }
  )
  assert.throws(
    () => contract.validateMaterialRequestDetail(staleShortcut),
    (error) => error.code === 'material_request_contract_approval_history_invalid'
  )

  const hiddenVisibleFacts = twoAttemptDetail()
  hiddenVisibleFacts.approval_history[0].return_line_facts = []
  assert.throws(
    () => contract.validateMaterialRequestDetail(hiddenVisibleFacts),
    (error) => error.code === 'material_request_contract_return_facts_invalid'
  )
})

test('fails closed on schema drift, extra fields, UUID and version errors', () => {
  const schema = detail()
  schema.schema_version = '0.9'
  assert.throws(
    () => contract.validateMaterialRequestDetail(schema),
    (error) => error.code === 'material_request_contract_version_unknown'
  )

  const extra = detail()
  extra.unexpected_field = REQUEST_ID
  assert.throws(
    () => contract.validateMaterialRequestDetail(extra),
    (error) => error.code === 'material_request_contract_shape_invalid'
  )

  const zeroUuid = detail()
  zeroUuid.request_id = '00000000-0000-0000-0000-000000000000'
  assert.throws(
    () => contract.validateMaterialRequestDetail(zeroUuid),
    (error) => error.code === 'material_request_contract_uuid_invalid'
  )

  const version = detail()
  version.lines[0].version = 0.5
  assert.throws(
    () => contract.validateMaterialRequestDetail(version),
    (error) => error.code === 'material_request_contract_version_invalid'
  )
})

test('requires fixed Decimal(18,3) strings and line quantity conservation', () => {
  for (const quantity of [12.345, '12', '01.000', '1.0000', '1e2', '1000000000000000.000']) {
    const value = detail()
    value.lines[0].requested_qty = quantity
    assert.throws(
      () => contract.validateMaterialRequestDetail(value),
      (error) => error.code === 'material_request_contract_decimal_invalid'
    )
  }

  const approved = detail()
  approved.lines[0].final_approved_qty = '12.346'
  assert.throws(
    () => contract.validateMaterialRequestDetail(approved),
    (error) => error.code === 'material_request_contract_quantity_order_invalid'
  )

  const cancelled = detail()
  cancelled.lines[0].final_approved_qty = '1.000'
  cancelled.lines[0].cancelled_qty = '1.001'
  assert.throws(
    () => contract.validateMaterialRequestDetail(cancelled),
    (error) => error.code === 'material_request_contract_quantity_order_invalid'
  )
})

test('requires exactly ten backend axis fields and exact enum values', () => {
  const missing = detail()
  delete missing.states.logistics_signature_status
  assert.throws(
    () => contract.validateMaterialRequestDetail(missing),
    (error) => error.code === 'material_request_contract_shape_invalid'
  )

  const oldNotification = detail()
  oldNotification.states.notification_status = 'not_queued'
  assert.throws(
    () => contract.validateMaterialRequestDetail(oldNotification),
    (error) => error.code === 'material_request_contract_enum_unknown'
  )

  const wrongAxis = detail()
  wrongAxis.states.allocation_status = 'fulfilled'
  assert.throws(
    () => contract.validateMaterialRequestDetail(wrongAxis),
    (error) => error.code === 'material_request_contract_enum_unknown'
  )
})

test('requires explicit masked request headers and formal line fields', () => {
  const plaintextContact = detail()
  plaintextContact.contact_masked.mobile_masked = '13800000000'
  assert.throws(
    () => contract.validateMaterialRequestDetail(plaintextContact),
    (error) => error.code === 'material_request_contract_mask_invalid'
  )

  const plaintextAddress = detail()
  plaintextAddress.address_snapshot.detail_masked = '江东中路 100 号'
  assert.throws(
    () => contract.validateMaterialRequestDetail(plaintextAddress),
    (error) => error.code === 'material_request_contract_mask_invalid'
  )

  const badUrgency = detail()
  badUrgency.urgency = 'highest'
  assert.throws(
    () => contract.validateMaterialRequestDetail(badUrgency),
    (error) => error.code === 'material_request_contract_enum_unknown'
  )

  const nonDraftWithoutSubmitTime = approvedDetail()
  nonDraftWithoutSubmitTime.submitted_at = null
  assert.throws(
    () => contract.validateMaterialRequestDetail(nonDraftWithoutSubmitTime),
    (error) => error.code === 'material_request_contract_submission_state_invalid'
  )
})

test('phase one rejects direct-star activation and route drift', () => {
  const futureMode = detail()
  futureMode.approval_mode = 'direct_star'
  assert.throws(
    () => contract.validateMaterialRequestDetail(futureMode),
    (error) => error.code === 'material_request_contract_approval_mode_unsupported'
  )

  const route = approvedDetail()
  route.approval_instance.steps[2].source_mode = 'internal'
  assert.throws(
    () => contract.validateMaterialRequestDetail(route),
    (error) => error.code === 'material_request_contract_approval_route_invalid'
  )
})

test('allowed_actions are exact, unique and backed by current step facts', () => {
  const unknown = detail()
  unknown.allowed_actions = ['unsafe_action']
  assert.throws(
    () => contract.validateMaterialRequestDetail(unknown),
    (error) => error.code === 'material_request_contract_enum_unknown'
  )

  const duplicate = detail()
  duplicate.allowed_actions = ['submit', 'submit']
  assert.throws(
    () => contract.validateMaterialRequestDetail(duplicate),
    (error) => error.code === 'material_request_contract_actions_duplicate'
  )

  const incompatible = detail()
  incompatible.allowed_actions = ['approve']
  assert.throws(
    () => contract.validateMaterialRequestDetail(incompatible),
    (error) => error.code === 'material_request_contract_action_state_mismatch'
  )

  const external = approvedDetail()
  external.states.request_status = 'approval_in_progress'
  external.approval_instance.status = 'active'
  external.approval_instance.current_step_no = 3
  external.approval_instance.current_step_id = external.approval_instance.steps[2].step_id
  external.approval_instance.steps[2].status = 'awaiting_external_evidence'
  external.approval_instance.steps[2].decided_at = null
  external.approval_instance.steps[2].line_decisions = []
  external.approval_instance.external_evidence_summaries = []
  external.allowed_actions = ['register_external_approval']
  assert.deepEqual(
    contract.validateMaterialRequestDetail(external).allowed_actions,
    ['register_external_approval']
  )
})

test('shortage tasks remain supply references rather than fulfillment facts', () => {
  const parsed = contract.validateMaterialRequestDetail(approvedDetail())
  assert.equal(parsed.supply_tasks[0].status, 'open')
  assert.deepEqual(
    parsed.supply_tasks[0].allowed_actions,
    ['update_supply_task', 'cancel_supply_task']
  )

  const falseFulfillment = approvedDetail()
  falseFulfillment.supply_tasks[0].status = 'fulfilled'
  assert.throws(
    () => contract.validateMaterialRequestDetail(falseFulfillment),
    (error) => error.code === 'material_request_contract_enum_unknown'
  )

  const crossRequest = approvedDetail()
  crossRequest.supply_tasks[0].request_line_id = '20000000-0000-4000-8000-000000000002'
  assert.throws(
    () => contract.validateMaterialRequestDetail(crossRequest),
    (error) => error.code === 'material_request_contract_supply_task_anchor_mismatch'
  )

  const missingReference = approvedDetail()
  missingReference.supply_tasks[0].status = 'reference_registered'
  assert.throws(
    () => contract.validateMaterialRequestDetail(missingReference),
    (error) => error.code === 'material_request_contract_supply_reference_missing'
  )
})

test('validates page uniqueness and next cursor anchoring', () => {
  const value = detail()
  delete value.schema_version
  delete value.lines
  delete value.revision_history
  delete value.approval_history
  delete value.supply_tasks
  value.line_count = 1
  const page = {
    schema_version: '1.0',
    items: [value],
    next_after_id: OTHER_REQUEST_ID
  }
  assert.equal(contract.validateMaterialRequestPage(page).items.length, 1)

  const duplicate = clone(page)
  duplicate.items.push(clone(duplicate.items[0]))
  assert.throws(
    () => contract.validateMaterialRequestPage(duplicate),
    (error) => error.code === 'material_request_contract_page_duplicate'
  )

  const selfCursor = clone(page)
  selfCursor.next_after_id = REQUEST_ID
  assert.throws(
    () => contract.validateMaterialRequestPage(selfCursor),
    (error) => error.code === 'material_request_contract_cursor_invalid'
  )
})

test('anchors mutation results to exact object, action, version and states', () => {
  const result = {
    schema_version: '1.0',
    request_id: REQUEST_ID,
    action: 'submit',
    request_version: 1,
    revision_id: REVISION_ID,
    revision_no: 1,
    approval_instance_id: INSTANCE_ID,
    approval_attempt_no: 1,
    current_step_id: '70000000-0000-4000-8000-000000000001',
    states: states('submitted'),
    idempotency_replayed: false
  }
  assert.equal(
    contract.validateMaterialRequestMutationResult(result, {
      requestId: REQUEST_ID,
      action: 'submit',
      previousVersion: 0
    }).request_version,
    1
  )

  result.request_version = 2
  assert.throws(
    () => contract.validateMaterialRequestMutationResult(result, {
      requestId: REQUEST_ID,
      action: 'submit',
      previousVersion: 0
    }),
    (error) => error.code === 'material_request_contract_version_mismatch'
  )
})

test('validates create result independently at version zero and neutral state', () => {
  const result = {
    schema_version: '1.0',
    request_id: REQUEST_ID,
    action: 'create',
    request_version: 0,
    revision_id: REVISION_ID,
    revision_no: 1,
    states: states('draft'),
    idempotency_replayed: false
  }
  assert.equal(contract.validateMaterialRequestCreateResult(result).request_id, REQUEST_ID)
  result.states.shipment_status = 'shipped'
  assert.throws(
    () => contract.validateMaterialRequestCreateResult(result),
    (error) => error.code === 'material_request_contract_create_state_invalid'
  )
})

test('create intent is memory-only, single-object and reuses exact coordinates', () => {
  const registry = contract.createMaterialRequestCreateIntentRegistry({
    coordinateFactory: coordinateFactory()
  })
  const first = registry.begin({ body: draft() })
  const retried = registry.begin({ body: clone(draft()) })
  assert.equal(first.action, 'create')
  assert.equal(first.path, '/v1/material-requests')
  assert.match(first.client_draft_key, /^draft-[A-Za-z0-9][A-Za-z0-9._:-]{15,159}$/)
  assert.equal(first.client_draft_key.includes(first.body.contact.mobile), false)
  assert.equal(retried, first)
  assert.equal(registry.size(), 1)

  const changed = draft()
  changed.purpose = '另一个需求'
  assert.throws(
    () => registry.begin({ body: changed }),
    (error) => error.code === 'material_request_create_intent_conflict'
  )
  assert.throws(
    () => registry.confirm(first.client_draft_key, 'wrong'),
    (error) => error.code === 'material_request_contract_intent_confirmation_invalid'
  )
  registry.clearDefinitiveRejection(first.client_draft_key, first.signature)
  assert.equal(registry.size(), 0)
})

test('generates safe distinct write coordinates from an injected factory', () => {
  const factory = coordinateFactory()
  const create = contract.createMaterialRequestWriteHeaders('create', factory)
  const first = contract.createMaterialRequestWriteHeaders('submit', factory)
  const second = contract.createMaterialRequestWriteHeaders('submit', factory)
  assert.match(first['X-Request-ID'], /^wxreq-[a-f0-9]{36}$/)
  assert.match(first['Idempotency-Key'], /^wxidem-[a-f0-9]{36}$/)
  assert.notEqual(first['Idempotency-Key'], second['Idempotency-Key'])
  assert.match(create['X-Request-ID'], /^wxreq-[a-f0-9]{36}$/)
})

test('reuses one exact unconfirmed intent and preserves coordinates', () => {
  const registry = contract.createMaterialRequestIntentRegistry({
    coordinateFactory: coordinateFactory()
  })
  const first = registry.begin({
    requestId: REQUEST_ID,
    action: 'submit',
    path: `/v1/material-requests/${REQUEST_ID}/submit`,
    body: { expected_version: 0, note: '提交' },
    expectedVersion: 0
  })
  const retried = registry.begin({
    requestId: REQUEST_ID.toUpperCase(),
    action: 'submit',
    path: `/v1/material-requests/${REQUEST_ID}/submit`,
    body: { note: '提交', expected_version: 0 },
    expectedVersion: 0
  })
  assert.equal(retried, first)
  assert.equal(registry.size(), 1)
  assert.equal(registry.get(REQUEST_ID).headers, first.headers)
  assert.equal(Object.isFrozen(first.body), true)
})

test('blocks a different same-object intent until exact confirmation', () => {
  const registry = contract.createMaterialRequestIntentRegistry({
    coordinateFactory: coordinateFactory()
  })
  const pending = registry.begin({
    requestId: REQUEST_ID,
    action: 'submit',
    path: `/v1/material-requests/${REQUEST_ID}/submit`,
    body: { expected_version: 0 },
    expectedVersion: 0
  })
  assert.throws(
    () => registry.begin({
      requestId: REQUEST_ID,
      action: 'update',
      path: `/v1/material-requests/${REQUEST_ID}`,
      body: { expected_version: 0 },
      expectedVersion: 0
    }),
    (error) => error.code === 'material_request_intent_conflict'
  )
  assert.throws(
    () => registry.confirm(REQUEST_ID, 'wrong-signature'),
    (error) => error.code === 'material_request_contract_intent_confirmation_invalid'
  )
  registry.confirm(REQUEST_ID, pending.signature)
  assert.equal(registry.size(), 0)
})

test('uses request-version fields by action and rejects namespace or anchor drift', () => {
  const registry = contract.createMaterialRequestIntentRegistry({
    coordinateFactory: coordinateFactory()
  })
  const approval = registry.begin({
    requestId: REQUEST_ID,
    action: 'approve',
    path: `/v1/material-requests/${REQUEST_ID}/approval-steps/70000000-0000-4000-8000-000000000001/decision`,
    body: { expected_request_version: 2, expected_step_version: 0, action: 'approve' },
    expectedVersion: 2
  })
  assert.equal(approval.expected_version, 2)

  const wrongNamespace = contract.createMaterialRequestIntentRegistry({
    coordinateFactory: coordinateFactory()
  })
  assert.throws(
    () => wrongNamespace.begin({
      requestId: REQUEST_ID,
      action: 'submit',
      path: `/requests/${REQUEST_ID}/submit`,
      body: { expected_version: 0 },
      expectedVersion: 0
    }),
    (error) => error.code === 'material_request_contract_intent_path_invalid'
  )

  const wrongAnchor = contract.createMaterialRequestIntentRegistry({
    coordinateFactory: coordinateFactory()
  })
  assert.throws(
    () => wrongAnchor.begin({
      requestId: REQUEST_ID,
      action: 'submit',
      path: `/v1/material-requests/${OTHER_REQUEST_ID}/submit`,
      body: { expected_version: 0 },
      expectedVersion: 0
    }),
    (error) => error.code === 'material_request_contract_anchor_mismatch'
  )

  const wrongVersion = contract.createMaterialRequestIntentRegistry({
    coordinateFactory: coordinateFactory()
  })
  assert.throws(
    () => wrongVersion.begin({
      requestId: REQUEST_ID,
      action: 'submit',
      path: `/v1/material-requests/${REQUEST_ID}/submit`,
      body: { expected_version: 1 },
      expectedVersion: 0
    }),
    (error) => error.code === 'material_request_contract_intent_version_mismatch'
  )
})
