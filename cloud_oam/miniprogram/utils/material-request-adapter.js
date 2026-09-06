const api = require('./api')
const contract = require('./material-request-contract')
const materialCatalog = require('./material-catalog-contract')
const materialRequestOptions = require('./material-request-option-contract')
const session = require('./session')

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const UUID_PATH_SOURCE = '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
const ZERO_UUID = '00000000-0000-0000-0000-000000000000'
const SAFE_REQUEST_ID = /^wxreq-[a-f0-9]{36}$/
const SAFE_IDEMPOTENCY_KEY = /^wxidem-[a-f0-9]{36}$/
const DECIMAL = /^(?:0|[1-9]\d{0,14})(?:\.\d{1,3})?$/
const AWARE_TIMESTAMP = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/
const SAFE_EXTERNAL_REFERENCE = /^[A-Za-z0-9][A-Za-z0-9._:/@+\-]*$/
const INTERNAL_ROLES = new Set(['admin', 'provincial_manager', 'technician'])
const SUPPORTED_ROLES = new Set([
  ...INTERNAL_ROLES,
  'star_headquarters_approver'
])

function adapterError(message, status = 409) {
  const error = new Error(message)
  error.status = status
  error.responseReceived = false
  return error
}

function exactObject(value, keys, name) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw adapterError(`${name}不是有效对象`)
  }
  const actual = Object.keys(value).sort()
  const expected = keys.slice().sort()
  if (
    actual.length !== expected.length ||
    actual.some((key, index) => key !== expected[index])
  ) throw adapterError(`${name}必须精确包含正式字段`)
  return value
}

function uuidValue(value, name) {
  if (
    typeof value !== 'string' ||
    !UUID.test(value) ||
    value.toLowerCase() === ZERO_UUID
  ) throw adapterError(`${name}无效`)
  return value.toLowerCase()
}

function positiveVersion(value, name) {
  if (!Number.isSafeInteger(value) || value <= 0) throw adapterError(`${name}无效`)
  return value
}

function nonnegativeVersion(value, name) {
  if (!Number.isSafeInteger(value) || value < 0) throw adapterError(`${name}无效`)
  return value
}

function requiredText(value, name) {
  if (typeof value !== 'string' || !value || value !== value.trim()) {
    throw adapterError(`${name}无效`)
  }
  return value
}

function boundedText(value, name, maximum, allowEmpty = true) {
  if (
    typeof value !== 'string' ||
    value !== value.trim() ||
    value.length > maximum ||
    (!allowEmpty && !value) ||
    /[\u0000-\u001f\u007f]/.test(value)
  ) throw adapterError(`${name}无效`)
  return value
}

function decimalText(value, name, positive) {
  if (typeof value !== 'string' || !DECIMAL.test(value)) {
    throw adapterError(`${name}无效`)
  }
  if (positive && /^0(?:\.0{1,3})?$/.test(value)) {
    throw adapterError(`${name}必须大于零`)
  }
  return value
}

function approvalLines(value, returnMode) {
  if (!Array.isArray(value) || value.length > 200) throw adapterError('审批逐行内容无效')
  const ids = value.map((row, index) => {
    const object = exactObject(
      row,
      returnMode
        ? ['request_line_id', 'requested_reapproval_qty', 'reason']
        : ['request_line_id', 'approved_qty', 'reason'],
      `审批明细 ${index + 1}`
    )
    const id = uuidValue(object.request_line_id, 'request_line_id')
    decimalText(
      object[returnMode ? 'requested_reapproval_qty' : 'approved_qty'],
      returnMode ? 'requested_reapproval_qty' : 'approved_qty',
      returnMode
    )
    boundedText(object.reason, 'reason', 4000, !returnMode)
    return id
  })
  if (new Set(ids).size !== ids.length) throw adapterError('审批逐行内容包含重复明细')
}

function approvalShape(object, action) {
  if (!Array.isArray(object.lines) || !Array.isArray(object.return_lines)) {
    throw adapterError('审批逐行内容无效')
  }
  if (action === 'approve' && (!object.lines.length || object.return_lines.length)) {
    throw adapterError('批准动作必须包含完整批准明细且不能包含退回明细')
  }
  if (action === 'return' && (object.lines.length || !object.return_lines.length)) {
    throw adapterError('退回动作必须包含完整重审明细且不能包含批准明细')
  }
  if (action === 'reject' && (object.lines.length || object.return_lines.length)) {
    throw adapterError('驳回动作不能包含逐行数量')
  }
  approvalLines(object.lines, false)
  approvalLines(object.return_lines, true)
  const comment = boundedText(object.comment, 'comment', 4000)
  if (['return', 'reject'].includes(action) && !comment) {
    throw adapterError('退回或驳回必须填写处理意见')
  }
}

function cancellationLines(value) {
  if (!Array.isArray(value) || value.length > 200) {
    throw adapterError('取消明细数量无效')
  }
  const ids = value.map((row, index) => {
    const object = exactObject(
      row,
      ['request_line_id', 'cancelled_qty', 'reason'],
      `取消明细 ${index + 1}`
    )
    const id = uuidValue(object.request_line_id, 'request_line_id')
    decimalText(object.cancelled_qty, 'cancelled_qty', true)
    boundedText(object.reason, 'reason', 4000, false)
    return id
  })
  if (new Set(ids).size !== ids.length) {
    throw adapterError('取消明细不能重复')
  }
}

function validateSupportedMutationBody(action, body) {
  if (['create_supply_task', 'update_supply_task', 'cancel_supply_task'].includes(action)) {
    const creating = action === 'create_supply_task'
    const object = exactObject(body, creating
      ? ['expected_request_version', 'request_line_id', 'supply_type', 'reference_no',
        'expected_qty', 'expected_date', 'note']
      : ['expected_request_version', 'expected_task_version', 'status', 'reference_no',
        'expected_date', 'comment'], '正式供给计划内容')
    if (object.reference_no !== null) {
      const reference = boundedText(object.reference_no, 'reference_no', 160, false)
      if (!SAFE_EXTERNAL_REFERENCE.test(reference)) throw adapterError('供给参考号无效')
    }
    if (object.expected_date !== null) {
      const value = object.expected_date
      if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)
        || !Number.isFinite(Date.parse(value))
        || new Date(value).toISOString().slice(0, 10) !== value) {
        throw adapterError('供给预计日期无效')
      }
    }
    if (creating) {
      uuidValue(object.request_line_id, 'request_line_id')
      if (!contract.SUPPLY_TYPES.includes(object.supply_type)) throw adapterError('供给类型无效')
      decimalText(object.expected_qty, 'expected_qty', true)
      boundedText(object.note, 'note', 4000)
    } else {
      nonnegativeVersion(object.expected_task_version, 'expected_task_version')
      if (!contract.SUPPLY_TASK_STATUSES.includes(object.status)
        || (action === 'cancel_supply_task') !== (object.status === 'cancelled')) {
        throw adapterError('供给计划动作与状态不一致')
      }
      if (object.status === 'reference_registered' && object.reference_no === null) {
        throw adapterError('登记参考状态必须提供参考号')
      }
      boundedText(object.comment, 'comment', 4000,
        !['cancelled', 'closed_no_supply'].includes(object.status))
    }
    return
  }
  if (action === 'update') {
    const object = exactObject(body, [
      'work_order_id', 'purpose', 'urgency', 'expected_date', 'address', 'contact',
      'attachment_file_ids', 'note', 'lines', 'expected_version'
    ], '正式需求修改内容')
    const draft = Object.assign({}, object)
    delete draft.expected_version
    contract.validateMaterialRequestDraftInput(draft)
    return
  }
  if (action === 'submit') {
    exactObject(body, ['expected_version'], '正式需求提交内容')
    return
  }
  if (action === 'withdraw') {
    const object = exactObject(
      body,
      ['expected_version', 'reason'],
      '正式需求撤回内容'
    )
    boundedText(object.reason, 'reason', 4000, false)
    return
  }
  if (action === 'cancel') {
    const object = exactObject(
      body,
      ['expected_version', 'reason', 'lines'],
      '正式需求安全取消内容'
    )
    boundedText(object.reason, 'reason', 4000, false)
    cancellationLines(object.lines)
    return
  }
  if (['approve', 'return', 'reject'].includes(action)) {
    const object = exactObject(body, [
      'expected_request_version', 'expected_step_version', 'action',
      'lines', 'return_lines', 'comment'
    ], '正式内部审批内容')
    nonnegativeVersion(object.expected_step_version, 'expected_step_version')
    if (object.action !== action) throw adapterError('审批动作与写意图不一致')
    approvalShape(object, action)
    return
  }
  if (action === 'register_external_approval') {
    const object = exactObject(body, [
      'expected_request_version', 'expected_step_version', 'evidence_file_id',
      'external_approver_name', 'external_reference_no', 'external_decided_at',
      'action', 'lines', 'return_lines', 'comment'
    ], '正式外部审批登记内容')
    nonnegativeVersion(object.expected_step_version, 'expected_step_version')
    uuidValue(object.evidence_file_id, 'evidence_file_id')
    boundedText(object.external_approver_name, 'external_approver_name', 160, false)
    const reference = boundedText(
      object.external_reference_no,
      'external_reference_no',
      200,
      false
    )
    if (!SAFE_EXTERNAL_REFERENCE.test(reference)) {
      throw adapterError('external_reference_no无效')
    }
    const decidedAt = boundedText(object.external_decided_at, 'external_decided_at', 80, false)
    if (!AWARE_TIMESTAMP.test(decidedAt) || !Number.isFinite(Date.parse(decidedAt))) {
      throw adapterError('external_decided_at无效')
    }
    if (!['approve', 'return', 'reject'].includes(object.action)) {
      throw adapterError('外部审批动作无效')
    }
    approvalShape(object, object.action)
    return
  }
  if (action === 'verify_external_approval') {
    const object = exactObject(body, [
      'expected_request_version', 'expected_step_version', 'decision', 'comment'
    ], '正式外部审批复核内容')
    nonnegativeVersion(object.expected_step_version, 'expected_step_version')
    if (!['accept', 'reject'].includes(object.decision)) {
      throw adapterError('外部审批复核决定无效')
    }
    const comment = boundedText(object.comment, 'comment', 4000)
    if (object.decision === 'reject' && !comment) {
      throw adapterError('复核拒绝必须填写原因')
    }
  }
}

function validateAccess(value) {
  const object = exactObject(
    value,
    [
      'schema_version', 'person_id', 'authorization_version', 'can_read', 'can_create',
      'can_read_material_catalog', 'can_approve_region', 'can_approve_headquarters',
      'can_register_external', 'can_verify_external', 'can_withdraw', 'can_cancel', 'can_manage_supply'
    ],
    '正式需求访问上下文'
  )
  if (object.schema_version !== contract.MATERIAL_REQUEST_SCHEMA_VERSION) {
    throw adapterError('正式需求访问上下文版本不受支持')
  }
  if ([
    'can_read', 'can_create', 'can_read_material_catalog', 'can_approve_region',
    'can_approve_headquarters', 'can_register_external', 'can_verify_external',
    'can_withdraw', 'can_cancel', 'can_manage_supply'
  ].some((field) => typeof object[field] !== 'boolean')) {
    throw adapterError('正式需求访问授权无效')
  }
  if (
    (object.can_create || object.can_withdraw || object.can_cancel || object.can_manage_supply) &&
    !object.can_read
  ) {
    throw adapterError('正式需求写权限缺少必需的读取回验权限')
  }
  return Object.freeze({
    schema_version: contract.MATERIAL_REQUEST_SCHEMA_VERSION,
    person_id: uuidValue(object.person_id, 'person_id'),
    authorization_version: positiveVersion(object.authorization_version, 'authorization_version'),
    can_read: object.can_read,
    can_create: object.can_create,
    can_read_material_catalog: object.can_read_material_catalog,
    can_approve_region: object.can_approve_region,
    can_approve_headquarters: object.can_approve_headquarters,
    can_register_external: object.can_register_external,
    can_verify_external: object.can_verify_external,
    can_withdraw: object.can_withdraw,
    can_cancel: object.can_cancel,
    can_manage_supply: object.can_manage_supply
  })
}

function validateFormalIdentity(value, expectedIdentity) {
  const object = exactObject(value, [
    'person_id', 'name', 'employee_no', 'organization_code', 'organization_name',
    'account_status', 'employment_status', 'access_mode', 'authorization_version',
    'role_codes'
  ], '正式登录身份')
  const personId = uuidValue(object.person_id, 'person_id')
  const authorizationVersion = positiveVersion(
    object.authorization_version,
    'authorization_version'
  )
  if (!expectedIdentity || typeof expectedIdentity !== 'object') {
    throw adapterError('本地登录身份不可用，已停止终止命令恢复', 401)
  }
  if (
    personId !== uuidValue(expectedIdentity.person_id, 'expected_person_id') ||
    authorizationVersion !== positiveVersion(
      expectedIdentity.authorization_version,
      'expected_authorization_version'
    )
  ) throw adapterError('新鲜登录身份或授权版本已变化，终止恢复哨兵已保留')
  if (
    object.account_status !== 'active' ||
    object.employment_status !== 'active' ||
    object.access_mode !== 'active'
  ) throw adapterError('新鲜登录身份不是有效在职访问状态，终止恢复哨兵已保留')
  const roleCodes = Array.isArray(object.role_codes)
    ? object.role_codes.map((role) => requiredText(role, 'role_code'))
    : []
  if (
    !roleCodes.length ||
    new Set(roleCodes).size !== roleCodes.length ||
    roleCodes.some((role) => !SUPPORTED_ROLES.has(role)) ||
    !roleCodes.some((role) => INTERNAL_ROLES.has(role))
  ) throw adapterError('新鲜登录身份角色无效，终止恢复哨兵已保留')
  return Object.freeze({
    person_id: personId,
    name: requiredText(object.name, 'name'),
    employee_no: requiredText(object.employee_no, 'employee_no'),
    organization_code: requiredText(object.organization_code, 'organization_code'),
    organization_name: requiredText(object.organization_name, 'organization_name'),
    account_status: object.account_status,
    employment_status: object.employment_status,
    access_mode: object.access_mode,
    authorization_version: authorizationVersion,
    role_codes: Object.freeze(roleCodes)
  })
}

function validateLifecycleCommandStatus(value) {
  const object = exactObject(
    value,
    ['schema_version', 'lookup_status', 'command'],
    '需求终止命令查询响应'
  )
  if (object.schema_version !== contract.MATERIAL_REQUEST_SCHEMA_VERSION) {
    throw adapterError('需求终止命令查询响应版本不受支持')
  }
  if (!['not_observed', 'confirmed'].includes(object.lookup_status)) {
    throw adapterError('需求终止命令查询状态无效')
  }
  if (object.lookup_status === 'not_observed') {
    if (object.command !== null) {
      throw adapterError('未观察到命令时不得返回命令内容')
    }
    return Object.freeze({
      schema_version: contract.MATERIAL_REQUEST_SCHEMA_VERSION,
      lookup_status: 'not_observed',
      command: null
    })
  }
  const command = exactObject(object.command, [
    'action', 'request_id', 'request_version', 'revision_id', 'revision_no',
    'approval_instance_id', 'approval_attempt_no', 'current_step_id', 'states',
    'occurred_at'
  ], '已确认需求终止命令')
  if (!['withdraw', 'cancel'].includes(command.action)) {
    throw adapterError('已确认命令不是撤回或取消')
  }
  const requestVersion = positiveVersion(command.request_version, 'request_version')
  const states = Object.freeze(contract.validateMaterialRequestStateAxes(command.states))
  const expectedStatus = command.action === 'withdraw' ? 'withdrawn' : 'cancelled'
  if (states.request_status !== expectedStatus) {
    throw adapterError('已确认命令动作与申请终态不一致')
  }
  if (command.current_step_id !== null) {
    throw adapterError('已确认终止命令不得保留当前审批步骤')
  }
  if (
    typeof command.occurred_at !== 'string' ||
    !AWARE_TIMESTAMP.test(command.occurred_at) ||
    !Number.isFinite(Date.parse(command.occurred_at))
  ) throw adapterError('已确认终止命令发生时间无效')
  return Object.freeze({
    schema_version: contract.MATERIAL_REQUEST_SCHEMA_VERSION,
    lookup_status: 'confirmed',
    command: Object.freeze({
      action: command.action,
      request_id: uuidValue(command.request_id, 'request_id'),
      request_version: requestVersion,
      revision_id: uuidValue(command.revision_id, 'revision_id'),
      revision_no: positiveVersion(command.revision_no, 'revision_no'),
      approval_instance_id: uuidValue(
        command.approval_instance_id,
        'approval_instance_id'
      ),
      approval_attempt_no: positiveVersion(
        command.approval_attempt_no,
        'approval_attempt_no'
      ),
      current_step_id: null,
      states,
      occurred_at: command.occurred_at
    })
  })
}

function projectAccessContext(value, expectedIdentity) {
  const object = exactObject(value, [
    'person_id',
    'account_status',
    'employment_status',
    'authorization_version',
    'access_mode',
    'role_codes',
    'assignments',
    'permissions'
  ], '正式访问上下文')
  if (!expectedIdentity || typeof expectedIdentity !== 'object') {
    throw adapterError('登录身份或授权版本缺失')
  }
  const personId = uuidValue(object.person_id, 'person_id')
  const authorizationVersion = positiveVersion(
    object.authorization_version,
    'authorization_version'
  )
  if (
    personId !== uuidValue(expectedIdentity.person_id, 'expected_person_id') ||
    authorizationVersion !== positiveVersion(
      expectedIdentity.authorization_version,
      'expected_authorization_version'
    )
  ) throw adapterError('登录身份或授权版本与正式访问上下文不一致')
  if (
    object.account_status !== 'active' ||
    object.employment_status !== 'active' ||
    object.access_mode !== 'active'
  ) throw adapterError('当前身份不是可执行需求提报的有效在职访问状态')
  if (!Array.isArray(object.role_codes) || !object.role_codes.length) {
    throw adapterError('正式访问上下文角色无效')
  }
  const roleCodes = object.role_codes.map((role) => requiredText(role, 'role_code'))
  if (
    new Set(roleCodes).size !== roleCodes.length ||
    roleCodes.some((role) => !SUPPORTED_ROLES.has(role)) ||
    !roleCodes.some((role) => INTERNAL_ROLES.has(role))
  ) throw adapterError('正式访问上下文角色无效')
  if (!Array.isArray(object.assignments) || !Array.isArray(object.permissions)) {
    throw adapterError('正式访问上下文授权结构无效')
  }
  const permissionKeys = object.permissions.map((value, index) => {
    const permission = exactObject(
      value,
      ['resource', 'action', 'field_code'],
      `正式权限 ${index + 1}`
    )
    const resource = requiredText(permission.resource, 'permission.resource')
    const action = requiredText(permission.action, 'permission.action')
    if (
      typeof permission.field_code !== 'string' ||
      permission.field_code !== permission.field_code.trim()
    ) throw adapterError('permission.field_code无效')
    return `${resource}\u0000${action}\u0000${permission.field_code}`
  })
  if (new Set(permissionKeys).size !== permissionKeys.length) {
    throw adapterError('正式访问上下文包含重复权限')
  }
  const canRead = permissionKeys.includes('material_request\u0000read\u0000')
  const canCreate = canRead && permissionKeys.includes('material_request\u0000create\u0000')
  return validateAccess({
    schema_version: contract.MATERIAL_REQUEST_SCHEMA_VERSION,
    person_id: personId,
    authorization_version: authorizationVersion,
    can_read: canRead,
    can_create: canCreate,
    can_read_material_catalog: permissionKeys.includes('inventory\u0000read\u0000'),
    can_approve_region: permissionKeys.includes(
      'material_request\u0000approve_region\u0000approval_decision'
    ),
    can_approve_headquarters: permissionKeys.includes(
      'material_request\u0000approve_headquarters\u0000approval_decision'
    ),
    can_register_external: permissionKeys.includes(
      'material_request\u0000register_external\u0000approval_evidence'
    ),
    can_verify_external: permissionKeys.includes(
      'material_request\u0000verify_external\u0000approval_evidence'
    ),
    can_withdraw: canRead && permissionKeys.includes(
      'material_request\u0000withdraw\u0000'
    ),
    can_cancel: canRead && permissionKeys.includes(
      'material_request\u0000cancel\u0000'
    ),
    can_manage_supply: canRead && permissionKeys.includes('supply_task\u0000manage\u0000')
  })
}

function validateEditableDraft(value, expectedRequestId, expectedVersion) {
  const object = exactObject(
    value,
    ['schema_version', 'request_id', 'request_version', 'draft'],
    '正式需求可编辑草稿'
  )
  if (object.schema_version !== contract.MATERIAL_REQUEST_SCHEMA_VERSION) {
    throw adapterError('正式需求可编辑草稿版本不受支持')
  }
  const requestId = uuidValue(object.request_id, 'request_id')
  if (requestId !== uuidValue(expectedRequestId, 'expected_request_id')) {
    throw adapterError('正式需求可编辑草稿与目标对象不一致')
  }
  const requestVersion = nonnegativeVersion(object.request_version, 'request_version')
  if (requestVersion !== nonnegativeVersion(expectedVersion, 'expected_version')) {
    throw adapterError('正式需求可编辑草稿版本已变化，请重新读取')
  }
  return Object.freeze({
    schema_version: contract.MATERIAL_REQUEST_SCHEMA_VERSION,
    request_id: requestId,
    request_version: requestVersion,
    draft: contract.validateMaterialRequestDraftInput(object.draft)
  })
}

function validateWriteHeaders(value, signature) {
  const headers = exactObject(
    value,
    ['X-Request-ID', 'Idempotency-Key'],
    '正式需求写请求头'
  )
  if (
    !SAFE_REQUEST_ID.test(headers['X-Request-ID']) ||
    !SAFE_IDEMPOTENCY_KEY.test(headers['Idempotency-Key'])
  ) throw adapterError('正式需求写请求坐标无效')
  if (signature !== headers['Idempotency-Key']) {
    throw adapterError('正式需求写意图签名与幂等键不一致')
  }
  return headers
}

function writeOptions(intent) {
  const headers = validateWriteHeaders(intent.headers, intent.signature)
  return {
    header: headers,
    requestId: headers['X-Request-ID'],
    idempotencyKey: headers['Idempotency-Key']
  }
}

function mutationMethod(intent) {
  const object = exactObject(intent, [
    'request_id', 'action', 'path', 'body', 'expected_version', 'signature', 'headers'
  ], '正式需求写意图')
  const requestId = uuidValue(object.request_id, 'request_id')
  const action = requiredText(object.action, 'action')
  if (!contract.MUTATION_ACTIONS.includes(action)) throw adapterError('正式需求写动作无效')
  const expectedVersion = nonnegativeVersion(object.expected_version, 'expected_version')
  if (!object.body || typeof object.body !== 'object' || Array.isArray(object.body)) {
    throw adapterError('正式需求写内容不是有效对象')
  }
  const versionField = ['update', 'submit', 'withdraw', 'cancel'].includes(action)
    ? 'expected_version'
    : 'expected_request_version'
  if (object.body[versionField] !== expectedVersion) {
    throw adapterError('正式需求写内容版本与意图不一致')
  }
  validateSupportedMutationBody(action, object.body)
  validateWriteHeaders(object.headers, object.signature)

  const root = `/v1/material-requests/${requestId}`
  const step = `${root}/approval-steps/(${UUID_PATH_SOURCE})`
  let expectedPath
  let method = 'POST'
  switch (action) {
    case 'update':
      expectedPath = root
      method = 'PUT'
      break
    case 'submit':
      expectedPath = `${root}/submit`
      break
    case 'withdraw':
      expectedPath = `${root}/withdraw`
      break
    case 'cancel':
      expectedPath = `${root}/cancel`
      break
    case 'create_supply_task':
      expectedPath = `${root}/supply-tasks`
      break
    case 'update_supply_task':
    case 'cancel_supply_task':
      expectedPath = new RegExp(`^${root}/supply-tasks/(${UUID_PATH_SOURCE})$`, 'i')
      break
    case 'approve':
    case 'return':
    case 'reject':
      expectedPath = new RegExp(`^${step}/decision$`, 'i')
      if (object.body.action !== action) {
        throw adapterError('审批动作与正式需求写意图不一致')
      }
      break
    case 'register_external_approval':
      expectedPath = new RegExp(`^${step}/external-evidence$`, 'i')
      break
    case 'verify_external_approval':
      expectedPath = new RegExp(
        `^${step}/external-evidence/(${UUID_PATH_SOURCE})/verification$`,
        'i'
      )
      break
    default:
      throw adapterError('该正式需求写动作尚无已验收后端接口')
  }
  if (
    typeof object.path !== 'string' ||
    (typeof expectedPath === 'string'
      ? object.path !== expectedPath
      : !expectedPath.test(object.path))
  ) throw adapterError('正式需求写路径与动作不一致')
  const pathUuids = object.path.match(new RegExp(UUID_PATH_SOURCE, 'gi')) || []
  if (
    !pathUuids.length ||
    pathUuids.some((value) => uuidValue(value, 'path_uuid') !== value)
  ) throw adapterError('正式需求写路径必须使用规范 UUID')
  return method
}

function createFormalMaterialRequestAdapter(options = {}) {
  const transport = options.transport || api
  const expectedIdentityProvider = options.expectedIdentityProvider || session.getUser
  if (
    !transport ||
    typeof transport.get !== 'function' ||
    typeof transport.request !== 'function' ||
    typeof transport.post !== 'function' ||
    typeof transport.put !== 'function' ||
    typeof expectedIdentityProvider !== 'function'
  ) throw adapterError('正式需求客户端 transport 配置无效', 503)

  return Object.freeze({
    async loadIdentity() {
      const expectedIdentity = expectedIdentityProvider()
      if (!expectedIdentity) throw adapterError('当前登录身份不可用', 401)
      return validateFormalIdentity(
        await transport.request('/auth/me', {
          method: 'GET',
          header: { 'Cache-Control': 'no-store', Pragma: 'no-cache' }
        }),
        expectedIdentity
      )
    },
    async loadAccess(freshIdentity) {
      const expectedIdentity = freshIdentity || expectedIdentityProvider()
      if (!expectedIdentity) throw adapterError('当前登录身份不可用', 401)
      return projectAccessContext(
        await transport.request('/access/context', {
          method: 'GET',
          header: { 'Cache-Control': 'no-store', Pragma: 'no-cache' }
        }),
        expectedIdentity
      )
    },
    async lifecycleCommandStatus(xRequestId) {
      if (typeof xRequestId !== 'string' || !SAFE_REQUEST_ID.test(xRequestId)) {
        throw adapterError('需求终止命令查询请求标识无效')
      }
      return validateLifecycleCommandStatus(await transport.request(
        '/v1/material-request-lifecycle-command-status',
        {
          method: 'GET',
          header: {
            'X-Request-ID': xRequestId,
            'Cache-Control': 'no-store',
            Pragma: 'no-cache'
          }
        }
      ))
    },
    async supplyCommandStatus(xRequestId) {
      if (typeof xRequestId !== 'string' || !SAFE_REQUEST_ID.test(xRequestId)) {
        throw adapterError('供给命令查询请求标识无效')
      }
      return contract.validateMaterialRequestSupplyCommandStatus(await transport.request(
        `/v1/material-request-supply-command-status?trace_request_id=${encodeURIComponent(xRequestId)}`,
        { method: 'GET', header: { 'Cache-Control': 'no-store', Pragma: 'no-cache' } }
      ))
    },
    async allocationCommandStatus(xRequestId) {
      if (typeof xRequestId !== 'string' || !SAFE_REQUEST_ID.test(xRequestId)) {
        throw adapterError('分配命令查询请求标识无效')
      }
      return contract.validateMaterialRequestAllocationCommandStatus(await transport.request(
        '/v1/material-request-allocation-command-status',
        {
          method: 'GET',
          header: {
            'X-Request-ID': xRequestId,
            'Cache-Control': 'no-store',
            Pragma: 'no-cache'
          }
        }
      ))
    },
    async createAllocation(requestId, input, headers) {
      const checkedRequestId = uuidValue(requestId, 'request_id')
      const object = exactObject(input, [
        'expected_request_version', 'request_line_id', 'source_stock_account_id', 'allocated_qty',
        'source_balance_version', 'source_ledger_cursor', 'serial_ids'
      ], '分配写内容')
      const body = {
        expected_request_version: nonnegativeVersion(object.expected_request_version, 'expected_request_version'),
        request_line_id: uuidValue(object.request_line_id, 'request_line_id'),
        source_stock_account_id: uuidValue(object.source_stock_account_id, 'source_stock_account_id'),
        allocated_qty: decimalText(object.allocated_qty, 'allocated_qty', true),
        source_balance_version: nonnegativeVersion(object.source_balance_version, 'source_balance_version'),
        source_ledger_cursor: nonnegativeVersion(object.source_ledger_cursor, 'source_ledger_cursor'),
        serial_ids: Array.isArray(object.serial_ids)
          ? object.serial_ids.map((serialId) => uuidValue(serialId, 'serial_id'))
          : (() => { throw adapterError('分配串码内容无效') })()
      }
      if (body.serial_ids.length > 1000 || new Set(body.serial_ids).size !== body.serial_ids.length) {
        throw adapterError('分配串码不能重复或超出上限')
      }
      const options = writeOptions({
        request_id: checkedRequestId,
        action: 'create_allocation',
        path: `/v1/material-requests/${checkedRequestId}/allocations`,
        body,
        expected_version: body.expected_request_version,
        signature: headers && headers['Idempotency-Key'],
        headers
      })
      return contract.validateMaterialRequestAllocationMutationResult(
        await transport.post(`/v1/material-requests/${checkedRequestId}/allocations`, body, options)
      )
    },
    list(afterId) {
      const suffix = afterId === null
        ? ''
        : `&after_id=${encodeURIComponent(uuidValue(afterId, 'after_id'))}`
      return transport.request(`/v1/material-requests?limit=50${suffix}`, {
        method: 'GET',
        header: { 'Cache-Control': 'no-store', Pragma: 'no-cache' }
      })
    },
    detail(requestId) {
      return transport.request(`/v1/material-requests/${uuidValue(requestId, 'request_id')}`, {
        method: 'GET',
        header: { 'Cache-Control': 'no-store', Pragma: 'no-cache' }
      })
    },
    loadDraftForEdit(requestId) {
      return transport.request(
        `/v1/material-requests/${uuidValue(requestId, 'request_id')}/editable-draft`,
        {
          method: 'GET',
          header: { 'Cache-Control': 'no-store', Pragma: 'no-cache' }
        }
      )
    },
    listMaterials(query, afterId) {
      const checkedQuery = materialCatalog.validateQuery(query)
      const queryPart = checkedQuery ? `&query=${encodeURIComponent(checkedQuery)}` : ''
      const cursorPart = afterId === null
        ? ''
        : `&after_id=${encodeURIComponent(uuidValue(afterId, 'after_id'))}`
      return transport.request(`/v1/materials?limit=50${queryPart}${cursorPart}`, {
        method: 'GET',
        header: { 'Cache-Control': 'no-store', Pragma: 'no-cache' }
      })
    },
    listWorkOrders(query, afterId) {
      const checkedQuery = materialRequestOptions.validateQuery(query)
      const queryPart = checkedQuery ? `&query=${encodeURIComponent(checkedQuery)}` : ''
      const cursorPart = afterId === null
        ? ''
        : `&after_id=${encodeURIComponent(
          materialRequestOptions.validateWorkOrderId(afterId)
        )}`
      return transport.request(
        `/v1/material-request-options/work-orders?limit=50${queryPart}${cursorPart}`,
        {
          method: 'GET',
          header: { 'Cache-Control': 'no-store', Pragma: 'no-cache' }
        }
      )
    },
    detailWorkOrder(workOrderId) {
      const checkedId = materialRequestOptions.validateWorkOrderId(workOrderId)
      return transport.request(
        `/v1/material-request-options/work-orders/${checkedId}`,
        {
          method: 'GET',
          header: { 'Cache-Control': 'no-store', Pragma: 'no-cache' }
        }
      )
    },
    createDraft(intent) {
      const object = exactObject(intent, [
        'client_draft_key', 'action', 'path', 'body', 'signature', 'headers'
      ], '正式需求创建意图')
      if (object.action !== 'create' || object.path !== '/v1/material-requests') {
        return Promise.reject(adapterError('正式需求创建意图路径或动作无效'))
      }
      const headers = validateWriteHeaders(object.headers, object.signature)
      if (object.client_draft_key !== `draft-${headers['X-Request-ID']}`) {
        return Promise.reject(adapterError('正式需求草稿锚点与请求坐标不一致'))
      }
      contract.validateMaterialRequestDraftInput(object.body)
      return transport.post(object.path, object.body, writeOptions(intent))
    },
    mutate(intent) {
      let method
      try {
        method = mutationMethod(intent)
      } catch (error) {
        return Promise.reject(error)
      }
      const options = writeOptions(intent)
      return method === 'PUT'
        ? transport.put(intent.path, intent.body, options)
        : transport.post(intent.path, intent.body, options)
    }
  })
}

const formalMaterialRequestAdapter = createFormalMaterialRequestAdapter()

function isDefinitiveRejection(error) {
  return Boolean(
    error &&
    error.responseReceived === true &&
    Number.isInteger(error.status) &&
    error.status >= 400 &&
    error.status < 500 &&
    error.status !== 408 &&
    error.status !== 425
  )
}

const DEFINITIVE_SUPPLY_POST_REJECTIONS = Object.freeze({
  404: Object.freeze({
    category: 'not_found',
    codes: new Set(['supply_task_not_found'])
  }),
  409: Object.freeze({
    category: 'conflict',
    codes: new Set([
      'material_request_version_conflict',
      'material_request_supply_line_not_current',
      'material_request_supply_quantity_exceeds_approved',
      'supply_task_not_current_revision',
      'supply_task_version_conflict',
      'supply_task_terminal',
      'supply_task_status_transition_invalid',
      'supply_task_cancel_metadata_changed'
    ])
  }),
  412: Object.freeze({
    category: 'precondition_failed',
    codes: new Set([
      'material_request_supply_line_not_approved',
      'material_request_supply_active_substitution_exists'
    ])
  })
})

function isDefinitiveSupplyPostRejection(error) {
  if (!error || error.responseReceived !== true || !Number.isInteger(error.status)) return false
  const rule = DEFINITIVE_SUPPLY_POST_REJECTIONS[error.status]
  return Boolean(
    rule && error.category === rule.category &&
    typeof error.code === 'string' && rule.codes.has(error.code)
  )
}

module.exports = {
  validateSupplyBody(action, body) {
    if (!['create_supply_task', 'update_supply_task', 'cancel_supply_task'].includes(action)) {
      throw adapterError('供给操作无效')
    }
    nonnegativeVersion(body.expected_request_version, 'expected_request_version')
    validateSupportedMutationBody(action, body)
  },
  validateAccess,
  validateFormalIdentity,
  validateLifecycleCommandStatus,
  projectAccessContext,
  validateEditableDraft,
  createFormalMaterialRequestAdapter,
  formalMaterialRequestAdapter,
  isDefinitiveRejection,
  isDefinitiveSupplyPostRejection
}
