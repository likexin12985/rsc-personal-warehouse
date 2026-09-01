const session = require('../../utils/session')
const { formalStocktakeAdapter, createFormalStocktakeIntentRegistry } = require('../../utils/formal-stocktake-adapter')
const { formalStocktakeLabels } = require('../../utils/formal-stocktake-contract')

Page({
  data: {
    loading: true,
    busy: false,
    accessAllowed: false,
    accessMessage: '正在校验正式盘点权限',
    tasks: [],
    blindCount: true,
    freezeMode: 'cutoff_replay',
    note: '',
    modeOptions: [{ value: 'blind', label: '盲盘' }, { value: 'open', label: '明盘' }],
    freezeOptions: [{ value: 'cutoff_replay', label: '截止游标回放' }, { value: 'hard', label: '硬冻结' }],
    pendingMessage: '',
    pendingRetryable: false,
    errorMessage: ''
  },

  onLoad() { this._intentRegistry = createFormalStocktakeIntentRegistry() },
  onShow() { if (session.ensureLogin()) this.load() },
  onPullDownRefresh() { this.load().finally(() => wx.stopPullDownRefresh()) },

  async load() {
    const generation = (this._loadGeneration || 0) + 1
    this._loadGeneration = generation
    this.setData({ loading: true, errorMessage: '' })
    try {
      const access = await formalStocktakeAdapter.loadAccess()
      if (!access.can_read) throw new Error('当前授权不包含正式盘点只读权限')
      const page = await formalStocktakeAdapter.list(null)
      if (generation !== this._loadGeneration) return
      this._access = access
      this.setData({
        accessAllowed: true,
        accessMessage: `正式盘点权限已验证 · 授权版本 v${access.authorization_version}`,
        tasks: page.items.map((item) => Object.assign({}, item, {
          typeLabel: formalStocktakeLabels.taskType[item.task_type],
          statusLabel: formalStocktakeLabels.status[item.status],
          modeLabel: item.blind_count ? '盲盘' : '明盘',
          progressLabel: `${item.current_round_visible_completed_scope_count}/${item.visible_scope_count}`
        }))
      })
    } catch (error) {
      if (generation !== this._loadGeneration) return
      this._access = null
      this.setData({ accessAllowed: false, accessMessage: '身份、授权或正式响应未通过校验，已失败关闭', tasks: [], errorMessage: error.message || '正式盘点读取失败' })
    } finally {
      if (generation === this._loadGeneration) this.setData({ loading: false })
    }
  },

  changeMode(event) { this.setData({ blindCount: Number(event.detail.value) === 0 }) },
  changeFreeze(event) { this.setData({ freezeMode: this.data.freezeOptions[Number(event.detail.value)].value }) },
  bindNote(event) { this.setData({ note: event.detail.value }) },

  async createPersonal() {
    if (!this._access || !this._access.can_count || this.data.busy) return
    let intent
    try {
      intent = this._intentRegistry.current() || this._intentRegistry.begin({ action: 'create_personal', body: { blind_count: this.data.blindCount, freeze_mode: this.data.freezeMode, note: this.data.note.trim() } })
      this.setData({ busy: true, errorMessage: '', pendingMessage: '', pendingRetryable: false })
      const completed = await formalStocktakeAdapter.execute(intent)
      this._intentRegistry.complete(intent)
      this.setData({ note: '', pendingMessage: '', pendingRetryable: false })
      await this.load()
      wx.navigateTo({ url: `/pages/formal-operational-stocktake-detail/index?task_id=${completed.detail.task_id}` })
    } catch (error) {
      const uncertain = error && error.write_result_uncertain === true
      if (intent && !uncertain) this._intentRegistry.complete(intent)
      const retryable = uncertain && error.stocktake_retry_state === 'retryable'
      this.setData({ errorMessage: error.message || '个人自盘创建失败', pendingRetryable: retryable, pendingMessage: uncertain ? (retryable ? '原写意图仍可复用同一路径、正文、幂等键和请求 ID 重试。' : '写结果不确定且精确回读无法确认；已停止其他写动作，请人工核验原坐标。') : '' })
    } finally { this.setData({ busy: false }) }
  },

  retryPending() { this.createPersonal() },
  openTask(event) {
    const taskId = String(event.currentTarget.dataset.id || '')
    if (taskId) wx.navigateTo({ url: `/pages/formal-operational-stocktake-detail/index?task_id=${taskId}` })
  }
})
