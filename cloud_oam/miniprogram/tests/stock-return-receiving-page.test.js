const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const f = require('./fixtures/stock-return-receiving-data.cjs')
const { createStore } = require('../utils/work-order-recovery-store')
const inboundContract = require('../utils/stock-return-inbound-contract')
const clone = value => JSON.parse(JSON.stringify(value))
const tick = () => new Promise(resolve => setImmediate(resolve))
function harness(detail = true) {
  const state = { token: 'test-session', user: { person_id: f.id(1), authorization_version: 1, role_codes: ['provincial_manager'] },
    access: { read: true }, calls: [], history: f.history(true, [f.receipt(true, 'shortage'), f.receipt(true, 'damaged', 2)]),
    directory: f.directory(), urls: [], inboundStates: {}, writes: [], sealWrites: [] }
  const saved = new Map(), storage = { getStorageInfoSync: () => { if (state.storageUnavailable) throw new Error('storage unavailable'); return { keys: [...saved.keys()] } },
    getStorageSync: key => saved.get(key) || '', setStorageSync: (key, value) => saved.set(key, value), removeStorageSync: key => saved.delete(key) }
  const store = createStore({ storage, state: { active: new Set(), faults: new Set() } })
  state.store = store; state.saved = saved
  const request = async (endpoint, options) => {
    state.calls.push({ endpoint, ...clone(options) }); if (state.hook) await state.hook(endpoint, options)
    if (endpoint === '/auth/me') return clone(state.user)
    if (endpoint === '/access/context') return clone(state.access)
    if (endpoint.endsWith('/inbound/preview')) return clone(state.inboundPreview)
    if (endpoint.endsWith('/inbound')) { const receipt = endpoint.split('/').at(-2); return clone(state.inboundStates[receipt] || inboundState(receipt)) }
    if (endpoint.includes('/inbound/by-request/')) return clone(state.inboundResult)
    return clone(endpoint.endsWith('/receipts') ? state.history : state.directory)
  }
  const api = { request, createRequestId: () => 'wxreq-' + 'a'.repeat(36), createIdempotencyKey: () => 'synthetic-inbound-key',
    async postSealNoReplay(endpoint, body, options) {
      state.sealWrites.push({ endpoint, body: clone(body), options: clone(options) })
      const marker = JSON.parse([...saved.values()][0]); state.inboundResult = sealedResult(marker)
      return clone(state.inboundResult)
    },
    async postNoReplay(endpoint, body, options) {
      state.writes.push({ endpoint, body: clone(body), options: clone(options) })
      if (state.postHook) await state.postHook()
      const receipt = endpoint.split('/').at(-2)
      state.inboundResult = { schema_version: '1.0', inbound_id: f.id(51), inbound_no: 'RET-IN-TEST', receipt_id: receipt, shipment_id: f.id(2),
        target_location_id: f.id(31), target_custody_assignment_id: f.id(32), status: 'posted', posting_transaction_id: f.id(52),
        request_id: body.request_id, request_hash: inboundContract.requestHash(receipt, body.expected_plan_hash, body.request_id), plan_hash: body.expected_plan_hash, replayed: false }
      state.inboundStates[receipt] = inboundState(receipt, true)
      return clone(state.inboundResult)
    } }
  let definition
  vm.runInNewContext(fs.readFileSync(path.resolve(__dirname, '../pages/formal-stock-return-receiving/index.js'), 'utf8'), {
    Page(value) { definition = value }, wx: { stopPullDownRefresh() {}, navigateTo({ url }) { state.urls.push(url) }, showModal({ success }) { success({ confirm: true }) } },
    require(name) {
      if (name === '../../utils/api') return api
      if (name === '../../utils/session') return { ensureLogin: () => !!state.token, getToken: () => state.token, getUser: () => state.user }
      if (name === '../../utils/production-guard') return { inventoryAccessDecision: () => ({ allowed: true }), hasFormalPermission: (access, _resource, action) => access.read && (action !== 'receive_return' || access.write !== false) }
      if (name === '../../utils/work-order-recovery-store') return { getStore: () => store }
      return require(path.resolve(__dirname, '../pages/formal-stock-return-receiving', name))
    }
  })
  const page = { ...definition, data: clone(definition.data), setData(value) { Object.assign(this.data, clone(value)) } }
  page.onLoad(detail ? { shipmentId: f.id(2) } : {})
  return { page, state }
}

function inboundFixture() {
  return { schema_version: '1.0', planning_status: 'inbound_preview_only', receipt_id: f.id(21), shipment_id: f.id(2),
    operator_person_id: f.id(1), authorization_version: 1, target_location_id: f.id(31), target_custody_assignment_id: f.id(32),
    receipt_plan_hash: 'c'.repeat(64), plan_hash: 'd'.repeat(64), reason: '退回件入账', checked_at: '2026-09-14T01:02:03Z', ledger_cursor: 12,
    lines: [{ receipt_line_id: f.id(211), shipment_line_id: f.id(8), source_account_id: f.id(41), target_account_id: f.id(42),
      material_id: f.id(43), condition_code: 'used', lot_id: null, accepted_qty: '1.000', serial_ids: [] }] }
}
function inboundState(receiptId = f.id(21), posted = false) {
  return { schema_version: '1.0', receipt_id: receiptId, shipment_id: f.id(2), operator_person_id: f.id(1), authorization_version: 1,
    status: posted ? 'posted' : 'not_posted', ledger_cursor: posted ? 13 : 12, checked_at: '2026-09-14T01:02:05Z',
    inbound: posted ? { inbound_id: f.id(51), inbound_no: 'RET-IN-TEST', target_location_id: f.id(31), posting_transaction_id: f.id(52), posted_at: '2026-09-14T01:02:04Z' } : null }
}
test('regional recipient reads exact partial history and selected abnormal receipt without any write', async () => {
  const { page, state } = harness(); await page.onShow()
  assert.equal(page.data.ready, true); assert.equal(page.data.progress[0].remaining, '0.000')
  assert.equal(page.data.progress[0].accepted, '1.000'); assert.equal(page.data.progress[0].damaged, '1.000')
  assert.equal(page.data.batches.length, 2); await page.selectReceipt({ currentTarget: { dataset: { id: f.id(21) } } })
  assert.equal(page.data.selectedReceipt.lines[0].shortage, '1.000')
  await page.selectReceipt({ currentTarget: { dataset: { id: f.id(22) } } })
  assert.equal(page.data.selectedReceipt.lines[0].serials[3].rows[0].number, 'TEST-SN')
  assert.match(page.data.message, /入账.*分别保存/)
  assert.ok(state.calls.every(call => call.method === 'GET' && call.noRefresh && call.header['Cache-Control'] === 'no-store'))
  for (const hidden of ['qr_code', 'request_hash', 'plan_hash', 'request_id', 'stock_account_id', 'posting_transaction_id']) assert.ok(!JSON.stringify(page.data).includes(hidden))
})
test('list isolates blocked parcels and only opens observed exact IDs', async () => {
  const { page, state } = harness(false)
  state.directory.items.push({ verification_status: 'unavailable', shipment_id: f.id(30), code: 'stock_return_receiving_verification_required', message: '原责任需核验' })
  await page.onShow(); assert.equal(page.data.packages.length, 2); assert.equal(page.data.packages[1].verified, false)
  for (const id of [f.id(30), f.id(300)]) page.openPackage({ currentTarget: { dataset: { id } } })
  assert.equal(state.urls.length, 0)
  page.openPackage({ currentTarget: { dataset: { id: f.id(2) } } })
  assert.equal(state.urls[0], `/pages/formal-stock-return-receiving/index?shipmentId=${f.id(2)}`)
})
test('directory pagination replaces the page and uses the returned exact cursor', async () => {
  const { page, state } = harness(false)
  state.directory.items = Array.from({ length: 10 }, (_, n) => ({ ...f.parcel(), shipment_id: f.id(200 + n) }))
  state.directory.next_after_id = f.id(209); await page.onShow(); assert.equal(page.data.next, true)
  state.directory.items = [{ ...f.parcel(), shipment_id: f.id(210) }]; state.directory.next_after_id = null
  await page.nextPage(); assert.equal(page.data.packages.length, 1); assert.equal(page.data.packages[0].id, f.id(210))
  assert.ok(state.calls.some(call => call.endpoint.endsWith(`limit=10&after_id=${f.id(209)}`)))
})
test('a long remaining SN list expands without losing its exact original position', async () => {
  const { page, state } = harness(); state.history = f.history(true)
  const line = state.history.package.lines[0]
  line.serials = Array.from({ length: 25 }, (_, n) => ({ serial_id: f.id(400 + n), serial_no: `TEST-${n}` })); line.shipped_quantity = '25.000'
  Object.assign(state.history.lines[0], { shipped_qty: '25.000', unconfirmed_qty: '25.000', unconfirmed_serials: line.serials })
  await page.onShow(); assert.equal(page.data.progress[0].serials.rows.length, 20)
  page.moreSerials({ currentTarget: { dataset: { key: page.data.progress[0].serials.key } } })
  assert.equal(page.data.progress[0].serials.rows.length, 25); assert.equal(page.data.progress[0].serials.more, false)
})
test('engineer role, denied access and changed recipient never expose regional parcel details', async () => {
  for (const mode of ['role', 'read', 'person']) {
    const { page, state } = harness()
    if (mode === 'role') state.user.role_codes = ['technician']
    if (mode === 'read') state.access.read = false
    if (mode === 'person') state.history.package.receiver_person_id = f.id(600)
    await page.onShow(); assert.equal(page.data.ready, false); assert.equal(page.data.package, null)
  }
})
test('hide and session changes discard late replies and all business projections', async () => {
  for (const mode of ['hide', 'session', 'permission']) {
    const { page, state } = harness(); let release
    state.hook = endpoint => endpoint.endsWith('/receipts') ? new Promise(resolve => { release = resolve }) : undefined
    const loading = page.onShow(); await tick(); assert.ok(release)
    if (mode === 'hide') page.onHide()
    if (mode === 'session') state.token = 'another-session'
    if (mode === 'permission') state.access.read = false
    release(); await loading; assert.equal(page.data.ready, false); assert.equal(page.data.package, null); assert.equal(page._history, null)
  }
})
test('a failed refresh clears prior confirmed quantities without presenting zero stock', async () => {
  const { page, state } = harness(); await page.onShow(); assert.equal(page.data.ready, true)
  state.hook = () => { throw new Error('offline') }; await page.refresh()
  assert.equal(page.data.ready, false); assert.deepEqual(clone(page.data.progress), []); assert.equal(page.data.package, null)
  assert.match(page.data.message, /暂时无法核验/)
})

test('selected accepted receipt previews and posts independent return inbound', async () => {
  const { page, state } = harness(); state.inboundPreview = inboundFixture()
  state.inboundResult = { schema_version: '1.0', inbound_id: f.id(51), inbound_no: 'RET-IN-TEST', receipt_id: f.id(21), shipment_id: f.id(2),
    target_location_id: f.id(31), target_custody_assignment_id: f.id(32), status: 'posted', posting_transaction_id: f.id(52),
    request_id: 'wxreq-' + 'a'.repeat(36), request_hash: 'e'.repeat(64), plan_hash: 'd'.repeat(64), replayed: false }
  state.history = f.history(true, [f.receipt(true)])
  await page.onShow(); await page.selectReceipt({ currentTarget: { dataset: { id: f.id(21) } } }); await page.beginInbound()
  assert.equal(page.data.inboundConfirming, true); assert.equal(page.data.inboundReview.rows[0].quantity, '1.000')
  await page.confirmInbound({ currentTarget: { dataset: { confirm: 'false' } } }); assert.equal(state.inboundPosted, undefined)
  assert.equal(state.writes.length, 0)
})

const choose = (page, id = f.id(21)) => page.selectReceipt({ currentTarget: { dataset: { id } } })
const confirm = page => page.confirmInbound({ currentTarget: { dataset: { confirm: 'true' } } })
function acceptedHarness() { const value = harness(); value.state.history = f.history(true, [f.receipt(true)]); value.state.inboundPreview = inboundFixture(); return value }
async function pending(state, receiptId = f.id(22)) {
  const marker = { v: 1, kind: 'stock_return_inbound', work_order_id: f.id(4), shipment_id: f.id(2), receipt_id: receiptId,
    person_id: f.id(1), authorization_version: 1, operation_type: 'receive_return', trace_request_id: 'wxreq-' + 'b'.repeat(36),
    plan_hash: 'd'.repeat(64), request_hash: inboundContract.requestHash(receiptId, 'd'.repeat(64), 'wxreq-' + 'b'.repeat(36)) }
  await state.store.withLease(marker, lease => lease.persist(marker))
  return marker
}
function sealedResult(marker) { return { schema_version: '1.0', lookup_status: 'sealed', seal: {
  seal_id: f.id(71), receipt_id: marker.receipt_id, shipment_id: marker.shipment_id, request_id: marker.trace_request_id,
  request_hash: marker.request_hash, sealed_at: '2026-09-14T01:02:04Z' } } }
function postedResult(marker) { return { schema_version: '1.0', inbound_id: f.id(51), inbound_no: 'RET-IN-TEST',
  receipt_id: marker.receipt_id, shipment_id: marker.shipment_id, target_location_id: f.id(31), target_custody_assignment_id: f.id(32),
  status: 'posted', posting_transaction_id: f.id(52), request_id: marker.trace_request_id,
  request_hash: marker.request_hash, plan_hash: marker.plan_hash, replayed: false } }

test('posted state survives refresh without a local request marker and cannot start another inbound', async () => {
  const { page, state } = acceptedHarness(); state.inboundStates[f.id(21)] = inboundState(f.id(21), true)
  await page.onShow(); await choose(page); assert.equal(page.data.inboundStateView.number, 'RET-IN-TEST')
  await page.refresh(); assert.equal(page.data.selectedReceipt.id, f.id(21)); assert.equal(page.data.inboundStatus, 'posted')
  assert.equal(page.data.inboundCanPost, false); await page.beginInbound(); await confirm(page)
  assert.equal(state.writes.length, 0); assert.equal(state.saved.size, 0)
  assert.ok(!state.calls.some(call => call.endpoint.endsWith('/preview')))
  assert.ok(!JSON.stringify(page.data).includes('posting_transaction_id'))
})

test('not posted requires fresh state and positive acceptance with current write permission', async () => {
  for (const mode of ['accepted', 'zero', 'read_only', 'bad_storage']) {
    const { page, state } = acceptedHarness()
    if (mode === 'zero') state.history = f.history(true, [f.receipt(true, 'shortage')])
    if (mode === 'read_only') state.access.write = false
    if (mode === 'bad_storage') state.storageUnavailable = true
    await page.onShow(); await choose(page)
    assert.equal(page.data.inboundStatus, 'not_posted'); assert.equal(page.data.inboundCanPost, mode === 'accepted')
    if (mode !== 'accepted') { await page.beginInbound(); assert.equal(page.data.inboundConfirming, false) }
    assert.equal(state.writes.length, 0)
  }
})

test('receipt switching and posted status never clear an unresolved original request', async () => {
  const { page, state } = harness(); await pending(state)
  state.inboundStates[f.id(22)] = inboundState(f.id(22), true)
  await page.onShow(); await choose(page, f.id(22)); assert.equal(page.data.inboundStatus, 'posted')
  const saved = [...state.saved.entries()]
  await choose(page); assert.equal(page.data.inboundPending, true); assert.equal(page.data.inboundCanPost, false)
  await page.refresh(); assert.equal(page.data.inboundPending, true); assert.deepEqual([...state.saved.entries()], saved)
  assert.equal(state.writes.length, 0)
})

test('failed or malformed status never becomes not-posted and clears the previous summary', async () => {
  for (const mode of ['network', 'receipt', 'version', 'missing_summary', 'unexpected']) {
    const { page, state } = acceptedHarness(); state.inboundStates[f.id(21)] = inboundState(f.id(21), true)
    await page.onShow(); await choose(page)
    if (mode === 'network') state.hook = endpoint => { if (endpoint.endsWith('/inbound')) throw new Error('offline') }
    if (mode === 'receipt') state.inboundStates[f.id(21)].receipt_id = f.id(22)
    if (mode === 'version') state.inboundStates[f.id(21)].authorization_version = 2
    if (mode === 'missing_summary') state.inboundStates[f.id(21)].inbound = null
    if (mode === 'unexpected') state.inboundStates[f.id(21)].extra = true
    await page.refreshInboundState(); assert.equal(page.data.inboundStatus, 'unavailable'); assert.equal(page.data.inboundCanPost, false)
    assert.equal(page.data.inboundStateView, null); await page.beginInbound(); assert.equal(state.writes.length, 0)
  }
})

test('a late status for an earlier receipt cannot overwrite the selected receipt', async () => {
  const { page, state } = harness(); await page.onShow(); let release
  state.inboundStates[f.id(22)] = inboundState(f.id(22), true)
  state.hook = endpoint => endpoint.endsWith(`${f.id(21)}/inbound`) ? new Promise(resolve => { release = resolve }) : undefined
  const first = choose(page); await tick(); assert.ok(release)
  await choose(page, f.id(22)); release(); await first
  assert.equal(page.data.selectedReceipt.id, f.id(22)); assert.equal(page.data.inboundStatus, 'posted')
})

test('hide, refresh, session and permission changes discard late inbound replies', async () => {
  for (const mode of ['hide', 'refresh', 'session', 'permission']) {
    const { page, state } = acceptedHarness(); await page.onShow(); let release
    state.hook = endpoint => endpoint.endsWith('/inbound') ? new Promise(resolve => { release = resolve }) : undefined
    const reading = choose(page); await tick(); assert.ok(release)
    if (mode === 'hide') page.onHide()
    if (mode === 'session') state.token = 'new-session'
    if (mode === 'permission') state.access.read = false
    if (mode === 'refresh') { state.hook = null; state.inboundStates[f.id(21)] = inboundState(f.id(21), true); await page.refresh() }
    release(); await reading
    if (mode === 'refresh') assert.equal(page.data.inboundStatus, 'posted')
    else { assert.equal(page.data.ready, false); assert.equal(page.data.inboundStateView, null); assert.equal(page.data.package, null) }
  }
})

test('a concurrent completed inbound is displayed without creating another request', async () => {
  const { page, state } = acceptedHarness(); await page.onShow(); await choose(page)
  state.inboundStates[f.id(21)] = inboundState(f.id(21), true)
  await page.beginInbound(); assert.equal(page.data.inboundStatus, 'posted'); assert.equal(page.data.inboundConfirming, false)
  assert.equal(state.writes.length, 0); assert.equal(state.saved.size, 0)
})

test('confirmed inbound refreshes selected status and duplicate clicks post once', async () => {
  const { page, state } = acceptedHarness(); await page.onShow(); await choose(page); await page.beginInbound()
  await Promise.all([confirm(page), confirm(page)])
  assert.equal(state.writes.length, 1); assert.equal(page.data.selectedReceipt.id, f.id(21))
  assert.equal(page.data.inboundStatus, 'posted'); assert.equal(page.data.inboundCanPost, false); assert.equal(state.saved.size, 0)
})

test('a changed confirmation plan or revoked write permission never persists or posts a command', async () => {
  for (const mode of ['plan', 'permission', 'preview_version']) {
    const { page, state } = acceptedHarness(); await page.onShow(); await choose(page); await page.beginInbound()
    if (mode === 'plan') state.inboundPreview.plan_hash = 'e'.repeat(64)
    else if (mode === 'preview_version') state.inboundPreview.authorization_version = 2
    else state.access.write = false
    await confirm(page); assert.equal(state.writes.length, 0); assert.equal(state.saved.size, 0); assert.equal(page.data.inboundCanPost, false)
  }
})

test('hiding during an inbound POST preserves its marker and discards the late page update', async () => {
  const { page, state } = acceptedHarness(); await page.onShow(); await choose(page); await page.beginInbound(); let release
  state.postHook = () => new Promise(resolve => { release = resolve })
  const submitting = confirm(page); await tick(); assert.ok(release); assert.equal(state.saved.size, 1)
  page.onHide(); release(); await submitting
  assert.equal(state.saved.size, 1); assert.equal(page.data.ready, false); assert.equal(page.data.package, null)
  state.postHook = null; await page.onShow(); await choose(page)
  assert.equal(page.data.inboundPending, true); assert.equal(page.data.inboundStatus, 'posted'); assert.equal(page.data.inboundCanPost, false)
  await page.recoverInbound(); assert.equal(state.saved.size, 0); assert.equal(state.writes.length, 1); assert.equal(page.data.inboundStatus, 'posted')
})

test('original recovery and seal races refresh exact posted or sealed state without another stock POST', async () => {
  for (const action of ['recoverInbound', 'sealInbound']) for (const posted of [false, true]) {
    const { page, state } = acceptedHarness(); const marker = await pending(state, f.id(21))
    state.inboundResult = posted ? postedResult(marker) : sealedResult(marker)
    if (posted) state.inboundStates[f.id(21)] = inboundState(f.id(21), true)
    await page.onShow(); await choose(page); await page[action]()
    assert.equal(state.saved.size, 0); assert.equal(page.data.inboundPending, false)
    assert.equal(page.data.inboundStatus, posted ? 'posted' : 'not_posted'); assert.equal(page.data.inboundCanPost, !posted)
    assert.equal(state.writes.length, 0); assert.equal(state.sealWrites.length, 0)
  }
})

test('fresh nonexecution seal uses the inbound HTTP contract then refreshes only after exact GET', async () => {
  const { page, state } = acceptedHarness(); await pending(state, f.id(21)); let lookups = 0
  state.hook = endpoint => {
    if (endpoint.includes('/by-request/') && ++lookups === 1) throw Object.assign(new Error('not observed'), {
      responseReceived: true, status: 404, code: 'stock_return_inbound_not_observed' })
  }
  await page.onShow(); await choose(page); await page.sealInbound()
  assert.equal(state.sealWrites.length, 1); assert.deepEqual(Object.keys(state.sealWrites[0].body), ['request_hash'])
  assert.equal(lookups, 2); assert.equal(state.saved.size, 0); assert.equal(page.data.inboundPending, false)
  assert.equal(page.data.inboundStatus, 'not_posted'); assert.equal(state.writes.length, 0)
})

test('unknown POST and failed original GET retain the recovery record across state refresh', async () => {
  for (const mode of ['post', 'readback']) {
    const { page, state } = acceptedHarness(); await page.onShow(); await choose(page); await page.beginInbound()
    if (mode === 'post') state.postHook = () => { throw new Error('transport unknown') }
    else state.hook = endpoint => { if (endpoint.includes('/by-request/')) throw new Error('original not verified') }
    await confirm(page); assert.equal(state.saved.size, 1); assert.equal(page.data.inboundPending, true); assert.equal(page.data.inboundCanPost, false)
    await page.refreshInboundState(); await page.beginInbound(); assert.equal(state.writes.length, 1); assert.equal(state.saved.size, 1)
  }
})
