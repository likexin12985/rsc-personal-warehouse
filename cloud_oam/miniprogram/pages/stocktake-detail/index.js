const api = require('../../utils/api')
const session = require('../../utils/session')
const { statusMeta, dateTime, CONDITION } = require('../../utils/format')

const MANAGER_ROLES = ['admin', 'provincial_manager']

Page({
  data: {
    id: '',
    loading: true,
    actionLoading: false,
    task: null,
    attachments: [],
    editable: false,
    canClose: false,
    missingCount: 0,
    hasDifference: false
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
      const [task, attachments] = await Promise.all([
        api.get(`/stocktakes/${this.data.id}`),
        api.get(`/media/stocktake/${this.data.id}`)
      ])
      const user = session.getUser()
      const status = statusMeta(task.status)
      const items = task.items.map((item) => Object.assign({}, item, {
        conditionLabel: CONDITION[item.condition] || item.condition,
        countedInput: item.counted === null ? '' : String(item.counted),
        differenceText: item.difference === null ? '-' : `${item.difference > 0 ? '+' : ''}${item.difference}`
      }))
      this.setData({
        task: Object.assign({}, task, {
          statusLabel: status.label,
          statusTone: status.tone,
          createdText: dateTime(task.createdAt),
          deadlineText: dateTime(task.deadline),
          items
        }),
        attachments: attachments.map((item) => Object.assign({}, item, {
          isVideo: item.mimeType.indexOf('video') === 0
        })),
        editable: ['pending', 'in_progress'].includes(task.status),
        canClose: task.status === 'submitted' && !!user && MANAGER_ROLES.includes(user.role)
      })
      this.recalculate(items)
    } catch (error) {
      wx.showToast({ title: error.message || '盘点详情加载失败', icon: 'none' })
    } finally {
      this.setData({ loading: false })
    }
  },

  updateItem(event) {
    const index = Number(event.currentTarget.dataset.index)
    const field = event.currentTarget.dataset.field
    const items = this.data.task.items.slice()
    items[index][field] = event.detail.value
    if (field === 'countedInput') {
      const raw = event.detail.value
      items[index].counted = raw === '' ? null : Number(raw)
      items[index].difference = raw === '' ? null : Number(raw) - items[index].expected
      items[index].differenceText = raw === '' ? '-' : `${items[index].difference > 0 ? '+' : ''}${items[index].difference}`
    }
    this.setData({ 'task.items': items })
    this.recalculate(items)
  },

  recalculate(items) {
    this.setData({
      missingCount: items.filter((item) => item.counted === null).length,
      hasDifference: items.some((item) => item.counted !== null && item.counted !== item.expected)
    })
  },

  async saveItem(event) {
    const index = Number(event.currentTarget.dataset.index)
    const item = this.data.task.items[index]
    if (!Number.isInteger(item.counted) || item.counted < 0) {
      wx.showToast({ title: '请输入非负整数', icon: 'none' })
      return
    }
    try {
      await api.put(`/stocktakes/${this.data.id}/items/${item.id}`, {
        counted_quantity: item.counted,
        remark: (item.remark || '').trim()
      })
      wx.showToast({ title: '已保存', icon: 'success' })
    } catch (error) {
      wx.showToast({ title: error.message || '保存失败', icon: 'none' })
    }
  },

  async setAllExpected() {
    const confirmed = await this.confirm('全部按账面数量', '将所有明细填写为盘点持平，已填写内容也会被覆盖。')
    if (!confirmed) return
    this.setData({ actionLoading: true })
    try {
      const items = this.data.task.items.map((item) => Object.assign({}, item, {
        counted: item.expected,
        countedInput: String(item.expected),
        difference: 0,
        differenceText: '0',
        remark: ''
      }))
      for (let i = 0; i < items.length; i += 5) {
        await Promise.all(items.slice(i, i + 5).map((item) => api.put(
          `/stocktakes/${this.data.id}/items/${item.id}`,
          { counted_quantity: item.expected, remark: '' }
        )))
      }
      this.setData({ 'task.items': items })
      this.recalculate(items)
      wx.showToast({ title: '已全部填平', icon: 'success' })
    } catch (error) {
      wx.showToast({ title: error.message || '批量填写失败', icon: 'none' })
      await this.load()
    } finally {
      this.setData({ actionLoading: false })
    }
  },

  async submitTask() {
    if (this.data.missingCount) {
      wx.showToast({ title: `还有${this.data.missingCount}项未盘点`, icon: 'none' })
      return
    }
    if (this.data.hasDifference && !this.data.attachments.length) {
      wx.showToast({ title: '有差异时必须上传凭证', icon: 'none' })
      return
    }
    const confirmed = await this.confirm('提交盘点结果', '提交后明细不可继续修改，请确认所有数量和差异原因。')
    if (!confirmed) return
    await this.runAction(() => api.post(`/stocktakes/${this.data.id}/submit`), '已提交复核')
  },

  async closeTask() {
    const confirmed = await this.confirm('复核并关闭任务', '关闭后盘点数量将写入库存，该操作不可撤销。')
    if (!confirmed) return
    await this.runAction(() => api.post(`/stocktakes/${this.data.id}/close`), '盘点已完成')
  },

  async runAction(action, title) {
    this.setData({ actionLoading: true })
    try {
      await action()
      wx.showToast({ title, icon: 'success' })
      await this.load()
    } catch (error) {
      wx.showToast({ title: error.message || '操作失败', icon: 'none', duration: 2800 })
    } finally {
      this.setData({ actionLoading: false })
    }
  },

  confirm(title, content) {
    return new Promise((resolve) => {
      wx.showModal({ title, content, confirmColor: '#e65318', success: (result) => resolve(result.confirm) })
    })
  },

  chooseMedia() {
    wx.chooseMedia({
      count: 9,
      mediaType: ['image', 'video'],
      sourceType: ['album', 'camera'],
      maxDuration: 60,
      success: (result) => this.uploadFiles(result.tempFiles)
    })
  },

  async uploadFiles(files) {
    wx.showLoading({ title: '上传中', mask: true })
    try {
      for (const file of files) {
        await api.upload(`/media/stocktake/${this.data.id}`, file.tempFilePath)
      }
      await this.load()
      wx.showToast({ title: '凭证已上传', icon: 'success' })
    } catch (error) {
      wx.showToast({ title: error.message || '上传失败', icon: 'none' })
    } finally {
      wx.hideLoading()
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
