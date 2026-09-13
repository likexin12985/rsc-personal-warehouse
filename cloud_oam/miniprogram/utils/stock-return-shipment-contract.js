// Carrier parcels partition exact physical departures; they never post stock.
const returns = require('./stock-return-contract')
const departure = require('./stock-return-outbound-contract')
const { uuid } = require('./work-order-query-contract')
const { exact, text } = require('./work-order-replacement-command')
const { canonical, utf8 } = require('./work-order-command')
const { quantity, units, fromUnits, time } = require('./my-receipt-command')
const { amount, digest, list, integer, requestId, reason, hash } = returns
const { instant, tracked } = departure
const KIND = returns.KIND, ACTION = 'ship_return'
const CORE = ['outbound_id', 'outbound_no', 'outbound_line_id', 'operation_line_id', 'source_recovery_line_id',
  'transit_stock_account_id', 'material_id', 'sku_code', 'material_name', 'base_unit', 'condition_code', 'lot_id', 'lot_no',
  'outbound_quantity', 'shipped_quantity', 'unshipped_quantity', 'in_transit_quantity', 'unassigned_quantity']
function fail(message = '原发出、分包明细或原请求不一致，请刷新核验。') { throw new Error(message) }
function earlier(a, b) { return instant(a) < instant(b) }
function parcelText(value) {
  if (typeof value !== 'string') fail('请填写承运商和运单号。')
  const clean = value.trim()
  if (!clean || [...clean].length > 100 || /[\u0000-\u001f\u007f]/.test(clean)) fail('承运商和运单号不能为空、超过 100 字或包含控制字符。')
  utf8(clean); return clean
}
function serials(rows, seen = new Set()) {
  for (const sn of list(rows)) {
    exact(sn, ['serial_id', 'serial_no']); uuid(sn.serial_id); text(sn.serial_no, 200)
    if (seen.has(sn.serial_id)) fail('同一 SN 不能重复装包。'); seen.add(sn.serial_id)
  }
  return rows
}
function lines(rows, option) {
  const ids = new Set(), seen = new Set(), accounts = new Map(), pairs = new Set()
  for (const row of list(rows, option ? 0 : 1, option ? 10000 : 100)) {
    exact(row, CORE.concat(option ? ['outbound_at', 'tracking_mode', 'quantity_scale', 'allow_fraction', 'selectable_quantity', 'serials'] : ['selected_quantity', 'selected_serials']))
    for (const key of ['outbound_id', 'outbound_line_id', 'operation_line_id', 'source_recovery_line_id', 'transit_stock_account_id', 'material_id']) uuid(row[key])
    const pair = row.outbound_id + '/' + row.operation_line_id
    if (ids.has(row.outbound_line_id) || pairs.has(pair)) fail(); ids.add(row.outbound_line_id); pairs.add(pair)
    for (const [key, limit] of [['outbound_no', 100], ['sku_code', 80], ['material_name', 1000], ['base_unit', 100]]) text(row[key], limit)
    if (!['used', 'damaged'].includes(row.condition_code)) fail()
    if (row.lot_id === null) { if (row.lot_no !== null) fail() } else { uuid(row.lot_id); text(row.lot_no, 160) }
    const total = amount(row.outbound_quantity, 1n), shipped = amount(row.shipped_quantity), remaining = amount(row.unshipped_quantity)
    const held = amount(row.in_transit_quantity), unassigned = amount(row.unassigned_quantity)
    if (shipped > total || remaining !== total - shipped || unassigned > held) fail()
    if (option) {
      instant(row.outbound_at)
      if (!['none', 'lot', 'serial', 'lot_and_serial'].includes(row.tracking_mode) || typeof row.allow_fraction !== 'boolean'
        || integer(row.quantity_scale) > 3 || ['lot', 'lot_and_serial'].includes(row.tracking_mode) !== (row.lot_id !== null)) fail()
      const scale = row.allow_fraction && !tracked(row) ? row.quantity_scale : 0, quantum = 10n ** BigInt(3 - scale)
      if (amount(row.selectable_quantity) !== (remaining < unassigned ? remaining : unassigned) / quantum * quantum) fail()
      serials(row.serials, seen)
      if (tracked(row) ? BigInt(row.serials.length) * 1000n !== remaining : row.serials.length) fail()
    } else {
      const selected = amount(row.selected_quantity, 1n)
      if (selected > remaining || selected > unassigned) fail()
      serials(row.selected_serials, seen)
      if (row.selected_serials.length && BigInt(row.selected_serials.length) * 1000n !== selected) fail()
    }
    const binding = canonical([row.material_id, row.condition_code, row.lot_id, row.in_transit_quantity, row.unassigned_quantity])
    const prior = accounts.get(row.transit_stock_account_id)
    if (prior && prior.binding !== binding) fail()
    const selected = (prior ? prior.selected : 0n) + (option ? 0n : amount(row.selected_quantity))
    if (selected > unassigned) fail('整组分包超过同一在途账户尚未绑定运单的数量。')
    accounts.set(row.transit_stock_account_id, { binding, selected })
  }
  return rows
}
function coordinates(raw, expected, operator = false) {
  if (raw.schema_version !== '1.0' || uuid(raw.operation_id) !== uuid(expected.operationId)
    || uuid(raw.work_order_id) !== uuid(expected.workOrderId)
    || uuid(raw[operator ? 'operator_person_id' : 'person_id']) !== uuid(expected.personId)) fail()
}
function command(input) {
  const seen = new Set(), sns = new Set()
  const selected = list(input.lines, 1, 100).map(row => {
    exact(row, ['outbound_line_id', 'quantity', 'serial_ids'])
    const id = uuid(row.outbound_line_id), q = quantity(row.quantity)
    if (seen.has(id) || units(q) <= 0n) fail(); seen.add(id)
    const ids = list(row.serial_ids).map(value => { const sn = uuid(value); if (sns.has(sn)) fail(); sns.add(sn); return sn }).sort()
    if (ids.length && units(q) !== BigInt(ids.length) * 1000n) fail()
    return { outbound_line_id: id, quantity: q, serial_ids: ids }
  }).sort((a, b) => a.outbound_line_id.localeCompare(b.outbound_line_id))
  return { operation_type: ACTION, operation_id: uuid(input.operationId), operator_person_id: uuid(input.personId),
    carrier: parcelText(input.carrier), tracking_no: parcelText(input.trackingNo), shipped_at: instant(input.shippedAt), reason: reason(input.reason), lines: selected }
}
function payload(input) { const value = command(input); delete value.operation_type; delete value.operation_id; return value }
function requestHash(input) { return hash(command(input)) }
function result(raw, expected) {
  exact(raw, ['schema_version', 'status', 'shipment_id', 'shipment_no', 'operation_id', 'work_order_id', 'operator_person_id',
    'shipped_at', 'recorded_at', 'carrier', 'tracking_no', 'reason', 'request_id', 'request_hash', 'plan_hash', 'destination', 'lines'])
  coordinates(raw, expected, true)
  if (raw.status !== 'shipped' || raw.reason !== reason(raw.reason) || raw.carrier !== parcelText(raw.carrier)
    || raw.tracking_no !== parcelText(raw.tracking_no) || earlier(raw.recorded_at, raw.shipped_at)) fail()
  uuid(raw.shipment_id); text(raw.shipment_no, 100); requestId(raw.request_id); digest(raw.plan_hash)
  returns.destination(raw.destination, raw.destination.source_location_id)
  if (earlier(raw.shipped_at, raw.destination.custody_effective_from)) fail()
  lines(raw.lines, false)
  if (digest(raw.request_hash) !== requestHash({ ...expected, shippedAt: raw.shipped_at, carrier: raw.carrier, trackingNo: raw.tracking_no,
    reason: raw.reason, lines: raw.lines.map(row => ({ outbound_line_id: row.outbound_line_id, quantity: row.selected_quantity, serial_ids: row.selected_serials.map(sn => sn.serial_id) })) })) fail()
  return raw
}
function sourceBinding(row, history, option) {
  const batch = history.departures.items.find(item => item.outbound_id === row.outbound_id)
  const origin = batch && batch.lines.find(item => item.operation_line_id === row.operation_line_id)
  if (!origin || row.outbound_no !== batch.outbound_no || row.outbound_quantity !== origin.selected_quantity) fail()
  for (const key of ['source_recovery_line_id', 'material_id', 'condition_code', 'lot_id']) if (row[key] !== origin[key]) fail()
  if (option && instant(row.outbound_at) !== instant(batch.outbound_at)) fail()
  const selected = row.selected_serials || row.serials
  if (selected.some(sn => !origin.selected_serials.some(old => old.serial_id === sn.serial_id && old.serial_no === sn.serial_no))) fail()
  if (option ? tracked(row) !== !!origin.selected_serials.length : origin.selected_serials.length ? BigInt(selected.length) * 1000n !== amount(row.selected_quantity) : selected.length) fail()
  return batch
}
function validateHistory(raw, expected) {
  exact(raw, ['schema_version', 'operation_id', 'work_order_id', 'person_id', 'authorization_version', 'queried_at', 'shipment_status', 'departures', 'items'])
  coordinates(raw, expected)
  if (raw.authorization_version !== integer(expected.authorizationVersion, 1)) fail()
  departure.validateHistory(raw.departures, expected)
  if (earlier(raw.queried_at, raw.departures.queried_at)) fail()
  const totals = new Map(), shipped = new Map(), pairs = new Map(), bindings = new Map(), sns = new Set(), ids = new Set()
  const requests = new Set([raw.departures.original.request_id, ...raw.departures.items.map(item => item.request_id)])
  let recorded = raw.departures.original.submitted_at
  for (const item of list(raw.items)) {
    result(item, expected)
    if (ids.has(item.shipment_id) || requests.has(item.request_id) || earlier(item.recorded_at, recorded) || earlier(raw.queried_at, item.recorded_at)) fail()
    ids.add(item.shipment_id); requests.add(item.request_id); recorded = item.recorded_at
    for (const row of item.lines) {
      const batch = sourceBinding(row, raw, false), pair = row.outbound_id + '/' + row.operation_line_id
      if (earlier(item.shipped_at, batch.outbound_at) || pairs.has(pair) && pairs.get(pair) !== row.outbound_line_id) fail()
      pairs.set(pair, row.outbound_line_id)
      const binding = canonical([pair, row.transit_stock_account_id, row.material_id, row.condition_code, row.lot_id])
      if (bindings.has(row.outbound_line_id) && bindings.get(row.outbound_line_id) !== binding) fail(); bindings.set(row.outbound_line_id, binding)
      const prior = shipped.get(row.outbound_line_id) || 0n
      if (amount(row.shipped_quantity) !== prior) fail()
      shipped.set(row.outbound_line_id, prior + amount(row.selected_quantity))
      totals.set(row.source_recovery_line_id, (totals.get(row.source_recovery_line_id) || 0n) + amount(row.selected_quantity))
      serials(row.selected_serials, sns)
    }
    for (const key of ['source_location_id', 'target_location_id', 'transit_location_id', 'region_org_id']) if (item.destination[key] !== raw.departures.original.destination[key]) fail()
  }
  if (raw.departures.cancellation && raw.items.length) fail()
  const complete = raw.departures.original.lines.every(row => (totals.get(row.source.source_recovery_line_id) || 0n) === amount(row.selected_quantity))
  if (raw.shipment_status !== (complete ? 'shipped' : raw.items.length ? 'partially_shipped' : 'not_shipped')) fail()
  return raw
}
function validateOptions(raw, expected, history) {
  exact(raw, ['schema_version', 'operation_id', 'operation_no', 'work_order_id', 'person_id', 'authorization_version', 'ledger_cursor', 'queried_at', 'destination', 'lines'])
  coordinates(raw, expected); validateHistory(history, expected)
  const original = history.departures.original
  if (history.departures.cancellation || raw.authorization_version !== integer(expected.authorizationVersion, 1)
    || raw.operation_no !== original.operation_no || earlier(raw.queried_at, history.queried_at)) fail()
  integer(raw.ledger_cursor); returns.destination(raw.destination, original.destination.source_location_id)
  if (earlier(raw.queried_at, raw.destination.custody_effective_from)) fail()
  for (const key of ['target_location_id', 'transit_location_id', 'region_org_id']) if (raw.destination[key] !== original.destination[key]) fail()
  lines(raw.lines, true)
  if (raw.lines.length !== history.departures.items.reduce((sum, batch) => sum + batch.lines.length, 0)) fail()
  for (const row of raw.lines) {
    sourceBinding(row, history, true)
    let shipped = 0n; const used = new Set()
    for (const item of history.items) for (const prior of item.lines.filter(old => old.outbound_id === row.outbound_id && old.operation_line_id === row.operation_line_id)) {
      if (prior.outbound_line_id !== row.outbound_line_id || prior.transit_stock_account_id !== row.transit_stock_account_id) fail()
      shipped += amount(prior.selected_quantity); prior.selected_serials.forEach(sn => used.add(sn.serial_id))
    }
    if (amount(row.shipped_quantity) !== shipped || row.serials.some(sn => used.has(sn.serial_id))) fail()
  }
  return raw
}
function buildLines(choices, drafts) {
  const accounts = new Map()
  return list(Object.entries(drafts || {}), 1, 100).map(([id, value]) => {
    const row = choices.lines.find(item => item.outbound_line_id === uuid(id)); if (!row) fail()
    exact(value, ['quantity', 'serial_ids'])
    const sns = list(value.serial_ids).map(uuid), q = tracked(row) ? fromUnits(BigInt(sns.length) * 1000n) : quantity(value.quantity)
    const scale = row.allow_fraction && !tracked(row) ? row.quantity_scale : 0
    if (new Set(sns).size !== sns.length || sns.some(sn => !row.serials.some(item => item.serial_id === sn))
      || !tracked(row) && sns.length) fail('请选择原发出行尚未装包的 SN。')
    if (units(q) <= 0n || units(q) > amount(row.selectable_quantity) || units(q) % 10n ** BigInt(3 - scale)) fail('分包数量超过剩余数量，或不符合物料精度。')
    const total = (accounts.get(row.transit_stock_account_id) || 0n) + units(q); accounts.set(row.transit_stock_account_id, total)
    if (total > amount(row.unassigned_quantity)) fail('整组分包超过同一在途账户尚未绑定运单的数量。')
    return { outbound_line_id: id, quantity: q, serial_ids: sns }
  })
}
function validatePreview(raw, input, choices) {
  exact(raw, ['schema_version', 'planning_status', 'operation_id', 'operation_no', 'work_order_id', 'operator_person_id', 'authorization_version',
    'shipped_at', 'carrier', 'tracking_no', 'reason', 'ledger_cursor', 'checked_at', 'destination', 'request_hash', 'plan_hash', 'lines'])
  coordinates(raw, input, true); const value = command(input)
  if (raw.planning_status !== 'preview_only' || raw.operation_no !== choices.operation_no || raw.authorization_version !== integer(input.authorizationVersion, 1)
    || integer(raw.ledger_cursor) < choices.ledger_cursor || instant(raw.shipped_at) !== value.shipped_at || earlier(raw.checked_at, choices.queried_at)
    || earlier(raw.checked_at, raw.shipped_at) || earlier(raw.shipped_at, raw.destination.custody_effective_from)
    || raw.reason !== value.reason || raw.carrier !== value.carrier || raw.tracking_no !== value.tracking_no
    || digest(raw.request_hash) !== requestHash(input) || canonical(raw.destination) !== canonical(choices.destination)) fail()
  digest(raw.plan_hash); lines(raw.lines, false)
  const selected = raw.lines.map(row => {
    const choice = choices.lines.find(item => item.outbound_line_id === row.outbound_line_id)
    if (!choice || CORE.some(key => row[key] !== choice[key]) || earlier(raw.shipped_at, choice.outbound_at)
      || amount(row.selected_quantity) > amount(choice.selectable_quantity)
      || row.selected_serials.some(sn => !choice.serials.some(item => item.serial_id === sn.serial_id && item.serial_no === sn.serial_no))) fail()
    return { outbound_line_id: row.outbound_line_id, quantity: row.selected_quantity, serial_ids: row.selected_serials.map(sn => sn.serial_id).sort() }
  }).sort((a, b) => a.outbound_line_id.localeCompare(b.outbound_line_id))
  if (canonical(selected) !== canonical(value.lines)) fail()
  return raw
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
