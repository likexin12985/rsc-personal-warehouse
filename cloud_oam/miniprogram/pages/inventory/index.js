const api = require('../../utils/api')
const session = require('../../utils/session')
const { CONDITION } = require('../../utils/format')

Page({
  data: {
    loading: false,
    mode: 'province',
    query: '',
    conditionIndex: 0,
    conditions: [
      { label: '全部状态', value: '' },
      { label: '好件', value: 'good' },
      { label: '旧件', value: 'old' },
      { label: '坏件', value: 'bad' }
    ],
    rows: [],
    totals: { onHand: 0, occupied: 0, inTransit: 0, available: 0 }
  },

  onShow() {
    if (!session.ensureLogin()) return
    const requestedMode = wx.getStorageSync('inventory_mode')
    if (requestedMode) {
      wx.removeStorageSync('inventory_mode')
      this.setData({ mode: requestedMode })
    }
    this.load()
  },

  onPullDownRefresh() {
    this.load().finally(() => wx.stopPullDownRefresh())
  },

  setMode(event) {
    this.setData({ mode: event.currentTarget.dataset.mode }, () => this.load())
  },

  bindQuery(event) {
    this.setData({ query: event.detail.value })
  },

  changeCondition(event) {
    this.setData({ conditionIndex: Number(event.detail.value) }, () => this.load())
  },

  async load() {
    this.setData({ loading: true })
    try {
      const condition = this.data.conditions[this.data.conditionIndex].value
      const rows = await api.get('/inventory', {
        q: this.data.query.trim(),
        condition,
        mine: this.data.mode === 'mine'
      })
      const normalized = rows.map((row) => Object.assign({}, row, {
        conditionLabel: CONDITION[row.condition] || row.condition
      }))
      const totals = normalized.reduce((sum, row) => ({
        onHand: sum.onHand + row.onHand,
        occupied: sum.occupied + row.occupied,
        inTransit: sum.inTransit + row.inTransit,
        available: sum.available + row.available
      }), { onHand: 0, occupied: 0, inTransit: 0, available: 0 })
      this.setData({ rows: normalized, totals })
    } catch (error) {
      wx.showToast({ title: error.message || '库存加载失败', icon: 'none' })
    } finally {
      this.setData({ loading: false })
    }
  }
})
