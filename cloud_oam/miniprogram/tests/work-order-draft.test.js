const test = require('node:test')
const assert = require('node:assert/strict')
const { buildDraft, addScanned, validatePreview } = require('../utils/work-order-draft')
const { requestHash } = require('../utils/work-order-command')
const PERSON = '10000000-0000-4000-8000-000000000001'
const ORDER = '20000000-0000-4000-8000-000000000001'
const ACCOUNT = '40000000-0000-4000-8000-000000000001'
const TARGET = '40000000-0000-4000-8000-000000000002'
const MATERIAL = '50000000-0000-4000-8000-000000000001'
const SERIAL = '60000000-0000-4000-8000-000000000001'
const clone = raw => JSON.parse(JSON.stringify(raw))
function options() {
  return { workOrder: { work_order_id: ORDER, engineer_person_id: PERSON, can_operate: true }, personId: PERSON, kind: 'release', items: [{
    stock_account_id: ACCOUNT, material_id: MATERIAL, condition_code: 'new', tracking_mode: 'none', sku_code: 'SKU-TEST', serials: [],
    allowed_actions: ['consume', 'release', 'replace'], selectable_quantity: '900719925474099.999', release_target_stock_account_id: TARGET
  }], drafts: { [ACCOUNT]: { quantity: '900719925474099.998', serial_verifications: [] } } }
}
test('release draft retains exact decimals and uses server-derived target even when absent from positive stock', () => {
  const lines = buildDraft(options())
  assert.equal(lines[0].quantity, '900719925474099.998'); assert.equal(lines[0].target_stock_account_id, TARGET)
  const consume = options(); consume.kind = 'consume'
  assert.equal('target_stock_account_id' in buildDraft(consume)[0], false)
})
test('draft does not silently omit invalid later rows, over-limit quantities, lost scope or missing input', () => {
  for (const change of [r => { r.drafts[TARGET] = { quantity: '1' } }, r => { r.drafts[ACCOUNT].quantity = '' },
    r => { r.drafts[ACCOUNT].quantity = '1000000000000000' }, r => { r.items[0].selectable_quantity = '1.000' },
    r => { r.workOrder.can_operate = false }, r => { r.workOrder.engineer_person_id = TARGET },
    r => { r.kind = 'occupy' }, r => { r.items[0].release_target_stock_account_id = null }, r => { r.drafts = {} }]) {
    const value = options(); change(value); assert.throws(() => buildDraft(value))
  }
})
test('each physical code is required and one serial cannot be collected twice', () => {
  const item = options().items[0]
  Object.assign(item, { tracking_mode: 'serial', serials: [{ serial_id: SERIAL, serial_no: 'SN-PHYSICAL' }] })
  const scanned = { sku_code: 'SKU-TEST', serial_no: 'SN-PHYSICAL', qr_code: 'QR-PHYSICAL' }
  for (const key of Object.keys(scanned)) { const incomplete = { ...scanned }; delete incomplete[key]; assert.throws(() => addScanned(item, [], incomplete)) }
  assert.throws(() => addScanned(item, [], { ...scanned, sku_code: 'OTHER' }))
  assert.throws(() => addScanned(item, [], { ...scanned, serial_no: 'OTHER' }))
  const proofs = addScanned(item, [], scanned)
  assert.throws(() => addScanned(item, proofs, scanned))
  const value = options(); value.items[0] = item; value.drafts[ACCOUNT] = { quantity: '999', serial_verifications: proofs }
  const result = buildDraft(value)
  assert.equal(result[0].quantity, '1.000'); assert.equal(result[0].serial_verifications[0].qr_code, 'QR-PHYSICAL')
})
test('preview proof is bound to exact command, current identity, source and ledger', () => {
  const lines = buildDraft(options())
  const expected = { workOrderId: ORDER, personId: PERSON, authorizationVersion: 7, kind: 'release', lines,
    sourceVersion: 'wo-v2:test', ledgerCursor: 8 }
  const raw = { schema_version: '1.0', status: 'batch_validated', work_order_id: ORDER, operator_person_id: PERSON,
    authorization_version: 7, operation_type: 'release', source_version: 'wo-v2:test', ledger_cursor: 8,
    checked_at: '2026-09-12T08:00:00Z', line_count: 1, request_hash: requestHash('release', ORDER, PERSON, lines) }
  assert.equal(validatePreview(raw, expected), raw)
  for (const change of [r => { r.request_hash = 'a'.repeat(64) }, r => { r.status = 'posted' }, r => { r.work_order_id = TARGET },
    r => { r.operator_person_id = TARGET }, r => { r.operation_type = 'consume' }, r => { r.authorization_version++ },
    r => { r.source_version = 'other' }, r => { r.ledger_cursor-- }, r => { r.ledger_cursor = '8' },
    r => { r.checked_at = 'invalid' }, r => { r.line_count++ }, r => { r.idempotency_key = 'unexpected' }]) {
    const value = clone(raw); change(value); assert.throws(() => validatePreview(value, expected))
  }
  const changed = clone(expected); changed.lines[0].quantity = '1.000'
  assert.throws(() => validatePreview(raw, changed))
})
