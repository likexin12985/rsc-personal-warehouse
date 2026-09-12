const { uuid } = require('./work-order-query-contract')
const { canonical, quantity, units, time } = require('./my-receipt-command')
const { sha256Hex } = require('./formal-file-upload')
const KINDS = ['occupy', 'consume', 'release']
function fail(message = '工单命令或原请求结果不一致，请保留恢复记录。') { throw new Error(message) }
function exact(value, fields) {
  if (!value || typeof value !== 'object' || Array.isArray(value) || Object.keys(value).sort().join('|') !== fields.slice().sort().join('|')) fail()
}
function text(value, limit) {
  if (typeof value !== 'string' || !value.trim() || value.length > limit || /[\u0000-\u001f\u007f]/.test(value)) fail()
  return value
}
function utf8(value) {
  const bytes = []
  for (const symbol of value) {
    const point = symbol.codePointAt(0)
    if (point >= 0xd800 && point <= 0xdfff) fail('扫码内容含无效字符。')
    if (point < 0x80) bytes.push(point)
    else if (point < 0x800) bytes.push(0xc0 | point >> 6, 0x80 | point & 0x3f)
    else if (point < 0x10000) bytes.push(0xe0 | point >> 12, 0x80 | point >> 6 & 0x3f, 0x80 | point & 0x3f)
    else bytes.push(0xf0 | point >> 18, 0x80 | point >> 12 & 0x3f, 0x80 | point >> 6 & 0x3f, 0x80 | point & 0x3f)
  }
  return Uint8Array.from(bytes)
}
function command(kind, workOrderId, personId, lines) {
  if (!KINDS.includes(kind) || !Array.isArray(lines) || !lines.length || lines.length > 100) fail()
  const accounts = new Set(), seenSerials = new Set()
  const normalized = lines.map(raw => {
    const fields = ['material_id', 'stock_account_id', 'quantity', 'condition_before', 'serial_ids', 'serial_verifications']
    if (kind === 'release' || Object.prototype.hasOwnProperty.call(raw, 'target_stock_account_id')) fields.push('target_stock_account_id')
    exact(raw, fields)
    const id = uuid(raw.stock_account_id)
    if (accounts.has(id) || !['new', 'used', 'damaged'].includes(raw.condition_before) || units(raw.quantity) <= 0n) fail()
    accounts.add(id)
    if (!Array.isArray(raw.serial_ids) || raw.serial_ids.length > 1000 || !Array.isArray(raw.serial_verifications) || raw.serial_verifications.length !== raw.serial_ids.length) fail()
    const serials = raw.serial_ids.map(value => {
      const serial = uuid(value)
      if (seenSerials.has(serial)) fail('同一 SN 不能重复投入。')
      seenSerials.add(serial); return serial
    }).sort()
    if (serials.length && BigInt(serials.length) * 1000n !== units(raw.quantity)) fail('SN 数量与物料数量不一致。')
    const proofs = raw.serial_verifications.map(proof => {
      exact(proof, ['serial_id', 'sku_code', 'serial_no', 'qr_code'])
      return { serial_id: uuid(proof.serial_id), sku_code: text(proof.sku_code, 80), serial_no: text(proof.serial_no, 200), qr_code: text(proof.qr_code, 250) }
    }).sort((a, b) => a.serial_id < b.serial_id ? -1 : a.serial_id > b.serial_id ? 1 : 0)
    if (JSON.stringify(proofs.map(value => value.serial_id)) !== JSON.stringify(serials)) fail('每个 SN 必须完成三码校验。')
    if (kind === 'consume' && Object.prototype.hasOwnProperty.call(raw, 'target_stock_account_id')) fail()
    if (kind === 'occupy' && raw.target_stock_account_id !== undefined && raw.target_stock_account_id !== null) fail('占用目标由服务器按本人来源库存确定。')
    return { material_id: uuid(raw.material_id), stock_account_id: id, quantity: quantity(raw.quantity), condition_before: raw.condition_before,
      serial_ids: serials, serial_verifications: proofs, target_stock_account_id: kind === 'release' ? uuid(raw.target_stock_account_id) : null }
  })
  return { operation_type: kind, work_order_id: uuid(workOrderId), operator_person_id: uuid(personId), lines: normalized, replacement_pairs: [] }
}
function requestHash(kind, workOrderId, personId, lines) { return sha256Hex(utf8(canonical(command(kind, workOrderId, personId, lines)))) }
function payload(kind, workOrderId, personId, lines) {
  const normalized = command(kind, workOrderId, personId, lines)
  return { operator_person_id: normalized.operator_person_id, lines: normalized.lines.map(line => {
    const result = { ...line }
    if (kind !== 'release') delete result.target_stock_account_id
    return result
  }) }
}
function validateResult(raw, marker) {
  exact(raw, ['schema_version', 'operation_id', 'operation_no', 'work_order_id', 'posting_transaction_id', 'operation_type', 'status', 'operator_person_id', 'request_id', 'request_hash', 'posted_at'])
  if (!KINDS.includes(marker.operation_type) || raw.schema_version !== '1.0' || raw.status !== 'posted' || raw.operation_type !== marker.operation_type
    || uuid(raw.work_order_id) !== marker.work_order_id || uuid(raw.operator_person_id) !== marker.person_id
    || raw.request_id !== marker.trace_request_id || raw.request_hash !== marker.request_hash) fail()
  uuid(raw.operation_id); uuid(raw.posting_transaction_id); text(raw.operation_no, 100); time(raw.posted_at)
  return raw
}
function validateLookup(raw, marker) {
  if (raw && raw.lookup_status === 'sealed_not_executed') {
    exact(raw, ['schema_version', 'lookup_status', 'command', 'seal'])
    exact(raw.seal, ['seal_id', 'work_order_id', 'operator_person_id', 'operation_type', 'request_id', 'request_hash', 'sealed_at'])
    const seal = raw.seal
    if (raw.schema_version !== '1.0' || raw.command !== null || !KINDS.includes(marker.operation_type)
      || uuid(seal.work_order_id) !== marker.work_order_id || uuid(seal.operator_person_id) !== marker.person_id
      || seal.operation_type !== marker.operation_type || seal.request_id !== marker.trace_request_id || seal.request_hash !== marker.request_hash) fail()
    uuid(seal.seal_id); time(seal.sealed_at)
    return { lookup_status: 'sealed_not_executed', seal }
  }
  exact(raw, ['schema_version', 'lookup_status', 'command'])
  if (raw.schema_version !== '1.0') fail()
  if (raw.lookup_status === 'not_observed' && raw.command === null) return null
  if (raw.lookup_status !== 'confirmed') fail()
  return validateResult(raw.command, marker)
}
module.exports = { KINDS, canonical, utf8, command, payload, requestHash, validateResult, validateLookup }
