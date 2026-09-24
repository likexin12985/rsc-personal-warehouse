const api = require('../../utils/api')
const session = require('../../utils/session')
const { stocktakeAccessDecision } = require('../../utils/production-guard')
const { preparationActor } = require('../../utils/opening-start-options')
const { emptyView, createPreparationController } = require('../../utils/opening-start-preparation')
const { emptyStartView, createStartController } = require('../../utils/opening-start-workflow')
const {
  validateOpeningStocktakePage,
  stocktakeStatusLabel,
  stocktakeActionLabel
} = require('../../utils/stocktake-contract')

function deadlineLabel(value) {
  if (!value) return '无截止时间'
  const date = new Date(value)
  if (!Number.isFinite(date.getTime())) return '截止时间无效'
  const pad = (part) => String(part).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

function presentTask(task) {
  return Object.assign({}, task, {
    statusLabel: stocktakeStatusLabel(task.status),
    deadlineLabel: deadlineLabel(task.deadline),
    progressLabel: `${task.completed_scope_count}/${task.visible_scope_count}`,
    actionLabels: task.allowed_actions.map(stocktakeActionLabel).join(' · ') || '只读'
  })
}

Page({
  data: {
    loading: true,
    accessAllowed: false,
    accessMessage: '正在校验正式盘点权限',
    tasks: [],
    canPrepare: false,
    preparation: emptyView(),
    openingStart: emptyStartView(),
    startFreezeOptions: ['整范围冻结', '按截止流水回算']
  },

  onShow() {
    this._pageVisible = true
    this.resetPreparation()
    if (!session.ensureLogin()) return
    return this.load()
  },

  onHide() {
    this._pageVisible = false
    this._loadGeneration = (this._loadGeneration || 0) + 1
    this.resetPreparation()
    this.setData({ tasks: [], accessAllowed: false, loading: false })
  },

  onUnload() { this.onHide() },

  resetPreparation() {
    if (this._preparationController) this._preparationController.hide()
    this.setData({ canPrepare: false, preparation: emptyView() })
  },

  preparationController() {
    if (!this._preparationController) this._preparationController = createPreparationController({
      currentIdentity: () => session.getUser(),
      publish: (preparation) => this.setData({ preparation }),
      onSelection: (selection) => { void this.startController().select(selection) }
    })
    return this._preparationController
  },

  startController() {
    if (!this._startController) this._startController = createStartController({
      currentIdentity: () => session.getUser(),
      publish: (openingStart) => this.setData({ openingStart }),
      confirmSeal: (content) => new Promise((resolve) => wx.showModal({ title: '终结原启动请求', content,
        confirmText: '确认终结', success: (value) => resolve(value.confirm === true), fail: () => resolve(false) })),
      open: (task) => wx.navigateTo({ url: `/pages/formal-stocktake-detail/index?task_id=${encodeURIComponent(task)}` })
    })
    return this._startController
  },
  refreshStartBatches() { return this.startController().refreshBatches() },
  moreStartBatches() { return this.startController().moreBatches() },
  selectStartBatch(event) { this.startController().selectBatch(event.detail.value) },
  addStartScope() { this.startController().addScope() },
  removeStartScope(event) { this.startController().removeScope(event.currentTarget.dataset.index) },
  editStart(event) { this.startController().edit(event.currentTarget.dataset.field, event.detail.value) },
  submitStart() { return this.startController().submit() },
  recoverStart() { return this.startController().recover() },
  sealStart() { return this.startController().seal() },
  openStartedTask() { this.startController().open() },

  togglePreparation() { return this.preparationController().toggle() },
  refreshPreparation(event) { return this.preparationController().refresh(event.currentTarget.dataset.stage) },
  morePreparation(event) { return this.preparationController().more(event.currentTarget.dataset.stage) },
  selectPreparation(event) { return this.preparationController().select(event.currentTarget.dataset.stage, event.detail.value) },

  onPullDownRefresh() {
    return this.load().finally(() => wx.stopPullDownRefresh())
  },

  async load() {
    if (this._pageVisible === false) return
    this.resetPreparation()
    const generation = (this._loadGeneration || 0) + 1
    this._loadGeneration = generation
    this.setData({
      loading: true,
      accessAllowed: false,
      accessMessage: '正在校验正式盘点权限',
      tasks: []
    })
    try {
      const [user, context] = await Promise.all([
        api.get('/auth/me'),
        api.get('/access/context')
      ])
      if (generation !== this._loadGeneration) return
      if (!getApp().setUser(user)) {
        session.ensureLogin()
        return
      }
      const decision = stocktakeAccessDecision(context, user)
      if (!decision.allowed) {
        this.setData({ accessMessage: decision.message })
        return
      }
      const page = validateOpeningStocktakePage(
        await api.get('/v1/stocktakes/opening')
      )
      if (generation !== this._loadGeneration) return
      this.setData({
        accessAllowed: true,
        accessMessage: '仅展示当前正式授权范围内的盘点任务',
        tasks: page.items.map(presentTask)
      })
      // This does not expand the registered pages or personal-subject release
      // configuration. Only an explicitly authorized internal manager sees
      // the existing page's read-only, not-ready preparation section.
      try {
        const actor = preparationActor(user, context)
        this.preparationController().activate(actor)
        this.setData({ canPrepare: true })
      } catch (_) {
        this.resetPreparation()
      }
    } catch (error) {
      if (generation !== this._loadGeneration) return
      this.setData({
        accessAllowed: false,
        accessMessage: '无法确认身份、权限或盘点响应契约，已失败关闭。',
        tasks: []
      })
      wx.showToast({ title: error.message || '正式盘点读取失败', icon: 'none' })
    } finally {
      if (generation === this._loadGeneration) this.setData({ loading: false })
    }
  },

  openTask(event) {
    const taskId = String(event.currentTarget.dataset.id || '')
    const exists = this.data.tasks.some((task) => task.task_id === taskId)
    if (!exists) {
      wx.showToast({ title: '任务标识已失效，请刷新', icon: 'none' })
      return
    }
    wx.navigateTo({
      url: `/pages/formal-stocktake-detail/index?task_id=${encodeURIComponent(taskId)}`
    })
  }
})
