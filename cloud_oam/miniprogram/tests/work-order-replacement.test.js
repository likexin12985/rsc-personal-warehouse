const test = require('node:test')
const assert = require('node:assert/strict')
const { createHash } = require('node:crypto')
const replacement = require('../utils/work-order-replacement-command')
const removed = require('../utils/work-order-removed-scan')
const ordinary = require('../utils/work-order-command')
const clone = value => JSON.parse(JSON.stringify(value))
const id = n => `abcdef00-0000-4000-8000-${String(n).padStart(12, '0')}`
const proof = n => ({ serial_id: id(n), sku_code: '配件-SKU', serial_no: `SN-${n}`, qr_code: `二维码-🔧-${n}` })
const consume = (n, serials = []) => ({ material_id: id(10), stock_account_id: id(n), quantity: serials.length ? String(serials.length) : '1.125',
  condition_before: 'new', serial_ids: serials.map(id), serial_verifications: serials.map(proof) })
const recover = (n, serials = []) => ({ basis_stock_account_id: id(n), material_id: id(11), quantity: serials.length ? String(serials.length) : '1.125',
  condition_before: 'damaged', lot_id: null, target_stock_account_id: null, serial_ids: serials.map(id), serial_verifications: serials.map(proof) })
function input(tracked = false) {
  return { workOrderId: id(1), personId: id(2), consumeLines: [consume(3, tracked ? [20, 21] : [])],
    recoverLines: [recover(3, tracked ? [31, 30] : [])], pairs: tracked ? [
      { installed_serial_id: id(21), removed_serial_id: id(31) }, { installed_serial_id: id(20), removed_serial_id: id(30) }] : [] }
}
function preview(value) {
  return { schema_version: '1.0', status: 'batch_validated', work_order_id: value.workOrderId, operator_person_id: value.personId,
    authorization_version: 7, source_version: 'source-v2', ledger_cursor: 19, checked_at: '2026-09-12T12:00:00.123456Z',
    consume_line_count: value.consumeLines.length, recover_line_count: value.recoverLines.length,
    pair_count: value.pairs.length, request_hash: replacement.requestHash(value) }
}
function expected(value = input()) { return { ...value, authorizationVersion: 7, sourceVersion: 'source-v2', ledgerCursor: 18 } }
function scanFixture(mode = 'none') {
  const tracked = ['serial', 'lot_and_serial'].includes(mode), lot = ['lot', 'lot_and_serial'].includes(mode)
  const scan = { operator_person_id: id(2), basis_stock_account_id: id(3), condition_before: 'damaged', sku_code: '配件-SKU',
    lot_no: lot && !tracked ? 'LOT-001' : null, serial_no: tracked ? 'SN-30' : null, qr_code: tracked ? proof(30).qr_code : null }
  const raw = { schema_version: '1.0', work_order_id: id(1), operator_person_id: id(2), authorization_version: 7,
    source_version: 'source-v2', ledger_cursor: 19, checked_at: '2026-09-12T12:00:00Z', basis_stock_account_id: id(3),
    material_id: id(11), sku_code: scan.sku_code, material_name: '拆回配件', base_unit: '件', condition_before: scan.condition_before,
    tracking_mode: mode, quantity_scale: tracked ? 0 : 3, allow_fraction: !tracked,
    lot_id: lot ? id(12) : null, lot_no: lot ? 'LOT-001' : null, serial_id: tracked ? id(30) : null, serial_no: scan.serial_no }
  return { raw, expected: { ...expected(), scan } }
}

test('paired canonical quantity is exact, parent hash covers both sides and payload excludes write coordinates', () => {
  const value = input(); value.recoverLines[0].quantity = '900719925474099.999'
  const before = clone(value), command = replacement.command(value), body = replacement.payload(value)
  assert.equal(command.recover_lines[0].quantity, '900719925474099.999')
  assert.equal(command.consume_lines[0].target_stock_account_id, null)
  assert.equal('target_stock_account_id' in body.consume_lines[0], false)
  assert.equal('work_order_id' in body, false); assert.equal('request_id' in body, false); assert.equal('idempotency_key' in body, false)
  assert.equal(replacement.requestHash(value), createHash('sha256').update(ordinary.canonical(command), 'utf8').digest('hex'))
  assert.notEqual(replacement.requestHash(value), ordinary.requestHash('consume', value.workOrderId, value.personId, value.consumeLines))
  assert.deepEqual(value, before)
  body.recover_lines[0].serial_ids.push(id(50)); assert.deepEqual(value, before)
  assert.deepEqual(ordinary.KINDS, ['occupy', 'consume', 'release'])
})
test('paired serial normalization preserves physical Unicode, sorts proofs and pairs without sorting line order', () => {
  const value = input(true), shuffled = clone(value)
  // Independently produced by Python replacement_request_payload/_hash.
  assert.equal(replacement.requestHash(value), 'e07fbb790d016a2a886d232da88dbef4b7ab431c3dc8f653246ac2f242253439')
  shuffled.consumeLines[0].serial_ids.reverse(); shuffled.consumeLines[0].serial_verifications.reverse()
  shuffled.recoverLines[0].serial_ids.reverse(); shuffled.recoverLines[0].serial_verifications.reverse(); shuffled.pairs.reverse()
  assert.equal(replacement.requestHash(value), replacement.requestHash(shuffled))
  const command = replacement.command(value)
  assert.equal(command.recover_lines[0].serial_verifications[0].qr_code, proof(30).qr_code)
  assert.equal(replacement.requestHash(value), createHash('sha256').update(ordinary.canonical(command), 'utf8').digest('hex'))
  const upper = clone(value); upper.workOrderId = upper.workOrderId.toUpperCase()
  assert.equal(replacement.requestHash(upper), replacement.requestHash(value))
  const multiple = input(); multiple.consumeLines.push(consume(4)); multiple.recoverLines.push(recover(4))
  const reordered = clone(multiple); reordered.consumeLines.reverse()
  assert.notEqual(replacement.requestHash(multiple), replacement.requestHash(reordered))
})
test('whole paired command rejects omitted recovery, foreign basis, targets, bad precision and duplicate dimensions', () => {
  for (const change of [v => { v.recoverLines = [] }, v => { v.recoverLines[0].basis_stock_account_id = id(9) },
    v => { v.consumeLines.push(consume(4)) }, v => { v.recoverLines.push(clone(v.recoverLines[0])) },
    v => { v.recoverLines[0].condition_before = 'new' }, v => { v.consumeLines[0].target_stock_account_id = null },
    v => { v.recoverLines[0].quantity = '0' }, v => { v.recoverLines[0].quantity = '1.1234' },
    v => { v.recoverLines[0].quantity = '1e3' }, v => { v.recoverLines[0].quantity = 1 },
    v => { v.recoverLines[0].lot_id = 'LOT' }, v => { v.recoverLines[0].target_stock_account_id = 'missing' },
    v => { v.recoverLines[0].request_id = 'extra' }, v => { v.pairs = null }, v => { v.consumeLines = [] },
    v => { v.recoverLines = Array.from({ length: 101 }, () => recover(3)) }]) {
    const value = input(); change(value); assert.throws(() => replacement.command(value))
  }
})
test('every SN pair must be complete, distinct and bound to its explicit original consume line', () => {
  for (const change of [v => { v.pairs.pop() }, v => { v.pairs.push(v.pairs[0]) },
    v => { v.pairs[0].removed_serial_id = id(30) }, v => { v.pairs[0].installed_serial_id = id(99) },
    v => { v.pairs[0].removed_serial_id = id(99) }, v => { v.pairs[0].extra = true },
    v => { v.recoverLines[0].serial_ids[0] = id(20); v.recoverLines[0].serial_verifications[0] = proof(20) },
    v => { v.recoverLines[0].serial_verifications.pop() }, v => { v.recoverLines[0].serial_verifications[0].qr_code = '' },
    v => { v.recoverLines[0].serial_verifications[0].qr_code = '\ud800' },
    v => { v.recoverLines[0].serial_verifications[0].qr_code = 'x\n' }, v => { v.recoverLines[0].quantity = '1' },
    v => { v.recoverLines.push({ ...recover(3, [30]), condition_before: 'used' }) }]) {
    const value = input(true); change(value); assert.throws(() => replacement.command(value))
  }
  const value = input(true); value.consumeLines.push(consume(4, [22])); value.recoverLines.push(recover(4, [32]))
  value.pairs.push({ installed_serial_id: id(22), removed_serial_id: id(32) })
  replacement.command(value)
  value.pairs[0].removed_serial_id = id(32); value.pairs[2].removed_serial_id = id(31)
  assert.throws(() => replacement.command(value))
})
test('preview is bound to both sets of lines, pairs, identity, authorization, source and inventory cursor', () => {
  const value = input(true), raw = preview(value), context = expected(value)
  assert.equal(replacement.validatePreview(raw, context), raw)
  for (const change of [v => { v.status = 'posted' }, v => { v.schema_version = '2.0' }, v => { v.work_order_id = id(9) },
    v => { v.operator_person_id = id(9) }, v => { v.authorization_version++ }, v => { v.source_version = 'old' },
    v => { v.ledger_cursor = 17 }, v => { v.ledger_cursor = '19' }, v => { v.checked_at = 'bad' },
    v => { v.consume_line_count++ }, v => { v.recover_line_count++ }, v => { v.pair_count++ },
    v => { v.request_hash = 'a'.repeat(64) }, v => { v.qr_code = 'leak' }]) {
    const copy = clone(raw); change(copy); assert.throws(() => replacement.validatePreview(copy, context))
  }
  for (const change of [v => { v.recoverLines[0].condition_before = 'used' }, v => { v.recoverLines[0].lot_id = id(12) },
    v => { v.recoverLines[0].serial_verifications[0].qr_code += '-changed' }, v => { v.consumeLines[0].condition_before = 'used' },
    v => { v.pairs.reverse(); [v.pairs[0].removed_serial_id, v.pairs[1].removed_serial_id] = [v.pairs[1].removed_serial_id, v.pairs[0].removed_serial_id] },
    v => { v.authorizationVersion = NaN }, v => { v.ledgerCursor = 0 }]) {
    const copy = clone(context); change(copy); assert.throws(() => replacement.validatePreview(raw, copy))
  }
})
for (const mode of ['none', 'lot', 'serial', 'lot_and_serial']) {
  test(`exact removed scan and recovery line support ${mode} without obtaining QR from server`, () => {
    const fixture = scanFixture(mode), { raw, expected } = fixture
    assert.equal(removed.validateRemoved(raw, expected), raw)
    const tracked = mode.includes('serial'), line = removed.recoveryLine(raw, expected, tracked ? '1' : '1.125')
    assert.equal(line.target_stock_account_id, null); assert.equal(line.quantity, tracked ? '1.000' : '1.125')
    assert.equal('qr_code' in raw, false)
    assert.deepEqual(line.serial_ids, tracked ? [id(30)] : [])
    if (tracked) assert.equal(line.serial_verifications[0].qr_code, expected.scan.qr_code)
    else assert.deepEqual(line.serial_verifications, [])
  })
}
test('removed scan rejects stale identity, basis, SKU, condition, policy, missing fields and injected QR', () => {
  const { raw, expected } = scanFixture('lot_and_serial')
  for (const change of [v => { v.operator_person_id = id(9) }, v => { v.work_order_id = id(9) },
    v => { v.basis_stock_account_id = id(4) }, v => { v.sku_code += '-other' }, v => { v.condition_before = 'used' },
    v => { v.authorization_version++ }, v => { v.source_version = 'old' }, v => { v.ledger_cursor = 17 },
    v => { v.tracking_mode = 'unknown' }, v => { v.quantity_scale = 4 }, v => { v.quantity_scale = 1.5 },
    v => { v.allow_fraction = 'false' }, v => { v.serial_id = null }, v => { v.serial_no = 'other' },
    v => { v.lot_id = null }, v => { v.lot_no = null }, v => { v.qr_code = 'injected' }, v => { delete v.material_id },
    v => { v.material_name = '' }, v => { v.base_unit = '' }, v => { v.checked_at = 'bad' }]) {
    const copy = clone(raw); change(copy); assert.throws(() => removed.validateRemoved(copy, expected))
  }
  for (const change of [v => { v.scan.operator_person_id = id(9) }, v => { v.scan.serial_no = null },
    v => { v.scan.qr_code = null }, v => { v.scan.lot_no = 'wrong' }, v => { v.scan.sku_code = 'wrong' },
    v => { v.scan.target_stock_account_id = id(8) }, v => { v.scan.qr_code = '\udfff' }]) {
    const copy = clone(expected); change(copy); assert.throws(() => removed.validateRemoved(raw, copy))
  }
})
test('scan policy does not silently drop unexpected serial/lot bindings or round quantity', () => {
  for (const mode of ['none', 'lot', 'serial']) {
    const { raw, expected } = scanFixture(mode)
    const copy = clone(raw)
    if (mode === 'serial') { copy.lot_id = id(12); copy.lot_no = 'LOT' }
    else { copy.serial_id = id(30); copy.serial_no = 'SN-30' }
    assert.throws(() => removed.validateRemoved(copy, expected))
  }
  const lot = scanFixture('lot'); lot.expected.scan.lot_no = null
  assert.throws(() => removed.validateRemoved(lot.raw, lot.expected))
  const serial = scanFixture('serial')
  for (const value of ['0', '0.001', '2', '1.0001', 1, '1e0']) assert.throws(() => removed.recoveryLine(serial.raw, serial.expected, value))
  const quantity = scanFixture(); quantity.raw.quantity_scale = 2
  assert.throws(() => removed.recoveryLine(quantity.raw, quantity.expected, '1.125'))
  quantity.raw.quantity_scale = 3; quantity.raw.allow_fraction = false
  assert.throws(() => removed.recoveryLine(quantity.raw, quantity.expected, '1.125'))
  assert.equal(removed.recoveryLine(quantity.raw, quantity.expected, '900719925474099').quantity, '900719925474099.000')
})
test('only exact original GET proof confirms both operations; POST output or unknown result cannot confirm', () => {
  const marker = { kind: 'work_order_replacement', operation_type: 'replace', work_order_id: id(1), person_id: id(2),
    trace_request_id: 'wxreq-' + 'a'.repeat(36), request_hash: replacement.requestHash(input(true)) }
  const raw = { schema_version: '1.0', replacement_id: id(50), replacement_no: 'WR-001', work_order_id: id(1),
    consume_operation_id: id(51), recover_operation_id: id(52), consume_transaction_id: id(53), recover_transaction_id: id(54),
    status: 'posted', operator_person_id: id(2), request_id: marker.trace_request_id, request_hash: marker.request_hash }
  assert.equal(replacement.validateResult(raw, marker), raw)
  for (const change of [v => { delete v.request_hash }, v => { delete v.operator_person_id }, v => { delete v.request_id },
    v => { v.consume_transaction_id = v.recover_transaction_id }, v => { v.consume_operation_id = v.recover_operation_id },
    v => { v.work_order_id = id(9) }, v => { v.operator_person_id = id(9) }, v => { v.request_id += 'b' },
    v => { v.request_hash = 'b'.repeat(64) }, v => { v.status = 'pending' }, v => { v.replacement_no = '' },
    v => { v.replacement_id = '' }, v => { v.ignored = true }]) {
    const copy = clone(raw); change(copy); assert.throws(() => replacement.validateResult(copy, marker))
  }
  for (const value of [null, {}, { lookup_status: 'not_observed', command: null }]) assert.throws(() => replacement.validateResult(value, marker))
  assert.throws(() => replacement.validateResult(raw, { ...marker, operation_type: 'consume' }))
  assert.throws(() => replacement.validateResult(raw, { ...marker, kind: 'work_order_material' }))
})
test('sealed parent proof requires every original coordinate and never accepts an ordinary child seal', () => {
  const marker = { kind: 'work_order_replacement', operation_type: 'replace', work_order_id: id(1), person_id: id(2),
    trace_request_id: 'wxreq-' + 'a'.repeat(36), request_hash: replacement.requestHash(input()) }
  const raw = { schema_version: '1.0', lookup_status: 'sealed_not_executed', command: null, seal: {
    seal_id: id(50), work_order_id: id(1), operator_person_id: id(2), operation_type: 'replace',
    request_id: marker.trace_request_id, request_hash: marker.request_hash, sealed_at: '2026-09-13T00:00:00.123456Z' } }
  assert.equal(replacement.validateLookup(raw, marker), raw)
  for (const change of [v => { v.seal.work_order_id = id(9) }, v => { v.seal.operator_person_id = id(9) },
    v => { v.seal.operation_type = 'consume' }, v => { v.seal.request_id += 'b' }, v => { v.seal.request_hash = 'b'.repeat(64) },
    v => { v.seal.sealed_at = 'bad' }, v => { v.seal.seal_id = '' }, v => { v.command = {} },
    v => { v.schema_version = '2.0' }, v => { v.lookup_status = 'not_observed' }, v => { v.seal.extra = true }]) {
    const copy = clone(raw); change(copy); assert.throws(() => replacement.validateLookup(copy, marker))
  }
  assert.throws(() => replacement.validateLookup(raw, { ...marker, kind: 'work_order_material' }))
})
