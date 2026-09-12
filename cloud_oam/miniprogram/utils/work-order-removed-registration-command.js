// Identity admission is not a stock fact. Bind all physically scanned codes to
// one original request; only a matching original GET can retire its marker.
const { uuid } = require('./work-order-query-contract')
const { canonical, utf8 } = require('./work-order-command')
const { sha256Hex } = require('./formal-file-upload')
const { time } = require('./my-receipt-command')
const { scanPayload } = require('./work-order-removed-scan')
const { exact, text, context } = require('./work-order-replacement-command')
const KIND = 'work_order_removed_registration'
function fail(message = '拆回 SN 登记证明不一致，请保留原请求并重新核验。') { throw new Error(message) }
function command({ workOrderId, personId, scan }) {
  const value = scanPayload(scan)
  if (value.operator_person_id !== uuid(personId) || value.serial_no === null || value.qr_code === null) fail('登记拆回 SN 须扫描完整 SKU、SN 和二维码。')
  return { work_order_id: uuid(workOrderId), ...value }
}
function requestHash(input) { return sha256Hex(utf8(canonical(command(input)))) }
function payload(input) { const value = command(input); delete value.work_order_id; return value }
function validatePreview(raw, expected) {
  exact(raw, ['schema_version', 'work_order_id', 'operator_person_id', 'authorization_version', 'source_version',
    'ledger_cursor', 'checked_at', 'basis_stock_account_id', 'material_id', 'sku_code', 'material_name', 'base_unit',
    'condition_before', 'tracking_mode', 'quantity_scale', 'allow_fraction', 'lot_id', 'lot_no', 'serial_id', 'serial_no', 'status', 'request_hash'])
  context(raw, expected)
  const value = command(expected)
  if (raw.status !== 'registration_validated' || raw.request_hash !== requestHash(expected)
    || uuid(raw.basis_stock_account_id) !== value.basis_stock_account_id || raw.sku_code !== value.sku_code
    || raw.serial_id !== null || raw.serial_no !== value.serial_no || raw.condition_before !== value.condition_before
    || !['serial', 'lot_and_serial'].includes(raw.tracking_mode)
    || !Number.isSafeInteger(raw.quantity_scale) || raw.quantity_scale < 0 || raw.quantity_scale > 3
    || typeof raw.allow_fraction !== 'boolean') fail()
  uuid(raw.material_id); text(raw.material_name, 1000); text(raw.base_unit, 100)
  if (raw.tracking_mode === 'lot_and_serial') {
    uuid(raw.lot_id); text(raw.lot_no, 160)
    if (value.lot_no === null || raw.lot_no !== value.lot_no) fail('批次 SN 必须对应准确的已登记批次。')
  } else if (value.lot_no !== null || raw.lot_id !== null || raw.lot_no !== null) fail()
  return raw
}
function checkMarker(marker) {
  if (marker.kind !== KIND || marker.operation_type !== 'register_removed'
    || typeof marker.trace_request_id !== 'string' || !/^wxreq-[a-f0-9]{36}$/.test(marker.trace_request_id)
    || typeof marker.request_hash !== 'string' || !/^[a-f0-9]{64}$/.test(marker.request_hash)) fail()
}
function validateLookup(raw, marker) {
  checkMarker(marker)
  if (raw && raw.lookup_status === 'sealed_not_executed') {
    exact(raw, ['schema_version', 'lookup_status', 'command', 'seal'])
    exact(raw.seal, ['seal_id', 'work_order_id', 'operator_person_id', 'operation_type', 'request_id', 'request_hash', 'sealed_at'])
    const seal = raw.seal
    if (raw.schema_version !== '1.0' || raw.command !== null || seal.operation_type !== 'register_removed'
      || uuid(seal.work_order_id) !== uuid(marker.work_order_id) || uuid(seal.operator_person_id) !== uuid(marker.person_id)
      || seal.request_id !== marker.trace_request_id || seal.request_hash !== marker.request_hash) fail()
    uuid(seal.seal_id); time(seal.sealed_at)
    return raw
  }
  exact(raw, ['schema_version', 'status', 'registration_id', 'registration_no', 'work_order_id', 'operator_person_id',
    'serial_id', 'material_id', 'lot_id', 'basis_stock_account_id', 'request_id', 'request_hash', 'registered_at'])
  if (raw.schema_version !== '1.0' || raw.status !== 'registered' || uuid(raw.work_order_id) !== uuid(marker.work_order_id)
    || uuid(raw.operator_person_id) !== uuid(marker.person_id) || raw.request_id !== marker.trace_request_id
    || raw.request_hash !== marker.request_hash || typeof raw.registration_no !== 'string' || !/^WORS-[A-F0-9]{24}$/.test(raw.registration_no)) fail()
  for (const key of ['registration_id', 'serial_id', 'material_id', 'basis_stock_account_id']) uuid(raw[key])
  if (raw.lot_id !== null) uuid(raw.lot_id)
  time(raw.registered_at)
  return raw
}
module.exports = { KIND, command, payload, requestHash, validatePreview, validateLookup }
