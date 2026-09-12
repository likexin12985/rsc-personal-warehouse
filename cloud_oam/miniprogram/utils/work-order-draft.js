// Physical scan drafts live only in page memory. Preview is not a stock write.
const { uuid } = require('./work-order-query-contract')
const { payload, requestHash, KINDS } = require('./work-order-command')
const { quantity, units, fromUnits, time } = require('./my-receipt-command')
function fail(message = '工单物料草稿已变化，请重新核对。') { throw new Error(message) }
function tracked(item) { return ['serial', 'lot_and_serial'].includes(item.tracking_mode) }
function addScanned(item, proofs, scans) {
  if (!tracked(item) || !Array.isArray(proofs) || !scans || scans.sku_code !== item.sku_code) fail('扫描的 SKU 与当前物料不一致。')
  const choices = item.serials.filter(row => row.serial_no === scans.serial_no)
  if (choices.length !== 1) fail('扫描的 SN 不在当前可用物料中。')
  const serialId = uuid(choices[0].serial_id)
  if (proofs.some(row => uuid(row.serial_id) === serialId)) fail('该 SN 已加入本次草稿。')
  if (typeof scans.qr_code !== 'string' || !scans.qr_code.trim() || scans.qr_code.length > 250) fail('请扫描该件物料的二维码。')
  return proofs.concat({ serial_id: serialId, sku_code: scans.sku_code, serial_no: scans.serial_no, qr_code: scans.qr_code })
}
function buildDraft({ workOrder, items, personId, kind, drafts }) {
  if (!KINDS.includes(kind) || !workOrder.can_operate || uuid(workOrder.engineer_person_id) !== uuid(personId)) fail('该工单当前不可新增物料操作。')
  const choices = new Map(items.map(row => [uuid(row.stock_account_id), row]))
  const keys = Object.keys(drafts || {})
  if (!keys.length || keys.length > 100) fail('请先选择本次操作的物料。')
  const lines = keys.map(key => {
    const item = choices.get(uuid(key)), draft = drafts[key]
    if (!item || !item.allowed_actions.includes(kind)) fail('所选物料已不在本次可操作范围，请刷新后核对。')
    const proofs = draft.serial_verifications || []
    if (!Array.isArray(proofs) || (!tracked(item) && proofs.length)) fail()
    const amount = tracked(item) ? fromUnits(BigInt(proofs.length) * 1000n) : quantity(draft.quantity)
    if (units(amount) <= 0n || units(amount) > units(item.selectable_quantity)) fail('数量须大于零，且不能超过本工单可用数量。')
    if (proofs.some(row => row.sku_code !== item.sku_code || !item.serials.some(sn => sn.serial_id === row.serial_id && sn.serial_no === row.serial_no))) fail('SN 或 SKU 已不属于当前物料候选，请重新扫描。')
    const line = { material_id: item.material_id, stock_account_id: item.stock_account_id, quantity: amount,
      condition_before: item.condition_code, serial_ids: proofs.map(row => row.serial_id), serial_verifications: proofs }
    if (kind === 'release') line.target_stock_account_id = item.release_target_stock_account_id
    return line
  })
  return payload(kind, workOrder.work_order_id, personId, lines).lines
}
function validatePreview(raw, { workOrderId, personId, authorizationVersion, kind, lines, sourceVersion, ledgerCursor }) {
  const fields = ['schema_version', 'status', 'work_order_id', 'operator_person_id', 'authorization_version', 'operation_type', 'source_version', 'ledger_cursor', 'checked_at', 'line_count', 'request_hash']
  if (!raw || typeof raw !== 'object' || Array.isArray(raw) || Object.keys(raw).sort().join('|') !== fields.sort().join('|')
    || raw.schema_version !== '1.0' || raw.status !== 'batch_validated' || raw.operation_type !== kind
    || uuid(raw.work_order_id) !== uuid(workOrderId) || uuid(raw.operator_person_id) !== uuid(personId)
    || raw.authorization_version !== authorizationVersion || raw.source_version !== sourceVersion
    || !Number.isSafeInteger(raw.ledger_cursor) || raw.ledger_cursor < ledgerCursor || raw.ledger_cursor < 1
    || raw.line_count !== lines.length || raw.request_hash !== requestHash(kind, workOrderId, personId, lines)) fail('整批预检结果与本次草稿不一致，请重新预检。')
  time(raw.checked_at)
  return raw
}
module.exports = { tracked, addScanned, buildDraft, validatePreview }
