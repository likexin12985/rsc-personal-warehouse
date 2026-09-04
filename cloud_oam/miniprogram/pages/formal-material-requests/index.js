const session = require('../../utils/session')
const adapterModule = require('../../utils/material-request-adapter')
const contract = require('../../utils/material-request-contract')
const materialCatalog = require('../../utils/material-catalog-contract')
const materialRequestOptions = require('../../utils/material-request-option-contract')
const formalFileUpload = require('../../utils/formal-file-upload')
const lifecycleRecovery = require('../../utils/material-request-lifecycle-recovery')
const supplyRecovery = require('../../utils/material-request-supply-recovery')

const transport = adapterModule.formalMaterialRequestAdapter
const lifecycleRuntime = {
  registry: contract.createMaterialRequestIntentRegistry(),
  context: null,
  confirmedClosures: new Map(),
  recovery: {
    generation: 0,
    inFlight: null
  }
}

const AXES = [
  ['request_status', '申请'],
  ['allocation_status', '分配'],
  ['reservation_status', '占用'],
  ['outbound_status', '出库'],
  ['shipment_status', '发货'],
  ['logistics_signature_status', '物流签收'],
  ['oam_receipt_status', 'OAM收货'],
  ['personal_inbound_status', 'RSC/个人仓入库'],
  ['notification_status', '通知送达'],
  ['reconciliation_status', '对账同步']
]

const STATUS_LABELS = {
  draft: '草稿',
  submitted: '已提交',
  approval_in_progress: '审批中',
  returned: '已退回',
  partially_approved: '部分批准',
  approved: '已批准',
  rejected: '已驳回',
  withdrawn: '已撤回',
  cancellation_pending: '取消处理中',
  cancelled: '已取消'
}
const NONZERO_UUID = /^(?!00000000-0000-0000-0000-000000000000$)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const AWARE_TIMESTAMP = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/
const SAFE_EXTERNAL_REFERENCE = /^[A-Za-z0-9][A-Za-z0-9._:/@+\-]{0,199}$/
const ACTIVE_WITHDRAW_STEP_STATUSES = new Set([
  'pending', 'open', 'awaiting_external_evidence', 'evidence_pending_verification'
])
const LIFECYCLE_RECOVERY_DELAYS_MS = [0, 100, 300]

function nextLineKey(page) {
  page._lineKey = (page._lineKey || 0) + 1
  return page._lineKey
}

function emptyLine(page) {
  return {
    key: nextLineKey(page),
    material: null,
    requestedQty: '',
    requiredDate: '',
    substituteMaterial: null,
    note: ''
  }
}

function emptyForm(page) {
  return {
    workOrder: null,
    workOrderUnavailable: false,
    purpose: '',
    urgency: 'normal',
    expectedDate: '',
    provinceCode: '',
    provinceName: '',
    cityName: '',
    districtName: '',
    addressDetail: '',
    contactName: '',
    contactMobile: '',
    attachmentFileIds: [],
    note: '',
    lines: [emptyLine(page)]
  }
}

function draftToForm(page, draft, materials, workOrderResolution) {
  return {
    workOrder: workOrderResolution.workOrder,
    workOrderUnavailable: workOrderResolution.workOrderUnavailable,
    purpose: draft.purpose,
    urgency: draft.urgency,
    expectedDate: draft.expected_date || '',
    provinceCode: draft.address.province_code,
    provinceName: draft.address.province_name,
    cityName: draft.address.city_name,
    districtName: draft.address.district_name,
    addressDetail: draft.address.detail,
    contactName: draft.contact.name,
    contactMobile: draft.contact.mobile,
    attachmentFileIds: draft.attachment_file_ids.slice(),
    note: draft.note,
    lines: draft.lines.map((line) => ({
      key: nextLineKey(page),
      material: materials.get(line.material_id) || null,
      requestedQty: line.requested_qty,
      requiredDate: line.required_date || '',
      substituteMaterial: line.suggested_substitute_material_id
        ? materials.get(line.suggested_substitute_material_id) || null
        : null,
      note: line.note
    }))
  }
}

function formToDraft(form, uploadedFiles = []) {
  if (form.workOrderUnavailable) {
    throw new Error('原关联 OAM 工单当前不可用，请先明确清除或重新选择')
  }
  const attachmentFileIds = Array.from(new Set(form.attachmentFileIds.concat(
    uploadedFiles.map((file) => file.file_id)
  )))
  return contract.validateMaterialRequestDraftInput({
    work_order_id: form.workOrder
      ? materialRequestOptions.validateItem(form.workOrder).work_order_id
      : null,
    purpose: form.purpose,
    urgency: form.urgency,
    expected_date: form.expectedDate || null,
    address: {
      province_code: form.provinceCode,
      province_name: form.provinceName,
      city_name: form.cityName,
      district_name: form.districtName,
      detail: form.addressDetail
    },
    contact: {
      name: form.contactName,
      mobile: form.contactMobile
    },
    attachment_file_ids: attachmentFileIds,
    note: form.note,
    lines: form.lines.map((line) => ({
      material_id: line.material ? line.material.material_id : '',
      requested_qty: line.requestedQty,
      required_date: line.requiredDate || null,
      suggested_substitute_material_id: line.substituteMaterial
        ? line.substituteMaterial.material_id
        : null,
      note: line.note
    }))
  })
}

function presentSummary(item) {
  return Object.assign({}, item, {
    statusLabel: STATUS_LABELS[item.states.request_status] || item.states.request_status,
    maskedContact: `${item.contact_masked.name_masked} · ${item.contact_masked.mobile_masked}`,
    maskedAddress: `${item.address_snapshot.province_name}${item.address_snapshot.city_name}${item.address_snapshot.district_name}${item.address_snapshot.detail_masked}`
  })
}

function trackingLabel(mode) {
  return {
    none: '不追踪',
    lot: '批次',
    serial: '序列号',
    lot_and_serial: '批次+序列号'
  }[mode] || '未知策略'
}

function presentMaterial(item) {
  return Object.assign({}, item, {
    trackingLabel: trackingLabel(item.tracking_mode),
    fractionLabel: item.allow_fraction ? '允许小数' : '仅整数'
  })
}

function currentApprovalStep(detail) {
  const stepId = detail.approval_instance && detail.approval_instance.current_step_id
  return stepId
    ? detail.approval_instance.steps.find((step) => step.step_id === stepId) || null
    : null
}

function approvalInputLines(detail) {
  const step = currentApprovalStep(detail)
  if (!step) throw new Error('当前审批步骤锚点缺失，已停止处理')
  const predecessor = step.predecessor_step_id
    ? detail.approval_instance.steps.find((item) => item.step_id === step.predecessor_step_id)
    : null
  const previous = new Map(
    ((predecessor && predecessor.line_decisions) || [])
      .map((item) => [item.request_line_id, item.approved_qty])
  )
  const rows = detail.lines.map((line) => {
    const inputQty = step.step_no === 1 ? line.requested_qty : previous.get(line.request_line_id)
    if (!inputQty) throw new Error('审批前序逐行数量不完整，已停止处理')
    return {
      requestLineId: line.request_line_id,
      inputQty,
      decisionQty: inputQty,
      reason: ''
    }
  })
  if (step.step_no > 1 && previous.size !== detail.lines.length) {
    throw new Error('审批前序逐行数量与当前修订不一致，已停止处理')
  }
  return rows
}

function decimalUnits(value) {
  if (!/^(?:0|[1-9]\d{0,14})(?:\.\d{1,3})?$/.test(value)) return null
  const [whole, fraction = ''] = value.split('.')
  return { whole, fraction: fraction.padEnd(3, '0') }
}

function compareDecimal(left, right) {
  if (left.whole.length !== right.whole.length) return left.whole.length - right.whole.length
  if (left.whole !== right.whole) return left.whole < right.whole ? -1 : 1
  if (left.fraction === right.fraction) return 0
  return left.fraction < right.fraction ? -1 : 1
}

function supplyQuantityUnits(value) {
  const parsed = decimalUnits(value)
  if (!parsed) throw new Error('供给计划数量格式无效')
  return BigInt(parsed.whole + parsed.fraction)
}

function supplyRemaining(detail, line) {
  return supplyQuantityUnits(line.final_approved_qty) - supplyQuantityUnits(line.cancelled_qty)
    - detail.supply_tasks.filter((task) => task.request_line_id === line.request_line_id
      && !['cancelled', 'closed_no_supply'].includes(task.status))
      .reduce((total, task) => total + supplyQuantityUnits(task.original_equivalent_qty), BigInt(0))
}

function approvalLinePayload(process) {
  if (process.action === 'reject') return { lines: [], return_lines: [] }
  return process.lines.reduce((result, line) => {
    const input = decimalUnits(line.inputQty)
    const decision = decimalUnits(line.decisionQty)
    if (
      input === null ||
      decision === null ||
      compareDecimal(decision, input) > 0 ||
      (process.action === 'return' && decision.whole === '0' && decision.fraction === '000')
    ) throw new Error('逐行处理数量无效或超过当前级可处理数量')
    if (process.action === 'approve') {
      if (compareDecimal(decision, input) < 0 && !line.reason.trim()) {
        throw new Error('部分驳回的明细必须填写原因')
      }
      result.lines.push({
        request_line_id: line.requestLineId,
        approved_qty: line.decisionQty,
        reason: line.reason.trim()
      })
    } else {
      if (!line.reason.trim()) throw new Error('退回的每条明细必须填写重审原因')
      result.return_lines.push({
        request_line_id: line.requestLineId,
        requested_reapproval_qty: line.decisionQty,
        reason: line.reason.trim()
      })
    }
    return result
  }, { lines: [], return_lines: [] })
}

function isPositiveDecimal(value) {
  const parsed = decimalUnits(value)
  return parsed !== null && (parsed.whole !== '0' || parsed.fraction !== '000')
}

function cancellationInputLines(detail) {
  return detail.lines
    .filter((line) => isPositiveDecimal(line.final_approved_qty))
    .map((line) => ({
      requestLineId: line.request_line_id,
      lineNo: line.line_no,
      materialId: line.material_id,
      cancelledQty: line.final_approved_qty,
      reason: ''
    }))
}

function lifecycleConfirmationFromPending(before, intent) {
  if (
    !before || !intent || intent.request_id !== before.request_id ||
    !['withdraw', 'cancel'].includes(intent.action) ||
    intent.expected_version !== before.request_version ||
    !intent.body || intent.body.expected_version !== before.request_version ||
    typeof intent.body.reason !== 'string'
  ) return null
  let lines = []
  if (intent.action === 'cancel') {
    if (!Array.isArray(intent.body.lines)) return null
    const byId = new Map(intent.body.lines.map((line) => [line.request_line_id, line]))
    const expected = cancellationInputLines(before)
    if (
      byId.size !== intent.body.lines.length ||
      expected.length !== intent.body.lines.length ||
      expected.some((line) => {
        const pendingLine = byId.get(line.requestLineId)
        return !pendingLine || pendingLine.cancelled_qty !== line.cancelledQty ||
          typeof pendingLine.reason !== 'string'
      })
    ) return null
    lines = expected.map((line) => Object.assign({}, line, {
      reason: byId.get(line.requestLineId).reason
    }))
  }
  return {
    action: intent.action,
    title: intent.action === 'withdraw' ? '二次确认撤回需求' : '二次确认安全取消',
    requestId: before.request_id,
    expectedVersion: before.request_version,
    reason: intent.body.reason,
    lines,
    pendingMessage: `结果仍未确认（${intent.headers['X-Request-ID']}）；禁止生成新坐标或执行其他动作。`,
    error: ''
  }
}

function lifecycleResultNotice(action) {
  return action === 'withdraw'
    ? '需求已撤回并精确回读；其他九个状态轴未被合并。'
    : '需求已安全取消并精确回读；取消明细与最终批准量一致。'
}

function lifecycleContextMatches(context, before, confirmation, identity) {
  return Boolean(
    context && before && confirmation &&
    context.identity === identity &&
    context.before.request_id === before.request_id &&
    context.before.request_version === before.request_version &&
    context.intent.request_id === before.request_id &&
    context.intent.expected_version === before.request_version &&
    context.intent.action === confirmation.action &&
    confirmation.requestId === before.request_id &&
    confirmation.expectedVersion === before.request_version
  )
}

function pendingLifecycleContext() {
  const context = lifecycleRuntime.context
  if (!context || context.status !== 'pending') return null
  const pending = lifecycleRuntime.registry.get(context.intent.request_id)
  return pending && pending.signature === context.intent.signature ? context : null
}

function consumeLifecycleCompletion(page, context, access) {
  if (
    !context || context.status !== 'confirmed' || !context.terminalDetail ||
    context.identity !== accessUploadIdentity(access) ||
    lifecycleRuntime.context !== context
  ) return false
  page._lifecycleBefore = null
  page._lifecyclePendingIdentity = ''
  page._lifecycleRecoveryBlocked = false
  page.setData({
    detail: presentDetail(context.terminalDetail, access, false),
    lifecycleConfirm: null,
    lifecycleRecoveryMessage: '',
    supplyForm: null,
    supplyRecoveryMessage: '',
    supplyBlocked: false,
    notice: lifecycleResultNotice(context.intent.action)
  })
  lifecycleRuntime.context = null
  return true
}

function lifecyclePageIsActive(page, generation) {
  return !page._unloaded && (page._lifecycleGeneration || 0) === generation
}

function approvalNotice(detail) {
  const current = detail.approval_instance && detail.approval_instance.current_step_id
  const step = current
    ? detail.approval_instance.steps.find((item) => item.step_id === current)
    : null
  if (
    step &&
    step.source_mode === 'external_registration' &&
    step.status === 'awaiting_external_evidence'
  ) return '外部审批证据尚未登记；禁止据此推进供给或履约。'
  if (detail.states.request_status === 'returned') {
    return '申请已退回；请按退回意见修改后重新提交，不会自动进入分配或履约。'
  }
  return '审批、供给计划与实际履约是独立事实；本页不推断后续状态。'
}

function presentDetail(detail, access, lifecycleBlocked = false) {
  const editable = ['draft', 'returned'].includes(detail.states.request_status)
  const step = currentApprovalStep(detail)
  const actions = new Set(detail.allowed_actions)
  const internalPermission = step && (
    step.step_no === 1
      ? access.can_approve_region
      : step.step_no === 2 && access.can_approve_headquarters
  )
  const evidence = (
    detail.approval_instance && detail.approval_instance.external_evidence_summaries
  ) || []
  const pendingEvidence = evidence.filter((item) => (
    step && item.step_id === step.step_id && item.status === 'pending_verification'
  ))
  return Object.assign({}, detail, {
    statusLabel: STATUS_LABELS[detail.states.request_status] || detail.states.request_status,
    maskedContact: `${detail.contact_masked.name_masked} · ${detail.contact_masked.mobile_masked}`,
    maskedAddress: `${detail.address_snapshot.province_name}${detail.address_snapshot.city_name}${detail.address_snapshot.district_name}${detail.address_snapshot.detail_masked}`,
    stateAxes: AXES.map(([key, label]) => ({ key, label, value: detail.states[key] })),
    approvalSteps: detail.approval_instance ? detail.approval_instance.steps : [],
    canEdit: editable && detail.allowed_actions.includes('update'),
    canSubmit: editable && detail.allowed_actions.includes('submit'),
    canWithdraw: !lifecycleBlocked && access.can_withdraw && actions.has('withdraw'),
    canCancel: !lifecycleBlocked && access.can_cancel && actions.has('cancel'),
    canCreateSupply: access.can_manage_supply && actions.has('create_supply_task'),
    supplyTaskViews: detail.supply_tasks.map((task) => Object.assign({}, task, {
      canManage: access.can_manage_supply && task.allowed_actions.length > 0
    })),
    canProcessInternal: !!(
      step &&
      step.source_mode === 'internal' &&
      internalPermission &&
      (actions.has('approve') || actions.has('return') || actions.has('reject'))
    ),
    canRegisterExternal: !!(
      step && step.source_mode === 'external_registration' &&
      access.can_register_external && actions.has('register_external_approval')
    ),
    canVerifyExternal: !!(
      step && step.source_mode === 'external_registration' &&
      access.can_verify_external && actions.has('verify_external_approval') &&
      pendingEvidence.length === 1
    ),
    verificationBlocked: !!(
      access.can_verify_external && actions.has('verify_external_approval') &&
      pendingEvidence.length !== 1
    ),
    approvalNotice: approvalNotice(detail)
  })
}

function axesMatch(result, detail) {
  return result.request_version === detail.request_version &&
    AXES.every(([key]) => result.states[key] === detail.states[key])
}

function draftWriteMatches(result, detail, draft) {
  return axesMatch(result, detail) &&
    result.revision_id === detail.current_revision_id &&
    result.revision_no === detail.current_revision_no &&
    detail.work_order_id === draft.work_order_id
}

function approvalMutationMatches(result, detail) {
  const instance = detail.approval_instance
  return axesMatch(result, detail) &&
    result.revision_id === detail.current_revision_id &&
    result.revision_no === detail.current_revision_no &&
    result.approval_instance_id === (instance ? instance.instance_id : null) &&
    result.approval_attempt_no === (instance ? instance.attempt_no : null) &&
    result.current_step_id === (instance ? instance.current_step_id : null)
}

function recoveredLifecycleMatches(command, detail) {
  const expectedStatus = command.action === 'withdraw' ? 'withdrawn' : 'cancelled'
  return command.request_id === detail.request_id &&
    command.current_step_id === null &&
    detail.states.request_status === expectedStatus &&
    detail.allowed_actions.length === 0 &&
    approvalMutationMatches(command, detail)
}

function lifecycleIntentMatchesCommand(intent, command, xRequestId) {
  return Boolean(
    intent && command &&
    intent.headers['X-Request-ID'] === xRequestId &&
    intent.action === command.action &&
    intent.request_id === command.request_id &&
    intent.expected_version + 1 === command.request_version
  )
}

function waitForLifecycleRecovery(delayMs) {
  if (!delayMs) return Promise.resolve()
  return new Promise((resolve) => setTimeout(resolve, delayMs))
}

function retryableLifecycleStatusError(error) {
  return Boolean(
    error && (
      error.status === 0 ||
      error.status === 408 ||
      error.status === 425 ||
      error.status === 429 ||
      (Number.isInteger(error.status) && error.status >= 500)
    )
  )
}

function lifecycleCompletionFact(result) {
  return Object.freeze({
    action: result.action,
    request_id: result.request_id,
    request_version: result.request_version,
    revision_id: result.revision_id,
    revision_no: result.revision_no,
    approval_instance_id: result.approval_instance_id,
    approval_attempt_no: result.approval_attempt_no,
    current_step_id: result.current_step_id,
    states: Object.freeze(Object.assign({}, result.states))
  })
}

function rememberLifecycleCompletion(xRequestId, result) {
  const closures = lifecycleRuntime.confirmedClosures
  closures.set(xRequestId, lifecycleCompletionFact(result))
  while (closures.size > 16) closures.delete(closures.keys().next().value)
}

function lifecycleCompletionMatches(xRequestId, result, detail) {
  const fact = lifecycleRuntime.confirmedClosures.get(xRequestId)
  return Boolean(
    fact &&
    fact.action === result.action &&
    fact.request_id === result.request_id &&
    fact.request_version === result.request_version &&
    fact.revision_id === result.revision_id &&
    fact.revision_no === result.revision_no &&
    fact.approval_instance_id === result.approval_instance_id &&
    fact.approval_attempt_no === result.approval_attempt_no &&
    fact.current_step_id === result.current_step_id &&
    AXES.every(([key]) => fact.states[key] === result.states[key]) &&
    recoveredLifecycleMatches(result, detail)
  )
}

function clearConfirmedLifecycleSentinel(xRequestId, result, detail) {
  try {
    lifecycleRecovery.clearLifecycleSentinel(xRequestId)
    rememberLifecycleCompletion(xRequestId, result)
    return
  } catch (error) {
    if (
      error &&
      error.code === 'material_request_lifecycle_recovery_sentinel_missing' &&
      lifecycleCompletionMatches(xRequestId, result, detail)
    ) return
    throw error
  }
}

async function recoverPersistedLifecycle(sentinel) {
  const tracker = lifecycleRuntime.recovery
  if (tracker.inFlight) {
    if (tracker.inFlight.xRequestId !== sentinel.x_request_id) {
      throw new Error('并发恢复批次的请求标识不一致，已失败关闭')
    }
    return tracker.inFlight.promise
  }
  tracker.generation += 1
  const generation = tracker.generation
  const batchPromise = Promise.resolve().then(async () => {
    const freshIdentity = await transport.loadIdentity()
    const access = adapterModule.validateAccess(await transport.loadAccess(freshIdentity))
    if (!access.can_read) {
      throw new Error('当前新鲜授权不包含正式需求读取权限，恢复哨兵已保留')
    }
    const activeRuntimeContext = lifecycleRuntime.context
    if (
      activeRuntimeContext &&
      activeRuntimeContext.identity !== accessUploadIdentity(access)
    ) {
      throw new Error('需求终止期间身份或授权版本已变化，恢复哨兵已保留并失败关闭')
    }
    let lastError = null
    let sawNotObserved = false
    let attempts = 0
    while (attempts < LIFECYCLE_RECOVERY_DELAYS_MS.length) {
      const delayMs = LIFECYCLE_RECOVERY_DELAYS_MS[attempts]
      attempts += 1
      await waitForLifecycleRecovery(delayMs)
      let status
      try {
        status = await transport.lifecycleCommandStatus(sentinel.x_request_id)
      } catch (error) {
        lastError = error
        if (retryableLifecycleStatusError(error)) continue
        if (error && error.responseReceived === true) break
        throw error
      }
      if (status.lookup_status === 'not_observed') {
        sawNotObserved = true
        continue
      }
      const commandPermission = status.command.action === 'withdraw'
        ? access.can_withdraw
        : access.can_cancel
      if (!commandPermission) {
        throw new Error(
          '新鲜授权不包含已确认终止命令对应的撤回或取消权限，恢复哨兵已保留'
        )
      }
      const detail = contract.validateMaterialRequestDetail(
        await transport.detail(status.command.request_id),
        status.command.request_id
      )
      if (!recoveredLifecycleMatches(status.command, detail)) {
        throw new Error(
          '已确认终止命令与新鲜详情的版本、修订、审批锚点或十状态轴不一致，恢复哨兵已保留'
        )
      }
      const pending = lifecycleRuntime.registry.get(status.command.request_id)
      const runtimeContext = lifecycleRuntime.context
      if (pending) {
        if (
          !runtimeContext || runtimeContext.status !== 'pending' ||
          runtimeContext.intent.signature !== pending.signature ||
          !lifecycleIntentMatchesCommand(pending, status.command, sentinel.x_request_id) ||
          !lifecycleIntentMatchesCommand(
            runtimeContext.intent,
            status.command,
            sentinel.x_request_id
          )
        ) throw new Error('恢复命令与当前身份、授权或内存写意图坐标不一致，已失败关闭')
      } else if (runtimeContext) {
        if (
          runtimeContext.status !== 'confirmed' ||
          !lifecycleIntentMatchesCommand(
            runtimeContext.intent,
            status.command,
            sentinel.x_request_id
          ) ||
          !lifecycleCompletionMatches(sentinel.x_request_id, status.command, detail)
        ) throw new Error('恢复命令与已确认运行时收口坐标不一致，已失败关闭')
      }
      clearConfirmedLifecycleSentinel(
        sentinel.x_request_id,
        status.command,
        detail
      )
      if (pending) {
        lifecycleRuntime.registry.confirm(pending.request_id, pending.signature)
      }
      if (runtimeContext) lifecycleRuntime.context = null
      return Object.freeze({
        status: 'confirmed',
        access,
        command: status.command,
        detail
      })
    }
    const message = sawNotObserved
      ? `服务端尚未观察到终止命令（${sentinel.x_request_id}）；已保留最小恢复哨兵并禁止新的撤回或取消。`
      : `终止命令查询结果仍不确定（${sentinel.x_request_id}）；已保留最小恢复哨兵并禁止新的撤回或取消。`
    return Object.freeze({
      status: 'pending',
      access,
      command: null,
      detail: null,
      error: lastError,
      message
    })
  })
  tracker.inFlight = Object.freeze({
    xRequestId: sentinel.x_request_id,
    generation,
    promise: batchPromise
  })
  try {
    return await batchPromise
  } finally {
    const current = tracker.inFlight
    if (
      current &&
      current.promise === batchPromise &&
      current.generation === generation &&
      current.xRequestId === sentinel.x_request_id
    ) {
      tracker.inFlight = null
    }
  }
}

function sameProjection(left, right) {
  return JSON.stringify(left) === JSON.stringify(right)
}

function lifecycleInvariantProjectionMatches(before, detail) {
  return before.schema_version === detail.schema_version &&
    before.request_id === detail.request_id &&
    before.request_no === detail.request_no &&
    before.current_revision_id === detail.current_revision_id &&
    before.current_revision_no === detail.current_revision_no &&
    before.work_order_id === detail.work_order_id &&
    before.requester_person_id === detail.requester_person_id &&
    before.requester_org_id === detail.requester_org_id &&
    before.purpose === detail.purpose &&
    before.urgency === detail.urgency &&
    before.expected_date === detail.expected_date &&
    before.note === detail.note &&
    before.approval_mode === detail.approval_mode &&
    before.created_at === detail.created_at &&
    before.submitted_at === detail.submitted_at &&
    sameProjection(before.address_snapshot, detail.address_snapshot) &&
    sameProjection(before.contact_masked, detail.contact_masked) &&
    sameProjection(before.attachment_refs, detail.attachment_refs) &&
    sameProjection(before.revision_history, detail.revision_history) &&
    sameProjection(before.supply_tasks, detail.supply_tasks) &&
    before.approval_history.length === detail.approval_history.length &&
    sameProjection(
      before.approval_history.slice(0, -1),
      detail.approval_history.slice(0, -1)
    )
}

function lifecycleMutationMatches(result, before, detail, action, cancellationLines) {
  const expectedStatus = action === 'withdraw' ? 'withdrawn' : 'cancelled'
  const beforeInstance = before.approval_instance
  const afterInstance = detail.approval_instance
  if (
    !approvalMutationMatches(result, detail) ||
    !lifecycleInvariantProjectionMatches(before, detail) ||
    result.current_step_id !== null ||
    detail.states.request_status !== expectedStatus ||
    !beforeInstance ||
    !afterInstance ||
    result.approval_instance_id !== beforeInstance.instance_id ||
    result.approval_attempt_no !== beforeInstance.attempt_no ||
    detail.current_revision_id !== before.current_revision_id ||
    detail.current_revision_no !== before.current_revision_no ||
    AXES.slice(1).some(([key]) => detail.states[key] !== before.states[key]) ||
    detail.allowed_actions.length !== 0 ||
    detail.lines.length !== before.lines.length
  ) return false
  if (action === 'withdraw') {
    const cancellableStepCount = beforeInstance.steps.filter(
      (step) => ACTIVE_WITHDRAW_STEP_STATUSES.has(step.status)
    ).length
    const expectedInstance = Object.assign({}, beforeInstance, {
      status: 'withdrawn',
      current_step_no: null,
      current_step_id: null,
      version: beforeInstance.version + 1,
      steps: beforeInstance.steps.map((step) => (
        ACTIVE_WITHDRAW_STEP_STATUSES.has(step.status)
          ? Object.assign({}, step, {
            status: 'cancelled',
            decided_at: null,
            version: step.version + 1
          })
          : step
      ))
    })
    return cancellableStepCount > 0 &&
      sameProjection(afterInstance, expectedInstance) &&
      sameProjection(detail.lines, before.lines)
  }
  const requested = new Map(cancellationLines.map((line) => [
    line.request_line_id,
    line.cancelled_qty
  ]))
  const positiveBefore = before.lines.filter((line) => isPositiveDecimal(line.final_approved_qty))
  if (
    requested.size !== cancellationLines.length ||
    requested.size !== positiveBefore.length ||
    positiveBefore.some((line) => requested.get(line.request_line_id) !== line.final_approved_qty)
  ) return false
  const expectedLines = before.lines.map((line) => Object.assign({}, line, {
    cancelled_qty: line.final_approved_qty,
    status: 'cancelled',
    version: line.version + 1
  }))
  return ['completed', 'returned'].includes(beforeInstance.status) &&
    sameProjection(afterInstance, beforeInstance) &&
    sameProjection(detail.lines, expectedLines)
}

function toast(error, fallback) {
  wx.showToast({ title: (error && error.message) || fallback, icon: 'none' })
}

function ensureRegistries(page) {
  if (!page._createRegistry) {
    page._createRegistry = contract.createMaterialRequestCreateIntentRegistry()
  }
  if (!page._mutationRegistry) {
    page._mutationRegistry = contract.createMaterialRequestIntentRegistry()
  }
}

function ensureFileUploadControllers(page) {
  if (!page._requestAttachmentUploads) {
    page._requestAttachmentUploads = formalFileUpload.createFormalFileUploadController({
      purpose: 'request_attachment',
      multiple: true,
      onChange(snapshot) {
        if (page._unloaded) return
        page.setData({
          requestUploadFiles: snapshot.files,
          requestUploadBlocking: snapshot.blocking,
          requestUploadCanChoose: snapshot.canChoose
        })
      }
    })
  }
  if (!page._externalEvidenceUploads) {
    page._externalEvidenceUploads = formalFileUpload.createFormalFileUploadController({
      purpose: 'external_approval_evidence',
      multiple: false,
      onChange(snapshot) {
        if (page._unloaded) return
        page.setData({
          externalUploadFiles: snapshot.files,
          externalUploadBlocking: snapshot.blocking,
          externalUploadCanChoose: snapshot.canChoose
        })
      }
    })
  }
}

function availableFiles(controller, expectedPurpose) {
  const snapshot = controller.snapshot()
  if (snapshot.blocking) throw new Error('附件尚未完成 available 严格确认，已停止业务写入')
  for (const file of snapshot.availableFiles) {
    if (
      !NONZERO_UUID.test(file.file_id) ||
      file.purpose !== expectedPurpose ||
      file.status !== 'available'
    ) throw new Error('附件用途或 available 状态与当前业务不一致')
  }
  return snapshot.availableFiles
}

function clearFileUploadMemory(page) {
  if (page._requestAttachmentUploads) page._requestAttachmentUploads.clear()
  if (page._externalEvidenceUploads) page._externalEvidenceUploads.clear()
}

function accessUploadIdentity(access) {
  return `${access.person_id}:${access.authorization_version}`
}

function exactScalarSnapshotMatches(left, right) {
  if (!left || !right || typeof left !== 'object' || typeof right !== 'object') return false
  const snapshot = (value) => Object.keys(value).sort().map((key) => [key, value[key]])
  return JSON.stringify(snapshot(left)) === JSON.stringify(snapshot(right))
}

function editContextIsCurrent(page, generation, identity, detail) {
  const access = page._access
  const currentDetail = page.data.detail
  return Boolean(
    !page._unloaded &&
    page._editGeneration === generation &&
    identity &&
    page._uploadIdentity === identity &&
    access &&
    accessUploadIdentity(access) === identity &&
    currentDetail &&
    currentDetail.request_id === detail.request_id &&
    currentDetail.request_version === detail.request_version
  )
}

Page({
  data: {
    loading: true,
    busy: false,
    accessAllowed: false,
    canCreate: false,
    accessMessage: '正在校验正式需求权限',
    requests: [],
    detail: null,
    formMode: '',
    formRequestId: '',
    formRequestVersion: 0,
    form: null,
    requestUploadFiles: [],
    requestUploadBlocking: false,
    requestUploadCanChoose: true,
    workOrderPicker: null,
    materialPicker: null,
    processing: null,
    externalUploadFiles: [],
    externalUploadBlocking: false,
    externalUploadCanChoose: true,
    submitConfirm: false,
    lifecycleConfirm: null,
    lifecycleRecoveryMessage: '',
    writePending: false,
    pendingWriteMessage: '',
    notice: ''
  },

  onLoad() {
    this._lifecycleRecoveryBlocked = false
    try {
      const sentinel = lifecycleRecovery.readLifecycleSentinel()
      if (sentinel) {
        this._lifecycleRecoveryBlocked = true
        this.setData({
          lifecycleRecoveryMessage: `检测到待核验的需求终止操作（${sentinel.x_request_id}），将在新鲜身份与权限校验后查询服务端命令状态。`
        })
      }
    } catch (error) {
      this._lifecycleRecoveryBlocked = true
      this.setData({ lifecycleRecoveryMessage: error.message })
    }
  },

  onShow() {
    if (!session.ensureLogin()) return
    this._unloaded = false
    this.load()
  },

  onPullDownRefresh() {
    this.load().finally(() => wx.stopPullDownRefresh())
  },

  onUnload() {
    this._loadGeneration = (this._loadGeneration || 0) + 1
    this._lifecycleGeneration = (this._lifecycleGeneration || 0) + 1
    this._editGeneration = (this._editGeneration || 0) + 1
    this._editBusyGeneration = 0
    this._workOrderPickerGeneration = (this._workOrderPickerGeneration || 0) + 1
    this._pickerGeneration = (this._pickerGeneration || 0) + 1
    this._unloaded = true
    this._createRegistry = null
    this._mutationRegistry = null
    this._access = null
    this._uploadIdentity = ''
    this._lifecycleBefore = null
    this._lifecyclePendingIdentity = ''
    clearFileUploadMemory(this)
    this._requestAttachmentUploads = null
    this._externalEvidenceUploads = null
    Object.assign(this.data, {
      form: null,
      workOrderPicker: null,
      materialPicker: null,
      processing: null,
      detail: null,
      submitConfirm: false,
      lifecycleConfirm: null
    })
  },

  async load() {
    this._unloaded = false
    const generation = (this._loadGeneration || 0) + 1
    this._loadGeneration = generation
    this._editGeneration = (this._editGeneration || 0) + 1
    if (this._editBusyGeneration) {
      this._editBusyGeneration = 0
      this.setData({ busy: false })
    }
    this._workOrderPickerGeneration = (this._workOrderPickerGeneration || 0) + 1
    ensureRegistries(this)
    this.supplyWriteBlocked()
    this._access = null
    this.setData({
      loading: true,
      accessAllowed: false,
      canCreate: false,
      accessMessage: '正在校验正式需求权限',
      requests: [],
      detail: null,
      workOrderPicker: null,
      lifecycleConfirm: null,
      lifecycleRecoveryMessage: '',
      notice: ''
    })
    try {
      const sentinel = lifecycleRecovery.readLifecycleSentinel()
      let recovery = null
      let access
      if (sentinel) {
        this._lifecycleRecoveryBlocked = true
        this.setData({
          lifecycleRecoveryMessage: `正在核验需求终止操作（${sentinel.x_request_id}）；不会生成替代坐标。`
        })
        recovery = await recoverPersistedLifecycle(sentinel)
        access = recovery.access
        this._lifecycleRecoveryBlocked = recovery.status !== 'confirmed'
      } else {
        this._lifecycleRecoveryBlocked = false
        access = adapterModule.validateAccess(await transport.loadAccess())
      }
      if (generation !== this._loadGeneration) return
      if (!access.can_read) {
        this._uploadIdentity = ''
        clearFileUploadMemory(this)
        this.setData({ form: null, formMode: '', processing: null })
        this.setData({ accessMessage: '当前主体没有正式需求读取权限，已失败关闭' })
        return
      }
      const response = contract.validateMaterialRequestPage(await transport.list(null))
      if (generation !== this._loadGeneration) return
      ensureFileUploadControllers(this)
      const nextUploadIdentity = accessUploadIdentity(access)
      if (this._uploadIdentity && this._uploadIdentity !== nextUploadIdentity) {
        clearFileUploadMemory(this)
        this.setData({ form: null, formMode: '', processing: null })
      }
      this._uploadIdentity = nextUploadIdentity
      this._access = access
      const supplyRecovered = this.data.supplyBlocked ? await this.recoverSupply() : null
      if (supplyRecovered && supplyRecovered.status === 'confirmed') access = supplyRecovered.access
      const runtimeContext = lifecycleRuntime.context
      const pendingContext = pendingLifecycleContext()
      const lifecycleBefore = pendingContext ? pendingContext.before : null
      const restoredLifecycle = pendingContext
        ? lifecycleConfirmationFromPending(lifecycleBefore, pendingContext.intent)
        : null
      const lifecycleIdentityMatches = Boolean(
        restoredLifecycle &&
        pendingContext.identity === nextUploadIdentity
      )
      const runtimeIdentityChanged = Boolean(
        runtimeContext && runtimeContext.identity !== nextUploadIdentity
      )
      const recoveredDetail = supplyRecovered && supplyRecovered.status === 'confirmed'
        ? presentDetail(supplyRecovered.detail, access, this._lifecycleRecoveryBlocked)
        : recovery && recovery.status === 'confirmed'
        ? presentDetail(recovery.detail, access, false)
        : null
      this._lifecycleBefore = lifecycleIdentityMatches ? lifecycleBefore : null
      this._lifecyclePendingIdentity = lifecycleIdentityMatches ? pendingContext.identity : ''
      this.setData({
        accessAllowed: true,
        canCreate: access.can_create,
        accessMessage: recovery && recovery.status === 'pending'
          ? '撤回或取消结果仍待服务端核验；其他正式读取保持可用'
          : runtimeIdentityChanged
          ? '未确认写入的身份或授权版本已变化，已隐藏原详情并停止自动重试'
          : runtimeContext && runtimeContext.status === 'rejected'
            ? '上次终止写入已被明确拒绝；请重新读取详情后再决定是否操作'
            : runtimeContext && runtimeContext.status === 'pending' && !restoredLifecycle
              ? '终止写入运行时锚点不完整，已失败关闭并禁止生成新坐标'
          : '仅展示当前主体正式授权范围内的脱敏需求',
        requests: response.items.map(presentSummary),
        detail: recoveredDetail || (
          lifecycleIdentityMatches
            ? presentDetail(lifecycleBefore, access, this._lifecycleRecoveryBlocked)
            : null
        ),
        lifecycleConfirm: lifecycleIdentityMatches ? restoredLifecycle : null,
        lifecycleRecoveryMessage: recovery && recovery.status === 'pending'
          ? recovery.message
          : '',
        notice: supplyRecovered && supplyRecovered.status === 'confirmed'
          ? '历史供给命令已核验；详情显示当前状态，不代表已发货或入库。'
          : recoveredDetail
          ? lifecycleResultNotice(recovery.command.action)
          : runtimeContext && runtimeContext.status === 'rejected' && !runtimeIdentityChanged
            ? runtimeContext.errorMessage
            : ''
      })
      if (
        runtimeContext && runtimeContext.status === 'confirmed' &&
        runtimeContext.identity === nextUploadIdentity
      ) {
        consumeLifecycleCompletion(this, runtimeContext, access)
      } else if (
        runtimeContext && runtimeContext.status === 'rejected' &&
        runtimeContext.identity === nextUploadIdentity &&
        lifecycleRuntime.context === runtimeContext
      ) {
        lifecycleRuntime.context = null
      }
    } catch (error) {
      if (generation !== this._loadGeneration) return
      this.setData({
        accessAllowed: false,
        canCreate: false,
        accessMessage: '无法确认身份、权限或正式需求响应契约，已失败关闭。',
        lifecycleRecoveryMessage: error.message || '需求终止恢复失败，恢复哨兵已保留。',
        requests: []
      })
      this._lifecycleRecoveryBlocked = true
      this._access = null
      this._uploadIdentity = ''
      clearFileUploadMemory(this)
      toast(error, '正式需求读取失败')
    } finally {
      if (generation === this._loadGeneration) this.setData({ loading: false })
    }
  },

  async openRequest(event) {
    const requestId = String(event.currentTarget.dataset.id || '')
    if (!this.data.requests.some((item) => item.request_id === requestId)) {
      toast(null, '需求标识已失效，请刷新')
      return
    }
    if (lifecycleRuntime.context) {
      toast(null, '已有终止写入等待收口，必须先恢复原详情或重新读取终态')
      return
    }
    ensureFileUploadControllers(this)
    this._externalEvidenceUploads.clear()
    this.setData({ busy: true, notice: '' })
    try {
      const detail = contract.validateMaterialRequestDetail(
        await transport.detail(requestId),
        requestId
      )
      if (!this._access) throw new Error('正式需求访问上下文已失效')
      this.setData({
        detail: presentDetail(detail, this._access, this._lifecycleRecoveryBlocked),
        processing: null,
        submitConfirm: false,
        lifecycleConfirm: null
      })
    } catch (error) {
      this.setData({ detail: null })
      toast(error, '需求详情读取失败')
    } finally {
      this.setData({ busy: false })
    }
  },

  closeDetail() {
    ensureRegistries(this)
    if (
      this.data.detail && (
        this._mutationRegistry.get(this.data.detail.request_id) ||
        (lifecycleRuntime.context &&
          lifecycleRuntime.context.before.request_id === this.data.detail.request_id)
      )
    ) {
      toast(null, '写入结果尚未确认，必须保留当前详情与请求坐标')
      return
    }
    this._editGeneration = (this._editGeneration || 0) + 1
    this._editBusyGeneration = 0
    if (this._externalEvidenceUploads) this._externalEvidenceUploads.clear()
    this.setData({
      busy: false,
      detail: null,
      submitConfirm: false,
      lifecycleConfirm: null
    })
  },

  startCreate() {
    if (this.supplyWriteBlocked()) return
    if (!this.data.canCreate) {
      toast(null, '当前主体没有正式需求创建权限')
      return
    }
    if (!this._access || !this._access.can_read_material_catalog) {
      toast(null, '当前主体没有正式物料目录权限，禁止回退旧目录')
      return
    }
    ensureRegistries(this)
    ensureFileUploadControllers(this)
    this._editGeneration = (this._editGeneration || 0) + 1
    this._editBusyGeneration = 0
    this._workOrderPickerGeneration = (this._workOrderPickerGeneration || 0) + 1
    this._pickerGeneration = (this._pickerGeneration || 0) + 1
    this._requestAttachmentUploads.bind(`${this._uploadIdentity}:request:create:new`)
    this.setData({
      detail: null,
      formMode: 'create',
      formRequestId: '',
      formRequestVersion: 0,
      form: emptyForm(this),
      workOrderPicker: null,
      materialPicker: null,
      writePending: false,
      pendingWriteMessage: '',
      notice: ''
    })
  },

  async resolveDraftWorkOrder(draft) {
    if (!draft.work_order_id) {
      return { workOrder: null, workOrderUnavailable: false }
    }
    if (!this._access) throw new Error('当前授权上下文不可用，已停止编辑')
    try {
      const response = materialRequestOptions.validateDetail(
        await transport.detailWorkOrder(draft.work_order_id),
        this._access,
        draft.work_order_id
      )
      return { workOrder: response.item, workOrderUnavailable: false }
    } catch (error) {
      if (error && error.status === 404 && error.responseReceived === true) {
        return { workOrder: null, workOrderUnavailable: true }
      }
      throw error
    }
  },

  async openWorkOrderPicker() {
    if (
      !this.data.form ||
      !this.data.formMode ||
      this.data.busy ||
      this.data.writePending ||
      !this._access ||
      (this.data.formMode === 'create' && !this._access.can_create)
    ) {
      toast(null, '正式工单选项不可用，禁止手填 UUID 或回退旧接口')
      return
    }
    this._workOrderPickerGeneration = (this._workOrderPickerGeneration || 0) + 1
    this._pickerGeneration = (this._pickerGeneration || 0) + 1
    this.setData({
      materialPicker: null,
      workOrderPicker: {
        query: '',
        items: [],
        nextAfterId: null,
        cursorHistory: [],
        loading: true,
        error: ''
      }
    })
    await this.readWorkOrderPickerPage('', null, false)
  },

  workOrderPickerQueryInput(event) {
    if (!this.data.workOrderPicker || this.data.workOrderPicker.loading) return
    this._workOrderPickerGeneration = (this._workOrderPickerGeneration || 0) + 1
    this.setData({
      workOrderPicker: Object.assign({}, this.data.workOrderPicker, {
        query: event.detail.value,
        items: [],
        nextAfterId: null,
        cursorHistory: [],
        error: ''
      })
    })
  },

  async searchWorkOrderPicker() {
    if (!this.data.workOrderPicker) return
    await this.readWorkOrderPickerPage(
      this.data.workOrderPicker.query,
      null,
      false
    )
  },

  async loadMoreWorkOrders() {
    const picker = this.data.workOrderPicker
    if (!picker || !picker.nextAfterId || picker.loading) return
    await this.readWorkOrderPickerPage(
      picker.query,
      picker.nextAfterId,
      true
    )
  },

  async readWorkOrderPickerPage(query, afterId, append) {
    const picker = this.data.workOrderPicker
    if (
      !picker ||
      !this._access ||
      (this.data.formMode === 'create' && !this._access.can_create)
    ) return
    const generation = (this._workOrderPickerGeneration || 0) + 1
    this._workOrderPickerGeneration = generation
    this.setData({
      workOrderPicker: Object.assign({}, picker, { loading: true, error: '' })
    })
    try {
      if (append && picker.cursorHistory.includes(afterId)) {
        throw new Error('正式工单选项游标重复，已失败关闭')
      }
      const response = materialRequestOptions.validatePage(
        await transport.listWorkOrders(query, afterId),
        this._access
      )
      if (
        generation !== this._workOrderPickerGeneration ||
        !this.data.workOrderPicker
      ) return
      const current = this.data.workOrderPicker
      const items = (append ? current.items : []).concat(response.items)
      if (
        new Set(items.map((item) => item.work_order_id)).size !== items.length ||
        new Set(items.map((item) => item.work_order_no)).size !== items.length
      ) throw new Error('正式工单选项跨页返回重复工单，已失败关闭')
      const cursorHistory = append
        ? current.cursorHistory.concat(afterId)
        : []
      if (
        response.next_after_id &&
        cursorHistory.includes(response.next_after_id)
      ) throw new Error('正式工单选项游标循环，已失败关闭')
      this.setData({
        workOrderPicker: Object.assign({}, current, {
          items,
          nextAfterId: response.next_after_id,
          cursorHistory,
          loading: false,
          error: ''
        })
      })
    } catch (error) {
      if (
        generation !== this._workOrderPickerGeneration ||
        !this.data.workOrderPicker
      ) return
      this.setData({
        workOrderPicker: Object.assign({}, this.data.workOrderPicker, {
          items: [],
          nextAfterId: null,
          cursorHistory: [],
          loading: false,
          error: error.message || '正式工单选项读取失败'
        })
      })
    }
  },

  chooseWorkOrder(event) {
    const picker = this.data.workOrderPicker
    if (
      !picker ||
      !this.data.form ||
      this.data.busy ||
      this.data.writePending ||
      picker.loading ||
      picker.error
    ) return
    const workOrderId = String(event.currentTarget.dataset.id || '').toLowerCase()
    const selected = picker.items.find((item) => item.work_order_id === workOrderId)
    if (!selected) {
      toast(null, '正式工单选择锚点已失效')
      return
    }
    this._workOrderPickerGeneration = (this._workOrderPickerGeneration || 0) + 1
    this.setData({
      form: Object.assign({}, this.data.form, {
        workOrder: selected,
        workOrderUnavailable: false
      }),
      workOrderPicker: null
    })
  },

  clearWorkOrder() {
    if (!this.data.form || this.data.busy || this.data.writePending) return
    this._workOrderPickerGeneration = (this._workOrderPickerGeneration || 0) + 1
    this.setData({
      form: Object.assign({}, this.data.form, {
        workOrder: null,
        workOrderUnavailable: false
      }),
      workOrderPicker: null
    })
  },

  closeWorkOrderPicker() {
    this._workOrderPickerGeneration = (this._workOrderPickerGeneration || 0) + 1
    this.setData({ workOrderPicker: null })
  },

  async resolveDraftMaterials(draft) {
    if (!this._access || !this._access.can_read_material_catalog) {
      throw new Error('当前授权不包含正式物料目录读取权限，已停止编辑')
    }
    const requiredIds = new Set(draft.lines.flatMap((line) => [
      line.material_id,
      ...(line.suggested_substitute_material_id ? [line.suggested_substitute_material_id] : [])
    ]))
    const found = new Map()
    const seenIds = new Set()
    const seenCursors = new Set()
    let afterId = null
    for (let pageNo = 0; pageNo < 100 && found.size < requiredIds.size; pageNo += 1) {
      const response = materialCatalog.validatePage(await transport.listMaterials('', afterId))
      for (const item of response.items) {
        if (seenIds.has(item.material_id)) throw new Error('正式物料目录跨页重复，已停止编辑')
        seenIds.add(item.material_id)
        if (requiredIds.has(item.material_id)) found.set(item.material_id, presentMaterial(item))
      }
      if (!response.next_after_id) break
      if (seenCursors.has(response.next_after_id)) throw new Error('正式物料目录游标循环，已停止编辑')
      seenCursors.add(response.next_after_id)
      afterId = response.next_after_id
    }
    if (found.size !== requiredIds.size) {
      throw new Error('草稿引用的物料不在当前正式活动目录中，已停止编辑')
    }
    return found
  },

  async openMaterialPicker(event) {
    const key = Number(event.currentTarget.dataset.key)
    const target = String(event.currentTarget.dataset.target || '')
    if (
      !this.data.form ||
      !this.data.form.lines.some((line) => line.key === key) ||
      !['material', 'substitute'].includes(target) ||
      !this._access ||
      !this._access.can_read_material_catalog
    ) {
      toast(null, '正式物料目录不可用，禁止手填 UUID 或回退旧目录')
      return
    }
    this._pickerGeneration = (this._pickerGeneration || 0) + 1
    this._workOrderPickerGeneration = (this._workOrderPickerGeneration || 0) + 1
    this.setData({
      workOrderPicker: null,
      materialPicker: {
        lineKey: key,
        target,
        query: '',
        items: [],
        nextAfterId: null,
        loading: true,
        error: ''
      }
    })
    await this.readMaterialPickerPage('', null, false)
  },

  materialPickerQueryInput(event) {
    if (!this.data.materialPicker || this.data.materialPicker.loading) return
    this.setData({
      materialPicker: Object.assign({}, this.data.materialPicker, {
        query: event.detail.value
      })
    })
  },

  async searchMaterialPicker() {
    if (!this.data.materialPicker) return
    await this.readMaterialPickerPage(this.data.materialPicker.query, null, false)
  },

  async loadMoreMaterials() {
    const picker = this.data.materialPicker
    if (!picker || !picker.nextAfterId) return
    await this.readMaterialPickerPage(picker.query, picker.nextAfterId, true)
  },

  async readMaterialPickerPage(query, afterId, append) {
    if (!this.data.materialPicker) return
    const generation = (this._pickerGeneration || 0) + 1
    this._pickerGeneration = generation
    this.setData({
      materialPicker: Object.assign({}, this.data.materialPicker, { loading: true, error: '' })
    })
    try {
      const response = materialCatalog.validatePage(await transport.listMaterials(query, afterId))
      if (generation !== this._pickerGeneration || !this.data.materialPicker) return
      const items = (append ? this.data.materialPicker.items : [])
        .concat(response.items.map(presentMaterial))
      if (
        new Set(items.map((item) => item.material_id)).size !== items.length ||
        new Set(items.map((item) => item.sku_code)).size !== items.length
      ) throw new Error('正式物料目录跨页返回重复物料，已失败关闭')
      this.setData({
        materialPicker: Object.assign({}, this.data.materialPicker, {
          items,
          nextAfterId: response.next_after_id,
          loading: false,
          error: ''
        })
      })
    } catch (error) {
      if (generation !== this._pickerGeneration || !this.data.materialPicker) return
      this.setData({
        materialPicker: Object.assign({}, this.data.materialPicker, {
          items: [], nextAfterId: null, loading: false,
          error: error.message || '正式物料目录读取失败'
        })
      })
    }
  },

  chooseMaterial(event) {
    const picker = this.data.materialPicker
    if (!picker || !this.data.form) return
    const materialId = String(event.currentTarget.dataset.id || '')
    const selected = picker.items.find((item) => item.material_id === materialId)
    if (!selected) {
      toast(null, '正式物料选择锚点已失效')
      return
    }
    const targetField = picker.target === 'material' ? 'material' : 'substituteMaterial'
    this.setData({
      form: Object.assign({}, this.data.form, {
        lines: this.data.form.lines.map((line) => line.key === picker.lineKey
          ? Object.assign({}, line, { [targetField]: selected })
          : line)
      }),
      materialPicker: null
    })
    this._pickerGeneration = (this._pickerGeneration || 0) + 1
  },

  clearSubstitute(event) {
    if (!this.data.form) return
    const key = Number(event.currentTarget.dataset.key)
    this.setData({
      form: Object.assign({}, this.data.form, {
        lines: this.data.form.lines.map((line) => line.key === key
          ? Object.assign({}, line, { substituteMaterial: null })
          : line)
      })
    })
  },

  closeMaterialPicker() {
    this._pickerGeneration = (this._pickerGeneration || 0) + 1
    this.setData({ materialPicker: null })
  },

  async startEdit() {
    if (this.supplyWriteBlocked()) return
    const detail = this.data.detail
    if (
      !detail ||
      !detail.canEdit ||
      !['draft', 'returned'].includes(detail.states.request_status)
    ) return
    const identity = this._access ? accessUploadIdentity(this._access) : ''
    if (!identity || identity !== this._uploadIdentity) {
      toast(null, '当前身份或授权上下文已变化，已停止读取明文草稿')
      return
    }
    const editGeneration = (this._editGeneration || 0) + 1
    this._editGeneration = editGeneration
    this._editBusyGeneration = editGeneration
    ensureRegistries(this)
    this.setData({ busy: true, notice: '' })
    try {
      const editable = await transport.loadDraftForEdit(detail.request_id)
      if (!editContextIsCurrent(this, editGeneration, identity, detail)) return
      const snapshot = adapterModule.validateEditableDraft(
        editable,
        detail.request_id,
        detail.request_version
      )
      const [materials, workOrder] = await Promise.all([
        this.resolveDraftMaterials(snapshot.draft),
        this.resolveDraftWorkOrder(snapshot.draft)
      ])
      if (!editContextIsCurrent(this, editGeneration, identity, detail)) return
      this._workOrderPickerGeneration = (this._workOrderPickerGeneration || 0) + 1
      this._pickerGeneration = (this._pickerGeneration || 0) + 1
      ensureFileUploadControllers(this)
      this._requestAttachmentUploads.bind(
        `${this._uploadIdentity}:request:edit:${snapshot.request_id}:${snapshot.request_version}`
      )
      this.setData({
        detail: null,
        formMode: 'edit',
        formRequestId: snapshot.request_id,
        formRequestVersion: snapshot.request_version,
        form: draftToForm(this, snapshot.draft, materials, workOrder),
        workOrderPicker: null,
        materialPicker: null,
        writePending: false,
        pendingWriteMessage: ''
      })
    } catch (error) {
      if (editContextIsCurrent(this, editGeneration, identity, detail)) {
        toast(error, '可编辑草稿读取失败')
      }
    } finally {
      if (!this._unloaded && this._editBusyGeneration === editGeneration) {
        this._editBusyGeneration = 0
        this.setData({ busy: false })
      }
    }
  },

  cancelForm() {
    ensureRegistries(this)
    const createPending = this._createRegistry.get()
    const updatePending = this.data.formMode === 'edit'
      ? this._mutationRegistry.get(this.data.formRequestId)
      : null
    if (createPending || updatePending) {
      toast(null, '写入结果尚未确认，必须保留原内容与坐标')
      return
    }
    if (this._requestAttachmentUploads) this._requestAttachmentUploads.clear()
    this._workOrderPickerGeneration = (this._workOrderPickerGeneration || 0) + 1
    this._pickerGeneration = (this._pickerGeneration || 0) + 1
    this.setData({
      formMode: '',
      formRequestId: '',
      formRequestVersion: 0,
      form: null,
      workOrderPicker: null,
      materialPicker: null,
      writePending: false,
      pendingWriteMessage: ''
    })
  },

  formInput(event) {
    const field = String(event.currentTarget.dataset.field || '')
    if (!this.data.form || !Object.prototype.hasOwnProperty.call(this.data.form, field)) return
    this.setData({ form: Object.assign({}, this.data.form, { [field]: event.detail.value }) })
  },

  lineInput(event) {
    const key = Number(event.currentTarget.dataset.key)
    const field = String(event.currentTarget.dataset.field || '')
    const allowed = ['requestedQty', 'requiredDate', 'note']
    if (!this.data.form || !allowed.includes(field)) return
    this.setData({
      form: Object.assign({}, this.data.form, {
        lines: this.data.form.lines.map((line) => (
          line.key === key ? Object.assign({}, line, { [field]: event.detail.value }) : line
        ))
      })
    })
  },

  addLine() {
    if (!this.data.form || this.data.busy) return
    this.setData({
      form: Object.assign({}, this.data.form, {
        lines: this.data.form.lines.concat(emptyLine(this))
      })
    })
  },

  removeLine(event) {
    if (!this.data.form || this.data.form.lines.length <= 1 || this.data.busy) return
    const key = Number(event.currentTarget.dataset.key)
    this.setData({
      form: Object.assign({}, this.data.form, {
        lines: this.data.form.lines.filter((line) => line.key !== key)
      })
    })
  },

  async chooseRequestAttachment() {
    if (!this.data.form || this.data.busy || this.data.writePending) return
    ensureFileUploadControllers(this)
    try {
      await this._requestAttachmentUploads.select()
    } catch (error) {
      toast(error, '需求附件上传失败')
    }
  },

  async retryRequestAttachment(event) {
    if (!this.data.form || this.data.busy || this.data.writePending) return
    ensureFileUploadControllers(this)
    try {
      await this._requestAttachmentUploads.retry(String(event.currentTarget.dataset.key || ''))
    } catch (error) {
      toast(error, '需求附件重试失败')
    }
  },

  removeRequestAttachment(event) {
    if (!this.data.form || this.data.busy || this.data.writePending) return
    ensureFileUploadControllers(this)
    try {
      this._requestAttachmentUploads.remove(String(event.currentTarget.dataset.key || ''))
    } catch (error) {
      toast(error, '需求附件不能移除')
    }
  },

  async saveDraft() {
    if (this.supplyWriteBlocked()) return
    if (!this.data.form || !this.data.formMode) return
    ensureRegistries(this)
    this.setData({ busy: true, notice: '' })
    let responseValidated = false
    try {
      ensureFileUploadControllers(this)
      const uploaded = availableFiles(this._requestAttachmentUploads, 'request_attachment')
      const draft = formToDraft(this.data.form, uploaded)
      if (this.data.formMode === 'create') {
        const intent = this._createRegistry.begin({ body: draft })
        this.setData({
          writePending: true,
          pendingWriteMessage: `草稿创建结果确认中（${intent.client_draft_key}）；重试只复用原请求坐标。`
        })
        const result = contract.validateMaterialRequestCreateResult(
          await transport.createDraft(intent)
        )
        responseValidated = true
        const reread = contract.validateMaterialRequestDetail(
          await transport.detail(result.request_id),
          result.request_id
        )
        if (!draftWriteMatches(result, reread, draft)) {
          throw new Error('创建响应、修订或工单绑定与详情回读不一致，仍待人工核验')
        }
        this._createRegistry.confirm(intent.client_draft_key, intent.signature)
        this._requestAttachmentUploads.clear()
        this.setData({
          formMode: '',
          form: null,
          detail: presentDetail(reread, this._access, this._lifecycleRecoveryBlocked),
          writePending: false,
          pendingWriteMessage: '',
          notice: '草稿已创建并完成精确详情回读'
        })
        wx.showToast({ title: '草稿已保存', icon: 'success' })
      } else {
        const requestId = this.data.formRequestId
        const previousVersion = this.data.formRequestVersion
        const intent = this._mutationRegistry.begin({
          requestId,
          action: 'update',
          path: `/v1/material-requests/${requestId}`,
          body: Object.assign({}, draft, { expected_version: previousVersion }),
          expectedVersion: previousVersion
        })
        this.setData({
          writePending: true,
          pendingWriteMessage: `草稿修改结果确认中（${intent.headers['X-Request-ID']}）；重试只复用原请求坐标。`
        })
        const result = contract.validateMaterialRequestMutationResult(
          await transport.mutate(intent),
          { requestId, action: 'update', previousVersion }
        )
        responseValidated = true
        const reread = contract.validateMaterialRequestDetail(
          await transport.detail(requestId),
          requestId
        )
        if (!draftWriteMatches(result, reread, draft)) {
          throw new Error('修改响应、修订或工单绑定与详情回读不一致，仍待人工核验')
        }
        this._mutationRegistry.confirm(intent.request_id, intent.signature)
        this._requestAttachmentUploads.clear()
        this.setData({
          formMode: '',
          form: null,
          detail: presentDetail(reread, this._access, this._lifecycleRecoveryBlocked),
          writePending: false,
          pendingWriteMessage: '',
          notice: '草稿修改已完成并精确回读'
        })
        wx.showToast({ title: '草稿已更新', icon: 'success' })
      }
    } catch (error) {
      if (this.data.formMode === 'create') {
        const pending = this._createRegistry.get()
        if (!responseValidated && pending && adapterModule.isDefinitiveRejection(error)) {
          this._createRegistry.clearDefinitiveRejection(
            pending.client_draft_key,
            pending.signature
          )
          this.setData({ writePending: false, pendingWriteMessage: '' })
        } else if (pending) {
          this.setData({
            writePending: true,
            pendingWriteMessage: `创建结果仍未确认（${pending.client_draft_key}）；禁止修改内容后生成新坐标。`
          })
        }
      } else {
        const pending = this._mutationRegistry.get(this.data.formRequestId)
        if (!responseValidated && pending && adapterModule.isDefinitiveRejection(error)) {
          this._mutationRegistry.clearDefinitiveRejection(
            pending.request_id,
            pending.signature
          )
          this.setData({ writePending: false, pendingWriteMessage: '' })
        } else if (pending) {
          this.setData({
            writePending: true,
            pendingWriteMessage: `修改结果仍未确认（${pending.headers['X-Request-ID']}）；禁止同对象其他写入。`
          })
        }
      }
      toast(error, '草稿保存失败')
    } finally {
      this.setData({ busy: false })
    }
  },

  openSubmitConfirm() {
    if (this.supplyWriteBlocked()) return
    const detail = this.data.detail
    if (
      !detail ||
      !detail.canSubmit ||
      !['draft', 'returned'].includes(detail.states.request_status)
    ) return
    this.setData({ submitConfirm: true })
  },

  cancelSubmit() {
    this.setData({ submitConfirm: false })
  },

  async confirmSubmit() {
    if (this.supplyWriteBlocked()) return
    const before = this.data.detail
    if (
      !before ||
      !before.canSubmit ||
      !['draft', 'returned'].includes(before.states.request_status)
    ) return
    ensureRegistries(this)
    this.setData({ submitConfirm: false, busy: true, notice: '' })
    let responseValidated = false
    try {
      const intent = this._mutationRegistry.begin({
        requestId: before.request_id,
        action: 'submit',
        path: `/v1/material-requests/${before.request_id}/submit`,
        body: { expected_version: before.request_version },
        expectedVersion: before.request_version
      })
      const result = contract.validateMaterialRequestMutationResult(
        await transport.mutate(intent),
        {
          requestId: before.request_id,
          action: 'submit',
          previousVersion: before.request_version
        }
      )
      responseValidated = true
      const reread = contract.validateMaterialRequestDetail(
        await transport.detail(before.request_id),
        before.request_id
      )
      if (!axesMatch(result, reread)) throw new Error('提交响应与详情回读不一致，仍待人工核验')
      this._mutationRegistry.confirm(intent.request_id, intent.signature)
      this.setData({
        detail: presentDetail(reread, this._access, this._lifecycleRecoveryBlocked),
        notice: '需求已提交并精确回读；后续状态仍分别展示'
      })
      wx.showToast({ title: '需求已提交', icon: 'success' })
    } catch (error) {
      const pending = this._mutationRegistry.get(before.request_id)
      if (!responseValidated && pending && adapterModule.isDefinitiveRejection(error)) {
        this._mutationRegistry.clearDefinitiveRejection(
          pending.request_id,
          pending.signature
        )
      }
      toast(error, '需求提交失败')
    } finally {
      this.setData({ busy: false })
    }
  },

  openLifecycleConfirm(event) {
    if (this.supplyWriteBlocked()) return
    const action = String(event.currentTarget.dataset.action || '')
    const detail = this.data.detail
    const access = this._access
    if (!detail || !access || !['withdraw', 'cancel'].includes(action)) return
    ensureRegistries(this)
    const context = lifecycleRuntime.context
    if (context) {
      if (
        context.status === 'confirmed' &&
        lifecycleContextMatches(
          context,
          detail,
          { action, requestId: detail.request_id, expectedVersion: detail.request_version },
          accessUploadIdentity(access)
        ) &&
        consumeLifecycleCompletion(this, context, access)
      ) return
      const pending = pendingLifecycleContext()
      const restored = pending
        ? lifecycleConfirmationFromPending(pending.before, pending.intent)
        : null
      if (
        restored &&
        pending.before.request_id === detail.request_id &&
        pending.identity === accessUploadIdentity(access)
      ) {
        this._lifecycleBefore = pending.before
        this._lifecyclePendingIdentity = pending.identity
        this.setData({ detail: pending.before, lifecycleConfirm: restored })
      } else {
        toast(null, '当前需求已有结果未确认的写入，无法创建新坐标，请人工核验')
      }
      return
    }
    if (this._lifecycleRecoveryBlocked) {
      toast(null, '已有需求终止操作待服务端核验，禁止生成新的撤回或取消坐标')
      return
    }
    const permissionAllowed = action === 'withdraw'
      ? access.can_withdraw
      : access.can_cancel
    if (!permissionAllowed || !detail.allowed_actions.includes(action)) {
      toast(null, '当前权限或详情允许动作不满足，已停止操作')
      return
    }
    const lines = action === 'cancel' ? cancellationInputLines(detail) : []
    this.setData({
      processing: null,
      submitConfirm: false,
      lifecycleConfirm: {
        action,
        title: action === 'withdraw' ? '二次确认撤回需求' : '二次确认安全取消',
        requestId: detail.request_id,
        expectedVersion: detail.request_version,
        reason: '',
        lines,
        pendingMessage: '',
        error: ''
      }
    })
  },

  lifecycleReasonInput(event) {
    const confirmation = this.data.lifecycleConfirm
    if (!confirmation || confirmation.pendingMessage) return
    this.setData({
      lifecycleConfirm: Object.assign({}, confirmation, {
        reason: String(event.detail.value || ''),
        error: ''
      })
    })
  },

  lifecycleLineReasonInput(event) {
    const confirmation = this.data.lifecycleConfirm
    if (!confirmation || confirmation.action !== 'cancel' || confirmation.pendingMessage) return
    const requestLineId = String(event.currentTarget.dataset.id || '')
    this.setData({
      lifecycleConfirm: Object.assign({}, confirmation, {
        lines: confirmation.lines.map((line) => line.requestLineId === requestLineId
          ? Object.assign({}, line, { reason: String(event.detail.value || '') })
          : line),
        error: ''
      })
    })
  },

  cancelLifecycleConfirm() {
    const confirmation = this.data.lifecycleConfirm
    if (!confirmation) return
    const pending = pendingLifecycleContext()
    if (
      confirmation.pendingMessage ||
      (pending && pending.before.request_id === confirmation.requestId)
    ) {
      toast(null, '写入结果尚未确认，必须保留原理由和请求坐标')
      return
    }
    this.setData({ lifecycleConfirm: null })
  },

  async confirmLifecycleAction() {
    if (this.supplyWriteBlocked()) return
    if (this.data.busy) return
    const before = this.data.detail
    const confirmation = this.data.lifecycleConfirm
    const access = this._access
    if (!before || !confirmation || !access) return
    const action = confirmation.action
    const permissionAllowed = action === 'withdraw'
      ? access.can_withdraw
      : action === 'cancel' && access.can_cancel
    if (
      !permissionAllowed ||
      !before.allowed_actions.includes(action) ||
      confirmation.requestId !== before.request_id ||
      confirmation.expectedVersion !== before.request_version
    ) {
      this.setData({
        lifecycleConfirm: Object.assign({}, confirmation, {
          error: '当前权限、允许动作或需求版本已变化，请重新读取详情'
        })
      })
      return
    }
    let body
    try {
      const reason = confirmation.reason.trim()
      if (!reason || reason.length > 4000 || /[\u0000-\u001f\u007f]/.test(reason)) {
        throw new Error(action === 'withdraw' ? '请填写有效撤回理由' : '请填写有效取消理由')
      }
      if (action === 'withdraw') {
        body = { expected_version: before.request_version, reason }
      } else {
        const exactLines = cancellationInputLines(before)
        if (
          exactLines.length !== confirmation.lines.length ||
          exactLines.some((line, index) => (
            line.requestLineId !== confirmation.lines[index].requestLineId ||
            line.cancelledQty !== confirmation.lines[index].cancelledQty
          ))
        ) throw new Error('取消明细与当前最终批准数量不一致，请重新读取详情')
        const lines = confirmation.lines.map((line) => {
          const lineReason = line.reason.trim()
          if (!lineReason || lineReason.length > 4000 || /[\u0000-\u001f\u007f]/.test(lineReason)) {
            throw new Error(`第 ${line.lineNo} 行必须填写有效取消原因`)
          }
          return {
            request_line_id: line.requestLineId,
            cancelled_qty: line.cancelledQty,
            reason: lineReason
          }
        })
        body = { expected_version: before.request_version, reason, lines }
      }
    } catch (error) {
      this.setData({
        lifecycleConfirm: Object.assign({}, confirmation, { error: error.message })
      })
      return
    }

    const identity = accessUploadIdentity(access)
    const existingContext = lifecycleRuntime.context
    if (existingContext && existingContext.status === 'confirmed') {
      if (
        lifecycleContextMatches(existingContext, before, confirmation, identity) &&
        consumeLifecycleCompletion(this, existingContext, access)
      ) {
        wx.showToast({
          title: action === 'withdraw' ? '需求已撤回' : '需求已取消',
          icon: 'success'
        })
      } else {
        this.setData({
          lifecycleConfirm: Object.assign({}, confirmation, {
            error: '已确认终态与当前身份、动作或版本锚点不一致，必须重新读取'
          })
        })
      }
      return
    }
    if (
      existingContext && (
        existingContext.status !== 'pending' ||
        !lifecycleContextMatches(existingContext, before, confirmation, identity)
      )
    ) {
      this.setData({
        lifecycleConfirm: Object.assign({}, confirmation, {
          error: '已有终止写入尚未安全收口，禁止生成新坐标'
        })
      })
      return
    }

    try {
      const storedSentinel = lifecycleRecovery.readLifecycleSentinel()
      if (
        storedSentinel && (
          !existingContext ||
          storedSentinel.x_request_id !== existingContext.intent.headers['X-Request-ID']
        )
      ) {
        this._lifecycleRecoveryBlocked = true
        this.setData({
          lifecycleRecoveryMessage: `已有需求终止操作待核验（${storedSentinel.x_request_id}），禁止生成新坐标。`,
          lifecycleConfirm: Object.assign({}, confirmation, {
            error: '本地恢复哨兵与当前运行时意图不一致，已失败关闭'
          })
        })
        return
      }
    } catch (error) {
      this._lifecycleRecoveryBlocked = true
      this.setData({
        lifecycleRecoveryMessage: error.message,
        lifecycleConfirm: Object.assign({}, confirmation, { error: error.message })
      })
      return
    }

    const registry = lifecycleRuntime.registry
    const lifecycleGeneration = this._lifecycleGeneration || 0
    let responseValidated = false
    let result = null
    let reread = null
    let intent
    try {
      intent = registry.begin({
        requestId: before.request_id,
        action,
        path: `/v1/material-requests/${before.request_id}/${action}`,
        body,
        expectedVersion: before.request_version
      })
      if (
        existingContext && (
          lifecycleRuntime.context !== existingContext ||
          existingContext.intent.signature !== intent.signature
        )
      ) throw new Error('未确认终止写入的请求坐标发生漂移，已停止处理')
      if (!existingContext) {
        lifecycleRuntime.context = Object.freeze({
          status: 'pending',
          identity,
          before,
          intent,
          terminalDetail: null,
          errorMessage: ''
        })
      }
      lifecycleRecovery.persistLifecycleSentinel(intent.headers['X-Request-ID'])
      this._lifecycleRecoveryBlocked = true
      this._lifecycleBefore = before
      this._lifecyclePendingIdentity = identity
      this.setData({
        busy: true,
        notice: '',
        lifecycleRecoveryMessage: `已持久化最小恢复哨兵（${intent.headers['X-Request-ID']}）；结果确认前禁止新的撤回或取消。`,
        lifecycleConfirm: Object.assign({}, confirmation, {
          reason: body.reason,
          lines: confirmation.lines.map((line, index) => Object.assign({}, line, {
            reason: body.lines ? body.lines[index].reason : line.reason
          })),
          error: '',
          pendingMessage: `结果确认中（${intent.headers['X-Request-ID']}）；重试只复用原坐标。`
        })
      })
      result = contract.validateMaterialRequestMutationResult(
        await transport.mutate(intent),
        { requestId: before.request_id, action, previousVersion: before.request_version }
      )
      responseValidated = true
      reread = contract.validateMaterialRequestDetail(
        await transport.detail(before.request_id),
        before.request_id
      )
      if (!lifecycleMutationMatches(result, before, reread, action, body.lines || [])) {
        throw new Error('终止响应与版本、审批锚点、明细或独立状态轴回读不一致，仍待人工核验')
      }
      const xRequestId = intent.headers['X-Request-ID']
      const pending = registry.get(intent.request_id)
      const pendingContext = lifecycleRuntime.context
      const completedByPeer = lifecycleCompletionMatches(xRequestId, result, reread)
      if (pending || pendingContext) {
        if (
          !pending || pending.signature !== intent.signature ||
          pending.headers['X-Request-ID'] !== xRequestId ||
          !pendingContext || pendingContext.status !== 'pending' ||
          !lifecycleContextMatches(
            pendingContext,
            before,
            confirmation,
            identity
          ) ||
          pendingContext.intent.signature !== intent.signature ||
          pendingContext.intent.headers['X-Request-ID'] !== xRequestId
        ) throw new Error('终止写入运行时锚点已变化，必须人工核验')
      } else if (!completedByPeer) {
        throw new Error('终止写入确认路径缺失或坐标不一致，必须人工核验')
      }
      clearConfirmedLifecycleSentinel(xRequestId, result, reread)
      this._lifecycleRecoveryBlocked = false
      let confirmedContext = null
      if (pending && pendingContext) {
        registry.confirm(intent.request_id, intent.signature)
        confirmedContext = Object.freeze({
          status: 'confirmed',
          identity: pendingContext.identity,
          before: pendingContext.before,
          intent: pendingContext.intent,
          terminalDetail: reread,
          errorMessage: ''
        })
        lifecycleRuntime.context = confirmedContext
      }
      if (lifecyclePageIsActive(this, lifecycleGeneration)) {
        if (confirmedContext) {
          consumeLifecycleCompletion(this, confirmedContext, access)
        } else {
          this._lifecycleBefore = null
          this._lifecyclePendingIdentity = ''
          this.setData({
            detail: presentDetail(reread, access, false),
            lifecycleConfirm: null,
            lifecycleRecoveryMessage: '',
            notice: lifecycleResultNotice(action)
          })
        }
        wx.showToast({
          title: action === 'withdraw' ? '需求已撤回' : '需求已取消',
          icon: 'success'
        })
      }
    } catch (error) {
      const pending = registry.get(before.request_id)
      const confirmedByPeer = lifecycleRuntime.context
      if (
        !pending && intent && confirmedByPeer && confirmedByPeer.status === 'confirmed' &&
        confirmedByPeer.intent.signature === intent.signature &&
        confirmedByPeer.intent.headers['X-Request-ID'] === intent.headers['X-Request-ID'] &&
        lifecycleContextMatches(confirmedByPeer, before, confirmation, identity)
      ) {
        if (lifecyclePageIsActive(this, lifecycleGeneration)) {
          consumeLifecycleCompletion(this, confirmedByPeer, access)
          wx.showToast({
            title: action === 'withdraw' ? '需求已撤回' : '需求已取消',
            icon: 'success'
          })
        }
      } else if (!responseValidated && pending && adapterModule.isDefinitiveRejection(error)) {
        let cleanupError = null
        try {
          lifecycleRecovery.clearLifecycleSentinel(pending.headers['X-Request-ID'])
          this._lifecycleRecoveryBlocked = false
        } catch (sentinelError) {
          cleanupError = sentinelError
          this._lifecycleRecoveryBlocked = true
        }
        if (cleanupError) {
          if (lifecyclePageIsActive(this, lifecycleGeneration)) {
            this.setData({
              lifecycleRecoveryMessage: cleanupError.message,
              lifecycleConfirm: Object.assign({}, this.data.lifecycleConfirm || confirmation, {
                error: cleanupError.message,
                pendingMessage: `服务端已明确拒绝，但本地恢复哨兵尚未安全清理（${pending.headers['X-Request-ID']}）。`
              })
            })
          }
          return
        }
        registry.clearDefinitiveRejection(pending.request_id, pending.signature)
        const runtimeContext = lifecycleRuntime.context
        if (
          runtimeContext && runtimeContext.status === 'pending' &&
          runtimeContext.intent.signature === pending.signature
        ) {
          lifecycleRuntime.context = Object.freeze({
            status: 'rejected',
            identity: runtimeContext.identity,
            before: runtimeContext.before,
            intent: runtimeContext.intent,
            terminalDetail: null,
            errorMessage: error.message || '需求终止操作被明确拒绝'
          })
        }
        if (lifecyclePageIsActive(this, lifecycleGeneration)) {
          this._lifecycleBefore = null
          this._lifecyclePendingIdentity = ''
          this.setData({
            lifecycleRecoveryMessage: '',
            lifecycleConfirm: Object.assign({}, this.data.lifecycleConfirm || confirmation, {
              error: error.message || '需求终止操作被明确拒绝',
              pendingMessage: ''
            })
          })
          if (
            lifecycleRuntime.context && lifecycleRuntime.context.status === 'rejected' &&
            lifecycleRuntime.context.intent.signature === pending.signature
          ) lifecycleRuntime.context = null
        }
      } else if (pending) {
        if (lifecyclePageIsActive(this, lifecycleGeneration)) {
          this._lifecycleRecoveryBlocked = true
          this.setData({
            lifecycleRecoveryMessage: error && String(error.code || '').startsWith(
              'material_request_lifecycle_recovery_'
            )
              ? error.message
              : `终止结果仍待核验（${pending.headers['X-Request-ID']}）；恢复哨兵保持不变。`,
            lifecycleConfirm: Object.assign({}, this.data.lifecycleConfirm || confirmation, {
              error: error.message || '需求终止结果未确认',
              pendingMessage: `结果仍未确认（${pending.headers['X-Request-ID']}）；禁止生成新坐标或执行其他动作。`
            })
          })
        }
      } else if (lifecyclePageIsActive(this, lifecycleGeneration)) {
        this._lifecycleRecoveryBlocked = true
        this.setData({
          lifecycleRecoveryMessage: intent
            ? `终止写入坐标仍待人工核验（${intent.headers['X-Request-ID']}）；禁止生成新坐标。`
            : '终止写入坐标无法确认，已失败关闭。',
          lifecycleConfirm: Object.assign({}, this.data.lifecycleConfirm || confirmation, {
            error: error.message || '需求终止失败',
            pendingMessage: intent ? '结果仍未确认；禁止生成新坐标或执行其他动作。' : ''
          })
        })
      }
    } finally {
      if (lifecyclePageIsActive(this, lifecycleGeneration)) this.setData({ busy: false })
    }
  },

  openApprovalProcess(event) {
    if (this.supplyWriteBlocked()) return
    const kind = String(event.currentTarget.dataset.kind || '')
    const detail = this.data.detail
    if (!detail || !this._access || ![
      'internal', 'external_registration', 'external_verification'
    ].includes(kind)) return
    if (
      (kind === 'internal' && !detail.canProcessInternal) ||
      (kind === 'external_registration' && !detail.canRegisterExternal) ||
      (kind === 'external_verification' && !detail.canVerifyExternal)
    ) {
      toast(null, '当前访问权限与详情允许动作不一致，已停止处理')
      return
    }
    const step = currentApprovalStep(detail)
    if (!step) {
      toast(null, '当前审批步骤锚点缺失，已停止处理')
      return
    }
    let lines = []
    try {
      if (kind !== 'external_verification') lines = approvalInputLines(detail)
    } catch (error) {
      toast(error, '审批逐行数量读取失败')
      return
    }
    const evidence = ((detail.approval_instance &&
      detail.approval_instance.external_evidence_summaries) || [])
      .filter((item) => item.step_id === step.step_id && item.status === 'pending_verification')
    const action = kind === 'internal'
      ? ['approve', 'return', 'reject'].find((item) => detail.allowed_actions.includes(item)) || 'reject'
      : 'approve'
    ensureFileUploadControllers(this)
    if (kind === 'external_registration') {
      this._externalEvidenceUploads.bind(
        `${this._uploadIdentity}:external:${detail.request_id}:${detail.request_version}:${step.step_id}:${step.version}`
      )
    } else {
      this._externalEvidenceUploads.clear()
    }
    this.setData({
      processing: {
        kind,
        stepId: step.step_id,
        stepVersion: step.version,
        action,
        canApprove: kind !== 'internal' || detail.allowed_actions.includes('approve'),
        canReturn: kind !== 'internal' || detail.allowed_actions.includes('return'),
        canReject: kind !== 'internal' || detail.allowed_actions.includes('reject'),
        lines,
        comment: '',
        externalApproverName: '',
        externalReferenceNo: '',
        externalDecidedAt: '',
        registrationId: evidence.length === 1 ? evidence[0].registration_id : '',
        evidence: evidence.length === 1 ? evidence[0] : null,
        verificationDecision: 'accept',
        error: '',
        pendingMessage: ''
      }
    })
  },

  chooseProcessAction(event) {
    const action = String(event.currentTarget.dataset.action || '')
    if (
      !this.data.processing ||
      this.data.processing.pendingMessage ||
      !['approve', 'return', 'reject'].includes(action)
    ) return
    if (
      this.data.processing.kind === 'internal' &&
      !this.data.detail.allowed_actions.includes(action)
    ) return
    this.setData({
      processing: Object.assign({}, this.data.processing, { action, error: '' })
    })
  },

  processFieldInput(event) {
    if (!this.data.processing || this.data.processing.pendingMessage) return
    const field = String(event.currentTarget.dataset.field || '')
    const allowed = [
      'comment', 'externalApproverName',
      'externalReferenceNo', 'externalDecidedAt'
    ]
    if (!allowed.includes(field)) return
    this.setData({
      processing: Object.assign({}, this.data.processing, {
        [field]: event.detail.value,
        error: ''
      })
    })
  },

  async chooseExternalEvidence() {
    if (
      !this.data.processing ||
      this.data.processing.kind !== 'external_registration' ||
      this.data.processing.pendingMessage ||
      this.data.busy
    ) return
    ensureFileUploadControllers(this)
    try {
      await this._externalEvidenceUploads.select()
    } catch (error) {
      toast(error, '外部审批证据上传失败')
    }
  },

  async retryExternalEvidence(event) {
    if (!this.data.processing || this.data.processing.pendingMessage || this.data.busy) return
    ensureFileUploadControllers(this)
    try {
      await this._externalEvidenceUploads.retry(String(event.currentTarget.dataset.key || ''))
    } catch (error) {
      toast(error, '外部审批证据重试失败')
    }
  },

  removeExternalEvidence(event) {
    if (!this.data.processing || this.data.processing.pendingMessage || this.data.busy) return
    ensureFileUploadControllers(this)
    try {
      this._externalEvidenceUploads.remove(String(event.currentTarget.dataset.key || ''))
    } catch (error) {
      toast(error, '外部审批证据不能移除')
    }
  },

  processLineInput(event) {
    if (!this.data.processing || this.data.processing.pendingMessage) return
    const requestLineId = String(event.currentTarget.dataset.id || '')
    const field = String(event.currentTarget.dataset.field || '')
    if (!['decisionQty', 'reason'].includes(field)) return
    this.setData({
      processing: Object.assign({}, this.data.processing, {
        error: '',
        lines: this.data.processing.lines.map((line) => line.requestLineId === requestLineId
          ? Object.assign({}, line, { [field]: event.detail.value })
          : line)
      })
    })
  },

  chooseVerificationDecision(event) {
    const decision = String(event.currentTarget.dataset.decision || '')
    if (
      !this.data.processing ||
      this.data.processing.pendingMessage ||
      !['accept', 'reject'].includes(decision)
    ) return
    this.setData({
      processing: Object.assign({}, this.data.processing, {
        verificationDecision: decision,
        error: ''
      })
    })
  },

  cancelApprovalProcess() {
    const detail = this.data.detail
    ensureRegistries(this)
    if (detail && this._mutationRegistry.get(detail.request_id)) {
      this.setData({
        processing: Object.assign({}, this.data.processing, {
          error: '处理结果尚未确认，必须保留原内容与请求坐标'
        })
      })
      return
    }
    if (this._externalEvidenceUploads) this._externalEvidenceUploads.clear()
    this.setData({ processing: null })
  },

  supplyWriteBlocked() {
    try {
      const pending = supplyRecovery.read()
      if (!pending) return Boolean(this.data.supplyBlocked)
      this.setData({ supplyBlocked: true,
        supplyRecoveryMessage: `供给操作结果待核验（${pending.trace_request_id}）；禁止重新提交或生成新坐标。` })
    } catch (error) {
      this.setData({ supplyBlocked: true, supplyRecoveryMessage: error.message })
    }
    return true
  },

  async recoverSupply() {
    if (this._supplyRecovering) return
    this._supplyRecovering = true
    const generation = this._loadGeneration
    const pageAccess = this._access
    try {
      const pending = supplyRecovery.read()
      if (!pending) return
      const result = await supplyRecovery.recover(pending, transport)
      if (this._unloaded || this._loadGeneration !== generation) return
      if (pageAccess && (pageAccess.person_id !== result.access.person_id
        || pageAccess.authorization_version !== result.access.authorization_version)) {
        throw new Error('页面身份与供给恢复身份不同；保留原标记，请重新进入页面核验')
      }
      if (result.status !== 'confirmed') {
        this.setData({ supplyBlocked: true,
          supplyRecoveryMessage: `服务端尚未确认供给命令（${pending.trace_request_id}）；保留标记，暂不重试写入。` })
        return
      }
      supplyRecovery.clear(pending)
      const intent = this._mutationRegistry && this._mutationRegistry.get(pending.request_id)
      if (intent && intent.headers['X-Request-ID'] === pending.trace_request_id) {
        this._mutationRegistry.confirm(intent.request_id, intent.signature)
      }
      this._access = result.access
      this.setData({ supplyBlocked: false, supplyForm: null, supplyRecoveryMessage: '',
        detail: presentDetail(result.detail, result.access, this._lifecycleRecoveryBlocked),
        notice: '已核验历史供给命令；详情显示当前任务状态，不代表已发货或入库。' })
      return result
    } catch (error) {
      if (!this._unloaded && this._loadGeneration === generation) {
        this.setData({ supplyBlocked: true, supplyRecoveryMessage: error.message || '供给结果仍未确认' })
      }
    } finally { this._supplyRecovering = false }
  },

  openSupplyForm(event) {
    if (this.data.busy || this.supplyWriteBlocked() || this._lifecycleRecoveryBlocked) return
    const detail = this.data.detail
    if (!detail || !this._access || !this._access.can_manage_supply) return
    ensureRegistries(this)
    if (this._mutationRegistry.get(detail.request_id) || this._createRegistry.size()
      || this.data.processing || this.data.lifecycleConfirm || this.data.formMode) {
      toast(null, '请先处理当前未完成操作')
      return
    }
    const taskId = String(event.currentTarget.dataset.task || '')
    const task = taskId ? detail.supply_tasks.find((row) => row.id === taskId) : null
    if (taskId && (!task || !task.allowed_actions.length)) return
    if (!taskId && !detail.allowed_actions.includes('create_supply_task')) return
    const eligible = detail.lines.filter((line) => supplyRemaining(detail, line) > BigInt(0))
    if (!task && !eligible.length) return
    this.setData({ supplyForm: {
      taskId: task ? task.id : null, taskVersion: task ? task.version : null,
      requestId: detail.request_id, requestVersion: detail.request_version,
      lineId: task ? task.request_line_id : eligible[0].request_line_id,
      supplyType: task ? task.supply_type : 'star_replenishment',
      quantity: task ? task.expected_qty : '', reference: task ? task.reference_no || '' : '',
      expectedDate: task ? task.expected_date || '' : '',
      status: task ? task.allowed_actions.includes('update_supply_task') ? task.status : 'cancelled' : 'open', comment: '', error: '',
      cancelOnly: !!task && !task.allowed_actions.includes('update_supply_task'),
      canChooseOpen: !task || task.status === 'open',
      canChooseReference: !task || ['open', 'reference_registered'].includes(task.status),
      lines: eligible.map((row) => ({ id: row.request_line_id, label: `第${row.line_no}行 / ${row.material_id}` }))
    } })
  },

  supplyFieldInput(event) {
    if (!this.data.supplyForm || this.data.busy || this.data.supplyBlocked) return
    const field = String(event.currentTarget.dataset.field || '')
    if (!['quantity', 'reference', 'expectedDate', 'comment'].includes(field)
      || (field === 'quantity' && this.data.supplyForm.taskId)
      || (['reference', 'expectedDate'].includes(field) && this.data.supplyForm.status === 'cancelled')) return
    this.setData({ supplyForm: Object.assign({}, this.data.supplyForm, { [field]: event.detail.value, error: '' }) })
  },

  supplyChoice(event) {
    const form = this.data.supplyForm
    if (!form || this.data.busy || this.data.supplyBlocked) return
    const field = String(event.currentTarget.dataset.field || '')
    const value = String(event.currentTarget.dataset.value || '')
    if ((field === 'supplyType' && !form.taskId && contract.SUPPLY_TYPES.includes(value))
      || (field === 'status' && form.taskId && contract.SUPPLY_TASK_STATUSES.includes(value)
        && (!form.cancelOnly || value === 'cancelled')
        && (value !== 'open' || form.canChooseOpen)
        && (value !== 'reference_registered' || form.canChooseReference))
      || (field === 'lineId' && !form.taskId && form.lines.some((row) => row.id === value))) {
      const changes = { [field]: value, error: '' }
      if (field === 'status' && value === 'cancelled') {
        const original = this.data.detail.supply_tasks.find((row) => row.id === form.taskId)
        if (!original) return
        changes.reference = original.reference_no || ''
        changes.expectedDate = original.expected_date || ''
      }
      this.setData({ supplyForm: Object.assign({}, form, changes) })
    }
  },

  closeSupplyForm() {
    if (!this.data.busy && !this.supplyWriteBlocked()) this.setData({ supplyForm: null })
  },

  async submitSupply() {
    if (this.data.busy || this.supplyWriteBlocked() || this._lifecycleRecoveryBlocked) return
    const before = this.data.detail
    const form = this.data.supplyForm
    const access = this._access
    if (!before || !form || !access || !access.can_manage_supply
      || before.request_id !== form.requestId || before.request_version !== form.requestVersion) return
    const task = form.taskId ? before.supply_tasks.find((row) => row.id === form.taskId) : null
    const action = !form.taskId ? 'create_supply_task'
      : form.status === 'cancelled' ? 'cancel_supply_task' : 'update_supply_task'
    if (form.taskId ? (!task || task.version !== form.taskVersion || !task.allowed_actions.includes(action))
      : !before.allowed_actions.includes(action)) return
    let intent
    let sentinel
    let responseValidated = false
    let directPostRejection
    const generation = this._loadGeneration
    this.setData({ busy: true })
    try {
      const body = !form.taskId ? {
        expected_request_version: before.request_version, request_line_id: form.lineId,
        supply_type: form.supplyType, expected_qty: form.quantity.trim(),
        reference_no: form.reference.trim() || null, expected_date: form.expectedDate || null,
        note: form.comment.trim()
      } : {
        expected_request_version: before.request_version, expected_task_version: form.taskVersion,
        status: form.status, reference_no: action === 'cancel_supply_task' ? task.reference_no : form.reference.trim() || null,
        expected_date: action === 'cancel_supply_task' ? task.expected_date : form.expectedDate || null,
        comment: form.comment.trim()
      }
      // Validate locally before a durable marker; the adapter repeats this check before HTTP.
      adapterModule.validateSupplyBody(action, body)
      if (action === 'create_supply_task') {
        const line = before.lines.find((row) => row.request_line_id === form.lineId)
        if (!line || supplyQuantityUnits(body.expected_qty) > supplyRemaining(before, line)) {
          throw new Error('计划数量超过当前明细尚未安排的批准余量')
        }
      }
      await supplyRecovery.verifyIdentity(access, transport)
      if (this._unloaded || this._loadGeneration !== generation || this._access !== access) return
      ensureRegistries(this)
      intent = this._mutationRegistry.begin({ requestId: before.request_id, action,
        path: `/v1/material-requests/${before.request_id}/supply-tasks${form.taskId ? `/${form.taskId}` : ''}`,
        body, expectedVersion: before.request_version })
      sentinel = supplyRecovery.persist({ v: 1, kind: 'material_request_supply',
        trace_request_id: intent.headers['X-Request-ID'], person_id: access.person_id,
        authorization_version: access.authorization_version, request_id: before.request_id,
        request_version: before.request_version, revision_id: before.current_revision_id,
        revision_no: before.current_revision_no, action, supply_task_id: form.taskId,
        task_version: form.taskVersion, created_at: new Date().toISOString() })
      this.setData({ supplyBlocked: true, supplyRecoveryMessage: `正在核验供给操作（${sentinel.trace_request_id}）` })
      let rawResult
      try {
        rawResult = await transport.mutate(intent)
      } catch (caught) {
        directPostRejection = caught
        throw caught
      }
      const result = contract.validateMaterialRequestSupplyTaskMutationResult(rawResult, {
        requestId: before.request_id, action, previousVersion: before.request_version,
        supplyTaskId: form.taskId, previousTaskVersion: form.taskVersion
      })
      responseValidated = true
      const fresh = contract.validateMaterialRequestDetail(await transport.detail(before.request_id))
      const updated = fresh.supply_tasks.find((row) => row.id === result.supply_task_id)
      if (!approvalMutationMatches(result, fresh) || !updated || updated.version !== result.task_version
        || updated.task_no !== result.task_no || updated.status !== result.task_status
        || updated.request_line_id !== form.lineId || updated.supply_type !== form.supplyType
        || updated.substitution_decision_id !== null
        || compareDecimal(decimalUnits(updated.expected_qty), decimalUnits(form.quantity.trim())) !== 0
        || compareDecimal(decimalUnits(updated.original_equivalent_qty), decimalUnits(updated.expected_qty)) !== 0
        || updated.reference_no !== body.reference_no || updated.expected_date !== body.expected_date
        || AXES.some(([key]) => before.states[key] !== fresh.states[key])) throw new Error('供给计划结果回读不一致')
      const freshAccess = await supplyRecovery.verifyIdentity(sentinel, transport)
      if (this._unloaded || this._loadGeneration !== generation || this._access !== access) return
      supplyRecovery.clear(sentinel)
      this._mutationRegistry.confirm(intent.request_id, intent.signature)
      this._access = freshAccess
      this.setData({ supplyBlocked: false, supplyRecoveryMessage: '', supplyForm: null,
        detail: presentDetail(fresh, freshAccess, false), notice: '供给计划已核验；库存及履约状态未改变。' })
    } catch (error) {
      if (this._unloaded || this._loadGeneration !== generation) return
      let released = false
      if (!responseValidated && intent && sentinel && error === directPostRejection
        && adapterModule.isDefinitiveSupplyPostRejection(error)) {
        try {
          const freshAccess = await supplyRecovery.verifyIdentity(sentinel, transport)
          if (this._unloaded || this._loadGeneration !== generation || this._access !== access) return
          if (!exactScalarSnapshotMatches(freshAccess, access)) {
            throw new Error('明确拒绝回收期间身份或权限已变化，原供给坐标继续保留')
          }
          const stored = supplyRecovery.read()
          if (!stored || !exactScalarSnapshotMatches(stored, sentinel)) {
            throw new Error('明确拒绝回收时供给坐标已变化，原阻断继续保留')
          }
          supplyRecovery.clear(sentinel)
          this._mutationRegistry.clearDefinitiveRejection(intent.request_id, intent.signature)
          this._access = freshAccess
          released = true
          this.setData({ supplyBlocked: false, supplyRecoveryMessage: '' })
        } catch (storageError) { this.setData({ supplyBlocked: true, supplyRecoveryMessage: storageError.message }) }
      }
      const message = error.message || '供给操作结果未确认'
      this.setData({ supplyForm: Object.assign({}, form, {
        error: released ? `${message}；服务端已明确拒绝，本地请求坐标已安全解除` : message
      }) })
    } finally { if (!this._unloaded && this._loadGeneration === generation) this.setData({ busy: false }) }
  },

  async submitApprovalProcess() {
    if (this.supplyWriteBlocked()) return
    const before = this.data.detail
    const process = this.data.processing
    if (!before || !process || !this._access) return
    const step = currentApprovalStep(before)
    if (!step || step.step_id !== process.stepId || step.version !== process.stepVersion) {
      this.setData({
        processing: Object.assign({}, process, {
          error: '当前审批步骤或版本已变化，请重新读取详情'
        })
      })
      return
    }
    const allowedActions = new Set(before.allowed_actions)
    const internalPermission = step.step_no === 1
      ? this._access.can_approve_region
      : step.step_no === 2 && this._access.can_approve_headquarters
    const pendingEvidence = ((before.approval_instance &&
      before.approval_instance.external_evidence_summaries) || []).filter((item) => (
      item.step_id === step.step_id && item.status === 'pending_verification'
    ))
    const authorized = process.kind === 'internal'
      ? step.source_mode === 'internal' && internalPermission && allowedActions.has(process.action)
      : process.kind === 'external_registration'
        ? step.source_mode === 'external_registration' && this._access.can_register_external &&
          allowedActions.has('register_external_approval')
        : step.source_mode === 'external_registration' && this._access.can_verify_external &&
          allowedActions.has('verify_external_approval') && pendingEvidence.length === 1 &&
          pendingEvidence[0].registration_id === process.registrationId
    if (!authorized) {
      this.setData({
        processing: Object.assign({}, process, {
          error: '当前权限、详情允许动作或证据锚点已不满足处理条件，已停止写入'
        })
      })
      return
    }
    let body
    let intentAction
    let path
    try {
      const comment = process.comment.trim()
      if (
        process.kind !== 'external_verification' &&
        ['return', 'reject'].includes(process.action) &&
        !comment
      ) throw new Error('退回或驳回必须填写处理意见')
      if (process.kind === 'external_verification') {
        if (!NONZERO_UUID.test(process.registrationId)) throw new Error('待复核证据锚点无效')
        if (process.verificationDecision === 'reject' && !comment) {
          throw new Error('复核拒绝必须填写原因')
        }
        body = {
          expected_request_version: before.request_version,
          expected_step_version: step.version,
          decision: process.verificationDecision,
          comment
        }
        intentAction = 'verify_external_approval'
        path = `/v1/material-requests/${before.request_id}/approval-steps/${step.step_id}/external-evidence/${process.registrationId}/verification`
      } else {
        const linePayload = approvalLinePayload(process)
        body = Object.assign({
          expected_request_version: before.request_version,
          expected_step_version: step.version,
          action: process.action
        }, linePayload, { comment })
        if (process.kind === 'internal') {
          intentAction = process.action
          path = `/v1/material-requests/${before.request_id}/approval-steps/${step.step_id}/decision`
        } else {
          ensureFileUploadControllers(this)
          const evidence = availableFiles(
            this._externalEvidenceUploads,
            'external_approval_evidence'
          )
          const approver = process.externalApproverName.trim()
          const reference = process.externalReferenceNo.trim()
          const decidedAt = process.externalDecidedAt.trim()
          if (evidence.length !== 1) throw new Error('必须先上传并完成一份外部审批证据的 available 确认')
          if (!approver || approver.length > 160) throw new Error('星星总部审批人无效')
          if (!SAFE_EXTERNAL_REFERENCE.test(reference)) throw new Error('外部审批参考号无效')
          if (!AWARE_TIMESTAMP.test(decidedAt) || !Number.isFinite(Date.parse(decidedAt))) {
            throw new Error('外部决定时间必须是包含时区的 ISO8601 时间')
          }
          body = Object.assign({
            expected_request_version: before.request_version,
            expected_step_version: step.version,
            evidence_file_id: evidence[0].file_id,
            external_approver_name: approver,
            external_reference_no: reference,
            external_decided_at: decidedAt,
            action: process.action
          }, linePayload, { comment })
          intentAction = 'register_external_approval'
          path = `/v1/material-requests/${before.request_id}/approval-steps/${step.step_id}/external-evidence`
        }
      }
    } catch (error) {
      this.setData({
        processing: Object.assign({}, process, { error: error.message, pendingMessage: '' })
      })
      return
    }

    ensureRegistries(this)
    this.setData({ busy: true, notice: '' })
    let responseValidated = false
    try {
      const intent = this._mutationRegistry.begin({
        requestId: before.request_id,
        action: intentAction,
        path,
        body,
        expectedVersion: before.request_version
      })
      this.setData({
        processing: Object.assign({}, process, {
          error: '',
          pendingMessage: `处理结果确认中（${intent.headers['X-Request-ID']}）；重试只复用原坐标。`
        })
      })
      const result = contract.validateMaterialRequestMutationResult(
        await transport.mutate(intent),
        { requestId: before.request_id, action: intentAction, previousVersion: before.request_version }
      )
      responseValidated = true
      const reread = contract.validateMaterialRequestDetail(
        await transport.detail(before.request_id),
        before.request_id
      )
      if (!approvalMutationMatches(result, reread)) {
        throw new Error('处理响应与审批锚点/状态回读不一致，仍待人工核验')
      }
      this._mutationRegistry.confirm(intent.request_id, intent.signature)
      if (process.kind === 'external_registration') this._externalEvidenceUploads.clear()
      this.setData({
        detail: presentDetail(reread, this._access, this._lifecycleRecoveryBlocked),
        processing: null,
        notice: '审批处理已完成精确回读；分配、占用及履约状态未被合并。'
      })
    } catch (error) {
      const pending = this._mutationRegistry.get(before.request_id)
      if (!responseValidated && pending && adapterModule.isDefinitiveRejection(error)) {
        this._mutationRegistry.clearDefinitiveRejection(pending.request_id, pending.signature)
        this.setData({
          processing: Object.assign({}, process, {
            error: error.message || '审批处理被明确拒绝', pendingMessage: ''
          })
        })
      } else if (pending) {
        this.setData({
          processing: Object.assign({}, process, {
            error: error.message || '审批处理结果未确认',
            pendingMessage: `处理结果仍未确认（${pending.headers['X-Request-ID']}）；禁止生成新坐标或执行其他动作。`
          })
        })
      }
    } finally {
      this.setData({ busy: false })
    }
  }
})
