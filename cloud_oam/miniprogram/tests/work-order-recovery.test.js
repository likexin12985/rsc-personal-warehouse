const assert = require('node:assert/strict')
const test = require('node:test')
const command = require('../utils/work-order-command')
const storageModule = require('../utils/work-order-recovery-store')
const { recoverPending } = require('../utils/work-order-recovery')
const PERSON = '10000000-0000-4000-8000-000000000001'
const ORDER = '20000000-0000-4000-8000-000000000001'
const OTHER_ORDER = '20000000-0000-4000-8000-000000000002'
const ACCOUNT = '40000000-0000-4000-8000-000000000001'
const MATERIAL = '50000000-0000-4000-8000-000000000001'
const SERIAL = '60000000-0000-4000-8000-000000000001'
const OPERATION = '70000000-0000-4000-8000-000000000001'
const TRACE = 'wxreq-' + 'a'.repeat(36)
const clone = value => JSON.parse(JSON.stringify(value))
function line() { return { material_id: MATERIAL, stock_account_id: ACCOUNT, quantity: '1', condition_before: 'used', serial_ids: [SERIAL], serial_verifications: [{ serial_id: SERIAL, sku_code: 'SKU-测试', serial_no: 'SN-旧件', qr_code: 'QR-测试😀' }] } }
function marker() { return storageModule.validateMarker({ v: 1, kind: 'work_order_material', work_order_id: ORDER, person_id: PERSON,
  authorization_version: 7, operation_type: 'occupy', trace_request_id: TRACE, request_hash: command.requestHash('occupy', ORDER, PERSON, [line()]) }) }
function confirmed() {
  return { schema_version: '1.0', lookup_status: 'confirmed', command: { schema_version: '1.0', operation_id: OPERATION,
    operation_no: 'WOM-TEST', work_order_id: ORDER, posting_transaction_id: ACCOUNT, operation_type: 'occupy', status: 'posted',
    operator_person_id: PERSON, request_id: TRACE, request_hash: marker().request_hash, posted_at: '2026-09-12T08:00:00Z' } }
}
function storage() {
  const data = new Map()
  return { data, getStorageInfoSync: () => ({ keys: [...data.keys()] }), getStorageSync: key => data.has(key) ? data.get(key) : '',
    setStorageSync: (key, value) => data.set(key, value), removeStorageSync: key => data.delete(key) }
}
function storeFor(backing = storage()) { return storageModule.createStore({ storage: backing, state: { active: new Set(), faults: new Set() } }) }
const anchor = { work_order_id: ORDER }
async function persisted(backing = storage()) { const store = storeFor(backing); await store.withLease(anchor, lease => lease.persist(marker())); return { store, backing } }

test('canonical command fingerprint matches Python for Unicode and server-derived occupy target', () => {
  // Computed independently by the backend client_request_hash helper.
  assert.equal(command.requestHash('occupy', ORDER, PERSON, [line()]), '2039bbb8002213aa96981d0bfc0d1ebeb8132780d37ba397f3e7f8b303d3a3c4')
  const payload = command.payload('occupy', ORDER, PERSON, [line()])
  assert.equal(payload.lines[0].quantity, '1.000'); assert.equal('target_stock_account_id' in payload.lines[0], false)
  const fraction = line(); fraction.serial_ids = []; fraction.serial_verifications = []; fraction.quantity = '900719925474099.999'
  assert.equal(command.payload('consume', ORDER, PERSON, [fraction]).lines[0].quantity, '900719925474099.999')
})
test('command normalization rejects ambiguous accounts, serials, targets and missing scan proof', () => {
  for (const change of [l => { l.quantity = '1.001' }, l => { l.serial_ids = [SERIAL, SERIAL] }, l => { l.serial_verifications = [] }, l => { l.serial_verifications[0].serial_id = OPERATION }, l => { l.serial_verifications[0].qr_code = '' }, l => { l.target_stock_account_id = ACCOUNT }]) {
    const value = line(); change(value); assert.throws(() => command.payload('occupy', ORDER, PERSON, [value]))
  }
  assert.throws(() => command.payload('occupy', ORDER, PERSON, [line(), line()]))
  assert.throws(() => command.payload('release', ORDER, PERSON, [line()]))
  assert.throws(() => command.utf8('\ud800'))
})
test('recovery records survive restart and contain only anchors and a digest', async () => {
  const { backing } = await persisted()
  const raw = backing.getStorageSync(storageModule.PREFIX + ORDER)
  for (const forbidden of ['qr_code', 'serial_no', 'quantity', 'material_id', 'idempotency_key', 'token', 'SKU-测试', 'SN-旧件']) assert.equal(raw.includes(forbidden), false)
  const restored = storeFor(backing).read(anchor)
  assert.equal(restored.kind, 'valid'); assert.deepEqual(restored.value, marker())
  assert.throws(() => storageModule.validateMarker({ ...marker(), token: 'forbidden' }))
})
test('one unresolved command blocks overwrite only for its own work order', async () => {
  const { store } = await persisted()
  await assert.rejects(store.withLease(anchor, lease => lease.persist(marker())))
  let release
  const held = store.withLease(anchor, () => new Promise(resolve => { release = resolve }))
  await assert.rejects(store.withLease(anchor, () => {}))
  const other = { ...marker(), work_order_id: OTHER_ORDER }
  await store.withLease(other, lease => lease.persist(other))
  release(); await held
  assert.equal(store.read(anchor).kind, 'valid')
})
test('storage write/readback failures and corrupt records stay blocked', async () => {
  const broken = storage(); broken.setStorageSync = () => {}
  const store = storeFor(broken)
  await assert.rejects(store.withLease(anchor, lease => lease.persist(marker())))
  assert.equal(store.read(anchor).kind, 'unavailable')
  const { backing } = await persisted(); backing.setStorageSync(storageModule.PREFIX + ORDER, '{bad')
  assert.equal(storeFor(backing).read(anchor).kind, 'unavailable')
})
test('confirmed original result clears only its exact marker using GET with no refresh', async () => {
  const { store } = await persisted(), calls = []
  const result = await recoverPending({ api: { async request(path, options) { calls.push({ path, ...clone(options) }); return confirmed() } }, store,
    workOrderId: ORDER, personId: PERSON, authorize: async () => 'current-authority' })
  assert.equal(result.status, 'confirmed'); assert.equal(store.read(anchor).kind, 'missing')
  assert.deepEqual(calls, [{ path: `/v1/work-orders/${ORDER}/material-operations/occupy/by-request/${TRACE}`, method: 'GET', noRefresh: true, header: { 'Cache-Control': 'no-store', Pragma: 'no-cache' } }])
})
test('not-observed, timeout and conflicting results retain the marker without any POST', async () => {
  for (const mode of ['not_observed', 'timeout', 'hash', 'kind', 'person', 'request']) {
    const { store } = await persisted(); let calls = 0
    const api = { async request(_, options) {
      calls++; assert.equal(options.method, 'GET')
      if (mode === 'timeout') throw new Error('timeout')
      if (mode === 'not_observed') return { schema_version: '1.0', lookup_status: 'not_observed', command: null }
      const raw = confirmed()
      if (mode === 'hash') raw.command.request_hash = 'b'.repeat(64)
      if (mode === 'kind') raw.command.operation_type = 'consume'
      if (mode === 'person') raw.command.operator_person_id = ACCOUNT
      if (mode === 'request') raw.command.request_id = 'wxreq-' + 'b'.repeat(36)
      return raw
    } }
    const work = recoverPending({ api, store, workOrderId: ORDER, personId: PERSON, authorize: async () => 'authority' })
    if (mode === 'not_observed') assert.equal((await work).status, 'pending')
    else await assert.rejects(work)
    assert.equal(calls, 1); assert.equal(store.read(anchor).kind, 'valid')
  }
})
test('authority changes, another person and storage-clear faults never discard recovery evidence', async () => {
  const { store, backing } = await persisted(); let count = 0
  await assert.rejects(recoverPending({ api: { request: async () => confirmed() }, store, workOrderId: ORDER, personId: PERSON, authorize: async () => String(++count) }))
  assert.equal(store.read(anchor).kind, 'valid')
  await assert.rejects(recoverPending({ api: { request: () => { throw new Error('must not read another person') } }, store, workOrderId: ORDER, personId: ACCOUNT, authorize: async () => '' }))
  backing.removeStorageSync = () => {}
  await assert.rejects(recoverPending({ api: { request: async () => confirmed() }, store, workOrderId: ORDER, personId: PERSON, authorize: async () => 'same' }))
  assert.equal(store.read(anchor).kind, 'unavailable')
  assert.ok(backing.getStorageSync(storageModule.PREFIX + ORDER))
})
