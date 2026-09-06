const assert = require('node:assert/strict')
const test = require('node:test')

function loadPage(stubs) {
  const preparedStubs = Object.assign({}, stubs)
  const saved = []
  for (const [modulePath, exports] of Object.entries(preparedStubs)) {
    const resolved = require.resolve(modulePath)
    saved.push([resolved, require.cache[resolved]])
    require.cache[resolved] = { id: resolved, filename: resolved, loaded: true, exports }
  }
  const pagePath = require.resolve('../pages/formal-operational-stocktake-detail/index')
  delete require.cache[pagePath]
  let definition
  global.Page = (value) => { definition = value }
  require(pagePath)
  return {
    definition,
    restore() {
      delete require.cache[pagePath]
      saved.forEach(([resolved, entry]) => { if (entry) require.cache[resolved] = entry; else delete require.cache[resolved] })
      delete global.Page
    }
  }
}

function instance(definition) {
  return Object.assign({}, definition, {
    data: JSON.parse(JSON.stringify(definition.data)),
    setData(update) { Object.assign(this.data, update) }
  })
}

const TASK = '10000000-0000-4000-8000-000000000001'
const ROUND = '20000000-0000-4000-8000-000000000001'
const SCOPE = '30000000-0000-4000-8000-000000000001'
const PERSON = '40000000-0000-4000-8000-000000000001'
const SENTINEL = {
  v: 1, kind: 'formal_scope_count', task_id: TASK, round_id: ROUND, round_no: 2,
  scope_id: SCOPE, operation: 'recount_count', expected_task_version: 7,
  actor_person_id: PERSON, actor_authorization_version: 9,
  trace_request_id: 'wx-count-recovery-0003'
}

function access() {
  return { person_id: PERSON, authorization_version: 9, can_read: true, can_count: true }
}

function recoveredDetail() {
  return {
    task_id: TASK, task_no: 'ST-RECOVERED', task_type: 'personal', status: 'counting',
    version: 8, current_round_no: 2, allowed_actions: [], scopes: [], rounds: [],
    state_axes: {
      count_status: 'counting', difference_status: 'not_ready', region_review_status: 'not_ready',
      headquarters_review_status: 'not_ready', recount_status: 'not_required',
      posting_status: 'not_posted', reconciliation_status: 'not_reconciled', closure_status: 'open'
    }
  }
}

test('a durable count marker blocks every other business write at run boundary', async () => {
  let writes = 0
  const loaded = loadPage({
    '../utils/session': { ensureLogin: () => true },
    '../utils/formal-stocktake-adapter': {
      formalStocktakeAdapter: {
        countRecoveryMode: 'durable',
        countCommandStatus() {},
        async loadIdentityNoReplay() {},
        async loadAccessNoReplay() {},
        async detailNoReplay() {},
        async execute() { writes += 1 }
      },
      createFormalStocktakeIntentRegistry: () => ({ current: () => null })
    },
    '../utils/formal-stocktake-count-recovery-store': {
      getFormalStocktakeCountRecoveryStore: () => ({ readPending: () => ({ kind: 'valid', values: [SENTINEL] }) })
    }
  })
  try {
    const page = instance(loaded.definition)
    page._hidden = false
    page._taskId = TASK
    page._access = access()
    page._detail = { task_id: TASK, version: 8 }
    page._loadGeneration = 1
    page._writeLease = null
    page._countRecoveryStore = { readPending: () => ({ kind: 'valid', values: [SENTINEL] }) }
    page._intentRegistry = { current: () => null }
    page.data.loading = false
    await page.run({ action: 'start', taskId: TASK, expectedTaskVersion: 8, body: { expected_version: 8 } })
    assert.equal(writes, 0)
    assert.match(page.data.pendingMessage, /待只读核验/)
  } finally { loaded.restore() }
})

test('confirmed recovery releases only the matching in-memory intent and clears stale count drafts', async () => {
  let pending = [SENTINEL]
  let currentIntent = {
    action: 'submit_recount_count', taskId: TASK, roundId: ROUND, scopeId: SCOPE,
    headers: { 'X-Request-ID': SENTINEL.trace_request_id }
  }
  let completed = 0
  let clearedUploads = 0
  const store = {
    readPending: () => pending.length ? { kind: 'valid', values: pending } : { kind: 'missing' },
    async withScopeLease(value, work) { return work({ value }) }
  }
  const loaded = loadPage({
    '../utils/session': { ensureLogin: () => true },
    '../utils/formal-stocktake-adapter': {
      formalStocktakeAdapter: {
        countRecoveryMode: 'durable',
        countCommandStatus() {},
        async loadIdentityNoReplay() {},
        async loadAccessNoReplay() {},
        async detailNoReplay() {},
        async execute() {}
      },
      createFormalStocktakeIntentRegistry: () => ({})
    },
    '../utils/formal-stocktake-count-recovery-store': {
      getFormalStocktakeCountRecoveryStore: () => store
    },
    '../utils/formal-stocktake-count-recovery': {
      createFormalStocktakeCountRecoveryAdapterFromFormalAdapter: () => ({}),
      async recoverFormalStocktakeCount() {
        pending = []
        return { detail: recoveredDetail(), command: {} }
      }
    }
  })
  try {
    const page = instance(loaded.definition)
    page._hidden = false
    page._taskId = TASK
    page._access = access()
    page._detail = recoveredDetail()
    page._loadGeneration = 1
    page._countRecoveryStore = store
    page._intentRegistry = {
      current: () => currentIntent,
      complete(value) { assert.equal(value, currentIntent); currentIntent = null; completed += 1 }
    }
    page._evidenceUploads = { clear() { clearedUploads += 1 } }
    page._evidenceClaims = new Map([['old', true]])
    Object.assign(page.data, {
      loading: false, accountDrafts: [{ old: true }], observations: [{ old: true }],
      evidenceUploadFiles: [{ old: true }], hasAccountInput: true,
      selectedScopeId: SCOPE, countAction: 'submit_recount_count'
    })
    await page.recoverCount()
    assert.equal(completed, 1)
    assert.equal(currentIntent, null)
    assert.equal(clearedUploads, 1)
    assert.equal(page._evidenceClaims.size, 0)
    assert.deepEqual(page.data.accountDrafts, [])
    assert.deepEqual(page.data.observations, [])
    assert.deepEqual(page.data.evidenceUploadFiles, [])
    assert.equal(page.data.hasAccountInput, false)
    assert.equal(page.data.pendingMessage, '')
    assert.equal(page.data.countRecoveryBlocked, false)
  } finally { loaded.restore() }
})

test('evidence upload handlers require a fresh authorized detail and cannot bypass the recovery barrier', async () => {
  let selects = 0
  const loaded = loadPage({
    '../utils/session': { ensureLogin: () => true },
    '../utils/formal-stocktake-adapter': {
      formalStocktakeAdapter: { countCommandStatus() {} },
      createFormalStocktakeIntentRegistry: () => ({ current: () => null })
    },
    '../utils/formal-stocktake-count-recovery-store': {
      getFormalStocktakeCountRecoveryStore: () => ({ readPending: () => ({ kind: 'missing' }) })
    }
  })
  try {
    const page = instance(loaded.definition)
    page.data.selectedScopeId = SCOPE
    page.data.countRecoveryBlocked = true
    page._evidenceUploads = { async select() { selects += 1 } }
    await page.chooseCountEvidence()
    assert.equal(selects, 0)
    page.data.countRecoveryBlocked = false
    page._access = null
    page._detail = null
    await page.chooseCountEvidence()
    assert.equal(selects, 0)
  } finally { loaded.restore() }
})

test('an unbranded partial production adapter fails closed before any business write', async () => {
  let writes = 0
  const loaded = loadPage({
    '../utils/session': { ensureLogin: () => true },
    '../utils/formal-stocktake-adapter': {
      formalStocktakeAdapter: {
        countRecoveryMode: 'broken',
        countCommandStatus() {},
        async execute() { writes += 1 }
      },
      createFormalStocktakeIntentRegistry: () => ({ current: () => null })
    },
    '../utils/formal-stocktake-count-recovery-store': {
      getFormalStocktakeCountRecoveryStore: () => ({ readPending: () => ({ kind: 'missing' }) })
    }
  })
  try {
    const page = instance(loaded.definition)
    page._hidden = false
    page._taskId = TASK
    page._access = access()
    page._detail = { task_id: TASK, version: 8 }
    page._loadGeneration = 1
    page._countRecoveryStore = { readPending: () => ({ kind: 'missing' }) }
    page._intentRegistry = { current: () => null }
    page.data.loading = false
    await page.run({ action: 'start', taskId: TASK, expectedTaskVersion: 8, body: { expected_version: 8 } })
    assert.equal(writes, 0)
    assert.equal(page.data.countRecoveryBlocked, true)
    assert.match(page.data.pendingMessage, /待只读核验|恢复能力未完整加载/)
  } finally { loaded.restore() }
})

test('an unmarked adapter stub is not silently promoted to the legacy compatibility mode', async () => {
  let writes = 0
  const loaded = loadPage({
    '../utils/session': { ensureLogin: () => true },
    '../utils/formal-stocktake-adapter': {
      formalStocktakeAdapter: {
        countCommandStatus() {},
        async execute() { writes += 1 }
      },
      createFormalStocktakeIntentRegistry: () => ({ current: () => null })
    },
    '../utils/formal-stocktake-count-recovery-store': {
      getFormalStocktakeCountRecoveryStore: () => ({ readPending: () => ({ kind: 'missing' }) })
    }
  })
  try {
    const page = instance(loaded.definition)
    page._hidden = false
    page._taskId = TASK
    page._access = access()
    page._detail = { task_id: TASK, version: 8 }
    page._loadGeneration = 1
    page._countRecoveryStore = { readPending: () => ({ kind: 'missing' }) }
    page._intentRegistry = { current: () => null }
    page.data.loading = false
    page.refreshCountRecovery()
    assert.match(page.data.pendingMessage, /持久恢复能力未完整加载/)
    await page.run({ action: 'start', taskId: TASK, expectedTaskVersion: 8, body: { expected_version: 8 } })
    assert.equal(writes, 0)
    assert.equal(page.data.countRecoveryBlocked, true)
    assert.match(page.data.pendingMessage, /待只读核验|停止其他写操作/)
  } finally { loaded.restore() }
})

test('hiding a detail page clears evidence binding and claims before late upload callbacks return', () => {
  const loaded = loadPage({
    '../utils/session': { ensureLogin: () => true },
    '../utils/formal-stocktake-adapter': {
      formalStocktakeAdapter: { countRecoveryMode: 'legacy-test' },
      createFormalStocktakeIntentRegistry: () => ({ current: () => null })
    },
    '../utils/formal-stocktake-count-recovery-store': {
      getFormalStocktakeCountRecoveryStore: () => ({ readPending: () => ({ kind: 'missing' }) })
    }
  })
  try {
    const page = instance(loaded.definition)
    let clears = 0
    page._evidenceUploads = { clear() { clears += 1 } }
    page._evidenceClaims = new Map([['sha256:x', 'intent']])
    page._intentRegistry = { current: () => null }
    page._uploadIdentity = 'old-person:3'
    page.data.accountDrafts = [{ counted_qty: '1' }]
    page.data.observations = [{ counted_qty: '1' }]
    page.data.evidenceUploadFiles = [{ status: 'available' }]
    page.data.hasAccountInput = true
    page.onHide()
    assert.equal(clears, 1)
    assert.equal(page._evidenceClaims.size, 0)
    assert.equal(page._uploadIdentity, '')
    assert.deepEqual(page.data.accountDrafts, [])
    assert.deepEqual(page.data.observations, [])
    assert.deepEqual(page.data.evidenceUploadFiles, [])
    assert.equal(page.data.hasAccountInput, false)
  } finally { loaded.restore() }
})
