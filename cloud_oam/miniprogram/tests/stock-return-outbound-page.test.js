const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const { createStore } = require('../utils/work-order-recovery-store')
const f = require('./fixtures/stock-return-outbound-data.cjs')
const clone = value => JSON.parse(JSON.stringify(value))
const tick = () => new Promise(resolve => setImmediate(resolve))
function harness(tracked = false) {
  const value = { ...f.input(tracked), outboundAt: '2026-09-13T01:00:00.000000Z' }, records = new Map()
  const store = createStore({ state: { active: new Set(), faults: new Set() }, storage: {
    getStorageInfoSync: () => ({ keys: [...records.keys()] }), getStorageSync: key => records.get(key) || '',
    setStorageSync: (key, data) => records.set(key, data), removeStorageSync: key => records.delete(key)
  } })
  const state = { user: { person_id: value.personId, authorization_version: value.authorizationVersion, role_codes: ['technician'] }, token: 'test-session',
    access: { allowed: ['read', 'outbound_return'] }, calls: [], posts: [], facts: [], lost: null, scan: null, cancelled: false }
  let definition, counter = 0
  const api = {
    createRequestId: () => 'wxreq-' + (++counter).toString(16).padStart(36, '0'), createIdempotencyKey: () => 'wxidem-' + 'c'.repeat(36),
    async request(endpoint, options) {
      state.calls.push({ endpoint, ...clone(options) }); if (state.readHook) await state.readHook(endpoint)
      if (endpoint === '/auth/me') return clone(state.user)
      if (endpoint === '/access/context') return clone(state.access)
      if (endpoint.includes('/by-request/')) {
        const marker = store.read({ work_order_id: value.workOrderId }).value, result = state.facts.find(row => row.request_id === marker.trace_request_id)
        if (!result) throw Object.assign(new Error('not observed'), { status: 404, code: 'stock_return_not_observed', responseReceived: true })
        return clone(result)
      }
      if (endpoint.endsWith('/options')) return f.options(value)
      if (endpoint.endsWith('/preview')) return f.preview({ ...value, reason: options.data.reason, outboundAt: options.data.outbound_at, lines: options.data.lines })
      if (endpoint.endsWith('/outbounds')) return f.history(value, clone(state.facts), state.cancelled)
      throw new Error('unexpected endpoint')
    },
    async postNoReplay(endpoint, data) {
      state.posts.push({ endpoint, ...clone(data) }); assert.equal(records.size, 1)
      if (state.lost === 'before') throw new Error('lost before')
      const marker = store.read({ work_order_id: value.workOrderId }).value
      state.facts.push(f.result({ ...value, reason: data.reason, outboundAt: data.outbound_at, lines: data.lines }, marker))
      if (state.lost === 'after') throw new Error('lost after')
      return { ignored: true }
    }
  }
  vm.runInNewContext(fs.readFileSync(path.resolve(__dirname, '../pages/formal-stock-return-outbounds/index.js'), 'utf8'), {
    Page(value) { definition = value }, wx: { stopPullDownRefresh() {}, scanCode(options) { state.scan = options }, showModal(options) { options.success({ confirm: true }) } },
    require(name) {
      if (name === '../../utils/api') return api
      if (name === '../../utils/session') return { ensureLogin: () => !!state.token, getToken: () => state.token, getUser: () => state.user }
      if (name === '../../utils/work-order-recovery-store') return { getStore: () => store }
      if (name === '../../utils/production-guard') return { inventoryAccessDecision: () => ({ allowed: true }), hasFormalPermission: (access, resource, action) => resource === 'stock_operation' && access.allowed.includes(action) }
      return require(path.resolve(__dirname, '../pages/formal-stock-return-outbounds', name))
    }
  })
  const page = { ...definition, data: clone(definition.data), setData(update) { Object.assign(this.data, clone(update)) } }
  page.onLoad({ workOrderId: value.workOrderId, operationId: value.operationId })
  async function draft() {
    page.addSource({ currentTarget: { dataset: { id: f.id(31) } } })
    if (tracked) {
      for (const code of ['sku_code', 'serial_no', 'qr_code']) {
        const scanning = page.scan({ currentTarget: { dataset: { id: f.id(31), code } } })
        assert.equal(state.scan.onlyFromCamera, true); state.scan.success({ result: value.lines[0].serial_verifications[0][code] }); await scanning
      }
      page.collectScan({ currentTarget: { dataset: { id: f.id(31) } } })
    } else page.editQuantity({ currentTarget: { dataset: { id: f.id(31) } }, detail: { value: '1' } })
    page.chooseDate({ detail: { value: '2026-09-13' } }); page.chooseTime({ detail: { value: '09:00' } })
    page.editReason({ detail: { value: value.reason } }); page.renderDraft()
  }
  return { value, state, page, store, records, draft }
}
test('departure page loads original status and remaining quantities without a stock write', async () => {
  const x = harness(true); await x.page.onShow()
  assert.equal(x.page.data.ready, true); assert.equal(x.page.data.canSubmit, true); assert.equal(x.state.posts.length, 0)
  assert.equal(x.page.data.status, '尚未发出'); assert.equal(x.page.data.items[0].remaining_quantity, '1.000')
  assert.ok(x.state.calls.every(row => row.noRefresh === true)); assert.ok(!JSON.stringify(x.page.data).includes('PRIVATE-RETURN-QR'))
})
for (const tracked of [false, true]) test(`${tracked ? 'camera SN' : 'quantity'} departure requires time and a full confirmation before posting`, async () => {
  const x = harness(tracked); await x.page.onShow(); await x.draft()
  const submitting = x.page.submit(); await tick()
  assert.equal(x.page.data.confirming, true); assert.equal(x.state.posts.length, 0); assert.equal(x.records.size, 0)
  assert.equal(x.page.data.review.rows[0].quantity, '1.000')
  assert.equal(x.page.data.review.outboundAt, '2026-09-13T01:00:00.000000Z')
  assert.equal(x.page.data.review.outboundAtLabel, '2026-09-13 09:00:00（北京时间）')
  assert.ok(!JSON.stringify(x.page.data).includes('PRIVATE-RETURN-QR'))
  x.page.confirmSubmission(); await submitting
  assert.equal(x.state.posts.length, 1); assert.equal(x.records.size, 0); assert.equal(x.page.data.status, '全部发出')
  assert.equal(x.page.data.canSubmit, false); assert.equal(x.page.data.history.length, 1)
  assert.match(x.page.data.feedback, /独立验收和入账/); assert.equal(x.page.data.draftRows.length, 0)
  assert.deepEqual(clone(x.page._scans), {}); assert.deepEqual(clone(x.page._drafts), {})
})
test('cancelled or read-only return shows independent history but never offers a new departure', async () => {
  for (const mode of ['cancelled', 'permission']) {
    const x = harness(); if (mode === 'cancelled') x.state.cancelled = true; else x.state.access.allowed = ['read']
    await x.page.onShow(); assert.equal(x.page.data.ready, true); assert.equal(x.page.data.canSubmit, false)
    assert.equal(x.page.data.status, '尚未发出'); assert.equal(x.state.calls.filter(row => row.endpoint.endsWith('/options')).length, 0)
    await x.page.submit(); assert.equal(x.state.posts.length, 0)
  }
})
test('missing actual time cannot become a departure and malformed directory retains history', async () => {
  const x = harness(); await x.page.onShow(); await x.draft(); x.page.setData({ date: '', clock: '' })
  await x.page.submit(); assert.equal(x.state.posts.length, 0); assert.match(x.page.data.feedback, /实际发出/)
  const bad = harness()
  bad.state.readHook = async endpoint => { if (endpoint.endsWith('/options')) throw new Error('changed') }
  await bad.page.onShow(); assert.equal(bad.page.data.ready, true); assert.equal(bad.page.data.canSubmit, false)
  assert.match(bad.page.data.message, /暂未核验/); assert.equal(bad.state.posts.length, 0)
})
test('hide during confirmation or late scan cannot send or restore physical codes', async () => {
  const x = harness(true); await x.page.onShow(); await x.draft()
  const submitting = x.page.submit(); await tick(); assert.equal(x.page.data.confirming, true)
  x.page.onHide(); await submitting; assert.equal(x.state.posts.length, 0); assert.equal(x.records.size, 0)
  await x.page.onShow(); await x.draft()
  const scan = x.page.scan({ currentTarget: { dataset: { id: f.id(31), code: 'qr_code' } } })
  x.page.onHide(); x.state.scan.success({ result: 'PRIVATE-LATE-SCAN' }); await scan
  assert.deepEqual(clone(x.page._scans), {}); assert.deepEqual(clone(x.page._drafts), {}); assert.equal(x.page.data.draftRows.length, 0)
})
for (const mode of ['before', 'after']) test(`uncertain ${mode} delivery retains original request across page reload without replay`, async () => {
  const x = harness(); await x.page.onShow(); await x.draft(); x.state.lost = mode
  const submitting = x.page.submit(); await tick(); x.page.confirmSubmission(); await submitting
  assert.equal(x.records.size, 1); assert.equal(x.page.data.pending, true); assert.equal(x.state.posts.length, 1)
  await x.page.submit(); assert.equal(x.state.posts.length, 1)
  x.page.onHide(); await x.page.onShow(); await x.page.recover()
  assert.equal(x.records.size, mode === 'after' ? 0 : 1); assert.equal(x.state.posts.length, 1)
})
test('identity and authority drift during preview or confirmation erase the draft and block sending', async () => {
  for (const phase of ['preview', 'confirm', 'session']) {
    const x = harness(true); await x.page.onShow(); await x.draft()
    if (phase === 'preview') { x.state.readHook = async endpoint => { if (endpoint.endsWith('/preview')) x.state.access.allowed = ['read'] }; await x.page.preview() }
    else {
      const submitting = x.page.submit(); await tick()
      if (phase === 'session') x.state.token = 'another'; else x.state.access.allowed = ['read']
      x.page.confirmSubmission(); await submitting
    }
    assert.equal(x.state.posts.length, 0); assert.equal(x.records.size, 0); assert.equal(x.page.data.draftRows.length, 0)
    assert.ok(!JSON.stringify(x.page._drafts).includes('PRIVATE-RETURN-QR'))
  }
})
