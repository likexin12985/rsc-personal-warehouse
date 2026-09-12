// An ordinary stock command is sent once, after its original recovery marker
// has survived storage readback. Only the original GET proves its result.
const { uuid, validateMaterialOptions } = require('./work-order-query-contract')
const { buildDraft, validatePreview } = require('./work-order-draft')
const { payload, requestHash, validateLookup, KINDS } = require('./work-order-command')
const { validateMarker } = require('./work-order-recovery-store')
const READ = { method: 'GET', noRefresh: true, header: { 'Cache-Control': 'no-store', Pragma: 'no-cache' } }
const LABELS = { occupy: '投入占用', consume: '实际消耗', release: '释放未用物料' }

function freeze(value) {
  if (value && typeof value === 'object') { Object.values(value).forEach(freeze); Object.freeze(value) }
  return value
}

async function submitDraft({ api, store, workOrderId, personId, authorizationVersion, kind, drafts, authorize, confirm }) {
  const order = uuid(workOrderId), person = uuid(personId)
  if (!KINDS.includes(kind) || !Number.isSafeInteger(authorizationVersion) || authorizationVersion < 1) throw new Error('工单提交身份或操作无效。')
  const originalDraft = freeze(JSON.parse(JSON.stringify(drafts)))
  return store.withLease({ work_order_id: order }, async lease => {
    if (lease.read().kind !== 'missing') throw new Error('请先核验该工单的原请求。')
    const before = await authorize()
    const current = async () => { if (await authorize() !== before) throw new Error('access changed') }
    const rawOptions = await api.request(`/v1/work-orders/${order}/material-options`, READ)
    const options = validateMaterialOptions(rawOptions, person, authorizationVersion, order)
    const lines = freeze(buildDraft({ workOrder: options.workOrder, items: options.items, personId: person, kind, drafts: originalDraft }))
    const body = freeze(payload(kind, order, person, lines))
    const digest = requestHash(kind, order, person, lines)
    await current()
    const endpoint = `/v1/work-orders/${order}/material-operations/${kind}`
    const rawPreview = await api.request(endpoint + '/preview', { ...READ, method: 'POST', data: body })
    validatePreview(rawPreview, { workOrderId: order, personId: person, authorizationVersion, kind,
      lines, sourceVersion: options.workOrder.source_version, ledgerCursor: rawOptions.ledger_cursor })
    await current()
    const review = freeze({ title: `确认${LABELS[kind]}`, kind, workOrderNo: options.workOrder.work_order_no,
      rows: lines.map((line, index) => {
        const item = options.items.find(item => item.stock_account_id === line.stock_account_id)
        return { lineNo: index + 1, materialName: item.material_name, sku: item.sku_code, condition: item.conditionLabel,
          lot: item.lot_no || '', quantity: line.quantity, unit: item.base_unit,
          serialNumbers: line.serial_verifications.map(proof => proof.serial_no) }
      }) })
    if (!await confirm(review)) return { status: 'cancelled' }
    await current()
    const trace = api.createRequestId(), key = api.createIdempotencyKey()
    if (typeof key !== 'string' || !/^wxidem-[a-f0-9]{36}$/.test(key)) throw new Error('未生成有效的提交标识，请重新核验。')
    const marker = validateMarker({ v: 1, kind: 'work_order_material', work_order_id: order, person_id: person,
      authorization_version: authorizationVersion, operation_type: kind, trace_request_id: trace, request_hash: digest })
    lease.persist(marker)
    try {
      await api.postNoReplay(endpoint, { ...body, idempotency_key: key, request_id: trace }, { requestId: trace, idempotencyKey: key })
    } catch (_) {
      // A timeout or 4xx cannot prove non-execution. Keep the same marker and
      // let the user recover or seal the original request, never retry a POST.
      return { status: 'pending' }
    }
    await current()
    const raw = await api.request(endpoint + '/by-request/' + trace, READ)
    const result = validateLookup(raw, marker)
    await current()
    if (result === null) return { status: 'pending' }
    lease.clearExact(marker)
    return result.lookup_status === 'sealed_not_executed' ? { status: 'sealed', seal: result.seal } : { status: 'confirmed', command: result }
  })
}

module.exports = { submitDraft }
