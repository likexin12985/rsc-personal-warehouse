const api = require('../../utils/api')
const session = require('../../utils/session')
const { dateTime } = require('../../utils/format')

const STATUS_LABELS = {
  waitingApproval: '待审批',
  waitingDelivery: '待发货',
  waitingReceive: '待收货',
  finished: '已完成',
  invalided: '已作废',
  refused: '已驳回'
}

const TYPE_LABELS = {
  1: '标准调拨',
  2: '区域调拨',
  4: '服务商申请'
}

Page({
  data: {
    loading: true,
    keyword: '',
    summary: null,
    orders: []
  },

  onShow() {
    if (!session.ensureLogin()) return
    this.load()
  },

  onPullDownRefresh() {
    this.load().finally(() => wx.stopPullDownRefresh())
  },

  onKeyword(event) {
    this.setData({ keyword: event.detail.value })
  },

  search() {
    this.load()
  },

  async load() {
    this.setData({ loading: true })
    try {
      const keyword = encodeURIComponent(this.data.keyword.trim())
      const result = await api.get(`/integrations/oam/orders?search=${keyword}&limit=100`)
      this.setData({
        summary: result.summary,
        orders: result.items.map((row) => Object.assign({}, row, {
          statusLabel: STATUS_LABELS[row.transferStatus || row.status] || row.transferStatus || row.status,
          typeLabel: TYPE_LABELS[row.type] || `类型${row.type}`,
          createdText: dateTime(row.createTime),
          materialText: (row.lines || []).map((line) => `${line.materialCode || '-'} × ${line.applyNum || 0}`).join('；')
        }))
      })
    } catch (error) {
      wx.showToast({ title: error.message || '加载失败', icon: 'none' })
    } finally {
      this.setData({ loading: false })
    }
  }
})
