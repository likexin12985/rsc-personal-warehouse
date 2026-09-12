const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const test = require('node:test')
const contract = require('../utils/my-receiving-contract')

const ID = '10000000-0000-4000-8000-000000000001'
const PERSON = '20000000-0000-4000-8000-000000000001'
const PACKAGE = '30000000-0000-4000-8000-000000000001'
const NEXT = '30000000-0000-4000-8000-000000000002'
const clone = (value) => JSON.parse(JSON.stringify(value))
function response(id = PACKAGE) {
  return { schema_version: '1.0', request_id: ID, request_no: 'REQ-TEST', request_version: 9, person_id: PERSON, next_after_id: null,
    packages: [{ shipment_id: id, shipment_no: 'SHP-TEST', shipment_status: 'shipped', carrier: '测试承运商', tracking_no: 'TEST-001', shipped_at: '2026-09-12T08:00:00+08:00', target_location_id: ID, target_location_name: '测试个人仓',
      lines: [{ shipment_line_id: id, request_line_id: ID, sku_code: 'SKU-001', material_name: '测试物料', base_unit: '个', shipped_qty: '12.345', accepted_qty: '2.000', rejected_qty: '0.345', unconfirmed_qty: '10.000', has_exception: true }] }] }
}
function deferred() { let resolve; const promise = new Promise((done) => { resolve = done }); return { promise, resolve } }
const tick = () => new Promise((resolve) => setImmediate(resolve))
function harness(options = {}) {
  const state = { user: { person_id: PERSON, authorization_version: 7 }, token: 'fixture-token', calls: [], stops: 0 }
  let definition, reads = 0
  const context = {
    Page(value) { definition = value },
    wx: { stopPullDownRefresh() { state.stops += 1 } },
    require(module) {
      if (module === '../../utils/api') return { async request(endpoint, options) { state.calls.push({ endpoint, ...clone(options) }); return optionsForResponse() } }
      if (module === '../../utils/session') return { getUser: () => state.user, getToken: () => state.token, ensureLogin: () => !!state.token }
      if (module === '../../utils/material-request-adapter') return { formalMaterialRequestAdapter: {
        async loadIdentityNoReplay() { state.calls.push({ identity: true }); return options.identity ? options.identity(state) : clone(state.user) },
        async loadAccessNoReplay() { state.calls.push({ access: true }); const access = { can_read: true, can_read_material_catalog: true }; return options.access ? options.access(++reads, access) : access }
      } }
      return contract
    }
  }
  function optionsForResponse() { return options.response ? options.response(state) : response() }
  vm.runInNewContext(fs.readFileSync(path.resolve(__dirname, '../pages/formal-my-receiving/index.js'), 'utf8'), context)
  const page = Object.assign({}, definition, { data: clone(definition.data), setData(update) { Object.assign(this.data, clone(update)) } })
  page.onLoad({ request_id: ID })
  return { page, state }
}

test('receiving contract preserves exact quantities and rejects source-account fields', () => {
  const r = response()
  r.packages[0].lines[0] = Object.assign(r.packages[0].lines[0], { shipped_qty: '900719925474099.999', accepted_qty: '900719925474099.998', rejected_qty: '0.000', unconfirmed_qty: '0.001' })
  assert.equal(contract.validateMyReceiving(r, ID, PERSON).packages[0].lines[0].unconfirmed_qty, '0.001')
  r.packages[0].source_account_id = ID
  assert.throws(() => contract.validateMyReceiving(r, ID, PERSON))
})

test('receiving contract rejects crossed identity, versions, quantities, cursors and duplicate lines', () => {
  const changes = [
    r => { r.request_id = PERSON }, r => { r.person_id = ID }, r => { r.request_version = 0 },
    r => { r.packages[0].lines[0].accepted_qty = '99.000' },
    r => { r.packages[0].lines[0].shipped_qty = 12.345 },
    r => { r.packages[0].lines[0].has_exception = false },
    r => { r.packages[0].shipment_status = 'inbound_posted' },
    r => { r.packages[0].shipped_at = '2026-09-12T08:00:00' },
    r => { r.packages[0].lines.push(clone(r.packages[0].lines[0])) },
    r => { r.packages.push(clone(r.packages[0])) },
    r => { r.next_after_id = NEXT },
    r => { r.packages = []; r.next_after_id = PACKAGE }
  ]
  for (const change of changes) { const r = response(); change(r); assert.throws(() => contract.validateMyReceiving(r, ID, PERSON)) }
  assert.throws(() => contract.validateMyReceiving(response(), ID, PERSON, NEXT))
})

test('page only reads recipient endpoint, with current identity on both sides and no automatic refresh', async () => {
  const { page, state } = harness()
  await page.onShow()
  assert.equal(page.data.state, 'ready')
  assert.equal(page.data.packages[0].lines[0].accepted_qty, '2.000')
  assert.equal(page.data.packages[0].statusLabel, '已发运')
  assert.deepEqual(state.calls[2], { endpoint: `/v1/material-requests/${ID}/my-receiving?limit=5`, method: 'GET', noRefresh: true, header: { 'Cache-Control': 'no-store', Pragma: 'no-cache' } })
  assert.equal(state.calls.length, 5)
  assert.equal(JSON.stringify(page.data).includes(state.token), false)
})

test('permission failure and malformed request links never request a package', async () => {
  for (const flag of ['can_read', 'can_read_material_catalog']) {
    const { page, state } = harness({ access: (_, access) => Object.assign(access, { [flag]: false }) })
    await page.onShow()
    assert.equal(page.data.state, 'error')
    assert.equal(state.calls.length, 2)
  }
  const { page, state } = harness()
  page.onLoad({ request_id: '../another-user' })
  await page.onShow()
  assert.equal(state.calls.length, 0)
})

test('revocation on the final context read hides package and quantity data', async () => {
  const { page } = harness({ access: (n, value) => n === 2 ? Object.assign(value, { can_read: false }) : value })
  await page.onShow()
  assert.equal(page.data.state, 'error')
  assert.equal(page.data.packages.length, 0)
})

test('late identity or package callbacks cannot repopulate a hidden page', async () => {
  for (const phase of ['identity', 'response']) {
    const pending = deferred()
    const { page, state } = harness({ [phase]: () => pending.promise })
    const loading = page.onShow()
    await tick()
    page.onHide()
    const count = state.calls.length
    pending.resolve(phase === 'identity' ? clone(state.user) : response())
    await loading
    assert.equal(page.data.state, 'idle')
    assert.equal(page.data.packages.length, 0)
    assert.equal(state.calls.length, count)
  }
})

test('account switch during a package request discards the response', async () => {
  const pending = deferred()
  const { page, state } = harness({ response: () => pending.promise })
  const loading = page.onShow()
  await tick()
  state.user = { person_id: ID, authorization_version: 7 }
  pending.resolve(response())
  await loading
  assert.equal(page.data.state, 'error')
  assert.equal(page.data.packages.length, 0)
})

test('pagination uses exact recipient cursor and clears earlier-page quantities while loading', async () => {
  let reads = 0
  const { page, state } = harness({ response: () => ++reads === 1 ? Object.assign(response(), { next_after_id: PACKAGE }) : response(NEXT) })
  await page.onShow()
  assert.equal(page.data.hasNext, true)
  const next = page.nextPage()
  assert.equal(page.data.packages.length, 0)
  await next
  assert.equal(page.data.pageNumber, 2)
  assert.equal(page.data.packages[0].shipment_id, NEXT)
  assert.equal(state.calls[7].endpoint, `/v1/material-requests/${ID}/my-receiving?limit=5&after_id=${PACKAGE}`)
})

test('version changes between pages require a fresh first-page read', async () => {
  let reads = 0
  const { page } = harness({ response: () => ++reads === 1 ? Object.assign(response(), { next_after_id: PACKAGE }) : Object.assign(response(NEXT), { request_version: 10 }) })
  await page.onShow()
  await page.nextPage()
  assert.equal(page.data.state, 'error')
  assert.equal(page.data.packages.length, 0)
  assert.deepEqual(clone(page._cursors), [null])
})

test('a newer refresh cannot be overwritten by the old package response', async () => {
  const pending = deferred()
  let reads = 0
  const { page } = harness({ response: () => ++reads === 1 ? pending.promise : response(NEXT) })
  const original = page.onShow()
  await tick()
  await page.refresh()
  pending.resolve(response())
  await original
  assert.equal(page.data.packages[0].shipment_id, NEXT)
  page.onUnload()
  assert.equal(page.data.packages.length, 0)
  assert.equal(page.data.requestNo, '')
})

test('a failed pull-down refresh cannot leave previous package balances visible', async () => {
  let fail = false
  const { page, state } = harness({ response: () => { if (fail) throw new Error('network'); return response() } })
  await page.onShow()
  fail = true
  await page.onPullDownRefresh()
  assert.equal(page.data.state, 'error')
  assert.equal(page.data.packages.length, 0)
  assert.equal(state.stops, 1)
})
