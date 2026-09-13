const contract = require('./stock-return-receipt-contract')
const receiving = require('./stock-return-receiving-contract')
const { validateMarker } = require('./work-order-recovery-store')
const { originalResult } = require('./work-order-recovery')
const { uuid } = require('./work-order-query-contract')

function freeze(value) { if (value && typeof value === 'object') { Object.values(value).forEach(freeze); Object.freeze(value) }; return value }
function pathOf(shipmentId) { return `/v1/stock-returns/my-receiving/${uuid(shipmentId)}/receipts` }
function displayTime(value) { return new Date(Date.parse(value)).toISOString().slice(0, 19).replace('T', ' ') + '（北京时间）' }
function reviewRows(lines, history) {
  return lines.map(row => {
    const source = history.package.lines.find(item => item.shipment_line_id === row.shipment_line_id)
    return { id: row.shipment_line_id, materialName: source.material_name, sku: source.sku_code, unit: source.base_unit,
      accepted: row.accepted_qty, rejected: row.rejected_qty, shortage: row.shortage_qty, damaged: row.damaged_qty,
      serialCount: row.accepted_serial_verifications.length + row.rejected_serial_ids.length + row.shortage_serial_ids.length,
      exceptionCount: row.exceptions.length }
  })
}
async function readHistory({ api, current, shipmentId, personId, authorizationVersion }) {
  await current()
  const history = receiving.validateHistory(await api.request(pathOf(shipmentId), contract.READ), { personId, shipmentId, authorizationVersion })
  await current(); return freeze(history)
}
async function prepareReceipt(args) {
  const history = await readHistory(args)
  const input = { operator_person_id: args.personId, received_at: args.receivedAt, reason: args.reason,
    lines: contract.buildLines(history, args.drafts) }
  const body = freeze(contract.payload(input, args.shipmentId))
  const raw = await args.api.request(pathOf(args.shipmentId) + '/preview', { ...contract.READ, method: 'POST', data: body })
  await args.current()
  const preview = freeze(contract.validatePreview(raw, input, history, args.shipmentId))
  return { history, body, preview, review: freeze({ title: '确认整组验收', description: '本次只登记退回包裹验收，不增加个人仓库存；异常凭证、短少和保管责任分开处理。',
    shipmentNo: history.package.shipment_no, receivedAt: displayTime(preview.received_at), reason: body.reason, rows: reviewRows(body.lines, history) }) }
}
async function submitReceipt(args) {
  const { api, store, workOrderId, shipmentId, personId, authorizationVersion, authorize, confirm } = args
  const drafts = freeze(JSON.parse(JSON.stringify(args.drafts)))
  return store.withLease({ work_order_id: workOrderId, shipment_id: shipmentId }, async lease => {
    if (lease.read().kind !== 'missing') throw new Error('请先核验该退回包裹的原请求。')
    const before = await authorize(), current = async () => { if (await authorize() !== before) throw new Error('access changed') }
    const prepared = await prepareReceipt({ ...args, drafts, current })
    if (!await confirm(prepared.review)) return { status: 'cancelled' }
    await current()
    const trace = api.createRequestId(), key = api.createIdempotencyKey()
    const marker = validateMarker({ v: 1, kind: contract.KIND, work_order_id: workOrderId, shipment_id: shipmentId,
      person_id: personId, authorization_version: authorizationVersion, operation_type: contract.ACTION, operation_id: prepared.preview.operation_id,
      trace_request_id: trace, request_hash: prepared.preview.request_hash, plan_hash: prepared.preview.plan_hash })
    lease.persist(marker)
    try {
      await api.postNoReplay(pathOf(shipmentId), { ...prepared.body, expected_plan_hash: marker.plan_hash, request_id: trace, idempotency_key: key }, { requestId: trace, idempotencyKey: key })
    } catch (_) { return { status: 'pending' } }
    await current()
    const result = await originalResult(api, marker)
    await current()
    if (result === null) return { status: 'pending' }
    lease.clearExact(marker)
    return result.lookup_status === 'sealed_not_executed' ? { status: 'sealed', seal: result.seal } : { status: 'confirmed', receipt: result }
  })
}
module.exports = { pathOf, readHistory, prepareReceipt, submitReceipt, displayTime, reviewRows }
