const api = require('../../utils/api')
const session = require('../../utils/session')
const { statusMeta, dateTime, TRANSFER_TYPE } = require('../../utils/format')

Page({
  data: {
    loading: false,
    status: '',
    filters: [
      { label: '全部', value: '' },
      { label: '旧：待审批', value: 'pending_approval' },
      { label: '旧：草稿', value: 'draft' },
      { label: '旧：待收货', value: 'dispatched' },
      { label: '旧：已入库', value: 'received' },
      { label: '旧：已驳回', value: 'rejected' },
      { label: '旧：已取消', value: 'cancelled' }
    ],
    rows: []
  },

  onShow() {
    if (!session.ensureLogin()) return
    const requestedStatus = wx.getStorageSync('transfer_status')
    if (requestedStatus !== '') {
      wx.removeStorageSync('transfer_status')
      this.setData({ status: requestedStatus || '' })
    }
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
      const rows = await api.get('/transfers', { status: this.data.status, limit: 200 })
      this.setData({
        rows: rows.map((row) => {
          const status = statusMeta(row.status)
          return Object.assign({}, row, {
            statusLabel: status.label,
            statusTone: status.tone,
            typeLabel: TRANSFER_TYPE[row.transferType] || row.transferType,
            createdText: dateTime(row.createdAt),
            itemTotal: row.items.reduce((sum, item) => sum + item.quantity, 0)
          })
        })
      })
    } catch (error) {
      wx.showToast({ title: error.message || '调拨单加载失败', icon: 'none' })
    } finally {
      this.setData({ loading: false })
    }
  },

  openDetail(event) {
    wx.navigateTo({ url: `/pages/transfer-detail/index?id=${event.currentTarget.dataset.id}` })
  },
})
