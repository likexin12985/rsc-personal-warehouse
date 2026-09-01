const api = require('../../utils/api')
const session = require('../../utils/session')
const { canUseOperationalClient } = require('../../utils/production-guard')
const { statusMeta, dateTime, CONDITION } = require('../../utils/format')

Page({
  data: {
    loading: false,
    actionLoading: '',
    status: '',
    canCreate: false,
    filters: [
      { label: '全部', value: '' },
      { label: '占用中', value: 'occupied' },
      { label: '已消耗', value: 'consumed' },
      { label: '已回收', value: 'recovered' },
      { label: '已释放', value: 'released' }
    ],
    rows: []
  },

  onShow() {
    if (!session.ensureLogin()) return
    const user = session.getUser()
    this.setData({ canCreate: !!user && canUseOperationalClient(user.role) })
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
      const rows = await api.get('/work-order-materials', {
        status: this.data.status,
        mine: session.getUser().role === 'technician',
        limit: 200
      })
      this.setData({
        rows: rows.map((row) => {
          const status = statusMeta(row.status)
          return Object.assign({}, row, {
            statusLabel: status.label,
            statusTone: status.tone,
            conditionLabel: CONDITION[row.condition] || row.condition,
            recoveryLabel: CONDITION[row.recoveryCondition] || row.recoveryCondition,
            createdText: dateTime(row.createdAt)
          })
        })
      })
    } catch (error) {
      wx.showToast({ title: error.message || '工单物料加载失败', icon: 'none' })
    } finally {
      this.setData({ loading: false })
    }
  },

  createRecord() {
    wx.navigateTo({ url: '/pages/work-material-create/index' })
  },

  consume(event) {
    this.confirmAction(event.currentTarget.dataset.id, 'consume', '确认消耗', '确认物料已投入该工单？库存现有量与占用量都会扣减。')
  },

  release(event) {
    this.confirmAction(event.currentTarget.dataset.id, 'release', '释放占用', '确认该工单不再使用此物料？释放后物料恢复可用。')
  },

  recover(event) {
    const id = event.currentTarget.dataset.id
    const recoveryCondition = event.currentTarget.dataset.condition
    const label = recoveryCondition === 'bad' ? '坏件' : '旧件'
    wx.showModal({
      title: `回收为${label}`,
      content: `确认已从设备回收对应${label}？回收后进入个人${label}库存，后续还需发起退回单。`,
      confirmColor: '#e65318',
      success: async (result) => {
        if (!result.confirm) return
        await this.runAction(
          id,
          () => api.post(`/work-order-materials/${id}/recover`, { recovery_condition: recoveryCondition, note: '' }),
          '回收完成'
        )
      }
    })
  },

  confirmAction(id, action, title, content) {
    wx.showModal({
      title,
      content,
      confirmColor: '#e65318',
      success: async (result) => {
        if (!result.confirm) return
        await this.runAction(
          id,
          () => api.post(`/work-order-materials/${id}/${action}`),
          action === 'consume' ? '已确认消耗' : '占用已释放'
        )
      }
    })
  },

  async runAction(id, action, title) {
    this.setData({ actionLoading: id })
    try {
      await action()
      wx.showToast({ title, icon: 'success' })
      await this.load()
    } catch (error) {
      wx.showToast({ title: error.message || '操作失败', icon: 'none', duration: 2800 })
    } finally {
      this.setData({ actionLoading: '' })
    }
  }
})
