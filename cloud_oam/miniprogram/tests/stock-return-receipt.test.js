const test = require('node:test')
const assert = require('node:assert/strict')
const contract = require('../utils/stock-return-receipt-contract')
const { createStore, validateMarker } = require('../utils/work-order-recovery-store')
const f = require('./fixtures/stock-return-receiving-data.cjs')

test('return receipt payload keeps acceptance separate from posting and hashes the exact parcel', () => {
  const history = f.history(false)
  const id = f.id(8)
  const drafts = { [id]: { accepted_qty: '0.375', rejected_qty: '0.000', shortage_qty: '0.000', damaged_qty: '0.000', serials: {}, proofs: {}, damaged_serial_ids: [], exceptions: {} } }
  const lines = contract.buildLines(history, drafts)
  const payload = contract.payload({ operator_person_id: f.id(1), received_at: '2026-09-13T02:00:00.000Z', reason: '退回件逐项核验', lines }, f.id(2))
  assert.equal(payload.lines[0].accepted_qty, '0.375')
  assert.equal(contract.requestHash(f.id(2), payload).length, 64)
  assert.equal(Object.hasOwn(payload, 'posting_transaction_id'), false)
})

test('accepting a tracked serial requires an in-memory three-code proof', () => {
  const history = f.history(true), line = history.package.lines[0], id = line.shipment_line_id, serial = line.serials[0]
  const drafts = { [id]: { accepted_qty: '0.000', rejected_qty: '0.000', shortage_qty: '0.000', damaged_qty: '0.000',
    serials: { [serial.serial_id]: 'accepted' }, proofs: { [serial.serial_id]: { serial_id: serial.serial_id, sku_code: 'TEST-SKU', serial_no: serial.serial_no, qr_code: 'QR-TEST' } }, damaged_serial_ids: [], exceptions: {} } }
  const built = contract.buildLines(history, drafts)
  assert.equal(built[0].accepted_serial_verifications[0].qr_code, 'QR-TEST')
  assert.throws(() => contract.buildLines(history, { [id]: { ...drafts[id], proofs: {} } }), /三码核验/)
})

test('receiving recovery marker is scoped to exact shipment', () => {
  const marker = validateMarker({ v: 1, kind: 'stock_return', work_order_id: f.id(4), shipment_id: f.id(2), person_id: f.id(1), authorization_version: 1,
    operation_type: 'receive_return', operation_id: f.id(3), trace_request_id: `wxreq-${'1'.repeat(36)}`, request_hash: 'a'.repeat(64), plan_hash: 'b'.repeat(64) })
  const storage = { values: new Map(), getStorageInfoSync() { return { keys: [...this.values.keys()] } }, getStorageSync(k) { return this.values.get(k) || '' }, setStorageSync(k, v) { this.values.set(k, v) }, removeStorageSync(k) { this.values.delete(k) } }
  const store = createStore({ storage, state: { active: new Set(), faults: new Set() } })
  return store.withLease({ work_order_id: f.id(4), shipment_id: f.id(2) }, async lease => { lease.persist(marker); assert.equal(lease.read().value.shipment_id, f.id(2)); lease.clearExact(marker) })
})

test('preview response is bound to the exact submitted lines and serials', () => {
  const history = f.history(true), line = history.package.lines[0]
  const input = { operator_person_id: f.id(1), received_at: '2026-09-13T02:00:00.000Z', reason: '逐项验收', lines: [{
    shipment_line_id: line.shipment_line_id, accepted_qty: '1.000', rejected_qty: '0.000', damaged_qty: '0.000', shortage_qty: '0.000',
    accepted_serial_verifications: [{ serial_id: line.serials[0].serial_id, sku_code: line.sku_code, serial_no: line.serials[0].serial_no, qr_code: 'PRIVATE-QR' }],
    damaged_serial_ids: [], rejected_serial_ids: [], shortage_serial_ids: [], exceptions: []
  }] }
  const raw = { schema_version: '1.0', planning_status: 'preview_only', shipment_id: f.id(2), operation_id: f.id(3), work_order_id: f.id(4),
    operator_person_id: f.id(1), authorization_version: 1, received_at: input.received_at, reason: input.reason, checked_at: input.received_at,
    ledger_cursor: 10, package: history.package, request_hash: contract.requestHash(f.id(2), input), plan_hash: 'b'.repeat(64), lines: [{
      shipment_line_id: line.shipment_line_id, material_id: line.material_id, sku_code: line.sku_code, material_name: line.material_name,
      base_unit: line.base_unit, condition_code: line.condition_code, lot_id: null, lot_no: null, shipped_qty: '1.000',
      previously_accepted_qty: '0.000', previously_rejected_qty: '0.000', unconfirmed_qty: '1.000', accepted_qty: '1.000', rejected_qty: '0.000',
      damaged_qty: '0.000', shortage_qty: '0.000', accepted_serials: [{ serial_id: line.serials[0].serial_id, serial_no: line.serials[0].serial_no }],
      damaged_serial_ids: [], rejected_serials: [], shortage_serials: [], exceptions: []
    }] }
  assert.doesNotThrow(() => contract.validatePreview(raw, input, history, f.id(2)))
  assert.throws(() => contract.validatePreview({ ...raw, lines: [{ ...raw.lines[0], accepted_qty: '0.000' }] }, input, history, f.id(2)))
})
