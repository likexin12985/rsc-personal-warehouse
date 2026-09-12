// Re-resolve every physical return against fresh own-order options. The parent
// binds both sides and all explicit pairs; only its original GET proves posting.
const { uuid, validateMaterialOptions } = require('./work-order-query-contract')
const { buildDraft, tracked } = require('./work-order-draft')
const { fromUnits, units } = require('./my-receipt-command')
const removed = require('./work-order-removed-scan')
const replacement = require('./work-order-replacement-command')
const { validateMarker } = require('./work-order-recovery-store')
const { originalResult } = require('./work-order-recovery')
const READ = { method: 'GET', noRefresh: true, header: { 'Cache-Control': 'no-store', Pragma: 'no-cache' } }
const CONDITION = { used: '旧件', damaged: '坏件' }
const IDENTITY = ['basis_stock_account_id', 'material_id', 'sku_code', 'material_name', 'base_unit',
  'condition_before', 'tracking_mode', 'quantity_scale', 'allow_fraction', 'lot_id', 'lot_no', 'serial_id', 'serial_no']
function freeze(value) {
  if (value && typeof value === 'object') { Object.values(value).forEach(freeze); Object.freeze(value) }
  return value
}
function snapshot(value) { return freeze(JSON.parse(JSON.stringify(value))) }
function consumeDraft(options, personId, drafts) {
  const lines = buildDraft({ workOrder: options.workOrder, items: options.items, personId, kind: 'consume', drafts })
  if (!options.openingEstablished || lines.some(line => !options.items.find(item => item.stock_account_id === line.stock_account_id).allowed_actions.includes('replace'))) {
    throw new Error('所选物料当前不能进行成对消耗与回收。')
  }
  return lines
}
function scanFor(row, personId) {
  return removed.scanPayload({ operator_person_id: personId, basis_stock_account_id: row.basisStockAccountId,
    condition_before: row.condition, sku_code: row.scan.sku_code, lot_no: row.scan.lot_no || null,
    serial_no: row.scan.serial_no || null, qr_code: row.scan.qr_code || null })
}
async function optionsFor({ api, workOrderId, personId, authorizationVersion, current }) {
  await current()
  const raw = await api.request(`/v1/work-orders/${workOrderId}/material-options`, READ)
  await current()
  const options = validateMaterialOptions(raw, personId, authorizationVersion, workOrderId)
  return { options, expected: { workOrderId, personId, authorizationVersion,
    sourceVersion: options.workOrder.source_version, ledgerCursor: raw.ledger_cursor } }
}
async function resolveRemoved({ api, row, expected, current }) {
  const scan = scanFor(row, expected.personId)
  await current()
  const raw = await api.request(`/v1/work-orders/${expected.workOrderId}/material-replacements/removed-part`,
    { ...READ, method: 'POST', data: scan })
  await current()
  return removed.validateRemoved(raw, { ...expected, scan })
}
function assemble({ workOrderId, personId, consumeLines, rows, expected }) {
  if (!Array.isArray(rows) || !rows.length || rows.length > 1000) throw new Error('请逐行登记本次拆回件。')
  const groups = new Map(), pairs = [], details = []
  for (const row of rows) {
    if (!row.candidate) throw new Error('请先识别每条拆回件，再核验整批。')
    const scan = scanFor(row, personId)
    const item = row.candidate
    const line = removed.recoveryLine(item, { ...expected, scan }, tracked(item) ? '1' : row.quantity)
    const key = [line.basis_stock_account_id, line.material_id, line.lot_id, line.condition_before].join('|')
    const prior = groups.get(key)
    if (prior) {
      if (!line.serial_ids.length || !prior.serial_ids.length) throw new Error('同一物料、批次和成色的数量拆回件请合并为一行。')
      prior.quantity = fromUnits(units(prior.quantity) + units(line.quantity))
      prior.serial_ids.push(...line.serial_ids); prior.serial_verifications.push(...line.serial_verifications)
    } else { groups.set(key, line); details.push(item) }
    if (tracked(item)) {
      if (!row.installedSerialId) throw new Error('请为每件拆回 SN 明确选择对应的投入 SN。')
      pairs.push({ installed_serial_id: uuid(row.installedSerialId), removed_serial_id: uuid(item.serial_id) })
    } else if (row.installedSerialId) throw new Error('数量拆回件不能绑定 SN 配对。')
  }
  const input = { workOrderId, personId, consumeLines, recoverLines: [...groups.values()], pairs }
  replacement.command(input)
  return { input, details }
}
async function prepareReplacement({ api, workOrderId, personId, authorizationVersion, drafts, removedDrafts, current }) {
  const { options, expected } = await optionsFor({ api, workOrderId, personId, authorizationVersion, current })
  const consumeLines = consumeDraft(options, personId, drafts)
  if (!Array.isArray(removedDrafts) || !removedDrafts.length || removedDrafts.length > 1000) throw new Error('请逐行登记本次拆回件。')
  const rows = []
  for (const row of removedDrafts) {
    if (!row.candidate || !consumeLines.some(line => line.stock_account_id === row.basisStockAccountId)) throw new Error('拆回件尚未识别或缺少对应的投入明细。')
    const candidate = await resolveRemoved({ api, row, expected, current })
    if (IDENTITY.some(key => row.candidate[key] !== candidate[key])) throw new Error('拆回件资料或管理策略已变化，请重新识别该件物料。')
    rows.push({ ...row, candidate })
  }
  const { input, details } = assemble({ workOrderId, personId, consumeLines, rows, expected })
  const body = freeze(replacement.payload(input)), digest = replacement.requestHash(input)
  await current()
  const raw = await api.request(`/v1/work-orders/${workOrderId}/material-replacements/preview`, { ...READ, method: 'POST', data: body })
  await current()
  const preview = replacement.validatePreview(raw, { ...expected, ...input })
  const consumeRows = consumeLines.map((line, index) => {
    const item = options.items.find(item => item.stock_account_id === line.stock_account_id)
    return { lineNo: index + 1, side: '投入消耗', basisLineNo: index + 1, materialName: item.material_name,
      sku: item.sku_code, condition: item.conditionLabel, lot: item.lot_no || '', quantity: line.quantity, unit: item.base_unit,
      serialNumbers: line.serial_verifications.map(proof => proof.serial_no) }
  })
  const recoverRows = input.recoverLines.map((line, index) => ({
    lineNo: consumeRows.length + index + 1, side: '拆回入库',
    basisLineNo: consumeLines.findIndex(item => item.stock_account_id === line.basis_stock_account_id) + 1,
    materialName: details[index].material_name, sku: details[index].sku_code, condition: CONDITION[line.condition_before],
    lot: details[index].lot_no || '', quantity: line.quantity, unit: details[index].base_unit,
    serialNumbers: line.serial_verifications.map(proof => proof.serial_no)
  }))
  const allProofs = consumeLines.concat(input.recoverLines).flatMap(line => line.serial_verifications)
  const review = freeze({ title: '确认成对消耗与回收', kind: 'replace', workOrderNo: options.workOrder.work_order_no,
    rows: [...consumeRows, ...recoverRows], pairs: input.pairs.map((pair, index) => ({ pairNo: index + 1,
      installed: allProofs.find(proof => proof.serial_id === pair.installed_serial_id).serial_no,
      removed: allProofs.find(proof => proof.serial_id === pair.removed_serial_id).serial_no })) })
  return { body, digest, review, preview }
}
async function submitReplacement({ api, store, workOrderId, personId, authorizationVersion, drafts, removedDrafts, authorize, confirm }) {
  const order = uuid(workOrderId), person = uuid(personId)
  if (!Number.isSafeInteger(authorizationVersion) || authorizationVersion < 1) throw new Error('提交身份无效。')
  const originalDrafts = snapshot(drafts), originalRemoved = snapshot(removedDrafts)
  return store.withLease({ work_order_id: order }, async lease => {
    if (lease.read().kind !== 'missing') throw new Error('请先核验该工单的原请求。')
    const before = await authorize()
    const current = async () => { if (await authorize() !== before) throw new Error('access changed') }
    const prepared = await prepareReplacement({ api, workOrderId: order, personId: person, authorizationVersion,
      drafts: originalDrafts, removedDrafts: originalRemoved, current })
    if (!await confirm(prepared.review)) return { status: 'cancelled' }
    await current()
    const trace = api.createRequestId(), key = api.createIdempotencyKey()
    if (typeof key !== 'string' || !/^wxidem-[a-f0-9]{36}$/.test(key)) throw new Error('未生成有效的提交标识，请重新核验。')
    const marker = validateMarker({ v: 1, kind: 'work_order_replacement', work_order_id: order, person_id: person,
      authorization_version: authorizationVersion, operation_type: 'replace', trace_request_id: trace, request_hash: prepared.digest })
    lease.persist(marker)
    try {
      await api.postNoReplay(`/v1/work-orders/${order}/material-replacements`,
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
module.exports = { consumeDraft, scanFor, optionsFor, resolveRemoved, assemble, prepareReplacement, submitReplacement }
