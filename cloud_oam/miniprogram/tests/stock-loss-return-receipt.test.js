const test = require('node:test')
const assert = require('node:assert/strict')
const f = require('./fixtures/stock-return-receiving-data.cjs')
const receiving = require('../utils/stock-return-receiving-contract')
const receipt = require('../utils/stock-return-receipt-contract')
const inbound = require('../utils/stock-return-inbound-contract')
const { createStore, validateMarker } = require('../utils/work-order-recovery-store')
const { recoverPending } = require('../utils/work-order-recovery')
const origin = Object.freeze({ origin_kind: 'loss_report', loss_operation_id: f.id(201), loss_line_id: f.id(202), headquarters_decision_id: f.id(203), disposition_id: f.id(204) })
function loss(row) { const result = structuredClone(row); delete result.work_order_id; result.origin = origin; result.lines?.forEach(line => { line.condition_code = 'new' }); return result }
function marker(kind = 'stock_return') {
  const row = loss(f.receipt())
  return validateMarker({ v: 1, kind, origin, shipment_id: f.id(2), person_id: f.id(1), authorization_version: 1,
    operation_type: 'receive_return', ...(kind === 'stock_return' ? { operation_id: f.id(3) } : { receipt_id: row.receipt_id }),
    trace_request_id: row.request_id, request_hash: row.request_hash, plan_hash: row.plan_hash })
}
function storage() { return { values: new Map(), getStorageInfoSync() { return { keys: [...this.values.keys()] } }, getStorageSync(k) { return this.values.get(k) || '' }, setStorageSync(k, v) { this.values.set(k, v) }, removeStorageSync(k) { this.values.delete(k) } } }
const storeFor = value => createStore({ storage: value, state: { active: new Set(), faults: new Set() } })

for (const tracked of [false, true]) test(`loss receipt history preserves new condition and explicit origin, tracked=${tracked}`, () => {
  const row = loss(f.receipt(tracked)), history = f.history(tracked, [row]); history.package = loss(history.package)
  assert.equal(receiving.validateHistory(history, f.expected).receipts[0].origin, origin)
  assert.equal(receipt.validateLookup(row, marker()).receipt_id, row.receipt_id)
  const ordinary = f.parcel(tracked); ordinary.lines[0].condition_code = 'new'
  assert.throws(() => receiving.parcel(ordinary, f.expected))
  assert.throws(() => receiving.parcel({ ...history.package, work_order_id: null }, f.expected))
  assert.throws(() => receiving.parcel({ ...history.package, origin: { ...origin, request_hash: 'a'.repeat(64) } }, f.expected))
  assert.throws(() => receipt.validateLookup({ ...row, origin: { ...origin, loss_line_id: f.id(999) } }, marker()))
})

for (const kind of ['stock_return', 'stock_return_inbound']) test(`loss ${kind} marker survives reload and stays distinct from ordinary requests`, async () => {
  const memory = storage(), store = storeFor(memory), value = marker(kind)
  await store.withLease(value, async lease => lease.persist(value))
  const reloaded = storeFor(memory)
  assert.deepEqual(reloaded.listPending(f.id(1)).items, [value])
  assert.equal(reloaded.read({ work_order_id: f.id(4), shipment_id: value.shipment_id }).kind, 'missing')
  assert.equal(reloaded.read({ origin: { ...origin, disposition_id: f.id(205) }, shipment_id: value.shipment_id }).kind, 'missing')
  assert.equal(memory.values.size, 1)
  assert.throws(() => validateMarker({ ...value, work_order_id: f.id(4) }))
  assert.throws(() => validateMarker({ ...value, origin: undefined }))
})

test('lost receipt response is recovered by exact original GET after reload without a write', async () => {
  const memory = storage(), value = marker()
  await storeFor(memory).withLease(value, async lease => lease.persist(value))
  const calls = [], api = { request: async path => { calls.push(path); return loss(f.receipt()) }, postNoReplay: () => assert.fail('no replay') }
  const store = storeFor(memory)
  const result = await recoverPending({ api, store, lossOrigin: origin, shipmentId: value.shipment_id, personId: value.person_id, authorize: async () => 'same' })
  assert.equal(result.status, 'confirmed')
  assert.deepEqual(calls, [`/v1/stock-returns/my-receiving/${value.shipment_id}/receipts/by-request/${value.trace_request_id}`])
  assert.equal(store.read(value).kind, 'missing')
})

test('wrong receipt seal cannot clear a loss recovery marker', async () => {
  const memory = storage(), value = marker(), store = storeFor(memory)
  await store.withLease(value, async lease => lease.persist(value))
  const seal = { schema_version: '1.0', lookup_status: 'sealed', seal: { seal_id: f.id(301), operation_type: 'receive_return',
    operator_person_id: value.person_id, origin, operation_id: value.operation_id, shipment_id: value.shipment_id,
    request_id: value.trace_request_id, request_hash: '0'.repeat(64), sealed_at: f.when(4) } }
  const args = { api: { request: async () => seal }, store, lossOrigin: origin, shipmentId: value.shipment_id, personId: value.person_id, authorize: async () => 'same' }
  await assert.rejects(recoverPending(args))
  assert.equal(store.read(value).kind, 'valid')
  seal.seal.request_hash = value.request_hash
  assert.equal((await recoverPending(args)).status, 'sealed')
  assert.equal(store.read(value).kind, 'missing')
})

test('new-condition inbound preview requires an exact loss-origin object', () => {
  const raw = { schema_version: '1.0', planning_status: 'inbound_preview_only', receipt_id: f.id(21), shipment_id: f.id(2),
    operator_person_id: f.id(1), authorization_version: 1, target_location_id: f.id(6), target_custody_assignment_id: f.id(7),
    receipt_plan_hash: 'a'.repeat(64), plan_hash: 'b'.repeat(64), reason: '合成报损入库', checked_at: f.when(4), ledger_cursor: 10, origin,
    lines: [{ receipt_line_id: f.id(22), shipment_line_id: f.id(8), source_account_id: f.id(31), target_account_id: f.id(32),
      material_id: f.id(9), condition_code: 'new', lot_id: null, accepted_qty: '1.000', serial_ids: [] }] }
  const expected = { ...f.expected, receiptId: f.id(21) }
  assert.equal(inbound.validatePreview(raw, expected).lines[0].condition_code, 'new')
  const missing = { ...raw }; delete missing.origin
  assert.throws(() => inbound.validatePreview(missing, expected))
  assert.throws(() => inbound.validatePreview({ ...raw, origin: { origin_kind: 'loss_report' } }, expected))
})
