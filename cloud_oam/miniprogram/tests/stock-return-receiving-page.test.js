const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const f = require('./fixtures/stock-return-receiving-data.cjs')
const clone = value => JSON.parse(JSON.stringify(value))
const tick = () => new Promise(resolve => setImmediate(resolve))
function harness(detail = true) {
  const state = { token: 'test-session', user: { person_id: f.id(1), authorization_version: 1, role_codes: ['provincial_manager'] },
    access: { read: true }, calls: [], history: f.history(true, [f.receipt(true, 'shortage'), f.receipt(true, 'damaged', 2)]),
    directory: f.directory(), urls: [] }
  let definition
  vm.runInNewContext(fs.readFileSync(path.resolve(__dirname, '../pages/formal-stock-return-receiving/index.js'), 'utf8'), {
    Page(value) { definition = value }, wx: { stopPullDownRefresh() {}, navigateTo({ url }) { state.urls.push(url) } },
    require(name) {
      if (name === '../../utils/api') return { async request(endpoint, options) {
        state.calls.push({ endpoint, ...clone(options) }); if (state.hook) await state.hook(endpoint)
        if (endpoint === '/auth/me') return clone(state.user)
        if (endpoint === '/access/context') return clone(state.access)
        if (endpoint.endsWith('/inbound/preview')) return clone(state.inboundPreview)
        if (endpoint.endsWith('/inbound')) { state.inboundPosted = true; return clone(state.inboundResult) }
        if (endpoint.includes('/inbound/by-request/')) return clone(state.inboundResult)
        return clone(endpoint.endsWith('/receipts') ? state.history : state.directory)
      } }
      if (name === '../../utils/session') return { ensureLogin: () => !!state.token, getToken: () => state.token, getUser: () => state.user }
      if (name === '../../utils/production-guard') return { inventoryAccessDecision: () => ({ allowed: true }), hasFormalPermission: access => access.read }
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
test('regional recipient reads exact partial history and selected abnormal receipt without any write', async () => {
  const { page, state } = harness(); await page.onShow()
  assert.equal(page.data.ready, true); assert.equal(page.data.progress[0].remaining, '0.000')
  assert.equal(page.data.progress[0].accepted, '1.000'); assert.equal(page.data.progress[0].damaged, '1.000')
  assert.equal(page.data.batches.length, 2); page.selectReceipt({ currentTarget: { dataset: { id: f.id(21) } } })
  assert.equal(page.data.selectedReceipt.lines[0].shortage, '1.000')
  page.selectReceipt({ currentTarget: { dataset: { id: f.id(22) } } })
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
  await page.onShow(); page.selectReceipt({ currentTarget: { dataset: { id: f.id(21) } } }); await page.beginInbound()
  assert.equal(page.data.inboundConfirming, true); assert.equal(page.data.inboundReview.rows[0].quantity, '1.000')
  await page.confirmInbound({ currentTarget: { dataset: { confirm: 'false' } } }); assert.equal(state.inboundPosted, undefined)
})
