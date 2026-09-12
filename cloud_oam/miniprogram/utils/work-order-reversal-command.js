// Validate the original selection, full reverse plan and immutable recovery.
const { uuid, validateWorkOrder } = require('./work-order-query-contract')
const { exact, text } = require('./work-order-replacement-command')
const { canonical, utf8 } = require('./work-order-command')
const { time } = require('./my-receipt-command')
const { sha256Hex } = require('./formal-file-upload')
const KIND = 'work_order_reversal'
const LABELS = { occupy: '投入占用', release: '释放未用物料', consume: '实际消耗', recover: '旧坏件回收', replace: '成对消耗与回收' }
function fail() { throw new Error('原冲销内容、整组反向明细或恢复结果不一致，请保留记录重新核验。') }
function hash(value) { if (typeof value !== 'string' || !/^[a-f0-9]{64}$/.test(value)) fail(); return value }
function integer(value, minimum, maximum = Number.MAX_SAFE_INTEGER) {
  if (!Number.isSafeInteger(value) || value < minimum || value > maximum) fail(); return value
}
function list(value, minimum, maximum) { if (!Array.isArray(value) || value.length < minimum || value.length > maximum) fail(); return value }
function nullableId(value) { return value === null ? null : uuid(value) }
function original(value) {
  const operation = nullableId(value.original_operation_id), replacement = nullableId(value.original_replacement_id)
  if ((operation === null) === (replacement === null)) fail()
  return { original_operation_id: operation, original_replacement_id: replacement }
}
function reason(value) {
  if (typeof value !== 'string') fail()
  const result = value.trim()
  if (!result || [...result].length > 500 || /[\u0000-\u0008\u000b\u000c\u000e-\u001f]/.test(result)) fail()
  utf8(result); return result
}
function command({ workOrderId, personId, selection, reason: explanation }) {
  exact(selection, ['original_operation_id', 'original_replacement_id'])
  return { work_order_id: uuid(workOrderId), operator_person_id: uuid(personId), ...original(selection), reason: reason(explanation) }
}
function payload(input) { const result = command(input); delete result.work_order_id; return result }
function requestHash(input) { return sha256Hex(utf8(canonical(command(input)))) }
function quantity(value) {
  if (typeof value !== 'string' || !/^(0|[1-9][0-9]{0,14})\.[0-9]{3}$/.test(value)) fail()
  const n = BigInt(value.replace('.', '')); if (n <= 0n) fail(); return n
}
function context(raw, expected, timestamp) {
  const order = validateWorkOrder(raw.work_order, expected.personId)
  if (raw.schema_version !== '1.0' || raw.authorization_version !== integer(expected.authorizationVersion, 1)
    || order.work_order_id !== uuid(expected.workOrderId) || order.source_version !== expected.sourceVersion) fail()
  integer(raw.ledger_cursor, expected.ledgerCursor == null ? 0 : integer(expected.ledgerCursor, 0))
  time(timestamp); if (Date.parse(timestamp) < Date.parse(order.synced_at)) fail()
  return order
}
function validateOriginals(raw, expected) {
  exact(raw, ['schema_version', 'person_id', 'authorization_version', 'work_order', 'queried_at', 'ledger_cursor', 'items'])
  context(raw, expected, raw.queried_at)
  if (uuid(raw.person_id) !== uuid(expected.personId)) fail()
  const seen = new Set()
  return list(raw.items, 0, 1000).map(row => {
    exact(row, ['original_operation_id', 'original_replacement_id', 'original_no', 'original_type', 'posted_at',
      'operation_count', 'line_count', 'material_names', 'reversal_id'])
    const selected = original(row), paired = selected.original_replacement_id !== null
    if (!Object.prototype.hasOwnProperty.call(LABELS, row.original_type) || (row.original_type === 'replace') !== paired
      || row.operation_count !== (paired ? 2 : 1)) fail()
    const id = (paired ? 'replace:' : 'operation:') + (selected.original_replacement_id || selected.original_operation_id)
    if (seen.has(id)) fail(); seen.add(id)
    text(row.original_no, 100); time(row.posted_at)
    if (Date.parse(row.posted_at) > Date.parse(raw.queried_at)) fail()
    integer(row.line_count, row.operation_count, 100 * row.operation_count)
    list(row.material_names, 1, row.line_count).forEach(name => text(name, 1000))
    nullableId(row.reversal_id)
    return { ...row, id, label: LABELS[row.original_type], materialSummary: row.material_names.join('、'), selectable: row.reversal_id === null }
  })
}
function validatePreview(raw, expected) {
  exact(raw, ['schema_version', 'planning_status', 'operator_person_id', 'authorization_version', 'work_order', 'ledger_cursor',
    'checked_at', 'original_operation_id', 'original_replacement_id', 'reason', 'request_hash', 'plan_hash', 'children', 'replacement_pairs'])
  const order = context(raw, expected, raw.checked_at), intent = command(expected)
  if (!order.can_operate || raw.planning_status !== 'preview_only' || uuid(raw.operator_person_id) !== intent.operator_person_id
    || raw.reason !== intent.reason || raw.original_operation_id !== intent.original_operation_id
    || raw.original_replacement_id !== intent.original_replacement_id || hash(raw.request_hash) !== requestHash(expected)) fail()
  hash(raw.plan_hash)
  const paired = intent.original_replacement_id !== null
  const operations = new Set(), transactions = new Set(), movements = new Set(), serials = new Set()
  const children = list(raw.children, paired ? 2 : 1, paired ? 2 : 1)
  const installed = new Set(), removed = new Set()
  for (const [index, child] of children.entries()) {
    exact(child, ['original_operation_id', 'original_operation_no', 'original_operation_type', 'original_transaction_id', 'original_ledger_cursor', 'movements'])
    const op = uuid(child.original_operation_id), tx = uuid(child.original_transaction_id), kind = child.original_operation_type
    if (operations.has(op) || transactions.has(tx) || !['occupy', 'consume', 'release', 'recover'].includes(kind)
      || (paired ? kind !== ['recover', 'consume'][index] : op !== intent.original_operation_id)) fail()
    operations.add(op); transactions.add(tx); text(child.original_operation_no, 100)
    integer(child.original_ledger_cursor, 1, raw.ledger_cursor)
    for (const [position, row] of list(child.movements, 1, 100).entries()) {
      exact(row, ['original_movement_id', 'line_no', 'material_id', 'sku_code', 'material_name', 'base_unit', 'condition_code', 'lot_id', 'lot_no',
        'from_account_id', 'to_account_id', 'from_bucket', 'to_bucket', 'quantity', 'reservation_delta', 'serials'])
      const movement = uuid(row.original_movement_id)
      if (movements.has(movement) || row.line_no !== position + 1) fail(); movements.add(movement)
      uuid(row.material_id); text(row.sku_code, 80); text(row.material_name, 1000); text(row.base_unit, 100)
      if (!['new', 'used', 'damaged'].includes(row.condition_code)) fail()
      if (nullableId(row.lot_id) === null) { if (row.lot_no !== null) fail() } else text(row.lot_no, 160)
      const from = nullableId(row.from_account_id), to = nullableId(row.to_account_id), qty = quantity(row.quantity)
      const buckets = { occupy: ['reserved', 'available'], release: ['available', 'reserved'], consume: [null, 'reserved'], recover: ['available', null] }[kind]
      if (row.from_bucket !== buckets[0] || row.to_bucket !== buckets[1]
        || (from === null) !== (row.from_bucket === null) || (to === null) !== (row.to_bucket === null) || from === to
        || row.reservation_delta !== (kind === 'occupy' ? '-' + row.quantity : kind === 'recover' ? '0.000' : row.quantity)
        || (kind === 'recover' && !['used', 'damaged'].includes(row.condition_code))) fail()
      const sns = list(row.serials, 0, 1000)
      if (sns.length && BigInt(sns.length) * 1000n !== qty) fail()
      for (const sn of sns) {
        exact(sn, ['serial_id', 'serial_no', 'lifecycle_before', 'lifecycle_after', 'previous_movement_id', 'previous_ledger_cursor', 'registration_id'])
        const id = uuid(sn.serial_id); text(sn.serial_no, 200)
        if (serials.has(id)) fail(); serials.add(id)
        nullableId(sn.registration_id); const previous = nullableId(sn.previous_movement_id)
        integer(sn.previous_ledger_cursor, 0, child.original_ledger_cursor - 1)
        if ((previous === null) !== (sn.previous_ledger_cursor === 0)
          || sn.lifecycle_before !== (kind === 'consume' ? 'consumed' : 'active')
          || !['active', 'consumed'].includes(sn.lifecycle_after)
          || (kind !== 'recover' && sn.lifecycle_after !== 'active')) fail()
        if (kind === 'consume') installed.add(id)
        if (kind === 'recover') removed.add(id)
      }
    }
  }
  const pairedInstalled = new Set(), pairedRemoved = new Set()
  for (const pair of list(raw.replacement_pairs, 0, 1000)) {
    exact(pair, ['installed_serial_id', 'removed_serial_id'])
    const put = uuid(pair.installed_serial_id), take = uuid(pair.removed_serial_id)
    if (!paired || !installed.has(put) || !removed.has(take) || pairedInstalled.has(put) || pairedRemoved.has(take) || put === take) fail()
    pairedInstalled.add(put); pairedRemoved.add(take)
  }
  if (paired && (pairedInstalled.size !== installed.size || pairedRemoved.size !== removed.size)) fail()
  return raw
}
function validateResult(raw, marker) {
  exact(raw, ['schema_version', 'status', 'reversal_id', 'reversal_no', 'work_order_id', 'operator_person_id', 'request_id', 'request_hash',
    'plan_hash', 'original_operation_id', 'original_replacement_id', 'reason', 'posted_at', 'items'])
  if (marker.kind !== KIND || marker.operation_type !== 'reverse' || raw.schema_version !== '1.0' || raw.status !== 'posted'
    || uuid(raw.work_order_id) !== uuid(marker.work_order_id) || uuid(raw.operator_person_id) !== uuid(marker.person_id)
    || raw.request_id !== marker.trace_request_id || !/^wxreq-[a-f0-9]{36}$/.test(raw.request_id)
    || hash(raw.request_hash) !== hash(marker.request_hash) || hash(raw.plan_hash) !== hash(marker.plan_hash)) fail()
  const selection = original(raw), paired = selection.original_replacement_id !== null
  if (requestHash({ workOrderId: raw.work_order_id, personId: raw.operator_person_id, selection, reason: raw.reason }) !== raw.request_hash
    || raw.reason !== reason(raw.reason)) fail()
  uuid(raw.reversal_id); text(raw.reversal_no, 100); time(raw.posted_at)
  const ops = new Set(), txs = new Set()
  for (const row of list(raw.items, paired ? 2 : 1, paired ? 2 : 1)) {
    exact(row, ['original_operation_id', 'original_transaction_id', 'inverse_operation_id', 'inverse_transaction_id'])
    if (!paired && uuid(row.original_operation_id) !== selection.original_operation_id) fail()
    for (const field of ['original_operation_id', 'inverse_operation_id']) {
      const id = uuid(row[field]); if (ops.has(id)) fail(); ops.add(id)
    }
    for (const field of ['original_transaction_id', 'inverse_transaction_id']) {
      const id = uuid(row[field]); if (txs.has(id)) fail(); txs.add(id)
    }
  }
  return raw
}
function validateLookup(raw, marker) {
  if (!raw || raw.lookup_status !== 'sealed_not_executed') return validateResult(raw, marker)
  exact(raw, ['schema_version', 'lookup_status', 'command', 'seal'])
  exact(raw.seal, ['seal_id', 'work_order_id', 'operator_person_id', 'operation_type', 'request_id', 'request_hash', 'sealed_at'])
  const seal = raw.seal
  if (raw.schema_version !== '1.0' || raw.command !== null || marker.kind !== KIND || marker.operation_type !== 'reverse'
    || seal.operation_type !== 'reverse' || uuid(seal.work_order_id) !== uuid(marker.work_order_id)
    || uuid(seal.operator_person_id) !== uuid(marker.person_id) || seal.request_id !== marker.trace_request_id
    || !/^wxreq-[a-f0-9]{36}$/.test(seal.request_id) || hash(seal.request_hash) !== hash(marker.request_hash)) fail()
  hash(marker.plan_hash); uuid(seal.seal_id); time(seal.sealed_at); return raw
}
module.exports = { KIND, LABELS, command, payload, requestHash, validateOriginals, validatePreview, validateResult, validateLookup }
