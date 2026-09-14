const assert = require('node:assert/strict')
const test = require('node:test')
const contract = require('../utils/stock-return-inbound-contract')
const { validateMarker } = require('../utils/work-order-recovery-store')
const { originalResult } = require('../utils/work-order-recovery')

const PERSON = '10000000-0000-4000-8000-000000000001'
const ORDER = '20000000-0000-4000-8000-000000000001'
const RECEIPT = '30000000-0000-4000-8000-000000000001'
const SHIPMENT = '40000000-0000-4000-8000-000000000001'
const LOC = '50000000-0000-4000-8000-000000000001'
const CUSTODY = '60000000-0000-4000-8000-000000000001'
const LINE = '70000000-0000-4000-8000-000000000001'
const SOURCE = '80000000-0000-4000-8000-000000000001'
const TARGET = '90000000-0000-4000-8000-000000000001'
const MATERIAL = 'a0000000-0000-4000-8000-000000000001'
const TX = 'b0000000-0000-4000-8000-000000000001'
const TRACE = 'wxreq-' + 'a'.repeat(36)

function preview() {
  return { schema_version: '1.0', planning_status: 'inbound_preview_only', receipt_id: RECEIPT, shipment_id: SHIPMENT,
    operator_person_id: PERSON, authorization_version: 2, target_location_id: LOC, target_custody_assignment_id: CUSTODY,
    receipt_plan_hash: 'c'.repeat(64), plan_hash: 'd'.repeat(64), reason: '退回件入账', checked_at: '2026-09-14T01:02:03Z', ledger_cursor: 12,
    lines: [{ receipt_line_id: LINE, shipment_line_id: SHIPMENT, source_account_id: SOURCE, target_account_id: TARGET,
      material_id: MATERIAL, condition_code: 'used', lot_id: null, accepted_qty: '1.000', serial_ids: [] }] }
}

test('inbound preview is bound to the exact receipt and target account', () => {
  const raw = preview()
  const value = contract.validatePreview(raw, { receiptId: RECEIPT, shipmentId: SHIPMENT, personId: PERSON })
  assert.equal(value.lines[0].accepted_qty, '1.000')
  assert.throws(() => contract.validatePreview({ ...raw, target_location_id: 'invalid' }, { receiptId: RECEIPT, shipmentId: SHIPMENT, personId: PERSON }))
  assert.equal(contract.requestHash(RECEIPT, raw.plan_hash, TRACE).length, 64)
})

test('inbound recovery marker is distinct from acceptance and carries receipt identity', () => {
  const planHash = 'd'.repeat(64), requestHash = contract.requestHash(RECEIPT, planHash, TRACE)
  const marker = validateMarker({ v: 1, kind: contract.KIND, work_order_id: ORDER, shipment_id: SHIPMENT, receipt_id: RECEIPT,
    person_id: PERSON, authorization_version: 2, operation_type: contract.ACTION, trace_request_id: TRACE,
    request_hash: requestHash, plan_hash: planHash })
  assert.equal(marker.kind, contract.KIND); assert.equal(marker.receipt_id, RECEIPT)
  assert.throws(() => validateMarker({ ...marker, kind: 'stock_return' }))
})

test('posted and sealed lookup results require all original inbound coordinates', () => {
  const planHash = 'd'.repeat(64), marker = validateMarker({ v: 1, kind: contract.KIND, work_order_id: ORDER, shipment_id: SHIPMENT,
    receipt_id: RECEIPT, person_id: PERSON, authorization_version: 2, operation_type: contract.ACTION, trace_request_id: TRACE,
    request_hash: contract.requestHash(RECEIPT, planHash, TRACE), plan_hash: planHash })
  const posted = { schema_version: '1.0', inbound_id: 'c0000000-0000-4000-8000-000000000001', inbound_no: 'RET-IN-TEST', receipt_id: RECEIPT,
    shipment_id: SHIPMENT, target_location_id: LOC, target_custody_assignment_id: CUSTODY, status: 'posted', posting_transaction_id: TX,
    request_id: TRACE, request_hash: marker.request_hash, plan_hash: planHash, replayed: false }
  assert.equal(contract.validateLookup(posted, marker).inbound_no, 'RET-IN-TEST')
  const sealed = { schema_version: '1.0', lookup_status: 'sealed', seal: { seal_id: TX, receipt_id: RECEIPT, shipment_id: SHIPMENT,
    request_id: TRACE, request_hash: marker.request_hash, sealed_at: '2026-09-14T01:03:00Z' } }
  assert.equal(contract.validateLookup(sealed, marker).lookup_status, 'sealed')
  assert.throws(() => contract.validateLookup({ ...sealed, seal: { ...sealed.seal, receipt_id: SHIPMENT } }, marker))
})

test('inbound recovery reads the dedicated receipt route', async () => {
  const planHash = 'd'.repeat(64), marker = validateMarker({ v: 1, kind: contract.KIND, work_order_id: ORDER, shipment_id: SHIPMENT,
    receipt_id: RECEIPT, person_id: PERSON, authorization_version: 2, operation_type: contract.ACTION, trace_request_id: TRACE,
    request_hash: contract.requestHash(RECEIPT, planHash, TRACE), plan_hash: planHash })
  const calls = [], posted = { schema_version: '1.0', inbound_id: 'c0000000-0000-4000-8000-000000000001', inbound_no: 'RET-IN-TEST', receipt_id: RECEIPT,
    shipment_id: SHIPMENT, target_location_id: LOC, target_custody_assignment_id: CUSTODY, status: 'posted', posting_transaction_id: TX,
    request_id: TRACE, request_hash: marker.request_hash, plan_hash: planHash, replayed: false }
  const result = await originalResult({ request: async (path, options) => { calls.push({ path, options }); return posted } }, marker)
  assert.equal(result.inbound_no, 'RET-IN-TEST')
  assert.equal(calls[0].path, `/v1/stock-returns/my-receiving/${RECEIPT}/inbound/by-request/${TRACE}`)
  assert.equal(calls[0].options.noRefresh, true)
})
