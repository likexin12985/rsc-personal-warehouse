const { uuid } = require('./work-order-query-contract')
const { validateLookup } = require('./work-order-command')
const replacement = require('./work-order-replacement-command')
const registration = require('./work-order-removed-registration-command')
const reversal = require('./work-order-reversal-command')
const stockReturn = require('./stock-return-contract')
const departure = require('./stock-return-outbound-contract')
const parcel = require('./stock-return-shipment-contract')
const READ = { method: 'GET', noRefresh: true, header: { 'Cache-Control': 'no-store', Pragma: 'no-cache' } }
function originalPath(marker) {
  if (marker.kind === parcel.KIND && marker.operation_type === parcel.ACTION) return `/v1/work-orders/${marker.work_order_id}/returns/${marker.operation_id}/shipments/by-request/${marker.trace_request_id}`
  if (marker.kind === departure.KIND && marker.operation_type === departure.ACTION) return `/v1/work-orders/${marker.work_order_id}/returns/${marker.operation_id}/outbounds/by-request/${marker.trace_request_id}`
  if (marker.kind === stockReturn.KIND) return `/v1/work-orders/${marker.work_order_id}/returns${marker.operation_type === 'cancel_return' ? '/' + marker.operation_id + '/cancellations' : ''}/by-request/${marker.trace_request_id}`
  if (marker.kind === reversal.KIND) return `/v1/work-orders/${marker.work_order_id}/material-reversals/by-request/${marker.trace_request_id}`
  if (marker.kind === registration.KIND) return `/v1/work-orders/${marker.work_order_id}/material-replacements/removed-registrations/by-request/${marker.trace_request_id}`
  return marker.kind === 'work_order_replacement'
    ? `/v1/work-orders/${marker.work_order_id}/material-replacements/by-request/${marker.trace_request_id}`
    : `/v1/work-orders/${marker.work_order_id}/material-operations/${marker.operation_type}/by-request/${marker.trace_request_id}`
}
async function originalResult(api, marker) {
  const paired = marker.kind === 'work_order_replacement'
  const registering = marker.kind === registration.KIND
  const reversing = marker.kind === reversal.KIND
  const returning = marker.kind === stockReturn.KIND
  let raw
  try { raw = await api.request(originalPath(marker), READ) } catch (error) {
    // Only this exact server response means no parent is currently observed.
    // It never clears storage or permits a stock POST; sealing arbitrates next.
    if (paired && error.responseReceived === true && error.status === 404 && error.code === 'replacement_not_found') return null
    if (registering && error.responseReceived === true && error.status === 404 && error.code === 'removed_registration_not_found') return null
    if (reversing && error.responseReceived === true && error.status === 404 && error.code === 'work_order_reversal_not_observed') return null
    if (returning && error.responseReceived === true && error.status === 404 && error.code === 'stock_return_not_observed') return null
    throw error
  }
  return returning ? (marker.operation_type === parcel.ACTION ? parcel : marker.operation_type === departure.ACTION ? departure : stockReturn).validateLookup(raw, marker) : reversing ? reversal.validateLookup(raw, marker) : registering ? registration.validateLookup(raw, marker) : paired ? replacement.validateLookup(raw, marker) : validateLookup(raw, marker)
}

async function recoverPending({ api, store, workOrderId, personId, authorize }) {
  const order = uuid(workOrderId), person = uuid(personId)
  return store.withLease({ work_order_id: order }, async lease => {
    const stored = lease.read()
    if (stored.kind === 'missing') return { status: 'missing' }
    if (stored.kind !== 'valid' || stored.value.person_id !== person) throw new Error('原工单恢复记录与当前人员不一致。')
    const marker = stored.value, before = await authorize()
    const result = await originalResult(api, marker)
    if (await authorize() !== before) throw new Error('核验期间身份或权限发生变化，请保留原请求。')
    if (result === null) return { status: 'pending' }
    lease.clearExact(marker)
    return outcome(result)
  })
}

function outcome(result) {
  return ['sealed', 'sealed_not_executed'].includes(result.lookup_status) ? { status: 'sealed', seal: result.seal } : { status: 'confirmed', command: result }
}

async function sealPending({ api, store, workOrderId, personId, authorize, confirm }) {
  const order = uuid(workOrderId), person = uuid(personId)
  return store.withLease({ work_order_id: order }, async lease => {
    const stored = lease.read()
    if (stored.kind !== 'valid' || stored.value.person_id !== person) throw new Error('原工单恢复记录与当前人员不一致。')
    const marker = stored.value, before = await authorize()
    const path = originalPath(marker)
    let result = await originalResult(api, marker)
    if (await authorize() !== before) throw new Error('access changed')
    if (result !== null) { lease.clearExact(marker); return outcome(result) }
    if (!await confirm()) return { status: 'cancelled' }
    if (await authorize() !== before) throw new Error('access changed')
    // The server arbitrates against the original stock writer. Neither an
    // absent GET nor a failed POST permits discarding this durable marker.
    await api.postSealNoReplay(path + '/seal', { operator_person_id: person, request_hash: marker.request_hash }, { requestId: marker.trace_request_id })
    if (await authorize() !== before) throw new Error('access changed')
    result = await originalResult(api, marker)
    if (await authorize() !== before) throw new Error('access changed')
    if (result === null) return { status: 'pending' }
    lease.clearExact(marker)
    return outcome(result)
  })
}

module.exports = { recoverPending, sealPending, originalResult }
