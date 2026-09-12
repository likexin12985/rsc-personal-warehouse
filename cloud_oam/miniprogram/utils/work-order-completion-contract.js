const { uuid, validateWorkOrder } = require('./work-order-query-contract')
const { exact, text } = require('./work-order-replacement-command')
const { conditionLabel } = require('./inventory-contract')
const { time } = require('./my-receipt-command')
const LABELS = {
  unreleased_reservation: '未释放占用', pending_recovery: '登记后待回收', pending_return: '回收后待退回',
  unpaired_serial_consumption: 'SN 消耗待核对拆回情况', opening_not_established: '个人仓期初尚未建立',
  source_stale: '工单同步已过期', source_disabled: '工单来源已停用',
  history_scope_unresolved: '历史经手人的物料责任待核验', reversal_review_required: '历史冲销结果待核验'
}
const KINDS = ['unreleased_reservation', 'pending_recovery', 'pending_return', 'unpaired_serial_consumption']
function fail() { throw new Error('工单结束检查结果不完整或已变化，请重新读取。') }
function quantity(value) {
  if (typeof value !== 'string' || !/^(0|[1-9][0-9]{0,14})\.[0-9]{3}$/.test(value)) fail()
  const units = BigInt(value.replace('.', '')); if (units <= 0n) fail(); return units
}
function validateCompletion(raw, { workOrderId, personId, authorizationVersion, sourceVersion }) {
  exact(raw, ['schema_version', 'person_id', 'authorization_version', 'work_order', 'checked_at', 'ledger_cursor',
    'material_check_status', 'blockers', 'issue_count', 'issues'])
  const order = validateWorkOrder(raw.work_order, personId)
  time(raw.checked_at)
  if (raw.schema_version !== '1.0' || uuid(raw.person_id) !== uuid(personId)
    || !Number.isSafeInteger(authorizationVersion) || authorizationVersion < 1 || raw.authorization_version !== authorizationVersion
    || uuid(order.work_order_id) !== uuid(workOrderId) || order.source_version !== sourceVersion
    || Date.parse(raw.checked_at) < Date.parse(order.synced_at)
    || !Number.isSafeInteger(raw.ledger_cursor) || raw.ledger_cursor < 0
    || !Array.isArray(raw.issues) || raw.issues.length > 1000 || raw.issue_count !== raw.issues.length
    || !Array.isArray(raw.blockers) || raw.blockers.some(code => !Object.prototype.hasOwnProperty.call(LABELS, code))
    || new Set(raw.blockers).size !== raw.blockers.length
    || raw.blockers.join('|') !== raw.blockers.slice().sort().join('|')
    || raw.material_check_status !== (raw.blockers.length ? 'blocked' : 'clear')) fail()
  const keys = new Set(), kinds = new Set()
  const issues = raw.issues.map(row => {
    exact(row, ['kind', 'reference_id', 'operation_id', 'operation_no', 'stock_account_id', 'material_id', 'sku_code',
      'material_name', 'base_unit', 'condition_code', 'lot_id', 'lot_no', 'quantity', 'serials'])
    if (!KINDS.includes(row.kind)) fail()
    const id = row.kind + ':' + uuid(row.reference_id)
    if (keys.has(id)) fail(); keys.add(id); kinds.add(row.kind)
    uuid(row.stock_account_id); uuid(row.material_id)
    text(row.sku_code, 80); text(row.material_name, 1000); text(row.base_unit, 100)
    if (!['new', 'used', 'damaged', 'scrapped'].includes(row.condition_code)) fail()
    if (row.lot_id === null) { if (row.lot_no !== null) fail() }
    else { uuid(row.lot_id); text(row.lot_no, 160) }
    const count = quantity(row.quantity), seen = new Set()
    if (!Array.isArray(row.serials) || row.serials.length > 1000) fail()
    row.serials.forEach(sn => {
      exact(sn, ['serial_id', 'serial_no']); const id = uuid(sn.serial_id); text(sn.serial_no, 200)
      if (seen.has(id)) fail(); seen.add(id)
    })
    if (row.serials.length && BigInt(row.serials.length) * 1000n !== count) fail()
    if (['pending_return', 'unpaired_serial_consumption'].includes(row.kind)) {
      uuid(row.operation_id); text(row.operation_no, 100)
    } else if (row.operation_id !== null || row.operation_no !== null) fail()
    if (row.kind === 'unreleased_reservation' && uuid(row.reference_id) !== uuid(row.stock_account_id)) fail()
    if (row.kind === 'pending_recovery' && (row.serials.length !== 1 || count !== 1000n)) fail()
    if (row.kind === 'unpaired_serial_consumption' && !row.serials.length) fail()
    if (['pending_recovery', 'pending_return'].includes(row.kind) && !['used', 'damaged'].includes(row.condition_code)) fail()
    return { ...row, id, label: LABELS[row.kind], conditionLabel: conditionLabel(row.condition_code) }
  })
  if (KINDS.some(kind => kinds.has(kind) !== raw.blockers.includes(kind))
    || (order.freshness === 'stale') !== raw.blockers.includes('source_stale')) fail()
  return { status: raw.material_check_status, checkedAt: raw.checked_at, ledgerCursor: raw.ledger_cursor,
    blockers: raw.blockers.map(code => ({ code, label: LABELS[code] })), issues, issueCount: issues.length }
}
module.exports = { validateCompletion }
