const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const test = require('node:test')
const contract = require('../utils/my-receipt-candidates-contract')
const receiving = require('../utils/my-receiving-contract')

const REQUEST = '10000000-0000-4000-8000-000000000001'
const PERSON = '20000000-0000-4000-8000-000000000001'
const SHIPMENT = '30000000-0000-4000-8000-000000000001'
const LINE = '40000000-0000-4000-8000-000000000001'
const SERIAL = '50000000-0000-4000-8000-000000000001'
const clone = value => JSON.parse(JSON.stringify(value))
function response() {
  return { schema_version: '1.0', request_id: REQUEST, request_no: 'REQ-TEST', request_version: 10, person_id: PERSON,
    shipment_id: SHIPMENT, shipment_no: 'SHP-TEST', shipped_at: '2026-09-10T00:00:00Z', target_location_name: '测试个人仓', checked_at: '2026-09-12T08:00:00Z',
    can_receive: true, blocked_reason: null, lines: [{ shipment_line_id: LINE, request_line_id: LINE, sku_code: 'SKU-TEST', material_name: '测试物料', base_unit: '个',
      shipped_qty: '2.000', accepted_qty: '1.000', rejected_qty: '0.000', unconfirmed_qty: '1.000', has_exception: false,
      lot_no: null, tracking_mode: 'serial', quantity_scale: 0, allow_fraction: false, remaining_serials: [{ serial_id: SERIAL, serial_no: 'SN-TEST', qr_code: 'QR-TEST' }] }] }
}
function validate(r = response()) { return contract.validateCandidates(r, REQUEST, SHIPMENT, PERSON) }
function deferred() { let resolve; const promise = new Promise(done => { resolve = done }); return { resolve, promise } }
const tick = () => new Promise(done => setImmediate(done))

const { harness } = require('./fixtures/my-receipt')

test('candidate contract preserves exact decimal quantities and only remaining serials', () => {
  assert.equal(validate().lines[0].remaining_serials[0].serial_no, 'SN-TEST')
  const r = response(), l = r.lines[0]
  Object.assign(l, { tracking_mode: 'none', allow_fraction: true, quantity_scale: 3, shipped_qty: '900719925474099.999', accepted_qty: '900719925474099.998', unconfirmed_qty: '0.001', remaining_serials: [] })
  assert.equal(validate(r).lines[0].unconfirmed_qty, '0.001')
  l.tracking_mode = 'lot'; l.lot_no = 'L'.repeat(160)
  assert.equal(validate(r).lines[0].lot_no.length, 160)
})

test('candidate contract rejects context mismatch, extra fields, quantities and serial drift', () => {
  for (const change of [
    r => { r.shipment_id = REQUEST }, r => { r.person_id = REQUEST }, r => { r.request_id = PERSON },
    r => { r.request_version = 0 }, r => { r.source_account_id = PERSON },
    r => { r.checked_at = '2026-09-12' }, r => { r.can_receive = false }, r => { r.blocked_reason = 'posted' },
    r => { r.lines[0].unconfirmed_qty = '2.000' }, r => { r.lines[0].shipped_qty = 2 },
    r => { r.lines[0].quantity_scale = 4 }, r => { r.lines[0].remaining_serials = [] },
    r => { r.lines[0].tracking_mode = 'lot_and_serial' },
    r => { r.lines[0].tracking_mode = 'none' }, r => { r.lines.push(clone(r.lines[0])) },
    r => { r.lines[0].remaining_serials[0].qr_code = '' }, r => { r.lines[0].remaining_serials[0].serial_id = 'invalid' }
  ]) { const r = response(); change(r); assert.throws(() => validate(r)) }
})

test('scanner only accepts exact package QR or uniquely matching SN, not a substring or URL', () => {
  const c = validate()
  for (const code of ['QR-TEST', 'SN-TEST']) assert.equal(contract.matchCandidateScan(c, code).serialId, SERIAL)
  for (const code of ['TEST', ' QR-TEST', 'https://example.test/QR-TEST', 'SN-OTHER']) assert.throws(() => contract.matchCandidateScan(c, code))
  const other = clone(c.lines[0]); other.shipment_line_id = PERSON
  other.remaining_serials[0].serial_id = PERSON; other.remaining_serials[0].qr_code = 'QR-OTHER'
  c.lines.push(other)
  assert.throws(() => contract.matchCandidateScan(c, 'SN-TEST'), /多条明细/)
  assert.equal(contract.matchCandidateScan(c, 'QR-OTHER').serialId, PERSON)
})

test('page revalidates both sides of read and scanner never writes or increases accepted quantities', async () => {
  const { page, state } = harness()
  await page.onShow()
  assert.equal(page.data.state, 'ready')
  assert.equal(state.calls.length, 5)
  assert.deepEqual(state.calls[2], { endpoint: `/v1/material-requests/${REQUEST}/my-receiving/${SHIPMENT}/candidates`, method: 'GET', noRefresh: true, header: { 'Cache-Control': 'no-store', Pragma: 'no-cache' } })
  await page.scan(); await page.scan()
  assert.equal(page.data.matchedCount, 1)
  assert.equal(page.data.lines[0].shownSerials[0].checked, true)
  assert.equal(page.data.lines[0].accepted_qty, '1.000')
  assert.match(page.data.message, /已核对/)
  assert.equal(state.calls.filter(c => c.endpoint).length, 1)
  assert.equal(JSON.stringify(page.data).includes('fixture-token'), false)
  assert.equal(JSON.stringify(page.data).includes('QR-TEST'), false)
})

test('unknown barcode and cancelled scans add no matches', async () => {
  for (const result of ['unknown', null]) {
    const { page } = harness({ scan(callbacks) { result ? callbacks.success({ result }) : callbacks.fail() } })
    await page.onShow(); await page.scan()
    assert.equal(page.data.matchedCount, 0)
    assert.equal(page.data.state, 'ready')
  }
})

test('read permission denial or malformed coordinates sends no package request', async () => {
  const { page, state } = harness({ access: (_, a) => Object.assign(a, { can_read: false }) })
  await page.onShow()
  assert.equal(page.data.state, 'error'); assert.equal(state.calls.length, 2)
  const broken = harness(); broken.page.onLoad({ request_id: REQUEST, shipment_id: '../x' })
  await broken.page.onShow(); assert.equal(broken.state.calls.length, 0)
})

test('permission revocation during scan clears all candidate data', async () => {
  const { page, state } = harness({ access: (n, a) => n === 4 ? Object.assign(a, { can_read: false }) : a })
  await page.onShow(); await page.scan()
  assert.equal(state.scans, 1)
  assert.equal(page.data.state, 'error')
  assert.equal(page.data.lines.length, 0); assert.equal(page._candidate, null)
})

test('account switch during scan drops all candidate data and never matches', async () => {
  const { page } = harness({ scan(callbacks, state) { state.user = { person_id: REQUEST, authorization_version: 7 }; callbacks.success({ result: 'QR-TEST' }) } })
  await page.onShow(); await page.scan()
  assert.equal(page.data.state, 'error'); assert.equal(page.data.lines.length, 0); assert.equal(page.data.matchedCount, 0)
})

test('hidden page discards late identity, candidate and scanner callbacks', async () => {
  for (const phase of ['identity', 'response', 'scan']) {
    const pending = deferred()
    const { page, state } = harness(phase === 'scan' ? { scan() {} } : { [phase]: () => pending.promise })
    let running
    if (phase === 'scan') { await page.onShow(); running = page.scan(); await tick() } else { running = page.onShow(); await tick() }
    page.onHide(); const count = state.calls.length
    if (phase === 'scan') state.scanCallbacks.success({ result: 'QR-TEST' })
    else pending.resolve(phase === 'identity' ? clone(state.user) : response())
    await running
    assert.equal(page.data.state, 'idle'); assert.equal(page.data.lines.length, 0); assert.equal(page._candidate, null)
    assert.equal(state.calls.length, count)
  }
})

test('refresh invalidates an older scan and its matches', async () => {
  const { page, state } = harness({ scan() {} })
  await page.onShow(); const scanning = page.scan(); await tick()
  await page.refresh(); state.scanCallbacks.success({ result: 'QR-TEST' }); await scanning
  assert.equal(page.data.state, 'ready'); assert.equal(page.data.matchedCount, 0)
})

test('serial display is bounded and more keeps only the selected line', async () => {
  const { page } = harness({ response() {
    const r = response(), l = r.lines[0]
    Object.assign(l, { shipped_qty: '26.000', accepted_qty: '1.000', unconfirmed_qty: '25.000', remaining_serials: Array.from({ length: 25 }, (_, i) => ({ serial_id: `50000000-0000-4000-8000-${String(i + 1).padStart(12, '0')}`, serial_no: `SN-${i}`, qr_code: `QR-${i}` })) })
    return r
  } })
  await page.onShow()
  assert.equal(page.data.lines[0].shownSerials.length, 20); assert.equal(page.data.lines[0].hasMore, true)
  page.showMore({ currentTarget: { dataset: { lineId: LINE } } })
  assert.equal(page.data.lines[0].shownSerials.length, 25); assert.equal(page.data.lines[0].hasMore, false)
  page.onUnload(); assert.equal(page.data.lines.length, 0)
})

test('page is registered and template cannot represent scanning as submitted acceptance', () => {
  const app = JSON.parse(fs.readFileSync(path.resolve(__dirname, '../app.json'), 'utf8'))
  assert.ok(app.pages.includes('pages/formal-my-receipt/index'))
  const template = fs.readFileSync(path.resolve(__dirname, '../pages/formal-my-receipt/index.wxml'), 'utf8')
  assert.match(template, /本次已核对/); assert.doesNotMatch(template, /确认入账|验收成功|确认收货/)
})
