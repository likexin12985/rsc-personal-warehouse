const api = require('../../utils/api')
const session = require('../../utils/session')
const { stocktakeAccessDecision } = require('../../utils/production-guard')
const {
  validateOpeningStocktakeDetail,
  validateOpeningStocktakeCountWriteResult,
  validateOpeningStocktakeTerminalWriteResult,
  stocktakeStatusLabel,
  stocktakeActionLabel,
  stocktakeDifferenceLabel
} = require('../../utils/stocktake-contract')

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const QUANTITY = /^(?:0|[1-9][0-9]*)(?:\.[0-9]{1,3})?$/

function pendingQuantityLabel(detail, scope) {
  if (scope.total_counted_qty !== null) return scope.total_counted_qty
  return detail.blind_count && detail.evidence_status === 'counting_hidden'
    ? '盲盘中隐藏'
    : '尚未封存数量'
}

function writeIntentSignature(path, body) {
  return `${path}\n${JSON.stringify(body)}`
}

function confirmedWriteIntent(page, kind, path, body, metadata = {}) {
  const signature = writeIntentSignature(path, body)
  const pending = page._pendingWriteIntent
  if (pending) {
    if (pending.signature !== signature) {
      const error = new Error('存在结果未确认的写请求，禁止以新坐标提交其他操作')
      error.code = 'write_intent_mismatch'
      error.status = 409
      throw error
    }
    showPendingWriteNotice(
      page,
      pending,
      hasRecordedWriteResponse(pending) ? 'projection_pending' : 'retryable'
    )
    return pending
  }
  if (
    typeof api.createIdempotencyKey !== 'function' ||
    typeof api.createRequestId !== 'function'
  ) {
    const error = new Error('当前客户端无法生成安全写请求坐标')
    error.status = 503
    throw error
  }
  const stableBody = JSON.parse(JSON.stringify(body))
  const intent = Object.assign({
    kind,
    path,
    body: stableBody,
    signature: writeIntentSignature(path, stableBody),
    idempotencyKey: api.createIdempotencyKey(),
    requestId: api.createRequestId(),
    postAttempted: false
  }, metadata)
  page._pendingWriteIntent = intent
  showPendingWriteNotice(page, intent, 'in_flight')
  return intent
}

function clearWriteIntent(page, intent) {
  if (page._pendingWriteIntent !== intent) return
  page._pendingWriteIntent = null
  page.setData({
    writePending: false,
    pendingWriteKind: '',
    pendingWriteRetryable: false,
    pendingWriteMessage: '',
    pendingWriteTarget: '',
    pendingWriteRequestId: ''
  })
}

function showPendingWriteNotice(page, intent, state) {
  const retryable = state === 'retryable'
  const projectionPending = state === 'projection_pending'
  const actionLabel = intent.kind === 'count'
    ? '实盘封存'
    : (intent.kind === 'post' ? '期初过账' : '任务关闭')
  const target = intent.kind === 'count'
    ? `任务 ${intent.taskId} · 轮次 ${intent.roundId} · 范围 ${intent.scopeId}`
    : `任务 ${intent.taskId} · 期望版本 v${intent.expectedVersion}`
  const message = retryable
    ? `上一笔${actionLabel}仍待精确确认。对象仍处于原请求可重放状态；再次确认只会复用原请求坐标。`
    : (projectionPending
        ? `上一笔${actionLabel}的成功响应已严格确认，但当前详情尚未包含对应完成状态。小程序不会再次发送该请求；当前只允许刷新并进行只读核验。`
        : `上一笔${actionLabel}结果待确认。当前详情不足以确认原请求结果；即使对象状态已变化，也不能认定原请求成功。小程序已停止新写入，请联系管理员，凭下列对象和追踪 ID 进行只读核验。`)
  page.setData({
    writePending: true,
    pendingWriteKind: intent.kind,
    pendingWriteRetryable: retryable,
    pendingWriteMessage: message,
    pendingWriteTarget: target,
    pendingWriteRequestId: intent.requestId
  })
}

function recordExactWriteResponse(intent, response) {
  intent.confirmedResponse = {
    idempotencyKey: intent.idempotencyKey,
    requestId: intent.requestId,
    responseSignature: JSON.stringify(response)
  }
}

function hasRecordedWriteResponse(intent) {
  return Boolean(intent && intent.confirmedResponse)
}

function exactWriteResponse(intent) {
  const confirmation = intent && intent.confirmedResponse
  if (
    !confirmation ||
    confirmation.idempotencyKey !== intent.idempotencyKey ||
    confirmation.requestId !== intent.requestId ||
    typeof confirmation.responseSignature !== 'string' ||
    !confirmation.responseSignature.length
  ) return null
  try {
    const response = JSON.parse(confirmation.responseSignature)
    return intent.kind === 'count'
      ? validateOpeningStocktakeCountWriteResult(
          response,
          intent.taskId,
          intent.roundId,
          intent.scopeId
        )
      : validateOpeningStocktakeTerminalWriteResult(
          response,
          intent.kind,
          intent.taskId,
          intent.roundId || null,
          intent.expectedVersion
        )
  } catch (_) {
    return null
  }
}

function sameIdentifier(left, right) {
  return typeof left === 'string' && typeof right === 'string' &&
    left.toLowerCase() === right.toLowerCase()
}

function countTaskProjectionContainsResult(resultStatus, currentStatus) {
  const allowedSuccessors = {
    counting: [
      'counting',
      'submitted',
      'region_review',
      'hq_review',
      'approved',
      'recount_required',
      'posted',
      'closed'
    ],
    submitted: [
      'submitted',
      'region_review',
      'hq_review',
      'approved',
      'recount_required',
      'posted',
      'closed'
    ],
    region_review: [
      'region_review',
      'hq_review',
      'approved',
      'recount_required',
      'posted',
      'closed'
    ],
    hq_review: ['hq_review', 'approved', 'recount_required', 'posted', 'closed'],
    approved: ['approved', 'posted', 'closed'],
    recount_required: ['recount_required'],
    posted: ['posted', 'closed'],
    closed: ['closed']
  }
  return Array.isArray(allowedSuccessors[resultStatus]) &&
    allowedSuccessors[resultStatus].includes(currentStatus)
}

function countProjectionContainsResult(intent, result, detail) {
  if (
    !detail.current_round ||
    !sameIdentifier(detail.current_round.round_id, result.round_id) ||
    !sameIdentifier(intent.roundId, result.round_id) ||
    !countTaskProjectionContainsResult(result.task_status, detail.status)
  ) return false
  const scope = detail.scopes.find((row) => (
    sameIdentifier(row.scope_id, result.scope_id) &&
    row.completion_status === 'completed' &&
    typeof row.completed_at === 'string'
  ))
  if (!scope) return false
  if (result.round_sealed) {
    return detail.current_round.status === 'submitted' &&
      detail.evidence_status === 'sealed'
  }
  return detail.current_round.status === 'counting' || (
    detail.current_round.status === 'submitted' &&
    detail.evidence_status === 'sealed'
  )
}

function terminalProjectionContainsResult(intent, result, detail) {
  if (
    !intent.roundId ||
    !detail.current_round ||
    !sameIdentifier(detail.current_round.round_id, intent.roundId) ||
    detail.current_round.status !== 'submitted' ||
    detail.evidence_status !== 'sealed' ||
    detail.task_version < result.task_version
  ) return false
  if (intent.kind === 'post') {
    return detail.status === 'posted' || (
      detail.status === 'closed' &&
      detail.task_version > result.task_version
    )
  }
  return intent.kind === 'close' && detail.status === 'closed'
}

function projectionContainsWriteResult(intent, result, detail) {
  return intent.kind === 'count'
    ? countProjectionContainsResult(intent, result, detail)
    : terminalProjectionContainsResult(intent, result, detail)
}

function blockWriteRequest(page, kind, label, invocation = null) {
  if (
    page.data.submitting ||
    (page._activeWriteInvocation && page._activeWriteInvocation !== invocation)
  ) {
    wx.showToast({ title: `${label}正在处理，请勿重复提交`, icon: 'none' })
    return true
  }
  const pending = page._pendingWriteIntent
  if (hasRecordedWriteResponse(pending)) {
    showPendingWriteNotice(page, pending, 'projection_pending')
    wx.showToast({ title: '原请求成功响应已确认，当前仅允许刷新详情', icon: 'none' })
    return true
  }
  if (
    page.data.writePending &&
    !(page.data.pendingWriteRetryable && page.data.pendingWriteKind === kind)
  ) {
    wx.showToast({ title: `原${label}请求仍待核验，禁止创建新请求`, icon: 'none' })
    return true
  }
  return false
}

function beginWriteInvocation(page, label) {
  if (page._activeWriteInvocation) {
    wx.showToast({ title: `${label}正在处理，请勿重复提交`, icon: 'none' })
    return null
  }
  const invocation = Symbol(label)
  page._activeWriteInvocation = invocation
  return invocation
}

function endWriteInvocation(page, invocation) {
  if (page._activeWriteInvocation === invocation) {
    page._activeWriteInvocation = null
  }
}

function validateAndRecordCountResponse(intent, response) {
  try {
    const verified = validateOpeningStocktakeCountWriteResult(
      response,
      intent.taskId,
      intent.roundId,
      intent.scopeId
    )
    recordExactWriteResponse(intent, verified)
  } catch (error) {
    error.writeResultUncertain = true
    throw error
  }
}

function validateAndRecordTerminalResponse(intent, response) {
  try {
    const verified = validateOpeningStocktakeTerminalWriteResult(
      response,
      intent.kind,
      intent.taskId,
      intent.roundId || null,
      intent.expectedVersion
    )
    recordExactWriteResponse(intent, verified)
  } catch (error) {
    error.writeResultUncertain = true
    throw error
  }
}

function uncertainWriteFailure(error) {
  if (error && error.writeResultUncertain) return true
  if (error && error.code === 'write_intent_mismatch') return true
  if (!error || !Number.isInteger(error.status)) return true
  return (
    error.status === 0 ||
    error.status === 408 ||
    error.status === 425 ||
    error.status >= 500
  )
}

const NO_EFFECT_WRITE_REJECTIONS = Object.freeze({
  count: Object.freeze(['opening_count_state_invalid']),
  post: Object.freeze(['opening_finalize_state_invalid']),
  close: Object.freeze([
    'opening_finalize_state_invalid',
    'opening_close_reconciliation_pending'
  ])
})

function isDefinitiveFirstPostRejection(intent, error, attempt) {
  const actionCodes = intent && Object.prototype.hasOwnProperty.call(
    NO_EFFECT_WRITE_REJECTIONS,
    intent.kind
  )
    ? NO_EFFECT_WRITE_REJECTIONS[intent.kind]
    : null
  if (
    !Array.isArray(actionCodes) ||
    !error ||
    error.responseReceived !== true ||
    !attempt ||
    attempt.firstDirectPost !== true ||
    attempt.directPostRejected !== true ||
    attempt.directPostRejection !== error
  ) return false
  if (
    error.status === 400 &&
    error.category === 'invalid_request'
  ) {
    return error.code === 'idempotency_key_invalid' ||
      error.code === 'x_request_id_invalid'
  }
  return error.status === 412 &&
    error.category === 'precondition_failed' &&
    actionCodes.includes(error.code)
}

function writeIntentState(intent, detail) {
  if (!intent) return 'unresolved'
  const responseRecorded = hasRecordedWriteResponse(intent)
  if (!detail || !sameIdentifier(detail.task_id, intent.taskId)) {
    return responseRecorded ? 'projection_pending' : 'unresolved'
  }
  if (responseRecorded) {
    const result = exactWriteResponse(intent)
    return result && projectionContainsWriteResult(intent, result, detail)
      ? 'confirmed'
      : 'projection_pending'
  }
  if (intent.kind === 'count') {
    const scope = detail.scopes.find((row) => row.scope_id === intent.scopeId)
    if (
      detail.current_round &&
      detail.current_round.round_id === intent.roundId &&
      detail.canCount &&
      scope &&
      scope.assigned_to_me &&
      scope.completion_status === 'pending'
    ) return 'retryable'
    return 'handoff_required'
  }
  if (intent.kind === 'post') {
    return detail.status === 'approved' &&
      detail.canPost &&
      detail.task_version === intent.expectedVersion
      ? 'retryable'
      : 'handoff_required'
  }
  if (intent.kind === 'close') {
    return detail.status === 'posted' &&
      detail.canClose &&
      detail.task_version === intent.expectedVersion
      ? 'retryable'
      : 'handoff_required'
  }
  return 'unresolved'
}

function reconcileWriteIntent(page, detail) {
  const intent = page._pendingWriteIntent
  if (!intent) {
    page.setData({
      writePending: false,
      pendingWriteKind: '',
      pendingWriteRetryable: false,
      pendingWriteMessage: '',
      pendingWriteTarget: '',
      pendingWriteRequestId: ''
    })
    return 'none'
  }
  const state = writeIntentState(intent, detail)
  if (state === 'confirmed') {
    clearWriteIntent(page, intent)
    if (intent.kind === 'count') page.setData({ draftObservations: [] })
  } else {
    if (intent.kind === 'count') {
      page.setData({
        selectedScopeId: state === 'retryable' ? intent.scopeId : '',
        draftObservations: JSON.parse(
          JSON.stringify(intent.body.physical_observations)
        )
      })
    }
    showPendingWriteNotice(page, intent, state)
  }
  return state
}

async function recoverWriteFailure(page, intent, error, attempt) {
  if (!intent) return { uncertain: uncertainWriteFailure(error), refreshed: false, state: 'none' }
  if (
    page._pendingWriteIntent === intent &&
    intent.postAttempted === true &&
    !hasRecordedWriteResponse(intent) &&
    isDefinitiveFirstPostRejection(intent, error, attempt)
  ) {
    clearWriteIntent(page, intent)
    const refreshed = await page.load()
    return { uncertain: false, refreshed, state: 'definitive' }
  }
  const refreshed = await page.load()
  return {
    uncertain: true,
    refreshed,
    state: page._lastWriteIntentState || 'unresolved'
  }
}

function presentDetail(detail) {
  return Object.assign({}, detail, {
    statusLabel: stocktakeStatusLabel(detail.status),
    roundLabel: detail.current_round
      ? `第 ${detail.current_round.round_no} 轮 · ${detail.current_round.round_type === 'initial' ? '初盘' : '复盘'}`
      : '尚无轮次',
    actionLabels: detail.allowed_actions.map(stocktakeActionLabel).join(' · ') || '只读',
    canCount: detail.allowed_actions.includes('count'),
    canReviewRegion: detail.allowed_actions.includes('review_region'),
    canReviewHeadquarters: detail.allowed_actions.includes('review_headquarters'),
    canOpenRecount: detail.allowed_actions.includes('open_recount'),
    canPost: detail.allowed_actions.includes('post'),
    canClose: detail.allowed_actions.includes('close'),
    scopes: detail.scopes.map((scope) => Object.assign({}, scope, {
      completionLabel: scope.completion_status === 'completed' ? '已完成' : '待盘点',
      quantityLabel: pendingQuantityLabel(detail, scope)
    })),
    differences: detail.differences.map((difference) => Object.assign({}, difference, {
      typeLabel: stocktakeDifferenceLabel(difference.difference_type)
    })),
    reviews: detail.reviews.map((review) => Object.assign({}, review, {
      stageLabel: review.stage === 'region' ? '区域复核' : '总部复核',
      decisionLabel: review.decision === 'approve'
        ? '通过'
        : (review.decision === 'recount' ? '要求复盘' : '驳回')
    }))
  })
}

Page({
  data: {
    taskId: '',
    loading: true,
    submitting: false,
    accessAllowed: false,
    accessMessage: '正在校验正式盘点权限',
    detail: null,
    selectedScopeId: '',
    draftObservations: [],
    writePending: false,
    pendingWriteKind: '',
    pendingWriteRetryable: false,
    pendingWriteMessage: '',
    pendingWriteTarget: '',
    pendingWriteRequestId: '',
    identifierOptions: [
      { label: 'SKU 物料号', value: 'sku_code' },
      { label: '二维码', value: 'qr_code' },
      { label: '外部编码', value: 'external_code' },
      { label: '待核实标识', value: 'unknown' }
    ],
    identifierIndex: 0,
    conditionOptions: [
      { label: '新件', value: 'new' },
      { label: '旧件', value: 'used' },
      { label: '坏件', value: 'damaged' },
      { label: '已报废', value: 'scrapped' }
    ],
    conditionIndex: 0,
    availabilityOptions: [
      { label: '可用', value: 'available' },
      { label: '已占用', value: 'reserved' },
      { label: '待拣货', value: 'picking' },
      { label: '待出库', value: 'outbound' },
      { label: '在途', value: 'in_transit' },
      { label: '到货待验', value: 'arrived_pending' },
      { label: '冻结', value: 'frozen' },
      { label: '待退回', value: 'return_pending' },
      { label: '待报废', value: 'scrap_pending' }
    ],
    availabilityIndex: 0,
    materialIdentifier: '',
    quantity: '',
    lotNo: '',
    serialNo: '',
    remark: '',
    countMethod: 'manual'
  },

  onLoad(options) {
    let taskId = ''
    try { taskId = decodeURIComponent(String(options.task_id || '')) } catch (_) {}
    if (!UUID.test(taskId)) {
      this.setData({ loading: false, accessMessage: '盘点任务标识无效' })
      return
    }
    this.setData({ taskId: taskId.toLowerCase() })
  },

  onShow() {
    if (!this.data.taskId || !session.ensureLogin()) return
    this.load()
  },

  onPullDownRefresh() {
    this.load().finally(() => wx.stopPullDownRefresh())
  },

  async load() {
    if (!this.data.taskId) return false
    const generation = (this._loadGeneration || 0) + 1
    this._loadGeneration = generation
    this.setData({
      loading: true,
      accessAllowed: false,
      accessMessage: '正在校验正式盘点权限',
      detail: null,
      selectedScopeId: ''
    })
    try {
      const [user, context] = await Promise.all([
        api.get('/auth/me'),
        api.get('/access/context')
      ])
      if (generation !== this._loadGeneration) return false
      if (!getApp().setUser(user)) {
        session.ensureLogin()
        return false
      }
      const decision = stocktakeAccessDecision(context, user)
      if (!decision.allowed) {
        this.setData({ accessMessage: decision.message })
        return false
      }
      const verified = validateOpeningStocktakeDetail(
        await api.get(`/v1/stocktakes/opening/${this.data.taskId}`),
        this.data.taskId
      )
      if (generation !== this._loadGeneration) return false
      const detail = presentDetail(verified)
      const countable = detail.canCount
        ? detail.scopes.find((scope) => (
          scope.assigned_to_me && scope.completion_status === 'pending'
        ))
        : null
      this.setData({
        accessAllowed: true,
        accessMessage: '正式盘点证据已验证',
        detail,
        selectedScopeId: countable ? countable.scope_id : ''
      })
      this._lastWriteIntentState = reconcileWriteIntent(this, detail)
      return true
    } catch (error) {
      if (generation !== this._loadGeneration) return false
      this.setData({
        accessAllowed: false,
        accessMessage: '无法确认身份、权限或盘点响应契约，已失败关闭。',
        detail: null,
        selectedScopeId: ''
      })
      wx.showToast({ title: error.message || '盘点详情读取失败', icon: 'none' })
      this._lastWriteIntentState = 'unresolved'
      return false
    } finally {
      if (generation === this._loadGeneration) this.setData({ loading: false })
    }
  },

  chooseScope(event) {
    if (this.data.writePending) {
      wx.showToast({ title: '上一写请求仍待确认，已停止切换盘点范围', icon: 'none' })
      return
    }
    const scopeId = String(event.currentTarget.dataset.id || '')
    if (
      scopeId !== this.data.selectedScopeId &&
      this.data.draftObservations.length
    ) {
      wx.showToast({ title: '请先移除当前范围的草稿明细', icon: 'none' })
      return
    }
    const scope = this.data.detail && this.data.detail.scopes.find((item) => (
      item.scope_id === scopeId &&
      item.assigned_to_me &&
      item.completion_status === 'pending'
    ))
    if (scope) this.setData({ selectedScopeId: scopeId })
  },

  bindMaterial(event) { this.setData({ materialIdentifier: event.detail.value }) },
  bindQuantity(event) { this.setData({ quantity: event.detail.value }) },
  bindLot(event) { this.setData({ lotNo: event.detail.value }) },
  bindSerial(event) { this.setData({ serialNo: event.detail.value }) },
  bindRemark(event) { this.setData({ remark: event.detail.value }) },
  changeIdentifierType(event) { this.setData({ identifierIndex: Number(event.detail.value) }) },
  changeCondition(event) { this.setData({ conditionIndex: Number(event.detail.value) }) },
  changeAvailability(event) { this.setData({ availabilityIndex: Number(event.detail.value) }) },

  scanMaterial() {
    wx.scanCode({
      onlyFromCamera: true,
      success: (result) => this.setData({
        materialIdentifier: String(result.result || '').trim(),
        identifierIndex: 1,
        countMethod: 'scan'
      }),
      fail: () => wx.showToast({ title: '未读取到物料二维码', icon: 'none' })
    })
  },

  scanSerial() {
    wx.scanCode({
      onlyFromCamera: true,
      success: (result) => this.setData({
        serialNo: String(result.result || '').trim(),
        countMethod: 'scan'
      }),
      fail: () => wx.showToast({ title: '未读取到 SN', icon: 'none' })
    })
  },

  addObservation() {
    if (this.data.writePending) {
      wx.showToast({ title: '上一写请求仍待确认，草稿已锁定', icon: 'none' })
      return
    }
    const material = this.data.materialIdentifier.trim()
    const quantity = this.data.quantity.trim()
    const lotNo = this.data.lotNo.trim()
    const serialNo = this.data.serialNo.trim()
    const remark = this.data.remark.trim()
    if (!material || !QUANTITY.test(quantity) || Number(quantity) <= 0) {
      wx.showToast({ title: '请填写物料号和正数数量（最多三位小数）', icon: 'none' })
      return
    }
    if (serialNo && Number(quantity) !== 1) {
      wx.showToast({ title: 'SN 物料每条实盘数量必须为 1', icon: 'none' })
      return
    }
    const observation = {
      material_identifier_raw: material,
      material_identifier_type: this.data.identifierOptions[this.data.identifierIndex].value,
      condition_code: this.data.conditionOptions[this.data.conditionIndex].value,
      availability_bucket: this.data.availabilityOptions[this.data.availabilityIndex].value,
      counted_qty: quantity,
      lot_no_raw: lotNo || null,
      serial_no_raw: serialNo || null,
      serial_identifier_type: serialNo ? 'serial_no' : null,
      count_method: this.data.countMethod,
      reason_code: null,
      remark
    }
    const duplicate = this.data.draftObservations.some((row) => (
      row.material_identifier_raw === observation.material_identifier_raw &&
      row.material_identifier_type === observation.material_identifier_type &&
      row.condition_code === observation.condition_code &&
      row.availability_bucket === observation.availability_bucket &&
      row.lot_no_raw === observation.lot_no_raw &&
      row.serial_no_raw === observation.serial_no_raw
    ))
    if (duplicate) {
      wx.showToast({ title: '相同实物维度请合并数量后再添加', icon: 'none' })
      return
    }
    this.setData({
      draftObservations: this.data.draftObservations.concat([observation]),
      materialIdentifier: '',
      quantity: '',
      lotNo: '',
      serialNo: '',
      remark: '',
      countMethod: 'manual'
    })
  },

  removeObservation(event) {
    if (this.data.writePending) {
      wx.showToast({ title: '上一写请求仍待确认，草稿已锁定', icon: 'none' })
      return
    }
    const index = Number(event.currentTarget.dataset.index)
    if (!Number.isSafeInteger(index) || index < 0) return
    this.setData({
      draftObservations: this.data.draftObservations.filter((_, rowIndex) => rowIndex !== index)
    })
  },

  async submitCount(event) {
    const zero = event.currentTarget.dataset.zero === 'true'
    if (blockWriteRequest(this, 'count', '实盘')) return
    const detail = this.data.detail
    if (
      !detail ||
      !detail.current_round ||
      !detail.canCount ||
      !this.data.selectedScopeId
    ) return
    if (zero && this.data.draftObservations.length) {
      wx.showToast({ title: '零库存确认不能与实盘明细同时提交', icon: 'none' })
      return
    }
    if (!zero && !this.data.draftObservations.length) {
      wx.showToast({ title: '请先添加完整实盘明细，或使用零库存确认', icon: 'none' })
      return
    }
    const invocation = beginWriteInvocation(this, '实盘')
    if (!invocation) return
    try {
      const confirmed = await new Promise((resolve) => wx.showModal({
        title: zero ? '确认本范围为零库存？' : '提交本范围完整实盘？',
        content: zero
          ? '提交后本轮该范围将封存，不能继续追加明细。'
          : `将一次性提交 ${this.data.draftObservations.length} 条实盘证据，提交后不能追加。`,
        confirmText: '确认提交',
        success: (result) => resolve(result.confirm),
        fail: () => resolve(false)
      }))
      if (!confirmed || blockWriteRequest(this, 'count', '实盘', invocation)) return
      this.setData({ submitting: true })
      let intent = null
      let firstDirectPost = false
      let directPostRejected = false
      let directPostRejection
      try {
        const path = `/v1/stocktakes/opening/${detail.task_id}/rounds/${detail.current_round.round_id}/scopes/${this.data.selectedScopeId}/count`
        const body = {
          physical_observations: zero ? [] : this.data.draftObservations,
          zero_confirmed: zero
        }
        const pendingBeforeInvocation = this._pendingWriteIntent
        intent = confirmedWriteIntent(this, 'count', path, body, {
          taskId: detail.task_id,
          roundId: detail.current_round.round_id,
          scopeId: this.data.selectedScopeId
        })
        if (hasRecordedWriteResponse(intent)) {
          showPendingWriteNotice(this, intent, 'projection_pending')
          return
        }
        firstDirectPost = !pendingBeforeInvocation &&
          this._pendingWriteIntent === intent &&
          intent.postAttempted !== true
        intent.postAttempted = true
        let response
        try {
          response = await api.post(path, intent.body, {
            idempotencyKey: intent.idempotencyKey,
            requestId: intent.requestId
          })
        } catch (error) {
          directPostRejected = true
          directPostRejection = error
          throw error
        }
        validateAndRecordCountResponse(intent, response)
        showPendingWriteNotice(this, intent, 'projection_pending')
        const refreshed = await this.load()
        const state = this._lastWriteIntentState || 'unresolved'
        if (refreshed && state === 'confirmed') {
          clearWriteIntent(this, intent)
          this.setData({ draftObservations: [] })
          wx.showToast({ title: '本范围实盘已封存', icon: 'success' })
        } else {
          wx.showToast({
            title: refreshed
              ? '提交已受理，但回读未确认生效；禁止创建新请求'
              : '提交已受理，但回读失败；请恢复网络后刷新确认',
            icon: 'none'
          })
        }
      } catch (error) {
        const outcome = await recoverWriteFailure(
          this,
          intent || this._pendingWriteIntent,
          error,
          { firstDirectPost, directPostRejected, directPostRejection }
        )
        const uncertainMessage = !outcome.refreshed
          ? '提交结果未确认且回读失败，禁止新请求；请恢复网络后刷新'
          : (outcome.state === 'retryable'
              ? '提交结果未确认；已回读，再次确认将复用原请求'
              : '对象状态虽已变化但无法匹配原请求；仍待确认，请联系管理员核验')
        wx.showToast({
          title: outcome.uncertain
            ? uncertainMessage
            : (error.message || '实盘提交失败，已重新读取'),
          icon: 'none'
        })
      } finally {
        this.setData({ submitting: false })
      }
    } finally {
      endWriteInvocation(this, invocation)
    }
  },

  async terminalAction(event) {
    const action = String(event.currentTarget.dataset.action || '')
    if (!['post', 'close'].includes(action)) return
    if (blockWriteRequest(this, action, '状态')) return
    const detail = this.data.detail
    if (
      !detail ||
      !((action === 'post' && detail.canPost) || (action === 'close' && detail.canClose))
    ) return
    const invocation = beginWriteInvocation(this, '状态')
    if (!invocation) return
    try {
      const confirmed = await new Promise((resolve) => wx.showModal({
        title: action === 'post' ? '确认期初过账？' : '确认关闭盘点？',
        content: action === 'post'
          ? '过账只建立库存事实，不等于任务关闭。过账后仍需单独核对并关闭。'
          : '仅在账面、实盘、流水与 SN 均完成对账后关闭。',
        confirmText: action === 'post' ? '确认过账' : '确认关闭',
        success: (result) => resolve(result.confirm),
        fail: () => resolve(false)
      }))
      if (!confirmed || blockWriteRequest(this, action, '状态', invocation)) return
      this.setData({ submitting: true })
      let intent = null
      let firstDirectPost = false
      let directPostRejected = false
      let directPostRejection
      try {
        const path = `/v1/stocktakes/opening/${detail.task_id}/${action}`
        const body = { expected_version: detail.task_version }
        const pendingBeforeInvocation = this._pendingWriteIntent
        intent = confirmedWriteIntent(this, action, path, body, {
          taskId: detail.task_id,
          roundId: detail.current_round ? detail.current_round.round_id : null,
          expectedVersion: detail.task_version
        })
        if (hasRecordedWriteResponse(intent)) {
          showPendingWriteNotice(this, intent, 'projection_pending')
          return
        }
        firstDirectPost = !pendingBeforeInvocation &&
          this._pendingWriteIntent === intent &&
          intent.postAttempted !== true
        intent.postAttempted = true
        let response
        try {
          response = await api.post(path, intent.body, {
            idempotencyKey: intent.idempotencyKey,
            requestId: intent.requestId
          })
        } catch (error) {
          directPostRejected = true
          directPostRejection = error
          throw error
        }
        validateAndRecordTerminalResponse(intent, response)
        showPendingWriteNotice(this, intent, 'projection_pending')
        const refreshed = await this.load()
        const state = this._lastWriteIntentState || 'unresolved'
        if (refreshed && state === 'confirmed') {
          clearWriteIntent(this, intent)
          const postTitle = this.data.detail && this.data.detail.status === 'closed'
            ? '过账已确认，当前已关闭'
            : '已过账，尚未关闭'
          wx.showToast({ title: action === 'post' ? postTitle : '盘点已关闭', icon: 'success' })
        } else {
          wx.showToast({
            title: refreshed
              ? '状态请求已受理，但回读未确认生效；禁止创建新请求'
              : '状态请求已受理，但回读失败；请恢复网络后刷新确认',
            icon: 'none'
          })
        }
      } catch (error) {
        const outcome = await recoverWriteFailure(
          this,
          intent || this._pendingWriteIntent,
          error,
          { firstDirectPost, directPostRejected, directPostRejection }
        )
        const uncertainMessage = !outcome.refreshed
          ? '状态结果未确认且回读失败，禁止新请求；请恢复网络后刷新'
          : (outcome.state === 'retryable'
              ? '状态结果未确认；已回读，再次确认将复用原请求'
              : '对象状态虽已变化但无法匹配原请求；仍待确认，请联系管理员核验')
        wx.showToast({
          title: outcome.uncertain
            ? uncertainMessage
            : (error.message || '状态操作失败，已重新读取'),
          icon: 'none'
        })
      } finally {
        this.setData({ submitting: false })
      }
    } finally {
      endWriteInvocation(this, invocation)
    }
  }
})
