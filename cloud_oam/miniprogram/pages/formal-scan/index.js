const api = require('../../utils/api')
const session = require('../../utils/session')
const { formalMaterialRequestAdapter: identityAdapter } = require('../../utils/material-request-adapter')
const { validate } = require('../../utils/personal-qr-contract')

const NO_STORE = { method: 'GET', noRefresh: true, header: { 'Cache-Control': 'no-store', Pragma: 'no-cache' } }
const SAFE_CODE = /^[^\u0000-\u001f\u007f]{1,250}$/

function empty() {
  return { state: 'idle', loading: false, busy: false, result: null, message: '扫描个人仓、物料或批次二维码。' }
}

Page({
  data: empty(),

  onShow() {
    this._visible = true
    this._generation = (this._generation || 0) + 1
    this.setData(empty())
    return this.readAuthority()
  },

  onHide() { this.clearView() },
  onUnload() { this.clearView() },

  clearView() {
    this._visible = false
    this._generation = (this._generation || 0) + 1
    this._session = null
    this.setData(empty())
  },

  sessionSnapshot() {
    if (!session.ensureLogin()) throw new Error('请登录后使用统一扫码。')
    const user = session.getUser()
    if (!user || typeof user.person_id !== 'string' || !Number.isSafeInteger(user.authorization_version) || user.authorization_version < 1) throw new Error('登录身份无效，请重新登录。')
    return { token: session.getToken(), person: user.person_id.toLowerCase(), version: user.authorization_version }
  },

  sameSession(snapshot) {
    const user = session.getUser()
    return !!user && session.getToken() === snapshot.token && String(user.person_id || '').toLowerCase() === snapshot.person && user.authorization_version === snapshot.version
  },

  async readAuthority() {
    try {
      const snapshot = this.sessionSnapshot()
      const identity = await identityAdapter.loadIdentityNoReplay()
      const access = await identityAdapter.loadAccessNoReplay(identity)
      if (!this._visible || !this.sameSession(snapshot) || String(identity.person_id || '').toLowerCase() !== snapshot.person || identity.authorization_version !== snapshot.version || !access.can_read || !access.can_read_material_catalog) throw new Error('身份或统一扫码权限已变化，请刷新。')
      this._session = snapshot
      this.setData({ state: 'ready', message: '相机扫描结果只在本次读取期间使用，不会保存二维码内容。' })
    } catch (error) {
      if (this._visible) this.setData({ state: 'error', message: error.message || '统一扫码权限校验失败。' })
    }
  },

  async scan() {
    if (!this._visible || this.data.state !== 'ready' || this.data.busy || !this._session || !this.sameSession(this._session)) return
    const generation = this._generation
    const current = () => this._visible && generation === this._generation && this.sameSession(this._session)
    this.setData({ busy: true, result: null, message: '正在读取二维码对象。' })
    try {
      const scanned = await new Promise((resolve, reject) => wx.scanCode({ onlyFromCamera: true, success: resolve, fail: reject }))
      if (!current()) return
      // Keep the credential in this local variable only.  It is never put in
      // Page.data, storage, recovery markers, logs, or the response contract.
      const code = scanned && typeof scanned.result === 'string' ? scanned.result : ''
      if (!SAFE_CODE.test(code) || code !== code.trim()) throw new Error('未采集到有效二维码，请重新扫描。')
      const raw = await api.request(`/v1/scan/qr?code=${encodeURIComponent(code)}`, NO_STORE)
      if (!current()) return
      const result = validate(raw)
      if (!current()) return
      this.setData({ result, message: '已按当前正式权限解析对象；服务端未返回二维码原文。' })
    } catch (error) {
      if (current()) this.setData({ result: null, message: error.message || '二维码读取失败，请重新扫描。' })
    } finally {
      if (this._visible && generation === this._generation) this.setData({ busy: false })
    }
  },

  openWarehouse() {
    if (!this._visible || !this.sameSession(this._session) || !this.data.result || !this.data.result.actions.includes('view_personal_warehouse')) return
    wx.switchTab({ url: '/pages/formal-personal-warehouse/index' })
  },

  openSerials() {
    const result = this.data.result
    if (!this._visible || !this.sameSession(this._session) || !result
      || result.objectType !== 'serial' || !result.stockAccountId
      || !result.actions.includes('view_personal_serials')) return
    wx.navigateTo({ url: `/pages/formal-personal-warehouse-serials/index?accountId=${result.stockAccountId}` })
  }
})
