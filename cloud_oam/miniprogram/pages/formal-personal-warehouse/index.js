const api = require('../../utils/api')
const session = require('../../utils/session')
const { inventoryAccessDecision, isFormalAuthenticatedUser } = require('../../utils/production-guard')
const { validatePersonalWarehouse, availabilityLabel, conditionLabel } = require('../../utils/inventory-contract')

const PAGE_SIZE = 40
const STATUS_OPTIONS = ['', 'available', 'reserved', 'picking', 'outbound', 'in_transit', 'arrived_pending', 'frozen', 'return_pending', 'scrap_pending']
const CONDITION_OPTIONS = ['', 'new', 'used', 'damaged', 'scrapped']
const TRACKING_LABELS = { none: '数量管理', lot: '批次管理', serial: 'SN 管理', lot_and_serial: '批次与 SN 管理' }

function emptyView() {
  return {
    loading: false, state: 'idle', title: '个人仓', message: '进入页面后读取最新库存。',
    personName: '', locationName: '', openingEstablished: false,
    rows: [], matchedCount: 0, hasMore: false, receivedAt: '',
    query: '', statusIndex: 0, conditionIndex: 0
  }
}

function identityKey(user) {
  if (!isFormalAuthenticatedUser(user) || !Number.isSafeInteger(user.authorization_version) || user.authorization_version <= 0) return ''
  return JSON.stringify([user.person_id.toLowerCase(), user.authorization_version, user.role_codes.slice().sort()])
}

// Credentials remain only in this short-lived closure, never in setData/storage.
// Token rotation deliberately discards the old read; the next refresh reads anew.
function sessionGuard() {
  const token = session.getToken()
  const key = identityKey(session.getUser())
  if (!token || !key) throw new Error('请重新验证登录身份后读取个人仓。')
  return () => session.getToken() === token && identityKey(session.getUser()) === key
}

async function readIdentity() {
  const [user, access] = await Promise.all([api.get('/auth/me'), api.get('/access/context')])
  const decision = inventoryAccessDecision(access, user)
  if (!identityKey(user) || !decision.allowed) throw new Error(decision.message || '个人仓读取权限未通过校验。')
  return user
}

function displayRow(item) {
  // Explicit projection keeps unrelated response fields out of the page bridge.
  return {
    id: item.stock_account_id, sku: item.sku_code, name: item.material_name,
    quantity: item.quantity, unit: item.base_unit, owner: item.owner_org_name,
    lot: item.lot_no || '', tracking: TRACKING_LABELS[item.tracking_mode],
    condition: item.condition_code, conditionLabel: conditionLabel(item.condition_code),
    status: item.availability_bucket, statusLabel: availabilityLabel(item.availability_bucket)
  }
}

Page({
  data: Object.assign(emptyView(), {
    statusLabels: STATUS_OPTIONS.map((value) => value ? availabilityLabel(value) : '全部状态'),
    conditionLabels: CONDITION_OPTIONS.map((value) => value ? conditionLabel(value) : '全部成色')
  }),

  onShow() {
    this._visible = true
    return this.load()
  },

  onHide() { this.clearView() },
  onUnload() { this.clearView() },

  clearView() {
    this._visible = false
    this._generation = (this._generation || 0) + 1
    this._allRows = []
    this._sessionCurrent = null
    this.setData(emptyView())
  },

  onPullDownRefresh() {
    return this.load().finally(() => wx.stopPullDownRefresh())
  },

  async load() {
    if (!this._visible) return
    const generation = (this._generation || 0) + 1
    this._generation = generation
    const currentPage = () => this._visible && generation === this._generation
    this._allRows = []
    this._sessionCurrent = null
    this._visibleLimit = PAGE_SIZE
    this.setData(Object.assign(emptyView(), {
      loading: true, state: 'loading', title: '正在读取个人仓', message: '正在确认身份和库存。'
    }))
    if (!session.ensureLogin()) {
      this.setData({ loading: false, state: 'error', title: '请先登录', message: '登录后可查看本人的个人仓。' })
      return
    }
    try {
      let sameSession = sessionGuard()
      const initialPerson = session.getUser().person_id.toLowerCase()
      const user = await readIdentity()
      if (!currentPage()) return
      if (!sameSession() || user.person_id.toLowerCase() !== initialPerson) throw new Error('登录状态已变化，请重新读取个人仓。')
      if (!getApp().setUser(user)) throw new Error('登录身份无法保存，请重新登录。')
      sameSession = sessionGuard()

      const payload = await api.request('/v1/inventory/personal/me', {
        method: 'GET', header: { 'Cache-Control': 'no-store', Pragma: 'no-cache' }
      })
      if (!currentPage()) return
      if (!sameSession()) throw new Error('登录状态已变化，请重新读取个人仓。')
      const warehouse = validatePersonalWarehouse(payload, user.person_id)
      // A delayed warehouse response must not survive revoked/changed authority.
      const latestUser = await readIdentity()
      if (!currentPage()) return
      if (!sameSession() || identityKey(latestUser) !== identityKey(user)) throw new Error('读取期间权限已变化，请重新读取个人仓。')

      const configured = warehouse.location_id !== null
      const established = warehouse.opening_balance_status === 'established'
      this._sessionCurrent = sameSession
      this._allRows = established ? warehouse.items.map(displayRow) : []
      this.setData({
        state: !configured ? 'unconfigured' : (established ? 'ready' : 'unopened'),
        title: !configured ? '尚未配置个人仓' : (established ? '我的个人仓' : '期初库存待建立'),
        message: !configured ? '请联系管理员确认个人仓库位和保管责任。'
          : (established ? '按物料、成色和状态分别查看数量。'
            : '首次实物盘点及区域、总部复核完成后，才会展示库存数量。'),
        personName: latestUser.name, locationName: warehouse.location_name || '',
        openingEstablished: established,
        receivedAt: new Date().toLocaleTimeString('zh-CN', { hour12: false })
      })
      this.applyFilters()
    } catch (error) {
      if (!currentPage()) return
      this._allRows = []
      this._sessionCurrent = null
      this.setData(Object.assign(emptyView(), {
        state: 'error', title: '暂时无法读取个人仓',
        message: '身份、权限或库存数据未通过确认，请刷新重试；持续失败请联系管理员。'
      }))
    } finally {
      if (currentPage()) this.setData({ loading: false })
    }
  },

  applyFilters() {
    if (!this._visible || this.data.state !== 'ready') return
    if (!this._sessionCurrent || !this._sessionCurrent()) {
      this._allRows = []
      this._sessionCurrent = null
      this.setData(Object.assign(emptyView(), { state: 'error', title: '登录状态已变化', message: '请重新读取个人仓。' }))
      return
    }
    const query = this.data.query.trim().toLowerCase()
    const status = STATUS_OPTIONS[this.data.statusIndex]
    const condition = CONDITION_OPTIONS[this.data.conditionIndex]
    const matches = this._allRows.filter((row) => (
      (!status || row.status === status) && (!condition || row.condition === condition)
      && (!query || [row.sku, row.name, row.lot].some((text) => text.toLowerCase().includes(query)))
    ))
    this.setData({ rows: matches.slice(0, this._visibleLimit), matchedCount: matches.length, hasMore: matches.length > this._visibleLimit })
  },

  onSearch(event) {
    if (!this._visible || this.data.state !== 'ready') return
    if (typeof event.detail.value !== 'string') return
    this._visibleLimit = PAGE_SIZE
    this.setData({ query: event.detail.value.slice(0, 100) })
    this.applyFilters()
  },

  onStatusChange(event) { this.changeFilter('statusIndex', STATUS_OPTIONS, event) },
  onConditionChange(event) { this.changeFilter('conditionIndex', CONDITION_OPTIONS, event) },

  changeFilter(field, options, event) {
    if (!this._visible || this.data.state !== 'ready') return
    const raw = event.detail.value
    if (!/^(0|[1-9][0-9]*)$/.test(String(raw))) return
    const index = Number(raw)
    if (!Number.isSafeInteger(index) || index >= options.length) return
    this._visibleLimit = PAGE_SIZE
    this.setData({ [field]: index })
    this.applyFilters()
  },

  loadMore() {
    if (!this.data.hasMore) return
    this._visibleLimit += PAGE_SIZE
    this.applyFilters()
  }
})
