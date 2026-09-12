// Scan proofs stay in memory; the read-only response never supplies a QR code.
const { uuid } = require('./work-order-query-contract')
const { quantity, units } = require('./my-receipt-command')
const { exact, text, context } = require('./work-order-replacement-command')
function fail(message = '拆回件扫描结果与当前工单不一致，请重新扫描。') { throw new Error(message) }
function scanPayload(raw) {
  const fields = ['operator_person_id', 'basis_stock_account_id', 'condition_before', 'sku_code']
  for (const key of ['lot_no', 'serial_no', 'qr_code']) if (Object.prototype.hasOwnProperty.call(raw, key)) fields.push(key)
  exact(raw, fields)
  if (!['used', 'damaged'].includes(raw.condition_before)) fail('拆回件只能登记为旧件或坏件。')
  const serial = raw.serial_no == null ? null : text(raw.serial_no, 200)
  const qr = raw.qr_code == null ? null : text(raw.qr_code, 250)
  if ((serial === null) !== (qr === null)) fail('受控拆回件须完成 SKU、SN 和二维码扫描。')
  return { operator_person_id: uuid(raw.operator_person_id), basis_stock_account_id: uuid(raw.basis_stock_account_id),
    condition_before: raw.condition_before, sku_code: text(raw.sku_code, 80),
    lot_no: raw.lot_no == null ? null : text(raw.lot_no, 160), serial_no: serial, qr_code: qr }
}
function validateRemoved(raw, expected) {
  exact(raw, ['schema_version', 'work_order_id', 'operator_person_id', 'authorization_version', 'source_version',
    'ledger_cursor', 'checked_at', 'basis_stock_account_id', 'material_id', 'sku_code', 'material_name', 'base_unit',
    'condition_before', 'tracking_mode', 'quantity_scale', 'allow_fraction', 'lot_id', 'lot_no', 'serial_id', 'serial_no'])
  context(raw, expected)
  const scan = scanPayload(expected.scan)
  if (scan.operator_person_id !== uuid(expected.personId) || uuid(raw.basis_stock_account_id) !== scan.basis_stock_account_id
    || raw.sku_code !== scan.sku_code || raw.condition_before !== scan.condition_before
    || !['none', 'lot', 'serial', 'lot_and_serial'].includes(raw.tracking_mode)
    || !Number.isSafeInteger(raw.quantity_scale) || raw.quantity_scale < 0 || raw.quantity_scale > 3
    || typeof raw.allow_fraction !== 'boolean') fail()
  uuid(raw.material_id); text(raw.sku_code, 80); text(raw.material_name, 1000); text(raw.base_unit, 100)
  const tracked = ['serial', 'lot_and_serial'].includes(raw.tracking_mode)
  const lotTracked = ['lot', 'lot_and_serial'].includes(raw.tracking_mode)
  if (tracked) {
    uuid(raw.serial_id); text(raw.serial_no, 200)
    if (!scan.qr_code || raw.serial_no !== scan.serial_no) fail()
  } else if (raw.serial_id !== null || raw.serial_no !== null || scan.serial_no !== null || scan.qr_code !== null) fail()
  if (lotTracked) {
    uuid(raw.lot_id); text(raw.lot_no, 160)
    if ((!tracked && scan.lot_no === null) || (scan.lot_no !== null && raw.lot_no !== scan.lot_no)) fail()
  } else if (raw.lot_id !== null || raw.lot_no !== null || scan.lot_no !== null) fail()
  return raw
}
function recoveryLine(raw, expected, amount) {
  const item = validateRemoved(raw, expected), scan = scanPayload(expected.scan)
  const value = quantity(amount), count = units(value)
  const tracked = ['serial', 'lot_and_serial'].includes(item.tracking_mode)
  if (count <= 0n || count % (10n ** BigInt(3 - item.quantity_scale)) !== 0n
    || (!item.allow_fraction && count % 1000n !== 0n) || (tracked && count !== 1000n)) fail('拆回数量不符合物料精度或已扫描 SN 数量。')
  const serialId = tracked ? uuid(item.serial_id) : null
  return { basis_stock_account_id: uuid(item.basis_stock_account_id), material_id: uuid(item.material_id),
    quantity: value, condition_before: item.condition_before, lot_id: item.lot_id === null ? null : uuid(item.lot_id),
    target_stock_account_id: null, serial_ids: tracked ? [serialId] : [], serial_verifications: tracked ? [{
      serial_id: serialId, sku_code: scan.sku_code, serial_no: scan.serial_no, qr_code: scan.qr_code }] : [] }
}
module.exports = { scanPayload, validateRemoved, recoveryLine }
