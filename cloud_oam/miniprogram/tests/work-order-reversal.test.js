const test = require('node:test')
const assert = require('node:assert/strict')
const reversal = require('../utils/work-order-reversal-command')
const { submitReversal } = require('../utils/work-order-reversal-submit')
const { recoverPending, sealPending } = require('../utils/work-order-recovery')
const { createStore, validateMarker } = require('../utils/work-order-recovery-store')
const f = require('./fixtures/work-order-reversal-data.cjs')
const clone = value => JSON.parse(JSON.stringify(value))
const context = value => ({ ...value, sourceVersion: 'wo-v2:test', ledgerCursor: 8 })
function storeFixture() {
  const records = new Map(), storage = { getStorageInfoSync: () => ({ keys: [...records.keys()] }), getStorageSync: key => records.get(key) || '',
    setStorageSync: (key, value) => records.set(key, value), removeStorageSync: key => records.delete(key) }
  const makeStore = () => createStore({ storage, state: { active: new Set(), faults: new Set() } })
  return { records, storage, makeStore, store: makeStore() }
}
function notObserved() { return Object.assign(new Error('尚未观察到'), { responseReceived: true, status: 404, code: 'work_order_reversal_not_observed' }) }
function submitFixture(value = f.input(), tracked = false) {
  const local = storeFixture(), calls = [], plan = f.preview(value, 'consume', tracked)
  let accepted, confirmed = false
  const api = { createRequestId: () => f.marker(value).trace_request_id, createIdempotencyKey: () => 'wxidem-' + 'c'.repeat(36),
    async request(path, options) {
      calls.push({ path, ...options })
      assert.equal(options.noRefresh, true); assert.equal(options.header['Cache-Control'], 'no-store')
      if (path.endsWith('/material-options')) return f.options(value)
      if (path.endsWith('/preview')) { assert.equal(local.records.size, 0); return clone(plan) }
      if (path.includes('/by-request/')) { assert.ok(accepted); return f.result(value, plan) }
      throw new Error('unexpected path')
    },
    async postNoReplay(path, data, coordinates) {
      assert.ok(confirmed); assert.equal(local.records.size, 1)
      assert.deepEqual(local.store.read({ work_order_id: value.workOrderId }).value, f.marker(value))
      assert.equal(data.expected_plan_hash, plan.plan_hash); assert.equal(coordinates.requestId, data.request_id)
      assert.equal(coordinates.idempotencyKey, data.idempotency_key)
      accepted = data; calls.push({ path, method: 'POST', data })
      return { deliberately: 'not a recovery proof' }
    } }
  const args = { ...value, api, store: local.store, authorize: async () => 'current-context', confirm: async review => {
    assert.equal(local.records.size, 0); assert.equal(review.kind, 'reverse'); assert.ok(Object.isFrozen(review.rows[0]))
    assert.ok(review.rows.every(row => row.effect)); assert.equal(review.pairs.length, plan.replacement_pairs.length)
    confirmed = true; return true
  } }
  return { ...local, args, calls, plan, api }
}

test('reason and whole original are exact, Unicode safe and hash binds intent independently of the plan', () => {
  const value = f.input(), digest = reversal.requestHash(value)
  assert.equal(reversal.requestHash({ ...value, reason: '  ' + value.reason + '\t' }), digest)
  assert.notEqual(reversal.requestHash({ ...value, reason: value.reason + '更正' }), digest)
  assert.equal('work_order_id' in reversal.payload(value), false)
  for (const reason of ['', ' ', 'x'.repeat(501), '\ud800', 'x\u0000']) assert.throws(() => reversal.command({ ...value, reason }))
  for (const selection of [null, {}, { ...value.selection, extra: true }, { original_operation_id: null, original_replacement_id: null },
    { ...value.selection, original_replacement_id: f.id(99) }]) assert.throws(() => reversal.command({ ...value, selection }))
  assert.equal(reversal.command({ ...value, reason: '🔧'.repeat(500) }).reason.length, 1000)
})
for (const tracked of [false, true]) for (const kind of ['occupy', 'release', 'consume', 'recover', 'replace']) {
  test(`complete ${kind} plan validates ${tracked ? 'SN' : 'quantity'} and rejects altered dimensions or partial groups`, () => {
    const value = f.input(kind === 'replace'), raw = f.preview(value, kind, tracked)
    assert.equal(reversal.validatePreview(raw, context(value)), raw)
    const changes = [r => { r.work_order.can_operate = false }, r => { r.work_order.source_version = 'old' }, r => { r.authorization_version++ },
      r => { r.ledger_cursor-- }, r => { r.reason += 'other' }, r => { r.plan_hash = '' }, r => { r.request_hash = 'f'.repeat(64) },
      r => { r.children[0].movements[0].quantity = '1.1' }, r => { r.children[0].movements[0].reservation_delta = '9.000' },
      r => { r.children[0].movements[0].from_account_id = r.children[0].movements[0].to_account_id }, r => { r.children[0].movements[0].line_no = 2 },
      r => { r.children.pop() }, r => { r.children.push(clone(r.children[0])) }, r => { r.children[0].movements[0].qr_code = 'unexpected' }]
    if (tracked) changes.push(r => { r.children[0].movements[0].serials[0].previous_ledger_cursor = 9 }, r => { r.children[0].movements[0].serials.push(clone(r.children[0].movements[0].serials[0])) })
    if (tracked && kind === 'replace') changes.push(r => { r.replacement_pairs = [] }, r => { r.replacement_pairs[0].removed_serial_id = f.id(99) })
    for (const change of changes) { const copy = clone(raw); change(copy); assert.throws(() => reversal.validatePreview(copy, context(value))) }
  })
}
test('original list groups pairs, marks completed inverses and rejects foreign, partial or duplicate objects', () => {
  const value = f.input(true), raw = f.originals(value)
  assert.equal(reversal.validateOriginals(raw, context(value))[0].selectable, true)
  const done = clone(raw); done.items[0].reversal_id = f.id(95)
  assert.equal(reversal.validateOriginals(done, context(value))[0].selectable, false)
  for (const change of [r => { r.items[0].operation_count = 1 }, r => { r.items.push(clone(r.items[0])) },
    r => { r.person_id = f.id(99) }, r => { r.items[0].original_operation_id = f.id(99) }, r => { r.items[0].reversal_id = '' }]) {
    const copy = clone(raw); change(copy); assert.throws(() => reversal.validateOriginals(copy, context(value)))
  }
})
test('original result requires selected plan, intent and whole proof; seal cannot substitute another request', () => {
  for (const paired of [false, true]) {
    const value = f.input(paired), raw = f.result(value), marker = f.marker(value)
    assert.equal(reversal.validateResult(raw, marker), raw)
    for (const change of [r => { r.plan_hash = 'f'.repeat(64) }, r => { r.request_hash = 'f'.repeat(64) }, r => { r.reason += 'altered' },
      r => { r.operator_person_id = f.id(99) }, r => { r.items = [] }, r => { r.items[0].inverse_transaction_id = r.items[0].original_transaction_id },
      r => { r.items.push(clone(r.items[0])) }, r => { r.extra = true }]) {
      const copy = clone(raw); change(copy); assert.throws(() => reversal.validateResult(copy, marker))
    }
    reversal.validateLookup(f.sealed(value), marker)
    assert.throws(() => reversal.validateLookup(f.sealed(value), { ...marker, trace_request_id: 'wxreq-' + 'f'.repeat(36) }))
  }
})
test('confirmed ordinary and paired submits persist only coordinates and digests before one POST, then require GET', async () => {
  for (const paired of [false, true]) {
    const value = f.input(paired), fixture = submitFixture(value, true)
    assert.equal((await submitReversal(fixture.args)).status, 'confirmed')
    assert.equal(fixture.records.size, 0)
    assert.deepEqual(fixture.calls.map(row => row.path.split('/').pop()), ['material-options', 'preview', 'material-reversals', f.marker(value).trace_request_id])
  }
})
test('cancel or stale confirmation causes no write; an uncertain POST preserves a restart-safe marker', async () => {
  const cancelled = submitFixture(); cancelled.args.confirm = async () => false
  assert.equal((await submitReversal(cancelled.args)).status, 'cancelled'); assert.equal(cancelled.records.size, 0)
  const changed = submitFixture(); let identity = 'before'
  changed.args.authorize = async () => identity; changed.args.confirm = async () => { identity = 'after'; return true }
  await assert.rejects(submitReversal(changed.args)); assert.equal(changed.records.size, 0)
  const lost = submitFixture(); let posts = 0
  lost.api.postNoReplay = async () => { posts++; throw new Error('network lost') }
  assert.equal((await submitReversal(lost.args)).status, 'pending'); assert.equal(posts, 1)
  const marker = lost.makeStore().read({ work_order_id: lost.args.workOrderId }).value
  assert.deepEqual(marker, f.marker())
  for (const secret of ['idempotency_key', 'reason', 'quantity', 'serial_no', 'children', 'token']) assert.equal([...lost.records.values()].join().includes(secret), false)
  assert.throws(() => validateMarker({ ...marker, plan_hash: '' }))
  await assert.rejects(submitReversal(lost.args)); assert.equal(posts, 1)
})
test('404, transport failure, partial proof and changed authority never clear the durable request', async () => {
  for (const mode of ['404', 'timeout', 'partial', 'plan', 'authority', 'foreign']) {
    const value = f.input(), local = storeFixture(), anchor = { work_order_id: value.workOrderId }
    await local.store.withLease(anchor, lease => lease.persist(f.marker(value)))
    let reads = 0, authority = 0
    const promise = recoverPending({ ...value, personId: mode === 'foreign' ? f.id(99) : value.personId, store: local.store,
      authorize: async () => mode === 'authority' ? String(++authority) : 'same', api: { request: async () => {
        reads++; if (mode === '404') throw notObserved(); if (mode === 'timeout') throw new Error('timeout')
        const raw = f.result(value); if (mode === 'partial') raw.items = []; if (mode === 'plan') raw.plan_hash = 'f'.repeat(64); return raw
      } } })
    if (mode === '404') assert.equal((await promise).status, 'pending'); else await assert.rejects(promise)
    assert.equal(local.store.read(anchor).kind, 'valid'); assert.equal(reads, mode === 'foreign' ? 0 : 1)
  }
})
test('permanent nonexecution is confirmed by GET; a racing posted result wins instead of a seal', async () => {
  for (const posted of [false, true]) {
    const value = f.input(), local = storeFixture(), anchor = { work_order_id: value.workOrderId }
    await local.store.withLease(anchor, lease => lease.persist(f.marker(value)))
    let reads = 0, seals = 0
    const result = await sealPending({ ...value, store: local.store, authorize: async () => 'same', confirm: async () => true, api: {
      request: async () => { if (++reads === 1) throw notObserved(); return posted ? f.result(value) : f.sealed(value) },
      postSealNoReplay: async (path, data) => { seals++; assert.equal(path.endsWith('/seal'), true); assert.equal(data.request_hash, f.marker(value).request_hash) }
    } })
    assert.equal(result.status, posted ? 'confirmed' : 'sealed'); assert.equal(reads, 2); assert.equal(seals, 1); assert.equal(local.records.size, 0)
  }
})
