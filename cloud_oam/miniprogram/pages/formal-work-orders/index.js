const api = require('../../utils/api')
const session = require('../../utils/session')
const { inventoryAccessDecision, hasFormalPermission } = require('../../utils/production-guard')
const { uuid, validateMyWorkOrders, validateMaterialOptions } = require('../../utils/work-order-query-contract')
const READ = { method: 'GET', noRefresh: true, header: { 'Cache-Control': 'no-store', Pragma: 'no-cache' } }
function empty() { return { state: 'idle', loading: false, search: '', orders: [], items: [], workOrder: null, locationName: '', message: '', hasNext: false, hasPrevious: false, pageNumber: 1 } }

Page({
  data: empty(),
  onShow() { this._visible = true; this._selected = null; this._cursors = [null]; return this.load() },
  onHide() { this.clearView() },
  onUnload() { this.clearView() },
  clearView() {
    this._visible = false; this._generation = (this._generation || 0) + 1
    this._selected = null; this._next = null; this._cursors = [null]
    this.setData(empty())
  },
  onPullDownRefresh() { this._cursors = [null]; return this.load().finally(() => wx.stopPullDownRefresh()) },
  onSearchInput(event) { this.setData({ search: String(event.detail.value || '').slice(0, 100) }) },
  search() { this._selected = null; this._cursors = [null]; return this.load() },
  refresh() { this._cursors = [null]; return this.load() },
  openOrder(event) {
    if (this.data.loading) return
    try {
      const id = uuid(event.currentTarget.dataset.id)
      if (!this.data.orders.some(row => uuid(row.work_order_id) === id)) return
      this._selected = id
      return this.load()
    } catch (_) { return }
  },
  backToOrders() { this._selected = null; this._cursors = [null]; return this.load() },
  nextPage() { if (!this.data.loading && this._next) { this._cursors.push(this._next); return this.load() } },
  previousPage() { if (!this.data.loading && this._cursors.length > 1) { this._cursors.pop(); return this.load() } },
  async load() {
    if (!this._visible) return
    const generation = (this._generation || 0) + 1; this._generation = generation
    const active = () => this._visible && generation === this._generation
    const selected = this._selected, search = this.data.search
    this._next = null
    this.setData(Object.assign(empty(), { loading: true, state: 'loading', search, message: '正在读取本人工单。' }))
    if (!session.ensureLogin()) { this.setData({ loading: false, state: 'error', message: '请先登录。' }); return }
    try {
      const token = session.getToken(), stored = session.getUser()
      const person = uuid(stored.person_id), version = stored.authorization_version
      const sameSession = () => session.getToken() === token && session.getUser() && uuid(session.getUser().person_id) === person && session.getUser().authorization_version === version
      const context = async () => {
        const user = await api.request('/auth/me', READ)
        if (!active() || !sameSession()) throw new Error('session changed')
        const access = await api.request('/access/context', READ)
        if (!active() || !sameSession() || uuid(user.person_id) !== person || user.authorization_version !== version || !inventoryAccessDecision(access, user).allowed || !hasFormalPermission(access, 'work_order_material', 'read')) throw new Error('access changed')
        return JSON.stringify({ access, roles: user.role_codes })
      }
      const before = await context()
      const after = this._cursors[this._cursors.length - 1]
      const endpoint = selected ? `/v1/work-orders/${selected}/material-options` : `/v1/work-orders/mine?limit=20&status=all&search=${encodeURIComponent(search)}${after ? '&after_id=' + after : ''}`
      const raw = await api.request(endpoint, READ)
      if (!active() || !sameSession()) throw new Error('session changed')
      const result = selected ? validateMaterialOptions(raw, person, version, selected) : validateMyWorkOrders(raw, person, version, after)
      if (await context() !== before || !active() || !sameSession()) throw new Error('access changed')
      if (selected) {
        this.setData({ loading: false, state: 'ready', workOrder: result.workOrder, items: result.items, locationName: result.locationName || '尚未配置个人仓',
          message: !result.openingEstablished ? '个人仓期初尚未建立，暂不展示数量。' : result.workOrder.can_operate ? '按物料查看本人可用库存和本工单剩余占用。' : '工单当前不可操作；以下为已核验的库存与占用记录。' })
      } else {
        this._next = result.next
        this.setData({ loading: false, state: 'ready', orders: result.items, hasNext: !!result.next, hasPrevious: this._cursors.length > 1, pageNumber: this._cursors.length,
          message: result.items.length ? '选择本人 OAM 工单查看物料。' : '没有符合条件的本人工单。' })
      }
    } catch (_) {
      if (active()) this.setData(Object.assign(empty(), { state: 'error', search, message: '工单或物料暂时无法读取，请确认权限后刷新。' }))
    }
  }
})
