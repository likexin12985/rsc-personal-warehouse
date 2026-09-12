const api = require('../../utils/api')
const session = require('../../utils/session')
const { formalMaterialRequestAdapter: identityAdapter } = require('../../utils/material-request-adapter')
const { uuid, validateMyReceiving } = require('../../utils/my-receiving-contract')

function empty() {
  return { loading: false, state: 'idle', requestNo: '', packages: [], hasNext: false, hasPrevious: false, pageNumber: 1, message: '' }
}

Page({
  data: empty(),

  onLoad(options) {
    try { this._requestId = uuid(options.request_id) } catch (_) { this._requestId = null }
  },
  onShow() { this._visible = true; this._cursors = [null]; return this.load() },
  onHide() { this.clearView() },
  onUnload() { this.clearView() },
  clearView() {
    this._visible = false
    this._generation = (this._generation || 0) + 1
    this._cursors = [null]
    this._next = null
    this._version = null
    this.setData(empty())
  },
  onPullDownRefresh() {
    this._cursors = [null]
    return this.load().finally(() => wx.stopPullDownRefresh())
  },
  refresh() { this._cursors = [null]; return this.load() },

  async load() {
    if (!this._visible) return
    const generation = (this._generation || 0) + 1
    this._generation = generation
    const current = () => this._visible && this._generation === generation
    this.setData(Object.assign(empty(), { loading: true, state: 'loading', message: '正在读取本人包裹。' }))
    this._next = null
    if (!this._requestId || !session.ensureLogin()) {
      this.setData({ state: 'error', loading: false, message: '请登录后从需求详情进入本人收货。' })
      return
    }
    try {
      const token = session.getToken()
      const person = uuid(session.getUser().person_id)
      const version = session.getUser().authorization_version
      const sameSession = () => session.getToken() === token && session.getUser() && uuid(session.getUser().person_id) === person && session.getUser().authorization_version === version
      const identity = await identityAdapter.loadIdentityNoReplay()
      if (!current()) return
      if (!sameSession()) throw new Error('session changed')
      const access = await identityAdapter.loadAccessNoReplay(identity)
      if (!current()) return
      if (!sameSession() || identity.person_id !== person || identity.authorization_version !== version || !access.can_read || !access.can_read_material_catalog) throw new Error('no current access')
      const after = this._cursors[this._cursors.length - 1]
      const endpoint = `/v1/material-requests/${this._requestId}/my-receiving?limit=5${after ? '&after_id=' + after : ''}`
      const payload = await api.request(endpoint, { method: 'GET', noRefresh: true, header: { 'Cache-Control': 'no-store', Pragma: 'no-cache' } })
      if (!current() || !sameSession()) throw new Error('session changed')
      const result = validateMyReceiving(payload, this._requestId, person, after)
      if (result.packages.length > 5) throw new Error('page limit exceeded')
      if (after && result.requestVersion !== this._version) throw new Error('request changed during pagination')
      const latestIdentity = await identityAdapter.loadIdentityNoReplay()
      if (!current()) return
      if (!sameSession()) throw new Error('session changed')
      const latestAccess = await identityAdapter.loadAccessNoReplay(latestIdentity)
      if (!current()) return
      if (!sameSession() || JSON.stringify(identity) !== JSON.stringify(latestIdentity) || JSON.stringify(access) !== JSON.stringify(latestAccess)) throw new Error('authority changed')
      this._next = result.nextAfterId
      this._version = result.requestVersion
      this.setData({ state: 'ready', requestNo: result.requestNo, packages: result.packages,
        hasNext: !!this._next, hasPrevious: this._cursors.length > 1, pageNumber: this._cursors.length,
        message: '验收与个人仓入账分别记录；本页仅展示包裹和已登记验收数量。' })
    } catch (_) {
      if (!current()) return
      this._cursors = [null]
      this._version = null
      this.setData(Object.assign(empty(), { state: 'error', message: '身份、权限或包裹数据未通过确认，请刷新重试。持续失败请联系管理员。' }))
    } finally {
      if (current()) this.setData({ loading: false })
    }
  },

  nextPage() {
    if (this.data.loading || this.data.state !== 'ready' || !this._next) return
    this._cursors.push(this._next)
    return this.load()
  },
  openCandidates(event) {
    if (!this._visible || this.data.loading || this.data.state !== 'ready') return
    const shipmentId = event.currentTarget.dataset.shipmentId
    if (!this.data.packages.some(item => item.shipment_id === shipmentId)) return
    wx.navigateTo({ url: `/pages/formal-my-receipt/index?request_id=${this._requestId}&shipment_id=${shipmentId}` })
  },
  previousPage() {
    if (this.data.loading || this._cursors.length <= 1) return
    this._cursors.pop()
    return this.load()
  }
})
