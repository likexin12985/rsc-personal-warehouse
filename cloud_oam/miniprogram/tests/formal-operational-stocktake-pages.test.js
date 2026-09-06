const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')

const contract = require('../utils/formal-stocktake-contract')
const formalFileUpload = require('../utils/formal-file-upload')

function loadPage(relativePath, stubs) {
  const preparedStubs = Object.assign({}, stubs)
  const saved = []
  for (const [modulePath, exports] of Object.entries(preparedStubs)) {
    const resolved = require.resolve(modulePath)
    saved.push([resolved, require.cache[resolved]])
    require.cache[resolved] = { id: resolved, filename: resolved, loaded: true, exports }
  }
  const resolvedPage = require.resolve(relativePath)
  delete require.cache[resolvedPage]
  let definition
  global.Page = (value) => { definition = value }
  require(resolvedPage)
  return { definition, restore() { delete require.cache[resolvedPage]; saved.forEach(([resolved, entry]) => { if (entry) require.cache[resolved] = entry; else delete require.cache[resolved] }); delete global.Page } }
}

// Compatibility doubles must opt in at the call site.  The production page
// intentionally fails closed for an unbranded adapter; keeping this helper
// separate makes the test-only escape hatch explicit and reviewable.
function loadDetailPage(relativePath, stubs) {
  const preparedStubs = Object.assign({}, stubs)
  const adapterModule = stubs['../utils/formal-stocktake-adapter']
  const adapter = adapterModule && adapterModule.formalStocktakeAdapter
  if (adapter && !adapter.countRecoveryMode) {
    preparedStubs['../utils/formal-stocktake-adapter'] = Object.assign({}, adapterModule, {
      formalStocktakeAdapter: Object.assign({}, adapter, { countRecoveryMode: 'legacy-test' })
    })
  }
  return loadPage(relativePath, preparedStubs)
}

function instance(definition) {
  return Object.assign({}, definition, { data: JSON.parse(JSON.stringify(definition.data)), setData(update) {
    for (const [key, value] of Object.entries(update)) {
      const parts = key.split('.')
      let target = this.data
      while (parts.length > 1) {
        const part = parts.shift()
        if (!target[part] || typeof target[part] !== 'object') target[part] = {}
        target = target[part]
      }
      target[parts[0]] = value
    }
  } })
}

const TASK = '10000000-0000-4000-8000-000000000001'
const REGION = '20000000-0000-4000-8000-000000000001'
const SCOPE = '30000000-0000-4000-8000-000000000001'
const SCOPE_2 = '30000000-0000-4000-8000-000000000002'
const LOCATION = '40000000-0000-4000-8000-000000000001'
const LOCATION_2 = '40000000-0000-4000-8000-000000000002'
const ROUND = '50000000-0000-4000-8000-000000000001'
const ASSIGNEE_USER = 'engineer-001'
const EVIDENCE_1 = '90000000-0000-4000-8000-000000000001'
const EVIDENCE_2 = '90000000-0000-4000-8000-000000000002'
const FILE_SHA = 'cd'.repeat(32)
const RECONCILIATION_COMPLETION = '70000000-0000-4000-8000-000000000001'
const RECONCILED_AT = '2026-09-01T09:10:00+08:00'

function axes(overrides = {}) { return Object.assign({ count_status: 'not_started', difference_status: 'not_ready', region_review_status: 'not_ready', headquarters_review_status: 'not_ready', recount_status: 'not_required', posting_status: 'not_posted', reconciliation_status: 'not_reconciled', closure_status: 'open' }, overrides) }
function detail() { return { schema_version: '1.0', task_id: TASK, task_no: 'ST-SELF-001', task_type: 'personal', region_org_id: REGION, status: 'draft', version: 0, blind_count: true, current_round_no: 0, cutoff_ledger_cursor: null, cutoff_at: null, issued_at: null, frozen_at: null, submitted_at: null, posted_at: null, closed_at: null, cancelled_at: null, deadline: null, note: '', state_axes: axes(), close_control: { latest_reconciliation: null, close_completion: null }, scopes: [{ scope_id: SCOPE, scope_no: 1, scope_mode: 'location_all', owner_org_id: REGION, location_id: LOCATION, custodian_person_id_snapshot: null, material_id: null, condition_code: null, availability_bucket: null, assigned_to_me: true, freeze: null, snapshot_visibility: 'not_started', snapshot_accounts: [], allowed_actions: [] }], rounds: [], allowed_actions: ['start'] } }
function summary() { return { task_id: TASK, task_no: 'ST-SELF-001', task_type: 'personal', region_org_id: REGION, status: 'draft', version: 0, blind_count: true, current_round_no: 0, current_round_status: null, cutoff_ledger_cursor: null, cutoff_at: null, visible_scope_count: 1, current_round_visible_completed_scope_count: 0, freeze_status: 'not_started', state_axes: axes(), deadline: null, allowed_actions: ['start'] } }

function terminalDetail(action) {
  const latest = action === 'close' ? { completion_id: RECONCILIATION_COMPLETION, reconciliation_no: 1, reconciliation_ledger_cursor: 81, reconciled_task_version: 9, reconciled_at: RECONCILED_AT } : null
  return Object.assign(detail(), {
    status: 'posted', version: action === 'close' ? 9 : 8, posted_at: '2026-09-01T09:00:00+08:00',
    state_axes: axes({ posting_status: 'recorded', reconciliation_status: latest ? 'recorded' : 'not_reconciled' }),
    close_control: { latest_reconciliation: latest, close_completion: null }, allowed_actions: [action]
  })
}

function countingDetail(scopeCount = 1) {
  const source = detail()
  const first = Object.assign({}, source.scopes[0], {
    snapshot_visibility: 'hidden',
    allowed_actions: ['submit_initial_count']
  })
  const scopes = scopeCount === 1 ? [first] : [first, Object.assign({}, first, {
    scope_id: SCOPE_2,
    scope_no: 2,
    location_id: LOCATION_2
  })]
  return Object.assign({}, source, {
    status: 'counting',
    version: 1,
    current_round_no: 1,
    cutoff_ledger_cursor: 10,
    cutoff_at: '2026-09-01T08:00:00+08:00',
    issued_at: '2026-09-01T07:00:00+08:00',
    frozen_at: '2026-09-01T08:00:00+08:00',
    state_axes: Object.assign({}, axes(), { count_status: 'counting' }),
    scopes,
    rounds: [{
      round_id: ROUND,
      round_no: 1,
      round_type: 'initial',
      status: 'counting',
      started_at: '2026-09-01T08:00:00+08:00',
      submitted_at: null,
      submission: null,
      visible_scope_completions: [],
      visible_count_lines: [],
      visible_observations: [],
      differences_visible: false,
      difference_completion: null,
      visible_differences: [],
      region_review: null,
      headquarters_review: null,
      recount_cause: null,
      posting: { status: 'not_posted', posting_ids: [], posting_fact_count: 0, visible_total_quantity: '0.000', covers_all_task_scopes: false, inventory_transaction_count: 0, first_posted_at: null, last_posted_at: null },
      allowed_actions: []
    }],
    allowed_actions: []
  })
}

function uploadFixture(fileIds = [EVIDENCE_1]) {
  let index = 0
  const state = { prepareCalls: [], executeCalls: [] }
  const module = Object.assign({}, formalFileUpload, {
    createFormalFileUploadController(options) {
      return formalFileUpload.createFormalFileUploadController(Object.assign({}, options, {
        chooseFiles: async () => [{ tempFilePath: '/tmp/盘点照片.jpg', name: '盘点照片.jpg', size: 3, mimeType: 'image/jpeg' }],
        prepare: async (file, purpose) => {
          const prepared = Object.freeze({
            content: new Uint8Array([4, 5, 6]),
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
          state.executeCalls.push(prepared)
          const fileId = fileIds[Math.min(index, fileIds.length - 1)]
          index += 1
          return { file_id: fileId, purpose: prepared.purpose, status: 'available', verified_at: '2026-09-01T08:01:00Z', sha256: prepared.sha256, size_bytes: prepared.size_bytes, mime_type: prepared.mime_type }
        }
      }))
    }
  })
  return { module, state }
}

async function nextTurn() {
  await new Promise((resolve) => setImmediate(resolve))
}

test('list page mounts personal create with one memory-only intent and opens the exact new detail page', async () => {
  let executed
  let navigated
  const adapter = {
    async loadAccess() { return { person_id: REGION, authorization_version: 7, can_read: true, can_count: true } },
    async list() { return { schema_version: '1.0', items: [summary()], next_after_id: null } },
    async execute(intent) { executed = intent; return { result: {}, detail: detail() } }
  }
  const registry = contract.createFormalStocktakeIntentRegistry({ coordinateFactory() { return { 'Idempotency-Key': `wxidem-${'a'.repeat(36)}`, 'X-Request-ID': `wxreq-${'b'.repeat(36)}` } } })
  const loaded = loadPage('../pages/formal-operational-stocktakes/index', {
    '../utils/session': { ensureLogin: () => true },
    '../utils/formal-stocktake-adapter': { formalStocktakeAdapter: adapter, createFormalStocktakeIntentRegistry: () => registry }
  })
  global.wx = { stopPullDownRefresh() {}, navigateTo({ url }) { navigated = url } }
  try {
    const page = instance(loaded.definition)
    page.onLoad()
    await page.load()
    assert.equal(page.data.tasks[0].statusLabel, '草稿')
    await page.createPersonal()
    assert.equal(executed.path, '/v1/stocktakes/personal')
    assert.equal(executed.body.blind_count, true)
    assert.equal(navigated, `/pages/formal-operational-stocktake-detail/index?task_id=${TASK}`)
  } finally { loaded.restore(); delete global.wx }
})

test('detail page start action forwards the exact visible task version only', () => {
  const loaded = loadDetailPage('../pages/formal-operational-stocktake-detail/index', {
    '../utils/session': { ensureLogin: () => true },
    '../utils/formal-stocktake-adapter': { formalStocktakeAdapter: {}, createFormalStocktakeIntentRegistry: () => ({ current: () => null }) }
  })
  try {
    const page = instance(loaded.definition)
    page._detail = detail()
    let input
    page.run = (value) => { input = value }
    page.startTask()
    assert.deepEqual(input, { action: 'start', taskId: TASK, expectedTaskVersion: 0, body: { expected_version: 0 } })
  } finally { loaded.restore() }
})

test('detail recount picker submits only the option assignee_user_id for selected scopes', async () => {
  const adapter = { async listAssignees() { return { items: [{ assignee_user_id: ASSIGNEE_USER, person_id: REGION, name: '工程师', employee_no: 'E001', role_codes: ['technician'] }], next_after_person_id: null } } }
  const loaded = loadDetailPage('../pages/formal-operational-stocktake-detail/index', {
    '../utils/session': { ensureLogin: () => true },
    '../utils/formal-stocktake-adapter': { formalStocktakeAdapter: adapter, createFormalStocktakeIntentRegistry: () => ({ current: () => null }) }
  })
  try {
    const page = instance(loaded.definition)
    page._detail = Object.assign(detail(), { version: 5, current_round_no: 1, rounds: [{ round_id: ROUND, round_no: 1 }] })
    page.data.detail = { scopes: page._detail.scopes.map((scope) => Object.assign({}, scope, { recountSelected: false, recountOptions: [], recountAssigneeIndex: 0, recountAssigneeUserId: '' })) }
    let input
    page.run = (value) => { input = value }
    await page.toggleRecountScope({ currentTarget: { dataset: { id: SCOPE } } })
    page.bindRecountReason({ detail: { value: '区域复核要求复盘' } })
    page.submitRecount()
    assert.deepEqual(input.body.assignments, [{ scope_id: SCOPE, assignee_user_id: ASSIGNEE_USER }])
    assert.equal(JSON.stringify(input.body).includes('person_id'), false)
  } finally { loaded.restore() }
})

test('detail binds only an available stocktake_evidence upload to the selected initial-count scope', async () => {
  const counting = countingDetail()
  const uploads = uploadFixture()
  const intents = []
  const adapter = {
    async loadAccess() { return { person_id: REGION, authorization_version: 7, can_read: true, can_count: true, can_manage: false, can_review_region: false, can_review_headquarters: false, can_post: false } },
    async detail() { return counting },
    async execute(intent) { intents.push(intent); return { result: {}, detail: counting } }
  }
  const loaded = loadDetailPage('../pages/formal-operational-stocktake-detail/index', {
    '../utils/session': { ensureLogin: () => true },
    '../utils/formal-stocktake-adapter': { formalStocktakeAdapter: adapter, createFormalStocktakeIntentRegistry: () => contract.createFormalStocktakeIntentRegistry({ coordinateFactory() { return { 'Idempotency-Key': `wxidem-${'a'.repeat(36)}`, 'X-Request-ID': `wxreq-${'b'.repeat(36)}` } } }) },
    '../utils/formal-file-upload': uploads.module
  })
  const toasts = []
  global.wx = { showToast(value) { toasts.push(value) }, stopPullDownRefresh() {} }
  try {
    const page = instance(loaded.definition)
    page.onLoad({ task_id: TASK })
    await page.load()
    page.chooseScope({ currentTarget: { dataset: { id: SCOPE } } })
    await page.chooseCountEvidence()
    assert.equal(page.data.evidenceUploadFiles[0].filename, '盘点照片.jpg')
    assert.equal(page.data.evidenceUploadFiles[0].sizeLabel, '3 B')
    assert.equal(page.data.evidenceUploadFiles[0].sha256, FILE_SHA)
    assert.equal(page.data.evidenceUploadFiles[0].status, 'available')
    page.submitCount({ currentTarget: { dataset: { zero: true } } })
    await nextTurn()
    assert.deepEqual(toasts, [])
    assert.equal(page.data.errorMessage, '')
    assert.equal(intents.length, 1)
    assert.equal(intents[0].action, 'submit_initial_count')
    assert.equal(intents[0].scopeId, SCOPE)
    assert.deepEqual(intents[0].body.evidence_file_ids, [EVIDENCE_1])
    assert.equal(uploads.state.prepareCalls[0][0], 'stocktake_evidence')
    assert.equal(toasts.length, 0)
  } finally { loaded.restore(); delete global.wx }
})

test('one stocktake file hash cannot be claimed by a second scope count intent', async () => {
  const counting = countingDetail(2)
  const uploads = uploadFixture([EVIDENCE_1, EVIDENCE_2])
  const intents = []
  const adapter = {
    async loadAccess() { return { person_id: REGION, authorization_version: 7, can_read: true, can_count: true, can_manage: false, can_review_region: false, can_review_headquarters: false, can_post: false } },
    async detail() { return counting },
    async execute(intent) { intents.push(intent); throw new Error('明确拒绝测试') }
  }
  const loaded = loadDetailPage('../pages/formal-operational-stocktake-detail/index', {
    '../utils/session': { ensureLogin: () => true },
    '../utils/formal-stocktake-adapter': { formalStocktakeAdapter: adapter, createFormalStocktakeIntentRegistry: () => contract.createFormalStocktakeIntentRegistry({ coordinateFactory() { return { 'Idempotency-Key': `wxidem-${'a'.repeat(36)}`, 'X-Request-ID': `wxreq-${'b'.repeat(36)}` } } }) },
    '../utils/formal-file-upload': uploads.module
  })
  const toasts = []
  global.wx = { showToast(value) { toasts.push(value) }, stopPullDownRefresh() {} }
  try {
    const page = instance(loaded.definition)
    page.onLoad({ task_id: TASK })
    await page.load()
    page.chooseScope({ currentTarget: { dataset: { id: SCOPE } } })
    await page.chooseCountEvidence()
    page.submitCount({ currentTarget: { dataset: { zero: true } } })
    await nextTurn()
    assert.deepEqual(toasts, [])
    assert.equal(page.data.errorMessage, '明确拒绝测试')
    assert.equal(intents.length, 1)

    page.chooseScope({ currentTarget: { dataset: { id: SCOPE_2 } } })
    await page.chooseCountEvidence()
    page.submitCount({ currentTarget: { dataset: { zero: true } } })
    assert.equal(intents.length, 1)
    assert.match(toasts.at(-1).title, /同一盘点证据只能用于一个范围/)
  } finally { loaded.restore(); delete global.wx }
})

test('detail shows eight independent axes and confirms reconcile and close as separate HQ actions', async () => {
  let source = terminalDetail('reconcile')
  const access = { person_id: REGION, authorization_version: 7, can_read: true, can_count: false, can_manage: false, can_review_region: false, can_review_headquarters: false, can_reconcile: true, can_close: true }
  const adapter = { async loadAccess() { return access }, async detail() { return source } }
  const loaded = loadDetailPage('../pages/formal-operational-stocktake-detail/index', {
    '../utils/session': { ensureLogin: () => true },
    '../utils/formal-stocktake-adapter': { formalStocktakeAdapter: adapter, createFormalStocktakeIntentRegistry: () => ({ current: () => null }) }
  })
  global.wx = { stopPullDownRefresh() {} }
  try {
    const page = instance(loaded.definition)
    page.onLoad({ task_id: TASK })
    await page.load()
    assert.deepEqual(page.data.detail.axes.map((item) => item.label), ['计数', '差异', '区域复核', '总部复核', '复盘', '过账', '内部对账', '关闭'])
    assert.equal(page.data.detail.canReconcile, true)
    assert.equal(page.data.detail.canClose, false)
    let input
    page.run = (value) => { input = value }
    page.openTerminalConfirm({ currentTarget: { dataset: { action: 'reconcile' } } })
    assert.equal(page.data.terminalConfirm, 'reconcile')
    page.confirmTerminalAction()
    assert.deepEqual(input, { action: 'reconcile', taskId: TASK, expectedTaskVersion: 8, body: { expected_task_version: 8 } })

    source = terminalDetail('close')
    page._detail = source
    page.data.detail = source
    page.openTerminalConfirm({ currentTarget: { dataset: { action: 'close' } } })
    assert.equal(page.data.terminalConfirm, 'close')
    page.confirmTerminalAction()
    assert.deepEqual(input, { action: 'close', taskId: TASK, expectedTaskVersion: 9, body: { expected_task_version: 9 } })
  } finally { loaded.restore(); delete global.wx }
})

test('terminal buttons fail closed without the independent HQ permission even if allowed_actions contains the action', () => {
  const loaded = loadDetailPage('../pages/formal-operational-stocktake-detail/index', {
    '../utils/session': { ensureLogin: () => true },
    '../utils/formal-stocktake-adapter': { formalStocktakeAdapter: {}, createFormalStocktakeIntentRegistry: () => ({ current: () => null }) }
  })
  try {
    const page = instance(loaded.definition)
    page._detail = terminalDetail('reconcile')
    page._access = { can_reconcile: false, can_close: false }
    page.openTerminalConfirm({ currentTarget: { dataset: { action: 'reconcile' } } })
    assert.equal(page.data.terminalConfirm, '')
    assert.match(page.data.errorMessage, /权限.*allowed_action/)
  } finally { loaded.restore() }
})

test('new mini pages are registered separately and contain the explicit recount/posting/formal-upload boundaries', () => {
  const app = JSON.parse(fs.readFileSync(path.resolve(__dirname, '../app.json'), 'utf8'))
  assert.equal(app.pages.includes('pages/formal-operational-stocktakes/index'), true)
  assert.equal(app.pages.includes('pages/formal-operational-stocktake-detail/index'), true)
  assert.equal(app.pages.includes('pages/formal-stocktakes/index'), true)
  const source = fs.readFileSync(path.resolve(__dirname, '../pages/formal-operational-stocktake-detail/index.wxml'), 'utf8')
  assert.match(source, /assignee_user_id/)
  assert.match(source, /按所选范围开复盘/)
  assert.doesNotMatch(source, /等待正式复盘人员目录/)
  assert.match(source, /posted 不等于 closed/)
  assert.match(source, /确认记录内部对账/)
  assert.match(source, /确认关闭盘点/)
  assert.match(source, /不产生通知、同步或外部系统动作/)
  assert.match(source, /选择并上传/)
  assert.match(source, /available 后才会加入当前范围计数/)
  assert.doesNotMatch(source, /已有正式 file_id|bindEvidence|\/media/)
  assert.equal(source.includes('wx.uploadFile'), false)
})

function deferred() {
  let resolve
  const promise = new Promise((done) => { resolve = done })
  return { promise, resolve }
}
function taskRow(number) {
  return Object.assign(summary(), { task_id: `10000000-0000-4000-8000-${String(number).padStart(12, '0')}`, task_no: `ST-${number}` })
}
function listHarness(adapter) {
  const loaded = loadPage('../pages/formal-operational-stocktakes/index', {
    '../utils/session': { ensureLogin: () => true },
    '../utils/formal-stocktake-adapter': { formalStocktakeAdapter: adapter, createFormalStocktakeIntentRegistry: () => ({ current: () => null }) }
  })
  const page = instance(loaded.definition)
  page.onLoad()
  return { page, restore: loaded.restore }
}
const listAccess = () => ({ person_id: REGION, authorization_version: 7, can_read: true, can_count: true })

test('task 51 is reachable with inclusive cursor; refresh restarts pagination and duplicate taps issue one request', async () => {
  const second = deferred()
  const calls = []
  const { page, restore } = listHarness({
    loadAccess: async () => listAccess(),
    async list(cursor) {
      calls.push(cursor)
      return cursor === null ? { items: Array.from({ length: 50 }, (_, i) => taskRow(i + 1)), next_after_id: taskRow(51).task_id } : second.promise
    }
  })
  let navigated
  global.wx = { navigateTo({ url }) { navigated = url } }
  try {
    await page.load()
    assert.equal(page.data.tasks.length, 50)
    const loading = page.loadMore()
    page.loadMore()
    await nextTurn()
    assert.deepEqual(calls, [null, taskRow(51).task_id])
    second.resolve({ items: [taskRow(51)], next_after_id: null })
    await loading
    assert.equal(page.data.tasks.length, 51)
    assert.equal(page.data.hasMore, false)
    page.openTask({ currentTarget: { dataset: { id: taskRow(51).task_id } } })
    assert.match(navigated, new RegExp(taskRow(51).task_id))
    await page.load()
    assert.equal(page.data.tasks.length, 50)
    assert.equal(page.data.hasMore, true)
  } finally { restore(); delete global.wx }
})

test('task pagination rejects duplicate, backwards, empty continuing and looping pages', async () => {
  for (const invalid of [
    { items: Array.from({ length: 51 }, (_, index) => taskRow(index + 2)), next_after_id: null },
    { items: [taskRow(1)], next_after_id: null },
    { items: [taskRow(2), taskRow(2)], next_after_id: null },
    { items: [taskRow(3), taskRow(2)], next_after_id: null },
    { items: [taskRow(2)], next_after_id: taskRow(2).task_id },
    { items: [], next_after_id: taskRow(3).task_id },
    { items: [taskRow(2)], next_after_id: taskRow(1).task_id }
  ]) {
    const { page, restore } = listHarness({ loadAccess: async () => listAccess(), async list(cursor) {
      return cursor === null ? { items: [taskRow(1)], next_after_id: taskRow(2).task_id } : invalid
    } })
    try {
      await page.load()
      await page.loadMore()
      assert.deepEqual(page.data.tasks, [])
      assert.equal(page.data.hasMore, false)
      assert.match(page.data.errorMessage, /分页/)
    } finally { restore() }
  }
})

test('refresh supersedes pending next page; hidden and unloaded pages reject late responses', async () => {
  for (const end of ['refresh', 'onHide', 'onUnload']) {
    const pending = deferred()
    let initialCalls = 0
    const { page, restore } = listHarness({ loadAccess: async () => listAccess(), async list(cursor) {
      if (cursor !== null) return pending.promise
      initialCalls += 1
      return initialCalls === 1 ? { items: [taskRow(1)], next_after_id: taskRow(2).task_id } : { items: [taskRow(9)], next_after_id: null }
    } })
    try {
      await page.load()
      const loading = page.loadMore()
      await nextTurn()
      if (end === 'refresh') await page.load()
      else page[end]()
      pending.resolve({ items: [taskRow(2)], next_after_id: null })
      await loading
      assert.deepEqual(page.data.tasks.map((row) => row.task_id), end === 'refresh' ? [taskRow(9).task_id] : [])
    } finally { restore() }
  }
})

test('authorization changes before or after next-page transport clear all previously visible tasks', async () => {
  for (const changeAfterTransport of [false, true]) {
    let access = listAccess()
    const { page, restore } = listHarness({ loadAccess: async () => access, async list(cursor) {
      if (cursor !== null && changeAfterTransport) access = Object.assign(listAccess(), { authorization_version: 8 })
      return cursor === null ? { items: [taskRow(1)], next_after_id: taskRow(2).task_id } : { items: [taskRow(2)], next_after_id: null }
    } })
    try {
      await page.load()
      if (!changeAfterTransport) access = Object.assign(listAccess(), { person_id: LOCATION })
      await page.loadMore()
      assert.deepEqual(page.data.tasks, [])
      assert.equal(page.data.accessAllowed, false)
      assert.match(page.data.errorMessage, /身份|权限|授权/)
    } finally { restore() }
  }
})

async function scanHarness() {
  let source = countingDetail(2)
  let access = listAccess()
  let scan
  const loaded = loadDetailPage('../pages/formal-operational-stocktake-detail/index', {
    '../utils/session': { ensureLogin: () => true },
    '../utils/formal-stocktake-adapter': { formalStocktakeAdapter: {
      loadAccess: async () => access,
      detail: async () => source
    }, createFormalStocktakeIntentRegistry: () => ({ current: () => null }) }
  })
  global.wx = { scanCode(value) { scan = value }, showToast() {} }
  const page = instance(loaded.definition)
  page.onLoad({ task_id: TASK })
  await page.load()
  page.chooseScope({ currentTarget: { dataset: { id: SCOPE } } })
  return { page, scanned: (value) => scan.success({ result: value }), source: (value) => { source = value }, access: (value) => { access = value }, restore() { loaded.restore(); delete global.wx } }
}

test('manual input preserves SKU/SN semantics; scanned codes remain unresolved raw identifiers with scan provenance', async () => {
  const h = await scanHarness()
  try {
    h.page.bindMaterial({ detail: { value: 'SKU-1' } })
    h.page.bindSerial({ detail: { value: 'SN-1' } })
    h.page.bindQuantity({ detail: { value: '1' } })
    h.page.addObservation()
    const manual = h.page.data.observations[0]
    assert.equal(manual.material_identifier_type, 'sku_code')
    assert.equal(manual.serial_identifier_type, 'serial_no')
    assert.equal(manual.count_method, 'manual')

    h.page.scanMaterial()
    await h.scanned('registered-material-qr-not-sku')
    h.page.scanSerial()
    await h.scanned('registered-serial-qr-not-sn')
    h.page.bindQuantity({ detail: { value: '1' } })
    h.page.addObservation()
    const scanned = h.page.data.observations[1]
    assert.equal(scanned.material_identifier_type, 'unknown')
    assert.equal(scanned.serial_identifier_type, 'unknown')
    assert.equal(scanned.material_identifier_raw, 'registered-material-qr-not-sku')
    assert.equal(scanned.serial_no_raw, 'registered-serial-qr-not-sn')
    assert.equal(scanned.count_method, 'scan')
    assert.equal(scanned.material_id, null)
    assert.equal(scanned.serial_id, null)
    // Use the real contract, not only the page's UI fields.
    const intent = contract.createFormalStocktakeIntentRegistry({ coordinateFactory: () => ({ 'Idempotency-Key': `wxidem-${'a'.repeat(36)}`, 'X-Request-ID': `wxreq-${'b'.repeat(36)}` }) }).begin({ action: 'submit_initial_count', taskId: TASK, roundId: ROUND, scopeId: SCOPE, expectedTaskVersion: 1, body: { count_mode: 'blind', account_counts: [], physical_observations: [scanned], evidence_file_ids: [], zero_confirmed: false } })
    assert.equal(intent.body.physical_observations[0].count_method, 'scan')
  } finally { h.restore() }
})

test('manual replacement resets scan provenance and late scan cannot overwrite edited input', async () => {
  const h = await scanHarness()
  try {
    h.page.scanMaterial()
    h.page.bindMaterial({ detail: { value: 'SKU-edited' } })
    await h.scanned('late-qr')
    assert.equal(h.page.data.materialIdentifier, 'SKU-edited')
    h.page.scanSerial()
    await h.scanned('qr')
    h.page.bindSerial({ detail: { value: 'SN-edited' } })
    h.page.bindQuantity({ detail: { value: '1' } })
    h.page.addObservation()
    assert.equal(h.page.data.observations[0].serial_identifier_type, 'serial_no')
    assert.equal(h.page.data.observations[0].count_method, 'manual')
  } finally { h.restore() }
})

test('late camera results cannot cross scope, page lifetime, task version or authorization', async () => {
  for (const change of ['scope', 'hide', 'unload', 'version', 'identity', 'permission']) {
    const h = await scanHarness()
    try {
      h.page.scanMaterial()
      if (change === 'scope') h.page.chooseScope({ currentTarget: { dataset: { id: SCOPE_2 } } })
      if (change === 'hide') h.page.onHide()
      if (change === 'unload') h.page.onUnload()
      if (change === 'version') h.source(Object.assign(countingDetail(2), { version: 2 }))
      if (change === 'identity') h.access(Object.assign(listAccess(), { person_id: LOCATION }))
      if (change === 'permission') h.access(Object.assign(listAccess(), { can_count: false }))
      await h.scanned('must-not-accept')
      assert.equal(h.page.data.materialIdentifier, '', change)
    } finally { h.restore() }
  }
})

test('SN observations require exactly one item and retain the draft after local rejection', async () => {
  const h = await scanHarness()
  const toasts = []
  global.wx.showToast = (message) => toasts.push(message.title)
  try {
    h.page.bindMaterial({ detail: { value: 'SKU-1' } })
    h.page.scanSerial()
    await h.scanned('registered-sn-qr')
    for (const quantity of ['2', '0.5']) {
      h.page.bindQuantity({ detail: { value: quantity } })
      h.page.addObservation()
      assert.equal(h.page.data.observations.length, 0)
      assert.equal(h.page.data.serialNo, 'registered-sn-qr')
      assert.match(toasts.at(-1), /逐件.*1/)
    }
    h.page.bindQuantity({ detail: { value: '1.000' } })
    h.page.addObservation()
    assert.equal(h.page.data.observations[0].counted_qty, '1.000')
  } finally { h.restore() }
})

test('list reads and creation are mutually exclusive without clearing a pending original intent', async () => {
  const listing = deferred()
  const creation = deferred()
  let listCalls = 0
  let writes = 0
  let pending = { original: true }
  const loaded = loadPage('../pages/formal-operational-stocktakes/index', {
    '../utils/session': { ensureLogin: () => true },
    '../utils/formal-stocktake-adapter': { formalStocktakeAdapter: {
      loadAccess: async () => listAccess(),
      async list(cursor) { listCalls += 1; return cursor ? listing.promise : { items: [taskRow(1)], next_after_id: taskRow(2).task_id } },
      async execute(intent) { assert.equal(intent, pending); writes += 1; return creation.promise }
    }, createFormalStocktakeIntentRegistry: () => ({ current: () => pending, complete() { pending = null } }) }
  })
  global.wx = { navigateTo() {} }
  try {
    const page = instance(loaded.definition)
    page.onLoad()
    await page.load()
    const more = page.loadMore()
    await page.createPersonal()
    assert.equal(writes, 0)
    assert.deepEqual(pending, { original: true })
    listing.resolve({ items: [taskRow(2)], next_after_id: null })
    await more
    page.data.pendingRetryable = true
    const writing = page.retryPending()
    await page.load()
    await page.loadMore()
    assert.equal(listCalls, 2)
    assert.equal(writes, 1)
    creation.resolve({ detail: detail() })
    await writing
    assert.equal(listCalls, 3)
    assert.equal(pending, null)
  } finally { loaded.restore(); delete global.wx }
})

test('creation response after hide or unload cannot clear original intent, reload or navigate', async () => {
  for (const lifecycle of ['onHide', 'onUnload']) {
    for (const outcome of ['success', 'failure']) {
      const pendingWrite = deferred()
      let reads = 0
      let completed = 0
      let navigation = 0
      const intent = { original: true }
      let registered = null
      const loaded = loadPage('../pages/formal-operational-stocktakes/index', {
        '../utils/session': { ensureLogin: () => true },
        '../utils/formal-stocktake-adapter': { formalStocktakeAdapter: {
          loadAccess: async () => listAccess(),
          async list() { reads += 1; return { items: [summary()], next_after_id: null } },
          async execute() { await pendingWrite.promise; if (outcome === 'failure') throw new Error('late rejection'); return { detail: detail() } }
        }, createFormalStocktakeIntentRegistry: () => ({ current: () => registered, begin() { registered = intent; return intent }, complete() { completed += 1 } }) }
      })
      global.wx = { navigateTo() { navigation += 1 } }
      try {
        const page = instance(loaded.definition)
        page.onLoad()
        await page.load()
        const writing = page.createPersonal()
        page[lifecycle]()
        let updates = 0
        page.setData = () => { updates += 1 }
        pendingWrite.resolve()
        await writing
        assert.equal(reads, 1)
        assert.equal(completed, 0)
        assert.equal(navigation, 0)
        assert.equal(updates, 0)
        assert.equal(page._intentRegistry.current(), intent)
        await page.createPersonal()
        assert.equal(page._creationLease, null)
      } finally { loaded.restore(); delete global.wx }
    }
  }
})

test('detail run and retry ignore late success or failure across hide/unload and preserve original intent', async () => {
  for (const method of ['run', 'retryPending']) {
    for (const lifecycle of ['onHide', 'onUnload']) {
      for (const outcome of ['success', 'failure']) {
        const pending = deferred()
        const intent = { original: true }
        let registered = method === 'retryPending' ? intent : null
        let completions = 0
        let reads = 0
        let navigations = 0
        const loaded = loadDetailPage('../pages/formal-operational-stocktake-detail/index', {
          '../utils/session': { ensureLogin: () => true },
          '../utils/formal-stocktake-adapter': { formalStocktakeAdapter: {
            loadAccess: async () => listAccess(),
            async detail() { reads += 1; return countingDetail() },
            async execute(value) { assert.equal(value, intent); await pending.promise; if (outcome === 'failure') throw new Error('late failure'); return { detail: countingDetail() } }
          }, createFormalStocktakeIntentRegistry: () => ({ current: () => registered, begin() { registered = intent; return intent }, complete() { completions += 1 } }) }
        })
        global.wx = { navigateTo() { navigations += 1 } }
        try {
          const page = instance(loaded.definition)
          page.onLoad({ task_id: TASK })
          await page.load()
          if (method === 'retryPending') page.data.pendingRetryable = true
          const writing = page[method]({})
          page[lifecycle]()
          let updates = 0
          page.setData = () => { updates += 1 }
          pending.resolve()
          await writing
          assert.equal(completions, 0)
          assert.equal(updates, 0)
          assert.equal(reads, 1)
          assert.equal(navigations, 0)
          assert.equal(page._intentRegistry.current(), intent)
          assert.equal(page._writeLease, null)
        } finally { loaded.restore(); delete global.wx }
      }
    }
  }
})

test('detail old finally cannot reset another write owner busy state', async () => {
  const pending = deferred()
  const intent = { original: true }
  let registered = null
  const loaded = loadDetailPage('../pages/formal-operational-stocktake-detail/index', {
    '../utils/session': { ensureLogin: () => true },
    '../utils/formal-stocktake-adapter': { formalStocktakeAdapter: {
      loadAccess: async () => listAccess(), detail: async () => countingDetail(),
      async execute() { await pending.promise; return { detail: countingDetail() } }
    }, createFormalStocktakeIntentRegistry: () => ({ current: () => registered, begin() { registered = intent; return intent }, complete() { throw new Error('must retain old intent') } }) }
  })
  try {
    const page = instance(loaded.definition)
    page.onLoad({ task_id: TASK })
    await page.load()
    const writing = page.run({})
    const newerOwner = {}
    page._writeLease = newerOwner
    page.data.busy = true
    pending.resolve()
    await writing
    assert.equal(page.data.busy, true)
    assert.equal(page._writeLease, newerOwner)
    assert.equal(page.data.errorMessage, '')
  } finally { loaded.restore() }
})

test('opening a task is blocked during list loading or creation', async () => {
  const { page, restore } = listHarness({ loadAccess: async () => listAccess(), list: async () => ({ items: [summary()], next_after_id: null }) })
  let navigations = 0
  global.wx = { navigateTo() { navigations += 1 } }
  try {
    await page.load()
    for (const flag of ['busy', '_pageBusy', '_creationLease']) {
      if (flag === 'busy') page.data.busy = true
      else page[flag] = true
      page.openTask({ currentTarget: { dataset: { id: TASK } } })
      if (flag === 'busy') page.data.busy = false
      else page[flag] = false
    }
    assert.equal(navigations, 0)
    page.openTask({ currentTarget: { dataset: { id: TASK } } })
    assert.equal(navigations, 1)
  } finally { restore(); delete global.wx }
})

test('returning while detail write is pending hides old authorization until an explicit fresh read', async () => {
  const pending = deferred()
  const intent = { original: true }
  let registered = null
  let reads = 0
  let accessReads = 0
  let writes = 0
  let completions = 0
  const loaded = loadDetailPage('../pages/formal-operational-stocktake-detail/index', {
    '../utils/session': { ensureLogin: () => true },
    '../utils/formal-stocktake-adapter': { formalStocktakeAdapter: {
      async loadAccess() { accessReads += 1; return listAccess() },
      async detail() { reads += 1; return countingDetail() },
      async execute() { writes += 1; await pending.promise; return { detail: countingDetail() } }
    }, createFormalStocktakeIntentRegistry: () => ({ current: () => registered, begin() { registered = intent; return intent }, complete() { completions += 1 } }) }
  })
  try {
    const page = instance(loaded.definition)
    page.onLoad({ task_id: TASK })
    await page.load()
    assert.equal(page.data.accessAllowed, true)
    const writing = page.run({})
    page.onHide()
    page.onShow()
    assert.equal(page.data.accessAllowed, false)
    assert.equal(page.data.detail, null)
    assert.equal(page.data.loading, false)
    assert.match(page.data.accessMessage, /等待结束后下拉刷新/)
    await page.retryPending()
    assert.equal(writes, 1)
    assert.equal(reads, 1)
    pending.resolve()
    await writing
    assert.equal(page.data.detail, null)
    assert.equal(page.data.accessAllowed, false)
    assert.equal(page.data.loading, false)
    assert.equal(completions, 0)
    assert.equal(page._intentRegistry.current(), intent)
    await page.load()
    assert.equal(accessReads, 2)
    assert.equal(reads, 2)
    assert.equal(page.data.accessAllowed, true)
    assert.equal(page.data.detail.task_id, TASK)
    assert.match(page.data.pendingMessage, /待核实/)
    assert.equal(page.data.pendingRetryable, false)
    await page.run({ action: 'new-action' })
    await page.retryPending()
    assert.equal(writes, 1)
  } finally { loaded.restore() }
})

test('list refresh after a hidden creation never replays its retained intent through new create or retry', async () => {
  const pending = deferred()
  const intent = { original: true }
  let registered = null
  let writes = 0
  const loaded = loadPage('../pages/formal-operational-stocktakes/index', {
    '../utils/session': { ensureLogin: () => true },
    '../utils/formal-stocktake-adapter': { formalStocktakeAdapter: {
      loadAccess: async () => listAccess(),
      list: async () => ({ items: [summary()], next_after_id: null }),
      async execute() { writes += 1; await pending.promise; return { detail: detail() } }
    }, createFormalStocktakeIntentRegistry: () => ({ current: () => registered, begin() { registered = intent; return intent }, complete() { registered = null } }) }
  })
  try {
    const page = instance(loaded.definition)
    page.onLoad()
    await page.load()
    const creating = page.createPersonal()
    page.onHide()
    pending.resolve()
    await creating
    page._hidden = false
    await page.load()
    assert.equal(page.data.accessAllowed, true)
    assert.match(page.data.pendingMessage, /待核实/)
    assert.equal(page.data.pendingRetryable, false)
    await page.createPersonal()
    await page.retryPending()
    assert.equal(writes, 1)
    assert.equal(registered, intent)
  } finally { loaded.restore() }
})

test('only the dedicated same-page retry action can replay an explicitly retryable intent', async () => {
  for (const isDetail of [false, true]) {
    const intent = { original: true }
    let registered = null
    let writes = 0
    const load = isDetail ? loadDetailPage : loadPage
    const loaded = load(isDetail ? '../pages/formal-operational-stocktake-detail/index' : '../pages/formal-operational-stocktakes/index', {
      '../utils/session': { ensureLogin: () => true },
      '../utils/formal-stocktake-adapter': { formalStocktakeAdapter: {
        loadAccess: async () => listAccess(),
        list: async () => ({ items: [summary()], next_after_id: null }),
        detail: async () => countingDetail(),
        async execute(value) {
          assert.equal(value, intent)
          writes += 1
          throw Object.assign(new Error('uncertain retryable'), { write_result_uncertain: true, stocktake_retry_state: 'retryable' })
        }
      }, createFormalStocktakeIntentRegistry: () => ({ current: () => registered, begin() { registered = intent; return intent }, complete() { registered = null } }) }
    })
    try {
      const page = instance(loaded.definition)
      page.onLoad({ task_id: TASK })
      await page.load()
      await (isDetail ? page.run({}) : page.createPersonal())
      assert.equal(page.data.pendingRetryable, true)
      await (isDetail ? page.run({ action: 'different-action' }) : page.createPersonal())
      assert.equal(writes, 1)
      await page.retryPending()
      assert.equal(writes, 2)
      await page.load()
      assert.equal(page.data.pendingRetryable, false)
      await page.retryPending()
      assert.equal(writes, 2)
      assert.equal(registered, intent)
    } finally { loaded.restore() }
  }
})
