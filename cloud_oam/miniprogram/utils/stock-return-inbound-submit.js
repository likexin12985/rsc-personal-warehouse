const origin = require('./loss-return-origin')
const contract = require('./stock-return-inbound-contract')
const { readHistory } = require('./stock-return-receipt-submit')
const { validateMarker } = require('./work-order-recovery-store')
const { originalResult } = require('./work-order-recovery')
const { uuid } = require('./work-order-query-contract')

function freeze(value) { if (value && typeof value === 'object') { Object.values(value).forEach(freeze); Object.freeze(value) }; return value }
function pathOf(receiptId) { return `/v1/stock-returns/my-receiving/${uuid(receiptId)}/inbound` }
async function readInboundState({ api, current, receiptId, shipmentId, personId, authorizationVersion }) {
  const before = await current()
  const raw = await api.request(pathOf(receiptId), contract.READ)
  if (await current() !== before) throw new Error('入账记录或权限已变化，请刷新。')
  return freeze(contract.validateState(raw, { receiptId, shipmentId, personId, authorizationVersion }))
}
async function prepareInbound({ api, current, receiptId, shipmentId, personId, authorizationVersion }) {
  const before = await current()
  const state = await readInboundState({ api, current, receiptId, shipmentId, personId, authorizationVersion })
  if (state.status === 'posted') return { state, preview: null, review: null }
  const history = await readHistory({ api, current, shipmentId, personId, authorizationVersion })
  const receipt = history.receipts.find(row => row.receipt_id === uuid(receiptId))
  if (!receipt) throw new Error('原验收记录不可核验，请刷新。')
  const raw = await api.request(pathOf(receiptId) + '/preview', { ...contract.READ, method: 'POST' })
  if (await current() !== before) throw new Error('入账记录或权限已变化，请刷新。')
  const preview = freeze(contract.validatePreview(raw, { receiptId, shipmentId, personId, authorizationVersion, receipt }))
  return { preview, review: freeze({ title: '确认退回入账', description: '仅将已验收的退回数量从在途账户转入当前区域仓账户；不执行普通需求收货或 OAM 收货。',
    receiptId: preview.receipt_id, shipmentId: preview.shipment_id, target: preview.target_location_id,
    rows: preview.lines.map(row => ({ id: row.receipt_line_id + ':' + row.condition_code, condition: { new: '新件', used: '旧件', damaged: '坏件' }[row.condition_code], quantity: row.accepted_qty, serialCount: row.serial_ids.length })) }) }
}
async function submitInbound({ api, store, workOrderId, lossOrigin, receiptId, shipmentId, personId, authorizationVersion, expectedPlanHash, authorize, confirm }) {
  return store.withLease(origin.scope(workOrderId, lossOrigin, shipmentId), async lease => {
    if (lease.read().kind !== 'missing') throw new Error('请先核验该退回入账请求的原结果。')
    const before = await authorize(), current = async () => { if (await authorize() !== before) throw new Error('access changed') }
    const prepared = await prepareInbound({ api, current, receiptId, shipmentId, personId, authorizationVersion })
    if (prepared.state && prepared.state.status === 'posted') return { status: 'already_posted', state: prepared.state }
    if (lossOrigin === undefined ? origin.isLoss(prepared.preview) : !origin.sameSource(prepared.preview, { origin: lossOrigin })) throw new Error('报损来源已变化，请重新核验。')
    if (prepared.preview.plan_hash !== expectedPlanHash) throw new Error('入账方案已变化，请重新预览并确认。')
    if (!await confirm(prepared.review)) return { status: 'cancelled' }
    await current()
    const trace = api.createRequestId(), key = api.createIdempotencyKey()
    const requestHash = contract.requestHash(receiptId, prepared.preview.plan_hash, trace)
    const marker = validateMarker({ v: 1, kind: contract.KIND, ...origin.scope(workOrderId, lossOrigin, shipmentId),
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
module.exports = { pathOf, readInboundState, prepareInbound, submitInbound }
