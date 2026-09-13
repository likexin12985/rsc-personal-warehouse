const returns = require('./stock-return-contract')
const { uuid } = require('./work-order-query-contract')
const { exact, text } = require('./work-order-replacement-command')
const { canonical, utf8 } = require('./work-order-command')
const { quantity, units, fromUnits, time } = require('./my-receipt-command')
const { amount, digest, list, integer, requestId, reason, hash } = returns
const KIND = returns.KIND
const ACTION = 'outbound_return'
const CORE = ['operation_line_id', 'source_recovery_line_id', 'source_stock_account_id', 'material_id', 'sku_code', 'material_name',
  'base_unit', 'condition_code', 'lot_id', 'lot_no', 'return_quantity', 'departed_quantity', 'remaining_quantity', 'held_quantity']
function fail(message = '原退回、实物发出明细或原请求不一致，请刷新核验。') { throw new Error(message) }
function instant(value) {
  time(value)
  const [year, month, day] = value.slice(0, 10).split('-').map(Number)
  const days = [31, year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0) ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
  if (year < 1 || month < 1 || month > 12 || day < 1 || day > days[month - 1]) fail('实物发出日期无效，请重新选择。')
  const fraction = (value.match(/\.(\d{1,6})/) || [null, ''])[1].padEnd(6, '0')
  const whole = new Date(Date.parse(value.replace(/\.\d{1,6}/, ''))).toISOString()
  // Preserve all six server microsecond digits; Date alone would truncate them.
  return whole.slice(0, 19) + '.' + fraction + 'Z'
}
function earlier(a, b) { return instant(a) < instant(b) }
function tracked(row) { return ['serial', 'lot_and_serial'].includes(row.tracking_mode) }
function serials(rows, seen = new Set()) {
  for (const sn of list(rows)) {
    exact(sn, ['serial_id', 'serial_no']); uuid(sn.serial_id); text(sn.serial_no, 200)
    if (seen.has(sn.serial_id)) fail(); seen.add(sn.serial_id)
  }
  return rows
}
function line(raw, option, origins, snSeen) {
  exact(raw, CORE.concat(option ? ['tracking_mode', 'quantity_scale', 'allow_fraction', 'selectable_quantity', 'serials'] : ['selected_quantity', 'selected_serials']))
  for (const key of ['operation_line_id', 'source_recovery_line_id', 'source_stock_account_id', 'material_id']) uuid(raw[key])
  if (origins.has(raw.source_recovery_line_id)) fail(); origins.add(raw.source_recovery_line_id)
  text(raw.sku_code, 80); text(raw.material_name, 1000); text(raw.base_unit, 100)
  if (!['used', 'damaged'].includes(raw.condition_code)) fail()
  if (raw.lot_id === null) { if (raw.lot_no !== null) fail() } else { uuid(raw.lot_id); text(raw.lot_no, 160) }
  const total = amount(raw.return_quantity, 1n), departed = amount(raw.departed_quantity), remaining = amount(raw.remaining_quantity), held = amount(raw.held_quantity)
  if (departed > total || total - departed !== remaining) fail()
  if (option) {
    if (!['none', 'lot', 'serial', 'lot_and_serial'].includes(raw.tracking_mode) || typeof raw.allow_fraction !== 'boolean'
      || integer(raw.quantity_scale) > 3 || ['lot', 'lot_and_serial'].includes(raw.tracking_mode) && raw.lot_id === null) fail()
    const scale = raw.allow_fraction && !tracked(raw) ? raw.quantity_scale : 0, quantum = 10n ** BigInt(3 - scale)
    if (amount(raw.selectable_quantity) !== (remaining < held ? remaining : held) / quantum * quantum) fail()
    serials(raw.serials, snSeen)
    if (tracked(raw) ? BigInt(raw.serials.length) * 1000n !== remaining : raw.serials.length) fail()
  } else {
    const selected = amount(raw.selected_quantity, 1n)
    if (selected > remaining || selected > held) fail()
    serials(raw.selected_serials, snSeen)
    if (raw.selected_serials.length && BigInt(raw.selected_serials.length) * 1000n !== selected) fail()
  }
  return raw
}
function lines(rows, option) {
  const ids = new Set(), origins = new Set(), snSeen = new Set(), accounts = new Map()
  for (const row of list(rows, 1, 100)) {
    line(row, option, origins, snSeen)
    if (ids.has(row.operation_line_id)) fail(); ids.add(row.operation_line_id)
    const binding = canonical([row.material_id, row.condition_code, row.lot_id, row.held_quantity])
    const prior = accounts.get(row.source_stock_account_id)
    if (prior && prior.binding !== binding) fail()
    const selected = (prior ? prior.selected : 0n) + (option ? 0n : amount(row.selected_quantity))
    if (selected > amount(row.held_quantity)) fail()
    accounts.set(row.source_stock_account_id, { binding, selected })
  }
  return rows
}
function coordinates(raw, expected, operator = false) {
  if (raw.schema_version !== '1.0' || uuid(raw.operation_id) !== uuid(expected.operationId)
    || uuid(raw.work_order_id) !== uuid(expected.workOrderId)
    || uuid(raw[operator ? 'operator_person_id' : 'person_id']) !== uuid(expected.personId)) fail()
}
function originalBinding(row, original) {
  const source = original.lines.find(item => item.source.source_recovery_line_id === row.source_recovery_line_id)
  if (!source || row.return_quantity !== source.selected_quantity) fail()
  // The return_pending account differs from the original available account.
  for (const key of ['material_id', 'sku_code', 'material_name', 'base_unit', 'condition_code', 'lot_id', 'lot_no']) if (row[key] !== source.source[key]) fail()
  const sns = row.selected_serials || row.serials
  if (sns.some(sn => !source.selected_serials.some(item => item.serial_id === sn.serial_id && item.serial_no === sn.serial_no))) fail()
  if (row.selected_serials && (source.selected_serials.length ? BigInt(sns.length) * 1000n !== amount(row.selected_quantity) : sns.length)) fail()
}
function validateHistory(raw, expected) {
  exact(raw, ['schema_version', 'operation_id', 'work_order_id', 'person_id', 'authorization_version', 'queried_at', 'outbound_status', 'original', 'cancellation', 'items'])
  coordinates(raw, expected)
  if (raw.authorization_version !== integer(expected.authorizationVersion, 1)) fail()
  time(raw.queried_at); returns.original(raw.original, expected)
  if (raw.original.operation_id !== raw.operation_id || earlier(raw.queried_at, raw.original.submitted_at)) fail()
  const quantities = new Map(), bindings = new Map(), lineOrigins = new Map(), sns = new Set(), ids = new Set(), requests = new Set(), postings = new Set([raw.original.posting_transaction_id])
  let recorded = raw.original.submitted_at
  for (const item of list(raw.items)) {
    result(item, expected)
    if (ids.has(item.outbound_id) || requests.has(item.request_id) || postings.has(item.posting_transaction_id)
      || earlier(item.recorded_at, recorded) || earlier(raw.queried_at, item.recorded_at) || earlier(item.outbound_at, raw.original.submitted_at)) fail()
    ids.add(item.outbound_id); requests.add(item.request_id); postings.add(item.posting_transaction_id); recorded = item.recorded_at
    for (const row of item.lines) {
      originalBinding(row, raw.original)
      const key = row.source_recovery_line_id, prior = quantities.get(key) || 0n
      if (lineOrigins.has(row.operation_line_id) && lineOrigins.get(row.operation_line_id) !== key) fail()
      lineOrigins.set(row.operation_line_id, key)
      if (amount(row.departed_quantity) !== prior) fail()
      quantities.set(key, prior + amount(row.selected_quantity))
      const binding = canonical([row.operation_line_id, row.source_stock_account_id, row.material_id, row.condition_code, row.lot_id])
      if (bindings.has(key) && bindings.get(key) !== binding) fail(); bindings.set(key, binding)
      serials(row.selected_serials, sns)
    }
    for (const key of ['source_location_id', 'target_location_id', 'transit_location_id', 'region_org_id']) if (item.destination[key] !== raw.original.destination[key]) fail()
  }
  if (raw.cancellation !== null) {
    returns.cancellation(raw.cancellation, expected)
    if (raw.items.length || postings.has(raw.cancellation.posting_transaction_id) || earlier(raw.cancellation.cancelled_at, raw.original.submitted_at)
      || earlier(raw.queried_at, raw.cancellation.cancelled_at)) fail()
  }
  const complete = raw.original.lines.every(row => (quantities.get(row.source.source_recovery_line_id) || 0n) === amount(row.selected_quantity))
  if (raw.outbound_status !== (complete ? 'outbound' : raw.items.length ? 'partially_outbound' : 'not_outbound')) fail()
  return raw
}
function validateOptions(raw, expected, history) {
  exact(raw, ['schema_version', 'operation_id', 'operation_no', 'work_order_id', 'person_id', 'authorization_version', 'ledger_cursor', 'queried_at', 'destination', 'lines'])
  coordinates(raw, expected); validateHistory(history, expected)
  if (history.cancellation || raw.authorization_version !== integer(expected.authorizationVersion, 1) || raw.operation_no !== history.original.operation_no
    || earlier(raw.queried_at, history.queried_at)) fail()
  integer(raw.ledger_cursor); returns.destination(raw.destination, history.original.destination.source_location_id)
  if (earlier(raw.queried_at, raw.destination.custody_effective_from)) fail()
  for (const key of ['target_location_id', 'transit_location_id', 'region_org_id']) if (raw.destination[key] !== history.original.destination[key]) fail()
  lines(raw.lines, true)
  if (raw.lines.length !== history.original.lines.length) fail()
  for (const row of raw.lines) {
    originalBinding(row, history.original)
    let departed = 0n; const used = new Set()
    for (const item of history.items) for (const old of item.lines.filter(x => x.source_recovery_line_id === row.source_recovery_line_id)) {
      if (old.operation_line_id !== row.operation_line_id || old.source_stock_account_id !== row.source_stock_account_id) fail()
      departed += amount(old.selected_quantity); old.selected_serials.forEach(sn => used.add(sn.serial_id))
    }
    if (amount(row.departed_quantity) !== departed || row.serials.some(sn => used.has(sn.serial_id))) fail()
  }
  return raw
}
function command(input) {
  const seen = new Set(), sns = new Set()
  const selected = list(input.lines, 1, 100).map(row => {
    exact(row, ['operation_line_id', 'quantity', 'serial_verifications'])
    const id = uuid(row.operation_line_id), q = quantity(row.quantity)
    if (seen.has(id) || units(q) <= 0n) fail(); seen.add(id)
    const proofs = list(row.serial_verifications).map(sn => {
      exact(sn, ['serial_id', 'sku_code', 'serial_no', 'qr_code']); uuid(sn.serial_id)
      if (sns.has(sn.serial_id)) fail('同一 SN 只能发出一次。'); sns.add(sn.serial_id)
      for (const [key, limit] of [['sku_code', 80], ['serial_no', 200], ['qr_code', 250]]) { text(sn[key], limit); utf8(sn[key]) }
      return { ...sn }
    }).sort((a, b) => a.serial_id.localeCompare(b.serial_id))
    if (proofs.length && units(q) !== BigInt(proofs.length) * 1000n) fail()
    return { operation_line_id: id, quantity: q, serial_verifications: proofs }
  }).sort((a, b) => a.operation_line_id.localeCompare(b.operation_line_id))
  return { operation_type: ACTION, operation_id: uuid(input.operationId), operator_person_id: uuid(input.personId),
    outbound_at: instant(input.outboundAt), reason: reason(input.reason), lines: selected }
}
function payload(input) { const value = command(input); delete value.operation_type; delete value.operation_id; return value }
function requestHash(input) { return hash(command(input)) }
function buildLines(choices, drafts) {
  const accounts = new Map()
  return list(Object.entries(drafts || {}), 1, 100).map(([id, value]) => {
    const row = choices.lines.find(item => item.operation_line_id === uuid(id)); if (!row) fail()
    const proofs = list(value.serial_verifications), q = tracked(row) ? fromUnits(BigInt(proofs.length) * 1000n) : quantity(value.quantity)
    const scale = row.allow_fraction && !tracked(row) ? row.quantity_scale : 0
    if (units(q) <= 0n || units(q) > amount(row.selectable_quantity) || units(q) % 10n ** BigInt(3 - scale) || !tracked(row) && proofs.length) fail('本次发出数量超过剩余实物数量，或不符合物料精度。')
    if (proofs.some(sn => sn.sku_code !== row.sku_code || !row.serials.some(item => item.serial_id === sn.serial_id && item.serial_no === sn.serial_no))) fail('扫码实物不属于原退回尚未发出的 SN。')
    const total = (accounts.get(row.source_stock_account_id) || 0n) + units(q); accounts.set(row.source_stock_account_id, total)
    if (total > amount(row.held_quantity)) fail('整批发出超过同一待退回账户的库存。')
    return { operation_line_id: id, quantity: q, serial_verifications: proofs }
  })
}
function validatePreview(raw, input, choices) {
  exact(raw, ['schema_version', 'planning_status', 'operation_id', 'operation_no', 'work_order_id', 'operator_person_id', 'authorization_version',
    'outbound_at', 'reason', 'ledger_cursor', 'checked_at', 'destination', 'request_hash', 'plan_hash', 'lines'])
  coordinates(raw, input, true); const value = command(input)
  if (raw.planning_status !== 'preview_only' || raw.operation_no !== choices.operation_no || raw.authorization_version !== integer(input.authorizationVersion, 1)
    || integer(raw.ledger_cursor) < choices.ledger_cursor || instant(raw.outbound_at) !== value.outbound_at || earlier(raw.checked_at, choices.queried_at)
    || earlier(raw.checked_at, raw.outbound_at) || raw.reason !== value.reason || digest(raw.request_hash) !== requestHash(input)
    || canonical(raw.destination) !== canonical(choices.destination)) fail()
  digest(raw.plan_hash); lines(raw.lines, false)
  const selected = raw.lines.map(row => {
    const choice = choices.lines.find(item => item.operation_line_id === row.operation_line_id)
    if (!choice || CORE.some(key => row[key] !== choice[key]) || amount(row.selected_quantity) > amount(choice.selectable_quantity)
      || row.selected_serials.some(sn => !choice.serials.some(item => item.serial_id === sn.serial_id && item.serial_no === sn.serial_no))) fail()
    return { operation_line_id: row.operation_line_id, quantity: row.selected_quantity, serials: row.selected_serials.map(sn => sn.serial_id).sort() }
  }).sort((a, b) => a.operation_line_id.localeCompare(b.operation_line_id))
  if (canonical(selected) !== canonical(value.lines.map(row => ({ operation_line_id: row.operation_line_id, quantity: row.quantity,
    serials: row.serial_verifications.map(sn => sn.serial_id) })))) fail()
  return raw
}
function result(raw, expected) {
  exact(raw, ['schema_version', 'status', 'outbound_id', 'outbound_no', 'operation_id', 'work_order_id', 'operator_person_id', 'outbound_at',
    'recorded_at', 'reason', 'request_id', 'request_hash', 'plan_hash', 'posting_transaction_id', 'destination', 'lines'])
  coordinates(raw, expected, true)
  if (raw.status !== 'outbound' || raw.reason !== reason(raw.reason) || earlier(raw.recorded_at, raw.outbound_at)) fail()
  uuid(raw.outbound_id); text(raw.outbound_no, 100); uuid(raw.posting_transaction_id)
  requestId(raw.request_id); digest(raw.request_hash); digest(raw.plan_hash)
  returns.destination(raw.destination, raw.destination.source_location_id)
  if (earlier(raw.recorded_at, raw.destination.custody_effective_from)) fail()
  lines(raw.lines, false); return raw
}
function validateLookup(raw, marker) {
  if (marker.kind !== KIND || marker.operation_type !== ACTION) fail()
  if (raw && raw.lookup_status === 'sealed') {
    exact(raw, ['schema_version', 'lookup_status', 'seal']); const seal = raw.seal
    exact(seal, ['seal_id', 'operator_person_id', 'work_order_id', 'operation_id', 'operation_type', 'request_id', 'request_hash', 'sealed_at'])
    if (raw.schema_version !== '1.0' || seal.work_order_id !== marker.work_order_id || seal.operator_person_id !== marker.person_id
      || seal.operation_id !== marker.operation_id || seal.operation_type !== ACTION || seal.request_id !== marker.trace_request_id
      || digest(seal.request_hash) !== marker.request_hash) fail()
    uuid(seal.seal_id); time(seal.sealed_at); return raw
  }
  result(raw, { operationId: marker.operation_id, workOrderId: marker.work_order_id, personId: marker.person_id })
  if (raw.request_id !== marker.trace_request_id || raw.request_hash !== marker.request_hash || raw.plan_hash !== marker.plan_hash) fail()
  return raw
}
module.exports = { KIND, ACTION, instant, tracked, command, payload, requestHash, buildLines, validateHistory, validateOptions, validatePreview, validateLookup }
