const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')

const adapterContract = require('../utils/material-request-adapter')
const formalFileUpload = require('../utils/formal-file-upload')

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
      detail_masked: '江东中路***号'
    },
    contact_masked: { name_masked: '李*', mobile_masked: '138****0000' },
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
    allowed_actions: ['update', 'submit', 'cancel'],
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
        assignee_snapshot: { name_masked: '省＊负责人', role_code: 'provincial_manager' },
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
    can_verify_external: false
  }
}

function fakeTransport(overrides = {}) {
  return Object.assign({
    async loadAccess() { return access() },
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

function globals() {
  const storageWrites = []
  const toasts = []
  global.wx = {
    showToast(value) { toasts.push(value) },
    stopPullDownRefresh() {},
    setStorageSync(key, value) { storageWrites.push([key, value]) }
  }
  return { storageWrites, toasts }
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
  assert.equal(instance.data.requests[0].maskedContact, '李* · 138****0000')
  assert.equal(JSON.stringify(instance.data).includes(RAW_MOBILE), false)
  await instance.openRequest({ currentTarget: { dataset: { id: REQUEST_ID } } })
  assert.equal(instance.data.detail.stateAxes.length, 10)
  assert.equal(instance.data.detail.maskedAddress, '江苏省南京市建邺区江东中路***号')
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
