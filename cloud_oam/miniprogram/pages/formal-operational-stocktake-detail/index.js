const session = require('../../utils/session')
const { formalStocktakeAdapter, createFormalStocktakeIntentRegistry } = require('../../utils/formal-stocktake-adapter')
const { fixedQuantityText, formalStocktakeLabels } = require('../../utils/formal-stocktake-contract')
const formalFileUpload = require('../../utils/formal-file-upload')

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const NONZERO_UUID = /^(?!00000000-0000-0000-0000-000000000000$)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

function idList(value, name) {
  const rows = String(value || '').split(/[\s,，]+/).map((item) => item.trim().toLowerCase()).filter(Boolean)
  if (rows.some((item) => !UUID.test(item))) throw new Error(`${name} 必须是正式 UUID`)
  if (new Set(rows).size !== rows.length) throw new Error(`${name} 不能重复`)
  return rows
}

function currentRound(detail) {
  return detail.rounds.find((row) => row.round_no === detail.current_round_no) || null
}

function viewDetail(detail, access) {
  const round = currentRound(detail)
  const axes = [
    ['计数', detail.state_axes.count_status],
    ['差异', detail.state_axes.difference_status],
    ['区域复核', detail.state_axes.region_review_status],
    ['总部复核', detail.state_axes.headquarters_review_status],
    ['复盘', detail.state_axes.recount_status],
    ['过账', detail.state_axes.posting_status],
    ['内部对账', detail.state_axes.reconciliation_status],
    ['关闭', detail.state_axes.closure_status]
  ].map(([label, value]) => ({ label, value, valueLabel: ({ not_started: '未开始', counting: '进行中', submitted: '已提交', not_ready: '未就绪', not_evaluated: '待生成', evaluated: '已生成', hidden_for_blind_counter: '盲盘隐藏', pending: '待处理', approve: '已通过', recount: '要求复盘', reject: '已驳回', not_required: '无需复盘', required: '待开复盘', not_posted: '未过账', not_reconciled: '未对账', recorded: '已记录', stale: '已失效', open: '未关闭', closed: '已关闭' })[value] || value }))
  return Object.assign({}, detail, {
    typeLabel: formalStocktakeLabels.taskType[detail.task_type],
    statusLabel: formalStocktakeLabels.status[detail.status],
    axes,
    currentRound: round ? Object.assign({}, round, {
      differences: round.visible_differences.map((item) => Object.assign({}, item, { typeLabel: formalStocktakeLabels.difference[item.difference_type] }))
    }) : null,
    canStart: access.can_count && detail.allowed_actions.includes('start'),
    canInitialDifference: Boolean(access.can_manage && round && round.allowed_actions.includes('generate_initial_differences')),
    canRecountDifference: Boolean(access.can_manage && round && round.allowed_actions.includes('generate_recount_differences')),
    canReviewRegion: Boolean(access.can_review_region && round && round.allowed_actions.includes('review_region')),
    canReviewHeadquarters: Boolean(access.can_review_headquarters && round && round.allowed_actions.includes('review_headquarters')),
    canOpenRecount: Boolean(access.can_manage && round && round.allowed_actions.includes('open_recount')),
    canReconcile: Boolean(access.can_reconcile && detail.allowed_actions.includes('reconcile')),
    canClose: Boolean(access.can_close && detail.allowed_actions.includes('close')),
    scopes: detail.scopes.map((scope) => Object.assign({}, scope, {
      canInitialCount: access.can_count && scope.allowed_actions.includes('submit_initial_count'),
      canRecountCount: access.can_count && scope.allowed_actions.includes('submit_recount_count'),
      snapshotLabel: ({ not_started: '尚未启动', hidden: '盲盘隐藏', visible: '账面可见' })[scope.snapshot_visibility],
      recountSelected: false,
      recountOptions: [],
      recountAssigneeIndex: 0,
      recountAssigneeUserId: ''
    }))
  })
}

function ensureEvidenceUploads(page) {
  if (page._evidenceUploads) return
  page._evidenceUploads = formalFileUpload.createFormalFileUploadController({
    purpose: 'stocktake_evidence',
    multiple: true,
    onChange(snapshot) {
      page.setData({
        evidenceUploadFiles: snapshot.files,
        evidenceUploadBlocking: snapshot.blocking,
        evidenceUploadCanChoose: snapshot.canChoose
      })
    }
  })
}

function availableEvidenceFiles(page) {
  ensureEvidenceUploads(page)
  const snapshot = page._evidenceUploads.snapshot()
  if (snapshot.blocking) throw new Error('盘点证据尚未完成 available 严格确认，已停止计数写入')
  for (const file of snapshot.availableFiles) {
    if (
      !NONZERO_UUID.test(file.file_id) ||
      file.purpose !== 'stocktake_evidence' ||
      file.status !== 'available'
    ) throw new Error('盘点证据用途或 available 状态与当前范围不一致')
  }
  return snapshot.availableFiles
}

function countIntentSignature(input) {
  return JSON.stringify({
    action: input.action,
    task_id: input.taskId,
    round_id: input.roundId,
    scope_id: input.scopeId,
    expected_task_version: input.expectedTaskVersion,
    body: input.body
  })
}

Page({
  data: {
    loading: true,
    busy: false,
    accessAllowed: false,
    accessMessage: '正在校验',
    detail: null,
    errorMessage: '',
    pendingMessage: '',
    pendingRetryable: false,
    terminalConfirm: '',
    selectedScopeId: '',
    countAction: '',
    accountDrafts: [],
    observations: [],
    materialIdentifier: '',
    quantity: '',
    conditionCode: 'new',
    availabilityBucket: 'available',
    lotNo: '',
    serialNo: '',
    remark: '',
    evidenceUploadFiles: [],
    evidenceUploadBlocking: false,
    evidenceUploadCanChoose: true,
    reviewComment: '',
    recountSelections: [],
    recountReason: '',
    recountReady: false,
    recountLoadingScopeId: '',
    hasAccountInput: false,
    conditionOptions: [{ value: 'new', label: '新件' }, { value: 'used', label: '旧件' }, { value: 'damaged', label: '损坏' }, { value: 'scrapped', label: '报废' }],
    availabilityOptions: [{ value: 'available', label: '可用' }, { value: 'reserved', label: '已占用' }, { value: 'picking', label: '拣货中' }, { value: 'outbound', label: '已出库' }, { value: 'in_transit', label: '在途' }, { value: 'arrived_pending', label: '到达待入库' }, { value: 'frozen', label: '冻结' }, { value: 'return_pending', label: '退回待处理' }, { value: 'scrap_pending', label: '报废待处理' }],
    conditionIndex: 0,
    availabilityIndex: 0
  },

  onLoad(options) {
    this._taskId = String(options.task_id || '').toLowerCase()
    this._intentRegistry = createFormalStocktakeIntentRegistry()
    this._evidenceClaims = new Map()
    this._uploadIdentity = ''
    ensureEvidenceUploads(this)
  },
  onShow() { if (session.ensureLogin()) this.load() },
  onPullDownRefresh() { this.load().finally(() => wx.stopPullDownRefresh()) },
  onUnload() {
    this._loadGeneration = (this._loadGeneration || 0) + 1
    if (this._evidenceUploads) this._evidenceUploads.clear()
    if (this._evidenceClaims) this._evidenceClaims.clear()
    this._access = null
    this._detail = null
    this._uploadIdentity = ''
  },

  async load() {
    if (!UUID.test(this._taskId || '')) {
      this.setData({ loading: false, accessAllowed: false, accessMessage: '任务标识无效，已停止读取', detail: null })
      return
    }
    const generation = (this._loadGeneration || 0) + 1
    this._loadGeneration = generation
    this.setData({ loading: true, errorMessage: '' })
    try {
      const access = await formalStocktakeAdapter.loadAccess()
      if (!access.can_read) throw new Error('当前授权不包含正式盘点只读权限')
      const detail = await formalStocktakeAdapter.detail(this._taskId)
      if (generation !== this._loadGeneration) return
      ensureEvidenceUploads(this)
      const nextUploadIdentity = `${access.person_id}:${access.authorization_version}`
      const previousDetailCoordinate = this._detail
        ? `${this._detail.task_id}:${this._detail.version}:${this._detail.current_round_no}`
        : ''
      const nextDetailCoordinate = `${detail.task_id}:${detail.version}:${detail.current_round_no}`
      if (
        (this._uploadIdentity && this._uploadIdentity !== nextUploadIdentity) ||
        (previousDetailCoordinate && previousDetailCoordinate !== nextDetailCoordinate)
      ) {
        this._evidenceUploads.clear()
        this.setData({ selectedScopeId: '', countAction: '', accountDrafts: [], observations: [] })
      }
      if (this._uploadIdentity && this._uploadIdentity !== nextUploadIdentity) {
        this._evidenceClaims.clear()
      }
      this._uploadIdentity = nextUploadIdentity
      this._access = access
      this._detail = detail
      this.setData({ accessAllowed: true, accessMessage: `正式盘点权限已验证 · v${access.authorization_version}`, detail: viewDetail(detail, access), terminalConfirm: '' })
    } catch (error) {
      if (generation !== this._loadGeneration) return
      this._access = null; this._detail = null
      this._uploadIdentity = ''
      if (this._evidenceUploads) this._evidenceUploads.clear()
      if (this._evidenceClaims) this._evidenceClaims.clear()
      this.setData({ accessAllowed: false, accessMessage: '身份、授权或正式响应未通过校验，已失败关闭', detail: null, terminalConfirm: '', errorMessage: error.message || '正式盘点详情读取失败' })
    } finally { if (generation === this._loadGeneration) this.setData({ loading: false }) }
  },

  async run(input) {
    if (!this._access || !this._detail || this.data.busy) return
    let intent
    try {
      intent = this._intentRegistry.current() || this._intentRegistry.begin(input)
      this.setData({ busy: true, errorMessage: '', pendingMessage: '', pendingRetryable: false, terminalConfirm: '' })
      const completed = await formalStocktakeAdapter.execute(intent)
      this._intentRegistry.complete(intent)
      this._detail = completed.detail
      if (this._evidenceUploads) this._evidenceUploads.clear()
      this.setData({ detail: viewDetail(completed.detail, this._access), selectedScopeId: '', countAction: '', accountDrafts: [], observations: [], pendingMessage: '', pendingRetryable: false, recountSelections: [], recountReason: '', recountReady: false })
    } catch (error) {
      const uncertain = error && error.write_result_uncertain === true
      if (intent && !uncertain) this._intentRegistry.complete(intent)
      const retryable = uncertain && error.stocktake_retry_state === 'retryable'
      this.setData({ errorMessage: error.message || '正式盘点写入失败', pendingRetryable: retryable, pendingMessage: uncertain ? (retryable ? '原写意图仍可复用同一路径、正文、幂等键和请求 ID 重试。' : '写结果不确定且精确回读不能确认；已停止其他写动作，请人工核验原坐标。') : '' })
    } finally { this.setData({ busy: false }) }
  },
  retryPending() {
    const intent = this._intentRegistry.current()
    if (intent) this.run({})
  },
  startTask() { this.run({ action: 'start', taskId: this._detail.task_id, expectedTaskVersion: this._detail.version, body: { expected_version: this._detail.version } }) },

  openTerminalConfirm(event) {
    if (!this._access || !this._detail || this.data.busy || this.data.pendingMessage) return
    const action = String(event.currentTarget.dataset.action || '')
    const permission = action === 'reconcile' ? this._access.can_reconcile : action === 'close' ? this._access.can_close : false
    if (!permission || !this._detail.allowed_actions.includes(action)) {
      this.setData({ errorMessage: '当前总部管理员权限与详情 allowed_action 未同时授权该动作', terminalConfirm: '' })
      return
    }
    this.setData({ errorMessage: '', terminalConfirm: action })
  },
  cancelTerminalConfirm() {
    if (!this.data.busy) this.setData({ terminalConfirm: '' })
  },
  confirmTerminalAction() {
    const action = this.data.terminalConfirm
    if (!this._access || !this._detail || this.data.busy || this.data.pendingMessage || !['reconcile', 'close'].includes(action)) return
    const allowed = this._detail.allowed_actions.includes(action)
    const permission = action === 'reconcile' ? this._access.can_reconcile : this._access.can_close
    const axes = this._detail.state_axes
    const commonReady = this._detail.status === 'posted' && this._detail.posted_at !== null && this._detail.closed_at === null && axes.posting_status === 'recorded' && axes.closure_status === 'open'
    const actionReady = action === 'reconcile' ? commonReady : commonReady && axes.reconciliation_status === 'recorded' && this._detail.close_control && this._detail.close_control.latest_reconciliation
    if (!permission || !allowed || !actionReady) {
      this.setData({ errorMessage: '对账或关闭前置事实、权限或 allowed_action 已变化，已停止写入', terminalConfirm: '' })
      return
    }
    const version = this._detail.version
    this.setData({ terminalConfirm: '' })
    this.run({ action, taskId: this._detail.task_id, expectedTaskVersion: version, body: { expected_task_version: version } })
  },

  chooseScope(event) {
    const scopeId = String(event.currentTarget.dataset.id || '')
    const scope = this._detail.scopes.find((row) => row.scope_id === scopeId)
    if (!scope) return
    const action = scope.allowed_actions.includes('submit_initial_count') ? 'submit_initial_count' : scope.allowed_actions.includes('submit_recount_count') ? 'submit_recount_count' : ''
    if (!action || !this._access.can_count) return
    const round = currentRound(this._detail)
    if (!round) return
    ensureEvidenceUploads(this)
    this._evidenceUploads.bind(`${this._uploadIdentity}:${this._detail.task_id}:${this._detail.version}:${round.round_id}:${scopeId}:${action}`)
    this.setData({ selectedScopeId: scopeId, countAction: action, accountDrafts: scope.snapshot_accounts.map((account) => Object.assign({}, account, { counted_qty: '', serial_ids_text: '' })), observations: [] })
  },
  bindAccountQty(event) { const index = Number(event.currentTarget.dataset.index); const rows = this.data.accountDrafts.slice(); rows[index].counted_qty = event.detail.value; this.setData({ accountDrafts: rows, hasAccountInput: rows.some((row) => String(row.counted_qty || '').trim()) }) },
  bindAccountSerials(event) { const index = Number(event.currentTarget.dataset.index); const rows = this.data.accountDrafts.slice(); rows[index].serial_ids_text = event.detail.value; this.setData({ accountDrafts: rows }) },
  bindMaterial(event) { this.setData({ materialIdentifier: event.detail.value }) },
  bindQuantity(event) { this.setData({ quantity: event.detail.value }) },
  bindLot(event) { this.setData({ lotNo: event.detail.value }) },
  bindSerial(event) { this.setData({ serialNo: event.detail.value }) },
  bindRemark(event) { this.setData({ remark: event.detail.value }) },
  changeCondition(event) { const index = Number(event.detail.value); this.setData({ conditionIndex: index, conditionCode: this.data.conditionOptions[index].value }) },
  changeAvailability(event) { const index = Number(event.detail.value); this.setData({ availabilityIndex: index, availabilityBucket: this.data.availabilityOptions[index].value }) },
  scanMaterial() { wx.scanCode({ onlyFromCamera: true, success: (result) => this.setData({ materialIdentifier: String(result.result || '').trim() }) }) },
  scanSerial() { wx.scanCode({ onlyFromCamera: true, success: (result) => this.setData({ serialNo: String(result.result || '').trim() }) }) },
  addObservation() {
    try {
      const identifier = this.data.materialIdentifier.trim()
      if (!identifier) throw new Error('请填写现场物料标识')
      const serial = this.data.serialNo.trim()
      const row = { material_id: null, material_identifier_raw: identifier, material_identifier_type: 'sku_code', condition_code: this.data.conditionCode, availability_bucket: this.data.availabilityBucket, counted_qty: fixedQuantityText(this.data.quantity, true), lot_id: null, lot_no_raw: this.data.lotNo.trim() || null, serial_id: null, serial_no_raw: serial || null, serial_identifier_type: serial ? 'serial_no' : null, count_method: 'manual', reason_code: null, remark: this.data.remark.trim() }
      this.setData({ observations: this.data.observations.concat([row]), materialIdentifier: '', quantity: '', lotNo: '', serialNo: '', remark: '' })
    } catch (error) { wx.showToast({ title: error.message || '观察行无效', icon: 'none' }) }
  },
  removeObservation(event) { const index = Number(event.currentTarget.dataset.index); this.setData({ observations: this.data.observations.filter((_, rowIndex) => rowIndex !== index) }) },
  async chooseCountEvidence() {
    if (!this.data.selectedScopeId || this.data.busy || this.data.pendingMessage) return
    ensureEvidenceUploads(this)
    try {
      await this._evidenceUploads.select()
    } catch (error) {
      wx.showToast({ title: error.message || '盘点证据上传失败', icon: 'none' })
    }
  },
  async retryCountEvidence(event) {
    if (!this.data.selectedScopeId || this.data.busy || this.data.pendingMessage) return
    ensureEvidenceUploads(this)
    try {
      await this._evidenceUploads.retry(String(event.currentTarget.dataset.key || ''))
    } catch (error) {
      wx.showToast({ title: error.message || '盘点证据重试失败', icon: 'none' })
    }
  },
  removeCountEvidence(event) {
    if (!this.data.selectedScopeId || this.data.busy || this.data.pendingMessage) return
    ensureEvidenceUploads(this)
    try {
      this._evidenceUploads.remove(String(event.currentTarget.dataset.key || ''))
    } catch (error) {
      wx.showToast({ title: error.message || '盘点证据不能移除', icon: 'none' })
    }
  },
  submitCount(event) {
    try {
      const zero = event.currentTarget.dataset.zero === true || event.currentTarget.dataset.zero === 'true'
      const scope = this._detail.scopes.find((row) => row.scope_id === this.data.selectedScopeId)
      const round = currentRound(this._detail)
      if (!scope || !round) throw new Error('当前计数范围或轮次已失效')
      const countMode = this._detail.blind_count ? 'blind' : 'open'
      const accounts = zero ? [] : this.data.accountDrafts.map((item) => {
        if (!item.counted_qty) throw new Error('明盘必须填写每个账面账户的实盘数量')
        return { stock_account_id: item.stock_account_id, counted_qty: fixedQuantityText(item.counted_qty), count_method: 'manual', serial_ids: idList(item.serial_ids_text, 'serial_id'), book_qty_confirmation: countMode === 'open' ? item.book_qty : null, reason_code: null, remark: '' }
      })
      const action = this.data.countAction
      if (!['submit_initial_count', 'submit_recount_count'].includes(action)) {
        throw new Error('盘点证据只能绑定初盘或复盘计数意图')
      }
      const evidenceFiles = availableEvidenceFiles(this)
      const input = { action, taskId: this._detail.task_id, roundId: round.round_id, scopeId: scope.scope_id, expectedTaskVersion: this._detail.version, body: { count_mode: countMode, account_counts: accounts, physical_observations: zero ? [] : this.data.observations, evidence_file_ids: evidenceFiles.map((file) => file.file_id), zero_confirmed: zero } }
      const signature = countIntentSignature(input)
      if (!this._evidenceClaims) this._evidenceClaims = new Map()
      for (const file of evidenceFiles) {
        for (const key of [`sha256:${file.sha256}`, `file_id:${file.file_id}`]) {
          const claimed = this._evidenceClaims.get(key)
          if (claimed && claimed !== signature) {
            throw new Error('同一盘点证据只能用于一个范围计数写意图')
          }
        }
      }
      for (const file of evidenceFiles) {
        this._evidenceClaims.set(`sha256:${file.sha256}`, signature)
        this._evidenceClaims.set(`file_id:${file.file_id}`, signature)
      }
      this.run(input)
    } catch (error) { wx.showToast({ title: error.message || '计数内容无效', icon: 'none' }) }
  },

  generateDifference(event) {
    const round = currentRound(this._detail)
    const action = event.currentTarget.dataset.action
    if (round) this.run({ action, taskId: this._detail.task_id, roundId: round.round_id, expectedTaskVersion: this._detail.version, body: { expected_task_version: this._detail.version } })
  },
  bindReviewComment(event) { this.setData({ reviewComment: event.detail.value }) },
  submitReview(event) {
    const stage = event.currentTarget.dataset.stage
    const decision = event.currentTarget.dataset.decision
    const round = currentRound(this._detail)
    if (!round) return
    const comment = this.data.reviewComment.trim()
    if (decision !== 'approve' && !comment) { wx.showToast({ title: '要求复盘或驳回必须填写意见', icon: 'none' }); return }
    const action = stage === 'region' ? 'review_region' : 'review_headquarters'
    this.run({ action, taskId: this._detail.task_id, roundId: round.round_id, expectedTaskVersion: this._detail.version, body: { expected_task_version: this._detail.version, decision, items: round.visible_differences.map((difference) => ({ difference_id: difference.difference_id, decision: decision === 'approve' ? (difference.posting_blocked_by_pending_verification ? 'pending_verification' : 'accept_for_posting') : decision === 'recount' ? 'recount' : 'reject', comment })), comment } })
  },
  async loadAllAssignees(regionOrgId, locationId) {
    const rows = []
    const seen = new Set()
    let cursor = null
    for (let pageNo = 0; pageNo < 100; pageNo += 1) {
      const page = await formalStocktakeAdapter.listAssignees(regionOrgId, locationId, cursor)
      if (page.items.some((item) => rows.some((known) => known.person_id === item.person_id || known.assignee_user_id === item.assignee_user_id))) throw new Error('盘点人员目录跨页重复，已失败关闭')
      rows.push(...page.items)
      if (!page.next_after_person_id) return rows
      if (seen.has(page.next_after_person_id)) throw new Error('盘点人员目录分页游标重复，已失败关闭')
      seen.add(page.next_after_person_id); cursor = page.next_after_person_id
    }
    throw new Error('盘点人员目录分页超出安全上限，已失败关闭')
  },
  updateRecountReady(scopes = this.data.detail ? this.data.detail.scopes : [], selections = this.data.recountSelections, reason = this.data.recountReason) {
    const ready = Boolean(selections.length && String(reason || '').trim() && selections.every((scopeId) => {
      const scope = scopes.find((row) => row.scope_id === scopeId)
      return scope && scope.recountOptions.some((option) => option.assignee_user_id === scope.recountAssigneeUserId)
    }))
    this.setData({ recountReady: ready })
  },
  async toggleRecountScope(event) {
    const scopeId = String(event.currentTarget.dataset.id || '')
    const selected = this.data.recountSelections.includes(scopeId)
    if (selected) {
      const selections = this.data.recountSelections.filter((id) => id !== scopeId)
      const scopes = this.data.detail.scopes.map((scope) => scope.scope_id === scopeId ? Object.assign({}, scope, { recountSelected: false, recountOptions: [], recountAssigneeIndex: 0, recountAssigneeUserId: '' }) : scope)
      this.setData({ recountSelections: selections, 'detail.scopes': scopes })
      this.updateRecountReady(scopes, selections)
      return
    }
    const sourceScope = this._detail.scopes.find((scope) => scope.scope_id === scopeId)
    if (!sourceScope) return
    this.setData({ recountLoadingScopeId: scopeId, errorMessage: '' })
    try {
      const options = await this.loadAllAssignees(this._detail.region_org_id, sourceScope.location_id)
      if (!options.length) throw new Error('该范围没有正式可分配盘点人员')
      const viewOptions = options.map((option) => Object.assign({}, option, { label: `${option.employee_no} · ${option.name}` }))
      const selections = this.data.recountSelections.concat([scopeId])
      const scopes = this.data.detail.scopes.map((scope) => scope.scope_id === scopeId ? Object.assign({}, scope, { recountSelected: true, recountOptions: viewOptions, recountAssigneeIndex: 0, recountAssigneeUserId: options[0].assignee_user_id }) : scope)
      this.setData({ recountSelections: selections, 'detail.scopes': scopes })
      this.updateRecountReady(scopes, selections)
    } catch (error) {
      this.setData({ errorMessage: error.message || '复盘人员目录读取失败' })
    } finally { this.setData({ recountLoadingScopeId: '' }) }
  },
  changeRecountAssignee(event) {
    const scopeId = String(event.currentTarget.dataset.id || '')
    const index = Number(event.detail.value)
    const scopes = this.data.detail.scopes.map((scope) => {
      if (scope.scope_id !== scopeId || !scope.recountOptions[index]) return scope
      return Object.assign({}, scope, { recountAssigneeIndex: index, recountAssigneeUserId: scope.recountOptions[index].assignee_user_id })
    })
    this.setData({ 'detail.scopes': scopes })
    this.updateRecountReady(scopes)
  },
  bindRecountReason(event) { const reason = event.detail.value; this.setData({ recountReason: reason }); this.updateRecountReady(undefined, undefined, reason) },
  submitRecount() {
    const round = currentRound(this._detail)
    if (!round || !this.data.recountReady) return
    try {
      const assignments = this.data.recountSelections.map((scopeId) => {
        const scope = this.data.detail.scopes.find((row) => row.scope_id === scopeId)
        const option = scope && scope.recountOptions.find((row) => row.assignee_user_id === scope.recountAssigneeUserId)
        if (!option) throw new Error('复盘人员选择已失效，请重新选择')
        return { scope_id: scopeId, assignee_user_id: option.assignee_user_id }
      })
      this.run({ action: 'open_recount', taskId: this._detail.task_id, roundId: round.round_id, expectedTaskVersion: this._detail.version, body: { expected_task_version: this._detail.version, assignments, reason: this.data.recountReason.trim() } })
    } catch (error) { this.setData({ errorMessage: error.message || '复盘人员选择无效' }) }
  }
})
