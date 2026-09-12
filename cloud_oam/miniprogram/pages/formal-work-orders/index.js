const api = require('../../utils/api')
const session = require('../../utils/session')
const { inventoryAccessDecision, hasFormalPermission } = require('../../utils/production-guard')
const { uuid, validateMyWorkOrders, validateMaterialOptions } = require('../../utils/work-order-query-contract')
const recoveryStore = require('../../utils/work-order-recovery-store')
const { recoverPending, sealPending } = require('../../utils/work-order-recovery')
const draft = require('../../utils/work-order-draft')
const READ = { method: 'GET', noRefresh: true, header: { 'Cache-Control': 'no-store', Pragma: 'no-cache' } }
function empty() { return { state: 'idle', loading: false, busy: false, search: '', orders: [], items: [], workOrder: null, locationName: '', message: '', hasNext: false, hasPrevious: false, pageNumber: 1, canRecover: false, recoveryMessage: '', pendingRequests: [], pendingMessage: '', canSeal: false, canDraft: false, operationKind: 'occupy', draftRows: [], previewMessage: '' } }

Page({
  data: empty(),
  onShow() { this._visible = true; this._selected = null; this._cursors = [null]; this._store = recoveryStore.getStore(); return this.load() },
  onHide() { this.clearView() },
  onUnload() { this.clearView() },
  clearView() {
    this._visible = false; this._generation = (this._generation || 0) + 1
    this._selected = null; this._next = null; this._cursors = [null]
    this._recoveryContext = null
    this._recoveryPerson = null; this._pendingMarkers = new Map()
    this.resetDraft()
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
    this._recoveryContext = null
    this._recoveryPerson = null; this._pendingMarkers = new Map()
    this.resetDraft()
    this.setData(Object.assign(empty(), { loading: true, state: 'loading', search, message: '正在读取本人工单。' }))
    if (!session.ensureLogin()) { this.setData({ loading: false, state: 'error', message: '请先登录。' }); return }
    try {
      const token = session.getToken(), stored = session.getUser()
      const person = uuid(stored.person_id), version = stored.authorization_version
      const sameSession = () => {
        try {
          const latest = session.getUser()
          return session.getToken() === token && !!latest && uuid(latest.person_id) === person && latest.authorization_version === version
        } catch (_) { return false }
      }
      const context = async () => {
        if (!active() || !sameSession()) throw new Error('session changed')
        const user = await api.request('/auth/me', READ)
        if (!active() || !sameSession()) throw new Error('session changed')
        const access = await api.request('/access/context', READ)
        if (!active() || !sameSession() || uuid(user.person_id) !== person || user.authorization_version !== version || !inventoryAccessDecision(access, user).allowed || !hasFormalPermission(access, 'work_order_material', 'read')) throw new Error('access changed')
        return JSON.stringify({ access, roles: user.role_codes })
      }
      const before = await context()
      const after = this._cursors[this._cursors.length - 1]
      const endpoint = selected ? `/v1/work-orders/${selected}/material-options` : `/v1/work-orders/mine?limit=20&status=all&search=${encodeURIComponent(search)}${after ? '&after_id=' + after : ''}`
      let result, queryFailed = false
      try {
        const raw = await api.request(endpoint, READ)
        if (!active() || !sameSession()) throw new Error('session changed')
        result = selected ? validateMaterialOptions(raw, person, version, selected) : validateMyWorkOrders(raw, person, version, after)
      } catch (_) { queryFailed = true }
      if (!active() || !sameSession()) throw new Error('session changed')
      if (await context() !== before || !active() || !sameSession()) throw new Error('access changed')
      this._recoveryContext = context
      this._sessionMatches = sameSession
      this._recoveryPerson = person
      this.refreshPendingRequests()
      this.setData({ canSeal: hasFormalPermission(JSON.parse(before).access, 'work_order_material', 'operate') })
      if (queryFailed) {
        this.setData({ loading: false, state: 'error', message: '工单或物料暂时无法读取；仍可核验下方本人待确认的原请求。' })
        return
      }
      if (selected) {
        const stored = this._store.read({ work_order_id: selected })
        const canRecover = stored.kind === 'valid' && stored.value.person_id === person
        this.setData({ loading: false, state: 'ready', workOrder: result.workOrder, items: result.items, locationName: result.locationName || '尚未配置个人仓',
          canDraft: stored.kind === 'missing' && result.workOrder.can_operate && result.openingEstablished && hasFormalPermission(JSON.parse(before).access, 'work_order_material', 'operate'),
          canRecover, recoveryMessage: canRecover ? '该工单有待确认的原请求，请先读取原结果。' : stored.kind !== 'missing' ? '本地恢复记录暂不可用或属于其他人员，暂勿提交新操作。' : '',
          message: !result.openingEstablished ? '个人仓期初尚未建立，暂不展示数量。' : result.workOrder.can_operate ? '按物料查看本人可用库存和本工单剩余占用。' : '工单当前不可操作；以下为已核验的库存与占用记录。' })
      } else {
        this._next = result.next
        this.setData({ loading: false, state: 'ready', orders: result.items, hasNext: !!result.next, hasPrevious: this._cursors.length > 1, pageNumber: this._cursors.length,
          message: result.items.length ? '选择本人 OAM 工单查看物料。' : '没有符合条件的本人工单。' })
      }
    } catch (_) {
      if (active()) {
        this._recoveryContext = null; this._recoveryPerson = null; this._pendingMarkers = new Map()
        this.setData(Object.assign(empty(), { state: 'error', search, message: '工单或物料暂时无法读取，请确认权限后刷新。' }))
      }
    }
  },
  refreshPendingRequests() {
    const snapshot = this._store.listPending(this._recoveryPerson)
    this._pendingMarkers = new Map(snapshot.items.map(marker => [marker.work_order_id, marker]))
    const labels = { occupy: '投入占用', consume: '实际消耗', release: '释放未用物料' }
    this.setData({ pendingRequests: snapshot.items.map((marker, index) => ({
      id: marker.work_order_id, label: `待确认请求 ${index + 1} · ${labels[marker.operation_type]}`
    })), pendingMessage: snapshot.kind === 'ready' ? '' : '部分恢复记录暂不可读取。请保留本机记录，已列出的本人请求仍可分别核验。' })
  },
  resetDraft() { this._drafts = {}; this._scans = {}; this._draftRevision = (this._draftRevision || 0) + 1; this._sessionMatches = null },
  draftReady() { return this._visible && this.data.canDraft && !this.data.busy && !this.data.loading && this._sessionMatches && this._sessionMatches() && this._store.read({ work_order_id: this._selected }).kind === 'missing' },
  renderDraft() {
    this._draftRevision++
    const rows = Object.keys(this._drafts).map(id => {
      const item = this.data.items.find(row => row.stock_account_id === id), value = this._drafts[id], codes = this._scans[id] || {}
      return { id, materialName: item.material_name, sku: item.sku_code, maximum: item.selectable_quantity,
        unit: item.base_unit, quantity: value.quantity, tracked: draft.tracked(item), serials: value.serial_verifications.map(proof => ({ id: proof.serial_id, number: proof.serial_no })),
        skuCaptured: !!codes.sku_code, snCaptured: !!codes.serial_no, qrCaptured: !!codes.qr_code }
    })
    this.setData({ draftRows: rows, previewMessage: '' })
  },
  chooseOperation(event) {
    const kind = event.currentTarget.dataset.kind
    if (!this.draftReady() || !['occupy', 'consume', 'release'].includes(kind) || kind === this.data.operationKind) return
    this._drafts = {}; this._scans = {}
    this.setData({ operationKind: kind }); this.renderDraft()
  },
  addMaterial(event) {
    if (!this.draftReady()) return
    const id = event.currentTarget.dataset.id, item = this.data.items.find(row => row.stock_account_id === id)
    if (!item || !item.allowed_actions.includes(this.data.operationKind) || this._drafts[id]) return
    this._drafts[id] = { quantity: '', serial_verifications: [] }; this.renderDraft()
  },
  removeMaterial(event) {
    if (!this.draftReady()) return
    const id = event.currentTarget.dataset.id
    delete this._drafts[id]; delete this._scans[id]; this.renderDraft()
  },
  editQuantity(event) {
    if (!this.draftReady()) return
    const id = event.currentTarget.dataset.id, value = this._drafts[id]
    if (!value) return
    value.quantity = String(event.detail.value || '').slice(0, 19); this.renderDraft()
  },
  async scanMaterialCode(event) {
    if (!this.draftReady()) return
    const { id, code } = event.currentTarget.dataset
    const item = this.data.items.find(row => row.stock_account_id === id)
    if (!this._drafts[id] || !item || !draft.tracked(item) || !['sku_code', 'serial_no', 'qr_code'].includes(code)) return
    const generation = this._generation, revision = this._draftRevision
    const current = () => this._visible && generation === this._generation && revision === this._draftRevision && this._sessionMatches && this._sessionMatches()
    this.setData({ busy: true })
    try {
      const result = await new Promise((resolve, reject) => wx.scanCode({ onlyFromCamera: true, success: resolve, fail: reject }))
      if (!current()) return
      const limit = { sku_code: 80, serial_no: 200, qr_code: 250 }[code]
      if (!result || typeof result.result !== 'string' || !result.result.trim() || result.result.length > limit || /[\u0000-\u001f\u007f]/.test(result.result)) throw new Error('扫码内容为空或格式不正确。')
      this._scans[id] = { ...(this._scans[id] || {}), [code]: result.result }
      this.renderDraft()
    } catch (_) { if (current()) this.setData({ previewMessage: '未采集到有效扫码内容，请重新扫描。' }) }
    finally {
      if (this._visible && generation === this._generation) {
        if (!this._sessionMatches || !this._sessionMatches()) this.clearView()
        else this.setData({ busy: false })
      }
    }
  },
  collectScanned(event) {
    if (!this.draftReady()) return
    const id = event.currentTarget.dataset.id, item = this.data.items.find(row => row.stock_account_id === id)
    if (!item || !this._drafts[id]) return
    try {
      this._drafts[id].serial_verifications = draft.addScanned(item, this._drafts[id].serial_verifications, this._scans[id])
      this._scans[id] = {}; this.renderDraft()
    } catch (error) { this.setData({ previewMessage: error.message }) }
  },
  removeScanned(event) {
    if (!this.draftReady()) return
    const { id, serial } = event.currentTarget.dataset
    if (!this._drafts[id]) return
    this._drafts[id].serial_verifications = this._drafts[id].serial_verifications.filter(row => row.serial_id !== serial)
    this.renderDraft()
  },
  async previewMaterials() {
    if (!this.draftReady() || !this._recoveryContext) return
    const generation = this._generation, revision = this._draftRevision, order = this._selected
    const context = this._recoveryContext, kind = this.data.operationKind, person = this.data.workOrder.engineer_person_id
    const version = session.getUser().authorization_version
    const current = () => this._visible && generation === this._generation && revision === this._draftRevision && this._sessionMatches && this._sessionMatches()
    this.setData({ busy: true, previewMessage: '正在核验整批物料，库存尚未变动。' })
    try {
      // Validate local input before issuing any request, then rebuild it from
      // newly read options. A preview has no idempotency key and cannot post.
      draft.buildDraft({ workOrder: this.data.workOrder, items: this.data.items, personId: person, kind, drafts: this._drafts })
      const before = await context()
      const rawOptions = await api.request(`/v1/work-orders/${order}/material-options`, READ)
      if (!current()) return
      const choices = validateMaterialOptions(rawOptions, person, version, order)
      const lines = draft.buildDraft({ workOrder: choices.workOrder, items: choices.items, personId: person, kind, drafts: this._drafts })
      if (await context() !== before || !current()) throw new Error('access changed')
      const raw = await api.request(`/v1/work-orders/${order}/material-operations/${kind}/preview`, {
        ...READ, method: 'POST', data: { operator_person_id: person, lines }
      })
      if (!current()) return
      const result = draft.validatePreview(raw, { workOrderId: order, personId: person, authorizationVersion: version,
        kind, lines, sourceVersion: choices.workOrder.source_version, ledgerCursor: rawOptions.ledger_cursor })
      if (await context() !== before || !current()) throw new Error('access changed')
      this.setData({ previewMessage: `整批 ${result.line_count} 条物料预检通过（${result.checked_at}）。库存尚未变动。` })
    } catch (error) {
      if (this._visible && generation === this._generation) {
        if (!this._sessionMatches() || ['session changed', 'access changed'].includes(error.message)) {
          this.clearView(); this.setData({ state: 'error', message: '身份或权限已变化，请重新进入工单页面。' })
        } else this.setData({ previewMessage: error.message || '整批预检未通过，请核对物料后重新预检。' })
      }
    } finally {
      if (this._visible && generation === this._generation) {
        if (!this._sessionMatches || !this._sessionMatches()) this.clearView()
        else this.setData({ busy: false })
      }
    }
  },
  async recover() {
    if (!this._visible || !this._selected || !this.data.canRecover || this.data.loading || this.data.busy || !this._recoveryContext) return
    return this.recoverRequest(this._selected)
  },
  async recoverPendingRequest(event) {
    const id = event.currentTarget.dataset.id
    if (!this._pendingMarkers || !this._pendingMarkers.has(id)) return
    return this.recoverRequest(id)
  },
  async sealPendingRequest(event) {
    const id = event.currentTarget.dataset.id
    if (!this.data.canSeal || !this._pendingMarkers || !this._pendingMarkers.has(id)) return
    return this.recoverRequest(id, true)
  },
  async recoverRequest(order, seal = false) {
    if (!this._visible || this.data.loading || this.data.busy || !this._recoveryContext || !this._recoveryPerson) return
    const generation = this._generation, context = this._recoveryContext, matches = this._sessionMatches
    const current = () => this._visible && generation === this._generation
    this.setData({ busy: true })
    try {
      const result = await (seal ? sealPending : recoverPending)({ api, store: this._store, workOrderId: order,
        personId: this._recoveryPerson, authorize: context, confirm: () => new Promise((resolve, reject) => wx.showModal({
          title: '结束未执行请求', content: '系统将先核验原操作。若已执行，将返回原结果；若尚未执行，将永久关闭此请求，之后可重新准备物料。',
          confirmText: '确认核验', cancelText: '暂不处理', success: value => resolve(value.confirm === true), fail: reject
        })) })
      if (!current()) return
      if (result.status === 'confirmed' || result.status === 'sealed') {
        this.refreshPendingRequests()
        this.setData({ canRecover: this._selected === order ? false : this.data.canRecover,
          recoveryMessage: result.status === 'sealed' ? '原请求已关闭且未执行。请刷新工单后重新准备物料。' : `原操作已确认：${result.command.operation_no}。请刷新库存后继续。` })
      }
      else if (result.status === 'cancelled') this.setData({ recoveryMessage: '原请求仍保留，可继续读取原结果。' })
      else this.setData({ recoveryMessage: '暂未读取到已提交的原结果，仍保留恢复记录；请稍后继续核验。' })
    } catch (error) {
      if (current()) {
        if (['session changed', 'access changed'].includes(error.message)) {
          this.clearView(); this.setData({ state: 'error', message: '身份或权限已变化，请重新进入工单页面。' })
        } else this.setData({ recoveryMessage: '原请求暂未完成核验，恢复记录仍保留，请稍后重试读取。' })
      }
    } finally {
      if (current()) {
        if (!matches || !matches()) this.clearView()
        else this.setData({ busy: false })
      }
    }
  }
})
