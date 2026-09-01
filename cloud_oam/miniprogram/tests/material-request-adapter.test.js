const assert = require('node:assert/strict')
const test = require('node:test')

const adapterModule = require('../utils/material-request-adapter')
const contract = require('../utils/material-request-contract')

const PERSON_ID = '10000000-0000-4000-8000-000000000001'
const REQUEST_ID = '20000000-0000-4000-8000-000000000001'
const STEP_ID = '30000000-0000-4000-8000-000000000001'
const REGISTRATION_ID = '40000000-0000-4000-8000-000000000001'
const MATERIAL_ID = '50000000-0000-4000-8000-000000000001'

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
      { resource: 'material_request', action: 'verify_external', field_code: 'approval_evidence' }
    ]
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
    can_verify_external: true
  })
  assert.deepEqual(transport.calls, [
    { method: 'GET', path: '/access/context', params: undefined }
  ])

  const createOnly = accessContext()
  createOnly.permissions = [
    { resource: 'material_request', action: 'create', field_code: '' }
  ]
  const projected = await adapter(fakeTransport(createOnly)).loadAccess()
  assert.equal(projected.can_read, false)
  assert.equal(projected.can_create, false)
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

  assert.deepEqual(transport.calls.map((call) => call.path), [
    '/v1/material-requests?limit=50',
    `/v1/material-requests?limit=50&after_id=${REQUEST_ID}`,
    `/v1/material-requests/${REQUEST_ID}`,
    `/v1/material-requests/${REQUEST_ID}/editable-draft`,
    '/v1/materials?limit=50&query=SKU%20A',
    `/v1/materials?limit=50&query=SKU%20A&after_id=${MATERIAL_ID}`
  ])
  assert.deepEqual(transport.calls[3].options, {
    method: 'GET',
    header: { 'Cache-Control': 'no-store', Pragma: 'no-cache' }
  })
  assert.deepEqual(transport.calls[4].options, {
    method: 'GET',
    header: { 'Cache-Control': 'no-store', Pragma: 'no-cache' }
  })
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

test('transport admits only implemented approval/evidence paths and blocks dormant actions', async () => {
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
  assert.deepEqual(
    transport.calls.map((call) => call.path),
    [approve.path, register.path, verify.path]
  )

  const unsupported = contract.createMaterialRequestIntentRegistry({
    coordinateFactory: coordinateFactory()
  }).begin({
    requestId: REQUEST_ID,
    action: 'withdraw',
    path: `/v1/material-requests/${REQUEST_ID}/withdraw`,
    body: { expected_version: 4 },
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
  assert.equal(transport.calls.length, 3)
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
