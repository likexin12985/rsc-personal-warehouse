const { uuid } = require('./work-order-query-contract')
const { optionsFor, scanFor } = require('./work-order-replacement-submit')
const registration = require('./work-order-removed-registration-command')
const { validateMarker } = require('./work-order-recovery-store')
const { originalResult } = require('./work-order-recovery')
const READ = { method: 'POST', noRefresh: true, header: { 'Cache-Control': 'no-store', Pragma: 'no-cache' } }
function freeze(value) {
  if (value && typeof value === 'object') { Object.values(value).forEach(freeze); Object.freeze(value) }
  return value
}
async function prepareRegistration({ api, workOrderId, personId, authorizationVersion, row, current }) {
  const scan = scanFor(row, personId), input = { workOrderId, personId, scan }
  registration.command(input)
  const { options, expected } = await optionsFor({ api, workOrderId, personId, authorizationVersion, current })
  const basis = options.items.find(item => item.stock_account_id === scan.basis_stock_account_id)
  if (!options.openingEstablished || !options.workOrder.can_operate || !basis || !basis.allowed_actions.includes('replace')) {
    throw new Error('对应投入明细已无可操作占用，请刷新工单后核验。')
  }
  const body = freeze(registration.payload(input)), digest = registration.requestHash(input)
  await current()
  const raw = await api.request(`/v1/work-orders/${workOrderId}/material-replacements/removed-registrations/preview`, { ...READ, data: body })
  await current()
  const result = registration.validatePreview(raw, { ...expected, scan })
  return { body, digest, review: freeze({ title: '确认登记拆回 SN', kind: 'register_removed', workOrderNo: options.workOrder.work_order_no,
    description: '本次只登记拆回件身份，库存不会增加。登记后仍需识别、配对并确认回收入库。',
    rows: [{ lineNo: 1, side: '拆回身份登记', basisName: basis.material_name, basisSku: basis.sku_code,
      basisCondition: basis.conditionLabel, basisLot: basis.lot_no || '',
      materialName: result.material_name, sku: result.sku_code, condition: result.condition_before === 'used' ? '旧件' : '坏件',
      lot: result.lot_no || '', quantity: '1', unit: result.base_unit, serialNumbers: [result.serial_no] }], pairs: [] }) }
}
async function submitRegistration({ api, store, workOrderId, personId, authorizationVersion, row, authorize, confirm }) {
  const order = uuid(workOrderId), person = uuid(personId)
  if (!Number.isSafeInteger(authorizationVersion) || authorizationVersion < 1) throw new Error('提交身份无效。')
  const physical = freeze(JSON.parse(JSON.stringify(row)))
  return store.withLease({ work_order_id: order }, async lease => {
    if (lease.read().kind !== 'missing') throw new Error('请先核验该工单的原请求。')
    const before = await authorize()
    const current = async () => { if (await authorize() !== before) throw new Error('access changed') }
    const prepared = await prepareRegistration({ api, workOrderId: order, personId: person, authorizationVersion, row: physical, current })
    if (!await confirm(prepared.review)) return { status: 'cancelled' }
    await current()
    const trace = api.createRequestId(), key = api.createIdempotencyKey()
    if (typeof key !== 'string' || !/^wxidem-[a-f0-9]{36}$/.test(key)) throw new Error('未生成有效的登记标识，请重新核验。')
    const marker = validateMarker({ v: 1, kind: registration.KIND, work_order_id: order, person_id: person,
      authorization_version: authorizationVersion, operation_type: 'register_removed', trace_request_id: trace, request_hash: prepared.digest })
    lease.persist(marker)
    try {
      await api.postNoReplay(`/v1/work-orders/${order}/material-replacements/removed-registrations`,
        { ...prepared.body, idempotency_key: key, request_id: trace }, { requestId: trace, idempotencyKey: key })
    } catch (_) { return { status: 'pending' } }
    await current()
    const result = await originalResult(api, marker)
    await current()
    if (result === null) return { status: 'pending' }
    lease.clearExact(marker)
    return result.lookup_status === 'sealed_not_executed' ? { status: 'sealed', seal: result.seal } : { status: 'confirmed', command: result }
  })
}
module.exports = { prepareRegistration, submitRegistration }
