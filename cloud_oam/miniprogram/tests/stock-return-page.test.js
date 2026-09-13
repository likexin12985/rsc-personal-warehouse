const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const { createStore } = require('../utils/work-order-recovery-store')
const f = require('./fixtures/stock-return-data.cjs')
const clone = value => JSON.parse(JSON.stringify(value))
const tick = () => new Promise(resolve => setImmediate(resolve))
function harness(tracked = false) {
  const value = f.input(tracked), records = new Map()
  const store = createStore({ state: { active: new Set(), faults: new Set() }, storage: {
    getStorageInfoSync: () => ({ keys: [...records.keys()] }), getStorageSync: key => records.get(key) || '',
    setStorageSync: (key, data) => records.set(key, data), removeStorageSync: key => records.delete(key)
  } })
  const state = { user: { person_id: value.personId, authorization_version: value.authorizationVersion, role_codes: ['technician'] }, token: 'test-session',
    access: { allowed: ['read', 'submit_return', 'cancel_return'] }, calls: [], posts: [], original: null, cancellation: null, lost: null, scan: null }
  let definition, counter = 0
  const api = {
    createRequestId: () => 'wxreq-' + (++counter).toString(16).padStart(36, '0'), createIdempotencyKey: () => 'wxidem-' + 'c'.repeat(36),
    async request(endpoint, options) {
      state.calls.push({ endpoint, ...clone(options) })
      if (state.readHook) await state.readHook(endpoint)
      if (endpoint === '/auth/me') return clone(state.user)
      if (endpoint === '/access/context') return clone(state.access)
      if (endpoint.includes('/by-request/')) {
        const marker = store.read({ work_order_id: value.workOrderId }).value
        const result = marker.operation_type === 'cancel_return' ? state.cancellation : state.original
        if (!result) throw Object.assign(new Error('not observed'), { status: 404, code: 'stock_return_not_observed', responseReceived: true })
        return clone(result)
      }
      if (endpoint.endsWith('/options')) {
        const raw = f.options(value)
        if (state.original && !state.cancellation) {
          Object.assign(raw.sources.items[0], { committed_quantity: '1.000', selectable_quantity: '0.000', available_quantity: '0.000' })
          raw.sources.items[0].serials.forEach(sn => { sn.selectable = false })
        }
        return raw
      }
      if (endpoint.endsWith('/preview')) return f.preview({ ...value, reason: options.data.reason })
      if (endpoint.endsWith('/returns')) return { ...f.history(value), items: state.original ? [{ original: clone(state.original), cancellation: clone(state.cancellation) }] : [] }
      throw new Error('unexpected endpoint')
    },
    async postNoReplay(endpoint, data) {
      state.posts.push({ endpoint, ...clone(data) }); assert.equal(records.size, 1)
      if (state.lost === 'before') throw new Error('lost before')
      const marker = store.read({ work_order_id: value.workOrderId }).value
      const result = f.result({ ...value, reason: data.reason }, marker)
      if (marker.operation_type === 'cancel_return') state.cancellation = result
      else state.original = result
      if (state.lost === 'after') throw new Error('lost after')
      return { ignored: true }
    }
  }
  vm.runInNewContext(fs.readFileSync(path.resolve(__dirname, '../pages/formal-stock-returns/index.js'), 'utf8'), {
    Page(value) { definition = value }, wx: { stopPullDownRefresh() {}, scanCode(options) { state.scan = options }, showModal(options) { options.success({ confirm: true }) } },
    require(name) {
      if (name === '../../utils/api') return api
      if (name === '../../utils/session') return { ensureLogin: () => !!state.token, getToken: () => state.token, getUser: () => state.user }
      if (name === '../../utils/work-order-recovery-store') return { getStore: () => store }
      if (name === '../../utils/production-guard') return { inventoryAccessDecision: () => ({ allowed: true }), hasFormalPermission: (access, resource, action) => resource === 'stock_operation' && access.allowed.includes(action) }
      return require(path.resolve(__dirname, '../pages/formal-stock-returns', name))
    }
  })
  const page = { ...definition, data: clone(definition.data), setData(update) { Object.assign(this.data, clone(update)) } }
  page.onLoad({ workOrderId: value.workOrderId })
  function draft() {
    page.addSource({ currentTarget: { dataset: { id: f.id(11) } } })
    if (tracked) page._drafts[f.id(11)].serial_verifications = clone(value.lines[0].serial_verifications)
    else page.editQuantity({ currentTarget: { dataset: { id: f.id(11) } }, detail: { value: '1' } })
    page.chooseDestination({ detail: { value: 0 } }); page.editReason({ detail: { value: value.reason } }); page.renderDraft()
  }
  return { value, state, page, store, records, draft }
}
test('closed work-order return page loads current sources and separate history without writing', async () => {
  const x = harness(true); await x.page.onShow()
  assert.equal(x.page.data.ready, true); assert.equal(x.page.data.canSubmit, true); assert.equal(x.state.posts.length, 0)
  assert.equal(x.page.data.destinations[0].label, '区域仓 · 退回在途位置')
  assert.ok(x.state.calls.every(row => row.noRefresh === true))
  assert.ok(!JSON.stringify(x.page.data).includes('PRIVATE-RETURN-QR'))
})
for (const tracked of [false, true]) test(`${tracked ? 'SN' : 'quantity'} page confirms full rows, submits and cancels only after another confirmation`, async () => {
  const x = harness(tracked); await x.page.onShow(); x.draft()
  const submitting = x.page.submit(); await tick()
  assert.equal(x.page.data.confirming, true); assert.equal(x.state.posts.length, 0); assert.equal(x.records.size, 0)
  assert.equal(x.page.data.review.rows[0].quantity, '1.000')
  assert.ok(!JSON.stringify(x.page.data.review).includes('PRIVATE-RETURN-QR'))
  x.page.confirmSubmission(); await submitting
  assert.equal(x.state.posts.length, 1); assert.equal(x.records.size, 0); assert.equal(x.page.data.history[0].cancellation, null)
  assert.equal(x.page.data.draftRows.length, 0); assert.deepEqual(clone(x.page._scans), {})
  x.page.chooseCancellation({ currentTarget: { dataset: { id: f.id(20) } } })
  x.page.editCancelReason({ detail: { value: '实物尚未寄出' } })
  const cancelling = x.page.cancelReturn(); await tick(); assert.equal(x.state.posts.length, 1)
  x.page.confirmSubmission(); await cancelling
  assert.equal(x.state.posts.length, 2); assert.equal(x.page.data.history[0].cancellation.status, 'cancelled')
  assert.match(x.page.data.feedback, /应退责任仍保留/)
})
test('leaving confirmation sends nothing and a late scan cannot restore discarded physical codes', async () => {
  const x = harness(true); await x.page.onShow(); x.draft()
  const submitting = x.page.submit(); await tick(); assert.equal(x.page.data.confirming, true)
  x.page.onHide(); await submitting
  assert.equal(x.state.posts.length, 0); assert.equal(x.records.size, 0); assert.equal(x.page.data.draftRows.length, 0)
  await x.page.onShow(); x.draft()
  const scan = x.page.scan({ currentTarget: { dataset: { id: f.id(11), code: 'qr_code' } } })
  x.page.onHide(); x.state.scan.success({ result: 'PRIVATE-LATE-SCAN' }); await scan
  assert.ok(!JSON.stringify(x.page.data).includes('PRIVATE-LATE-SCAN')); assert.deepEqual(clone(x.page._scans), {})
})
for (const mode of ['before', 'after']) test(`lost ${mode} delivery retains recovery across reload and never resends`, async () => {
  const x = harness(); await x.page.onShow(); x.draft(); x.state.lost = mode
  const promise = x.page.submit(); await tick(); x.page.confirmSubmission(); await promise
  assert.equal(x.state.posts.length, 1); assert.equal(x.records.size, 1); assert.equal(x.page.data.pending, true)
  assert.equal(x.page.data.draftRows.length, 0)
  await x.page.submit(); assert.equal(x.state.posts.length, 1)
  x.page.onHide(); await x.page.onShow(); await x.page.recover()
  assert.equal(x.records.size, mode === 'after' ? 0 : 1); assert.equal(x.state.posts.length, 1)
})
test('authority changes during preview or confirmation erase the physical draft and prevent sending', async () => {
  for (const phase of ['preview', 'confirm', 'session']) {
    const x = harness(true); await x.page.onShow(); x.draft()
    if (phase === 'preview') x.state.readHook = async endpoint => { if (endpoint.endsWith('/preview')) x.state.access.allowed = ['read'] }
    if (phase === 'preview') await x.page.preview()
    else {
      const promise = x.page.submit(); await tick()
      if (phase === 'session') x.state.token = 'another-account'
      else x.state.access.allowed = ['read']
      x.page.confirmSubmission(); await promise
    }
    assert.equal(x.state.posts.length, 0); assert.equal(x.records.size, 0)
    assert.equal(x.page.data.draftRows.length, 0); assert.ok(!JSON.stringify(x.page._drafts).includes('PRIVATE-RETURN-QR'))
  }
})
