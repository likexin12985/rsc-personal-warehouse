const { uuid, validateWorkOrder } = require('./work-order-query-contract')
const { exact, text } = require('./work-order-replacement-command')
const { canonical, utf8 } = require('./work-order-command')
const { quantity, units, fromUnits, time } = require('./my-receipt-command')
const { sha256Hex } = require('./formal-file-upload')
const KIND = 'stock_return'
const BLOCKERS = ['opening_not_established', 'source_stale', 'source_disabled', 'history_scope_unresolved', 'reversal_review_required']
function fail(message = '退回来源、接收位置或原请求结果不一致，请重新核验。') { throw new Error(message) }
function digest(value) { if (typeof value !== 'string' || !/^[a-f0-9]{64}$/.test(value)) fail(); return value }
function list(value, min = 0, max = 1000) { if (!Array.isArray(value) || value.length < min || value.length > max) fail(); return value }
function integer(value, min = 0) { if (!Number.isSafeInteger(value) || value < min) fail(); return value }
function amount(value, min = 0n) { if (typeof value !== 'string' || !/^(0|[1-9][0-9]{0,14})\.[0-9]{3}$/.test(value)) fail(); const n = units(value); if (n < min) fail(); return n }
function reason(value) {
  if (typeof value !== 'string') fail(); const result = value.trim()
  if (!result || [...result].length > 500 || /[\u0000-\u0008\u000b\u000c\u000e-\u001f]/.test(result)) fail('请填写明确的退回或取消原因。')
  utf8(result); return result
}
function requestId(value) { if (typeof value !== 'string' || !/^[A-Za-z0-9._:-]{8,160}$/.test(value)) fail(); return value }
function hash(value) { return sha256Hex(utf8(canonical(value))) }
function source(raw, person, location) {
  exact(raw, ['source_recovery_line_id', 'recovery_operation_id', 'recovery_operation_no', 'stock_account_id', 'owner_org_id',
    'custodian_person_id', 'location_id', 'material_id', 'sku_code', 'material_name', 'base_unit', 'condition_code', 'lot_id', 'lot_no',
    'owed_quantity', 'committed_quantity', 'available_quantity', 'selectable_quantity', 'serials'])
  for (const key of ['source_recovery_line_id', 'recovery_operation_id', 'stock_account_id', 'owner_org_id', 'material_id']) uuid(raw[key])
  if (uuid(raw.custodian_person_id) !== uuid(person) || uuid(raw.location_id) !== uuid(location) || !['used', 'damaged'].includes(raw.condition_code)) fail()
  text(raw.recovery_operation_no, 100); text(raw.sku_code, 80); text(raw.material_name, 1000); text(raw.base_unit, 100)
  if (raw.lot_id === null) { if (raw.lot_no !== null) fail() } else { uuid(raw.lot_id); text(raw.lot_no, 160) }
  const owed = amount(raw.owed_quantity, 1n), committed = amount(raw.committed_quantity), available = amount(raw.available_quantity), selectable = amount(raw.selectable_quantity)
  if (committed > owed || selectable > owed - committed || selectable > available) fail()
  const seen = new Set(); let availableSerials = 0
  for (const sn of list(raw.serials)) {
    exact(sn, ['serial_id', 'serial_no', 'selectable']); const id = uuid(sn.serial_id); text(sn.serial_no, 200)
    if (seen.has(id) || typeof sn.selectable !== 'boolean') fail(); seen.add(id)
    if (sn.selectable) availableSerials++
  }
  if (seen.size && (BigInt(seen.size) * 1000n !== owed || selectable > BigInt(availableSerials) * 1000n)) fail()
  return raw
}
function destination(raw, location) {
  exact(raw, ['source_location_id', 'target_location_id', 'target_location_code', 'target_location_name', 'transit_location_id',
    'transit_location_code', 'transit_location_name', 'region_org_id', 'custody_assignment_id', 'custodian_person_id', 'custody_effective_from'])
  if (uuid(raw.source_location_id) !== uuid(location) || new Set([raw.source_location_id, raw.target_location_id, raw.transit_location_id].map(uuid)).size !== 3) fail()
  for (const key of ['region_org_id', 'custody_assignment_id', 'custodian_person_id']) uuid(raw[key])
  for (const key of ['target_location_code', 'target_location_name', 'transit_location_code', 'transit_location_name']) text(raw[key], 1000)
  time(raw.custody_effective_from); return raw
}
function validateOptions(raw, expected) {
  exact(raw, ['schema_version', 'sources', 'destinations'])
  const row = raw.sources
  exact(row, ['schema_version', 'person_id', 'authorization_version', 'work_order', 'location_id', 'custody_effective_from',
    'ledger_cursor', 'projected_at', 'queried_at', 'blockers', 'items'])
  const order = validateWorkOrder(row.work_order, expected.personId)
  if (raw.schema_version !== '1.0' || row.schema_version !== '1.0' || uuid(row.person_id) !== uuid(expected.personId)
    || row.authorization_version !== integer(expected.authorizationVersion, 1) || order.work_order_id !== uuid(expected.workOrderId)) fail()
  integer(row.ledger_cursor); time(row.queried_at)
  if (Date.parse(row.queried_at) < Date.parse(order.synced_at)) fail()
  if (row.location_id === null) { if (row.custody_effective_from !== null || row.items.length || raw.destinations.length) fail() }
  else { uuid(row.location_id); time(row.custody_effective_from); if (Date.parse(row.custody_effective_from) > Date.parse(row.queried_at)) fail() }
  if (row.projected_at !== null) { time(row.projected_at); if (Date.parse(row.projected_at) > Date.parse(row.queried_at)) fail() }
  list(row.blockers, 0, BLOCKERS.length)
  if (row.blockers.some(code => !BLOCKERS.includes(code)) || new Set(row.blockers).size !== row.blockers.length
    || row.blockers.join('|') !== row.blockers.slice().sort().join('|') || (order.freshness === 'stale') !== row.blockers.includes('source_stale')) fail()
  const origins = new Set(), serials = new Set(), accounts = new Map()
  const items = list(row.items).map(item => {
    source(item, expected.personId, row.location_id)
    if (origins.has(item.source_recovery_line_id)) fail(); origins.add(item.source_recovery_line_id)
    for (const sn of item.serials) { if (serials.has(sn.serial_id)) fail(); serials.add(sn.serial_id) }
    const binding = canonical([item.owner_org_id, item.custodian_person_id, item.location_id, item.material_id, item.condition_code, item.lot_id, item.available_quantity])
    if (accounts.has(item.stock_account_id) && accounts.get(item.stock_account_id) !== binding) fail()
    accounts.set(item.stock_account_id, binding); return item
  })
  const routes = new Set()
  for (const route of list(raw.destinations, 0, 100)) {
    destination(route, row.location_id)
    if (row.blockers.length || routes.has(route.transit_location_id) || items.some(item => item.owner_org_id !== route.region_org_id)
      || Date.parse(route.custody_effective_from) > Date.parse(row.queried_at)) fail()
    routes.add(route.transit_location_id)
  }
  return { raw, workOrder: order, items, destinations: raw.destinations, blockers: row.blockers, ledgerCursor: row.ledger_cursor }
}
function command(input) {
  const seen = new Set(), serials = new Set()
  const lines = list(input.lines, 1, 100).map(row => {
    exact(row, ['source_recovery_line_id', 'stock_account_id', 'quantity', 'serial_verifications'])
    const id = uuid(row.source_recovery_line_id)
    if (seen.has(id)) fail(); seen.add(id)
    const proofs = list(row.serial_verifications).map(proof => {
      exact(proof, ['serial_id', 'sku_code', 'serial_no', 'qr_code']); const sn = uuid(proof.serial_id)
      if (serials.has(sn)) fail('同一 SN 只能退回一次。'); serials.add(sn)
      for (const [key, limit] of [['sku_code', 80], ['serial_no', 200], ['qr_code', 250]]) { text(proof[key], limit); utf8(proof[key]) }
      return { ...proof, serial_id: sn }
    }).sort((a, b) => a.serial_id.localeCompare(b.serial_id))
    const q = quantity(row.quantity); if (units(q) <= 0n || (proofs.length && units(q) !== BigInt(proofs.length) * 1000n)) fail()
    return { source_recovery_line_id: id, stock_account_id: uuid(row.stock_account_id), quantity: q, serial_verifications: proofs }
  }).sort((a, b) => a.source_recovery_line_id.localeCompare(b.source_recovery_line_id))
  return { operation_type: 'return', work_order_id: uuid(input.workOrderId), operator_person_id: uuid(input.personId),
    target_location_id: uuid(input.targetLocationId), transit_location_id: uuid(input.transitLocationId), reason: reason(input.reason), lines }
}
function payload(input) { const value = command(input); delete value.operation_type; delete value.work_order_id; return value }
function requestHash(input) { return hash(command(input)) }
function buildLines(choices, drafts) {
  if (choices.blockers.length) fail('退回来源尚有待核验事项。')
  const totals = new Map()
  return list(Object.entries(drafts || {}), 1, 100).map(([id, value]) => {
    const item = choices.items.find(row => row.source_recovery_line_id === uuid(id)); if (!item) fail()
    const proofs = list(value.serial_verifications), q = item.serials.length ? fromUnits(BigInt(proofs.length) * 1000n) : quantity(value.quantity)
    if (units(q) <= 0n || units(q) > amount(item.selectable_quantity) || (!item.serials.length && proofs.length)) fail('退回数量超过原回收行当前可选数量。')
    if (proofs.some(proof => proof.sku_code !== item.sku_code || !item.serials.some(sn => sn.selectable && sn.serial_id === proof.serial_id && sn.serial_no === proof.serial_no))) fail('扫码实物已不属于该原回收行。')
    const total = (totals.get(item.stock_account_id) || 0n) + units(q); totals.set(item.stock_account_id, total)
    if (total > amount(item.available_quantity)) fail('这些原回收行合计超过同一账户可用库存。')
    return { source_recovery_line_id: id, stock_account_id: item.stock_account_id, quantity: q, serial_verifications: proofs }
  })
}
function selectedLines(rows, person, location) {
  const origins = new Set(), serials = new Set(), accounts = new Map()
  for (const row of list(rows, 1, 100)) {
    exact(row, ['source', 'selected_quantity', 'selected_serials']); source(row.source, person, location)
    const id = row.source.source_recovery_line_id, q = amount(row.selected_quantity, 1n)
    if (origins.has(id) || q > amount(row.source.selectable_quantity)) fail(); origins.add(id)
    const total = (accounts.get(row.source.stock_account_id) || 0n) + q; accounts.set(row.source.stock_account_id, total)
    if (total > amount(row.source.available_quantity)) fail()
    for (const sn of list(row.selected_serials)) {
      exact(sn, ['serial_id', 'serial_no']); uuid(sn.serial_id); text(sn.serial_no, 200)
      if (serials.has(sn.serial_id) || !row.source.serials.some(item => item.selectable && item.serial_id === sn.serial_id && item.serial_no === sn.serial_no)) fail()
      serials.add(sn.serial_id)
    }
    if (row.source.serials.length ? BigInt(row.selected_serials.length) * 1000n !== q : row.selected_serials.length) fail()
  }
}
function validatePreview(raw, input, choices) {
  exact(raw, ['schema_version', 'planning_status', 'operator_person_id', 'authorization_version', 'work_order_id', 'reason',
    'ledger_cursor', 'checked_at', 'destination', 'request_hash', 'plan_hash', 'lines'])
  const value = command(input)
  if (raw.schema_version !== '1.0' || raw.planning_status !== 'preview_only' || raw.operator_person_id !== value.operator_person_id
    || raw.work_order_id !== value.work_order_id || raw.authorization_version !== integer(input.authorizationVersion, 1)
    || integer(raw.ledger_cursor) < choices.ledgerCursor || raw.reason !== value.reason || digest(raw.request_hash) !== requestHash(input)) fail()
  time(raw.checked_at); digest(raw.plan_hash)
  const route = choices.destinations.find(row => row.target_location_id === value.target_location_id && row.transit_location_id === value.transit_location_id)
  if (!route || canonical(destination(raw.destination, choices.raw.sources.location_id)) !== canonical(route)) fail()
  selectedLines(raw.lines, input.personId, choices.raw.sources.location_id)
  const resultLines = raw.lines.map(row => ({ source_recovery_line_id: row.source.source_recovery_line_id, stock_account_id: row.source.stock_account_id,
    quantity: row.selected_quantity, serials: row.selected_serials.map(sn => sn.serial_id).sort() })).sort((a, b) => a.source_recovery_line_id.localeCompare(b.source_recovery_line_id))
  if (canonical(resultLines) !== canonical(value.lines.map(row => ({ source_recovery_line_id: row.source_recovery_line_id,
    stock_account_id: row.stock_account_id, quantity: row.quantity, serials: row.serial_verifications.map(sn => sn.serial_id) })))) fail()
  return raw
}
function original(raw, expected) {
  exact(raw, ['schema_version', 'operation_id', 'operation_no', 'work_order_id', 'requester_id', 'status', 'reason', 'request_id',
    'request_hash', 'plan_hash', 'posting_transaction_id', 'submitted_at', 'destination', 'lines'])
  if (raw.schema_version !== '1.0' || raw.status !== 'submitted' || uuid(raw.work_order_id) !== uuid(expected.workOrderId)
    || uuid(raw.requester_id) !== uuid(expected.personId) || raw.reason !== reason(raw.reason)) fail()
  uuid(raw.operation_id); text(raw.operation_no, 100); uuid(raw.posting_transaction_id); time(raw.submitted_at)
  requestId(raw.request_id); digest(raw.request_hash); digest(raw.plan_hash)
  destination(raw.destination, raw.destination.source_location_id); selectedLines(raw.lines, expected.personId, raw.destination.source_location_id)
  if (raw.lines.some(row => row.source.owner_org_id !== raw.destination.region_org_id) || Date.parse(raw.destination.custody_effective_from) > Date.parse(raw.submitted_at)) fail()
  return raw
}
function cancellation(raw, expected) {
  exact(raw, ['schema_version', 'cancellation_id', 'operation_id', 'operator_person_id', 'reason', 'request_id', 'request_hash', 'status', 'posting_transaction_id', 'cancelled_at'])
  if (raw.schema_version !== '1.0' || raw.status !== 'cancelled' || uuid(raw.operation_id) !== uuid(expected.operationId)
    || uuid(raw.operator_person_id) !== uuid(expected.personId) || raw.reason !== reason(raw.reason)) fail()
  uuid(raw.cancellation_id); uuid(raw.posting_transaction_id); requestId(raw.request_id); time(raw.cancelled_at)
  if (digest(raw.request_hash) !== hash({ operation_id: raw.operation_id, operator_person_id: raw.operator_person_id, reason: raw.reason })) fail()
  return raw
}
function validateHistory(raw, expected) {
  exact(raw, ['schema_version', 'person_id', 'work_order_id', 'authorization_version', 'queried_at', 'items'])
  if (raw.schema_version !== '1.0' || uuid(raw.person_id) !== uuid(expected.personId) || uuid(raw.work_order_id) !== uuid(expected.workOrderId)
    || raw.authorization_version !== integer(expected.authorizationVersion, 1)) fail()
  time(raw.queried_at); const seen = new Set(), transactions = new Set()
  for (const row of list(raw.items, 0, 100)) {
    exact(row, ['original', 'cancellation']); original(row.original, expected)
    if (seen.has(row.original.operation_id) || transactions.has(row.original.posting_transaction_id) || Date.parse(row.original.submitted_at) > Date.parse(raw.queried_at)) fail()
    seen.add(row.original.operation_id); transactions.add(row.original.posting_transaction_id)
    if (row.cancellation !== null) {
      cancellation(row.cancellation, { ...expected, operationId: row.original.operation_id })
      if (transactions.has(row.cancellation.posting_transaction_id) || Date.parse(row.cancellation.cancelled_at) < Date.parse(row.original.submitted_at)
        || Date.parse(row.cancellation.cancelled_at) > Date.parse(raw.queried_at)) fail()
      transactions.add(row.cancellation.posting_transaction_id)
    }
  }
  return raw.items
}
function validateLookup(raw, marker) {
  if (marker.kind !== KIND || !['submit_return', 'cancel_return'].includes(marker.operation_type)) fail()
  if (raw && raw.lookup_status === 'sealed') {
    exact(raw, ['schema_version', 'lookup_status', 'seal']); const seal = raw.seal
    exact(seal, ['seal_id', 'operator_person_id', 'work_order_id', 'operation_id', 'operation_type', 'request_id', 'request_hash', 'sealed_at'])
    if (raw.schema_version !== '1.0' || seal.work_order_id !== marker.work_order_id || seal.operator_person_id !== marker.person_id
      || seal.operation_type !== marker.operation_type || seal.operation_id !== marker.operation_id || seal.request_id !== marker.trace_request_id
      || digest(seal.request_hash) !== marker.request_hash) fail()
    uuid(seal.seal_id); time(seal.sealed_at); return raw
  }
  if (marker.operation_type === 'submit_return') {
    original(raw, { personId: marker.person_id, workOrderId: marker.work_order_id })
    if (raw.plan_hash !== marker.plan_hash) fail()
  } else cancellation(raw, { personId: marker.person_id, operationId: marker.operation_id })
  if (raw.request_id !== marker.trace_request_id || raw.request_hash !== marker.request_hash) fail()
  return raw
}
module.exports = { KIND, reason, hash, command, payload, requestHash, buildLines, validateOptions, validatePreview, validateHistory, validateLookup,
  original, cancellation, destination, amount, digest, list, integer, requestId }
