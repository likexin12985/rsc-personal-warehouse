const session = require('../../utils/session')
const { formalStocktakeAdapter, createFormalStocktakeIntentRegistry } = require('../../utils/formal-stocktake-adapter')
const { formalStocktakeLabels } = require('../../utils/formal-stocktake-contract')
const { appendTaskPage, accessIdentity } = require('../../utils/formal-operational-stocktake-pagination')

Page({
  data: {
    loading: true,
    busy: false,
    accessAllowed: false,
    accessMessage: '正在校验正式盘点权限',
    tasks: [],
    loadingMore: false,
    hasMore: false,
    canCount: false,
    blindCount: true,
    freezeMode: 'cutoff_replay',
    note: '',
    modeOptions: [{ value: 'blind', label: '盲盘' }, { value: 'open', label: '明盘' }],
    freezeOptions: [{ value: 'cutoff_replay', label: '截止游标回放' }, { value: 'hard', label: '硬冻结' }],
    pendingMessage: '',
    pendingRetryable: false,
    errorMessage: ''
  },

  onLoad() { this._intentRegistry = createFormalStocktakeIntentRegistry(); this._hidden = false },
  onShow() { this._hidden = false; if (session.ensureLogin()) this.load() },
  onHide() { this.invalidateList() },
  onUnload() { this.invalidateList() },
  onPullDownRefresh() { this.load().finally(() => wx.stopPullDownRefresh()) },
  invalidateList() {
    this._hidden = true
    this._loadGeneration = (this._loadGeneration || 0) + 1
    this._pageBusy = false
    this._access = null
    this._nextAfterId = null
    this.setData({ tasks: [], hasMore: false, accessAllowed: false, canCount: false, loading: false, loadingMore: false, busy: false })
    if (this._intentRegistry.current()) this.setData({ pendingMessage: '原盘点写请求结果待核实，已停止新操作；刷新不代表原请求未执行。', pendingRetryable: false })
  },
  loadMore() {
    if (this._hidden || this._pageBusy || this.data.busy || this._creationLease || this.data.loading || !this._access || !this._nextAfterId) return
    return this.load(true)
  },

  async load(append = false, creationLease = null) {
    append = append === true
    if (this._hidden || (this.data.busy && !(creationLease && creationLease === this._creationLease)) || (this._creationLease && creationLease !== this._creationLease) || (append && this._pageBusy)) return
    const cursor = append ? this._nextAfterId : null
    if (append && (!cursor || !this._access)) return
    const previous = append ? this.data.tasks : []
    const expectedIdentity = append ? accessIdentity(this._access) : null
    const generation = (this._loadGeneration || 0) + 1
    this._loadGeneration = generation
    this._pageBusy = true
    if (!append) { this._access = null; this._nextAfterId = null }
    this.setData(append ? { loadingMore: true, errorMessage: '' } : { loading: true, tasks: [], hasMore: false, accessAllowed: false, canCount: false, errorMessage: '' })
    try {
      const access = await formalStocktakeAdapter.loadAccess()
      if (generation !== this._loadGeneration || this._hidden) return
      const identity = accessIdentity(access)
      if (expectedIdentity && expectedIdentity !== identity) throw new Error('盘点分页身份或授权已变化，请刷新')
      const page = await formalStocktakeAdapter.list(cursor)
      if (generation !== this._loadGeneration || this._hidden) return
      const confirmedAccess = await formalStocktakeAdapter.loadAccess()
      if (generation !== this._loadGeneration || this._hidden) return
      if (accessIdentity(confirmedAccess) !== identity || JSON.stringify(confirmedAccess) !== JSON.stringify(access)) throw new Error('盘点读取期间权限已变化，请刷新')
      const merged = appendTaskPage(previous, page, cursor)
      this._access = access
      this._nextAfterId = merged.next_after_id
      if (this._intentRegistry.current()) this.setData({ pendingMessage: '原盘点写请求结果待核实，已停止新操作；请按原坐标人工核验。', pendingRetryable: false })
      this.setData({
        accessAllowed: true,
        canCount: access.can_count === true,
        hasMore: merged.next_after_id !== null,
        accessMessage: `正式盘点权限已验证 · 授权版本 v${access.authorization_version}`,
        tasks: merged.items.map((item) => Object.assign({}, item, {
          typeLabel: formalStocktakeLabels.taskType[item.task_type],
          statusLabel: formalStocktakeLabels.status[item.status],
          modeLabel: item.blind_count ? '盲盘' : '明盘',
          progressLabel: `${item.current_round_visible_completed_scope_count}/${item.visible_scope_count}`
        }))
      })
    } catch (error) {
      if (generation !== this._loadGeneration || this._hidden) return
      this._access = null
      this._nextAfterId = null
      this.setData({ accessAllowed: false, canCount: false, hasMore: false, accessMessage: '身份、授权或正式响应未通过校验，已失败关闭', tasks: [], errorMessage: error.message || '正式盘点读取失败' })
    } finally {
      if (generation === this._loadGeneration) { this._pageBusy = false; this.setData({ loading: false, loadingMore: false }) }
    }
  },

  changeMode(event) { this.setData({ blindCount: Number(event.detail.value) === 0 }) },
  changeFreeze(event) { this.setData({ freezeMode: this.data.freezeOptions[Number(event.detail.value)].value }) },
  bindNote(event) { this.setData({ note: event.detail.value }) },

  async createPersonal(retryIntent = null) {
    if (!this._access || !this._access.can_count || this.data.busy || this._pageBusy || this._hidden || this._creationLease) return
    const existing = this._intentRegistry.current()
    if (existing && (retryIntent !== existing || !this.data.pendingRetryable)) {
      this.setData({ pendingMessage: '原盘点写请求结果待核实，禁止以新操作重发原请求。' })
      return
    }
    const lease = {}
    this._creationLease = lease
    let generation = this._loadGeneration
    const current = () => !this._hidden && this._creationLease === lease && this._loadGeneration === generation
    let intent
    try {
      intent = existing || this._intentRegistry.begin({ action: 'create_personal', body: { blind_count: this.data.blindCount, freeze_mode: this.data.freezeMode, note: this.data.note.trim() } })
      this.setData({ busy: true, errorMessage: '', pendingMessage: '', pendingRetryable: false })
      const completed = await formalStocktakeAdapter.execute(intent)
      if (!current()) return
      this._intentRegistry.complete(intent)
      this.setData({ note: '', pendingMessage: '', pendingRetryable: false })
      const reload = this.load(false, lease)
      generation = this._loadGeneration
      await reload
      if (current() && this._access) wx.navigateTo({ url: `/pages/formal-operational-stocktake-detail/index?task_id=${completed.detail.task_id}` })
    } catch (error) {
      if (!current()) return
      const uncertain = error && error.write_result_uncertain === true
      if (intent && !uncertain) this._intentRegistry.complete(intent)
      const retryable = uncertain && error.stocktake_retry_state === 'retryable'
      this.setData({ errorMessage: error.message || '个人自盘创建失败', pendingRetryable: retryable, pendingMessage: uncertain ? (retryable ? '原写意图仍可复用同一路径、正文、幂等键和请求 ID 重试。' : '写结果不确定且精确回读无法确认；已停止其他写动作，请人工核验原坐标。') : '' })
    } finally {
      if (current()) this.setData({ busy: false })
      if (this._creationLease === lease) this._creationLease = null
    }
  },

  retryPending() {
    const intent = this._intentRegistry.current()
    if (intent && this.data.pendingRetryable) return this.createPersonal(intent)
  },
  openTask(event) {
    const taskId = String(event.currentTarget.dataset.id || '')
    if (!this._hidden && !this.data.busy && !this._pageBusy && !this._creationLease && this._access && this.data.tasks.some((task) => task.task_id === taskId)) wx.navigateTo({ url: `/pages/formal-operational-stocktake-detail/index?task_id=${taskId}` })
  }
})
