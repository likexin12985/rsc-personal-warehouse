const api = require('../../utils/api')
const session = require('../../utils/session')
const { formalMaterialRequestAdapter: identityAdapter } = require('../../utils/material-request-adapter')
const { validate } = require('../../utils/personal-warehouse-serials-contract')

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const SAFE_SN = /^[^\u0000-\u001f\u007f]{1,200}$/
const NO_STORE = { method: 'GET', noRefresh: true, header: { 'Cache-Control': 'no-store', Pragma: 'no-cache' } }
function empty() { return { state: 'idle', loading: false, busy: false, rows: [], sku: '', materialName: '', lot: '', total: 0, hasNext: false, hasPrevious: false, pageNumber: 1, scanMode: false, message: '' } }

Page({
  data: empty(),
  onLoad(options) { this._accountId = options && UUID.test(String(options.accountId || '')) ? String(options.accountId).toLowerCase() : '' },
  onShow() { this._visible = true; this._cursors = [null]; this._ledgerCursor = null; return this.load() },
  onHide() { this.clearView() },
  onUnload() { this.clearView() },
  onPullDownRefresh() { this._cursors = [null]; this._ledgerCursor = null; return this.load().finally(() => wx.stopPullDownRefresh()) },
  clearView() {
    this._visible = false; this._generation = (this._generation || 0) + 1
    this._cursors = [null]; this._next = null; this._ledgerCursor = null; this._session = null
    this.setData(empty())
  },
  sameSession(s) {
    const user = session.getUser()
    return !!user && session.getToken() === s.token && String(user.person_id || '').toLowerCase() === s.person && user.authorization_version === s.version
  },
  async readAuthority(s, current) {
    if (!current() || !this.sameSession(s)) throw new Error('身份已变化，请刷新。')
    const identity = await identityAdapter.loadIdentityNoReplay()
    if (!current() || !this.sameSession(s)) throw new Error('身份已变化，请刷新。')
    const access = await identityAdapter.loadAccessNoReplay(identity)
    if (!current() || !this.sameSession(s) || String(identity.person_id || '').toLowerCase() !== s.person || identity.authorization_version !== s.version
      || !access.can_read || !access.can_read_material_catalog) throw new Error('身份或个人仓 SN 权限已变化，请刷新。')
    return { identity, access }
  },
  sessionSnapshot() {
    if (!session.ensureLogin()) throw new Error('请登录后查看个人仓 SN。')
    const user = session.getUser()
    const person = String(user.person_id || '').toLowerCase()
    if (!UUID.test(person) || !Number.isSafeInteger(user.authorization_version) || user.authorization_version < 1) throw new Error('登录身份无效，请重新登录。')
    return { token: session.getToken(), person, version: user.authorization_version }
  },
  endpoint(after, serialNo) {
    const base = `/v1/inventory/personal/me/accounts/${this._accountId}/serials?limit=${serialNo ? 1 : 50}`
    if (serialNo) return `${base}&serial_no=${encodeURIComponent(serialNo)}`
    return after ? `${base}&after_id=${after}` : base
  },
  async load() {
    if (!this._visible || this.data.loading) return
    const generation = (this._generation || 0) + 1; this._generation = generation
    const current = () => this._visible && generation === this._generation
    this.setData({ ...empty(), state: 'loading', loading: true, message: '正在核验本人个人仓及 SN 账本。' })
    try {
      if (!this._accountId) throw new Error('库存明细标识无效，请返回个人仓重新选择。')
      const s = this.sessionSnapshot()
      await this.readAuthority(s, current)
      const after = this._cursors[this._cursors.length - 1]
      const raw = await api.request(this.endpoint(after), NO_STORE)
      if (!current() || !this.sameSession(s)) throw new Error('身份已变化，请刷新。')
      const result = validate(raw, { personId: s.person, accountId: this._accountId, afterId: after, ledgerCursor: after ? this._ledgerCursor : undefined })
      await this.readAuthority(s, current)
      if (!current()) return
      this._next = result.next_after_id; this._ledgerCursor = result.ledger_cursor; this._session = s
      this.setData({ state: 'ready', loading: false, rows: result.items, sku: result.sku_code, materialName: result.material_name,
        lot: result.lot_no || '', total: result.total_serials, hasNext: !!this._next, hasPrevious: this._cursors.length > 1,
        pageNumber: this._cursors.length, scanMode: false, message: result.items.length ? '仅展示当前仍在本人该库存明细中的 SN；二维码不会返回页面。' : '当前库存明细没有 SN。' })
    } catch (error) {
      if (current()) { this._next = null; this._ledgerCursor = null; this._cursors = [null]; this.setData({ ...empty(), state: 'error', message: error.message || '个人仓 SN 未通过核验，请刷新。' }) }
    } finally { if (current() && this.data.loading) this.setData({ loading: false }) }
  },
  nextPage() { if (!this.data.loading && !this.data.busy && this.data.state === 'ready' && this._next) { this._cursors.push(this._next); return this.load() } },
  previousPage() { if (!this.data.loading && !this.data.busy && this._cursors.length > 1) { this._cursors.pop(); return this.load() } },
  showAll() { if (!this.data.loading && !this.data.busy) { this._cursors = [null]; this._ledgerCursor = null; return this.load() } },
  async scanSerial() {
    if (!this._visible || this.data.state !== 'ready' || this.data.loading || this.data.busy || !this._session || !this.sameSession(this._session)) return
    const generation = this._generation, s = this._session
    const current = () => this._visible && generation === this._generation && this.sameSession(s)
    this.setData({ busy: true, message: '正在扫描 SN 条码。' })
    try {
      const scanned = await new Promise((resolve, reject) => wx.scanCode({ onlyFromCamera: true, success: resolve, fail: reject }))
      if (!current()) return
      const serialNo = scanned && typeof scanned.result === 'string' ? scanned.result : ''
      if (!SAFE_SN.test(serialNo) || serialNo !== serialNo.trim()) throw new Error('未采集到有效 SN，请重新扫描。')
      await this.readAuthority(s, current)
      const raw = await api.request(this.endpoint(null, serialNo), NO_STORE)
      if (!current()) return
      const result = validate(raw, { personId: s.person, accountId: this._accountId, ledgerCursor: this._ledgerCursor, serialNo })
      await this.readAuthority(s, current)
      if (!current()) return
      this.setData({ rows: result.items, scanMode: true, message: result.items.length ? '已在本人当前个人仓中找到该 SN。' : '本人当前该库存明细中未找到扫描的 SN。' })
    } catch (error) {
      if (current()) this.setData({ rows: [], scanMode: true, message: error.message || 'SN 扫描查询失败，请重新扫描或显示全部。' })
    } finally { if (this._visible && generation === this._generation) this.setData({ busy: false }) }
  }
})
