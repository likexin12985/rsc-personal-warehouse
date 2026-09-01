const api = require('../../utils/api')
const session = require('../../utils/session')
const { statusMeta, dateTime, CONDITION, TRANSFER_TYPE } = require('../../utils/format')

Page({
  data: {
    id: '',
    loading: true,
    transfer: null,
    attachments: []
  },

  onLoad(options) {
    this.setData({ id: options.id || '' })
  },

  onShow() {
    if (!session.ensureLogin()) return
    this.load()
  },

  onPullDownRefresh() {
    this.load().finally(() => wx.stopPullDownRefresh())
  },

  async load() {
    if (!this.data.id) return
    this.setData({ loading: true })
    try {
      const [transfer, attachments] = await Promise.all([
        api.get(`/transfers/${this.data.id}`),
        api.get(`/media/transfer/${this.data.id}`)
      ])
      const status = statusMeta(transfer.status)
      this.setData({
        transfer: Object.assign({}, transfer, {
          statusLabel: status.label,
          statusTone: status.tone,
          typeLabel: TRANSFER_TYPE[transfer.transferType] || transfer.transferType,
          createdText: dateTime(transfer.createdAt),
          dispatchedText: dateTime(transfer.dispatchedAt),
          receivedText: dateTime(transfer.receivedAt),
          items: transfer.items.map((item) => Object.assign({}, item, {
            conditionLabel: CONDITION[item.condition] || item.condition
          }))
        }),
        attachments: attachments.map((item) => Object.assign({}, item, {
          isVideo: item.mimeType.indexOf('video') === 0
        }))
      })
    } catch (error) {
      wx.showToast({ title: error.message || '详情加载失败', icon: 'none' })
    } finally {
      this.setData({ loading: false })
    }
  },

  previewMedia(event) {
    const attachment = this.data.attachments.find((item) => item.id === event.currentTarget.dataset.id)
    if (!attachment) return
    wx.navigateTo({
      url: '/pages/media-preview/index',
      success: (result) => result.eventChannel.emit('media', attachment)
    })
  }
})
