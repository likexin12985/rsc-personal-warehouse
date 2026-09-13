const test = require('node:test')
const assert = require('node:assert/strict')
const c = require('../utils/stock-return-contract')
const { submitReturn, cancelReturn } = require('../utils/stock-return-submit')
const { createStore, validateMarker } = require('../utils/work-order-recovery-store')
const { recoverPending, sealPending } = require('../utils/work-order-recovery')
const f = require('./fixtures/stock-return-data.cjs')
const clone = value => JSON.parse(JSON.stringify(value))
function local() {
  const records = new Map(), storage = { getStorageInfoSync: () => ({ keys: [...records.keys()] }), getStorageSync: key => records.get(key) || '',
    setStorageSync: (key, value) => records.set(key, value), removeStorageSync: key => records.delete(key) }
  return { records, make: () => createStore({ storage, state: { active: new Set(), faults: new Set() } }) }
}
function missing() { return Object.assign(new Error('missing'), { status: 404, code: 'stock_return_not_observed', responseReceived: true }) }
function fixture(tracked = false, cancelling = false) {
  const value = f.input(tracked), state = local(), calls = [], marker = f.marker(value, cancelling)
  let accepted = false, confirmed = false, sealed = false
  const api = { createRequestId: () => marker.trace_request_id, createIdempotencyKey: () => 'wxidem-' + 'c'.repeat(36),
    request: async (path, options) => {
      calls.push({ path, ...options }); assert.equal(options.noRefresh, true)
      if (path.includes('/by-request/')) { if (sealed) return f.sealed(marker); if (!accepted) throw missing(); return f.result(value, marker) }
      if (path.endsWith('/options')) return f.options(value)
      if (path.endsWith('/preview')) return f.preview(value)
      if (path.endsWith('/returns')) return f.history(value)
      throw new Error('unexpected route')
    },
    postNoReplay: async (path, body, coordinates) => {
      assert.ok(confirmed); assert.equal(state.records.size, 1); assert.equal(body.request_id, marker.trace_request_id)
      assert.equal(coordinates.requestId, body.request_id); assert.equal(coordinates.idempotencyKey, body.idempotency_key)
      accepted = true; calls.push({ path, method: 'POST', body }); return { not: 'a recovery proof' }
    },
    postSealNoReplay: async (path, body, coordinates) => {
      assert.equal(body.request_hash, marker.request_hash); assert.equal(coordinates.requestId, marker.trace_request_id)
      assert.equal(body.idempotency_key, undefined); sealed = true
    } }
  const args = { ...value, api, store: state.make(), drafts: f.drafts(value), operationId: f.id(20), authorize: async () => 'current',
    confirm: async review => { assert.equal(state.records.size, 0); assert.ok(Object.isFrozen(review.rows)); assert.ok(review.rows.length); confirmed = true; return true } }
  return { ...state, value, args, calls, marker, api, run: () => (cancelling ? cancelReturn : submitReturn)(args) }
}
for (const tracked of [false, true]) {
  test(`return sources and exact destination validate ${tracked ? 'SN' : 'quantity'} even after work order closed`, () => {
    const value = f.input(tracked), raw = f.options(value), choices = c.validateOptions(raw, value)
    assert.equal(choices.workOrder.can_operate, false)
    assert.deepEqual(c.buildLines(choices, f.drafts(value)), value.lines)
    assert.equal(c.validatePreview(f.preview(value), value, choices).request_hash, c.requestHash(value))
    for (const change of [x => { x.sources.person_id = f.id(90) }, x => { x.destinations[0].source_location_id = f.id(90) },
      x => { x.sources.items[0].committed_quantity = '2.000' }, x => { x.sources.items[0].selectable_quantity = '2.000' },
      x => { x.sources.items.push(clone(x.sources.items[0])) }, x => { x.destinations.push(clone(x.destinations[0])) },
      x => { x.sources.items[0].qr_code = 'leak' }, x => { x.sources.blockers = ['unknown'] }]) {
      const damaged = clone(raw); change(damaged); assert.throws(() => c.validateOptions(damaged, value))
    }
    for (const change of [x => { x.lines[0].selected_quantity = '0.000' }, x => { x.lines = [] }, x => { x.reason += 'altered' },
      x => { x.plan_hash = '' }, x => { x.destination.transit_location_id = f.id(90) }]) {
      const damaged = f.preview(value); change(damaged); assert.throws(() => c.validatePreview(damaged, value, choices))
    }
  })
  for (const cancelling of [false, true]) test(`${tracked ? 'SN' : 'quantity'} ${cancelling ? 'cancel' : 'submit'} sends once after durable marker and accepts only GET`, async () => {
    const x = fixture(tracked, cancelling)
    assert.equal((await x.run()).status, 'confirmed'); assert.equal(x.records.size, 0)
    assert.equal(x.calls.filter(row => row.body).length, 1)
    assert.ok(x.calls.at(-1).path.endsWith(x.marker.trace_request_id))
  })
}
test('pooled recovery rows cannot sum the same available balance or duplicate a physical SN', () => {
  const value = f.input(), raw = f.options(value), row = clone(raw.sources.items[0]); row.source_recovery_line_id = f.id(30)
  raw.sources.items.push(row); const choices = c.validateOptions(raw, value)
  assert.throws(() => c.buildLines(choices, { ...f.drafts(value), [f.id(30)]: { quantity: '1', serial_verifications: [] } }))
  const tracked = f.input(true); tracked.lines.push({ ...tracked.lines[0], source_recovery_line_id: f.id(30) })
  assert.throws(() => c.command(tracked))
})
test('original and cancellation stay separate; malformed history and partial original proofs are rejected', () => {
  const value = f.input(true), history = f.history(value, true)
  assert.equal(c.validateHistory(history, value)[0].original.status, 'submitted')
  for (const change of [x => { x.items[0].original.lines = [] }, x => { x.items[0].cancellation.operation_id = f.id(90) },
    x => { x.items[0].original.status = 'cancelled' }, x => { x.items[0].cancellation.reason += 'altered' },
    x => { x.items.push(clone(x.items[0])) }, x => { x.items[0].original.destination.qr_code = 'unexpected' }]) {
    const damaged = clone(history); change(damaged); assert.throws(() => c.validateHistory(damaged, value))
  }
})
for (const cancelling of [false, true]) test(`uncertain ${cancelling ? 'cancel' : 'submit'} retains restart-safe recovery and seals only exact absence`, async () => {
  const x = fixture(true, cancelling); let posts = 0
  x.api.postNoReplay = async () => { posts++; throw new Error('response lost') }
  assert.equal((await x.run()).status, 'pending'); assert.equal(posts, 1)
  const marker = x.make().read({ work_order_id: x.value.workOrderId }).value
  assert.deepEqual(marker, x.marker)
  for (const secret of ['idempotency_key', 'PRIVATE-RETURN-QR', 'serial_no', 'reason', 'lines']) assert.ok(![...x.records.values()].join('').includes(secret))
  await assert.rejects(x.run()); assert.equal(posts, 1)
  assert.equal((await recoverPending({ ...x.args, store: x.make() })).status, 'pending'); assert.equal(x.records.size, 1)
  assert.equal((await sealPending({ ...x.args, store: x.make(), confirm: async () => true })).status, 'sealed')
  assert.equal(x.records.size, 0)
})
test('a lost response after actual success is resolved without issuing a second command', async () => {
  const x = fixture(true), post = x.api.postNoReplay; let calls = 0
  x.api.postNoReplay = async (...args) => { calls++; await post(...args); throw new Error('lost after commit') }
  assert.equal((await x.run()).status, 'pending')
  assert.equal((await recoverPending({ ...x.args, store: x.make() })).status, 'confirmed')
  assert.equal(calls, 1); assert.equal(x.records.size, 0)
})
test('cancelled confirmation, changed access, unreadable storage or partial recovery cannot start another write', async () => {
  const stopped = fixture(); stopped.args.confirm = async () => false
  assert.equal((await stopped.run()).status, 'cancelled'); assert.equal(stopped.records.size, 0)
  const changed = fixture(); let access = 'before'; changed.args.authorize = async () => access
  changed.args.confirm = async () => { access = 'after'; return true }
  await assert.rejects(changed.run()); assert.equal(changed.records.size, 0)
  for (const mode of ['partial', 'foreign', 'wrong-plan', 'untrusted-404']) {
    const x = fixture(); await x.args.store.withLease({ work_order_id: x.value.workOrderId }, lease => lease.persist(x.marker))
    x.api.request = async () => {
      if (mode === 'untrusted-404') throw { status: 404, code: 'stock_return_not_observed' }
      const result = f.result(x.value)
      if (mode === 'partial') result.lines = []
      if (mode === 'foreign') result.requester_id = f.id(90)
      if (mode === 'wrong-plan') result.plan_hash = 'f'.repeat(64)
      return result
    }
    await assert.rejects(recoverPending(x.args)); assert.equal(x.records.size, 1)
  }
  assert.throws(() => validateMarker({ ...f.marker(), operation_id: f.id(90) }))
  assert.throws(() => validateMarker({ ...f.marker(f.input(), true), plan_hash: 'f'.repeat(64) }))
})
