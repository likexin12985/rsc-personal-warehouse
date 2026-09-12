const api = require('../../utils/api')
const session = require('../../utils/session')
const { formalMaterialRequestAdapter: identityAdapter } = require('../../utils/material-request-adapter')
const { uuid } = require('../../utils/my-receiving-contract')
const { validateCandidates, matchCandidateScan } = require('../../utils/my-receipt-candidates-contract')

function empty() { return { state: 'idle', loading: false, scanning: false, canScan: false, requestNo: '', shipmentNo: '', targetLocationName: '', checkedAt: '', lines: [], matchedCount: 0, message: '', blockedMessage: '' } }

Page({
  data: empty(),
  onLoad(options) {
    try { this._requestId = uuid(options.request_id); this._shipmentId = uuid(options.shipment_id) } catch (_) { this._requestId = null; this._shipmentId = null }
  },
  onShow() { this._visible = true; return this.load() },
  onHide() { this.clearView() },
  onUnload() { this.clearView() },
  clearView() {
    this._visible = false; this._generation = (this._generation || 0) + 1
    this._candidate = null; this._session = null; this._matched = new Set(); this._limits = {}
    this.setData(empty())
  },
  onPullDownRefresh() { return this.load().finally(() => wx.stopPullDownRefresh()) },
  refresh() { return this.load() },
  sameSession(snapshot) {
    const user = session.getUser()
    return !!user && session.getToken() === snapshot.token && user.person_id === snapshot.person && user.authorization_version === snapshot.version
  },
  async authority(snapshot, current) {
    try {
      if (!current() || !this.sameSession(snapshot)) throw new Error('context changed')
      const identity = await identityAdapter.loadIdentityNoReplay()
      if (!current() || !this.sameSession(snapshot)) throw new Error('context changed')
      const access = await identityAdapter.loadAccessNoReplay(identity)
      if (!current() || !this.sameSession(snapshot) || identity.person_id !== snapshot.person || identity.authorization_version !== snapshot.version || !access.can_read || !access.can_read_material_catalog) throw new Error('no current access')
      return JSON.stringify({ identity, access })
    } catch (_) {
      const error = new Error('身份或读取权限已变化，请刷新后重新核对。')
      error.contextInvalid = true
      throw error
    }
  },
  async load() {
    if (!this._visible) return
    const generation = (this._generation || 0) + 1
    this._generation = generation; this._candidate = null; this._session = null; this._matched = new Set(); this._limits = {}
    const current = () => this._visible && generation === this._generation
    this.setData(Object.assign(empty(), { loading: true, state: 'loading', message: '正在核对本人包裹及未验收明细。' }))
    try {
      if (!this._requestId || !this._shipmentId || !session.ensureLogin()) throw new Error('请登录后从本人收货页面进入。')
      const user = session.getUser()
      const snapshot = { token: session.getToken(), person: uuid(user.person_id), version: user.authorization_version }
      const before = await this.authority(snapshot, current)
      if (!current()) return
      const response = await api.request(`/v1/material-requests/${this._requestId}/my-receiving/${this._shipmentId}/candidates`, { method: 'GET', noRefresh: true, header: { 'Cache-Control': 'no-store', Pragma: 'no-cache' } })
      if (!current()) return
      const candidate = validateCandidates(response, this._requestId, this._shipmentId, snapshot.person)
      if (await this.authority(snapshot, current) !== before) throw new Error('查询期间权限已变化，请刷新。')
      if (!current()) return
      this._candidate = candidate; this._session = snapshot
      this.setData({ state: 'ready', requestNo: candidate.requestNo, shipmentNo: candidate.shipmentNo, targetLocationName: candidate.targetLocationName,
        checkedAt: candidate.checkedAt, blockedMessage: candidate.blockedMessage,
        message: '核对包裹内未验收物料。扫码只标记本次核对，不登记验收或个人仓入账。' })
      this.renderLines()
    } catch (_) {
      if (current()) this.setData(Object.assign(empty(), { state: 'error', message: '身份、权限或包裹明细未通过确认，请刷新重试。' }))
    } finally { if (current()) this.setData({ loading: false }) }
  },
  renderLines() {
    const lines = this._candidate.lines.map(line => {
      const limit = this._limits[line.shipment_line_id] || 20
      const { remaining_serials, ...display } = line
      return Object.assign({}, display, {
        shownSerials: line.remaining_serials.slice(0, limit).map(s => ({ serial_id: s.serial_id, serial_no: s.serial_no, checked: this._matched.has(s.serial_id) })),
        hasMore: limit < line.remaining_serials.length, totalSerials: line.remaining_serials.length })
    })
    this.setData({ lines, matchedCount: this._matched.size, canScan: lines.some(line => line.totalSerials > 0) })
  },
  showMore(event) {
    if (!this._candidate || !this._visible || !this.sameSession(this._session)) { this.clearView(); return }
    const id = event.currentTarget.dataset.lineId
    if (!this._candidate.lines.some(line => line.shipment_line_id === id)) return
    this._limits[id] = (this._limits[id] || 20) + 20; this.renderLines()
  },
  async scan() {
    if (!this._candidate || !this._visible || !this.data.canScan || this.data.scanning || this.data.loading) return
    const generation = this._generation, snapshot = this._session
    const current = () => this._visible && generation === this._generation
    this.setData({ scanning: true })
    try {
      const before = await this.authority(snapshot, current)
      if (!current()) return
      const result = await new Promise((resolve, reject) => wx.scanCode({ onlyFromCamera: true, success: resolve, fail: () => reject(new Error('扫描已取消，可重新扫描。')) }))
      if (!current()) return
      if (await this.authority(snapshot, current) !== before) {
        const error = new Error('扫描期间权限已变化，请刷新。'); error.contextInvalid = true; throw error
      }
      if (!current()) return
      const matched = matchCandidateScan(this._candidate, result.result)
      const repeated = this._matched.has(matched.serialId)
      this._matched.add(matched.serialId); this.renderLines()
      this.setData({ message: repeated ? '该 SN 本次已核对，无需重复扫描。' : 'SN 与本包裹待验收明细一致，已标记本次核对。' })
    } catch (error) {
      if (!current()) return
      if (!this.sameSession(snapshot) || error.contextInvalid) {
        this._candidate = null; this._session = null; this._matched = new Set()
        this.setData(Object.assign(empty(), { state: 'error', message: '身份已变化，请刷新后重新核对。' }))
      } else this.setData({ message: error.message || '扫描核对失败，请刷新。' })
    } finally { if (current()) this.setData({ scanning: false }) }
  }
})
