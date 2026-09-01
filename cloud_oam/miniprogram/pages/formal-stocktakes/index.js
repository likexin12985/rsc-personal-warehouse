const api = require('../../utils/api')
const session = require('../../utils/session')
const { stocktakeAccessDecision } = require('../../utils/production-guard')
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
    tasks: []
  },

  onShow() {
    if (!session.ensureLogin()) return
    this.load()
  },

  onPullDownRefresh() {
    this.load().finally(() => wx.stopPullDownRefresh())
  },

  async load() {
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
