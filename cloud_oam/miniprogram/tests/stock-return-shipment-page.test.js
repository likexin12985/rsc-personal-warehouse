const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const { createStore } = require('../utils/work-order-recovery-store')
const f = require('./fixtures/stock-return-shipment-data.cjs')
const clone = value => JSON.parse(JSON.stringify(value))
const tick = () => new Promise(resolve => setImmediate(resolve))
function harness(tracked = false) {
  const value = { ...f.input(tracked), shippedAt: '2026-09-13T01:02:00.000000Z' }, records = new Map()
  const store = createStore({ state: { active: new Set(), faults: new Set() }, storage: {
    getStorageInfoSync: () => ({ keys: [...records.keys()] }), getStorageSync: key => records.get(key) || '',
    setStorageSync: (key, data) => records.set(key, data), removeStorageSync: key => records.delete(key)
  } })
  const state = { user: { person_id: value.personId, authorization_version: value.authorizationVersion, role_codes: ['technician'] }, token: 'test-session',
    access: { allowed: ['read', 'ship_return'] }, calls: [], posts: [], facts: [], lost: null, cancelled: false, undeparted: false, sealed: null, navigated: [] }
  let definition, counter = 0
  const source = () => f.departures(value, state.cancelled || state.undeparted ? [] : undefined, state.cancelled)
  const input = data => ({ ...value, reason: data.reason, carrier: data.carrier, trackingNo: data.tracking_no, shippedAt: data.shipped_at, lines: data.lines })
  const api = {
    createRequestId: () => 'wxreq-' + (++counter).toString(16).padStart(36, '0'), createIdempotencyKey: () => 'wxidem-' + 'c'.repeat(36),
    async request(endpoint, options) {
      state.calls.push({ endpoint, ...clone(options) }); if (state.readHook) await state.readHook(endpoint)
      if (endpoint === '/auth/me') return clone(state.user)
      if (endpoint === '/access/context') return clone(state.access)
      if (endpoint.includes('/by-request/')) {
        const marker = store.read({ work_order_id: value.workOrderId }).value
        if (state.sealed) return clone(state.sealed)
        const result = state.facts.find(row => row.request_id === marker.trace_request_id)
        if (!result) throw Object.assign(new Error('not observed'), { status: 404, code: 'stock_return_not_observed', responseReceived: true })
        return clone(result)
      }
      if (endpoint.endsWith('/options')) return f.options(value, clone(state.facts), source())
      if (endpoint.endsWith('/preview')) return f.preview(input(options.data))
      if (endpoint.endsWith('/shipments')) return f.history(value, clone(state.facts), source())
      throw new Error('unexpected endpoint')
    },
    async postNoReplay(endpoint, data) {
      state.posts.push({ endpoint, ...clone(data) }); assert.equal(records.size, 1)
      if (state.lost === 'before') throw new Error('lost before')
      state.facts.push(f.result(input(data), store.read({ work_order_id: value.workOrderId }).value))
      if (state.lost === 'after') throw new Error('lost after')
      return { ignored: true }
    },
    async postSealNoReplay() { state.sealed = f.sealed(store.read({ work_order_id: value.workOrderId }).value) }
  }
  vm.runInNewContext(fs.readFileSync(path.resolve(__dirname, '../pages/formal-stock-return-shipments/index.js'), 'utf8'), {
    Page(value) { definition = value }, wx: { stopPullDownRefresh() {}, navigateTo(options) { state.navigated.push(options.url) }, showModal(options) { options.success({ confirm: true }) } },
    require(name) {
      if (name === '../../utils/api') return api
      if (name === '../../utils/session') return { ensureLogin: () => !!state.token, getToken: () => state.token, getUser: () => state.user }
      if (name === '../../utils/work-order-recovery-store') return { getStore: () => store }
      if (name === '../../utils/production-guard') return { inventoryAccessDecision: () => ({ allowed: true }), hasFormalPermission: (access, resource, action) => resource === 'stock_operation' && access.allowed.includes(action) }
      return require(path.resolve(__dirname, '../pages/formal-stock-return-shipments', name))
    }
  })
  const page = { ...definition, data: clone(definition.data), setData(update) { Object.assign(this.data, clone(update)) } }
  page.onLoad({ workOrderId: value.workOrderId, operationId: value.operationId })
  async function draft() {
    const event = { currentTarget: { dataset: { id: f.id(40) } } }
    page.addSource(event)
    if (tracked) page.chooseSerials({ ...event, detail: { value: value.lines[0].serial_ids } })
    else page.editQuantity({ ...event, detail: { value: '1' } })
    page.chooseDate({ detail: { value: '2026-09-13' } }); page.chooseTime({ detail: { value: '09:02' } })
    page.editCarrier({ detail: { value: value.carrier } }); page.editTrackingNo({ detail: { value: value.trackingNo } })
    page.editReason({ detail: { value: value.reason } }); page.renderDraft()
  }
  return { value, state, page, store, records, draft }
}
test('parcel page reads separate departure and shipment status without issuing a write', async () => {
  const x = harness(true); await x.page.onShow()
  assert.equal(x.page.data.ready, true); assert.equal(x.page.data.canSubmit, true); assert.equal(x.state.posts.length, 0)
  assert.equal(x.page.data.status, '尚未发运'); assert.equal(x.page.data.outboundStatus, '全部实物发出')
  assert.equal(x.page.data.items[0].unshipped_quantity, '1.000'); assert.ok(x.state.calls.every(row => row.noRefresh === true))
  assert.ok(!JSON.stringify(x.page.data).includes('qr_code'))
  x.page.openDepartures(); assert.equal(x.state.navigated[0], `/pages/formal-stock-return-outbounds/index?workOrderId=${x.value.workOrderId}&operationId=${x.value.operationId}`)
})
for (const tracked of [false, true]) test(`${tracked ? 'selected SN' : 'quantity'} parcel shows carrier, waybill, time and every line before sending`, async () => {
  const x = harness(tracked); await x.page.onShow(); await x.draft()
  const submitting = x.page.submit(); await tick()
  assert.equal(x.page.data.confirming, true); assert.equal(x.state.posts.length, 0); assert.equal(x.records.size, 0)
  assert.equal(x.page.data.review.rows[0].quantity, '1.000'); assert.equal(x.page.data.review.carrier, x.value.carrier)
  assert.equal(x.page.data.review.trackingNo, x.value.trackingNo); assert.equal(x.page.data.review.shippedAtLabel, '2026-09-13 09:02:00（北京时间）')
  assert.equal(x.page.data.review.rows[0].serials.length, tracked ? 1 : 0)
  // Draft edits and duplicate submit are disabled while confirmation is open.
  x.page.editCarrier({ detail: { value: 'Late replacement' } }); await x.page.submit()
  assert.equal(x.page.data.carrier, x.value.carrier)
  x.page.confirmSubmission(); await submitting
  assert.equal(x.state.posts.length, 1); assert.equal(x.records.size, 0); assert.equal(x.page.data.status, '全部发运')
  assert.equal(x.page.data.canSubmit, false); assert.equal(x.page.data.history.length, 1)
  assert.match(x.page.data.feedback, /独立验收和入账/); assert.equal(x.page.data.draftRows.length, 0)
  assert.equal(x.page.data.carrier, ''); assert.equal(x.page.data.trackingNo, ''); assert.deepEqual(clone(x.page._drafts), {})
})
test('undeparted, cancelled and read-only returns do not offer a new parcel', async () => {
  for (const mode of ['undeparted', 'cancelled', 'permission']) {
    const x = harness(); if (mode === 'permission') x.state.access.allowed = ['read']; else x.state[mode] = true
    await x.page.onShow(); assert.equal(x.page.data.ready, true); assert.equal(x.page.data.canSubmit, false)
    assert.equal(x.page.data.status, '尚未发运'); assert.equal(x.page.data.noChoices, mode === 'undeparted')
    if (mode !== 'undeparted') assert.equal(x.state.calls.filter(row => row.endpoint.endsWith('/options')).length, 0)
    await x.page.submit(); assert.equal(x.state.posts.length, 0)
  }
})
test('missing carrier, tracking or actual time cannot become a parcel', async () => {
  for (const field of ['carrier', 'trackingNo', 'date', 'clock']) {
    const x = harness(); await x.page.onShow(); await x.draft(); x.page.setData({ [field]: '' })
    await x.page.submit(); assert.equal(x.state.posts.length, 0); assert.equal(x.records.size, 0); assert.ok(x.page.data.feedback)
  }
})
test('actual seconds are preserved and malformed seconds cannot be silently replaced', async () => {
  const x = harness(); await x.page.onShow(); await x.draft()
  x.page.editSeconds({ detail: { value: '45' } })
  const submitting = x.page.submit(); await tick()
  assert.equal(x.page.data.review.shippedAtLabel, '2026-09-13 09:02:45（北京时间）')
  x.page.confirmSubmission(); await submitting
  assert.equal(x.state.posts[0].shipped_at, '2026-09-13T01:02:45.000000Z')
  for (const seconds of ['', '60', '-1', 'xx']) {
    const invalid = harness(); await invalid.page.onShow(); await invalid.draft(); invalid.page.editSeconds({ detail: { value: seconds } })
    await invalid.page.submit(); assert.equal(invalid.state.posts.length, 0); assert.match(invalid.page.data.feedback, /秒数/)
  }
})
test('malformed or unavailable choices retain history without claiming zero stock', async () => {
  const x = harness(); x.state.readHook = async endpoint => { if (endpoint.endsWith('/options')) throw new Error('changed') }
  await x.page.onShow(); assert.equal(x.page.data.ready, true); assert.equal(x.page.data.canSubmit, false); assert.equal(x.page.data.noChoices, false)
  assert.match(x.page.data.message, /暂未核验/)
})
test('hide during confirmation and late directory replies cannot restore or submit the draft', async () => {
  const x = harness(true); await x.page.onShow(); await x.draft()
  const submitting = x.page.submit(); await tick(); assert.equal(x.page.data.confirming, true)
  x.page.onHide(); await submitting; assert.equal(x.state.posts.length, 0); assert.equal(x.records.size, 0)
  let release
  x.state.readHook = endpoint => endpoint.endsWith('/options') ? new Promise(resolve => { release = resolve }) : undefined
  const loading = x.page.onShow(); await tick(); assert.ok(release)
  x.page.onHide(); release(); await loading
  assert.equal(x.page.data.ready, false); assert.deepEqual(clone(x.page._drafts), {}); assert.equal(x.page.data.trackingNo, '')
})
for (const mode of ['before', 'after']) test(`uncertain ${mode} parcel response survives page restart without replay`, async () => {
  const x = harness(); await x.page.onShow(); await x.draft(); x.state.lost = mode
  const submitting = x.page.submit(); await tick(); x.page.confirmSubmission(); await submitting
  assert.equal(x.records.size, 1); assert.equal(x.page.data.pending, true); assert.equal(x.state.posts.length, 1)
  await x.page.submit(); assert.equal(x.state.posts.length, 1)
  x.page.onHide(); await x.page.onShow(); await x.page.recover()
  assert.equal(x.records.size, mode === 'after' ? 0 : 1); assert.equal(x.state.posts.length, 1)
  if (mode === 'before') { await x.page.seal(); assert.equal(x.records.size, 0); assert.match(x.page.data.feedback, /已永久封存/) }
})
test('identity or permission drift at preview or confirmation removes drafts and blocks sending', async () => {
  for (const phase of ['preview', 'confirm', 'session']) {
    const x = harness(true); await x.page.onShow(); await x.draft()
    if (phase === 'preview') { x.state.readHook = async endpoint => { if (endpoint.endsWith('/preview')) x.state.access.allowed = ['read'] }; await x.page.preview() }
    else { const submitting = x.page.submit(); await tick(); if (phase === 'session') x.state.token = 'another'; else x.state.access.allowed = ['read']; x.page.confirmSubmission(); await submitting }
    assert.equal(x.state.posts.length, 0); assert.equal(x.records.size, 0); assert.equal(x.page.data.draftRows.length, 0)
    assert.equal(x.page.data.carrier, ''); assert.equal(x.page.data.trackingNo, '')
  }
})
