const session = require('../../utils/session')
const { formalStocktakeAdapter, createFormalStocktakeIntentRegistry } = require('../../utils/formal-stocktake-adapter')
const { getFormalStocktakeCountRecoveryStore } = require('../../utils/formal-stocktake-count-recovery-store')
const { createFormalStocktakeCountRecoveryAdapterFromFormalAdapter, recoverFormalStocktakeCount } = require('../../utils/formal-stocktake-count-recovery')
const { submitDurableFormalStocktakeCount, FormalStocktakeCountSubmissionPendingError } = require('../../utils/formal-stocktake-count-submission')
const { getFormalStocktakeReviewRecoveryStore } = require('../../utils/formal-stocktake-review-recovery-store')
const { createFormalStocktakeReviewRecoveryAdapterFromFormalAdapter, recoverFormalStocktakeReview } = require('../../utils/formal-stocktake-review-recovery')
const { submitDurableFormalStocktakeReview, FormalStocktakeReviewSubmissionPendingError } = require('../../utils/formal-stocktake-review-submission')
const { getFormalStocktakePostRecoveryStore } = require('../../utils/formal-stocktake-post-recovery-store')
const { createFormalStocktakePostRecoveryAdapterFromFormalAdapter, recoverFormalStocktakePost, sealFormalStocktakePost, submitDurableFormalStocktakePost, FormalStocktakePostSubmissionPendingError } = require('../../utils/formal-stocktake-post-recovery')
const { fixedQuantityText, formalStocktakeLabels } = require('../../utils/formal-stocktake-contract')
const formalFileUpload = require('../../utils/formal-file-upload')
const { accessIdentity } = require('../../utils/formal-operational-stocktake-pagination')

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const NONZERO_UUID = /^(?!00000000-0000-0000-0000-000000000000$)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

function hasDurableCountCapability(adapter) {
  return Boolean(adapter && adapter.countRecoveryMode === 'durable'
    && typeof adapter.countCommandStatus === 'function'
    && typeof adapter.loadIdentityNoReplay === 'function'
    && typeof adapter.loadAccessNoReplay === 'function'
    && typeof adapter.detailNoReplay === 'function'
    && typeof adapter.execute === 'function')
}

function isLegacyCountCompatibilityAdapter(adapter) {
  return Boolean(adapter && adapter.countRecoveryMode === 'legacy-test')
}

function hasDurableReviewCapability(adapter) {
  return Boolean(adapter
    && typeof adapter.loadIdentityNoReplay === 'function'
    && typeof adapter.loadAccessNoReplay === 'function'
    && typeof adapter.detailNoReplay === 'function'
    && typeof adapter.reviewCommandStatus === 'function'
    && typeof adapter.execute === 'function')
}

function isLegacyReviewCompatibilityAdapter(adapter) {
  return Boolean(adapter && adapter.countRecoveryMode === 'legacy-test')
}

function hasDurablePostCapability(adapter) {
  return Boolean(adapter && typeof adapter.loadIdentityNoReplay === 'function'
    && typeof adapter.loadAccessNoReplay === 'function' && typeof adapter.detailNoReplay === 'function'
    && typeof adapter.postingCommandStatus === 'function' && typeof adapter.execute === 'function')
}

function postReady(detail, access) {
  if (!detail || !access || access.can_read !== true || access.can_post !== true
    || !['full', 'sample', 'ad_hoc', 'personal', 'termination'].includes(detail.task_type)
    || detail.status !== 'approved' || !detail.allowed_actions.includes('post')
    || detail.posted_at !== null || detail.closed_at !== null) return false
  const axes = detail.state_axes
  const round = currentRound(detail)
  return Boolean(round && axes.region_review_status === 'approve' && axes.headquarters_review_status === 'approve'
    && axes.posting_status === 'not_posted' && axes.closure_status === 'open'
    && round.region_review && round.region_review.decision === 'approve'
    && round.headquarters_review && round.headquarters_review.decision === 'approve')
}

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
    canPost: postReady(detail, access) && hasDurablePostCapability(formalStocktakeAdapter),
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
    countRecoveryBlocked: false,
    countRecoveryCanCheck: false,
    reviewRecoveryBlocked: false,
    reviewRecoveryCanCheck: false,
    postRecoveryBlocked: false,
    postRecoveryCanCheck: false,
    postRecoveryCanSeal: false,
    countRecoveryMessage: '',
    reviewRecoveryMessage: '',
    postRecoveryMessage: '',
    postConfirm: false,
    terminalConfirm: '',
    selectedScopeId: '',
    countAction: '',
    accountDrafts: [],
    observations: [],
    materialIdentifier: '',
    materialIdentifierType: 'sku_code',
    materialScanned: false,
    quantity: '',
    conditionCode: 'new',
    availabilityBucket: 'available',
    lotNo: '',
    serialNo: '',
    serialIdentifierType: 'serial_no',
    serialScanned: false,
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
    this._hidden = false
    this._taskId = String(options.task_id || '').toLowerCase()
    this._countRecoveryContext = ''
    this._reviewRecoveryContext = ''
    this._intentRegistry = createFormalStocktakeIntentRegistry()
    this._countRecoveryStore = getFormalStocktakeCountRecoveryStore()
    this._reviewRecoveryStore = getFormalStocktakeReviewRecoveryStore()
    this._postRecoveryStore = getFormalStocktakePostRecoveryStore()
    this._evidenceClaims = new Map()
    this._uploadIdentity = ''
    ensureEvidenceUploads(this)
  },
  onShow() {
    this._hidden = false
    if (!session.ensureLogin()) return
    if (this._writeLease) {
      this.setData({ loading: false, accessAllowed: false, detail: null, accessMessage: '原盘点请求仍在处理中，请等待结束后下拉刷新；原请求坐标继续保留。' })
      return
    }
    this.load()
  },
  onHide() {
    this._hidden = true
    this._postPageLease = null
    this._postConfirmation = null
    this._scanGeneration = (this._scanGeneration || 0) + 1
    this._loadGeneration = (this._loadGeneration || 0) + 1
    // Hide invalidates the active evidence binding as well as scan/load
    // generations.  The upload controller's generation gate then discards
    // late prepare/complete callbacks instead of repopulating a new page
    // lifecycle with evidence chosen under the old authorization/detail.
    if (this._evidenceUploads) this._evidenceUploads.clear()
    if (this._evidenceClaims) this._evidenceClaims.clear()
    this._uploadIdentity = ''
    this.setData({ busy: false, loading: false, accessAllowed: false, detail: null, accessMessage: '返回后须重新校验正式身份与盘点权限', terminalConfirm: '', postConfirm: false, selectedScopeId: '', countAction: '', accountDrafts: [], observations: [], evidenceUploadFiles: [], evidenceUploadBlocking: false, evidenceUploadCanChoose: true, hasAccountInput: false })
    this._countRecoveryContext = ''
    this._reviewRecoveryContext = ''
    this.setData({ countRecoveryCanCheck: false, reviewRecoveryCanCheck: false, postRecoveryCanCheck: false, postRecoveryCanSeal: false })
    if (this._intentRegistry.current()) this.setData({ pendingMessage: '原盘点写请求结果待核实，已停止新操作；刷新不代表原请求未执行。', pendingRetryable: false })
  },
  onPullDownRefresh() { this.load().finally(() => wx.stopPullDownRefresh()) },
  onUnload() {
    this.onHide()
    this._loadGeneration = (this._loadGeneration || 0) + 1
    if (this._evidenceUploads) this._evidenceUploads.clear()
    if (this._evidenceClaims) this._evidenceClaims.clear()
    this._access = null
    this._detail = null
    this._countRecoveryContext = ''
    this._reviewRecoveryContext = ''
    this._uploadIdentity = ''
  },

  async load() {
    if (this._hidden || this._writeLease) return
    this._scanGeneration = (this._scanGeneration || 0) + 1
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
      this.refreshRecoveryBarriers()
      if (this._intentRegistry.current() && !this.recoveryBlocked()) this.setData({ pendingMessage: '原盘点写请求结果待核实，已停止新操作；请按原坐标人工核验。', pendingRetryable: false })
      this.setData({ accessAllowed: true, accessMessage: `正式盘点权限已验证 · v${access.authorization_version}`, detail: viewDetail(detail, access), terminalConfirm: '', postConfirm: false })
    } catch (error) {
      if (generation !== this._loadGeneration) return
      this._access = null; this._detail = null
      this._uploadIdentity = ''
      this._countRecoveryContext = ''
      if (this._evidenceUploads) this._evidenceUploads.clear()
      if (this._evidenceClaims) this._evidenceClaims.clear()
      this.setData({ accessAllowed: false, accessMessage: '身份、授权或正式响应未通过校验，已失败关闭', detail: null, terminalConfirm: '', postConfirm: false, errorMessage: error.message || '正式盘点详情读取失败', selectedScopeId: '', countAction: '', accountDrafts: [], observations: [], evidenceUploadFiles: [], evidenceUploadBlocking: false, evidenceUploadCanChoose: true, hasAccountInput: false, countRecoveryBlocked: false, countRecoveryCanCheck: false, reviewRecoveryBlocked: false, reviewRecoveryCanCheck: false })
      // A failed fresh read must not erase a durable count marker. Re-evaluate
      // it after dropping the in-memory identity so recovery remains the only
      // path that can release the barrier.
      this.refreshRecoveryBarriers()
    } finally { if (generation === this._loadGeneration) this.setData({ loading: false }) }
  },

  recoveryBlocked() {
    return this.data.countRecoveryBlocked || this.data.reviewRecoveryBlocked || this.data.postRecoveryBlocked
  },
  refreshRecoveryMessages() {
    const messages = ['count', 'review', 'post'].filter((axis) => this.data[`${axis}RecoveryBlocked`])
      .map((axis) => this.data[`${axis}RecoveryMessage`]).filter(Boolean)
    const hasIntent = Boolean(this._intentRegistry && this._intentRegistry.current && this._intentRegistry.current())
    this.setData({ pendingMessage: messages.length ? messages.join('\n') : hasIntent ? this.data.pendingMessage : '', pendingRetryable: messages.length ? false : this.data.pendingRetryable })
  },
  refreshRecoveryBarriers() {
    this.refreshCountRecovery()
    this.refreshReviewRecovery()
    this.refreshPostRecovery()
  },
  refreshCountRecovery() { this.refreshRecoveryAxis('count') },
  refreshReviewRecovery() { this.refreshRecoveryAxis('review') },
  refreshPostRecovery() { this.refreshRecoveryAxis('post') },
  refreshRecoveryAxis(axis) {
    const names = { count: '日常盘点范围计数', review: '日常盘点复核', post: '日常盘点过账' }
    const name = names[axis]
    const store = this[`_${axis}RecoveryStore`]
    const canRecover = axis === 'count' ? hasDurableCountCapability(formalStocktakeAdapter)
      : axis === 'review' ? hasDurableReviewCapability(formalStocktakeAdapter) : hasDurablePostCapability(formalStocktakeAdapter)
    const legacy = formalStocktakeAdapter && formalStocktakeAdapter.countRecoveryMode === 'legacy-test'
    const set = (blocked, canCheck, message = '') => {
      const patch = { [`${axis}RecoveryBlocked`]: blocked, [`${axis}RecoveryCanCheck`]: canCheck, [`${axis}RecoveryMessage`]: message }
      if (axis === 'post') patch.postRecoveryCanSeal = false
      this.setData(patch)
      this.refreshRecoveryMessages()
    }
    if (!UUID.test(this._taskId || '')) { set(true, false, '任务恢复坐标无效，已停止所有业务写入。'); return }
    let pending
    try { pending = store && typeof store.readPending === 'function' ? store.readPending(this._taskId) : { kind: 'unavailable' } }
    catch (_) { set(true, false, `${name}恢复记录无法读取，已停止所有业务写入。`); return }
    // Only explicitly marked test adapters may omit a durable capability.
    // A positively observed marker always blocks, including legacy adapters.
    if (!canRecover && legacy && ['missing', 'unavailable'].includes(pending.kind)) { set(false, false); return }
    if (!canRecover) { set(true, false, `正式盘点${axis === 'count' ? '' : axis === 'review' ? '复核' : '过账'}持久恢复能力未完整加载，已停止所有业务写入，待只读核验。`); return }
    if (pending.kind === 'valid' && Array.isArray(pending.values) && pending.values.length) {
      set(true, Boolean(this._access) && !this._hidden, `原${name}结果待只读核验；已停止其他写操作，未发送任何新请求。`)
      if (axis === 'post' && pending.values.length === 1) {
        const sentinel = pending.values[0]
        const canSeal = Boolean(this._access && this._access.can_read === true && this._access.can_post === true
          && typeof formalStocktakeAdapter.sealPostingCommand === 'function'
          && sentinel.task_id === this._taskId
          && sentinel.actor_person_id === this._access.person_id
          && sentinel.actor_authorization_version === this._access.authorization_version
          && !this._hidden)
        this.setData({ postRecoveryCanSeal: canSeal })
      }
    } else if (pending.kind === 'missing') set(false, false)
    else set(true, false, `${name}恢复记录无法确认，已停止所有业务写入。`)
  },

  async run(input, retryIntent = null) {
    if (!this._access || !this._detail || this.data.busy || this._writeLease || this._hidden || this.data.loading) return
    this.refreshRecoveryBarriers()
    // A durable count marker blocks every business write, not only another
    // count.  UI disabled flags are advisory; this method is the final
    // fail-closed boundary for start, differences, reviews and terminal work.
    if (this.recoveryBlocked()) {
      this.setData({ pendingRetryable: false })
      return
    }
    const existing = this._intentRegistry.current()
    if (input.action === 'post' || (retryIntent && retryIntent.action === 'post') || (existing && existing.action === 'post')) {
      this.setData({ pendingRetryable: false, errorMessage: '过账只能通过独立确认与持久恢复入口执行，禁止通用写入或重试。' })
      return
    }
    if (existing && (retryIntent !== existing || !this.data.pendingRetryable)) {
      this.setData({ pendingMessage: '原盘点写请求结果待核实，禁止以新操作重发原请求。' })
      return
    }
    if (!existing && retryIntent) return
    const lease = {}
    this._writeLease = lease
    const generation = this._loadGeneration
    const access = this._access
    const current = () => !this._hidden && this._writeLease === lease && this._loadGeneration === generation && this._access === access
    let intent
    const durableCount = (input.action === 'submit_initial_count' || input.action === 'submit_recount_count') && hasDurableCountCapability(formalStocktakeAdapter)
    const durableReview = (input.action === 'review_region' || input.action === 'review_headquarters') && hasDurableReviewCapability(formalStocktakeAdapter)
    const compatibilityAdapter = isLegacyCountCompatibilityAdapter(formalStocktakeAdapter) || isLegacyReviewCompatibilityAdapter(formalStocktakeAdapter)
    try {
      intent = existing || this._intentRegistry.begin(input)
      if ((input.action === 'review_region' || input.action === 'review_headquarters') && !durableReview && !compatibilityAdapter) throw new Error('当前小程序不支持安全的复核持久恢复，已停止复核')
      if ((input.action === 'submit_initial_count' || input.action === 'submit_recount_count') && !durableCount && !compatibilityAdapter) throw new Error('当前小程序不支持安全的计数持久恢复，已停止计数')
      this.setData({ busy: true, errorMessage: '', pendingMessage: '', pendingRetryable: false, terminalConfirm: '' })
      const completed = durableCount
        ? await submitDurableFormalStocktakeCount({
          intent,
          expectedIdentity: this._access,
          roundNo: input.roundNo,
          adapter: formalStocktakeAdapter,
          store: this._countRecoveryStore,
          canCommit: current
        })
        : durableReview
          ? await submitDurableFormalStocktakeReview({
            intent,
            expectedIdentity: this._access,
            adapter: formalStocktakeAdapter,
            store: this._reviewRecoveryStore,
            canCommit: current
          })
          : await formalStocktakeAdapter.execute(intent)
      // A successful response is a fact about the original write intent even
      // when the page became hidden while the response was in flight.  Clear
      // the matching in-memory intent before checking page liveness; otherwise
      // a later onShow can resurrect a generic pending blocker after the
      // durable count marker has already been cleared.  Do not complete an
      // intent after a newer lease owner has taken over the page.
      if (this._writeLease === lease && this._intentRegistry.current() === intent && (durableCount || current())) this._intentRegistry.complete(intent)
      if (!current()) return
      this._detail = completed.detail
      if (this._evidenceUploads) this._evidenceUploads.clear()
      this.setData({ detail: viewDetail(completed.detail, this._access), selectedScopeId: '', countAction: '', accountDrafts: [], observations: [], pendingMessage: '', pendingRetryable: false, recountSelections: [], recountReason: '', recountReady: false })
    } catch (error) {
      if (!current()) return
      const uncertain = error && error.write_result_uncertain === true
      if (durableCount && error instanceof FormalStocktakeCountSubmissionPendingError) {
        this.refreshCountRecovery()
        this.setData({ pendingRetryable: false, pendingMessage: '原日常盘点范围计数已进入持久待核验状态；只允许查询原追踪坐标，禁止重新提交。', errorMessage: error.message })
        return
      }
      if (durableReview && error instanceof FormalStocktakeReviewSubmissionPendingError) {
        this.refreshReviewRecovery()
        this.setData({ pendingRetryable: false, pendingMessage: '原日常盘点复核结果已进入持久待核验状态；只允许查询原追踪坐标，禁止重新提交。', errorMessage: error.message })
        return
      }
      if (intent && !uncertain) this._intentRegistry.complete(intent)
      const retryable = uncertain && error.stocktake_retry_state === 'retryable'
      this.setData({ errorMessage: error.message || '正式盘点写入失败', pendingRetryable: retryable, pendingMessage: uncertain ? (retryable ? '原写意图仍可复用同一路径、正文、幂等键和请求 ID 重试。' : '写结果不确定且精确回读不能确认；已停止其他写动作，请人工核验原坐标。') : '' })
    } finally {
      if (current()) this.setData({ busy: false })
      if (this._writeLease === lease) this._writeLease = null
    }
  },
  async recoverCount() {
    if (!hasDurableCountCapability(formalStocktakeAdapter) || !this._countRecoveryStore || this.data.busy || this._hidden || !this._access) {
      this.refreshCountRecovery()
      return
    }
    const pending = this._countRecoveryStore.readPending(this._taskId)
    if (pending.kind !== 'valid' || !pending.values || !pending.values.length) { this.refreshCountRecovery(); return }
    const sentinel = pending.values[0]
    const lease = {}
    this._writeLease = lease
    const generation = this._loadGeneration
    const live = () => !this._hidden && this._writeLease === lease && this._loadGeneration === generation
    this.setData({ busy: true, errorMessage: '' })
    try {
      const result = await this._countRecoveryStore.withScopeLease(sentinel, (scopeLease) => recoverFormalStocktakeCount(scopeLease, sentinel, createFormalStocktakeCountRecoveryAdapterFromFormalAdapter(this._access, formalStocktakeAdapter), live))
      const currentIntent = this._intentRegistry.current()
      const expectedAction = sentinel.operation === 'initial_count' ? 'submit_initial_count' : 'submit_recount_count'
      const intentMatches = Boolean(currentIntent
        && currentIntent.action === expectedAction
        && currentIntent.taskId === sentinel.task_id
        && currentIntent.roundId === sentinel.round_id
        && currentIntent.scopeId === sentinel.scope_id
        && currentIntent.headers
        && currentIntent.headers['X-Request-ID'] === sentinel.trace_request_id)
      // The recovery proof already cleared the durable marker.  Complete the
      // matching in-memory intent while this lease still owns the page, even
      // if onHide/onUnload advanced the load generation during the read-only
      // proof.  A newer lease owner is never touched.
      if (intentMatches && this._writeLease === lease) this._intentRegistry.complete(currentIntent)
      if (!live()) return
      this._detail = result.detail
      if (this._evidenceUploads) this._evidenceUploads.clear()
      if (this._evidenceClaims) this._evidenceClaims.clear()
      this.refreshCountRecovery()
      const registryBlocked = Boolean(this._intentRegistry.current())
      const pendingAfter = this._countRecoveryStore.readPending(this._taskId)
      const pendingMessage = registryBlocked
        ? '仍有另一笔盘点写请求待核实，已停止新操作。'
        : pendingAfter.kind === 'valid'
          ? '仍有其他日常盘点范围计数待只读核验；未发送任何新请求。'
          : ''
      const canCheckRemaining = !registryBlocked && pendingAfter.kind === 'valid'
        && hasDurableCountCapability(formalStocktakeAdapter)
      this.setData({
        detail: viewDetail(result.detail, this._access),
        selectedScopeId: '', countAction: '', accountDrafts: [], observations: [],
        evidenceUploadFiles: [], evidenceUploadBlocking: false, evidenceUploadCanChoose: true,
        hasAccountInput: false, recountSelections: [], recountReason: '', recountReady: false,
        pendingMessage, pendingRetryable: false,
        countRecoveryBlocked: registryBlocked || pendingAfter.kind !== 'missing',
        countRecoveryCanCheck: canCheckRemaining
      })
      this.refreshRecoveryMessages()
    } catch (error) {
      if (live()) { this.refreshCountRecovery(); this.setData({ errorMessage: error.message || '历史范围计数仍待核验' }) }
    } finally {
      if (this._writeLease === lease) this._writeLease = null
      if (!this._hidden) this.setData({ busy: false })
    }
  },
  async recoverReview() {
    if (!hasDurableReviewCapability(formalStocktakeAdapter) || !this._reviewRecoveryStore || this.data.busy || this._hidden || !this._access) {
      this.refreshReviewRecovery()
      return
    }
    const pending = this._reviewRecoveryStore.readPending(this._taskId)
    if (pending.kind !== 'valid' || !pending.values || !pending.values.length) { this.refreshReviewRecovery(); return }
    const sentinel = pending.values[0]
    const lease = {}
    this._writeLease = lease
    const generation = this._loadGeneration
    const live = () => !this._hidden && this._writeLease === lease && this._loadGeneration === generation
    this.setData({ busy: true, errorMessage: '' })
    try {
      const result = await this._reviewRecoveryStore.withTaskLease(sentinel, (reviewLease) => recoverFormalStocktakeReview(reviewLease, sentinel, createFormalStocktakeReviewRecoveryAdapterFromFormalAdapter(this._access, formalStocktakeAdapter), live))
      const currentIntent = this._intentRegistry.current()
      const expectedAction = sentinel.review_stage === 'region' ? 'review_region' : 'review_headquarters'
      const intentMatches = Boolean(currentIntent
        && currentIntent.action === expectedAction
        && currentIntent.taskId === sentinel.task_id
        && currentIntent.roundId === sentinel.round_id
        && currentIntent.expectedTaskVersion === sentinel.expected_task_version
        && currentIntent.headers
        && currentIntent.headers['X-Request-ID'] === sentinel.trace_request_id)
      if (intentMatches && this._writeLease === lease) this._intentRegistry.complete(currentIntent)
      if (!live()) return
      this._detail = result.detail
      this.refreshReviewRecovery()
      const pendingAfter = this._reviewRecoveryStore.readPending(this._taskId)
      const registryBlocked = Boolean(this._intentRegistry.current())
      this.setData({ detail: viewDetail(result.detail, this._access), pendingMessage: registryBlocked ? '仍有另一笔盘点写请求待核实，已停止新操作。' : pendingAfter.kind === 'valid' ? '仍有其他日常盘点复核待只读核验；未发送任何新请求。' : '', pendingRetryable: false, reviewRecoveryBlocked: registryBlocked || pendingAfter.kind !== 'missing', reviewRecoveryCanCheck: !registryBlocked && pendingAfter.kind === 'valid' })
      this.refreshRecoveryMessages()
    } catch (error) {
      if (live()) { this.refreshReviewRecovery(); this.setData({ errorMessage: error.message || '历史盘点复核仍待核验' }) }
    } finally {
      if (this._writeLease === lease) this._writeLease = null
      if (!this._hidden) this.setData({ busy: false })
    }
  },
  openPostConfirm() {
    if (this._hidden || this.data.busy || this.data.loading || this._writeLease) return
    this.refreshRecoveryBarriers()
    if (this.recoveryBlocked() || this._intentRegistry.current()) return
    if (!postReady(this._detail, this._access) || !hasDurablePostCapability(formalStocktakeAdapter)) {
      this.setData({ postConfirm: false, errorMessage: '当前权限、复核结果或任务状态不允许过账，请刷新核验。' })
      return
    }
    this._postConfirmation = Object.freeze({ taskId: this._detail.task_id, version: this._detail.version, identity: accessIdentity(this._access) })
    this.setData({ postConfirm: true, terminalConfirm: '', errorMessage: '' })
  },
  cancelPostConfirm() {
    if (this.data.busy) return
    this._postConfirmation = null
    this.setData({ postConfirm: false })
  },
  async confirmPost() {
    if (this._hidden || this.data.loading || this.data.busy || this._writeLease || !this.data.postConfirm) return
    this.refreshRecoveryBarriers()
    if (this.recoveryBlocked() || this._intentRegistry.current()) return
    const confirmation = this._postConfirmation
    if (!confirmation || !postReady(this._detail, this._access) || !hasDurablePostCapability(formalStocktakeAdapter)
      || confirmation.taskId !== this._taskId || confirmation.taskId !== this._detail.task_id
      || confirmation.version !== this._detail.version || confirmation.identity !== accessIdentity(this._access)) {
      this._postConfirmation = null
      this.setData({ postConfirm: false, errorMessage: '确认期间盘点版本、身份或权限已变化，请重新核验。' })
      return
    }
    const lease = {}
    this._writeLease = lease
    this._postPageLease = lease
    const generation = this._loadGeneration
    const access = this._access
    const current = () => !this._hidden && this._postPageLease === lease && this._writeLease === lease
      && this._loadGeneration === generation && this._access === access
      && accessIdentity(this._access) === confirmation.identity && this._taskId === confirmation.taskId
    let intent
    this.setData({ busy: true, postConfirm: false, errorMessage: '', pendingRetryable: false })
    this._postConfirmation = null
    try {
      intent = this._intentRegistry.begin({ action: 'post', taskId: confirmation.taskId, expectedTaskVersion: confirmation.version, body: { expected_task_version: confirmation.version } })
      const completed = await submitDurableFormalStocktakePost({ intent, expectedIdentity: access, adapter: formalStocktakeAdapter, store: this._postRecoveryStore, canCommit: current })
      if (!current()) return
      if (this._intentRegistry.current() === intent) this._intentRegistry.complete(intent)
      this._detail = completed.detail
      this.setData({ detail: viewDetail(completed.detail, access), pendingMessage: '', pendingRetryable: false })
      this.refreshRecoveryBarriers()
    } catch (error) {
      if (!current()) return
      const pending = this._postRecoveryStore.read(confirmation.taskId)
      if (intent && pending.kind === 'missing' && !(error instanceof FormalStocktakePostSubmissionPendingError)) this._intentRegistry.complete(intent)
      this.refreshRecoveryBarriers()
      this.setData({ errorMessage: error.message || '原盘点过账仍待只读核验', pendingRetryable: false })
    } finally {
      if (current()) this.setData({ busy: false })
      if (this._postPageLease === lease) this._postPageLease = null
      if (this._writeLease === lease) this._writeLease = null
    }
  },
  async recoverPost() {
    this.refreshPostRecovery()
    if (!hasDurablePostCapability(formalStocktakeAdapter) || !this._postRecoveryStore || this._hidden
      || this.data.loading || this.data.busy || this._writeLease || !this._access) return
    const pending = this._postRecoveryStore.readPending(this._taskId)
    if (pending.kind !== 'valid' || !pending.values || pending.values.length !== 1) return
    const sentinel = pending.values[0]
    const access = this._access
    const identity = accessIdentity(access)
    const generation = this._loadGeneration
    const lease = {}
    this._writeLease = lease
    this._postPageLease = lease
    const current = () => !this._hidden && this._postPageLease === lease && this._writeLease === lease
      && this._loadGeneration === generation && this._access === access && accessIdentity(this._access) === identity
      && this._taskId === sentinel.task_id
    this.setData({ busy: true, postConfirm: false, errorMessage: '', pendingRetryable: false })
    try {
      const recovered = await this._postRecoveryStore.withTaskLease(sentinel.task_id, (taskLease) => recoverFormalStocktakePost(taskLease, sentinel, createFormalStocktakePostRecoveryAdapterFromFormalAdapter(access, formalStocktakeAdapter), current))
      if (!current()) return
      const intent = this._intentRegistry.current()
      if (intent && intent.action === 'post' && intent.taskId === sentinel.task_id
        && intent.expectedTaskVersion === sentinel.expected_task_version && intent.headers
        && intent.headers['X-Request-ID'] === sentinel.trace_request_id) this._intentRegistry.complete(intent)
      this._detail = recovered.detail
      this.setData({ detail: viewDetail(recovered.detail, access), pendingMessage: '', pendingRetryable: false })
      this.refreshRecoveryBarriers()
    } catch (error) {
      if (current()) { this.refreshRecoveryBarriers(); this.setData({ errorMessage: error.message || '原盘点过账仍待只读核验' }) }
    } finally {
      if (current()) this.setData({ busy: false })
      if (this._postPageLease === lease) this._postPageLease = null
      if (this._writeLease === lease) this._writeLease = null
    }
  },
  openPostSealConfirm() {
    this.refreshPostRecovery()
    if (!this.data.postRecoveryCanSeal || this.data.busy || this._hidden || this._writeLease) return
    if (typeof wx === 'undefined' || typeof wx.showModal !== 'function') {
      this.setData({ errorMessage: '当前客户端不支持永久封存确认，恢复记录继续保留。' })
      return
    }
    wx.showModal({
      title: '确认永久封存',
      content: '这只确认原过账请求未执行，并永久封存该坐标。封存后不能再次过账，也不会改变任务库存或关闭状态。',
      confirmText: '确认封存',
      confirmColor: '#b83d16',
      success: (result) => { if (result && result.confirm) this.confirmPostSeal() }
    })
  },
  async confirmPostSeal() {
    this.refreshPostRecovery()
    if (!this.data.postRecoveryCanSeal || this.data.busy || this._hidden || this._writeLease || !this._access) return
    const pending = this._postRecoveryStore && this._postRecoveryStore.readPending(this._taskId)
    if (!pending || pending.kind !== 'valid' || !Array.isArray(pending.values) || pending.values.length !== 1) {
      this.refreshPostRecovery()
      return
    }
    const sentinel = pending.values[0]
    const access = this._access
    const identity = accessIdentity(access)
    const generation = this._loadGeneration
    const lease = {}
    this._writeLease = lease
    this._postPageLease = lease
    const current = () => !this._hidden && this._postPageLease === lease && this._writeLease === lease
      && this._loadGeneration === generation && this._access === access && accessIdentity(this._access) === identity
      && this._taskId === sentinel.task_id
    let refreshAfterSeal = false
    this.setData({ busy: true, errorMessage: '', pendingRetryable: false })
    try {
      await this._postRecoveryStore.withTaskLease(sentinel.task_id, (taskLease) => sealFormalStocktakePost(
        taskLease, sentinel, createFormalStocktakePostRecoveryAdapterFromFormalAdapter(access, formalStocktakeAdapter), current,
      ))
      if (!current()) return
      refreshAfterSeal = true
      this.setData({ postRecoveryBlocked: false, postRecoveryCanCheck: false, postRecoveryCanSeal: false, postRecoveryMessage: '', pendingMessage: '', pendingRetryable: false })
    } catch (error) {
      if (current()) {
        this.refreshPostRecovery()
        this.setData({ errorMessage: error.message || '永久封存未完成，恢复记录继续保留。' })
      }
    } finally {
      if (current()) this.setData({ busy: false })
      if (this._postPageLease === lease) this._postPageLease = null
      if (this._writeLease === lease) this._writeLease = null
      if (refreshAfterSeal && !this._hidden) this.load()
    }
  },
  retryPending() {
    const intent = this._intentRegistry.current()
    if (intent && intent.action === 'post') { this.setData({ pendingRetryable: false }); return this.recoverPost() }
    if (intent && this.data.pendingRetryable) return this.run({}, intent)
  },
  startTask() { this.run({ action: 'start', taskId: this._detail.task_id, expectedTaskVersion: this._detail.version, body: { expected_version: this._detail.version } }) },

  openTerminalConfirm(event) {
    if (!this._access || !this._detail || this.data.busy || this.data.pendingMessage || this.recoveryBlocked()) return
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
    if (!this._access || !this._detail || this.data.busy || this.data.pendingMessage || this.recoveryBlocked() || !['reconcile', 'close'].includes(action)) return
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
    if (!this._detail || !this._access || this.data.busy || this.data.pendingMessage || this.recoveryBlocked() || this._hidden) return
    const scopeId = String(event.currentTarget.dataset.id || '')
    const scope = this._detail.scopes.find((row) => row.scope_id === scopeId)
    if (!scope) return
    const action = scope.allowed_actions.includes('submit_initial_count') ? 'submit_initial_count' : scope.allowed_actions.includes('submit_recount_count') ? 'submit_recount_count' : ''
    if (!action || !this._access.can_count) return
    const round = currentRound(this._detail)
    if (!round) return
    this._scanGeneration = (this._scanGeneration || 0) + 1
    ensureEvidenceUploads(this)
    this._evidenceUploads.bind(`${this._uploadIdentity}:${this._detail.task_id}:${this._detail.version}:${round.round_id}:${scopeId}:${action}`)
    this.setData({ selectedScopeId: scopeId, countAction: action, accountDrafts: scope.snapshot_accounts.map((account) => Object.assign({}, account, { counted_qty: '', serial_ids_text: '' })), observations: [], materialIdentifier: '', materialIdentifierType: 'sku_code', materialScanned: false, serialNo: '', serialIdentifierType: 'serial_no', serialScanned: false, quantity: '', lotNo: '', remark: '' })
  },
  bindAccountQty(event) { if (this.recoveryBlocked()) return; const index = Number(event.currentTarget.dataset.index); const rows = this.data.accountDrafts.slice(); rows[index].counted_qty = event.detail.value; this.setData({ accountDrafts: rows, hasAccountInput: rows.some((row) => String(row.counted_qty || '').trim()) }) },
  bindAccountSerials(event) { if (this.recoveryBlocked()) return; const index = Number(event.currentTarget.dataset.index); const rows = this.data.accountDrafts.slice(); rows[index].serial_ids_text = event.detail.value; this.setData({ accountDrafts: rows }) },
  bindMaterial(event) { if (this.recoveryBlocked()) return; this._scanGeneration = (this._scanGeneration || 0) + 1; this.setData({ materialIdentifier: event.detail.value, materialIdentifierType: 'sku_code', materialScanned: false }) },
  bindQuantity(event) { if (this.recoveryBlocked()) return; this.setData({ quantity: event.detail.value }) },
  bindLot(event) { if (this.recoveryBlocked()) return; this.setData({ lotNo: event.detail.value }) },
  bindSerial(event) { if (this.recoveryBlocked()) return; this._scanGeneration = (this._scanGeneration || 0) + 1; this.setData({ serialNo: event.detail.value, serialIdentifierType: 'serial_no', serialScanned: false }) },
  bindRemark(event) { if (this.recoveryBlocked()) return; this.setData({ remark: event.detail.value }) },
  changeCondition(event) { if (this.recoveryBlocked()) return; const index = Number(event.detail.value); this.setData({ conditionIndex: index, conditionCode: this.data.conditionOptions[index].value }) },
  changeAvailability(event) { if (this.recoveryBlocked()) return; const index = Number(event.detail.value); this.setData({ availabilityIndex: index, availabilityBucket: this.data.availabilityOptions[index].value }) },
  scanCoordinate() {
    if (this._hidden || this._writeLease || this.data.loading || this.data.busy || this.data.pendingMessage || this.recoveryBlocked() || !this._access || !this._access.can_count || !this._detail) return null
    const scope = this._detail.scopes.find((item) => item.scope_id === this.data.selectedScopeId)
    const round = currentRound(this._detail)
    if (!scope || !round || !scope.allowed_actions.includes(this.data.countAction)) return null
    return `${accessIdentity(this._access)}:${this._detail.task_id}:${this._detail.version}:${round.round_id}:${scope.scope_id}:${this.data.countAction}`
  },
  scanMaterial() { this.scanIdentifier('material') },
  scanSerial() { this.scanIdentifier('serial') },
  scanIdentifier(kind) {
    const coordinate = this.scanCoordinate()
    if (!coordinate) return
    const generation = (this._scanGeneration || 0) + 1
    this._scanGeneration = generation
    const isCurrent = () => generation === this._scanGeneration && coordinate === this.scanCoordinate()
    wx.scanCode({ onlyFromCamera: true, success: async (result) => {
      if (!isCurrent()) return
      try {
        const value = String(result.result || '').trim()
        if (!value || value.length > (kind === 'material' ? 300 : 200)) throw new Error('扫描标识为空或超长，请重新扫描')
        const access = await formalStocktakeAdapter.loadAccess()
        if (!isCurrent()) return
        if (accessIdentity(access) !== accessIdentity(this._access) || !access.can_count) throw new Error('扫码期间身份或盘点权限已变化，请刷新')
        const detail = await formalStocktakeAdapter.detail(this._detail.task_id)
        if (!isCurrent()) return
        const confirmedAccess = await formalStocktakeAdapter.loadAccess()
        if (!isCurrent()) return
        if (accessIdentity(confirmedAccess) !== accessIdentity(access) || !confirmedAccess.can_count) throw new Error('扫码期间身份或盘点权限已变化，请刷新')
        const scope = detail.scopes.find((item) => item.scope_id === this.data.selectedScopeId)
        const round = currentRound(detail)
        if (detail.task_id !== this._detail.task_id || detail.version !== this._detail.version || !round || round.round_id !== currentRound(this._detail).round_id || !scope || !scope.allowed_actions.includes(this.data.countAction)) throw new Error('扫码期间盘点范围已变化，请刷新')
        // Camera codes can contain a SKU/SN or a registered QR code. Preserve
        // raw input; the server resolves exact matches and rejects ambiguity.
        this.setData(kind === 'material'
          ? { materialIdentifier: value, materialIdentifierType: 'unknown', materialScanned: true }
          : { serialNo: value, serialIdentifierType: 'unknown', serialScanned: true })
      } catch (error) {
        if (isCurrent()) this.setData({ errorMessage: error.message || '扫码核验失败，请刷新' })
      }
    } })
  },
  addObservation() {
    try {
      if (this.recoveryBlocked()) throw new Error('原日常盘点范围计数待核验，禁止修改计数草稿')
      if (!this.scanCoordinate()) throw new Error('当前盘点范围不可录入，请刷新')
      const identifier = this.data.materialIdentifier.trim()
      if (!identifier) throw new Error('请填写现场物料标识')
      const serial = this.data.serialNo.trim()
      const quantity = fixedQuantityText(this.data.quantity, true)
      if (serial && quantity !== '1.000') throw new Error('SN 必须逐件盘点，每条现场观察数量必须为 1')
      const row = { material_id: null, material_identifier_raw: identifier, material_identifier_type: this.data.materialIdentifierType, condition_code: this.data.conditionCode, availability_bucket: this.data.availabilityBucket, counted_qty: quantity, lot_id: null, lot_no_raw: this.data.lotNo.trim() || null, serial_id: null, serial_no_raw: serial || null, serial_identifier_type: serial ? this.data.serialIdentifierType : null, count_method: this.data.materialScanned || (serial && this.data.serialScanned) ? 'scan' : 'manual', reason_code: null, remark: this.data.remark.trim() }
      this._scanGeneration = (this._scanGeneration || 0) + 1
      this.setData({ observations: this.data.observations.concat([row]), materialIdentifier: '', materialIdentifierType: 'sku_code', materialScanned: false, quantity: '', lotNo: '', serialNo: '', serialIdentifierType: 'serial_no', serialScanned: false, remark: '' })
    } catch (error) { wx.showToast({ title: error.message || '观察行无效', icon: 'none' }) }
  },
  removeObservation(event) { if (this.recoveryBlocked()) return; const index = Number(event.currentTarget.dataset.index); this.setData({ observations: this.data.observations.filter((_, rowIndex) => rowIndex !== index) }) },
  async chooseCountEvidence() {
    if (!this._access || !this._detail || !this._access.can_count || !this.data.selectedScopeId || this.data.busy || this.data.pendingMessage || this.recoveryBlocked()) return
    ensureEvidenceUploads(this)
    try {
      await this._evidenceUploads.select()
    } catch (error) {
      wx.showToast({ title: error.message || '盘点证据上传失败', icon: 'none' })
    }
  },
  async retryCountEvidence(event) {
    if (!this._access || !this._detail || !this._access.can_count || !this.data.selectedScopeId || this.data.busy || this.data.pendingMessage || this.recoveryBlocked()) return
    ensureEvidenceUploads(this)
    try {
      await this._evidenceUploads.retry(String(event.currentTarget.dataset.key || ''))
    } catch (error) {
      wx.showToast({ title: error.message || '盘点证据重试失败', icon: 'none' })
    }
  },
  removeCountEvidence(event) {
    if (!this._access || !this._detail || !this._access.can_count || !this.data.selectedScopeId || this.data.busy || this.data.pendingMessage || this.recoveryBlocked()) return
    ensureEvidenceUploads(this)
    try {
      this._evidenceUploads.remove(String(event.currentTarget.dataset.key || ''))
    } catch (error) {
      wx.showToast({ title: error.message || '盘点证据不能移除', icon: 'none' })
    }
  },
  submitCount(event) {
    try {
      if (this.recoveryBlocked()) throw new Error('原日常盘点范围计数待核验，禁止重新提交')
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
      const input = { action, taskId: this._detail.task_id, roundId: round.round_id, roundNo: round.round_no, scopeId: scope.scope_id, expectedTaskVersion: this._detail.version, body: { count_mode: countMode, account_counts: accounts, physical_observations: zero ? [] : this.data.observations, evidence_file_ids: evidenceFiles.map((file) => file.file_id), zero_confirmed: zero } }
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
    if (this.recoveryBlocked()) return
    const round = currentRound(this._detail)
    const action = event.currentTarget.dataset.action
    if (round) this.run({ action, taskId: this._detail.task_id, roundId: round.round_id, expectedTaskVersion: this._detail.version, body: { expected_task_version: this._detail.version } })
  },
  bindReviewComment(event) { if (this.data.reviewRecoveryBlocked) return; this.setData({ reviewComment: event.detail.value }) },
  submitReview(event) {
    if (this.recoveryBlocked() || this.data.reviewRecoveryBlocked) return
    const stage = event.currentTarget.dataset.stage
    const decision = event.currentTarget.dataset.decision
    const round = currentRound(this._detail)
    if (!round) return
    const comment = this.data.reviewComment.trim()
    if (decision !== 'approve' && !comment) { wx.showToast({ title: '要求复盘或驳回必须填写意见', icon: 'none' }); return }
    const action = stage === 'region' ? 'review_region' : 'review_headquarters'
    if (!hasDurableReviewCapability(formalStocktakeAdapter) && !isLegacyReviewCompatibilityAdapter(formalStocktakeAdapter)) {
      wx.showToast({ title: '当前小程序不支持安全的复核持久恢复，已停止复核', icon: 'none' })
      return
    }
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
    if (this.recoveryBlocked()) return
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
    if (this.recoveryBlocked()) return
    const scopeId = String(event.currentTarget.dataset.id || '')
    const index = Number(event.detail.value)
    const scopes = this.data.detail.scopes.map((scope) => {
      if (scope.scope_id !== scopeId || !scope.recountOptions[index]) return scope
      return Object.assign({}, scope, { recountAssigneeIndex: index, recountAssigneeUserId: scope.recountOptions[index].assignee_user_id })
    })
    this.setData({ 'detail.scopes': scopes })
    this.updateRecountReady(scopes)
  },
  bindRecountReason(event) { if (this.recoveryBlocked()) return; const reason = event.detail.value; this.setData({ recountReason: reason }); this.updateRecountReady(undefined, undefined, reason) },
  submitRecount() {
    if (this.recoveryBlocked()) return
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
