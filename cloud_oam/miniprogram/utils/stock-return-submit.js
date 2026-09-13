const contract = require('./stock-return-contract')
const { validateMarker } = require('./work-order-recovery-store')
const { originalResult } = require('./work-order-recovery')
const { conditionLabel } = require('./inventory-contract')
const READ = { method: 'GET', noRefresh: true, header: { 'Cache-Control': 'no-store', Pragma: 'no-cache' } }
function freeze(value) { if (value && typeof value === 'object') { Object.values(value).forEach(freeze); Object.freeze(value) }; return value }
function reviewRows(lines) { return lines.map((row, index) => ({ id: row.source.source_recovery_line_id, lineNo: index + 1,
  materialName: row.source.material_name, sku: row.source.sku_code, condition: conditionLabel(row.source.condition_code),
  quantity: row.selected_quantity, unit: row.source.base_unit, lot: row.source.lot_no || '',
  originalNo: row.source.recovery_operation_no, serials: row.selected_serials.map(sn => ({ id: sn.serial_id, number: sn.serial_no })) })) }
async function prepareReturn({ api, workOrderId, personId, authorizationVersion, targetLocationId, transitLocationId, reason, drafts, current }) {
  await current()
  const expected = { workOrderId, personId, authorizationVersion }
  const choices = contract.validateOptions(await api.request(`/v1/work-orders/${workOrderId}/returns/options`, READ), expected)
  await current()
  if (!choices.destinations.some(row => row.target_location_id === targetLocationId && row.transit_location_id === transitLocationId)) throw new Error('所选接收仓或在途位置已变化，请重新选择。')
  const input = { ...expected, targetLocationId, transitLocationId, reason, lines: contract.buildLines(choices, drafts) }
  const body = freeze(contract.payload(input))
  const raw = await api.request(`/v1/work-orders/${workOrderId}/returns/preview`, { ...READ, method: 'POST', data: body })
  await current()
  const preview = freeze(contract.validatePreview(raw, input, choices))
  return { body, preview, review: freeze({ title: '确认退回占用', description: '确认后物料进入待退回，个人保管责任仍保留。发出和对方接收另行记录。',
    reason: body.reason, target: preview.destination.target_location_name, transit: preview.destination.transit_location_name,
    workOrderNo: choices.workOrder.work_order_no, rows: reviewRows(preview.lines) }) }
}
async function submitReturn(args) {
  const { api, store, workOrderId, personId, authorizationVersion, authorize, confirm } = args
  // Copy the page draft before asynchronous reads or user confirmation.
  const drafts = freeze(JSON.parse(JSON.stringify(args.drafts)))
  return store.withLease({ work_order_id: workOrderId }, async lease => {
    if (lease.read().kind !== 'missing') throw new Error('请先核验该工单的原请求。')
    const before = await authorize(), current = async () => { if (await authorize() !== before) throw new Error('access changed') }
    const prepared = await prepareReturn({ ...args, drafts, current })
    if (!await confirm(prepared.review)) return { status: 'cancelled' }
    await current()
    const trace = api.createRequestId(), key = api.createIdempotencyKey()
    if (typeof key !== 'string' || !/^wxidem-[a-f0-9]{36}$/.test(key)) throw new Error('退回提交标识生成失败。')
    const marker = validateMarker({ v: 1, kind: contract.KIND, work_order_id: workOrderId, person_id: personId,
      authorization_version: authorizationVersion, operation_type: 'submit_return', trace_request_id: trace,
      request_hash: prepared.preview.request_hash, plan_hash: prepared.preview.plan_hash, operation_id: null })
    lease.persist(marker)
    try { await api.postNoReplay(`/v1/work-orders/${workOrderId}/returns`, { ...prepared.body,
      expected_plan_hash: marker.plan_hash, request_id: trace, idempotency_key: key }, { requestId: trace, idempotencyKey: key }) }
    catch (_) { return { status: 'pending' } }
    await current(); const result = await originalResult(api, marker); await current()
    if (result === null) return { status: 'pending' }
    lease.clearExact(marker)
    return result.lookup_status === 'sealed' ? { status: 'sealed', seal: result.seal } : { status: 'confirmed', command: result }
  })
}
async function cancelReturn({ api, store, workOrderId, personId, authorizationVersion, operationId, reason, authorize, confirm }) {
  const explanation = contract.reason(reason)
  return store.withLease({ work_order_id: workOrderId }, async lease => {
    if (lease.read().kind !== 'missing') throw new Error('请先核验该工单的原请求。')
    const before = await authorize(), current = async () => { if (await authorize() !== before) throw new Error('access changed') }
    const rows = contract.validateHistory(await api.request(`/v1/work-orders/${workOrderId}/returns`, READ), { workOrderId, personId, authorizationVersion })
    await current()
    const row = rows.find(item => item.original.operation_id === operationId)
    if (!row || row.cancellation) throw new Error('原退回单不存在或已经取消，请刷新历史。')
    if (!await confirm(freeze({ title: '确认取消未发出退回', workOrderNo: row.original.operation_no, reason: explanation,
      description: '仅取消本单尚未发出的占用。原回收件的应退责任仍保留。', target: row.original.destination.target_location_name,
      transit: row.original.destination.transit_location_name, rows: reviewRows(row.original.lines) }))) return { status: 'cancelled' }
    await current()
    const trace = api.createRequestId(), key = api.createIdempotencyKey()
    if (typeof key !== 'string' || !/^wxidem-[a-f0-9]{36}$/.test(key)) throw new Error('取消提交标识生成失败。')
    const marker = validateMarker({ v: 1, kind: contract.KIND, work_order_id: workOrderId, person_id: personId,
      authorization_version: authorizationVersion, operation_type: 'cancel_return', trace_request_id: trace,
      request_hash: contract.hash({ operation_id: operationId, operator_person_id: personId, reason: explanation }), plan_hash: null, operation_id: operationId })
    lease.persist(marker)
    try { await api.postNoReplay(`/v1/work-orders/${workOrderId}/returns/${operationId}/cancellations`,
      { operator_person_id: personId, reason: explanation, request_id: trace, idempotency_key: key }, { requestId: trace, idempotencyKey: key }) }
    catch (_) { return { status: 'pending' } }
    await current(); const result = await originalResult(api, marker); await current()
    if (result === null) return { status: 'pending' }
    lease.clearExact(marker)
    return result.lookup_status === 'sealed' ? { status: 'sealed', seal: result.seal } : { status: 'confirmed', command: result }
  })
}
module.exports = { prepareReturn, submitReturn, cancelReturn, reviewRows }
