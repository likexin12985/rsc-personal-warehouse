const test = require('node:test')
const assert = require('node:assert/strict')
const c = require('../utils/stock-return-shipment-contract')
const { submitShipment } = require('../utils/stock-return-shipment-submit')
const { createStore, validateMarker } = require('../utils/work-order-recovery-store')
const { recoverPending, sealPending } = require('../utils/work-order-recovery')
const f = require('./fixtures/stock-return-shipment-data.cjs')
const out = require('./fixtures/stock-return-outbound-data.cjs')
const clone = value => JSON.parse(JSON.stringify(value))
const missing = () => Object.assign(new Error('not observed'), { status: 404, code: 'stock_return_not_observed', responseReceived: true })
function fixture(tracked = false) {
  const value = f.input(tracked), marker = f.marker(value), records = new Map(), calls = []
  const storage = { getStorageInfoSync: () => ({ keys: [...records.keys()] }), getStorageSync: key => records.get(key) || '',
    setStorageSync: (key, raw) => records.set(key, raw), removeStorageSync: key => records.delete(key) }
  const make = () => createStore({ storage, state: { active: new Set(), faults: new Set() } })
  let accepted = false, sealed = false, confirmed = false
  const api = { createRequestId: () => marker.trace_request_id, createIdempotencyKey: () => 'wxidem-' + 'c'.repeat(36),
    request: async (path, options) => {
      calls.push({ path, ...options }); assert.equal(options.noRefresh, true); assert.equal(options.header['Cache-Control'], 'no-store')
      if (path.includes('/by-request/')) { assert.ok(path.includes('/shipments/by-request/')); if (sealed) return f.sealed(marker); if (!accepted) throw missing(); return f.result(value, marker) }
      if (path.endsWith('/options')) return f.options(value)
      if (path.endsWith('/preview')) return f.preview(value)
      if (path.endsWith('/shipments')) return f.history(value)
      throw new Error('unexpected route')
    },
    postNoReplay: async (path, body, coordinates) => {
      assert.ok(confirmed); assert.equal(records.size, 1); assert.ok(path.endsWith('/shipments'))
      assert.deepEqual(coordinates, { requestId: marker.trace_request_id, idempotencyKey: body.idempotency_key })
      assert.deepEqual(body, { ...c.payload(value), expected_plan_hash: marker.plan_hash, request_id: marker.trace_request_id, idempotency_key: 'wxidem-' + 'c'.repeat(36) })
      accepted = true; calls.push({ path, body }); return { ignored: true }
    },
    postSealNoReplay: async (path, body, coordinates) => { assert.ok(path.endsWith('/shipments/by-request/' + marker.trace_request_id + '/seal'))
      assert.deepEqual(body, { operator_person_id: value.personId, request_hash: marker.request_hash }); assert.equal(coordinates.requestId, marker.trace_request_id); sealed = true }
  }
  const args = { ...value, api, store: make(), drafts: f.drafts(value), authorize: async () => 'current',
    confirm: async review => { assert.equal(records.size, 0); assert.ok(Object.isFrozen(review.rows)); assert.equal(review.shippedAt, value.shippedAt)
      assert.equal(review.carrier, value.carrier); assert.equal(review.trackingNo, value.trackingNo); assert.equal(review.rows[0].quantity, value.lines[0].quantity); confirmed = true; return true } }
  return { value, marker, records, storage, make, api, args, calls, run: () => submitShipment(args) }
}
for (const tracked of [false, true]) {
  test(`parcel ${tracked ? 'SN' : 'quantity'} choices and preview bind the exact departure`, () => {
    const value = f.input(tracked), history = c.validateHistory(f.history(value), value), choices = c.validateOptions(f.options(value), value, history)
    assert.deepEqual(c.buildLines(choices, f.drafts(value)), value.lines)
    assert.equal(c.validatePreview(f.preview(value), value, choices).request_hash, c.requestHash(value))
    assert.equal(c.validateHistory(f.history(value, [f.result(value)]), value).shipment_status, 'shipped')
    for (const change of [x => { x.operation_id = f.id(99) }, x => { x.person_id = f.id(99) }, x => { x.authorization_version++ },
      x => { x.lines[0].unshipped_quantity = '2.000' }, x => { x.lines[0].unassigned_quantity = '0.000' }, x => { x.lines[0].material_id = f.id(99) },
      x => { x.lines[0].outbound_id = f.id(99) }, x => { x.lines[0].operation_line_id = f.id(99) }, x => { x.lines[0].tracking_mode = 'lot' },
      x => { x.lines[0].outbound_quantity = '2.000' }, x => { x.lines[0].quantity_scale = 4 }, x => { x.lines[0].outbound_at = '2026-09-13T00:00:00Z' },
      x => { x.destination.transit_location_id = f.id(99) }, x => { x.lines[0].qr_code = 'leaked' }, x => { x.lines.push(clone(x.lines[0])) }]) {
      const raw = f.options(value); change(raw); assert.throws(() => c.validateOptions(raw, value, history))
    }
    for (const change of [x => { x.reason += 'tampered' }, x => { x.carrier += 'changed' }, x => { x.tracking_no += 'changed' },
      x => { x.shipped_at = '2026-09-13T01:02:00.123455Z' }, x => { x.lines[0].selected_quantity = '0.000' }, x => { x.plan_hash = '' },
      x => { x.lines[0].transit_stock_account_id = f.id(99) }, x => { x.destination.target_location_id = f.id(99) }, x => { x.lines = [] }]) {
      const raw = f.preview(value); change(raw); assert.throws(() => c.validatePreview(raw, value, choices))
    }
  })
  test(`parcel ${tracked ? 'SN' : 'quantity'} sends once after full confirmation and durable marker`, async () => {
    const x = fixture(tracked); assert.equal((await x.run()).status, 'confirmed')
    assert.equal(x.records.size, 0); assert.equal(x.calls.filter(row => row.body).length, 1)
    assert.ok(x.calls.at(-1).path.endsWith('/shipments/by-request/' + x.marker.trace_request_id))
  })
}
test('carrier text, microseconds, timezone and quantity normalization match the parcel intent', () => {
  const value = f.input(true)
  assert.equal(c.requestHash(value), c.requestHash({ ...value, shippedAt: '2026-09-13T09:02:00.123456+08:00', carrier: ' ' + value.carrier + ' ' }))
  assert.notEqual(c.requestHash(value), c.requestHash({ ...value, shippedAt: '2026-09-13T01:02:00.123455Z' }))
  for (const invalid of ['', ' ', 'bad\tcarrier', 'bad\x7ftracking', 'x'.repeat(101), '\ud800']) {
    assert.throws(() => c.command({ ...value, carrier: invalid })); assert.throws(() => c.command({ ...value, trackingNo: invalid }))
  }
  const before = { ...value, shippedAt: '2026-09-13T00:00:00Z' }
  assert.throws(() => c.validatePreview(f.preview(before), before, f.options(value)))
})
test('partial parcels retain independent stock and per-departure budgets', () => {
  const value = f.input(), firstInput = { ...value, lines: [{ ...value.lines[0], quantity: '0.375' }] }, first = f.result(firstInput)
  const partial = f.history(value, [first]); assert.equal(c.validateHistory(partial, value).shipment_status, 'partially_shipped')
  const choices = c.validateOptions(f.options(value, [first]), value, partial)
  assert.equal(choices.lines[0].selectable_quantity, '0.625'); assert.equal(choices.lines[0].in_transit_quantity, '1.000')
  const secondInput = { ...value, trackingNo: 'SECOND', lines: [{ ...value.lines[0], quantity: '0.625' }] }, second = f.result(secondInput)
  second.shipment_id = f.id(44); second.request_id = 'wxreq-' + 'f'.repeat(36)
  Object.assign(second.lines[0], { shipped_quantity: '0.375', unshipped_quantity: '0.625', unassigned_quantity: '0.625' })
  const final = f.history(value, [first, second]); assert.equal(c.validateHistory(final, value).shipment_status, 'shipped')
  assert.equal(final.departures.original.status, 'submitted'); assert.ok(!JSON.stringify(final.items).includes('posting_transaction_id'))
  for (const change of [x => { x.shipment_status = 'received' }, x => { x.items[1].lines[0].shipped_quantity = '0.000' },
    x => { x.items[1].shipment_id = first.shipment_id }, x => { x.items[1].lines[0].outbound_line_id = f.id(99) },
    x => { x.items[1].lines[0].transit_stock_account_id = f.id(99) }, x => { x.items[1].carrier += 'changed' }]) {
    const raw = clone(final); change(raw); assert.throws(() => c.validateHistory(raw, value))
  }
})
test('shipping all of one partial departure does not complete the original return', () => {
  const value = f.input(), part = { ...value, lines: [{ ...value.lines[0], quantity: '0.375' }] }
  const sourceInput = out.input(); sourceInput.lines[0].quantity = '0.375'
  const source = out.history(sourceInput, [out.result(sourceInput)]), shipment = f.result(part)
  Object.assign(shipment.lines[0], { outbound_quantity: '0.375', unshipped_quantity: '0.375', in_transit_quantity: '0.375', unassigned_quantity: '0.375' })
  const history = f.history(value, [shipment], source)
  assert.equal(c.validateHistory(history, value).shipment_status, 'partially_shipped')
  history.shipment_status = 'shipped'; assert.throws(() => c.validateHistory(history, value))
})
test('empty and cancelled departure histories do not manufacture choices', () => {
  const value = f.input(), empty = f.history(value, [], f.departures(value, []))
  assert.equal(c.validateOptions(f.options(value, [], empty.departures), value, empty).lines.length, 0)
  const cancelled = f.history(value, [], f.departures(value, [], true))
  assert.equal(c.validateHistory(cancelled, value).shipment_status, 'not_shipped')
  assert.throws(() => c.validateOptions(f.options(value), value, cancelled))
})
test('SN membership, repeats, precision and pooled account quantity are checked for the whole parcel', () => {
  const value = f.input(true), choices = c.validateOptions(f.options(value), value, f.history(value))
  for (const ids of [[], [f.id(99)], value.lines[0].serial_ids.concat(value.lines[0].serial_ids)])
    assert.throws(() => c.buildLines(choices, { [f.id(40)]: { quantity: '1', serial_ids: ids } }))
  assert.throws(() => c.command({ ...value, lines: value.lines.concat({ ...value.lines[0], outbound_line_id: f.id(99) }) }))
  const plain = f.input(), options = f.options(plain); options.lines[0].quantity_scale = 2
  assert.throws(() => c.buildLines(options, { [f.id(40)]: { quantity: '.375', serial_ids: [] } }))
  options.lines[0].quantity_scale = 3; options.lines.push({ ...options.lines[0], outbound_line_id: f.id(90), outbound_id: f.id(91) })
  assert.throws(() => c.buildLines(options, { [f.id(40)]: { quantity: '.750', serial_ids: [] }, [f.id(90)]: { quantity: '.750', serial_ids: [] } }))
})
for (const lost of ['before', 'after']) test(`lost ${lost} parcel response recovers or seals without replay after restart`, async () => {
  const x = fixture(true), write = x.api.postNoReplay; let posts = 0
  x.api.postNoReplay = async (...args) => { posts++; if (lost === 'after') await write(...args); throw new Error('lost') }
  assert.equal((await x.run()).status, 'pending'); assert.equal(posts, 1)
  for (const field of ['carrier', 'tracking_no', 'shipped_at', 'serial_ids', 'idempotency_key', 'reason', 'lines']) assert.ok(![...x.records.values()].join('').includes(field))
  await assert.rejects(x.run()); assert.equal(posts, 1)
  const result = await recoverPending({ ...x.args, store: x.make() })
  assert.equal(result.status, lost === 'after' ? 'confirmed' : 'pending')
  if (lost === 'before') assert.equal((await sealPending({ ...x.args, store: x.make(), confirm: async () => true })).status, 'sealed')
  assert.equal(posts, 1); assert.equal(x.records.size, 0)
})
test('failed storage, declined confirmation and authority drift issue no parcel POST', async () => {
  const cancelled = fixture(); cancelled.args.confirm = async () => false
  assert.equal((await cancelled.run()).status, 'cancelled'); assert.equal(cancelled.records.size, 0)
  const changed = fixture(); let access = 'old'; changed.args.authorize = async () => access
  changed.args.confirm = async () => { access = 'new'; return true }; await assert.rejects(changed.run()); assert.equal(changed.records.size, 0)
  const storage = fixture(); storage.storage.setStorageSync = () => { throw new Error('full') }
  await assert.rejects(storage.run()); assert.equal(storage.calls.filter(row => row.body).length, 0)
})
test('mismatched or unproven recovery retains the exact original parcel marker', async () => {
  for (const kind of ['partial', 'foreign', 'plan', 'operation', 'sealed-wrong-action', 'untrusted-404', 'changed-carrier']) {
    const x = fixture(); await x.args.store.withLease({ work_order_id: x.value.workOrderId }, lease => lease.persist(x.marker))
    x.api.request = async () => {
      if (kind === 'untrusted-404') throw { status: 404, code: 'stock_return_not_observed' }
      if (kind === 'sealed-wrong-action') { const raw = f.sealed(x.marker); raw.seal.operation_type = 'outbound_return'; return raw }
      const raw = f.result(x.value)
      if (kind === 'partial') raw.lines = []
      if (kind === 'foreign') raw.operator_person_id = f.id(99)
      if (kind === 'plan') raw.plan_hash = 'f'.repeat(64)
      if (kind === 'operation') raw.operation_id = f.id(99)
      if (kind === 'changed-carrier') raw.carrier += 'tampered'
      return raw
    }
    await assert.rejects(recoverPending(x.args)); assert.equal(x.records.size, 1)
  }
  for (const changes of [{ plan_hash: null }, { operation_id: null }, { operation_type: 'ship' }]) assert.throws(() => validateMarker({ ...f.marker(), ...changes }))
})
