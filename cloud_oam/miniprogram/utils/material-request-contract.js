const api = require('./api')

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const ZERO_UUID = '00000000-0000-0000-0000-000000000000'
const DECIMAL_18_3 = /^(?:0|[1-9]\d{0,14})\.\d{3}$/
const SAFE_REQUEST_ID = /^wxreq-[a-f0-9]{36}$/
const SAFE_IDEMPOTENCY_KEY = /^wxidem-[a-f0-9]{36}$/
const SAFE_CLIENT_DRAFT_KEY = /^draft-[A-Za-z0-9][A-Za-z0-9._:-]{15,159}$/
const DATE_ONLY = /^\d{4}-\d{2}-\d{2}$/
const AWARE_TIMESTAMP = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/
const CONTACT_MOBILE = /^\+?[0-9][0-9 -]{4,30}[0-9]$/

const MATERIAL_REQUEST_SCHEMA_VERSION = '1.0'
const MATERIAL_REQUEST_STATUSES = [
  'draft',
  'submitted',
  'approval_in_progress',
  'returned',
  'partially_approved',
  'approved',
  'rejected',
  'withdrawn',
  'cancellation_pending',
  'cancelled'
]
const APPROVAL_MODES = ['external_registration', 'direct_star']
const PHASE_ONE_APPROVAL_MODE = 'external_registration'
const APPROVAL_INSTANCE_STATUSES = [
  'active', 'returned', 'completed', 'rejected', 'withdrawn', 'cancelled', 'superseded'
]
const APPROVAL_STEP_STATUSES = [
  'pending', 'open', 'awaiting_external_evidence', 'evidence_pending_verification',
  'approved', 'partially_approved', 'rejected', 'returned', 'cancelled', 'superseded'
]
const APPROVAL_SOURCE_MODES = ['internal', 'external_registration', 'direct_star']
const APPROVAL_ASSIGNEE_ROLES = ['admin', 'provincial_manager', 'star_headquarters_approver']
const APPROVAL_CANDIDATE_KINDS = ['assignee', 'registrar', 'verifier']
const LINE_STATUSES = [
  'draft', 'approval_pending', 'approved', 'partially_approved', 'rejected', 'cancelled'
]
const URGENCIES = ['normal', 'urgent', 'emergency']
const ALLOWED_ACTIONS = [
  'update',
  'submit',
  'withdraw',
  'cancel',
  'approve',
  'return',
  'reject',
  'register_external_approval',
  'verify_external_approval',
  'propose_substitution',
  'confirm_substitution',
  'reject_substitution',
  'create_supply_task'
]
const SUPPLY_TASK_ALLOWED_ACTIONS = ['update_supply_task', 'cancel_supply_task']
const MUTATION_ACTIONS = ALLOWED_ACTIONS.concat(SUPPLY_TASK_ALLOWED_ACTIONS)
const WRITE_ACTIONS = ['create'].concat(MUTATION_ACTIONS)
const SUPPLY_TYPES = [
  'cross_region_transfer',
  'headquarters_replenishment',
  'star_replenishment',
  'external_procurement_reference'
]
const SUPPLY_TASK_STATUSES = [
  'open', 'reference_registered', 'awaiting_supply', 'cancelled', 'closed_no_supply'
]

const ALLOCATION_STATUSES = [
  'not_allocated', 'partially_allocated', 'allocated', 'shortage'
]
const RESERVATION_STATUSES = [
  'not_reserved', 'pending', 'reserved', 'partially_released', 'released', 'fulfilled'
]
const OUTBOUND_STATUSES = ['not_started', 'pending_pick', 'picked', 'outbound']
const SHIPMENT_STATUSES = [
  'not_started', 'pending_handover', 'shipped', 'in_transit', 'exception'
]
const LOGISTICS_SIGNATURE_STATUSES = ['not_signed', 'signed', 'refused', 'exception']
const OAM_RECEIPT_STATUSES = ['not_occurred', 'synced', 'exception']
const PERSONAL_INBOUND_STATUSES = [
  'not_started', 'pending_acceptance', 'partially_accepted', 'accepted', 'posted'
]
const NOTIFICATION_STATUSES = ['not_started', 'queued', 'sent', 'delivered', 'read', 'failed']
const RECONCILIATION_STATUSES = [
  'not_started', 'pending', 'staged', 'validated', 'reconciled', 'conflict', 'failed'
]

// Short UI labels map one-to-one to these exact formal wire fields.
const STATE_AXIS_FIELDS = Object.freeze({
  application: 'request_status',
  allocation: 'allocation_status',
  reservation: 'reservation_status',
  outbound: 'outbound_status',
  shipment: 'shipment_status',
  logistics_signature: 'logistics_signature_status',
  oam_receipt: 'oam_receipt_status',
  inbound: 'personal_inbound_status',
  notification: 'notification_status',
  reconciliation: 'reconciliation_status'
})
const STATE_AXIS_WIRE_KEYS = Object.values(STATE_AXIS_FIELDS)

function contractError(code, message) {
  const error = new Error(message)
  error.name = 'MaterialRequestContractError'
  error.code = code
  error.status = 409
  return error
}

function fail(code, message) {
  throw contractError(code, message)
}

function objectValue(value, name) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    fail('material_request_contract_object_invalid', `${name}不是有效对象`)
  }
  return value
}

function own(object, name) {
  if (!Object.prototype.hasOwnProperty.call(object, name)) {
    fail('material_request_contract_field_missing', `正式需求响应缺少字段 ${name}`)
  }
  return object[name]
}

function exactKeys(object, keys, name) {
  const actual = Object.keys(object).sort()
  const expected = keys.slice().sort()
  if (
    actual.length !== expected.length ||
    actual.some((key, index) => key !== expected[index])
  ) fail('material_request_contract_shape_invalid', `${name}必须精确包含正式字段`)
}

function enumValue(value, allowed, name) {
  if (typeof value !== 'string' || !allowed.includes(value)) {
    fail('material_request_contract_enum_unknown', `正式需求响应包含未知 ${name}`)
  }
  return value
}

function uuidValue(value, name) {
  if (
    typeof value !== 'string' ||
    !UUID.test(value) ||
    value.toLowerCase() === ZERO_UUID
  ) fail('material_request_contract_uuid_invalid', `正式需求响应中的 ${name} 无效`)
  return value.toLowerCase()
}

function textValue(value, name) {
  if (typeof value !== 'string' || !value || value !== value.trim()) {
    fail('material_request_contract_text_invalid', `正式需求响应中的 ${name} 无效`)
  }
  return value
}

function plainText(value, name) {
  if (typeof value !== 'string' || value !== value.trim()) {
    fail('material_request_contract_text_invalid', `正式需求响应中的 ${name} 无效`)
  }
  return value
}

function nullableText(value, name) {
  return value === null ? null : textValue(value, name)
}

function nullableUuid(value, name) {
  return value === null ? null : uuidValue(value, name)
}

function dateOnly(value, name) {
  if (value === null) return null
  if (typeof value !== 'string' || !DATE_ONLY.test(value)) {
    fail('material_request_contract_date_invalid', `正式需求响应中的 ${name} 无效`)
  }
  const [year, month, day] = value.split('-').map(Number)
  const parsed = new Date(Date.UTC(year, month - 1, day))
  if (
    parsed.getUTCFullYear() !== year ||
    parsed.getUTCMonth() !== month - 1 ||
    parsed.getUTCDate() !== day
  ) fail('material_request_contract_date_invalid', `正式需求响应中的 ${name} 无效`)
  return value
}

function awareTimestamp(value, name) {
  if (
    typeof value !== 'string' ||
    !AWARE_TIMESTAMP.test(value) ||
    !Number.isFinite(Date.parse(value))
  ) fail('material_request_contract_timestamp_invalid', `正式需求响应中的 ${name} 无效`)
  return value
}

function nullableTimestamp(value, name) {
  return value === null ? null : awareTimestamp(value, name)
}

function maskedText(value, name) {
  const checked = textValue(value, name)
  if (!/[＊*•]/.test(checked)) {
    fail('material_request_contract_mask_invalid', `正式需求响应中的 ${name} 必须脱敏`)
  }
  return checked
}

function validateAddressSnapshot(value) {
  const object = objectValue(value, '脱敏收货地址快照')
  exactKeys(
    object,
    ['province_code', 'province_name', 'city_name', 'district_name', 'detail_masked'],
    '脱敏收货地址快照'
  )
  return {
    province_code: textValue(own(object, 'province_code'), 'province_code'),
    province_name: textValue(own(object, 'province_name'), 'province_name'),
    city_name: textValue(own(object, 'city_name'), 'city_name'),
    district_name: textValue(own(object, 'district_name'), 'district_name'),
    detail_masked: maskedText(own(object, 'detail_masked'), 'detail_masked')
  }
}

function validateContactMasked(value) {
  const object = objectValue(value, '脱敏联系人')
  exactKeys(object, ['name_masked', 'mobile_masked'], '脱敏联系人')
  const mobile = maskedText(own(object, 'mobile_masked'), 'mobile_masked')
  if (/\d{7,}/.test(mobile)) {
    fail('material_request_contract_mask_invalid', '脱敏手机号包含连续明文号码')
  }
  return {
    name_masked: maskedText(own(object, 'name_masked'), 'name_masked'),
    mobile_masked: mobile
  }
}

function validateAttachmentRef(value) {
  const object = objectValue(value, '需求附件引用')
  exactKeys(object, [
    'revision_id', 'revision_no', 'request_line_id', 'file_id', 'display_name', 'purpose'
  ], '需求附件引用')
  const purpose = enumValue(
    own(object, 'purpose'), ['request_attachment', 'request_line_attachment'], '附件用途'
  )
  const requestLineId = nullableUuid(own(object, 'request_line_id'), 'attachment.request_line_id')
  if ((purpose === 'request_attachment') !== (requestLineId === null)) {
    fail('material_request_contract_attachment_scope_invalid', '附件用途与需求明细锚点不一致')
  }
  return {
    revision_id: uuidValue(own(object, 'revision_id'), 'attachment.revision_id'),
    revision_no: positiveInteger(own(object, 'revision_no'), 'attachment.revision_no'),
    request_line_id: requestLineId,
    file_id: uuidValue(own(object, 'file_id'), 'attachment.file_id'),
    display_name: textValue(own(object, 'display_name'), 'attachment.display_name'),
    purpose
  }
}

function nonnegativeInteger(value, name) {
  if (!Number.isSafeInteger(value) || value < 0) {
    fail('material_request_contract_version_invalid', `正式需求响应中的 ${name} 无效`)
  }
  return value
}

function positiveInteger(value, name) {
  const checked = nonnegativeInteger(value, name)
  if (checked === 0) {
    fail('material_request_contract_integer_invalid', `${name}必须大于零`)
  }
  return checked
}

function decimalValue(value, name, positive = false) {
  if (typeof value !== 'string' || !DECIMAL_18_3.test(value)) {
    fail(
      'material_request_contract_decimal_invalid',
      `${name}必须是 Decimal(18,3) 精确字符串`
    )
  }
  if (positive && value === '0.000') {
    fail('material_request_contract_decimal_invalid', `${name}必须大于零`)
  }
  return value
}

function decimalUnits(value) {
  return BigInt(value.replace('.', ''))
}

function requireNotGreater(left, right, message) {
  if (decimalUnits(left) > decimalUnits(right)) {
    fail('material_request_contract_quantity_order_invalid', message)
  }
}

function currentStep(value) {
  if (value === null) return null
  if (![1, 2, 3].includes(value)) {
    fail('material_request_contract_step_invalid', 'current_step_no无效')
  }
  return value
}

function draftText(value, name, minimum, maximum) {
  if (
    typeof value !== 'string' ||
    value !== value.trim() ||
    value.length < minimum ||
    value.length > maximum
  ) fail('material_request_draft_text_invalid', `正式需求草稿中的 ${name} 无效`)
  return value
}

function validateDraftAddress(value) {
  const object = objectValue(value, '正式需求草稿收货地址')
  exactKeys(
    object,
    ['province_code', 'province_name', 'city_name', 'district_name', 'detail'],
    '正式需求草稿收货地址'
  )
  return {
    province_code: draftText(own(object, 'province_code'), 'province_code', 1, 12),
    province_name: draftText(own(object, 'province_name'), 'province_name', 1, 80),
    city_name: draftText(own(object, 'city_name'), 'city_name', 1, 80),
    district_name: draftText(own(object, 'district_name'), 'district_name', 1, 80),
    detail: draftText(own(object, 'detail'), 'address.detail', 1, 500)
  }
}

function validateDraftContact(value) {
  const object = objectValue(value, '正式需求草稿联系人')
  exactKeys(object, ['name', 'mobile'], '正式需求草稿联系人')
  const mobile = draftText(own(object, 'mobile'), 'contact.mobile', 6, 32)
  if (!CONTACT_MOBILE.test(mobile)) {
    fail('material_request_draft_mobile_invalid', '正式需求草稿中的 contact.mobile 无效')
  }
  return {
    name: draftText(own(object, 'name'), 'contact.name', 1, 120),
    mobile
  }
}

function validateDraftLine(value) {
  const object = objectValue(value, '正式需求草稿明细')
  exactKeys(object, [
    'material_id', 'requested_qty', 'required_date',
    'suggested_substitute_material_id', 'note'
  ], '正式需求草稿明细')
  const materialId = uuidValue(own(object, 'material_id'), 'material_id')
  const substituteId = nullableUuid(
    own(object, 'suggested_substitute_material_id'),
    'suggested_substitute_material_id'
  )
  if (substituteId === materialId) {
    fail('material_request_contract_substitute_invalid', '建议替代物料不能与申请物料相同')
  }
  return {
    material_id: materialId,
    requested_qty: decimalValue(own(object, 'requested_qty'), '申请数量', true),
    required_date: dateOnly(own(object, 'required_date'), 'line.required_date'),
    suggested_substitute_material_id: substituteId,
    note: draftText(own(object, 'note'), 'line.note', 0, 2000)
  }
}

function validateMaterialRequestDraftInput(value) {
  const object = objectValue(value, '正式需求草稿')
  exactKeys(object, [
    'work_order_id', 'purpose', 'urgency', 'expected_date', 'address', 'contact',
    'attachment_file_ids', 'note', 'lines'
  ], '正式需求草稿')
  const rawAttachments = own(object, 'attachment_file_ids')
  if (!Array.isArray(rawAttachments) || rawAttachments.length > 20) {
    fail('material_request_draft_attachments_invalid', '正式需求草稿附件引用无效')
  }
  const attachmentFileIds = rawAttachments.map((id) => uuidValue(id, 'attachment_file_id'))
  if (new Set(attachmentFileIds).size !== attachmentFileIds.length) {
    fail('material_request_draft_attachments_duplicate', '正式需求草稿附件引用不能重复')
  }
  const rawLines = own(object, 'lines')
  if (!Array.isArray(rawLines) || rawLines.length < 1 || rawLines.length > 200) {
    fail('material_request_draft_lines_invalid', '正式需求草稿必须包含一至二百条明细')
  }
  const lines = rawLines.map(validateDraftLine)
  const dimensions = lines.map((line) => [
    line.material_id,
    line.required_date,
    line.suggested_substitute_material_id
  ].join('|'))
  if (new Set(dimensions).size !== dimensions.length) {
    fail('material_request_draft_lines_duplicate', '正式需求草稿包含重复明细维度')
  }
  return deepFreeze({
    work_order_id: nullableUuid(own(object, 'work_order_id'), 'work_order_id'),
    purpose: draftText(own(object, 'purpose'), 'purpose', 1, 4000),
    urgency: enumValue(own(object, 'urgency'), URGENCIES, '紧急程度'),
    expected_date: dateOnly(own(object, 'expected_date'), 'expected_date'),
    address: validateDraftAddress(own(object, 'address')),
    contact: validateDraftContact(own(object, 'contact')),
    attachment_file_ids: attachmentFileIds,
    note: draftText(own(object, 'note'), 'note', 0, 10000),
    lines
  })
}

function validateMaterialRequestStateAxes(value) {
  const object = objectValue(value, '正式需求十状态轴')
  exactKeys(object, STATE_AXIS_WIRE_KEYS, '正式需求十状态轴')
  return {
    request_status: enumValue(own(object, 'request_status'), MATERIAL_REQUEST_STATUSES, '申请状态'),
    allocation_status: enumValue(own(object, 'allocation_status'), ALLOCATION_STATUSES, '分配状态'),
    reservation_status: enumValue(own(object, 'reservation_status'), RESERVATION_STATUSES, '占用状态'),
    outbound_status: enumValue(own(object, 'outbound_status'), OUTBOUND_STATUSES, '出库状态'),
    shipment_status: enumValue(own(object, 'shipment_status'), SHIPMENT_STATUSES, '发运状态'),
    logistics_signature_status: enumValue(
      own(object, 'logistics_signature_status'),
      LOGISTICS_SIGNATURE_STATUSES,
      '物流签收状态'
    ),
    oam_receipt_status: enumValue(
      own(object, 'oam_receipt_status'), OAM_RECEIPT_STATUSES, 'OAM收货状态'
    ),
    personal_inbound_status: enumValue(
      own(object, 'personal_inbound_status'), PERSONAL_INBOUND_STATUSES, '个人仓入库状态'
    ),
    notification_status: enumValue(
      own(object, 'notification_status'), NOTIFICATION_STATUSES, '通知状态'
    ),
    reconciliation_status: enumValue(
      own(object, 'reconciliation_status'), RECONCILIATION_STATUSES, '同步对账状态'
    )
  }
}

function validateApprovalAssigneeSnapshot(value) {
  if (value === null) return null
  const object = objectValue(value, '审批人脱敏快照')
  exactKeys(object, ['name_masked', 'role_code'], '审批人脱敏快照')
  return {
    name_masked: maskedText(own(object, 'name_masked'), 'assignee.name_masked'),
    role_code: enumValue(own(object, 'role_code'), APPROVAL_ASSIGNEE_ROLES, '审批角色')
  }
}

function validateCandidatePoolSummary(value) {
  if (value === null) return null
  const object = objectValue(value, '审批候选池摘要')
  exactKeys(object, ['candidate_count', 'candidate_kinds'], '审批候选池摘要')
  const rawKinds = own(object, 'candidate_kinds')
  if (!Array.isArray(rawKinds) || !rawKinds.length) {
    fail('material_request_contract_candidate_pool_invalid', '审批候选池类型不能为空')
  }
  const kinds = rawKinds.map((item) => enumValue(item, APPROVAL_CANDIDATE_KINDS, '候选人类型'))
  const canonical = APPROVAL_CANDIDATE_KINDS.filter((item) => kinds.includes(item))
  if (
    new Set(kinds).size !== kinds.length ||
    kinds.some((item, index) => item !== canonical[index])
  ) fail('material_request_contract_candidate_pool_invalid', '审批候选池类型必须唯一且顺序固定')
  return {
    candidate_count: positiveInteger(own(object, 'candidate_count'), 'candidate_count'),
    candidate_kinds: kinds
  }
}

function validateApprovalLineDecision(value) {
  const object = objectValue(value, '逐行审批事实')
  exactKeys(object, [
    'decision_id', 'step_id', 'request_revision_id', 'revision_no', 'request_line_id',
    'input_qty', 'approved_qty', 'rejected_qty', 'reason', 'decision_source',
    'external_registration_id', 'decided_at'
  ], '逐行审批事实')
  const inputQty = decimalValue(own(object, 'input_qty'), '审批输入数量', true)
  const approvedQty = decimalValue(own(object, 'approved_qty'), '审批同意数量')
  const rejectedQty = decimalValue(own(object, 'rejected_qty'), '审批拒绝数量')
  if (decimalUnits(approvedQty) + decimalUnits(rejectedQty) !== decimalUnits(inputQty)) {
    fail('material_request_contract_decision_quantity_invalid', '逐行审批数量不守恒')
  }
  const reason = plainText(own(object, 'reason'), 'decision.reason')
  if (rejectedQty !== '0.000' && !reason) {
    fail('material_request_contract_decision_reason_missing', '存在拒绝数量时必须填写理由')
  }
  const source = enumValue(own(object, 'decision_source'), APPROVAL_SOURCE_MODES, '审批事实来源')
  const registrationId = nullableUuid(
    own(object, 'external_registration_id'), 'external_registration_id'
  )
  if ((source === 'external_registration') !== (registrationId !== null)) {
    fail('material_request_contract_decision_source_invalid', '外部审批来源与证据登记锚点不一致')
  }
  return {
    decision_id: uuidValue(own(object, 'decision_id'), 'decision_id'),
    step_id: uuidValue(own(object, 'step_id'), 'decision.step_id'),
    request_revision_id: uuidValue(
      own(object, 'request_revision_id'), 'decision.request_revision_id'
    ),
    revision_no: positiveInteger(own(object, 'revision_no'), 'decision.revision_no'),
    request_line_id: uuidValue(own(object, 'request_line_id'), 'decision.request_line_id'),
    input_qty: inputQty,
    approved_qty: approvedQty,
    rejected_qty: rejectedQty,
    reason,
    decision_source: source,
    external_registration_id: registrationId,
    decided_at: awareTimestamp(own(object, 'decided_at'), 'decision.decided_at')
  }
}

function validateExternalEvidenceSummary(value) {
  const object = objectValue(value, '外部审批证据摘要')
  exactKeys(object, [
    'registration_id', 'registration_no', 'step_id', 'external_action', 'status',
    'evidence_file_id', 'external_approver_name_masked', 'external_decided_at',
    'registered_at', 'verified_at', 'version'
  ], '外部审批证据摘要')
  const status = enumValue(
    own(object, 'status'), ['pending_verification', 'accepted', 'rejected', 'superseded'],
    '外部证据状态'
  )
  const externalDecidedAt = awareTimestamp(
    own(object, 'external_decided_at'), 'external_decided_at'
  )
  const registeredAt = awareTimestamp(own(object, 'registered_at'), 'registered_at')
  const verifiedAt = nullableTimestamp(own(object, 'verified_at'), 'verified_at')
  if (
    Date.parse(externalDecidedAt) > Date.parse(registeredAt) ||
    (verifiedAt !== null && Date.parse(verifiedAt) < Date.parse(registeredAt))
  ) fail('material_request_contract_timestamp_order_invalid', '外部审批证据时间顺序无效')
  if ((status === 'pending_verification') !== (verifiedAt === null)) {
    fail('material_request_contract_external_verification_invalid', '外部证据状态与复核时间不一致')
  }
  return {
    registration_id: uuidValue(own(object, 'registration_id'), 'registration_id'),
    registration_no: textValue(own(object, 'registration_no'), 'registration_no'),
    step_id: uuidValue(own(object, 'step_id'), 'external_evidence.step_id'),
    external_action: enumValue(
      own(object, 'external_action'), ['approve', 'partial_approve', 'reject', 'return'],
      '外部审批动作'
    ),
    status,
    evidence_file_id: uuidValue(own(object, 'evidence_file_id'), 'evidence_file_id'),
    external_approver_name_masked: maskedText(
      own(object, 'external_approver_name_masked'), 'external_approver_name_masked'
    ),
    external_decided_at: externalDecidedAt,
    registered_at: registeredAt,
    verified_at: verifiedAt,
    version: nonnegativeInteger(own(object, 'version'), 'external_evidence.version')
  }
}

function validateReturnLineFact(value) {
  const object = objectValue(value, '审批退回逐行事实')
  exactKeys(object, [
    'return_fact_id', 'return_action_id', 'instance_id', 'returned_from_step_id',
    'target_kind', 'target_step_id', 'request_revision_id', 'revision_no', 'request_line_id',
    'returned_step_input_qty', 'target_step_max_qty', 'required_review_qty', 'reason', 'occurred_at'
  ], '审批退回逐行事实')
  const targetKind = enumValue(
    own(object, 'target_kind'), ['requester_revision', 'approval_step'], '退回目标'
  )
  const targetStepId = nullableUuid(own(object, 'target_step_id'), 'return.target_step_id')
  if ((targetKind === 'requester_revision') !== (targetStepId === null)) {
    fail('material_request_contract_return_target_invalid', '退回目标类型与步骤锚点不一致')
  }
  const targetMaxQty = decimalValue(
    own(object, 'target_step_max_qty'), '目标步骤最大数量', true
  )
  const reviewQty = decimalValue(own(object, 'required_review_qty'), '必须复核数量', true)
  requireNotGreater(reviewQty, targetMaxQty, '必须复核数量不能超过目标步骤最大数量')
  return {
    return_fact_id: uuidValue(own(object, 'return_fact_id'), 'return_fact_id'),
    return_action_id: uuidValue(own(object, 'return_action_id'), 'return_action_id'),
    instance_id: uuidValue(own(object, 'instance_id'), 'return.instance_id'),
    returned_from_step_id: uuidValue(
      own(object, 'returned_from_step_id'), 'returned_from_step_id'
    ),
    target_kind: targetKind,
    target_step_id: targetStepId,
    request_revision_id: uuidValue(
      own(object, 'request_revision_id'), 'return.request_revision_id'
    ),
    revision_no: positiveInteger(own(object, 'revision_no'), 'return.revision_no'),
    request_line_id: uuidValue(own(object, 'request_line_id'), 'return.request_line_id'),
    returned_step_input_qty: decimalValue(
      own(object, 'returned_step_input_qty'), '退回步骤输入数量', true
    ),
    target_step_max_qty: targetMaxQty,
    required_review_qty: reviewQty,
    reason: textValue(own(object, 'reason'), 'return.reason'),
    occurred_at: awareTimestamp(own(object, 'occurred_at'), 'return.occurred_at')
  }
}

function validateApprovalStep(value) {
  const object = objectValue(value, '正式需求审批步骤')
  exactKeys(object, [
    'step_id', 'step_no', 'attempt_no', 'predecessor_step_id', 'supersedes_step_id',
    'reopened_from_step_id', 'source_mode', 'status', 'assignee_snapshot',
    'candidate_pool_summary', 'opened_at', 'decided_at', 'version', 'line_decisions'
  ], '正式需求审批步骤')
  const stepNo = positiveInteger(own(object, 'step_no'), 'step_no')
  if (stepNo > 3) fail('material_request_contract_step_invalid', 'step_no无效')
  const attemptNo = positiveInteger(own(object, 'attempt_no'), 'step.attempt_no')
  const predecessorId = nullableUuid(own(object, 'predecessor_step_id'), 'predecessor_step_id')
  const supersedesId = nullableUuid(own(object, 'supersedes_step_id'), 'supersedes_step_id')
  const reopenedId = nullableUuid(own(object, 'reopened_from_step_id'), 'reopened_from_step_id')
  if ((stepNo === 1) !== (predecessorId === null)) {
    fail('material_request_contract_step_causality_invalid', '审批前序步骤锚点无效')
  }
  if ((attemptNo === 1) !== (supersedesId === null)) {
    fail('material_request_contract_step_causality_invalid', '审批重审步骤锚点无效')
  }
  const source = enumValue(own(object, 'source_mode'), APPROVAL_SOURCE_MODES, '审批来源模式')
  const status = enumValue(own(object, 'status'), APPROVAL_STEP_STATUSES, '审批步骤状态')
  const assignee = validateApprovalAssigneeSnapshot(own(object, 'assignee_snapshot'))
  const pool = validateCandidatePoolSummary(own(object, 'candidate_pool_summary'))
  if (source === 'internal' && assignee === null && pool === null) {
    fail('material_request_contract_assignee_invalid', '内部审批步骤缺少审批人或候选池摘要')
  }
  if (source !== 'internal' && assignee !== null) {
    fail('material_request_contract_assignee_invalid', '外部登记步骤不能暴露内部审批人')
  }
  if (
    source === 'external_registration' &&
    (!pool || pool.candidate_kinds.join('|') !== 'registrar|verifier')
  ) fail('material_request_contract_candidate_pool_invalid', '外部登记步骤缺少登记人和复核人候选池')
  const openedAt = nullableTimestamp(own(object, 'opened_at'), 'step.opened_at')
  const decidedAt = nullableTimestamp(own(object, 'decided_at'), 'step.decided_at')
  const decidedStatuses = ['approved', 'partially_approved', 'rejected', 'returned']
  if (decidedStatuses.includes(status) !== (decidedAt !== null)) {
    fail('material_request_contract_step_time_invalid', '审批步骤状态与决定时间不一致')
  }
  const requiresOpened = [
    'open', 'awaiting_external_evidence', 'evidence_pending_verification'
  ].concat(decidedStatuses)
  if (requiresOpened.includes(status) && openedAt === null) {
    fail('material_request_contract_step_time_invalid', '已打开或已决定步骤缺少打开时间')
  }
  if (status === 'pending' && (openedAt !== null || decidedAt !== null)) {
    fail('material_request_contract_step_time_invalid', '待处理步骤不能包含操作时间')
  }
  if (['cancelled', 'superseded'].includes(status) && decidedAt !== null) {
    fail('material_request_contract_step_time_invalid', '取消或被替代步骤不能伪造决定时间')
  }
  if (openedAt && decidedAt && Date.parse(decidedAt) < Date.parse(openedAt)) {
    fail('material_request_contract_timestamp_order_invalid', '审批决定时间不能早于打开时间')
  }
  const rawDecisions = own(object, 'line_decisions')
  if (!Array.isArray(rawDecisions)) {
    fail('material_request_contract_decisions_invalid', '逐行审批事实必须是数组')
  }
  const decisions = rawDecisions.map(validateApprovalLineDecision)
  const stepId = uuidValue(own(object, 'step_id'), 'step_id')
  if (
    new Set(decisions.map((item) => item.decision_id)).size !== decisions.length ||
    new Set(decisions.map((item) => item.request_line_id)).size !== decisions.length ||
    decisions.some((item) => item.step_id !== stepId)
  ) fail('material_request_contract_decisions_invalid', '逐行审批事实重复或步骤锚点不一致')
  if (['approved', 'partially_approved', 'rejected'].includes(status) && !decisions.length) {
    fail('material_request_contract_decisions_invalid', '已决定审批步骤必须包含逐行事实')
  }
  return {
    step_id: stepId,
    step_no: stepNo,
    attempt_no: attemptNo,
    predecessor_step_id: predecessorId,
    supersedes_step_id: supersedesId,
    reopened_from_step_id: reopenedId,
    source_mode: source,
    status,
    assignee_snapshot: assignee,
    candidate_pool_summary: pool,
    opened_at: openedAt,
    decided_at: decidedAt,
    version: nonnegativeInteger(own(object, 'version'), 'step.version'),
    line_decisions: decisions
  }
}

function validateApprovalInstance(value) {
  if (value === null) return null
  const object = objectValue(value, '正式需求审批实例')
  exactKeys(object, [
    'instance_id', 'request_revision_id', 'revision_no', 'attempt_no', 'status',
    'current_step_no', 'current_step_id', 'version', 'steps',
    'external_evidence_summaries', 'return_line_facts'
  ], '正式需求审批实例')
  const rawSteps = own(object, 'steps')
  if (!Array.isArray(rawSteps) || rawSteps.length < 3) {
    fail('material_request_contract_approval_steps_invalid', '审批步骤历史至少包含三级初始步骤')
  }
  const steps = rawSteps.map(validateApprovalStep)
  const coordinates = steps.map((step) => `${step.step_no}:${step.attempt_no}`)
  const sorted = coordinates.slice().sort((left, right) => {
    const a = left.split(':').map(Number)
    const b = right.split(':').map(Number)
    return a[0] - b[0] || a[1] - b[1]
  })
  if (
    coordinates.some((item, index) => item !== sorted[index]) ||
    new Set(coordinates).size !== coordinates.length ||
    new Set(steps.map((step) => step.step_id)).size !== steps.length
  ) fail('material_request_contract_approval_steps_invalid', '审批步骤历史坐标重复或顺序不固定')
  if (steps.some((step) => step.source_mode !== (
    step.step_no === 3 ? 'external_registration' : 'internal'
  ))) fail('material_request_contract_approval_route_invalid', '一期审批路由必须为两级内部审批加外部登记')
  const byId = new Map(steps.map((step) => [step.step_id, step]))
  for (const stepNo of [1, 2, 3]) {
    const attempts = steps.filter((step) => step.step_no === stepNo)
    if (attempts.some((step, index) => (
      step.attempt_no !== index + 1 ||
      step.supersedes_step_id !== (index === 0 ? null : attempts[index - 1].step_id)
    ))) fail('material_request_contract_step_causality_invalid', '审批重审链不连续')
  }
  for (const step of steps) {
    if (step.predecessor_step_id !== null) {
      const predecessor = byId.get(step.predecessor_step_id)
      if (!predecessor || predecessor.step_no !== step.step_no - 1) {
        fail('material_request_contract_step_causality_invalid', '审批前序不是紧邻下一级')
      }
    }
    if (step.reopened_from_step_id !== null) {
      const source = byId.get(step.reopened_from_step_id)
      if (!source || source.step_no !== step.step_no + 1 || source.status !== 'returned') {
        fail('material_request_contract_step_causality_invalid', '重开步骤缺少上一级退回因果')
      }
    }
  }
  const currentStepNo = currentStep(own(object, 'current_step_no'))
  const currentStepId = nullableUuid(own(object, 'current_step_id'), 'current_step_id')
  const status = enumValue(own(object, 'status'), APPROVAL_INSTANCE_STATUSES, '审批实例状态')
  if (status === 'active' && (currentStepNo === null || currentStepId === null)) {
    fail('material_request_contract_step_mismatch', '活动审批实例必须精确指向当前步骤')
  }
  if (
    ['returned', 'completed', 'rejected', 'withdrawn', 'cancelled', 'superseded'].includes(status) &&
    (currentStepNo !== null || currentStepId !== null)
  ) fail('material_request_contract_step_mismatch', '非活动审批实例不能指向当前步骤')
  if (currentStepId !== null) {
    const current = byId.get(currentStepId)
    if (
      !current || current.step_no !== currentStepNo ||
      !['open', 'awaiting_external_evidence', 'evidence_pending_verification'].includes(current.status)
    ) fail('material_request_contract_step_mismatch', '当前步骤 ID 未命中唯一可处理步骤')
  }
  const instanceId = uuidValue(own(object, 'instance_id'), 'instance_id')
  const revisionId = uuidValue(
    own(object, 'request_revision_id'), 'instance.request_revision_id'
  )
  const revisionNo = positiveInteger(own(object, 'revision_no'), 'instance.revision_no')
  const rawEvidence = own(object, 'external_evidence_summaries')
  let evidence = null
  if (rawEvidence !== null) {
    if (!Array.isArray(rawEvidence)) {
      fail('material_request_contract_external_evidence_invalid', '外部证据可见投影必须是数组或null')
    }
    evidence = rawEvidence.map(validateExternalEvidenceSummary)
    if (
      new Set(evidence.map((item) => item.registration_id)).size !== evidence.length ||
      new Set(evidence.map((item) => item.registration_no)).size !== evidence.length ||
      evidence.some((item) => {
        const step = byId.get(item.step_id)
        return !step || step.source_mode !== 'external_registration'
      })
    ) fail('material_request_contract_external_evidence_invalid', '外部证据重复或步骤锚点不一致')
  }
  const rawFacts = own(object, 'return_line_facts')
  let facts = null
  if (rawFacts !== null) {
    if (!Array.isArray(rawFacts)) {
      fail('material_request_contract_return_facts_invalid', '退回事实可见投影必须是数组或null')
    }
    facts = rawFacts.map(validateReturnLineFact)
    if (
      new Set(facts.map((item) => item.return_fact_id)).size !== facts.length ||
      new Set(facts.map((item) => `${item.return_action_id}:${item.request_line_id}`)).size !== facts.length
    ) fail('material_request_contract_return_facts_invalid', '退回逐行事实重复')
    for (const fact of facts) {
      const source = byId.get(fact.returned_from_step_id)
      const target = fact.target_step_id === null ? null : byId.get(fact.target_step_id)
      if (
        fact.instance_id !== instanceId || fact.request_revision_id !== revisionId ||
        fact.revision_no !== revisionNo || !source || source.status !== 'returned'
      ) fail('material_request_contract_return_facts_invalid', '退回事实与审批实例锚点不一致')
      if (fact.target_kind === 'requester_revision' && source.step_no !== 1) {
        fail('material_request_contract_return_facts_invalid', '仅一级退回可指向申请人修订')
      }
      if (
        fact.target_kind === 'approval_step' &&
        (!target || target.step_no !== source.step_no - 1 || target.reopened_from_step_id !== source.step_id)
      ) fail('material_request_contract_return_facts_invalid', '退回目标缺少重开步骤因果')
    }
    const returnedStepIds = new Set(
      steps.filter((step) => step.status === 'returned').map((step) => step.step_id)
    )
    const factStepIds = new Set(facts.map((fact) => fact.returned_from_step_id))
    if (
      returnedStepIds.size !== factStepIds.size ||
      Array.from(returnedStepIds).some((stepId) => !factStepIds.has(stepId))
    ) fail('material_request_contract_return_facts_invalid', '可见退回事实必须覆盖每个退回步骤')
  }
  if (status === 'returned' && !steps.some((step) => step.status === 'returned')) {
    fail('material_request_contract_return_facts_invalid', '退回审批实例缺少退回步骤')
  }
  return {
    instance_id: instanceId,
    request_revision_id: revisionId,
    revision_no: revisionNo,
    attempt_no: positiveInteger(own(object, 'attempt_no'), 'instance.attempt_no'),
    status,
    current_step_no: currentStepNo,
    current_step_id: currentStepId,
    version: nonnegativeInteger(own(object, 'version'), 'approval_instance.version'),
    steps,
    external_evidence_summaries: evidence,
    return_line_facts: facts
  }
}

function validateRevisionSummary(value) {
  const object = objectValue(value, '需求修订摘要')
  exactKeys(object, [
    'revision_id', 'revision_no', 'previous_revision_id', 'status', 'line_count',
    'attachment_count', 'sealed_at', 'created_at'
  ], '需求修订摘要')
  const status = enumValue(own(object, 'status'), ['draft', 'sealed'], '修订状态')
  const sealedAt = nullableTimestamp(own(object, 'sealed_at'), 'revision.sealed_at')
  const createdAt = awareTimestamp(own(object, 'created_at'), 'revision.created_at')
  if ((status === 'sealed') !== (sealedAt !== null)) {
    fail('material_request_contract_revision_state_invalid', '修订状态与封存时间不一致')
  }
  if (sealedAt !== null && Date.parse(sealedAt) < Date.parse(createdAt)) {
    fail('material_request_contract_timestamp_order_invalid', '修订封存时间不能早于创建时间')
  }
  return {
    revision_id: uuidValue(own(object, 'revision_id'), 'revision_id'),
    revision_no: positiveInteger(own(object, 'revision_no'), 'revision_no'),
    previous_revision_id: nullableUuid(own(object, 'previous_revision_id'), 'previous_revision_id'),
    status,
    line_count: positiveInteger(own(object, 'line_count'), 'revision.line_count'),
    attachment_count: nonnegativeInteger(
      own(object, 'attachment_count'), 'revision.attachment_count'
    ),
    sealed_at: sealedAt,
    created_at: createdAt
  }
}

function validateLine(value) {
  const object = objectValue(value, '正式需求明细')
  exactKeys(object, [
    'request_line_id', 'revision_id', 'revision_no', 'line_no', 'material_id', 'requested_qty', 'required_date',
    'suggested_substitute_material_id', 'note', 'final_approved_qty', 'cancelled_qty',
    'status', 'version'
  ], '正式需求明细')
  const requestedQty = decimalValue(own(object, 'requested_qty'), '申请数量', true)
  const finalApprovedQty = decimalValue(own(object, 'final_approved_qty'), '最终批准数量')
  const cancelledQty = decimalValue(own(object, 'cancelled_qty'), '取消数量')
  const materialId = uuidValue(own(object, 'material_id'), 'material_id')
  const substituteMaterialId = nullableUuid(
    own(object, 'suggested_substitute_material_id'),
    'suggested_substitute_material_id'
  )
  requireNotGreater(finalApprovedQty, requestedQty, '最终批准数量不能超过申请数量')
  requireNotGreater(cancelledQty, finalApprovedQty, '取消数量不能超过最终批准数量')
  if (substituteMaterialId === materialId) {
    fail('material_request_contract_substitute_invalid', '建议替代物料不能与申请物料相同')
  }
  return {
    request_line_id: uuidValue(own(object, 'request_line_id'), 'request_line_id'),
    revision_id: uuidValue(own(object, 'revision_id'), 'line.revision_id'),
    revision_no: positiveInteger(own(object, 'revision_no'), 'line.revision_no'),
    line_no: positiveInteger(own(object, 'line_no'), 'line_no'),
    material_id: materialId,
    requested_qty: requestedQty,
    required_date: dateOnly(own(object, 'required_date'), 'line.required_date'),
    suggested_substitute_material_id: substituteMaterialId,
    note: plainText(own(object, 'note'), 'line.note'),
    final_approved_qty: finalApprovedQty,
    cancelled_qty: cancelledQty,
    status: enumValue(own(object, 'status'), LINE_STATUSES, '明细状态'),
    version: nonnegativeInteger(own(object, 'version'), 'line.version')
  }
}

function validateSupplyTaskAllowedActions(value, status) {
  if (!Array.isArray(value)) {
    fail('material_request_contract_actions_invalid', '供给任务allowed_actions必须是数组')
  }
  const actions = value.map((action) => enumValue(
    action,
    SUPPLY_TASK_ALLOWED_ACTIONS,
    '供给任务allowed_action'
  ))
  if (new Set(actions).size !== actions.length) {
    fail('material_request_contract_actions_duplicate', '供给任务allowed_actions不能重复')
  }
  if (['cancelled', 'closed_no_supply'].includes(status) && actions.length) {
    fail('material_request_contract_action_state_mismatch', '已关闭供给任务不能包含可执行动作')
  }
  return actions
}

function validateSupplyTask(value) {
  const object = objectValue(value, '正式需求供给任务')
  exactKeys(object, [
    'id', 'task_no', 'request_line_id', 'substitution_decision_id', 'supply_type',
    'reference_no', 'expected_qty', 'original_equivalent_qty', 'expected_date', 'status',
    'version', 'created_at', 'updated_at', 'allowed_actions'
  ], '正式需求供给任务')
  const status = enumValue(own(object, 'status'), SUPPLY_TASK_STATUSES, '供给任务状态')
  const referenceNo = nullableText(own(object, 'reference_no'), 'reference_no')
  if (status === 'reference_registered' && referenceNo === null) {
    fail('material_request_contract_supply_reference_missing', '已登记参考的供给任务必须包含参考编号')
  }
  const createdAt = awareTimestamp(own(object, 'created_at'), 'created_at')
  const updatedAt = awareTimestamp(own(object, 'updated_at'), 'updated_at')
  if (Date.parse(updatedAt) < Date.parse(createdAt)) {
    fail('material_request_contract_timestamp_order_invalid', '供给任务更新时间不能早于创建时间')
  }
  return {
    id: uuidValue(own(object, 'id'), 'supply_task.id'),
    task_no: textValue(own(object, 'task_no'), 'task_no'),
    request_line_id: uuidValue(own(object, 'request_line_id'), 'supply_task.request_line_id'),
    substitution_decision_id: nullableUuid(
      own(object, 'substitution_decision_id'),
      'substitution_decision_id'
    ),
    supply_type: enumValue(own(object, 'supply_type'), SUPPLY_TYPES, '供给类型'),
    reference_no: referenceNo,
    expected_qty: decimalValue(own(object, 'expected_qty'), '供给计划数量', true),
    original_equivalent_qty: decimalValue(
      own(object, 'original_equivalent_qty'),
      '原物料等价数量',
      true
    ),
    expected_date: dateOnly(own(object, 'expected_date'), 'expected_date'),
    status,
    version: nonnegativeInteger(own(object, 'version'), 'supply_task.version'),
    created_at: createdAt,
    updated_at: updatedAt,
    allowed_actions: validateSupplyTaskAllowedActions(own(object, 'allowed_actions'), status)
  }
}

function validateAllowedActions(value) {
  if (!Array.isArray(value)) {
    fail('material_request_contract_actions_invalid', 'allowed_actions必须是数组')
  }
  const actions = value.map((action) => enumValue(action, ALLOWED_ACTIONS, 'allowed_action'))
  if (new Set(actions).size !== actions.length) {
    fail('material_request_contract_actions_duplicate', 'allowed_actions不能重复')
  }
  return actions
}

function currentApprovalStep(instance) {
  if (!instance || instance.current_step_id === null) return null
  return instance.steps.find((step) => step.step_id === instance.current_step_id) || null
}

function validateApprovalPhaseOne(requestStatus, mode, instance) {
  if (mode !== PHASE_ONE_APPROVAL_MODE) {
    fail('material_request_contract_approval_mode_unsupported', '一期仅支持外部审批登记模式')
  }
  if (
    instance &&
    instance.steps.some((step) => step.source_mode !== (
      step.step_no === 3 ? 'external_registration' : 'internal'
    ))
  ) {
    fail(
      'material_request_contract_approval_route_invalid',
      '一期审批路由必须为两级内部审批加外部登记'
    )
  }
  if (requestStatus === 'draft' && instance !== null) {
    fail('material_request_contract_approval_instance_unexpected', '草稿不能包含审批实例')
  }
  if (requestStatus === 'approval_in_progress' && (!instance || instance.status !== 'active')) {
    fail('material_request_contract_approval_instance_mismatch', '审批中需求必须包含活动审批实例')
  }
  const expectedInstanceStatus = {
    submitted: 'active',
    returned: 'returned',
    partially_approved: 'completed',
    approved: 'completed',
    rejected: 'rejected',
    withdrawn: 'withdrawn',
    cancellation_pending: 'completed'
  }
  const expected = expectedInstanceStatus[requestStatus]
  if (expected && (!instance || instance.status !== expected)) {
    fail('material_request_contract_approval_instance_mismatch', '需求状态与审批实例状态不一致')
  }
}

function validateActionCompatibility(actions, status, instance) {
  const step = currentApprovalStep(instance)
  const requestStatusActions = {
    update: ['draft', 'returned'],
    submit: ['draft', 'returned'],
    withdraw: ['submitted', 'approval_in_progress'],
    cancel: ['draft', 'returned', 'partially_approved', 'approved', 'cancellation_pending'],
    propose_substitution: ['partially_approved', 'approved'],
    confirm_substitution: ['partially_approved', 'approved'],
    reject_substitution: ['partially_approved', 'approved'],
    create_supply_task: ['partially_approved', 'approved']
  }
  for (const action of actions) {
    const statuses = requestStatusActions[action]
    if (statuses && !statuses.includes(status)) {
      fail(
        'material_request_contract_action_state_mismatch',
        `${action}与当前申请状态不一致`
      )
    }
    if (
      ['approve', 'return', 'reject'].includes(action) &&
      (!step || step.status !== 'open' || step.source_mode !== 'internal')
    ) {
      fail(
        'material_request_contract_action_state_mismatch',
        `${action}与当前内部审批步骤不一致`
      )
    }
    if (
      action === 'register_external_approval' &&
      (
        !step ||
        step.status !== 'awaiting_external_evidence' ||
        step.source_mode !== 'external_registration'
      )
    ) {
      fail(
        'material_request_contract_action_state_mismatch',
        '外部审批登记动作与当前步骤不一致'
      )
    }
    if (
      action === 'verify_external_approval' &&
      (
        !step ||
        step.status !== 'evidence_pending_verification' ||
        step.source_mode !== 'external_registration'
      )
    ) {
      fail(
        'material_request_contract_action_state_mismatch',
        '外部审批复核动作与当前步骤不一致'
      )
    }
  }
}

const DETAIL_KEYS = [
  'schema_version', 'request_id', 'request_no', 'request_version', 'current_revision_id',
  'current_revision_no', 'work_order_id',
  'requester_person_id', 'requester_org_id', 'purpose', 'urgency', 'expected_date',
  'address_snapshot', 'contact_masked', 'note', 'attachment_refs', 'approval_mode', 'states',
  'approval_instance', 'lines', 'revision_history', 'approval_history', 'supply_tasks', 'allowed_actions', 'created_at', 'updated_at',
  'submitted_at'
]
const SUMMARY_KEYS = [
  'request_id', 'request_no', 'request_version', 'current_revision_id', 'current_revision_no',
  'work_order_id', 'requester_person_id',
  'requester_org_id', 'purpose', 'urgency', 'expected_date', 'address_snapshot', 'contact_masked',
  'note', 'attachment_refs', 'approval_mode', 'states', 'approval_instance', 'line_count',
  'allowed_actions', 'created_at', 'updated_at', 'submitted_at'
]

function validateSummaryFields(object) {
  const mode = enumValue(own(object, 'approval_mode'), APPROVAL_MODES, 'approval_mode')
  const states = validateMaterialRequestStateAxes(own(object, 'states'))
  const approvalInstance = validateApprovalInstance(own(object, 'approval_instance'))
  validateApprovalPhaseOne(states.request_status, mode, approvalInstance)
  const actions = validateAllowedActions(own(object, 'allowed_actions'))
  validateActionCompatibility(actions, states.request_status, approvalInstance)
  const rawAttachmentRefs = own(object, 'attachment_refs')
  if (!Array.isArray(rawAttachmentRefs)) {
    fail('material_request_contract_attachments_invalid', '需求附件引用必须是数组')
  }
  const attachmentRefs = rawAttachmentRefs.map(validateAttachmentRef)
  if (new Set(attachmentRefs.map((attachment) => attachment.file_id)).size !== attachmentRefs.length) {
    fail('material_request_contract_attachments_duplicate', '需求附件引用不能重复')
  }
  const currentRevisionId = uuidValue(own(object, 'current_revision_id'), 'current_revision_id')
  const currentRevisionNo = positiveInteger(own(object, 'current_revision_no'), 'current_revision_no')
  if (attachmentRefs.some((item) => (
    item.revision_id !== currentRevisionId || item.revision_no !== currentRevisionNo
  ))) fail('material_request_contract_revision_anchor_invalid', '附件未锚定当前修订')
  const createdAt = awareTimestamp(own(object, 'created_at'), 'created_at')
  const updatedAt = awareTimestamp(own(object, 'updated_at'), 'updated_at')
  const submittedAt = nullableTimestamp(own(object, 'submitted_at'), 'submitted_at')
  if (
    Date.parse(updatedAt) < Date.parse(createdAt) ||
    (submittedAt !== null && Date.parse(submittedAt) < Date.parse(createdAt))
  ) fail('material_request_contract_timestamp_order_invalid', '需求时间字段顺序无效')
  if ((states.request_status === 'draft') !== (submittedAt === null)) {
    fail('material_request_contract_submission_state_invalid', '需求状态与提交时间不一致')
  }
  return {
    request_id: uuidValue(own(object, 'request_id'), 'request_id'),
    request_no: textValue(own(object, 'request_no'), 'request_no'),
    request_version: nonnegativeInteger(own(object, 'request_version'), 'request_version'),
    current_revision_id: currentRevisionId,
    current_revision_no: currentRevisionNo,
    work_order_id: nullableUuid(own(object, 'work_order_id'), 'work_order_id'),
    requester_person_id: uuidValue(own(object, 'requester_person_id'), 'requester_person_id'),
    requester_org_id: uuidValue(own(object, 'requester_org_id'), 'requester_org_id'),
    purpose: textValue(own(object, 'purpose'), 'purpose'),
    urgency: enumValue(own(object, 'urgency'), URGENCIES, '紧急程度'),
    expected_date: dateOnly(own(object, 'expected_date'), 'expected_date'),
    address_snapshot: validateAddressSnapshot(own(object, 'address_snapshot')),
    contact_masked: validateContactMasked(own(object, 'contact_masked')),
    note: plainText(own(object, 'note'), 'note'),
    attachment_refs: attachmentRefs,
    approval_mode: mode,
    states,
    approval_instance: approvalInstance,
    allowed_actions: actions,
    created_at: createdAt,
    updated_at: updatedAt,
    submitted_at: submittedAt
  }
}

function validateMaterialRequestDetail(value, expectedRequestId = '') {
  const object = objectValue(value, '正式需求详情')
  exactKeys(object, DETAIL_KEYS, '正式需求详情')
  if (own(object, 'schema_version') !== MATERIAL_REQUEST_SCHEMA_VERSION) {
    fail('material_request_contract_version_unknown', '正式需求响应版本不受支持')
  }
  const summary = validateSummaryFields(object)
  if (
    expectedRequestId &&
    summary.request_id !== uuidValue(expectedRequestId, 'expected_request_id')
  ) {
    fail('material_request_contract_anchor_mismatch', '正式需求详情与请求目标不一致')
  }
  const rawLines = own(object, 'lines')
  if (!Array.isArray(rawLines) || !rawLines.length) {
    fail('material_request_contract_lines_invalid', '正式需求必须包含至少一条明细')
  }
  const lines = rawLines.map(validateLine)
  if (
    new Set(lines.map((line) => line.request_line_id)).size !== lines.length ||
    new Set(lines.map((line) => line.line_no)).size !== lines.length
  ) {
    fail('material_request_contract_lines_duplicate', '正式需求明细标识或行号重复')
  }
  if (lines.some((line) => (
    line.revision_id !== summary.current_revision_id ||
    line.revision_no !== summary.current_revision_no
  ))) fail('material_request_contract_revision_anchor_invalid', '需求明细未锚定当前修订')
  const rawRevisions = own(object, 'revision_history')
  if (!Array.isArray(rawRevisions) || !rawRevisions.length) {
    fail('material_request_contract_revision_history_invalid', '修订历史不能为空')
  }
  const revisionHistory = rawRevisions.map(validateRevisionSummary)
  if (
    new Set(revisionHistory.map((item) => item.revision_id)).size !== revisionHistory.length ||
    revisionHistory.some((item, index) => (
      item.revision_no !== index + 1 ||
      item.previous_revision_id !== (index === 0 ? null : revisionHistory[index - 1].revision_id) ||
      (index < revisionHistory.length - 1 && item.status !== 'sealed')
    ))
  ) fail('material_request_contract_revision_history_invalid', '修订历史编号、链路或封存状态无效')
  const currentRevision = revisionHistory[revisionHistory.length - 1]
  if (
    currentRevision.revision_id !== summary.current_revision_id ||
    currentRevision.revision_no !== summary.current_revision_no ||
    currentRevision.line_count !== lines.length ||
    currentRevision.attachment_count !== summary.attachment_refs.length
  ) fail('material_request_contract_revision_anchor_invalid', '当前修订与修订历史头不一致')
  const rawApprovalHistory = own(object, 'approval_history')
  if (!Array.isArray(rawApprovalHistory)) {
    fail('material_request_contract_approval_history_invalid', '审批实例历史必须是数组')
  }
  const approvalHistory = rawApprovalHistory.map((item) => {
    const parsed = validateApprovalInstance(item)
    if (parsed === null) {
      fail('material_request_contract_approval_history_invalid', '审批实例历史不能包含空值')
    }
    return parsed
  })
  const instance = summary.approval_instance
  if ((instance === null) !== (approvalHistory.length === 0)) {
    fail('material_request_contract_approval_history_invalid', '审批快捷投影与完整历史不一致')
  }
  if (
    instance !== null &&
    JSON.stringify(instance) !== JSON.stringify(approvalHistory[approvalHistory.length - 1])
  ) fail('material_request_contract_approval_history_invalid', '审批快捷投影不是最近历史实例')
  if (
    approvalHistory.some((item, index) => item.attempt_no !== index + 1) ||
    new Set(approvalHistory.map((item) => item.instance_id)).size !== approvalHistory.length ||
    approvalHistory.some((item, index) => (
      index > 0 && item.revision_no <= approvalHistory[index - 1].revision_no
    )) ||
    approvalHistory.slice(0, -1).some((item) => item.status === 'active')
  ) fail('material_request_contract_approval_history_invalid', '审批实例历史尝试、修订或顺序无效')
  for (const historicalInstance of approvalHistory) {
    const instanceRevision = revisionHistory.find(
      (item) => item.revision_id === historicalInstance.request_revision_id
    )
    if (
      !instanceRevision || instanceRevision.revision_no !== historicalInstance.revision_no ||
      instanceRevision.status !== 'sealed'
    ) fail('material_request_contract_revision_anchor_invalid', '审批实例未锚定已封存修订')
    const visibleRegistrationIds = historicalInstance.external_evidence_summaries === null
      ? null
      : new Set(historicalInstance.external_evidence_summaries.map((item) => item.registration_id))
    for (const step of historicalInstance.steps) {
      for (const decision of step.line_decisions) {
        if (
          decision.request_revision_id !== historicalInstance.request_revision_id ||
          decision.revision_no !== historicalInstance.revision_no ||
          (visibleRegistrationIds !== null && decision.external_registration_id !== null &&
            !visibleRegistrationIds.has(decision.external_registration_id))
        ) fail('material_request_contract_revision_anchor_invalid', '逐行审批事实修订或外部证据锚点无效')
      }
    }
    if (historicalInstance.request_revision_id === summary.current_revision_id) {
      const lineIds = new Set(lines.map((line) => line.request_line_id))
      if (
        historicalInstance.steps.some((step) => step.line_decisions.some(
          (decision) => !lineIds.has(decision.request_line_id)
        )) ||
        (historicalInstance.return_line_facts && historicalInstance.return_line_facts.some(
          (fact) => !lineIds.has(fact.request_line_id)
        ))
      ) fail('material_request_contract_revision_anchor_invalid', '审批或退回事实未锚定需求明细')
    }
  }
  const rawSupplyTasks = own(object, 'supply_tasks')
  if (!Array.isArray(rawSupplyTasks)) {
    fail('material_request_contract_supply_tasks_invalid', '正式需求供给任务必须是数组')
  }
  const supplyTasks = rawSupplyTasks.map(validateSupplyTask)
  const lineIds = new Set(lines.map((line) => line.request_line_id))
  if (
    new Set(supplyTasks.map((task) => task.id)).size !== supplyTasks.length ||
    new Set(supplyTasks.map((task) => task.task_no)).size !== supplyTasks.length
  ) fail('material_request_contract_supply_tasks_duplicate', '正式需求包含重复供给任务')
  if (supplyTasks.some((task) => !lineIds.has(task.request_line_id))) {
    fail('material_request_contract_supply_task_anchor_mismatch', '供给任务不属于当前需求明细')
  }
  return Object.assign(
    { schema_version: MATERIAL_REQUEST_SCHEMA_VERSION },
    summary,
    { lines, revision_history: revisionHistory, approval_history: approvalHistory, supply_tasks: supplyTasks }
  )
}

function validateMaterialRequestPage(value) {
  const object = objectValue(value, '正式需求分页响应')
  exactKeys(object, ['schema_version', 'items', 'next_after_id'], '正式需求分页响应')
  if (own(object, 'schema_version') !== MATERIAL_REQUEST_SCHEMA_VERSION) {
    fail('material_request_contract_version_unknown', '正式需求分页版本不受支持')
  }
  const rawItems = own(object, 'items')
  if (!Array.isArray(rawItems)) {
    fail('material_request_contract_page_invalid', '正式需求分页items无效')
  }
  const items = rawItems.map((item) => {
    const source = objectValue(item, '正式需求摘要')
    exactKeys(source, SUMMARY_KEYS, '正式需求摘要')
    return Object.assign(
      {},
      validateSummaryFields(source),
      { line_count: positiveInteger(own(source, 'line_count'), 'line_count') }
    )
  })
  if (new Set(items.map((item) => item.request_id)).size !== items.length) {
    fail('material_request_contract_page_duplicate', '正式需求分页包含重复申请')
  }
  const rawNext = own(object, 'next_after_id')
  const nextAfterId = rawNext === null ? null : uuidValue(rawNext, 'next_after_id')
  if (nextAfterId && items.some((item) => item.request_id === nextAfterId)) {
    fail('material_request_contract_cursor_invalid', '正式需求分页游标指向当前页对象')
  }
  return { schema_version: MATERIAL_REQUEST_SCHEMA_VERSION, items, next_after_id: nextAfterId }
}

function validateMaterialRequestMutationResult(value, expected) {
  const object = objectValue(value, '正式需求写响应')
  exactKeys(object, [
    'schema_version', 'request_id', 'action', 'request_version', 'revision_id', 'revision_no',
    'approval_instance_id', 'approval_attempt_no', 'current_step_id', 'states', 'idempotency_replayed'
  ], '正式需求写响应')
  if (own(object, 'schema_version') !== MATERIAL_REQUEST_SCHEMA_VERSION) {
    fail('material_request_contract_version_unknown', '正式需求写响应版本不受支持')
  }
  const requestId = uuidValue(own(object, 'request_id'), 'request_id')
  const action = enumValue(own(object, 'action'), MUTATION_ACTIONS, '写动作')
  const requestVersion = nonnegativeInteger(own(object, 'request_version'), 'request_version')
  const revisionId = uuidValue(own(object, 'revision_id'), 'revision_id')
  const revisionNo = positiveInteger(own(object, 'revision_no'), 'revision_no')
  const instanceId = nullableUuid(own(object, 'approval_instance_id'), 'approval_instance_id')
  const rawAttempt = own(object, 'approval_attempt_no')
  const attemptNo = rawAttempt === null ? null : positiveInteger(rawAttempt, 'approval_attempt_no')
  const currentStepId = nullableUuid(own(object, 'current_step_id'), 'current_step_id')
  if (
    (instanceId === null) !== (attemptNo === null) ||
    (instanceId === null && currentStepId !== null)
  ) fail('material_request_contract_approval_anchor_invalid', '审批实例、尝试和当前步骤锚点不一致')
  const approvalActions = [
    'submit', 'approve', 'return', 'reject',
    'register_external_approval', 'verify_external_approval'
  ]
  if (approvalActions.includes(action) && instanceId === null) {
    fail('material_request_contract_approval_anchor_invalid', '审批写响应缺少实例尝试锚点')
  }
  if (action === 'submit' && currentStepId === null) {
    fail('material_request_contract_approval_anchor_invalid', '提交响应缺少已打开当前步骤')
  }
  const expectedVersion = nonnegativeInteger(expected.previousVersion, 'previousVersion')
  if (
    requestId !== uuidValue(expected.requestId, 'expected_request_id') ||
    action !== expected.action
  ) {
    fail(
      'material_request_contract_anchor_mismatch',
      '正式需求写响应与请求目标或动作不一致'
    )
  }
  if (requestVersion !== expectedVersion + 1) {
    fail('material_request_contract_version_mismatch', '正式需求写响应版本未精确递增')
  }
  const replayed = own(object, 'idempotency_replayed')
  if (typeof replayed !== 'boolean') {
    fail('material_request_contract_replay_invalid', 'idempotency_replayed无效')
  }
  return {
    schema_version: MATERIAL_REQUEST_SCHEMA_VERSION,
    request_id: requestId,
    action,
    request_version: requestVersion,
    revision_id: revisionId,
    revision_no: revisionNo,
    approval_instance_id: instanceId,
    approval_attempt_no: attemptNo,
    current_step_id: currentStepId,
    states: validateMaterialRequestStateAxes(own(object, 'states')),
    idempotency_replayed: replayed
  }
}

function validateMaterialRequestCreateResult(value) {
  const object = objectValue(value, '正式需求创建响应')
  exactKeys(object, [
    'schema_version', 'request_id', 'action', 'request_version', 'revision_id', 'revision_no',
    'states', 'idempotency_replayed'
  ], '正式需求创建响应')
  if (own(object, 'schema_version') !== MATERIAL_REQUEST_SCHEMA_VERSION) {
    fail('material_request_contract_version_unknown', '正式需求创建响应版本不受支持')
  }
  if (own(object, 'action') !== 'create') {
    fail('material_request_contract_anchor_mismatch', '正式需求创建响应动作不一致')
  }
  if (own(object, 'request_version') !== 0) {
    fail('material_request_contract_version_mismatch', '新建正式需求版本必须为零')
  }
  if (own(object, 'revision_no') !== 1) {
    fail('material_request_contract_revision_anchor_invalid', '新建正式需求修订号必须为一')
  }
  const states = validateMaterialRequestStateAxes(own(object, 'states'))
  if (
    states.request_status !== 'draft' ||
    states.allocation_status !== 'not_allocated' ||
    states.reservation_status !== 'not_reserved' ||
    states.outbound_status !== 'not_started' ||
    states.shipment_status !== 'not_started' ||
    states.logistics_signature_status !== 'not_signed' ||
    states.oam_receipt_status !== 'not_occurred' ||
    states.personal_inbound_status !== 'not_started' ||
    states.notification_status !== 'not_started' ||
    states.reconciliation_status !== 'not_started'
  ) fail('material_request_contract_create_state_invalid', '新建正式需求必须返回十轴中性草稿状态')
  const replayed = own(object, 'idempotency_replayed')
  if (typeof replayed !== 'boolean') {
    fail('material_request_contract_replay_invalid', 'idempotency_replayed无效')
  }
  return {
    schema_version: MATERIAL_REQUEST_SCHEMA_VERSION,
    request_id: uuidValue(own(object, 'request_id'), 'request_id'),
    action: 'create',
    request_version: 0,
    revision_id: uuidValue(own(object, 'revision_id'), 'revision_id'),
    revision_no: 1,
    states,
    idempotency_replayed: replayed
  }
}

function createMaterialRequestWriteHeaders(action, coordinateFactory = api) {
  enumValue(action, WRITE_ACTIONS, '写动作')
  if (
    !coordinateFactory ||
    typeof coordinateFactory.createRequestId !== 'function' ||
    typeof coordinateFactory.createIdempotencyKey !== 'function'
  ) {
    fail('material_request_contract_coordinate_invalid', '无法生成安全正式需求写请求坐标')
  }
  const requestId = coordinateFactory.createRequestId()
  const idempotencyKey = coordinateFactory.createIdempotencyKey()
  if (!SAFE_REQUEST_ID.test(requestId) || !SAFE_IDEMPOTENCY_KEY.test(idempotencyKey)) {
    fail('material_request_contract_coordinate_invalid', '无法生成安全正式需求写请求坐标')
  }
  return Object.freeze({
    'X-Request-ID': requestId,
    'Idempotency-Key': idempotencyKey
  })
}

function createMaterialRequestCreateIntentRegistry(options = {}) {
  const coordinateFactory = options.coordinateFactory || api
  let pending = null

  function confirm(clientDraftKey, signature) {
    if (
      !pending ||
      pending.intent.client_draft_key !== clientDraftKey ||
      pending.intent.signature !== signature
    ) {
      fail(
        'material_request_contract_intent_confirmation_invalid',
        '不能确认不匹配的正式需求创建意图'
      )
    }
    pending = null
  }

  return Object.freeze({
    begin(input) {
      const pathname = input.path === undefined ? '/v1/material-requests' : input.path
      if (pathname !== '/v1/material-requests') {
        fail('material_request_contract_intent_path_invalid', '正式需求创建路径不在正式命名空间')
      }
      const body = validateMaterialRequestDraftInput(transportedValue(input.body))
      const canonical = canonicalJson({ action: 'create', body, path: pathname })
      if (pending) {
        if (pending.canonical !== canonical) {
          const error = contractError(
            'material_request_create_intent_conflict',
            '存在结果未确认的草稿创建，已停止生成新的写请求坐标'
          )
          error.name = 'MaterialRequestCreateIntentConflictError'
          error.client_draft_key = pending.intent.client_draft_key
          error.pending_signature = pending.intent.signature
          throw error
        }
        return pending.intent
      }
      const headers = createMaterialRequestWriteHeaders('create', coordinateFactory)
      const clientDraftKey = `draft-${headers['X-Request-ID']}`
      if (!SAFE_CLIENT_DRAFT_KEY.test(clientDraftKey)) {
        fail('material_request_contract_coordinate_invalid', '无法生成安全正式需求草稿锚点')
      }
      const intent = deepFreeze({
        client_draft_key: clientDraftKey,
        action: 'create',
        path: '/v1/material-requests',
        body,
        signature: headers['Idempotency-Key'],
        headers
      })
      pending = Object.freeze({ canonical, intent })
      return intent
    },
    get() {
      return pending && pending.intent
    },
    confirm,
    clearDefinitiveRejection: confirm,
    size() {
      return pending ? 1 : 0
    }
  })
}

function sortedJsonValue(value) {
  if (Array.isArray(value)) return value.map(sortedJsonValue)
  if (!value || typeof value !== 'object') return value
  return Object.fromEntries(
    Object.entries(value)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, child]) => [key, sortedJsonValue(child)])
  )
}

function transportedValue(value) {
  let encoded
  try {
    encoded = JSON.stringify(value)
  } catch (_) {
    encoded = undefined
  }
  if (encoded === undefined) {
    fail('material_request_contract_intent_body_invalid', '正式需求写入内容不可序列化')
  }
  return JSON.parse(encoded)
}

function canonicalJson(value) {
  return JSON.stringify(sortedJsonValue(transportedValue(value)))
}

function deepFreeze(value) {
  if (!value || typeof value !== 'object' || Object.isFrozen(value)) return value
  for (const child of Object.values(value)) deepFreeze(child)
  return Object.freeze(value)
}

function canonicalFormalPath(value, requestId) {
  if (typeof value !== 'string' || !value.startsWith('/v1/material-requests/')) {
    fail('material_request_contract_intent_path_invalid', '正式需求写入路径不在正式命名空间')
  }
  if (
    value.includes('?') ||
    value.includes('#') ||
    value.includes('\\') ||
    value.includes('%') ||
    value.includes('//') ||
    /[\u0000-\u0020\u007f]/.test(value) ||
    value.split('/').some((part) => part === '.' || part === '..')
  ) {
    fail('material_request_contract_intent_path_invalid', '正式需求写入路径不规范')
  }
  const normalized = value.replace(/\/+$/, '')
  const root = `/v1/material-requests/${requestId}`
  if (normalized !== root && !normalized.startsWith(`${root}/`)) {
    fail('material_request_contract_anchor_mismatch', '正式需求写入路径与目标对象不一致')
  }
  return normalized
}

function expectedVersionField(action) {
  return ['update', 'submit', 'withdraw', 'cancel'].includes(action)
    ? 'expected_version'
    : 'expected_request_version'
}

function intentConflict(requestId, pendingSignature) {
  const error = contractError(
    'material_request_intent_conflict',
    '同一正式需求存在结果未确认的不同写入，已停止创建新请求坐标'
  )
  error.name = 'MaterialRequestIntentConflictError'
  error.request_id = requestId
  error.pending_signature = pendingSignature
  return error
}

function createMaterialRequestIntentRegistry(options = {}) {
  const maximum = options.maximum === undefined ? 64 : options.maximum
  const coordinateFactory = options.coordinateFactory || api
  if (!Number.isSafeInteger(maximum) || maximum <= 0) {
    fail('material_request_contract_intent_limit_invalid', '未确认写意图上限无效')
  }
  const intents = new Map()

  function confirm(requestId, signature) {
    const checkedId = uuidValue(requestId, 'request_id')
    const pending = intents.get(checkedId)
    if (!pending || pending.intent.signature !== signature) {
      fail(
        'material_request_contract_intent_confirmation_invalid',
        '不能确认不匹配的正式需求写意图'
      )
    }
    intents.delete(checkedId)
  }

  return Object.freeze({
    begin(input) {
      const requestId = uuidValue(input.requestId, 'request_id')
      const action = enumValue(input.action, MUTATION_ACTIONS, '写动作')
      const path = canonicalFormalPath(input.path, requestId)
      const expectedVersion = nonnegativeInteger(input.expectedVersion, 'expectedVersion')
      const body = transportedValue(input.body)
      const bodyObject = objectValue(body, '正式需求写入内容')
      const versionField = expectedVersionField(action)
      if (own(bodyObject, versionField) !== expectedVersion) {
        fail(
          'material_request_contract_intent_version_mismatch',
          '写入内容与期望版本不一致'
        )
      }
      const canonical = canonicalJson({
        action,
        body,
        expected_version: expectedVersion,
        path,
        request_id: requestId
      })
      const pending = intents.get(requestId)
      if (pending) {
        if (pending.canonical !== canonical) {
          throw intentConflict(requestId, pending.intent.signature)
        }
        return pending.intent
      }
      if (intents.size >= maximum) {
        fail(
          'material_request_contract_intent_limit_reached',
          '未确认正式需求写入过多，已停止创建新坐标'
        )
      }
      const headers = createMaterialRequestWriteHeaders(action, coordinateFactory)
      const intent = deepFreeze({
        request_id: requestId,
        action,
        path,
        body,
        expected_version: expectedVersion,
        signature: headers['Idempotency-Key'],
        headers
      })
      intents.set(requestId, Object.freeze({ canonical, intent }))
      return intent
    },
    get(requestId) {
      const pending = intents.get(uuidValue(requestId, 'request_id'))
      return pending && pending.intent
    },
    confirm,
    clearDefinitiveRejection: confirm,
    size() {
      return intents.size
    }
  })
}

module.exports = {
  MATERIAL_REQUEST_SCHEMA_VERSION,
  MATERIAL_REQUEST_STATUSES,
  APPROVAL_MODES,
  APPROVAL_INSTANCE_STATUSES,
  APPROVAL_STEP_STATUSES,
  ALLOWED_ACTIONS,
  SUPPLY_TASK_ALLOWED_ACTIONS,
  MUTATION_ACTIONS,
  WRITE_ACTIONS,
  SUPPLY_TYPES,
  SUPPLY_TASK_STATUSES,
  STATE_AXIS_FIELDS,
  validateMaterialRequestDraftInput,
  validateMaterialRequestStateAxes,
  validateMaterialRequestDetail,
  validateMaterialRequestPage,
  validateMaterialRequestMutationResult,
  validateMaterialRequestCreateResult,
  createMaterialRequestWriteHeaders,
  createMaterialRequestCreateIntentRegistry,
  createMaterialRequestIntentRegistry
}
