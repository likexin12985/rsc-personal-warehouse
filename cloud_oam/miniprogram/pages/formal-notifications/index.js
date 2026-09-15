const api = require('../../utils/api')
const session = require('../../utils/session')
const { validatePage, validateRead } = require('../../utils/notification-contract')

Page({
  data: { loading: true, loadingMore: false, error: '', items: [], nextAfterId: null, unreadCount: 0 },
  onShow() { if (session.ensureLogin()) this.load() },
  onPullDownRefresh() { this.load().finally(() => wx.stopPullDownRefresh()) },
  async load(afterId = null, append = false) {
    this.setData({ loading: !append, loadingMore: append, error: '' })
    try {
      const suffix = afterId ? `?limit=50&after_id=${encodeURIComponent(afterId)}` : '?limit=50'
      const page = validatePage(await api.get(`/v1/notifications${suffix}`))
      this.setData({ items: append ? this.data.items.concat(page.items) : page.items, nextAfterId: page.next_after_id, unreadCount: page.unread_count })
    } catch (error) {
      this.setData({ error: error.message || '通知读取失败', ...(append ? {} : { items: [], nextAfterId: null }) })
    } finally { this.setData({ loading: false, loadingMore: false }) }
  },
  loadMore() { if (!this.data.loadingMore && this.data.nextAfterId) this.load(this.data.nextAfterId, true) },
  async openItem(event) {
    const id = event.currentTarget.dataset.id
    const item = this.data.items.find(row => row.delivery_id === id)
    if (!item || item.status === 'read') return
    try {
      const result = validateRead(await api.post(`/v1/notifications/${encodeURIComponent(id)}/read`, {}))
      this.setData({ items: this.data.items.map(row => row.delivery_id === id ? result.item : row), unreadCount: Math.max(0, this.data.unreadCount - 1) })
    } catch (error) { wx.showToast({ title: error.message || '通知已读状态未确认', icon: 'none' }) }
  }
})
