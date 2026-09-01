const api = require('../../utils/api')
const session = require('../../utils/session')
const { statusMeta, dateTime } = require('../../utils/format')

const MANAGER_ROLES = ['admin', 'provincial_manager']

Page({
  data: {
    loading: false,
    status: '',
    canManage: false,
    filters: [
      { label: '全部', value: '' },
      { label: '待盘点', value: 'pending' },
      { label: '盘点中', value: 'in_progress' },
      { label: '待复核', value: 'submitted' },
      { label: '已完成', value: 'closed' }
    ],
    rows: []
  },

  onShow() {
    if (!session.ensureLogin()) return
    const requested = wx.getStorageSync('stocktake_status')
    if (requested) {
      wx.removeStorageSync('stocktake_status')
      this.setData({ status: requested })
    }
    const user = session.getUser()
    this.setData({ canManage: !!user && MANAGER_ROLES.includes(user.role) })
    this.load()
  },

  onPullDownRefresh() {
    this.load().finally(() => wx.stopPullDownRefresh())
  },

  setStatus(event) {
    this.setData({ status: event.currentTarget.dataset.status }, () => this.load())
  },

  async load() {
    this.setData({ loading: true })
    try {
      const rows = await api.get('/stocktakes', { status: this.data.status })
      this.setData({
        rows: rows.map((row) => {
          const status = statusMeta(row.status)
          const percent = row.progress.total ? Math.round(row.progress.counted * 100 / row.progress.total) : 0
          return Object.assign({}, row, {
            statusLabel: status.label,
            statusTone: status.tone,
            createdText: dateTime(row.createdAt),
            deadlineText: dateTime(row.deadline),
            percent
          })
        })
      })
    } catch (error) {
      wx.showToast({ title: error.message || '盘点任务加载失败', icon: 'none' })
    } finally {
      this.setData({ loading: false })
    }
  },

  openDetail(event) {
    wx.navigateTo({ url: `/pages/stocktake-detail/index?id=${event.currentTarget.dataset.id}` })
  },

  createTask() {
    wx.navigateTo({ url: '/pages/stocktake-create/index' })
  }
})
