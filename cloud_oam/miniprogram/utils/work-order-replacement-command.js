// One parent digest binds both stock operations and every physical SN pair.
const { uuid } = require('./work-order-query-contract')
const ordinary = require('./work-order-command')
const { time } = require('./my-receipt-command')
const { sha256Hex } = require('./formal-file-upload')

function fail(message = '成对消耗与回收内容不一致，请保留原请求并重新核验。') { throw new Error(message) }
function exact(value, fields) {
  if (!value || typeof value !== 'object' || Array.isArray(value)
    || Object.keys(value).sort().join('|') !== fields.slice().sort().join('|')) fail()
}
function text(value, limit) {
  if (typeof value !== 'string' || !value.trim() || value.length > limit || /[\u0000-\u001f\u007f]/.test(value)) fail()
  ordinary.utf8(value)
  return value
}
function list(value, limit, empty = false) {
  if (!Array.isArray(value) || (!empty && !value.length) || value.length > limit) fail()
}
function sameKeys(left, right) { return left.size === right.size && [...left.keys()].every(key => right.has(key)) }
function command({ workOrderId, personId, consumeLines, recoverLines, pairs }) {
  const consumed = ordinary.command('consume', workOrderId, personId, consumeLines)
  list(recoverLines, 100); list(pairs, 1000, true)
  const bases = new Set(consumed.lines.map(line => line.stock_account_id))
  const installed = new Map(consumed.lines.flatMap(line => line.serial_ids.map(id => [id, line.stock_account_id])))
  const removed = new Map(), recoveredBases = new Set(), targets = new Set()
  const recovered = recoverLines.map(raw => {
    const fields = ['basis_stock_account_id', 'material_id', 'quantity', 'condition_before', 'serial_ids', 'serial_verifications']
    for (const key of ['lot_id', 'target_stock_account_id']) if (Object.prototype.hasOwnProperty.call(raw, key)) fields.push(key)
    exact(raw, fields)
    const basis = uuid(raw.basis_stock_account_id)
    if (!bases.has(basis) || !['used', 'damaged'].includes(raw.condition_before)) fail('拆回件必须对应本次投入明细，并登记为旧件或坏件。')
    // Reuse exact decimal and three-code validation without inventing a destination account.
    const line = ordinary.command('consume', workOrderId, personId, [{ material_id: raw.material_id,
      stock_account_id: basis, quantity: raw.quantity, condition_before: raw.condition_before,
      serial_ids: raw.serial_ids, serial_verifications: raw.serial_verifications }]).lines[0]
    const lot = raw.lot_id == null ? null : uuid(raw.lot_id)
    const target = raw.target_stock_account_id == null ? null : uuid(raw.target_stock_account_id)
    const dimension = [basis, line.material_id, lot, line.condition_before].join('|')
    if (targets.has(dimension)) fail('同一回收目标的拆回件请合并后核验。')
    targets.add(dimension); recoveredBases.add(basis)
    for (const id of line.serial_ids) {
      if (installed.has(id) || removed.has(id)) fail('投入和拆回 SN 必须各自唯一，且不能相同。')
      removed.set(id, basis)
    }
    return { basis_stock_account_id: basis, material_id: line.material_id, lot_id: lot, target_stock_account_id: target,
      quantity: line.quantity, condition_before: line.condition_before, serial_ids: line.serial_ids, serial_verifications: line.serial_verifications }
  })
  if (!sameKeys(bases, recoveredBases)) fail('每条投入明细都必须登记对应的拆回件。')
  const pairedInstalled = new Set(), pairedRemoved = new Set()
  const normalizedPairs = pairs.map(raw => {
    exact(raw, ['installed_serial_id', 'removed_serial_id'])
    const put = uuid(raw.installed_serial_id), take = uuid(raw.removed_serial_id)
    if (!installed.has(put) || !removed.has(take) || pairedInstalled.has(put) || pairedRemoved.has(take)
      || installed.get(put) !== removed.get(take)) fail('每对新旧 SN 必须对应同一条投入明细，且不能重复。')
    pairedInstalled.add(put); pairedRemoved.add(take)
    return { installed_serial_id: put, removed_serial_id: take }
  }).sort((a, b) => a.installed_serial_id < b.installed_serial_id ? -1 : a.installed_serial_id > b.installed_serial_id ? 1 : 0)
  if (!sameKeys(installed, pairedInstalled) || !sameKeys(removed, pairedRemoved)) fail('请逐一完成全部投入与拆回 SN 的配对。')
  for (const line of consumed.lines.concat(recovered)) {
    for (const proof of line.serial_verifications) {
      text(proof.sku_code, 80); text(proof.serial_no, 200); text(proof.qr_code, 250)
    }
  }
  return { work_order_id: consumed.work_order_id, operator_person_id: consumed.operator_person_id,
    consume_lines: consumed.lines, recover_lines: recovered, replacement_pairs: normalizedPairs }
}
function requestHash(input) { return sha256Hex(ordinary.utf8(ordinary.canonical(command(input)))) }
function payload(input) {
  const result = command(input)
  delete result.work_order_id
  result.consume_lines = result.consume_lines.map(line => { const copy = { ...line }; delete copy.target_stock_account_id; return copy })
  return result
}
function context(raw, expected) {
  if (raw.schema_version !== '1.0' || uuid(raw.work_order_id) !== uuid(expected.workOrderId)
    || uuid(raw.operator_person_id) !== uuid(expected.personId)
    || !Number.isSafeInteger(expected.authorizationVersion) || expected.authorizationVersion < 1
    || raw.authorization_version !== expected.authorizationVersion || raw.source_version !== expected.sourceVersion
    || !Number.isSafeInteger(expected.ledgerCursor) || expected.ledgerCursor < 1
    || !Number.isSafeInteger(raw.ledger_cursor) || raw.ledger_cursor < expected.ledgerCursor) fail()
  text(raw.source_version, 1000); time(raw.checked_at)
}
function validatePreview(raw, expected) {
  exact(raw, ['schema_version', 'status', 'work_order_id', 'operator_person_id', 'authorization_version', 'source_version',
    'ledger_cursor', 'checked_at', 'consume_line_count', 'recover_line_count', 'pair_count', 'request_hash'])
  context(raw, expected)
  const value = command(expected)
  if (raw.status !== 'batch_validated' || raw.consume_line_count !== value.consume_lines.length
    || raw.recover_line_count !== value.recover_lines.length || raw.pair_count !== value.replacement_pairs.length
    || raw.request_hash !== requestHash(expected)) fail('整批预检与当前投入、拆回或配对内容不一致，请重新核验。')
  return raw
}
function validateResult(raw, marker) {
  exact(raw, ['schema_version', 'replacement_id', 'replacement_no', 'work_order_id', 'consume_operation_id',
    'recover_operation_id', 'consume_transaction_id', 'recover_transaction_id', 'status', 'operator_person_id', 'request_id', 'request_hash'])
  if (marker.kind !== 'work_order_replacement' || marker.operation_type !== 'replace'
    || raw.schema_version !== '1.0' || raw.status !== 'posted' || uuid(raw.work_order_id) !== uuid(marker.work_order_id)
    || uuid(raw.operator_person_id) !== uuid(marker.person_id) || raw.request_id !== marker.trace_request_id
    || typeof raw.request_id !== 'string' || !/^[A-Za-z0-9._:-]{8,160}$/.test(raw.request_id)
    || raw.request_hash !== marker.request_hash || typeof raw.request_hash !== 'string' || !/^[a-f0-9]{64}$/.test(raw.request_hash)) fail()
  uuid(raw.replacement_id); text(raw.replacement_no, 100)
  if (uuid(raw.consume_operation_id) === uuid(raw.recover_operation_id)
    || uuid(raw.consume_transaction_id) === uuid(raw.recover_transaction_id)) fail()
  return raw
}

module.exports = { command, payload, requestHash, validatePreview, validateResult, context, exact, text }
