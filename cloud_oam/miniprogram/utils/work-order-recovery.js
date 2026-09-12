const { uuid } = require('./work-order-query-contract')
const { validateLookup } = require('./work-order-command')
const READ = { method: 'GET', noRefresh: true, header: { 'Cache-Control': 'no-store', Pragma: 'no-cache' } }

async function recoverPending({ api, store, workOrderId, personId, authorize }) {
  const order = uuid(workOrderId), person = uuid(personId)
  return store.withLease({ work_order_id: order }, async lease => {
    const stored = lease.read()
    if (stored.kind === 'missing') return { status: 'missing' }
    if (stored.kind !== 'valid' || stored.value.person_id !== person) throw new Error('原工单恢复记录与当前人员不一致。')
    const marker = stored.value, before = await authorize()
    const raw = await api.request(`/v1/work-orders/${order}/material-operations/${marker.operation_type}/by-request/${marker.trace_request_id}`, READ)
    const result = validateLookup(raw, marker)
    if (await authorize() !== before) throw new Error('核验期间身份或权限发生变化，请保留原请求。')
    if (result === null) return { status: 'pending' }
    lease.clearExact(marker)
    return outcome(result)
  })
}

function outcome(result) {
  return result.lookup_status === 'sealed_not_executed' ? { status: 'sealed', seal: result.seal } : { status: 'confirmed', command: result }
}

async function sealPending({ api, store, workOrderId, personId, authorize, confirm }) {
  const order = uuid(workOrderId), person = uuid(personId)
  return store.withLease({ work_order_id: order }, async lease => {
    const stored = lease.read()
    if (stored.kind !== 'valid' || stored.value.person_id !== person) throw new Error('原工单恢复记录与当前人员不一致。')
    const marker = stored.value, before = await authorize()
    const path = `/v1/work-orders/${order}/material-operations/${marker.operation_type}/by-request/${marker.trace_request_id}`
    let result = validateLookup(await api.request(path, READ), marker)
    if (await authorize() !== before) throw new Error('access changed')
    if (result !== null) { lease.clearExact(marker); return outcome(result) }
    if (!await confirm()) return { status: 'cancelled' }
    if (await authorize() !== before) throw new Error('access changed')
    // The server arbitrates against the original stock writer. Neither an
    // absent GET nor a failed POST permits discarding this durable marker.
    await api.postSealNoReplay(path + '/seal', { operator_person_id: person, request_hash: marker.request_hash }, { requestId: marker.trace_request_id })
    if (await authorize() !== before) throw new Error('access changed')
    result = validateLookup(await api.request(path, READ), marker)
    if (await authorize() !== before) throw new Error('access changed')
    if (result === null) return { status: 'pending' }
    lease.clearExact(marker)
    return outcome(result)
  })
}

module.exports = { recoverPending, sealPending }
