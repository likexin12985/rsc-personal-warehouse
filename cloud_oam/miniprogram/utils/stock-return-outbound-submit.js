const contract = require('./stock-return-outbound-contract')
const { validateMarker } = require('./work-order-recovery-store')
const { originalResult } = require('./work-order-recovery')
const { conditionLabel } = require('./inventory-contract')
const { uuid } = require('./work-order-query-contract')
const READ = { method: 'GET', noRefresh: true, header: { 'Cache-Control': 'no-store', Pragma: 'no-cache' } }
function freeze(value) { if (value && typeof value === 'object') { Object.values(value).forEach(freeze); Object.freeze(value) }; return value }
function pathOf(args) { return `/v1/work-orders/${uuid(args.workOrderId)}/returns/${uuid(args.operationId)}/outbounds` }
function displayTime(value) { return new Date(Date.parse(contract.instant(value)) + 8 * 60 * 60 * 1000).toISOString().slice(0, 19).replace('T', ' ') + '（北京时间）' }
function reviewRows(lines) { return lines.map((row, index) => ({ id: row.operation_line_id, lineNo: index + 1, materialName: row.material_name,
  sku: row.sku_code, condition: conditionLabel(row.condition_code), quantity: row.selected_quantity, unit: row.base_unit, lot: row.lot_no || '',
  remaining: row.remaining_quantity, serials: row.selected_serials.map(sn => ({ id: sn.serial_id, number: sn.serial_no })) })) }
async function readDeparture({ api, current, ...expected }) {
  await current()
  const history = freeze(contract.validateHistory(await api.request(pathOf(expected), READ), expected))
  await current(); return history
}
async function prepareDeparture(args) {
  const { api, current } = args
  const history = await readDeparture(args)
  if (history.cancellation || history.outbound_status === 'outbound') throw new Error('原退回已取消或已全部发出，请刷新原记录。')
  const choices = contract.validateOptions(await api.request(pathOf(args) + '/options', READ), args, history)
  await current()
  const input = { ...args, lines: contract.buildLines(choices, args.drafts) }
  const body = freeze(contract.payload(input))
  const raw = await api.request(pathOf(args) + '/preview', { ...READ, method: 'POST', data: body })
  await current()
  const preview = freeze(contract.validatePreview(raw, input, choices))
  return { body, preview, review: freeze({ title: '确认实物发出', description: '仅登记本次实物已从个人仓发往在途位置。接收仓验收前，个人保管责任仍保留。',
    workOrderNo: history.original.operation_no, target: preview.destination.target_location_name, transit: preview.destination.transit_location_name,
    outboundAt: preview.outbound_at, outboundAtLabel: displayTime(preview.outbound_at), reason: body.reason, rows: reviewRows(preview.lines) }) }
}
async function submitDeparture(args) {
  const { api, store, workOrderId, operationId, personId, authorizationVersion, authorize, confirm } = args
  const drafts = freeze(JSON.parse(JSON.stringify(args.drafts)))
  return store.withLease({ work_order_id: workOrderId }, async lease => {
    if (lease.read().kind !== 'missing') throw new Error('请先核验该工单的原请求。')
    const before = await authorize(), current = async () => { if (await authorize() !== before) throw new Error('access changed') }
    const prepared = await prepareDeparture({ ...args, drafts, current })
    if (!await confirm(prepared.review)) return { status: 'cancelled' }
    await current()
    const trace = api.createRequestId(), key = api.createIdempotencyKey()
    if (typeof key !== 'string' || !/^wxidem-[a-f0-9]{36}$/.test(key)) throw new Error('发出提交标识生成失败。')
    const marker = validateMarker({ v: 1, kind: contract.KIND, work_order_id: workOrderId, person_id: personId,
      authorization_version: authorizationVersion, operation_type: contract.ACTION, trace_request_id: trace,
      request_hash: prepared.preview.request_hash, plan_hash: prepared.preview.plan_hash, operation_id: operationId })
    lease.persist(marker)
    try { await api.postNoReplay(pathOf(args), { ...prepared.body, expected_plan_hash: marker.plan_hash,
      request_id: trace, idempotency_key: key }, { requestId: trace, idempotencyKey: key }) }
    catch (_) { return { status: 'pending' } }
    await current(); const result = await originalResult(api, marker); await current()
    if (result === null) return { status: 'pending' }
    lease.clearExact(marker)
    return result.lookup_status === 'sealed' ? { status: 'sealed', seal: result.seal } : { status: 'confirmed', command: result }
  })
}
module.exports = { readDeparture, prepareDeparture, submitDeparture, reviewRows, displayTime }
