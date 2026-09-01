const api = require('./api')
const contract = require('./material-request-contract')
const materialCatalog = require('./material-catalog-contract')
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

function validateSupportedMutationBody(action, body) {
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
      'can_register_external', 'can_verify_external'
    ],
    '正式需求访问上下文'
  )
  if (object.schema_version !== contract.MATERIAL_REQUEST_SCHEMA_VERSION) {
    throw adapterError('正式需求访问上下文版本不受支持')
  }
  if ([
    'can_read', 'can_create', 'can_read_material_catalog', 'can_approve_region',
    'can_approve_headquarters', 'can_register_external', 'can_verify_external'
  ].some((field) => typeof object[field] !== 'boolean')) {
    throw adapterError('正式需求访问授权无效')
  }
  if (object.can_create && !object.can_read) {
    throw adapterError('正式需求创建权限缺少必需的读取回验权限')
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
    can_verify_external: object.can_verify_external
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
    )
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
    async loadAccess() {
      const expectedIdentity = expectedIdentityProvider()
      if (!expectedIdentity) throw adapterError('当前登录身份不可用', 401)
      return projectAccessContext(
        await transport.get('/access/context'),
        expectedIdentity
      )
    },
    list(afterId) {
      const suffix = afterId === null
        ? ''
        : `&after_id=${encodeURIComponent(uuidValue(afterId, 'after_id'))}`
      return transport.get(`/v1/material-requests?limit=50${suffix}`)
    },
    detail(requestId) {
      return transport.get(`/v1/material-requests/${uuidValue(requestId, 'request_id')}`)
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

module.exports = {
  validateAccess,
  projectAccessContext,
  validateEditableDraft,
  createFormalMaterialRequestAdapter,
  formalMaterialRequestAdapter,
  isDefinitiveRejection
}
