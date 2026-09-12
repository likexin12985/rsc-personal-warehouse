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
    return { status: 'confirmed', command: result }
  })
}

module.exports = { recoverPending }
