const { uuid } = require('./work-order-query-contract')
const reversal = require('./work-order-reversal-command')
const { optionsFor } = require('./work-order-replacement-submit')
const { validateMarker } = require('./work-order-recovery-store')
const { originalResult } = require('./work-order-recovery')
const { conditionLabel } = require('./inventory-contract')
const READ = { method: 'POST', noRefresh: true, header: { 'Cache-Control': 'no-store', Pragma: 'no-cache' } }
function freeze(value) { if (value && typeof value === 'object') { Object.values(value).forEach(freeze); Object.freeze(value) }; return value }
const EFFECT = { occupy: '撤回原占用，恢复本人可用库存', release: '撤回原释放，恢复本工单占用', consume: '撤回原消耗，恢复本工单占用和实物库存', recover: '撤回原拆回入库，重新核对回收责任' }
async function prepareReversal({ api, workOrderId, personId, authorizationVersion, selection, reason, current }) {
  const input = { workOrderId, personId, selection, reason }, body = freeze(reversal.payload(input))
  const { options, expected } = await optionsFor({ api, workOrderId, personId, authorizationVersion, current })
  if (!options.workOrder.can_operate || !options.openingEstablished) throw new Error('当前工单或个人仓不允许冲销。')
  await current()
  const raw = await api.request(`/v1/work-orders/${workOrderId}/material-reversals/preview`, { ...READ, data: body })
  await current()
  const preview = freeze(reversal.validatePreview(raw, { ...expected, ...input }))
  const serialNames = new Map(preview.children.flatMap(child => child.movements.flatMap(row => row.serials.map(sn => [sn.serial_id, sn.serial_no]))))
  let number = 0
  const review = freeze({ title: '确认整笔冲销', kind: 'reverse', workOrderNo: options.workOrder.work_order_no,
    description: `冲销原因：${body.reason}。请核对以下整组反向明细。退回及接收仍须按实物交接处理。`,
    rows: preview.children.flatMap(child => child.movements.map(row => ({ lineNo: ++number,
      side: `冲销${reversal.LABELS[child.original_operation_type]} · ${child.original_operation_no}`,
      materialName: row.material_name, sku: row.sku_code, condition: conditionLabel(row.condition_code), lot: row.lot_no || '',
      quantity: row.quantity, unit: row.base_unit, serialNumbers: row.serials.map(sn => sn.serial_no),
      effect: EFFECT[child.original_operation_type], serialEffects: row.serials.map(sn => `${sn.serial_no}：${sn.lifecycle_after === 'consumed' ? '恢复为此前已消耗状态' : child.original_operation_type === 'recover' ? '恢复至库外，身份登记保留' : '恢复为可管理库存'}`)
    }))), pairs: preview.replacement_pairs.map((pair, index) => ({ pairNo: index + 1, installed: serialNames.get(pair.installed_serial_id), removed: serialNames.get(pair.removed_serial_id) })) })
  return { body, digest: preview.request_hash, planHash: preview.plan_hash, preview, review }
}
async function submitReversal({ api, store, workOrderId, personId, authorizationVersion, selection, reason, authorize, confirm }) {
  const order = uuid(workOrderId), person = uuid(personId)
  const input = reversal.command({ workOrderId: order, personId: person, selection, reason })
  const chosen = freeze({ original_operation_id: input.original_operation_id, original_replacement_id: input.original_replacement_id })
  if (!Number.isSafeInteger(authorizationVersion) || authorizationVersion < 1) throw new Error('提交身份无效。')
  return store.withLease({ work_order_id: order }, async lease => {
    if (lease.read().kind !== 'missing') throw new Error('请先核验该工单的原请求。')
    const before = await authorize()
    const current = async () => { if (await authorize() !== before) throw new Error('access changed') }
    const prepared = await prepareReversal({ api, workOrderId: order, personId: person, authorizationVersion,
      selection: chosen, reason: input.reason, current })
    if (!await confirm(prepared.review)) return { status: 'cancelled' }
    await current()
    const trace = api.createRequestId(), key = api.createIdempotencyKey()
    if (typeof key !== 'string' || !/^wxidem-[a-f0-9]{36}$/.test(key)) throw new Error('未生成有效的冲销提交标识。')
    const marker = validateMarker({ v: 1, kind: reversal.KIND, work_order_id: order, person_id: person,
      authorization_version: authorizationVersion, operation_type: 'reverse', trace_request_id: trace,
      request_hash: prepared.digest, plan_hash: prepared.planHash })
    lease.persist(marker)
    try {
      await api.postNoReplay(`/v1/work-orders/${order}/material-reversals`,
        { ...prepared.body, expected_plan_hash: prepared.planHash, idempotency_key: key, request_id: trace }, { requestId: trace, idempotencyKey: key })
    } catch (_) { return { status: 'pending' } }
    await current()
    const result = await originalResult(api, marker)
    await current()
    if (result === null) return { status: 'pending' }
    lease.clearExact(marker)
    return result.lookup_status === 'sealed_not_executed' ? { status: 'sealed', seal: result.seal } : { status: 'confirmed', command: result }
  })
}
module.exports = { prepareReversal, submitReversal }
