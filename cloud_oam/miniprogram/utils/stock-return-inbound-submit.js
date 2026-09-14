const contract = require('./stock-return-inbound-contract')
const { validateMarker } = require('./work-order-recovery-store')
const { originalResult } = require('./work-order-recovery')
const { uuid } = require('./work-order-query-contract')

function freeze(value) { if (value && typeof value === 'object') { Object.values(value).forEach(freeze); Object.freeze(value) }; return value }
function pathOf(receiptId) { return `/v1/stock-returns/my-receiving/${uuid(receiptId)}/inbound` }
async function prepareInbound({ api, current, receiptId, shipmentId, personId }) {
  await current()
  const raw = await api.request(pathOf(receiptId) + '/preview', { ...contract.READ, method: 'POST' })
  await current()
  const preview = freeze(contract.validatePreview(raw, { receiptId, shipmentId, personId }))
  return { preview, review: freeze({ title: '确认退回入账', description: '仅将已验收的退回数量从在途账户转入当前区域仓账户；不执行普通需求收货或 OAM 收货。',
    receiptId: preview.receipt_id, shipmentId: preview.shipment_id, target: preview.target_location_id,
    rows: preview.lines.map(row => ({ id: row.receipt_line_id, quantity: row.accepted_qty, serialCount: row.serial_ids.length })) }) }
}
async function submitInbound({ api, store, workOrderId, receiptId, shipmentId, personId, authorizationVersion, authorize, confirm }) {
  return store.withLease({ work_order_id: workOrderId, shipment_id: shipmentId }, async lease => {
    if (lease.read().kind !== 'missing') throw new Error('请先核验该退回入账请求的原结果。')
    const before = await authorize(), current = async () => { if (await authorize() !== before) throw new Error('access changed') }
    const prepared = await prepareInbound({ api, current, receiptId, shipmentId, personId })
    if (!await confirm(prepared.review)) return { status: 'cancelled' }
    await current()
    const trace = api.createRequestId(), key = api.createIdempotencyKey()
    const requestHash = contract.requestHash(receiptId, prepared.preview.plan_hash, trace)
    const marker = validateMarker({ v: 1, kind: contract.KIND, work_order_id: workOrderId, shipment_id: shipmentId,
      receipt_id: receiptId, person_id: personId, authorization_version: authorizationVersion, operation_type: contract.ACTION,
      trace_request_id: trace, request_hash: requestHash, plan_hash: prepared.preview.plan_hash })
    lease.persist(marker)
    try {
      await api.postNoReplay(pathOf(receiptId), { operator_person_id: personId, expected_plan_hash: marker.plan_hash,
        request_id: trace, idempotency_key: key }, { requestId: trace, idempotencyKey: key })
    } catch (_) { return { status: 'pending' } }
    await current()
    const result = await originalResult(api, marker)
    await current()
    if (result === null) return { status: 'pending' }
    lease.clearExact(marker)
    return result.lookup_status === 'sealed' ? { status: 'sealed', seal: result.seal } : { status: 'confirmed', inbound: result }
  })
}
module.exports = { pathOf, prepareInbound, submitInbound }
