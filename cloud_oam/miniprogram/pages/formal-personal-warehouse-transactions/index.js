const api = require('../../utils/api')
const session = require('../../utils/session')
const { formalMaterialRequestAdapter: identityAdapter } = require('../../utils/material-request-adapter')
const { validate } = require('../../utils/personal-warehouse-transactions-contract')

const NO_STORE = { method: 'GET', noRefresh: true, header: { 'Cache-Control': 'no-store', Pragma: 'no-cache' } }

function empty() {
  return { state: 'idle', loading: false, rows: [], hasNext: false, hasPrevious: false, pageNumber: 1, message: '' }
}

Page({
  data: empty(),

  onShow() { this._visible = true; this._cursors = [null]; return this.load() },
  onHide() { this.clearView() },
  onUnload() { this.clearView() },
  onPullDownRefresh() { this._cursors = [null]; return this.load().finally(() => wx.stopPullDownRefresh()) },
  refresh() { if (this.data.loading) return; this._cursors = [null]; this._ledgerCursor = null; return this.load() },
  clearView() {
    this._visible = false
    this._generation = (this._generation || 0) + 1
    this._cursors = [null]
    this._next = null
    this._ledgerCursor = null
    this._session = null
    this.setData(empty())
  },
  sameSession(s) {
    const user = session.getUser()
    return !!user && session.getToken() === s.token && user.person_id === s.person && user.authorization_version === s.version
  },
  async readAuthority(s, current) {
    if (!current() || !this.sameSession(s)) throw new Error('身份已变化，请刷新。')
    const identity = await identityAdapter.loadIdentityNoReplay()
    if (!current() || !this.sameSession(s)) throw new Error('身份已变化，请刷新。')
    const access = await identityAdapter.loadAccessNoReplay(identity)
    if (!current() || !this.sameSession(s) || identity.person_id !== s.person || identity.authorization_version !== s.version
      || !access.can_read || !access.can_read_material_catalog) throw new Error('身份或个人仓流水权限已变化，请刷新。')
    return { identity, access }
  },
  async load() {
    if (!this._visible || this.data.loading) return
    const generation = (this._generation || 0) + 1
    this._generation = generation
    const current = () => this._visible && this._generation === generation
    this.setData({ ...empty(), state: 'loading', loading: true, message: '正在核验身份并读取个人仓不可变流水。' })
    try {
      if (!session.ensureLogin()) throw new Error('请登录后查看个人仓流水。')
      const user = session.getUser()
      const s = { token: session.getToken(), person: String(user.person_id || '').toLowerCase(), version: user.authorization_version }
      await this.readAuthority(s, current)
      const after = this._cursors[this._cursors.length - 1]
      const suffix = after ? `&after_cursor=${after}` : ''
      const raw = await api.request(`/v1/inventory/personal/me/transactions?limit=20${suffix}`, NO_STORE)
      if (!current() || !this.sameSession(s)) throw new Error('身份已变化，请刷新。')
      const result = validate(raw, { personId: s.person, afterCursor: after, ledgerCursor: after ? this._ledgerCursor : undefined })
      const latest = await this.readAuthority(s, current)
      if (!latest || !current()) return
      const rows = result.items.map((item) => ({
        ...item,
        changes: item.changes.map(change => ({ ...change, changeKey: `${item.transaction_id}:${change.movement_id}:${change.direction}:${change.stock_account_id}`,
          directionLabel: change.direction === 'in' ? '增加' : '减少',
          serialLabel: change.serial_count ? `${change.serial_count} 个 SN` : '无 SN' }))
      }))
      this._next = result.next_after_cursor
      this._ledgerCursor = result.ledger_cursor
      this._session = s
      this.setData({ state: 'ready', loading: false, rows, hasNext: !!this._next, hasPrevious: this._cursors.length > 1,
        pageNumber: this._cursors.length, message: rows.length ? '仅展示触及本人个人仓的数量变化；来源账户和其他人员数据不会在此页展开。' : '当前个人仓暂无已过账流水。' })
    } catch (error) {
      if (current()) { this._next = null; this._ledgerCursor = null; this._cursors = [null]; this.setData({ ...empty(), state: 'error', message: error.message || '个人仓流水未通过核验，请刷新。' }) }
    } finally {
      if (current() && this.data.loading) this.setData({ loading: false })
    }
  },
  nextPage() {
    if (!this.data.loading && this.data.state === 'ready' && this._next) { this._cursors.push(this._next); return this.load() }
  },
  previousPage() {
    if (!this.data.loading && this._cursors.length > 1) { this._cursors.pop(); return this.load() }
  }
})
