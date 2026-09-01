const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')

const adapterContract = require('../utils/material-request-adapter')
const formalFileUpload = require('../utils/formal-file-upload')
const lifecycleRecovery = require('../utils/material-request-lifecycle-recovery')

function loadPage(relativePath, stubs) {
  const savedModules = []
  for (const [modulePath, exports] of Object.entries(stubs)) {
    const resolved = require.resolve(modulePath)
    savedModules.push([resolved, require.cache[resolved]])
    require.cache[resolved] = {
      id: resolved,
      filename: resolved,
      loaded: true,
      exports
    }
  }
  const resolvedPage = require.resolve(relativePath)
  delete require.cache[resolvedPage]
  let definition
  global.Page = (value) => { definition = value }
  require(resolvedPage)
  return {
    definition,
    restore() {
      delete require.cache[resolvedPage]
      for (const [resolved, saved] of savedModules) {
        if (saved) require.cache[resolved] = saved
        else delete require.cache[resolved]
      }
      delete global.Page
    }
  }
}

function pageInstance(definition) {
  return Object.assign({}, definition, {
    data: JSON.parse(JSON.stringify(definition.data)),
    setData(update) { Object.assign(this.data, update) }
  })
}

const REQUEST_ID = '10000000-0000-4000-8000-000000000001'
const LINE_ID = '20000000-0000-4000-8000-000000000001'
const PERSON_ID = '30000000-0000-4000-8000-000000000001'
const PERSON_2_ID = '30000000-0000-4000-8000-000000000002'
const ORG_ID = '40000000-0000-4000-8000-000000000001'
const MATERIAL_ID = '50000000-0000-4000-8000-000000000001'
const MATERIAL_2_ID = '50000000-0000-4000-8000-000000000002'
const INSTANCE_ID = '60000000-0000-4000-8000-000000000001'
const STEP_1_ID = '70000000-0000-4000-8000-000000000001'
const STEP_2_ID = '70000000-0000-4000-8000-000000000002'
const STEP_3_ID = '70000000-0000-4000-8000-000000000003'
const REGISTRATION_ID = '80000000-0000-4000-8000-000000000001'
const ATTACHMENT_ID = '90000000-0000-4000-8000-000000000001'
const ATTACHMENT_2_ID = '90000000-0000-4000-8000-000000000002'
const EVIDENCE_ID = '90000000-0000-4000-8000-000000000003'
const REVISION_ID = 'a0000000-0000-4000-8000-000000000001'
const FILE_SHA = 'ab'.repeat(32)
const RAW_MOBILE = '138 0000 0000'
const RAW_ADDRESS = '江东中路 100 号'

function material() {
  return {
    material_id: MATERIAL_ID,
    sku_code: 'SKU-A',
    name: '交流接触器',
    specification: '32A',
    base_unit: '件',
    tracking_mode: 'serial',
    quantity_scale: 0,
    allow_fraction: false,
    source_updated_at: '2026-09-01T07:00:00+08:00'
  }
}

function catalogPage() {
  return { schema_version: '1.0', items: [material()], next_after_id: null }
}

function material2() {
  return Object.assign({}, material(), {
    material_id: MATERIAL_2_ID,
    sku_code: 'SKU-B',
    name: '直流接触器',
    specification: '64A',
    tracking_mode: 'lot'
  })
}

function axes(status = 'draft') {
  return {
    request_status: status,
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

function detail(version = 0) {
  return {
    schema_version: '1.0',
    request_id: REQUEST_ID,
    request_no: 'MR-20260901-0001',
    request_version: version,
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
      detail_masked: '******'
    },
    contact_masked: { name_masked: '李*', mobile_masked: '*******0000' },
    note: '请及时处理',
    attachment_refs: [{
      revision_id: REVISION_ID,
      revision_no: 1,
      request_line_id: null,
      file_id: ATTACHMENT_ID,
      display_name: '故障照片.jpg',
      purpose: 'request_attachment'
    }],
    approval_mode: 'external_registration',
    states: axes(),
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
      note: '故障替换',
      final_approved_qty: '0.000',
      cancelled_qty: '0.000',
      status: 'draft',
      version
    }],
    revision_history: [{
      revision_id: REVISION_ID,
      revision_no: 1,
      previous_revision_id: null,
      status: 'draft',
      line_count: 1,
      attachment_count: 1,
      sealed_at: null,
      created_at: '2026-09-01T08:00:00+08:00'
    }],
    approval_history: [],
    supply_tasks: [],
    allowed_actions: ['update', 'submit'],
    created_at: '2026-09-01T08:00:00+08:00',
    updated_at: version ? '2026-09-01T08:10:00+08:00' : '2026-09-01T08:00:00+08:00',
    submitted_at: null
  }
}

function submittedDetail() {
  const value = detail(1)
  value.states = axes('approval_in_progress')
  value.submitted_at = '2026-09-01T08:10:00+08:00'
  value.revision_history[0].status = 'sealed'
  value.revision_history[0].sealed_at = value.submitted_at
  value.approval_instance = {
    instance_id: INSTANCE_ID,
    request_revision_id: REVISION_ID,
    revision_no: 1,
    attempt_no: 1,
    status: 'active',
    current_step_no: 1,
    current_step_id: STEP_1_ID,
    version: 0,
    steps: [
      {
        step_id: STEP_1_ID,
        step_no: 1,
        attempt_no: 1,
        predecessor_step_id: null,
        supersedes_step_id: null,
        reopened_from_step_id: null,
        source_mode: 'internal',
        status: 'open',
        assignee_snapshot: { name_masked: '省＊＊＊＊', role_code: 'provincial_manager' },
        candidate_pool_summary: null,
        opened_at: '2026-09-01T08:10:00+08:00',
        decided_at: null,
        version: 0,
        line_decisions: []
      },
      {
        step_id: STEP_2_ID,
        step_no: 2,
        attempt_no: 1,
        predecessor_step_id: STEP_1_ID,
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
        step_id: STEP_3_ID,
        step_no: 3,
        attempt_no: 1,
        predecessor_step_id: STEP_2_ID,
        supersedes_step_id: null,
        reopened_from_step_id: null,
        source_mode: 'external_registration',
        status: 'pending',
        assignee_snapshot: null,
        candidate_pool_summary: {
          candidate_count: 4,
          candidate_kinds: ['registrar', 'verifier']
        },
        opened_at: null,
        decided_at: null,
        version: 0,
        line_decisions: []
      }
    ],
    external_evidence_summaries: null,
    return_line_facts: []
  }
  value.approval_history = [value.approval_instance]
  value.lines[0].status = 'approval_pending'
  value.allowed_actions = ['withdraw']
  return value
}

function returnedDetail() {
  const value = submittedDetail()
  value.request_version = 2
  value.updated_at = '2026-09-01T08:20:00+08:00'
  value.states = axes('returned')
  value.approval_instance.status = 'returned'
  value.approval_instance.current_step_no = null
  value.approval_instance.current_step_id = null
  value.approval_instance.version = 1
  value.approval_instance.steps[0].status = 'returned'
  value.approval_instance.steps[0].decided_at = '2026-09-01T08:20:00+08:00'
  value.approval_instance.steps[1].status = 'cancelled'
  value.approval_instance.steps[2].status = 'cancelled'
  value.approval_instance.return_line_facts = [{
    return_fact_id: 'c0000000-0000-4000-8000-000000000001',
    return_action_id: 'd0000000-0000-4000-8000-000000000001',
    instance_id: INSTANCE_ID,
    returned_from_step_id: STEP_1_ID,
    target_kind: 'requester_revision',
    target_step_id: null,
    request_revision_id: REVISION_ID,
    revision_no: 1,
    request_line_id: LINE_ID,
    returned_step_input_qty: '12.345',
    target_step_max_qty: '12.345',
    required_review_qty: '12.345',
    reason: '请补充故障证据',
    occurred_at: '2026-09-01T08:20:00+08:00'
  }]
  value.allowed_actions = ['update', 'submit']
  return value
}

function withdrawnDetail() {
  const value = submittedDetail()
  value.request_version = 2
  value.updated_at = '2026-09-01T08:20:00+08:00'
  value.states = axes('withdrawn')
  value.allowed_actions = []
  value.approval_instance.status = 'withdrawn'
  value.approval_instance.current_step_no = null
  value.approval_instance.current_step_id = null
  value.approval_instance.version = 1
  value.approval_instance.steps = value.approval_instance.steps.map((step) => Object.assign({}, step, {
    status: 'cancelled',
    decided_at: null,
    version: step.version + 1,
    line_decisions: []
  }))
  return value
}

function cancellableReturnedDetail() {
  const value = returnedDetail()
  value.allowed_actions = ['cancel']
  value.lines[0].final_approved_qty = '4.000'
  return value
}

function cancelledDetail() {
  const value = cancellableReturnedDetail()
  value.request_version = 3
  value.updated_at = '2026-09-01T08:30:00+08:00'
  value.states = axes('cancelled')
  value.allowed_actions = []
  value.lines[0].cancelled_qty = '4.000'
  value.lines[0].status = 'cancelled'
  value.lines[0].version += 1
  return value
}

function lineDecision(stepId, decisionId) {
  return {
    decision_id: decisionId,
    step_id: stepId,
    request_revision_id: REVISION_ID,
    revision_no: 1,
    request_line_id: LINE_ID,
    input_qty: '12.345',
    approved_qty: '12.345',
    rejected_qty: '0.000',
    reason: '',
    decision_source: 'internal',
    external_registration_id: null,
    decided_at: stepId === STEP_1_ID
      ? '2026-09-01T08:20:00+08:00'
      : '2026-09-01T08:30:00+08:00'
  }
}

function internalApprovalDetail() {
  const value = submittedDetail()
  value.allowed_actions = ['approve', 'return', 'reject']
  return value
}

function headquartersApprovalDetail() {
  const value = submittedDetail()
  value.request_version = 2
  value.updated_at = '2026-09-01T08:20:00+08:00'
  value.approval_instance.current_step_no = 2
  value.approval_instance.current_step_id = STEP_2_ID
  value.approval_instance.version = 1
  value.approval_instance.steps[0] = Object.assign({}, value.approval_instance.steps[0], {
    status: 'approved',
    decided_at: '2026-09-01T08:20:00+08:00',
    version: 1,
    line_decisions: [lineDecision(STEP_1_ID, '71000000-0000-4000-8000-000000000001')]
  })
  value.approval_instance.steps[1] = Object.assign({}, value.approval_instance.steps[1], {
    status: 'open',
    opened_at: '2026-09-01T08:20:00+08:00',
    version: 1
  })
  value.allowed_actions = ['approve', 'return', 'reject']
  return value
}

function externalRegistrationDetail() {
  const value = submittedDetail()
  value.request_version = 3
  value.updated_at = '2026-09-01T08:30:00+08:00'
  value.approval_instance.current_step_no = 3
  value.approval_instance.current_step_id = STEP_3_ID
  value.approval_instance.version = 2
  value.approval_instance.steps[0] = Object.assign({}, value.approval_instance.steps[0], {
    status: 'approved',
    decided_at: '2026-09-01T08:20:00+08:00',
    version: 1,
    line_decisions: [lineDecision(STEP_1_ID, '71000000-0000-4000-8000-000000000001')]
  })
  value.approval_instance.steps[1] = Object.assign({}, value.approval_instance.steps[1], {
    status: 'approved',
    opened_at: '2026-09-01T08:20:00+08:00',
    decided_at: '2026-09-01T08:30:00+08:00',
    version: 1,
    line_decisions: [lineDecision(STEP_2_ID, '72000000-0000-4000-8000-000000000001')]
  })
  value.approval_instance.steps[2] = Object.assign({}, value.approval_instance.steps[2], {
    status: 'awaiting_external_evidence',
    opened_at: '2026-09-01T08:30:00+08:00'
  })
  value.allowed_actions = ['register_external_approval']
  return value
}

function externalVerificationDetail() {
  const value = externalRegistrationDetail()
  value.request_version = 4
  value.updated_at = '2026-09-01T08:40:00+08:00'
  value.approval_instance.version = 3
  value.approval_instance.steps[2].status = 'evidence_pending_verification'
  value.approval_instance.steps[2].version = 1
  value.approval_instance.external_evidence_summaries = [{
    registration_id: REGISTRATION_ID,
    registration_no: 'STAR-20260901-001',
    step_id: STEP_3_ID,
    external_action: 'approve',
    status: 'pending_verification',
    evidence_file_id: ATTACHMENT_ID,
    external_approver_name_masked: '王*',
    external_decided_at: '2026-09-01T08:35:00+08:00',
    registered_at: '2026-09-01T08:40:00+08:00',
    verified_at: null,
    version: 0
  }]
  value.allowed_actions = ['verify_external_approval']
  return value
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
      detail: RAW_ADDRESS
    },
    contact: { name: '李工程师', mobile: RAW_MOBILE },
    attachment_file_ids: [ATTACHMENT_ID],
    note: '请及时处理',
    lines: [{
      material_id: MATERIAL_ID,
      requested_qty: '12.345',
      required_date: '2026-09-05',
      suggested_substitute_material_id: null,
      note: '故障替换'
    }]
  }
}

function form(attachmentFileIds = [ATTACHMENT_ID]) {
  return {
    workOrderId: '',
    purpose: '现场故障处理',
    urgency: 'urgent',
    expectedDate: '2026-09-05',
    provinceCode: '320000',
    provinceName: '江苏省',
    cityName: '南京市',
    districtName: '建邺区',
    addressDetail: RAW_ADDRESS,
    contactName: '李工程师',
    contactMobile: RAW_MOBILE,
    attachmentFileIds: attachmentFileIds.slice(),
    note: '请及时处理',
    lines: [{
      key: 1,
      material: material(),
      requestedQty: '12.345',
      requiredDate: '2026-09-05',
      substituteMaterial: null,
      note: '故障替换'
    }]
  }
}

function page(value = detail()) {
  const summary = JSON.parse(JSON.stringify(value))
  delete summary.schema_version
  delete summary.lines
  delete summary.revision_history
  delete summary.approval_history
  delete summary.supply_tasks
  summary.line_count = value.lines.length
  return { schema_version: '1.0', items: [summary], next_after_id: null }
}

function access() {
  return {
    schema_version: '1.0',
    person_id: PERSON_ID,
    authorization_version: 1,
    can_read: true,
    can_create: true,
    can_read_material_catalog: true,
    can_approve_region: false,
    can_approve_headquarters: false,
    can_register_external: false,
    can_verify_external: false,
    can_withdraw: false,
    can_cancel: false
  }
}

function freshIdentity() {
  return {
    person_id: PERSON_ID,
    name: '李工程师',
    employee_no: 'E-001',
    organization_code: 'ORG-JS',
    organization_name: '江苏区域公司',
    account_status: 'active',
    employment_status: 'active',
    access_mode: 'active',
    authorization_version: 1,
    role_codes: ['technician']
  }
}

function confirmedLifecycleStatus(action = 'withdraw') {
  return {
    schema_version: '1.0',
    lookup_status: 'confirmed',
    command: {
      action,
      request_id: REQUEST_ID,
      request_version: action === 'withdraw' ? 2 : 3,
      revision_id: REVISION_ID,
      revision_no: 1,
      approval_instance_id: INSTANCE_ID,
      approval_attempt_no: 1,
      current_step_id: null,
      states: axes(action === 'withdraw' ? 'withdrawn' : 'cancelled'),
      occurred_at: '2026-09-01T08:20:00+08:00'
    }
  }
}

function fakeTransport(overrides = {}) {
  return Object.assign({
    async loadIdentity() { return freshIdentity() },
    async loadAccess() { return access() },
    async lifecycleCommandStatus() {
      return { schema_version: '1.0', lookup_status: 'not_observed', command: null }
    },
    async list() { return page() },
    async detail() { return detail() },
    async loadDraftForEdit() {
      return { schema_version: '1.0', request_id: REQUEST_ID, request_version: 0, draft: draft() }
    },
    async listMaterials() { return catalogPage() },
    async createDraft() { throw new Error('not configured') },
    async mutate() { throw new Error('not configured') }
  }, overrides)
}

function adapterStub(transport) {
  return Object.assign({}, adapterContract, { formalMaterialRequestAdapter: transport })
}

function globals(initialStorage = {}) {
  const storage = new Map(Object.entries(initialStorage))
  const storageWrites = []
  const storageRemovals = []
  const toasts = []
  global.wx = {
    showToast(value) { toasts.push(value) },
    stopPullDownRefresh() {},
    getStorageSync(key) { return storage.has(key) ? storage.get(key) : '' },
    setStorageSync(key, value) {
      storage.set(key, value)
      storageWrites.push([key, value])
    },
    removeStorageSync(key) {
      storage.delete(key)
      storageRemovals.push(key)
    }
  }
  return { storage, storageWrites, storageRemovals, toasts }
}

async function waitUntil(predicate, message) {
  for (let attempt = 0; attempt < 50; attempt += 1) {
    if (predicate()) return
    await new Promise((resolve) => setImmediate(resolve))
  }
  assert.fail(message)
}

function uploadFixture(fileIds = {}) {
  const state = { controllers: [], prepareCalls: [], executeCalls: [] }
  const module = Object.assign({}, formalFileUpload, {
    createFormalFileUploadController(options) {
      const fileId = fileIds[options.purpose] || ATTACHMENT_ID
      const controller = formalFileUpload.createFormalFileUploadController(Object.assign({}, options, {
        chooseFiles: async () => [{
          tempFilePath: `/tmp/${options.purpose}.jpg`,
          name: `${options.purpose}.jpg`,
          size: 3,
          mimeType: 'image/jpeg'
        }],
        prepare: async (file, purpose) => {
          const prepared = Object.freeze({
            content: new Uint8Array([1, 2, 3]),
            purpose,
            original_filename: file.name,
            size_bytes: file.size,
            mime_type: file.mimeType,
            sha256: FILE_SHA,
            intent_coordinates: Object.freeze({ requestId: `wxreq-${'a'.repeat(36)}`, idempotencyKey: `wxidem-${'b'.repeat(36)}` }),
            complete_coordinates: Object.freeze({ requestId: `wxreq-${'c'.repeat(36)}`, idempotencyKey: `wxidem-${'d'.repeat(36)}` })
          })
          state.prepareCalls.push([purpose, prepared])
          return prepared
        },
        execute: async (prepared) => {
          state.executeCalls.push([options.purpose, prepared])
          return {
            file_id: fileId,
            purpose: prepared.purpose,
            status: 'available',
            verified_at: '2026-09-01T08:01:00Z',
            sha256: prepared.sha256,
            size_bytes: prepared.size_bytes,
            mime_type: prepared.mime_type
          }
        }
      }))
      state.controllers.push([options.purpose, controller])
      return controller
    }
  })
  return { module, state }
}

function loadWith(transport, uploadModule = formalFileUpload) {
  return loadPage('../pages/formal-material-requests/index', {
    '../utils/session': { ensureLogin: () => true },
    '../utils/material-request-adapter': adapterStub(transport),
    '../utils/formal-file-upload': uploadModule
  })
}

test('page is registered and selects the reviewed formal transport', async (context) => {
  globals()
  const calls = []
  const transport = fakeTransport({
    async loadAccess() { calls.push('access'); return access() },
    async list() { calls.push('list'); return page() }
  })
  const loaded = loadWith(transport)
  context.after(() => {
    loaded.restore()
    delete global.wx
  })
  const instance = pageInstance(loaded.definition)
  await instance.load()
  const app = JSON.parse(fs.readFileSync(path.join(__dirname, '../app.json'), 'utf8'))
  const wxml = fs.readFileSync(
    path.join(__dirname, '../pages/formal-material-requests/index.wxml'),
    'utf8'
  )
  assert.equal(app.pages.includes('pages/formal-material-requests/index'), true)
  assert.match(wxml, /生产写入仍受服务端写 gate 与 runtime ACL 控制/)
  assert.equal(instance.data.accessAllowed, true)
  assert.deepEqual(calls, ['access', 'list'])
})

test('list and detail expose only masked projections and ten separate axes', async (context) => {
  globals()
  const loaded = loadWith(fakeTransport())
  context.after(() => {
    loaded.restore()
    delete global.wx
  })
  const instance = pageInstance(loaded.definition)
  await instance.load()
  assert.equal(instance.data.requests[0].maskedContact, '李* · *******0000')
  assert.equal(JSON.stringify(instance.data).includes(RAW_MOBILE), false)
  await instance.openRequest({ currentTarget: { dataset: { id: REQUEST_ID } } })
  assert.equal(instance.data.detail.stateAxes.length, 10)
  assert.equal(instance.data.detail.maskedAddress, '江苏省南京市建邺区******')
  assert.equal(JSON.stringify(instance.data.detail).includes(RAW_ADDRESS), false)
})

test('create binds a completed request_attachment upload and clears raw form only after exact reread', async (context) => {
  const { storageWrites } = globals()
  const intents = []
  const uploads = uploadFixture({ request_attachment: ATTACHMENT_ID })
  const transport = fakeTransport({
    async createDraft(intent) {
      intents.push(intent)
      return {
        schema_version: '1.0',
        request_id: REQUEST_ID,
        action: 'create',
        request_version: 0,
        revision_id: REVISION_ID,
        revision_no: 1,
        states: axes(),
        idempotency_replayed: false
      }
    }
  })
  const loaded = loadWith(transport, uploads.module)
  context.after(() => {
    loaded.restore()
    delete global.wx
  })
  const instance = pageInstance(loaded.definition)
  await instance.load()
  instance.startCreate()
  instance.setData({ form: form([]) })
  await instance.chooseRequestAttachment()
  assert.equal(instance.data.requestUploadFiles[0].filename, 'request_attachment.jpg')
  assert.equal(instance.data.requestUploadFiles[0].sizeLabel, '3 B')
  assert.equal(instance.data.requestUploadFiles[0].sha256, FILE_SHA)
  assert.equal(instance.data.requestUploadFiles[0].status, 'available')
  await instance.saveDraft()

  assert.equal(intents.length, 1)
  assert.equal(intents[0].action, 'create')
  assert.equal(intents[0].path, '/v1/material-requests')
  assert.deepEqual(intents[0].body, draft())
  assert.equal(uploads.state.prepareCalls[0][0], 'request_attachment')
  assert.equal(uploads.state.executeCalls.length, 1)
  assert.match(intents[0].headers['X-Request-ID'], /^wxreq-[a-f0-9]{36}$/)
  assert.match(intents[0].headers['Idempotency-Key'], /^wxidem-[a-f0-9]{36}$/)
  assert.deepEqual(storageWrites, [])
  assert.equal(instance.data.form, null)
  assert.equal(JSON.stringify(instance.data.detail).includes(RAW_MOBILE), false)
})

test('material picker searches and paginates only the formal catalog', async (context) => {
  globals()
  const calls = []
  const loaded = loadWith(fakeTransport({
    async listMaterials(query, afterId) {
      calls.push([query, afterId])
      if (query) {
        return { schema_version: '1.0', items: [material2()], next_after_id: null }
      }
      if (afterId) {
        return { schema_version: '1.0', items: [material2()], next_after_id: null }
      }
      return Object.assign({}, catalogPage(), { next_after_id: MATERIAL_2_ID })
    }
  }))
  context.after(() => {
    loaded.restore()
    delete global.wx
  })
  const instance = pageInstance(loaded.definition)
  await instance.load()
  instance.startCreate()
  const lineKey = instance.data.form.lines[0].key
  await instance.openMaterialPicker({
    currentTarget: { dataset: { key: lineKey, target: 'material' } }
  })
  assert.deepEqual(instance.data.materialPicker.items.map((item) => item.sku_code), ['SKU-A'])
  await instance.loadMoreMaterials()
  assert.deepEqual(
    instance.data.materialPicker.items.map((item) => item.sku_code),
    ['SKU-A', 'SKU-B']
  )
  instance.materialPickerQueryInput({ detail: { value: '直流接触器' } })
  await instance.searchMaterialPicker()
  assert.deepEqual(instance.data.materialPicker.items.map((item) => item.sku_code), ['SKU-B'])
  assert.deepEqual(calls, [
    ['', null],
    ['', MATERIAL_2_ID],
    ['直流接触器', null]
  ])
})

test('uncertain create retry preserves raw form and reuses exact coordinates', async (context) => {
  globals()
  const intents = []
  const transport = fakeTransport({
    async createDraft(intent) {
      intents.push(intent)
      if (intents.length === 1) throw new Error('network timeout')
      return {
        schema_version: '1.0',
        request_id: REQUEST_ID,
        action: 'create',
        request_version: 0,
        revision_id: REVISION_ID,
        revision_no: 1,
        states: axes(),
        idempotency_replayed: true
      }
    }
  })
  const loaded = loadWith(transport)
  context.after(() => {
    loaded.restore()
    delete global.wx
  })
  const instance = pageInstance(loaded.definition)
  await instance.load()
  instance.startCreate()
  instance.setData({ form: form([]) })
  await instance.saveDraft()
  assert.equal(instance.data.form.contactMobile, RAW_MOBILE)
  assert.equal(instance.data.writePending, true)
  await instance.saveDraft()
  assert.equal(intents.length, 2)
  assert.equal(intents[0], intents[1])
  assert.deepEqual(intents[0].headers, intents[1].headers)
})

test('edit loads raw values only after explicit action and writes through update intent', async (context) => {
  globals()
  const uploads = uploadFixture({ request_attachment: ATTACHMENT_2_ID })
  let editableReads = 0
  const intents = []
  let detailReads = 0
  const transport = fakeTransport({
    async detail() {
      detailReads += 1
      return detailReads === 1 ? detail() : detail(1)
    },
    async loadDraftForEdit() {
      editableReads += 1
      return { schema_version: '1.0', request_id: REQUEST_ID, request_version: 0, draft: draft() }
    },
    async mutate(intent) {
      intents.push(intent)
      return {
        schema_version: '1.0', request_id: REQUEST_ID, action: 'update',
        request_version: 1, revision_id: REVISION_ID, revision_no: 1,
        approval_instance_id: null, approval_attempt_no: null, current_step_id: null,
        states: axes(), idempotency_replayed: false
      }
    }
  })
  const loaded = loadWith(transport, uploads.module)
  context.after(() => {
    loaded.restore()
    delete global.wx
  })
  const instance = pageInstance(loaded.definition)
  await instance.load()
  assert.equal(editableReads, 0)
  await instance.openRequest({ currentTarget: { dataset: { id: REQUEST_ID } } })
  assert.equal(JSON.stringify(instance.data.detail).includes(RAW_MOBILE), false)
  await instance.startEdit()
  assert.equal(editableReads, 1)
  assert.equal(instance.data.form.contactMobile, RAW_MOBILE)
  assert.deepEqual(instance.data.form.attachmentFileIds, [ATTACHMENT_ID])
  await instance.chooseRequestAttachment()
  await instance.saveDraft()
  assert.equal(intents[0].action, 'update')
  assert.equal(intents[0].body.expected_version, 0)
  assert.equal(intents[0].body.contact.mobile, RAW_MOBILE)
  assert.deepEqual(intents[0].body.attachment_file_ids, [ATTACHMENT_ID, ATTACHMENT_2_ID])
  assert.equal(instance.data.form, null)
})

test('edit keeps server-bound attachments and deduplicates an available upload with the same file id', async (context) => {
  globals()
  const uploads = uploadFixture({ request_attachment: ATTACHMENT_ID })
  const intents = []
  let detailReads = 0
  const loaded = loadWith(fakeTransport({
    async detail() {
      detailReads += 1
      return detailReads === 1 ? detail() : detail(1)
    },
    async mutate(intent) {
      intents.push(intent)
      return {
        schema_version: '1.0', request_id: REQUEST_ID, action: 'update',
        request_version: 1, revision_id: REVISION_ID, revision_no: 1,
        approval_instance_id: null, approval_attempt_no: null, current_step_id: null,
        states: axes(), idempotency_replayed: false
      }
    }
  }), uploads.module)
  context.after(() => {
    loaded.restore()
    delete global.wx
  })
  const instance = pageInstance(loaded.definition)
  await instance.load()
  await instance.openRequest({ currentTarget: { dataset: { id: REQUEST_ID } } })
  await instance.startEdit()
  assert.deepEqual(instance.data.form.attachmentFileIds, [ATTACHMENT_ID])
  await instance.chooseRequestAttachment()
  await instance.saveDraft()
  assert.deepEqual(intents[0].body.attachment_file_ids, [ATTACHMENT_ID])
})

test('authorization and identity changes clear only unbound upload memory while server attachment references remain rereadable', async (context) => {
  globals()
  let authorizationVersion = 1
  let personId = PERSON_ID
  let mutationCount = 0
  const uploads = uploadFixture({ request_attachment: ATTACHMENT_2_ID })
  const loaded = loadWith(fakeTransport({
    async loadAccess() {
      return Object.assign({}, access(), {
        person_id: personId,
        authorization_version: authorizationVersion
      })
    },
    async mutate() {
      mutationCount += 1
      throw new Error('unexpected mutation')
    }
  }), uploads.module)
  context.after(() => {
    loaded.restore()
    delete global.wx
  })
  const instance = pageInstance(loaded.definition)
  await instance.load()
  await instance.openRequest({ currentTarget: { dataset: { id: REQUEST_ID } } })
  await instance.startEdit()
  await instance.chooseRequestAttachment()
  assert.deepEqual(instance.data.form.attachmentFileIds, [ATTACHMENT_ID])
  assert.equal(instance.data.requestUploadFiles.length, 1)

  authorizationVersion = 2
  await instance.load()
  assert.equal(instance.data.requestUploadFiles.length, 0)
  assert.equal(instance.data.form, null)
  assert.equal(mutationCount, 0)

  await instance.openRequest({ currentTarget: { dataset: { id: REQUEST_ID } } })
  await instance.startEdit()
  assert.deepEqual(instance.data.form.attachmentFileIds, [ATTACHMENT_ID])
  await instance.chooseRequestAttachment()
  assert.equal(instance.data.requestUploadFiles.length, 1)

  personId = PERSON_2_ID
  await instance.load()
  assert.equal(instance.data.requestUploadFiles.length, 0)
  assert.equal(instance.data.form, null)
  assert.equal(mutationCount, 0)
  await instance.openRequest({ currentTarget: { dataset: { id: REQUEST_ID } } })
  await instance.startEdit()
  assert.deepEqual(instance.data.form.attachmentFileIds, [ATTACHMENT_ID])
})

test('submit requires confirmation and keeps approval separate from every fulfillment axis', async (context) => {
  globals()
  const intents = []
  let detailReads = 0
  const transport = fakeTransport({
    async detail() {
      detailReads += 1
      return detailReads === 1 ? detail() : submittedDetail()
    },
    async mutate(intent) {
      intents.push(intent)
      return {
        schema_version: '1.0', request_id: REQUEST_ID, action: 'submit',
        request_version: 1, revision_id: REVISION_ID, revision_no: 1,
        approval_instance_id: INSTANCE_ID, approval_attempt_no: 1,
        current_step_id: STEP_1_ID,
        states: axes('approval_in_progress'), idempotency_replayed: false
      }
    }
  })
  const loaded = loadWith(transport)
  context.after(() => {
    loaded.restore()
    delete global.wx
  })
  const instance = pageInstance(loaded.definition)
  await instance.load()
  await instance.openRequest({ currentTarget: { dataset: { id: REQUEST_ID } } })
  instance.openSubmitConfirm()
  assert.equal(instance.data.submitConfirm, true)
  assert.equal(intents.length, 0)
  await instance.confirmSubmit()
  assert.equal(intents.length, 1)
  assert.deepEqual(intents[0].body, { expected_version: 0 })
  assert.equal(instance.data.detail.states.request_status, 'approval_in_progress')
  assert.equal(instance.data.detail.states.allocation_status, 'not_allocated')
  const wxml = fs.readFileSync(
    path.join(__dirname, '../pages/formal-material-requests/index.wxml'),
    'utf8'
  )
  assert.match(wxml, /审批通过不等于分配、占用、出库、发货、物流签收、OAM收货、RSC\/个人仓入库、通知送达或对账同步完成/)
})

test('withdraw requires permission plus allowed action, reuses one intent and exact-rereads terminal axes', async (context) => {
  const { storage, storageWrites, storageRemovals } = globals()
  const actionable = submittedDetail()
  const terminal = withdrawnDetail()
  const intents = []
  let detailReads = 0
  const loaded = loadWith(fakeTransport({
    async loadAccess() {
      return Object.assign({}, access(), { can_withdraw: true })
    },
    async detail() {
      detailReads += 1
      return detailReads === 1 ? actionable : terminal
    },
    async mutate(intent) {
      intents.push(intent)
      const sentinel = storage.get(lifecycleRecovery.STORAGE_KEY)
      assert.deepEqual(Object.keys(sentinel).sort(), [
        'created_at', 'kind', 'v', 'x_request_id'
      ])
      assert.equal(sentinel.x_request_id, intent.headers['X-Request-ID'])
      assert.equal(JSON.stringify(sentinel).includes(intent.headers['Idempotency-Key']), false)
      assert.equal(JSON.stringify(sentinel).includes(intent.body.reason), false)
      if (intents.length === 1) throw new Error('network uncertain')
      return {
        schema_version: '1.0', request_id: REQUEST_ID, action: 'withdraw',
        request_version: 2, revision_id: REVISION_ID, revision_no: 1,
        approval_instance_id: INSTANCE_ID, approval_attempt_no: 1,
        current_step_id: null, states: axes('withdrawn'), idempotency_replayed: true
      }
    }
  }))
  context.after(() => {
    loaded.restore()
    delete global.wx
  })
  const instance = pageInstance(loaded.definition)
  await instance.load()
  await instance.openRequest({ currentTarget: { dataset: { id: REQUEST_ID } } })
  assert.equal(instance.data.detail.canWithdraw, true)
  assert.equal(instance.data.detail.canCancel, false)
  instance.openLifecycleConfirm({ currentTarget: { dataset: { action: 'withdraw' } } })
  assert.equal(intents.length, 0)
  assert.match(instance.data.lifecycleConfirm.title, /二次确认/)
  instance.lifecycleReasonInput({ detail: { value: '工单需求已变更' } })

  await instance.confirmLifecycleAction()
  assert.match(instance.data.lifecycleConfirm.pendingMessage, /禁止生成新坐标/)
  await instance.load()
  assert.equal(instance.data.lifecycleConfirm.reason, '工单需求已变更')
  assert.match(instance.data.lifecycleConfirm.pendingMessage, /禁止生成新坐标/)
  await instance.confirmLifecycleAction()

  assert.equal(intents.length, 2)
  assert.equal(intents[0], intents[1])
  assert.equal(intents[0].path, `/v1/material-requests/${REQUEST_ID}/withdraw`)
  assert.deepEqual(intents[0].body, {
    expected_version: 1,
    reason: '工单需求已变更'
  })
  assert.equal(instance.data.detail.states.request_status, 'withdrawn')
  assert.equal(instance.data.detail.states.allocation_status, 'not_allocated')
  assert.equal(instance.data.lifecycleConfirm, null)
  assert.equal(storage.has(lifecycleRecovery.STORAGE_KEY), false)
  assert.equal(storageWrites.length, 1)
  assert.deepEqual(storageRemovals, [lifecycleRecovery.STORAGE_KEY])
})

test('in-flight withdraw survives page unload and a restored page never creates replacement coordinates', async (context) => {
  const { toasts } = globals()
  const actionable = submittedDetail()
  const terminal = withdrawnDetail()
  const intents = []
  let detailReads = 0
  let resolveMutation
  const mutationResult = new Promise((resolve) => { resolveMutation = resolve })
  const transport = fakeTransport({
    async loadAccess() {
      return Object.assign({}, access(), { can_withdraw: true })
    },
    async detail() {
      detailReads += 1
      return detailReads === 1 ? actionable : terminal
    },
    async mutate(intent) {
      intents.push(intent)
      return mutationResult
    }
  })
  const loaded = loadWith(transport)
  context.after(() => {
    loaded.restore()
    delete global.wx
  })

  const oldInstance = pageInstance(loaded.definition)
  let oldUnloaded = false
  const oldSetData = oldInstance.setData
  oldInstance.setData = function setData(update) {
    if (oldUnloaded) throw new Error('unloaded page must not call setData')
    oldSetData.call(this, update)
  }
  await oldInstance.load()
  await oldInstance.openRequest({ currentTarget: { dataset: { id: REQUEST_ID } } })
  oldInstance.openLifecycleConfirm({ currentTarget: { dataset: { action: 'withdraw' } } })
  oldInstance.lifecycleReasonInput({ detail: { value: '工单需求已变更' } })
  const inFlightWrite = oldInstance.confirmLifecycleAction()
  assert.equal(intents.length, 1)

  oldUnloaded = true
  oldInstance.onUnload()
  const restoredInstance = pageInstance(loaded.definition)
  await restoredInstance.load()
  assert.equal(restoredInstance.data.lifecycleConfirm.reason, '工单需求已变更')
  assert.match(restoredInstance.data.lifecycleConfirm.pendingMessage, /禁止生成新坐标/)

  resolveMutation({
    schema_version: '1.0', request_id: REQUEST_ID, action: 'withdraw',
    request_version: 2, revision_id: REVISION_ID, revision_no: 1,
    approval_instance_id: INSTANCE_ID, approval_attempt_no: 1,
    current_step_id: null, states: axes('withdrawn'), idempotency_replayed: false
  })
  await inFlightWrite
  assert.equal(intents.length, 1)
  assert.equal(toasts.some((item) => item.icon === 'success'), false)

  await restoredInstance.confirmLifecycleAction()
  assert.equal(intents.length, 1)
  assert.equal(detailReads, 2)
  assert.equal(restoredInstance.data.detail.states.request_status, 'withdrawn')
  assert.equal(restoredInstance.data.lifecycleConfirm, null)
  assert.equal(toasts.filter((item) => item.icon === 'success').length, 1)
})

test('process restart recovers a confirmed command only after fresh identity, access and exact detail', async (context) => {
  const xRequestId = `wxreq-${'e'.repeat(36)}`
  const sentinel = {
    v: 1,
    kind: 'material_request_lifecycle',
    x_request_id: xRequestId,
    created_at: '2026-09-01T00:19:59.000Z'
  }
  const { storage, storageRemovals } = globals({
    [lifecycleRecovery.STORAGE_KEY]: sentinel
  })
  const calls = []
  let mutations = 0
  const terminal = withdrawnDetail()
  const loaded = loadWith(fakeTransport({
    async loadIdentity() {
      calls.push('auth/me')
      return freshIdentity()
    },
    async loadAccess(identity) {
      calls.push('access/context')
      assert.equal(identity.person_id, PERSON_ID)
      assert.equal(identity.authorization_version, 1)
      return Object.assign({}, access(), { can_withdraw: true })
    },
    async lifecycleCommandStatus(value) {
      calls.push('command-status')
      assert.equal(value, xRequestId)
      return confirmedLifecycleStatus('withdraw')
    },
    async detail(value) {
      calls.push('exact-detail')
      assert.equal(value, REQUEST_ID)
      return terminal
    },
    async list() {
      calls.push('list')
      return page(terminal)
    },
    async mutate() {
      mutations += 1
      throw new Error('recovery must not replay the POST')
    }
  }))
  context.after(() => {
    loaded.restore()
    delete global.wx
  })

  const instance = pageInstance(loaded.definition)
  instance.onLoad()
  assert.match(instance.data.lifecycleRecoveryMessage, /待核验/)
  await instance.load()

  assert.deepEqual(calls, [
    'auth/me', 'access/context', 'command-status', 'exact-detail', 'list'
  ])
  assert.equal(mutations, 0)
  assert.equal(storage.has(lifecycleRecovery.STORAGE_KEY), false)
  assert.deepEqual(storageRemovals, [lifecycleRecovery.STORAGE_KEY])
  assert.equal(instance.data.lifecycleRecoveryMessage, '')
  assert.equal(instance.data.detail.states.request_status, 'withdrawn')
  assert.equal(instance.data.detail.stateAxes.length, 10)
  assert.match(instance.data.notice, /精确回读/)
})

test('process restart applies the independent cancel grant and exact cancelled projection', async (context) => {
  const xRequestId = `wxreq-${'2'.repeat(36)}`
  const { storage } = globals({
    [lifecycleRecovery.STORAGE_KEY]: {
      v: 1,
      kind: 'material_request_lifecycle',
      x_request_id: xRequestId,
      created_at: '2026-09-01T00:29:59.000Z'
    }
  })
  const terminal = cancelledDetail()
  const loaded = loadWith(fakeTransport({
    async loadIdentity() { return freshIdentity() },
    async loadAccess() {
      return Object.assign({}, access(), { can_cancel: true })
    },
    async lifecycleCommandStatus() { return confirmedLifecycleStatus('cancel') },
    async detail() { return terminal },
    async list() { return page(terminal) }
  }))
  context.after(() => {
    loaded.restore()
    delete global.wx
  })

  const instance = pageInstance(loaded.definition)
  await instance.load()
  assert.equal(storage.has(lifecycleRecovery.STORAGE_KEY), false)
  assert.equal(instance.data.detail.states.request_status, 'cancelled')
  assert.equal(instance.data.detail.lines[0].cancelled_qty, '4.000')
  assert.equal(instance.data.detail.states.reservation_status, 'not_reserved')
})

test('confirmed recovery closes a late original POST only for the same exact lifecycle coordinate', async (context) => {
  const { storage, storageRemovals, toasts } = globals()
  const actionable = submittedDetail()
  const terminal = withdrawnDetail()
  let capturedIntent = null
  let detailReads = 0
  let listReads = 0
  let statusReads = 0
  let resolveMutation
  let markMutationStarted
  const mutationStarted = new Promise((resolve) => { markMutationStarted = resolve })
  const mutationResult = new Promise((resolve) => { resolveMutation = resolve })
  const loaded = loadWith(fakeTransport({
    async loadAccess() {
      return Object.assign({}, access(), { can_withdraw: true })
    },
    async list() {
      listReads += 1
      return page(listReads === 1 ? actionable : terminal)
    },
    async detail() {
      detailReads += 1
      return detailReads === 1 ? actionable : terminal
    },
    async mutate(intent) {
      capturedIntent = intent
      markMutationStarted()
      return mutationResult
    },
    async lifecycleCommandStatus(xRequestId) {
      statusReads += 1
      assert.equal(xRequestId, capturedIntent.headers['X-Request-ID'])
      return confirmedLifecycleStatus('withdraw')
    }
  }))
  context.after(() => {
    loaded.restore()
    delete global.wx
  })

  const instance = pageInstance(loaded.definition)
  await instance.load()
  await instance.openRequest({ currentTarget: { dataset: { id: REQUEST_ID } } })
  instance.openLifecycleConfirm({ currentTarget: { dataset: { action: 'withdraw' } } })
  instance.lifecycleReasonInput({ detail: { value: '工单需求已变更' } })
  const originalWrite = instance.confirmLifecycleAction()
  await mutationStarted

  await instance.load()
  assert.equal(statusReads, 1)
  assert.equal(storage.has(lifecycleRecovery.STORAGE_KEY), false)
  assert.equal(instance.data.detail.states.request_status, 'withdrawn')
  assert.equal(instance.data.lifecycleConfirm, null)
  assert.equal(toasts.some((item) => item.icon === 'success'), false)

  resolveMutation({
    schema_version: '1.0', request_id: REQUEST_ID, action: 'withdraw',
    request_version: 2, revision_id: REVISION_ID, revision_no: 1,
    approval_instance_id: INSTANCE_ID, approval_attempt_no: 1,
    current_step_id: null, states: axes('withdrawn'), idempotency_replayed: false
  })
  await originalWrite

  assert.equal(storage.has(lifecycleRecovery.STORAGE_KEY), false)
  assert.deepEqual(storageRemovals, [lifecycleRecovery.STORAGE_KEY])
  assert.equal(detailReads, 3)
  assert.equal(instance.data.detail.states.request_status, 'withdrawn')
  assert.equal(instance.data.lifecycleConfirm, null)
  assert.equal(instance.data.lifecycleRecoveryMessage, '')
  assert.match(instance.data.notice, /精确回读/)
  assert.equal(toasts.filter((item) => item.icon === 'success').length, 1)
})

test('concurrent explicit loads share one local three-attempt recovery budget and a later load gets a new batch', async (context) => {
  const xRequestId = `wxreq-${'3'.repeat(36)}`
  const { storage } = globals({
    [lifecycleRecovery.STORAGE_KEY]: {
      v: 1,
      kind: 'material_request_lifecycle',
      x_request_id: xRequestId,
      created_at: '2026-09-01T00:39:59.000Z'
    }
  })
  let identityReads = 0
  let accessReads = 0
  let statusReads = 0
  let resolveFirstStatus
  const firstStatus = new Promise((resolve) => { resolveFirstStatus = resolve })
  const notObserved = {
    schema_version: '1.0', lookup_status: 'not_observed', command: null
  }
  const loaded = loadWith(fakeTransport({
    async loadIdentity() {
      identityReads += 1
      return freshIdentity()
    },
    async loadAccess() {
      accessReads += 1
      return Object.assign({}, access(), { can_withdraw: true })
    },
    async lifecycleCommandStatus() {
      statusReads += 1
      return statusReads === 1 ? firstStatus : notObserved
    }
  }))
  context.after(() => {
    loaded.restore()
    delete global.wx
  })

  const instance = pageInstance(loaded.definition)
  const firstLoad = instance.load()
  const concurrentLoad = instance.load()
  await waitUntil(() => statusReads === 1, '首个恢复状态查询未开始')
  resolveFirstStatus(notObserved)
  await Promise.all([firstLoad, concurrentLoad])

  assert.equal(identityReads, 1)
  assert.equal(accessReads, 1)
  assert.equal(statusReads, 3)
  assert.equal(storage.has(lifecycleRecovery.STORAGE_KEY), true)
  assert.match(instance.data.lifecycleRecoveryMessage, /尚未观察到终止命令/)

  await instance.load()
  assert.equal(identityReads, 2)
  assert.equal(accessReads, 2)
  assert.equal(statusReads, 6)
  assert.equal(storage.has(lifecycleRecovery.STORAGE_KEY), true)
})

test('not_observed is retried with a finite budget, retains the sentinel and blocks new lifecycle coordinates', async (context) => {
  const xRequestId = `wxreq-${'f'.repeat(36)}`
  const sentinel = {
    v: 1,
    kind: 'material_request_lifecycle',
    x_request_id: xRequestId,
    created_at: '2026-09-01T00:19:59.000Z'
  }
  const { storage, toasts } = globals({
    [lifecycleRecovery.STORAGE_KEY]: sentinel
  })
  const actionable = submittedDetail()
  let statusReads = 0
  let mutations = 0
  const loaded = loadWith(fakeTransport({
    async loadIdentity() { return freshIdentity() },
    async loadAccess() {
      return Object.assign({}, access(), { can_withdraw: true })
    },
    async lifecycleCommandStatus() {
      statusReads += 1
      return { schema_version: '1.0', lookup_status: 'not_observed', command: null }
    },
    async list() { return page(actionable) },
    async detail() { return actionable },
    async mutate() {
      mutations += 1
      throw new Error('blocked recovery must not mutate')
    }
  }))
  context.after(() => {
    loaded.restore()
    delete global.wx
  })

  const instance = pageInstance(loaded.definition)
  await instance.load()
  assert.equal(statusReads, 3)
  assert.equal(storage.has(lifecycleRecovery.STORAGE_KEY), true)
  assert.match(instance.data.lifecycleRecoveryMessage, /尚未观察到终止命令/)
  await instance.openRequest({ currentTarget: { dataset: { id: REQUEST_ID } } })
  assert.equal(instance.data.detail.canWithdraw, false)
  instance.openLifecycleConfirm({ currentTarget: { dataset: { action: 'withdraw' } } })
  assert.equal(instance.data.lifecycleConfirm, null)
  assert.equal(mutations, 0)
  assert.match(toasts[toasts.length - 1].title, /禁止生成新的撤回或取消坐标/)

  await instance.load()
  assert.equal(statusReads, 6)
  assert.equal(storage.has(lifecycleRecovery.STORAGE_KEY), true)
})

test('confirmed recovery without the matching fresh action grant retains the sentinel and skips detail', async (context) => {
  const xRequestId = `wxreq-${'1'.repeat(36)}`
  const { storage } = globals({
    [lifecycleRecovery.STORAGE_KEY]: {
      v: 1,
      kind: 'material_request_lifecycle',
      x_request_id: xRequestId,
      created_at: '2026-09-01T00:19:59.000Z'
    }
  })
  let detailReads = 0
  const loaded = loadWith(fakeTransport({
    async loadIdentity() { return freshIdentity() },
    async loadAccess() { return access() },
    async lifecycleCommandStatus() { return confirmedLifecycleStatus('withdraw') },
    async detail() {
      detailReads += 1
      return withdrawnDetail()
    }
  }))
  context.after(() => {
    loaded.restore()
    delete global.wx
  })

  const instance = pageInstance(loaded.definition)
  await instance.load()
  assert.equal(instance.data.accessAllowed, false)
  assert.equal(detailReads, 0)
  assert.equal(storage.has(lifecycleRecovery.STORAGE_KEY), true)
  assert.match(instance.data.lifecycleRecoveryMessage, /不包含已确认终止命令对应/)
})

test('authorization drift during a live recovery retains the sentinel before status or detail lookup', async (context) => {
  const { storage } = globals()
  const actionable = submittedDetail()
  let statusReads = 0
  let detailReads = 0
  const loaded = loadWith(fakeTransport({
    async loadIdentity() {
      return Object.assign({}, freshIdentity(), { authorization_version: 2 })
    },
    async loadAccess(identity) {
      return Object.assign({}, access(), {
        authorization_version: identity ? 2 : 1,
        can_withdraw: true
      })
    },
    async list() { return page(actionable) },
    async detail() {
      detailReads += 1
      return actionable
    },
    async lifecycleCommandStatus() {
      statusReads += 1
      return confirmedLifecycleStatus('withdraw')
    },
    async mutate() {
      const error = new Error('network uncertain')
      error.status = 0
      error.responseReceived = false
      throw error
    }
  }))
  context.after(() => {
    loaded.restore()
    delete global.wx
  })

  const instance = pageInstance(loaded.definition)
  await instance.load()
  await instance.openRequest({ currentTarget: { dataset: { id: REQUEST_ID } } })
  instance.openLifecycleConfirm({ currentTarget: { dataset: { action: 'withdraw' } } })
  instance.lifecycleReasonInput({ detail: { value: '工单需求已变更' } })
  await instance.confirmLifecycleAction()
  assert.equal(storage.has(lifecycleRecovery.STORAGE_KEY), true)

  await instance.load()
  assert.equal(statusReads, 0)
  assert.equal(detailReads, 1)
  assert.equal(storage.has(lifecycleRecovery.STORAGE_KEY), true)
  assert.equal(instance.data.accessAllowed, false)
  assert.match(instance.data.lifecycleRecoveryMessage, /身份或授权版本已变化/)
})

test('confirmed command and fresh detail anchor mismatch retains the recovery sentinel', async (context) => {
  const xRequestId = `wxreq-${'4'.repeat(36)}`
  const { storage, storageRemovals } = globals({
    [lifecycleRecovery.STORAGE_KEY]: {
      v: 1,
      kind: 'material_request_lifecycle',
      x_request_id: xRequestId,
      created_at: '2026-09-01T00:49:59.000Z'
    }
  })
  const status = confirmedLifecycleStatus('withdraw')
  status.command.revision_no = 2
  const loaded = loadWith(fakeTransport({
    async loadAccess() {
      return Object.assign({}, access(), { can_withdraw: true })
    },
    async lifecycleCommandStatus() { return status },
    async detail() { return withdrawnDetail() }
  }))
  context.after(() => {
    loaded.restore()
    delete global.wx
  })

  const instance = pageInstance(loaded.definition)
  await instance.load()
  assert.equal(storage.has(lifecycleRecovery.STORAGE_KEY), true)
  assert.deepEqual(storageRemovals, [])
  assert.equal(instance.data.accessAllowed, false)
  assert.match(instance.data.lifecycleRecoveryMessage, /版本、修订、审批锚点或十状态轴不一致/)
})

test('a malformed persisted lifecycle sentinel is retained and blocks every recovery request', async (context) => {
  const malformed = {
    v: 1,
    kind: 'material_request_lifecycle',
    x_request_id: `wxreq-${'5'.repeat(36)}`,
    created_at: '2026-09-01T00:59:59.000Z',
    reason: 'must-not-be-persisted'
  }
  const { storage } = globals({ [lifecycleRecovery.STORAGE_KEY]: malformed })
  let identityReads = 0
  let statusReads = 0
  let listReads = 0
  const loaded = loadWith(fakeTransport({
    async loadIdentity() { identityReads += 1; return freshIdentity() },
    async lifecycleCommandStatus() {
      statusReads += 1
      return confirmedLifecycleStatus('withdraw')
    },
    async list() { listReads += 1; return page() }
  }))
  context.after(() => {
    loaded.restore()
    delete global.wx
  })

  const instance = pageInstance(loaded.definition)
  instance.onLoad()
  await instance.load()
  assert.deepEqual(storage.get(lifecycleRecovery.STORAGE_KEY), malformed)
  assert.equal(identityReads, 0)
  assert.equal(statusReads, 0)
  assert.equal(listReads, 0)
  assert.equal(instance.data.accessAllowed, false)
  assert.match(instance.data.lifecycleRecoveryMessage, /含未知字段/)
})

test('a storage reread exception after sentinel removal remains a fail-closed pending write', async (context) => {
  const fixture = globals()
  const actionable = submittedDetail()
  const terminal = withdrawnDetail()
  const originalGet = global.wx.getStorageSync.bind(global.wx)
  const originalRemove = global.wx.removeStorageSync.bind(global.wx)
  let removed = false
  global.wx.getStorageSync = (key) => {
    if (removed && key === lifecycleRecovery.STORAGE_KEY) {
      throw new Error('post-remove storage reread unavailable')
    }
    return originalGet(key)
  }
  global.wx.removeStorageSync = (key) => {
    originalRemove(key)
    if (key === lifecycleRecovery.STORAGE_KEY) removed = true
  }
  let detailReads = 0
  const loaded = loadWith(fakeTransport({
    async loadAccess() {
      return Object.assign({}, access(), { can_withdraw: true })
    },
    async detail() {
      detailReads += 1
      return detailReads === 1 ? actionable : terminal
    },
    async mutate() {
      return {
        schema_version: '1.0', request_id: REQUEST_ID, action: 'withdraw',
        request_version: 2, revision_id: REVISION_ID, revision_no: 1,
        approval_instance_id: INSTANCE_ID, approval_attempt_no: 1,
        current_step_id: null, states: axes('withdrawn'), idempotency_replayed: false
      }
    }
  }))
  context.after(() => {
    loaded.restore()
    delete global.wx
  })

  const instance = pageInstance(loaded.definition)
  await instance.load()
  await instance.openRequest({ currentTarget: { dataset: { id: REQUEST_ID } } })
  instance.openLifecycleConfirm({ currentTarget: { dataset: { action: 'withdraw' } } })
  instance.lifecycleReasonInput({ detail: { value: '工单需求已变更' } })
  await instance.confirmLifecycleAction()

  assert.equal(fixture.storage.has(lifecycleRecovery.STORAGE_KEY), false)
  assert.equal(fixture.toasts.some((item) => item.icon === 'success'), false)
  assert.match(instance.data.lifecycleRecoveryMessage, /无法安全清理/)
  assert.match(instance.data.lifecycleConfirm.pendingMessage, /禁止生成新坐标/)
  assert.equal(instance.data.detail.states.request_status, 'approval_in_progress')
})

test('a definitive POST rejection clears the matching sentinel, while storage failure prevents POST', async (context) => {
  const first = globals()
  const actionable = submittedDetail()
  let rejectedMutations = 0
  const rejectedTransport = fakeTransport({
    async loadAccess() {
      return Object.assign({}, access(), { can_withdraw: true })
    },
    async detail() { return actionable },
    async mutate() {
      rejectedMutations += 1
      const error = new Error('版本已变化')
      error.status = 409
      error.responseReceived = true
      throw error
    }
  })
  const rejectedLoaded = loadWith(rejectedTransport)
  const rejectedPage = pageInstance(rejectedLoaded.definition)
  await rejectedPage.load()
  await rejectedPage.openRequest({ currentTarget: { dataset: { id: REQUEST_ID } } })
  rejectedPage.openLifecycleConfirm({ currentTarget: { dataset: { action: 'withdraw' } } })
  rejectedPage.lifecycleReasonInput({ detail: { value: '工单需求已变更' } })
  await rejectedPage.confirmLifecycleAction()
  assert.equal(rejectedMutations, 1)
  assert.equal(first.storage.has(lifecycleRecovery.STORAGE_KEY), false)
  assert.equal(rejectedPage.data.lifecycleConfirm.pendingMessage, '')
  rejectedLoaded.restore()

  const second = globals()
  global.wx.setStorageSync = () => { throw new Error('storage unavailable') }
  let blockedMutations = 0
  const blockedLoaded = loadWith(fakeTransport({
    async loadAccess() {
      return Object.assign({}, access(), { can_withdraw: true })
    },
    async detail() { return actionable },
    async mutate() { blockedMutations += 1 }
  }))
  const blockedPage = pageInstance(blockedLoaded.definition)
  await blockedPage.load()
  await blockedPage.openRequest({ currentTarget: { dataset: { id: REQUEST_ID } } })
  blockedPage.openLifecycleConfirm({ currentTarget: { dataset: { action: 'withdraw' } } })
  blockedPage.lifecycleReasonInput({ detail: { value: '工单需求已变更' } })
  await blockedPage.confirmLifecycleAction()
  assert.equal(blockedMutations, 0)
  assert.equal(second.storage.has(lifecycleRecovery.STORAGE_KEY), false)
  assert.match(blockedPage.data.lifecycleConfirm.pendingMessage, /禁止生成新坐标/)
  assert.match(blockedPage.data.lifecycleRecoveryMessage, /无法确认需求终止恢复哨兵已持久化/)
  blockedLoaded.restore()
  context.after(() => { delete global.wx })
})

test('withdraw keeps the intent pending when request content or active approval steps drift on reread', async (context) => {
  const scenarios = [
    {
      mutateTerminal: (terminal) => { terminal.purpose = '被错误改写的需求用途' },
      expectedError: /仍待人工核验/
    },
    {
      mutateTerminal: (terminal) => {
        terminal.approval_instance.steps[0].status = 'open'
        terminal.approval_instance.steps[0].version = 0
      },
      expectedError: /不能保留活动审批步骤/
    }
  ]
  for (const { mutateTerminal, expectedError } of scenarios) {
    globals()
    const actionable = submittedDetail()
    const terminal = withdrawnDetail()
    mutateTerminal(terminal)
    let detailReads = 0
    const loaded = loadWith(fakeTransport({
      async loadAccess() {
        return Object.assign({}, access(), { can_withdraw: true })
      },
      async detail() {
        detailReads += 1
        return detailReads === 1 ? actionable : terminal
      },
      async mutate() {
        return {
          schema_version: '1.0', request_id: REQUEST_ID, action: 'withdraw',
          request_version: 2, revision_id: REVISION_ID, revision_no: 1,
          approval_instance_id: INSTANCE_ID, approval_attempt_no: 1,
          current_step_id: null, states: axes('withdrawn'), idempotency_replayed: false
        }
      }
    }))
    const instance = pageInstance(loaded.definition)
    await instance.load()
    await instance.openRequest({ currentTarget: { dataset: { id: REQUEST_ID } } })
    instance.openLifecycleConfirm({ currentTarget: { dataset: { action: 'withdraw' } } })
    instance.lifecycleReasonInput({ detail: { value: '工单需求已变更' } })
    await instance.confirmLifecycleAction()
    assert.match(instance.data.lifecycleConfirm.error, expectedError)
    assert.match(instance.data.lifecycleConfirm.pendingMessage, /禁止生成新坐标/)
    loaded.restore()
  }
  context.after(() => { delete global.wx })
})

test('safe cancel submits the exact positive approved-line set and verifies line projection', async (context) => {
  globals()
  const actionable = cancellableReturnedDetail()
  const terminal = cancelledDetail()
  const intents = []
  let detailReads = 0
  const loaded = loadWith(fakeTransport({
    async loadAccess() {
      return Object.assign({}, access(), { can_cancel: true })
    },
    async list() { return page(actionable) },
    async detail() {
      detailReads += 1
      return detailReads === 1 ? actionable : terminal
    },
    async mutate(intent) {
      intents.push(intent)
      return {
        schema_version: '1.0', request_id: REQUEST_ID, action: 'cancel',
        request_version: 3, revision_id: REVISION_ID, revision_no: 1,
        approval_instance_id: INSTANCE_ID, approval_attempt_no: 1,
        current_step_id: null, states: axes('cancelled'), idempotency_replayed: false
      }
    }
  }))
  context.after(() => {
    loaded.restore()
    delete global.wx
  })
  const instance = pageInstance(loaded.definition)
  await instance.load()
  await instance.openRequest({ currentTarget: { dataset: { id: REQUEST_ID } } })
  assert.equal(instance.data.detail.canCancel, true)
  instance.openLifecycleConfirm({ currentTarget: { dataset: { action: 'cancel' } } })
  assert.equal(instance.data.lifecycleConfirm.lines.length, 1)
  assert.equal(instance.data.lifecycleConfirm.lines[0].cancelledQty, '4.000')
  instance.lifecycleReasonInput({ detail: { value: '现场需求已取消' } })
  instance.lifecycleLineReasonInput({
    currentTarget: { dataset: { id: LINE_ID } },
    detail: { value: '取消该行全部批准数量' }
  })
  await instance.confirmLifecycleAction()

  assert.equal(intents.length, 1)
  assert.equal(intents[0].path, `/v1/material-requests/${REQUEST_ID}/cancel`)
  assert.deepEqual(intents[0].body, {
    expected_version: 2,
    reason: '现场需求已取消',
    lines: [{
      request_line_id: LINE_ID,
      cancelled_qty: '4.000',
      reason: '取消该行全部批准数量'
    }]
  })
  assert.equal(instance.data.detail.lines[0].cancelled_qty, '4.000')
  assert.equal(instance.data.detail.lines[0].status, 'cancelled')
  assert.equal(instance.data.detail.states.reservation_status, 'not_reserved')
})

test('safe cancel keeps the intent pending when reread rewrites the terminal approval status', async (context) => {
  globals()
  const actionable = cancellableReturnedDetail()
  const invalidTerminal = cancelledDetail()
  invalidTerminal.approval_instance.status = 'cancelled'
  let detailReads = 0
  const loaded = loadWith(fakeTransport({
    async loadAccess() {
      return Object.assign({}, access(), { can_cancel: true })
    },
    async list() { return page(actionable) },
    async detail() {
      detailReads += 1
      return detailReads === 1 ? actionable : invalidTerminal
    },
    async mutate() {
      return {
        schema_version: '1.0', request_id: REQUEST_ID, action: 'cancel',
        request_version: 3, revision_id: REVISION_ID, revision_no: 1,
        approval_instance_id: INSTANCE_ID, approval_attempt_no: 1,
        current_step_id: null, states: axes('cancelled'), idempotency_replayed: false
      }
    }
  }))
  context.after(() => {
    loaded.restore()
    delete global.wx
  })
  const instance = pageInstance(loaded.definition)
  await instance.load()
  await instance.openRequest({ currentTarget: { dataset: { id: REQUEST_ID } } })
  instance.openLifecycleConfirm({ currentTarget: { dataset: { action: 'cancel' } } })
  instance.lifecycleReasonInput({ detail: { value: '现场需求已取消' } })
  instance.lifecycleLineReasonInput({
    currentTarget: { dataset: { id: LINE_ID } },
    detail: { value: '取消该行全部批准数量' }
  })
  await instance.confirmLifecycleAction()

  assert.match(instance.data.lifecycleConfirm.error, /必须保留原有退回或已完成审批实例/)
  assert.match(instance.data.lifecycleConfirm.pendingMessage, /禁止生成新坐标/)
})

test('safe cancel keeps the intent pending when reread rewrites approval step history', async (context) => {
  globals()
  const actionable = cancellableReturnedDetail()
  const invalidTerminal = cancelledDetail()
  invalidTerminal.approval_instance.steps[0].assignee_snapshot.name_masked = '另＊＊＊＊'
  let detailReads = 0
  const loaded = loadWith(fakeTransport({
    async loadAccess() {
      return Object.assign({}, access(), { can_cancel: true })
    },
    async list() { return page(actionable) },
    async detail() {
      detailReads += 1
      return detailReads === 1 ? actionable : invalidTerminal
    },
    async mutate() {
      return {
        schema_version: '1.0', request_id: REQUEST_ID, action: 'cancel',
        request_version: 3, revision_id: REVISION_ID, revision_no: 1,
        approval_instance_id: INSTANCE_ID, approval_attempt_no: 1,
        current_step_id: null, states: axes('cancelled'), idempotency_replayed: false
      }
    }
  }))
  context.after(() => {
    loaded.restore()
    delete global.wx
  })
  const instance = pageInstance(loaded.definition)
  await instance.load()
  await instance.openRequest({ currentTarget: { dataset: { id: REQUEST_ID } } })
  instance.openLifecycleConfirm({ currentTarget: { dataset: { action: 'cancel' } } })
  instance.lifecycleReasonInput({ detail: { value: '现场需求已取消' } })
  instance.lifecycleLineReasonInput({
    currentTarget: { dataset: { id: LINE_ID } },
    detail: { value: '取消该行全部批准数量' }
  })
  await instance.confirmLifecycleAction()

  assert.match(instance.data.lifecycleConfirm.error, /仍待人工核验/)
  assert.match(instance.data.lifecycleConfirm.pendingMessage, /禁止生成新坐标/)
})

test('lifecycle buttons fail closed unless both access permission and allowed action are present', async (context) => {
  globals()
  const actionable = submittedDetail()
  const loaded = loadWith(fakeTransport({ async detail() { return actionable } }))
  context.after(() => {
    loaded.restore()
    delete global.wx
  })
  const instance = pageInstance(loaded.definition)
  await instance.load()
  await instance.openRequest({ currentTarget: { dataset: { id: REQUEST_ID } } })
  assert.equal(actionable.allowed_actions.includes('withdraw'), true)
  assert.equal(instance.data.detail.canWithdraw, false)
  instance.openLifecycleConfirm({ currentTarget: { dataset: { action: 'withdraw' } } })
  assert.equal(instance.data.lifecycleConfirm, null)
})

test('region processing sends exact approve, return and reject intents', async (context) => {
  globals()
  const actionable = internalApprovalDetail()
  const intents = []
  const loaded = loadWith(fakeTransport({
    async loadAccess() {
      return Object.assign({}, access(), { can_approve_region: true })
    },
    async list() { return page(actionable) },
    async detail() { return actionable },
    async mutate(intent) {
      intents.push(intent)
      const error = new Error('明确拒绝测试')
      error.status = 422
      error.responseReceived = true
      throw error
    }
  }))
  context.after(() => {
    loaded.restore()
    delete global.wx
  })
  const instance = pageInstance(loaded.definition)
  await instance.load()
  await instance.openRequest({ currentTarget: { dataset: { id: REQUEST_ID } } })
  instance.openApprovalProcess({ currentTarget: { dataset: { kind: 'internal' } } })

  await instance.submitApprovalProcess()
  assert.equal(intents[0].action, 'approve')
  assert.equal(intents[0].path, `/v1/material-requests/${REQUEST_ID}/approval-steps/${STEP_1_ID}/decision`)
  assert.deepEqual(intents[0].body, {
    expected_request_version: 1,
    expected_step_version: 0,
    action: 'approve',
    lines: [{ request_line_id: LINE_ID, approved_qty: '12.345', reason: '' }],
    return_lines: [],
    comment: ''
  })

  instance.chooseProcessAction({ currentTarget: { dataset: { action: 'return' } } })
  instance.processLineInput({
    currentTarget: { dataset: { id: LINE_ID, field: 'reason' } },
    detail: { value: '补充核验依据' }
  })
  instance.processFieldInput({
    currentTarget: { dataset: { field: 'comment' } },
    detail: { value: '退回申请人补充' }
  })
  await instance.submitApprovalProcess()
  assert.equal(intents[1].action, 'return')
  assert.deepEqual(intents[1].body, {
    expected_request_version: 1,
    expected_step_version: 0,
    action: 'return',
    lines: [],
    return_lines: [{
      request_line_id: LINE_ID,
      requested_reapproval_qty: '12.345',
      reason: '补充核验依据'
    }],
    comment: '退回申请人补充'
  })

  instance.chooseProcessAction({ currentTarget: { dataset: { action: 'reject' } } })
  instance.processFieldInput({
    currentTarget: { dataset: { field: 'comment' } },
    detail: { value: '不符合申请范围' }
  })
  await instance.submitApprovalProcess()
  assert.equal(intents[2].action, 'reject')
  assert.deepEqual(intents[2].body, {
    expected_request_version: 1,
    expected_step_version: 0,
    action: 'reject',
    lines: [],
    return_lines: [],
    comment: '不符合申请范围'
  })
})

test('uncertain approval retry reuses the same frozen intent coordinates', async (context) => {
  globals()
  const actionable = internalApprovalDetail()
  const intents = []
  const loaded = loadWith(fakeTransport({
    async loadAccess() {
      return Object.assign({}, access(), { can_approve_region: true })
    },
    async list() { return page(actionable) },
    async detail() { return actionable },
    async mutate(intent) {
      intents.push(intent)
      throw new Error('network uncertain')
    }
  }))
  context.after(() => {
    loaded.restore()
    delete global.wx
  })
  const instance = pageInstance(loaded.definition)
  await instance.load()
  await instance.openRequest({ currentTarget: { dataset: { id: REQUEST_ID } } })
  instance.openApprovalProcess({ currentTarget: { dataset: { kind: 'internal' } } })
  await instance.submitApprovalProcess()
  assert.match(instance.data.processing.pendingMessage, /禁止生成新坐标/)
  await instance.submitApprovalProcess()
  assert.equal(intents.length, 2)
  assert.equal(intents[0], intents[1])
})

test('headquarters processing uses its independent grant and predecessor line facts', async (context) => {
  globals()
  const actionable = headquartersApprovalDetail()
  const intents = []
  const loaded = loadWith(fakeTransport({
    async loadAccess() {
      return Object.assign({}, access(), { can_approve_headquarters: true })
    },
    async list() { return page(actionable) },
    async detail() { return actionable },
    async mutate(intent) {
      intents.push(intent)
      const error = new Error('明确拒绝测试')
      error.status = 422
      error.responseReceived = true
      throw error
    }
  }))
  context.after(() => {
    loaded.restore()
    delete global.wx
  })
  const instance = pageInstance(loaded.definition)
  await instance.load()
  await instance.openRequest({ currentTarget: { dataset: { id: REQUEST_ID } } })
  instance.openApprovalProcess({ currentTarget: { dataset: { kind: 'internal' } } })
  assert.equal(instance.data.processing.lines[0].inputQty, '12.345')
  await instance.submitApprovalProcess()
  assert.equal(
    intents[0].path,
    `/v1/material-requests/${REQUEST_ID}/approval-steps/${STEP_2_ID}/decision`
  )
  assert.equal(intents[0].body.expected_request_version, 2)
  assert.equal(intents[0].body.expected_step_version, 1)
})

test('external registration binds only an independently uploaded external_approval_evidence file', async (context) => {
  globals()
  const actionable = externalRegistrationDetail()
  const intents = []
  const uploads = uploadFixture({ external_approval_evidence: EVIDENCE_ID })
  const loaded = loadWith(fakeTransport({
    async loadAccess() {
      return Object.assign({}, access(), { can_register_external: true })
    },
    async list() { return page(actionable) },
    async detail() { return actionable },
    async mutate(intent) {
      intents.push(intent)
      const error = new Error('明确拒绝测试')
      error.status = 422
      error.responseReceived = true
      throw error
    }
  }), uploads.module)
  context.after(() => {
    loaded.restore()
    delete global.wx
  })
  const instance = pageInstance(loaded.definition)
  await instance.load()
  await instance.openRequest({ currentTarget: { dataset: { id: REQUEST_ID } } })
  instance.openApprovalProcess({ currentTarget: { dataset: { kind: 'external_registration' } } })
  await instance.chooseExternalEvidence()
  for (const [field, value] of [
    ['externalApproverName', '星星总部王审批员'],
    ['externalReferenceNo', 'STAR-APPROVAL-001'],
    ['externalDecidedAt', '2026-09-01T08:35:00+08:00']
  ]) {
    instance.processFieldInput({
      currentTarget: { dataset: { field } },
      detail: { value }
    })
  }
  await instance.submitApprovalProcess()
  assert.equal(intents[0].action, 'register_external_approval')
  assert.equal(intents[0].path, `/v1/material-requests/${REQUEST_ID}/approval-steps/${STEP_3_ID}/external-evidence`)
  assert.deepEqual(intents[0].body, {
    expected_request_version: 3,
    expected_step_version: 0,
    evidence_file_id: EVIDENCE_ID,
    external_approver_name: '星星总部王审批员',
    external_reference_no: 'STAR-APPROVAL-001',
    external_decided_at: '2026-09-01T08:35:00+08:00',
    action: 'approve',
    lines: [{ request_line_id: LINE_ID, approved_qty: '12.345', reason: '' }],
    return_lines: [],
    comment: ''
  })
  assert.equal(uploads.state.prepareCalls[0][0], 'external_approval_evidence')
  assert.equal(uploads.state.executeCalls.length, 1)
  const wxml = fs.readFileSync(
    path.join(__dirname, '../pages/formal-material-requests/index.wxml'),
    'utf8'
  )
  assert.doesNotMatch(wxml, /附件上传尚未实现|必须填写已有正式文件|data-field="evidenceFileId"/)
  assert.equal(/(?:bindtap|url|src)=["'][^"']*\/media/.test(wxml), false)
})

test('external verification uses one pending evidence anchor and exact step version', async (context) => {
  globals()
  const actionable = externalVerificationDetail()
  const intents = []
  const loaded = loadWith(fakeTransport({
    async loadAccess() {
      return Object.assign({}, access(), { can_verify_external: true })
    },
    async list() { return page(actionable) },
    async detail() { return actionable },
    async mutate(intent) {
      intents.push(intent)
      const error = new Error('明确拒绝测试')
      error.status = 422
      error.responseReceived = true
      throw error
    }
  }))
  context.after(() => {
    loaded.restore()
    delete global.wx
  })
  const instance = pageInstance(loaded.definition)
  await instance.load()
  await instance.openRequest({ currentTarget: { dataset: { id: REQUEST_ID } } })
  instance.openApprovalProcess({ currentTarget: { dataset: { kind: 'external_verification' } } })
  assert.equal(instance.data.processing.registrationId, REGISTRATION_ID)
  instance.chooseVerificationDecision({ currentTarget: { dataset: { decision: 'reject' } } })
  instance.processFieldInput({
    currentTarget: { dataset: { field: 'comment' } },
    detail: { value: '证据不可核验' }
  })
  await instance.submitApprovalProcess()
  assert.equal(intents[0].action, 'verify_external_approval')
  assert.equal(
    intents[0].path,
    `/v1/material-requests/${REQUEST_ID}/approval-steps/${STEP_3_ID}/external-evidence/${REGISTRATION_ID}/verification`
  )
  assert.deepEqual(intents[0].body, {
    expected_request_version: 4,
    expected_step_version: 1,
    decision: 'reject',
    comment: '证据不可核验'
  })
})

test('returned state allows immutable amend and resubmit actions', async (context) => {
  globals()
  const returned = returnedDetail()
  const intents = []
  const loaded = loadWith(fakeTransport({
    async list() { return page(returned) },
    async detail() { return returned },
    async loadDraftForEdit() {
      return {
        schema_version: '1.0',
        request_id: REQUEST_ID,
        request_version: 2,
        draft: draft()
      }
    },
    async mutate(intent) {
      intents.push(intent)
      const error = new Error('服务端明确拒绝测试')
      error.status = 422
      error.responseReceived = true
      throw error
    }
  }))
  context.after(() => {
    loaded.restore()
    delete global.wx
  })
  const instance = pageInstance(loaded.definition)
  await instance.load()
  await instance.openRequest({ currentTarget: { dataset: { id: REQUEST_ID } } })
  assert.equal(instance.data.detail.canEdit, true)
  assert.equal(instance.data.detail.canSubmit, true)
  assert.match(instance.data.detail.approvalNotice, /按退回意见/)

  instance.openSubmitConfirm()
  assert.equal(instance.data.submitConfirm, true)
  await instance.confirmSubmit()
  assert.equal(intents.length, 1)
  assert.equal(intents[0].action, 'submit')
  assert.deepEqual(intents[0].body, { expected_version: 2 })

  await instance.startEdit()
  assert.equal(instance.data.formMode, 'edit')
  assert.equal(instance.data.formRequestVersion, 2)
})
