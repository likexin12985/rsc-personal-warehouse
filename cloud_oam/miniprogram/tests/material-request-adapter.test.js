const assert = require('node:assert/strict')
const test = require('node:test')

const adapterModule = require('../utils/material-request-adapter')
const contract = require('../utils/material-request-contract')

const PERSON_ID = '10000000-0000-4000-8000-000000000001'
const REQUEST_ID = '20000000-0000-4000-8000-000000000001'
const STEP_ID = '30000000-0000-4000-8000-000000000001'
const REGISTRATION_ID = '40000000-0000-4000-8000-000000000001'
const MATERIAL_ID = '50000000-0000-4000-8000-000000000001'
const WORK_ORDER_ID = '60000000-0000-4000-8000-000000000001'
const SUPPLY_TASK_ID = '70000000-0000-4000-8000-000000000001'

function supplyBody(action) {
  return action === 'create_supply_task' ? {
    expected_request_version: 7, request_line_id: MATERIAL_ID,
    supply_type: 'star_replenishment', reference_no: 'SUP-001',
    expected_qty: '2.500', expected_date: '2026-09-20', note: ''
  } : {
    expected_request_version: 7, expected_task_version: 0,
    status: action === 'cancel_supply_task' ? 'cancelled' : 'reference_registered',
    reference_no: 'SUP-001', expected_date: '2026-09-20', comment: '计划处理'
  }
}

test('supply rejection recovery accepts only exact post-replay status category and code tuples', () => {
  const accepted = [
    [404, 'not_found', 'supply_task_not_found'],
    [409, 'conflict', 'material_request_version_conflict'],
    [409, 'conflict', 'material_request_supply_line_not_current'],
    [409, 'conflict', 'material_request_supply_quantity_exceeds_approved'],
    [409, 'conflict', 'supply_task_not_current_revision'],
    [409, 'conflict', 'supply_task_version_conflict'],
    [409, 'conflict', 'supply_task_terminal'],
    [409, 'conflict', 'supply_task_status_transition_invalid'],
    [409, 'conflict', 'supply_task_cancel_metadata_changed'],
    [412, 'precondition_failed', 'material_request_supply_line_not_approved'],
    [412, 'precondition_failed', 'material_request_supply_active_substitution_exists']
  ]
  for (const [status, category, code] of accepted) {
    assert.equal(adapterModule.isDefinitiveSupplyPostRejection({
      status, category, code, responseReceived: true
    }), true)
  }
  for (const error of [
    { status: 403, category: 'forbidden', code: 'material_request_supply_manage_forbidden', responseReceived: true },
    { status: 409, category: 'conflict', code: 'material_request_supply_idempotency_conflict', responseReceived: true },
    { status: 409, category: 'conflict', code: 'material_request_supply_concurrent_conflict', responseReceived: true },
    { status: 409, category: 'conflict', code: 'unknown_conflict', responseReceived: true },
    { status: 409, category: 'precondition_failed', code: 'material_request_version_conflict', responseReceived: true },
    { status: 409, category: 'conflict', code: 'material_request_version_conflict', responseReceived: false },
    { status: 500, category: 'conflict', code: 'material_request_version_conflict', responseReceived: true },
    { status: 409, category: 'conflict', responseReceived: true }
  ]) assert.equal(adapterModule.isDefinitiveSupplyPostRejection(error), false)
})

test('supply plans use exact POST paths and preserve original intent coordinates', async () => {
  for (const action of ['create_supply_task', 'update_supply_task', 'cancel_supply_task']) {
    const transport = fakeTransport()
    const registry = contract.createMaterialRequestIntentRegistry({ coordinateFactory: coordinateFactory() })
    const path = `/v1/material-requests/${REQUEST_ID}/supply-tasks`
      + (action === 'create_supply_task' ? '' : `/${SUPPLY_TASK_ID}`)
    const args = { requestId: REQUEST_ID, action, path, body: supplyBody(action), expectedVersion: 7 }
    const intent = registry.begin(args)
    assert.equal(registry.begin(args), intent)
    await adapter(transport).mutate(intent)
    assert.equal(transport.calls[0].method, 'POST')
    assert.equal(transport.calls[0].path, path)
    assert.equal(transport.calls[0].data, intent.body)
    assert.equal(transport.calls[0].options.header, intent.headers)
  }
})

test('supply plan invalid facts fail before transport', async () => {
  for (const [action, change] of [
    ['create_supply_task', { expected_qty: 2.5 }],
    ['create_supply_task', { expected_qty: '0.000' }],
    ['create_supply_task', { reference_no: 'A'.repeat(161) }],
    ['create_supply_task', { expected_date: '2026-02-30' }],
    ['create_supply_task', { supply_type: 'shipment' }],
    ['create_supply_task', { personal_inbound_status: 'posted' }],
    ['update_supply_task', { status: 'cancelled' }],
    ['update_supply_task', { status: 'fulfilled' }],
    ['update_supply_task', { reference_no: null }],
    ['update_supply_task', { expected_task_version: true }],
    ['cancel_supply_task', { comment: '' }]
  ]) {
    const transport = fakeTransport()
    const actionPath = `/v1/material-requests/${REQUEST_ID}/supply-tasks`
      + (action === 'create_supply_task' ? '' : `/${SUPPLY_TASK_ID}`)
    const intent = contract.createMaterialRequestIntentRegistry({ coordinateFactory: coordinateFactory() }).begin({
      requestId: REQUEST_ID, action, path: actionPath,
      body: Object.assign(supplyBody(action), change), expectedVersion: 7
    })
    await assert.rejects(adapter(transport).mutate(intent))
    assert.equal(transport.calls.length, 0)
  }
})

function accessContext() {
  return {
    person_id: PERSON_ID,
    account_status: 'active',
    employment_status: 'active',
    authorization_version: 7,
    access_mode: 'active',
    role_codes: ['technician'],
    assignments: [],
    permissions: [
      { resource: 'material_request', action: 'read', field_code: '' },
      { resource: 'material_request', action: 'create', field_code: '' },
      { resource: 'material_request', action: 'update_draft', field_code: '' },
      { resource: 'inventory', action: 'read', field_code: '' },
      { resource: 'material_request', action: 'approve_region', field_code: 'approval_decision' },
      { resource: 'material_request', action: 'approve_headquarters', field_code: 'approval_decision' },
      { resource: 'material_request', action: 'register_external', field_code: 'approval_evidence' },
      { resource: 'material_request', action: 'verify_external', field_code: 'approval_evidence' },
      { resource: 'material_request', action: 'withdraw', field_code: '' },
      { resource: 'material_request', action: 'cancel', field_code: '' }
    ]
  }
}

function formalIdentity() {
  return {
    person_id: PERSON_ID,
    name: '李工程师',
    employee_no: 'E-001',
    organization_code: 'ORG-JS',
    organization_name: '江苏区域公司',
    account_status: 'active',
    employment_status: 'active',
    access_mode: 'active',
    authorization_version: 7,
    role_codes: ['technician']
  }
}

function commandStatus(action = 'withdraw') {
  return {
    schema_version: '1.0',
    lookup_status: 'confirmed',
    command: {
      action,
      request_id: REQUEST_ID,
      request_version: 2,
      revision_id: '60000000-0000-4000-8000-000000000001',
      revision_no: 1,
      approval_instance_id: '70000000-0000-4000-8000-000000000001',
      approval_attempt_no: 1,
      current_step_id: null,
      states: {
        request_status: action === 'withdraw' ? 'withdrawn' : 'cancelled',
        allocation_status: 'not_allocated',
        reservation_status: 'not_reserved',
        outbound_status: 'not_started',
        shipment_status: 'not_started',
        logistics_signature_status: 'not_signed',
        oam_receipt_status: 'not_occurred',
        personal_inbound_status: 'not_started',
        notification_status: 'not_started',
        reconciliation_status: 'not_started'
      },
      occurred_at: '2026-09-01T09:00:00+08:00'
    }
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
    attachment_file_ids: [],
    note: '请及时处理',
    lines: [{
      material_id: MATERIAL_ID,
      requested_qty: '1.000',
      required_date: null,
      suggested_substitute_material_id: null,
      note: '故障替换'
    }]
  }
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

function fakeTransport(response = { ok: true }) {
  const calls = []
  return {
    calls,
    async get(path, params) {
      calls.push({ method: 'GET', path, params })
      return typeof response === 'function' ? response(path) : response
    },
    async request(path, options) {
      calls.push({ method: options.method || 'GET', path, options })
      return typeof response === 'function' ? response(path) : response
    },
    async post(path, data, options) {
      calls.push({ method: 'POST', path, data, options })
      return typeof response === 'function' ? response(path) : response
    },
    async put(path, data, options) {
      calls.push({ method: 'PUT', path, data, options })
      return typeof response === 'function' ? response(path) : response
    }
  }
}

function adapter(transport, identity = {
  person_id: PERSON_ID,
  authorization_version: 7
}) {
  return adapterModule.createFormalMaterialRequestAdapter({
    transport,
    expectedIdentityProvider: () => identity
  })
}

test('loadAccess projects only fresh matching material-request read/create grants', async () => {
  const transport = fakeTransport(accessContext())
  assert.deepEqual(await adapter(transport).loadAccess(), {
    schema_version: '1.0',
    person_id: PERSON_ID,
    authorization_version: 7,
    can_read: true,
    can_create: true,
    can_read_material_catalog: true,
    can_approve_region: true,
    can_approve_headquarters: true,
    can_register_external: true,
    can_verify_external: true,
    can_withdraw: true,
    can_cancel: true,
    can_manage_supply: false
  })
  assert.deepEqual(transport.calls, [
    {
      method: 'GET',
      path: '/access/context',
      options: {
        method: 'GET',
        header: { 'Cache-Control': 'no-store', Pragma: 'no-cache' }
      }
    }
  ])

  const createOnly = accessContext()
  createOnly.permissions = [
    { resource: 'material_request', action: 'create', field_code: '' }
  ]
  const projected = await adapter(fakeTransport(createOnly)).loadAccess()
  assert.equal(projected.can_read, false)
  assert.equal(projected.can_create, false)
})

test('fresh identity and lifecycle command status use exact no-store contracts', async () => {
  const responseByPath = {
    '/auth/me': formalIdentity(),
    '/v1/material-request-lifecycle-command-status': commandStatus()
  }
  const transport = fakeTransport((path) => responseByPath[path])
  const client = adapter(transport)

  assert.deepEqual(await client.loadIdentity(), formalIdentity())
  const status = await client.lifecycleCommandStatus(`wxreq-${'a'.repeat(36)}`)
  assert.equal(status.lookup_status, 'confirmed')
  assert.equal(status.command.action, 'withdraw')
  assert.deepEqual(transport.calls, [
    {
      method: 'GET',
      path: '/auth/me',
      options: {
        method: 'GET',
        header: { 'Cache-Control': 'no-store', Pragma: 'no-cache' }
      }
    },
    {
      method: 'GET',
      path: '/v1/material-request-lifecycle-command-status',
      options: {
        method: 'GET',
        header: {
          'X-Request-ID': `wxreq-${'a'.repeat(36)}`,
          'Cache-Control': 'no-store',
          Pragma: 'no-cache'
        }
      }
    }
  ])

  const notObserved = adapter(fakeTransport({
    schema_version: '1.0', lookup_status: 'not_observed', command: null
  }))
  assert.deepEqual(
    await notObserved.lifecycleCommandStatus(`wxreq-${'b'.repeat(36)}`),
    { schema_version: '1.0', lookup_status: 'not_observed', command: null }
  )
})

test('fresh identity or lifecycle command contract drift fails closed', async () => {
  const changedIdentity = formalIdentity()
  changedIdentity.authorization_version = 8
  await assert.rejects(
    adapter(fakeTransport(changedIdentity)).loadIdentity(),
    /授权版本已变化/
  )

  const extraCommandField = commandStatus()
  extraCommandField.command.idempotency_key_hash = 'forbidden'
  await assert.rejects(
    adapter(fakeTransport(extraCommandField)).lifecycleCommandStatus(
      `wxreq-${'c'.repeat(36)}`
    ),
    /精确包含正式字段/
  )

  const wrongTerminalState = commandStatus()
  wrongTerminalState.command.states.request_status = 'approval_in_progress'
  await assert.rejects(
    adapter(fakeTransport(wrongTerminalState)).lifecycleCommandStatus(
      `wxreq-${'d'.repeat(36)}`
    ),
    /动作与申请终态不一致/
  )
})

test('loadAccess fails closed on identity, authorization and permission-shape drift', async () => {
  await assert.rejects(
    adapter(fakeTransport(accessContext()), {
      person_id: '10000000-0000-4000-8000-000000000002',
      authorization_version: 7
    }).loadAccess(),
    (error) => error.status === 409
  )
  await assert.rejects(
    adapter(fakeTransport(accessContext()), {
      person_id: PERSON_ID,
      authorization_version: 8
    }).loadAccess(),
    (error) => error.status === 409
  )
  const malformed = accessContext()
  malformed.permissions[0].legacy = true
  await assert.rejects(
    adapter(fakeTransport(malformed)).loadAccess(),
    (error) => error.status === 409
  )
})

test('formal reads use canonical endpoints and plaintext edit requests are explicitly no-store', async () => {
  const transport = fakeTransport()
  const client = adapter(transport)
  await client.list(null)
  await client.list(REQUEST_ID.toUpperCase())
  await client.detail(REQUEST_ID.toUpperCase())
  await client.loadDraftForEdit(REQUEST_ID.toUpperCase())
  await client.listMaterials('SKU A', null)
  await client.listMaterials('SKU A', MATERIAL_ID.toUpperCase())
  await client.listWorkOrders('WO A', null)
  await client.listWorkOrders('WO A', WORK_ORDER_ID.toUpperCase())
  await client.detailWorkOrder(WORK_ORDER_ID.toUpperCase())

  assert.deepEqual(transport.calls.map((call) => call.path), [
    '/v1/material-requests?limit=50',
    `/v1/material-requests?limit=50&after_id=${REQUEST_ID}`,
    `/v1/material-requests/${REQUEST_ID}`,
    `/v1/material-requests/${REQUEST_ID}/editable-draft`,
    '/v1/materials?limit=50&query=SKU%20A',
    `/v1/materials?limit=50&query=SKU%20A&after_id=${MATERIAL_ID}`,
    '/v1/material-request-options/work-orders?limit=50&query=WO%20A',
    `/v1/material-request-options/work-orders?limit=50&query=WO%20A&after_id=${WORK_ORDER_ID}`,
    `/v1/material-request-options/work-orders/${WORK_ORDER_ID}`
  ])
  assert.deepEqual(transport.calls[3].options, {
    method: 'GET',
    header: { 'Cache-Control': 'no-store', Pragma: 'no-cache' }
  })
  assert.deepEqual(transport.calls[4].options, {
    method: 'GET',
    header: { 'Cache-Control': 'no-store', Pragma: 'no-cache' }
  })
  for (const index of [0, 1, 2, 5, 6, 7, 8]) {
    assert.deepEqual(transport.calls[index].options, {
      method: 'GET',
      header: { 'Cache-Control': 'no-store', Pragma: 'no-cache' }
    })
  }
})

test('formal work-order option reads reject ambiguous inputs before transport', () => {
  const transport = fakeTransport()
  const client = adapter(transport)
  assert.throws(() => client.listWorkOrders(' WO-1', null), /检索词无效/)
  assert.throws(
    () => client.detailWorkOrder('00000000-0000-0000-0000-000000000000'),
    /work_order_id无效/
  )
  assert.deepEqual(transport.calls, [])
})

test('create and update pass exact intent path, body and coordinates to api.js', async () => {
  const transport = fakeTransport()
  const client = adapter(transport)
  const createIntent = contract.createMaterialRequestCreateIntentRegistry({
    coordinateFactory: coordinateFactory()
  }).begin({ body: draft() })
  await client.createDraft(createIntent)
  const createCall = transport.calls[0]
  assert.equal(createCall.path, createIntent.path)
  assert.equal(createCall.data, createIntent.body)
  assert.equal(createCall.options.header, createIntent.headers)
  assert.equal(createCall.options.requestId, createIntent.headers['X-Request-ID'])
  assert.equal(
    createCall.options.idempotencyKey,
    createIntent.headers['Idempotency-Key']
  )

  const updateIntent = contract.createMaterialRequestIntentRegistry({
    coordinateFactory: coordinateFactory()
  }).begin({
    requestId: REQUEST_ID,
    action: 'update',
    path: `/v1/material-requests/${REQUEST_ID}`,
    body: Object.assign({}, draft(), { expected_version: 0 }),
    expectedVersion: 0
  })
  await client.mutate(updateIntent)
  const updateCall = transport.calls[1]
  assert.equal(updateCall.method, 'PUT')
  assert.equal(updateCall.path, updateIntent.path)
  assert.equal(updateCall.data, updateIntent.body)
  assert.equal(updateCall.options.header, updateIntent.headers)
})

test('transport admits implemented lifecycle, approval and evidence paths and blocks dormant actions', async () => {
  const transport = fakeTransport()
  const client = adapter(transport)
  const approve = contract.createMaterialRequestIntentRegistry({
    coordinateFactory: coordinateFactory()
  }).begin({
    requestId: REQUEST_ID,
    action: 'approve',
    path: `/v1/material-requests/${REQUEST_ID}/approval-steps/${STEP_ID}/decision`,
    body: {
      expected_request_version: 3,
      expected_step_version: 0,
      action: 'approve',
      lines: [{ request_line_id: MATERIAL_ID, approved_qty: '1.000', reason: '' }],
      return_lines: [],
      comment: ''
    },
    expectedVersion: 3
  })
  await client.mutate(approve)

  const register = contract.createMaterialRequestIntentRegistry({
    coordinateFactory: coordinateFactory()
  }).begin({
    requestId: REQUEST_ID,
    action: 'register_external_approval',
    path: `/v1/material-requests/${REQUEST_ID}/approval-steps/${STEP_ID}/external-evidence`,
    body: {
      expected_request_version: 4,
      expected_step_version: 1,
      evidence_file_id: MATERIAL_ID,
      external_approver_name: '星星总部审批人',
      external_reference_no: 'STAR-001',
      external_decided_at: '2026-09-01T08:00:00+08:00',
      action: 'approve',
      lines: [{ request_line_id: MATERIAL_ID, approved_qty: '1.000', reason: '' }],
      return_lines: [],
      comment: ''
    },
    expectedVersion: 4
  })
  await client.mutate(register)

  const verify = contract.createMaterialRequestIntentRegistry({
    coordinateFactory: coordinateFactory()
  }).begin({
    requestId: REQUEST_ID,
    action: 'verify_external_approval',
    path: `/v1/material-requests/${REQUEST_ID}/approval-steps/${STEP_ID}/external-evidence/${REGISTRATION_ID}/verification`,
    body: {
      expected_request_version: 5,
      expected_step_version: 2,
      decision: 'accept',
      comment: ''
    },
    expectedVersion: 5
  })
  await client.mutate(verify)

  const withdraw = contract.createMaterialRequestIntentRegistry({
    coordinateFactory: coordinateFactory()
  }).begin({
    requestId: REQUEST_ID,
    action: 'withdraw',
    path: `/v1/material-requests/${REQUEST_ID}/withdraw`,
    body: { expected_version: 6, reason: '审批中需求变更' },
    expectedVersion: 6
  })
  await client.mutate(withdraw)

  const cancel = contract.createMaterialRequestIntentRegistry({
    coordinateFactory: coordinateFactory()
  }).begin({
    requestId: REQUEST_ID,
    action: 'cancel',
    path: `/v1/material-requests/${REQUEST_ID}/cancel`,
    body: {
      expected_version: 7,
      reason: '需求不再存在',
      lines: [{
        request_line_id: MATERIAL_ID,
        cancelled_qty: '1.000',
        reason: '取消全部批准数量'
      }]
    },
    expectedVersion: 7
  })
  await client.mutate(cancel)

  const zeroApprovedCancel = contract.createMaterialRequestIntentRegistry({
    coordinateFactory: coordinateFactory()
  }).begin({
    requestId: REQUEST_ID,
    action: 'cancel',
    path: `/v1/material-requests/${REQUEST_ID}/cancel`,
    body: {
      expected_version: 8,
      reason: '退回后取消且无最终批准量',
      lines: []
    },
    expectedVersion: 8
  })
  await client.mutate(zeroApprovedCancel)
  assert.deepEqual(
    transport.calls.map((call) => call.path),
    [
      approve.path,
      register.path,
      verify.path,
      withdraw.path,
      cancel.path,
      zeroApprovedCancel.path
    ]
  )

  const unsupported = contract.createMaterialRequestIntentRegistry({
    coordinateFactory: coordinateFactory()
  }).begin({
    requestId: REQUEST_ID,
    action: 'propose_substitution',
    path: `/v1/material-requests/${REQUEST_ID}/substitutions`,
    body: { expected_request_version: 4 },
    expectedVersion: 4
  })
  await assert.rejects(
    client.mutate(unsupported),
    (error) => error.status === 409
  )

  const staleApprovalPath = contract.createMaterialRequestIntentRegistry({
    coordinateFactory: coordinateFactory()
  }).begin({
    requestId: REQUEST_ID,
    action: 'approve',
    path: `/v1/material-requests/${REQUEST_ID}/approval/decision`,
    body: {
      expected_request_version: 4,
      expected_step_version: 0,
      action: 'approve',
      lines: [{ request_line_id: MATERIAL_ID, approved_qty: '1.000', reason: '' }],
      return_lines: [],
      comment: ''
    },
    expectedVersion: 4
  })
  await assert.rejects(
    client.mutate(staleApprovalPath),
    (error) => error.status === 409
  )
  assert.equal(transport.calls.length, 6)
})

test('transport rejects malformed approval bodies before api.js', async () => {
  const transport = fakeTransport()
  const client = adapter(transport)
  const malformed = contract.createMaterialRequestIntentRegistry({
    coordinateFactory: coordinateFactory()
  }).begin({
    requestId: REQUEST_ID,
    action: 'approve',
    path: `/v1/material-requests/${REQUEST_ID}/approval-steps/${STEP_ID}/decision`,
    body: { action: 'approve', expected_request_version: 3 },
    expectedVersion: 3
  })
  await assert.rejects(client.mutate(malformed), (error) => error.status === 409)

  const malformedCancel = contract.createMaterialRequestIntentRegistry({
    coordinateFactory: coordinateFactory()
  }).begin({
    requestId: REQUEST_ID,
    action: 'cancel',
    path: `/v1/material-requests/${REQUEST_ID}/cancel`,
    body: {
      expected_version: 3,
      reason: '需求不再存在',
      lines: [{ request_line_id: MATERIAL_ID, cancelled_qty: '1.000' }]
    },
    expectedVersion: 3
  })
  await assert.rejects(client.mutate(malformedCancel), (error) => error.status === 409)
  assert.equal(transport.calls.length, 0)
})

test('an uncertain retry reuses the same intent and never creates replacement coordinates', async () => {
  const calls = []
  const transport = fakeTransport()
  transport.post = async (path, data, options) => {
    calls.push({ path, data, options })
    throw new Error('network uncertain')
  }
  const client = adapter(transport)
  const intent = contract.createMaterialRequestIntentRegistry({
    coordinateFactory: coordinateFactory()
  }).begin({
    requestId: REQUEST_ID,
    action: 'submit',
    path: `/v1/material-requests/${REQUEST_ID}/submit`,
    body: { expected_version: 0 },
    expectedVersion: 0
  })
  await assert.rejects(client.mutate(intent), /network uncertain/)
  await assert.rejects(client.mutate(intent), /network uncertain/)
  assert.equal(calls[0].data, intent.body)
  assert.equal(calls[1].data, intent.body)
  assert.equal(calls[0].options.header, intent.headers)
  assert.equal(calls[1].options.header, intent.headers)
})

test('editable draft version zero remains valid and raw values stay in the returned memory object', () => {
  const parsed = adapterModule.validateEditableDraft({
    schema_version: '1.0',
    request_id: REQUEST_ID,
    request_version: 0,
    draft: draft()
  }, REQUEST_ID, 0)
  assert.equal(parsed.draft.contact.mobile, '138 0000 0000')
  assert.equal(Object.isFrozen(parsed.draft), true)
})
