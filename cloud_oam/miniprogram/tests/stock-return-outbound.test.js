const test = require('node:test')
const assert = require('node:assert/strict')
const c = require('../utils/stock-return-outbound-contract')
const { submitDeparture } = require('../utils/stock-return-outbound-submit')
const { createStore, validateMarker } = require('../utils/work-order-recovery-store')
const { recoverPending, sealPending } = require('../utils/work-order-recovery')
const f = require('./fixtures/stock-return-outbound-data.cjs')
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
      calls.push({ path, ...options }); assert.equal(options.noRefresh, true)
      assert.equal(options.header['Cache-Control'], 'no-store')
      if (path.includes('/by-request/')) { assert.ok(path.includes('/outbounds/by-request/')); if (sealed) return f.sealed(marker); if (!accepted) throw missing(); return f.result(value, marker) }
      if (path.endsWith('/options')) return f.options(value)
      if (path.endsWith('/preview')) return f.preview(value)
      if (path.endsWith('/outbounds')) return f.history(value)
      throw new Error('unexpected route')
    },
    postNoReplay: async (path, body, coordinates) => {
      assert.ok(confirmed); assert.equal(records.size, 1); assert.ok(path.endsWith('/outbounds'))
      assert.deepEqual(coordinates, { requestId: marker.trace_request_id, idempotencyKey: body.idempotency_key })
      assert.deepEqual(body, { ...c.payload(value), expected_plan_hash: marker.plan_hash, request_id: marker.trace_request_id, idempotency_key: 'wxidem-' + 'c'.repeat(36) })
      accepted = true; calls.push({ path, body }); return { accepted: 'this is not a durable recovery proof' }
    },
    postSealNoReplay: async (path, body, coordinates) => { assert.ok(path.endsWith('/outbounds/by-request/' + marker.trace_request_id + '/seal'))
      assert.deepEqual(body, { operator_person_id: value.personId, request_hash: marker.request_hash }); assert.equal(coordinates.requestId, marker.trace_request_id); sealed = true }
  }
  const args = { ...value, api, store: make(), drafts: f.drafts(value), authorize: async () => 'current',
    confirm: async review => { assert.equal(records.size, 0); assert.ok(Object.isFrozen(review.rows)); assert.equal(review.outboundAt, value.outboundAt)
      assert.equal(review.rows[0].quantity, value.lines[0].quantity); confirmed = true; return true } }
  return { value, marker, records, storage, make, api, args, calls, run: () => submitDeparture(args) }
}
for (const tracked of [false, true]) {
  test(`departure ${tracked ? 'SN' : 'quantity'} catalog and preview bind original committed return`, () => {
    const value = f.input(tracked), history = c.validateHistory(f.history(value), value), choices = c.validateOptions(f.options(value), value, history)
    assert.deepEqual(c.buildLines(choices, f.drafts(value)), value.lines)
    assert.equal(c.validatePreview(f.preview(value), value, choices).request_hash, c.requestHash(value))
    assert.equal(c.validateHistory(f.history(value, [f.result(value)]), value).outbound_status, 'outbound')
    for (const change of [x => { x.operation_id = f.id(99) }, x => { x.person_id = f.id(99) }, x => { x.authorization_version++ },
      x => { x.lines[0].remaining_quantity = '2.000' }, x => { x.lines[0].held_quantity = '0.000' }, x => { x.lines[0].material_id = f.id(99) },
      x => { x.lines[0].source_recovery_line_id = f.id(99) }, x => { x.lines[0].tracking_mode = 'lot' }, x => { x.lines[0].quantity_scale = 4 },
      x => { x.destination.transit_location_id = f.id(99) }, x => { x.lines[0].qr_code = 'leaked' }, x => { x.lines.push(clone(x.lines[0])) }]) {
      const raw = f.options(value); change(raw); assert.throws(() => c.validateOptions(raw, value, history))
    }
    for (const change of [x => { x.reason += 'tampered' }, x => { x.outbound_at = '2026-09-13T01:00:00.123455Z' },
      x => { x.lines[0].selected_quantity = '0.000' }, x => { x.plan_hash = '' }, x => { x.lines[0].source_stock_account_id = f.id(99) },
      x => { x.destination.target_location_id = f.id(99) }, x => { x.lines = [] }]) {
      const raw = f.preview(value); change(raw); assert.throws(() => c.validatePreview(raw, value, choices))
    }
  })
  test(`departure ${tracked ? 'SN' : 'quantity'} writes once after confirmation and persistent recovery marker`, async () => {
    const x = fixture(tracked); assert.equal((await x.run()).status, 'confirmed')
    assert.equal(x.records.size, 0); assert.equal(x.calls.filter(row => row.body).length, 1)
    assert.ok(x.calls.at(-1).path.endsWith('/outbounds/by-request/' + x.marker.trace_request_id))
  })
}
test('microseconds and timezone are normalized exactly for the Python command digest', () => {
  assert.equal(c.instant('2026-09-13T09:00:00.123456+08:00'), '2026-09-13T01:00:00.123456Z')
  assert.equal(c.instant('2026-09-13T01:00:00Z'), '2026-09-13T01:00:00.000000Z')
  const value = f.input(true), same = { ...value, outboundAt: '2026-09-13T09:00:00.123456+08:00' }
  assert.equal(c.requestHash(value), c.requestHash(same))
  assert.notEqual(c.requestHash(value), c.requestHash({ ...value, outboundAt: '2026-09-13T01:00:00.123455Z' }))
  for (const invalid of ['2026-02-29T01:00:00Z', '2026-04-31T01:00:00Z', '0000-01-01T01:00:00Z']) assert.throws(() => c.instant(invalid))
})
test('partial departure history sums each original line and does not imply warehouse receipt', () => {
  const value = f.input(), part = { ...value, lines: [{ ...value.lines[0], quantity: '0.375' }] }, first = f.result(part)
  const history = f.history(value, [first]); assert.equal(c.validateHistory(history, value).outbound_status, 'partially_outbound')
  const options = f.options(value); Object.assign(options.lines[0], { departed_quantity: '0.375', remaining_quantity: '0.625', held_quantity: '0.625', selectable_quantity: '0.625' })
  c.validateOptions(options, value, history)
  const second = f.result({ ...value, lines: [{ ...value.lines[0], quantity: '0.625' }] })
  second.outbound_id = f.id(40); second.posting_transaction_id = f.id(41); second.request_id = 'wxreq-' + 'e'.repeat(36)
  Object.assign(second.lines[0], { departed_quantity: '0.375', remaining_quantity: '0.625', held_quantity: '0.625' })
  const full = f.history(value, [first, second]); assert.equal(c.validateHistory(full, value).outbound_status, 'outbound')
  assert.equal(full.original.status, 'submitted'); assert.equal(full.cancellation, null)
  for (const change of [x => { x.outbound_status = 'received' }, x => { x.items[1].lines[0].departed_quantity = '0.000' },
    x => { x.items[1].lines[0].selected_quantity = '1.000' }, x => { x.items[1].posting_transaction_id = first.posting_transaction_id },
    x => { x.items[1].lines[0].operation_line_id = f.id(99) }, x => { x.items[1].lines[0].material_id = f.id(99) },
    x => { x.items[1].lines[0].source_stock_account_id = f.id(99) }]) {
    const raw = clone(full); change(raw); assert.throws(() => c.validateHistory(raw, value))
  }
  assert.throws(() => c.validateOptions(f.options(value), value, history))
})
test('original cancellation stays independent and blocks a new physical departure', async () => {
  const value = f.input(), cancelled = f.history(value, [], true)
  assert.equal(c.validateHistory(cancelled, value).outbound_status, 'not_outbound')
  assert.throws(() => c.validateOptions(f.options(value), value, cancelled))
  assert.throws(() => c.validateHistory(f.history(value, [f.result(value)], true), value))
  const x = fixture(); x.api.request = async () => cancelled
  await assert.rejects(x.run()); assert.equal(x.records.size, 0)
})
test('SN selection requires fresh physical proofs; duplicate serials and invalid precision cannot become commands', () => {
  const value = f.input(true), choices = c.validateOptions(f.options(value), value, f.history(value))
  assert.throws(() => c.buildLines(choices, { [f.id(31)]: { quantity: '1', serial_verifications: [] } }))
  const wrong = f.drafts(value); wrong[f.id(31)].serial_verifications[0].sku_code = 'OTHER'
  assert.throws(() => c.buildLines(choices, wrong))
  const duplicate = f.input(true); duplicate.lines.push({ ...duplicate.lines[0], operation_line_id: f.id(90) })
  assert.throws(() => c.command(duplicate))
  const plain = f.input(), options = f.options(plain); options.lines[0].quantity_scale = 2
  const checked = c.validateOptions(options, plain, f.history(plain))
  assert.throws(() => c.buildLines(checked, { [f.id(31)]: { quantity: '0.375', serial_verifications: [] } }))
})
test('different original return lines cannot sum the same held stock twice', () => {
  const value = f.input(), history = f.history(value), options = f.options(value)
  const origin = clone(history.original.lines[0]); origin.source.source_recovery_line_id = f.id(90)
  origin.source.owed_quantity = origin.source.available_quantity = origin.source.selectable_quantity = '2.000'
  history.original.lines[0].source.available_quantity = '2.000'; history.original.lines.push(origin)
  options.lines.push({ ...clone(options.lines[0]), source_recovery_line_id: f.id(90), operation_line_id: f.id(91) })
  const choices = c.validateOptions(options, value, history)
  assert.throws(() => c.buildLines(choices, { ...f.drafts(value), [f.id(91)]: { quantity: '1', serial_verifications: [] } }))
})
test('response loss retains only coordinates and digests; restart reads or seals without another stock POST', async () => {
  const x = fixture(true); let writes = 0
  x.api.postNoReplay = async () => { writes++; throw new Error('lost') }
  assert.equal((await x.run()).status, 'pending'); assert.equal(writes, 1)
  assert.deepEqual(x.make().read({ work_order_id: x.value.workOrderId }).value, x.marker)
  for (const secret of ['PRIVATE-RETURN-QR', 'idempotency_key', 'outbound_at', 'serial_no', 'reason', 'lines']) assert.ok(![...x.records.values()].join('').includes(secret))
  await assert.rejects(x.run()); assert.equal(writes, 1)
  assert.equal((await recoverPending({ ...x.args, store: x.make() })).status, 'pending')
  assert.equal((await sealPending({ ...x.args, store: x.make(), confirm: async () => true })).status, 'sealed')
  assert.equal(writes, 1); assert.equal(x.records.size, 0)
})
test('lost success is recovered by exact GET without replaying the physical movement', async () => {
  const x = fixture(true), write = x.api.postNoReplay; let posts = 0
  x.api.postNoReplay = async (...args) => { posts++; await write(...args); throw new Error('lost after success') }
  assert.equal((await x.run()).status, 'pending')
  assert.equal((await recoverPending({ ...x.args, store: x.make() })).status, 'confirmed')
  assert.equal(posts, 1); assert.equal(x.records.size, 0)
})
test('storage failure, changed access and cancelled confirmation cannot issue a physical write', async () => {
  const cancelled = fixture(); cancelled.args.confirm = async () => false
  assert.equal((await cancelled.run()).status, 'cancelled'); assert.equal(cancelled.records.size, 0)
  const changed = fixture(); let access = 'old'; changed.args.authorize = async () => access
  changed.args.confirm = async () => { access = 'new'; return true }
  await assert.rejects(changed.run()); assert.equal(changed.records.size, 0)
  const storage = fixture(); storage.storage.setStorageSync = () => { throw new Error('full') }
  await assert.rejects(storage.run()); assert.equal(storage.calls.filter(row => row.body).length, 0)
})
test('unknown, cross-operation and partial recovery retain the marker', async () => {
  for (const kind of ['partial', 'foreign', 'plan', 'operation', 'sealed-wrong-action', 'untrusted-404']) {
    const x = fixture(); await x.args.store.withLease({ work_order_id: x.value.workOrderId }, lease => lease.persist(x.marker))
    x.api.request = async () => {
      if (kind === 'untrusted-404') throw { status: 404, code: 'stock_return_not_observed' }
      if (kind === 'sealed-wrong-action') { const raw = f.sealed(x.marker); raw.seal.operation_type = 'cancel_return'; return raw }
      const raw = f.result(x.value)
      if (kind === 'partial') raw.lines = []
      if (kind === 'foreign') raw.operator_person_id = f.id(99)
      if (kind === 'plan') raw.plan_hash = 'f'.repeat(64)
      if (kind === 'operation') raw.operation_id = f.id(99)
      return raw
    }
    await assert.rejects(recoverPending(x.args)); assert.equal(x.records.size, 1)
  }
  assert.throws(() => validateMarker({ ...f.marker(), plan_hash: null }))
  assert.throws(() => validateMarker({ ...f.marker(), operation_id: null }))
})
