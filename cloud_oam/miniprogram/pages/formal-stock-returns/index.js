const api = require('../../utils/api')
const session = require('../../utils/session')
const { uuid } = require('../../utils/work-order-query-contract')
const { inventoryAccessDecision, hasFormalPermission } = require('../../utils/production-guard')
const contract = require('../../utils/stock-return-contract')
const { prepareReturn, submitReturn, cancelReturn, reviewRows } = require('../../utils/stock-return-submit')
const { getStore } = require('../../utils/work-order-recovery-store')
const { recoverPending, sealPending } = require('../../utils/work-order-recovery')
const { addScanned } = require('../../utils/work-order-draft')
const { conditionLabel } = require('../../utils/inventory-contract')
const READ = { method: 'GET', noRefresh: true, header: { 'Cache-Control': 'no-store', Pragma: 'no-cache' } }
function empty() { return { loading: false, busy: false, ready: false, message: '', feedback: '', workOrderNo: '', items: [], destinations: [],
  destinationIndex: -1, destinationLabel: '请选择接收仓和在途位置', draftRows: [], reason: '', history: [], canSubmit: false, canCancel: false,
  pending: false, pendingMessage: '', canSeal: false, cancellationId: '', cancelReason: '', confirming: false, review: null } }
Page({
  data: empty(),
  onLoad(options) { try { this._order = uuid(options.workOrderId) } catch (_) { this._order = null } },
  onShow() { this._visible = true; this._store = getStore(); return this.load() },
  onHide() { this.clearView() },
  onUnload() { this.clearView() },
  onPullDownRefresh() { return this.load().finally(() => wx.stopPullDownRefresh()) },
  refresh() { return this.load() },
  openOutbounds(event) {
    if (!this._visible || !this.data.ready || this.data.busy || this.data.loading || !this._matches || !this._matches()) return
    const id = event.currentTarget.dataset.id
    if (!this._history.has(id)) return
    wx.navigateTo({ url: `/pages/formal-stock-return-outbounds/index?workOrderId=${this._order}&operationId=${id}` })
  },
  finishReview(value) { const finish = this._confirmFinish; this._confirmFinish = null; if (finish) finish(value); this.setData({ confirming: false, review: null }) },
  clearDraft() { this.finishReview(false); this._drafts = {}; this._scans = {}; this._revision = (this._revision || 0) + 1;
    this.setData({ draftRows: [], reason: '', destinationIndex: -1, destinationLabel: '请选择接收仓和在途位置', cancellationId: '', cancelReason: '' }) },
  clearView() { this._visible = false; this._generation = (this._generation || 0) + 1; this.clearDraft(); this._choices = null;
    this._history = new Map(); this._context = null; this._matches = null; this._person = null; this.setData(empty()) },
  async load() {
    if (!this._visible) return
    const generation = (this._generation || 0) + 1; this._generation = generation
    const active = () => this._visible && generation === this._generation
    this.clearDraft(); this._choices = null; this._history = new Map(); this._context = null; this._matches = null
    this.setData({ ...empty(), loading: true, message: '正在核验本人退回来源和历史。' })
    if (!this._order || !session.ensureLogin()) { this.setData({ loading: false, message: '请从本人工单进入退回页面并确认已登录。' }); return }
    try {
      const token = session.getToken(), user = session.getUser(), person = uuid(user.person_id), version = user.authorization_version
      const matches = () => { try { const now = session.getUser(); return session.getToken() === token && uuid(now.person_id) === person && now.authorization_version === version } catch (_) { return false } }
      const context = async () => {
        if (!active() || !matches()) throw new Error('session changed')
        const current = await api.request('/auth/me', READ), access = await api.request('/access/context', READ)
        if (!active() || !matches() || uuid(current.person_id) !== person || current.authorization_version !== version
          || !inventoryAccessDecision(access, current).allowed || !hasFormalPermission(access, 'stock_operation', 'read')) throw new Error('access changed')
        return JSON.stringify({ access, roles: current.role_codes })
      }
      const before = await context(), access = JSON.parse(before).access
      this._person = person; this._version = version; this._context = context; this._matches = matches
      this.refreshPending(access)
      const expected = { workOrderId: this._order, personId: person, authorizationVersion: version }
      const history = contract.validateHistory(await api.request(`/v1/work-orders/${this._order}/returns`, READ), expected)
      let choices = null, optionMessage = ''
      const canSubmit = hasFormalPermission(access, 'stock_operation', 'submit_return')
      if (canSubmit) {
        try { choices = contract.validateOptions(await api.request(`/v1/work-orders/${this._order}/returns/options`, READ), expected) }
        catch (_) { optionMessage = '退回来源或接收位置暂未核验，仍可读取此前退回记录。' }
      }
      if (await context() !== before) throw new Error('access changed')
      this._choices = choices; this._history = new Map(history.map(row => [row.original.operation_id, row]))
      this.setData({ loading: false, ready: true, workOrderNo: choices ? choices.workOrder.work_order_no : '本人退回记录',
        items: choices ? choices.items.map(row => ({ ...row, condition: conditionLabel(row.condition_code), selectable: row.selectable_quantity !== '0.000' })) : [],
        destinations: choices ? choices.destinations.map(row => ({ ...row, label: row.target_location_name + ' · ' + row.transit_location_name })) : [],
        canSubmit: !!(canSubmit && choices && !choices.blockers.length && choices.destinations.length),
        canCancel: hasFormalPermission(access, 'stock_operation', 'cancel_return'),
        history: history.map(row => ({ id: row.original.operation_id, number: row.original.operation_no,
          submittedAt: row.original.submitted_at, reason: row.original.reason, target: row.original.destination.target_location_name,
          cancellation: row.cancellation, rows: reviewRows(row.original.lines) })),
        message: optionMessage || (choices && choices.blockers.length ? '来源、期初或责任记录存在待核验事项，暂不可新建退回。'
          : choices && !choices.destinations.length ? '尚无可核验的区域仓在途位置，请先完善库位与接收责任配置。' : '退回占用、取消、发出和接收分别记录；个人保管责任在对方接收前仍保留。') })
      this.refreshPending(access)
    } catch (_) {
      if (active()) { this._context = null; this._matches = null; this._choices = null; this._history = new Map(); this.clearDraft();
        this.setData({ ...empty(), message: '退回数据或当前权限暂时无法核验，请刷新；本机恢复记录仍保留。' }) }
    }
  },
  refreshPending(access) {
    const record = this._store.read({ work_order_id: this._order })
    const own = record.kind === 'valid' && record.value.person_id === this._person && record.value.kind === contract.KIND
    this.setData({ pending: own, canSeal: !!(own && hasFormalPermission(access, 'stock_operation', record.value.operation_type)),
      pendingMessage: own ? '这笔工单有退回结果待确认。请先读取原结果，不要重复提交。'
        : record.kind !== 'missing' ? '该工单还有其他操作待核验，或本机记录不可读取。请回到工单物料处理原请求。' : '' })
  },
  draftReady() { return this._visible && this.data.ready && this.data.canSubmit && !this.data.busy && !this.data.loading
    && this._matches && this._matches() && this._store.read({ work_order_id: this._order }).kind === 'missing' },
  renderDraft() {
    this._revision++
    this.setData({ feedback: '', draftRows: Object.entries(this._drafts).map(([id, value]) => {
      const item = this._choices.items.find(row => row.source_recovery_line_id === id), codes = this._scans[id] || {}
      return { id, materialName: item.material_name, sku: item.sku_code, maximum: item.selectable_quantity, unit: item.base_unit,
        originalNo: item.recovery_operation_no, tracked: item.serials.length > 0, quantity: value.quantity,
        serials: value.serial_verifications.map(sn => ({ id: sn.serial_id, number: sn.serial_no })),
        skuCaptured: !!codes.sku_code, snCaptured: !!codes.serial_no, qrCaptured: !!codes.qr_code }
    }) })
  },
  addSource(event) { if (!this.draftReady()) return; const id = event.currentTarget.dataset.id;
    if (!this._choices.items.some(row => row.source_recovery_line_id === id && row.selectable_quantity !== '0.000') || this._drafts[id]) return
    this._drafts[id] = { quantity: '', serial_verifications: [] }; this.renderDraft() },
  removeSource(event) { if (!this.draftReady()) return; const id = event.currentTarget.dataset.id; delete this._drafts[id]; delete this._scans[id]; this.renderDraft() },
  editQuantity(event) { if (!this.draftReady()) return; const row = this._drafts[event.currentTarget.dataset.id]; if (!row) return;
    row.quantity = String(event.detail.value || '').slice(0, 19); this.renderDraft() },
  chooseDestination(event) { if (!this.draftReady()) return; const index = Number(event.detail.value);
    if (!Number.isInteger(index) || !this.data.destinations[index]) return; this._revision++;
    this.setData({ destinationIndex: index, destinationLabel: this.data.destinations[index].label, feedback: '' }) },
  editReason(event) { if (!this.draftReady()) return; this._revision++; this.setData({ reason: [...String(event.detail.value || '')].slice(0, 500).join(''), feedback: '' }) },
  async scan(event) {
    if (!this.draftReady()) return
    const { id, code } = event.currentTarget.dataset, limits = { sku_code: 80, serial_no: 200, qr_code: 250 }
    if (!this._drafts[id] || !Object.prototype.hasOwnProperty.call(limits, code)) return
    const generation = this._generation, revision = this._revision
    this.setData({ busy: true })
    try {
      const result = await new Promise((resolve, reject) => wx.scanCode({ onlyFromCamera: true, success: resolve, fail: reject }))
      if (!this._visible || generation !== this._generation || revision !== this._revision || !this._matches()) return
      if (!result || typeof result.result !== 'string' || !result.result.trim() || result.result.length > limits[code] || /[\u0000-\u001f\u007f]/.test(result.result)) throw new Error('invalid scan')
      this._scans[id] = { ...(this._scans[id] || {}), [code]: result.result }; this.renderDraft()
    } catch (_) { if (this._visible && generation === this._generation) this.setData({ feedback: '扫码未完成，请重新扫描该件实物。' }) }
    finally { if (this._visible && generation === this._generation) { if (!this._matches || !this._matches()) this.clearView(); else this.setData({ busy: false }) } }
  },
  collectScan(event) { if (!this.draftReady()) return; const id = event.currentTarget.dataset.id;
    const item = this._choices.items.find(row => row.source_recovery_line_id === id); if (!item || !this._drafts[id]) return
    try { this._drafts[id].serial_verifications = addScanned({ ...item, tracking_mode: 'serial', serials: item.serials.filter(sn => sn.selectable) },
      this._drafts[id].serial_verifications, this._scans[id]); this._scans[id] = {}; this.renderDraft() }
    catch (error) { this.setData({ feedback: error.message }) } },
  removeScan(event) { if (!this.draftReady()) return; const { id, serial } = event.currentTarget.dataset; if (!this._drafts[id]) return;
    this._drafts[id].serial_verifications = this._drafts[id].serial_verifications.filter(sn => sn.serial_id !== serial); this.renderDraft() },
  chooseCancellation(event) { if (!this._visible || this.data.busy || this.data.loading || !this.data.canCancel || !this._matches || !this._matches()) return;
    const row = this._history.get(event.currentTarget.dataset.id); if (!row || row.cancellation || this._store.read({ work_order_id: this._order }).kind !== 'missing') return;
    this.clearDraft(); this.setData({ cancellationId: row.original.operation_id, cancelReason: '', feedback: '请说明取消原因，并确认实物尚未发出。' }) },
  editCancelReason(event) { if (this.data.busy || !this.data.cancellationId || !this._matches || !this._matches()) return; this._revision++;
    this.setData({ cancelReason: [...String(event.detail.value || '')].slice(0, 500).join('') }) },
  confirmSubmission() { if (!this._visible || !this.data.confirming || !this._matches || !this._matches()) return this.finishReview(false); this.finishReview(true) },
  holdReview() {},
  cancelSubmission() { this.finishReview(false) },
  preview() { return this.perform('preview') },
  submit() { return this.perform('submit_return') },
  cancelReturn() { return this.perform('cancel_return') },
  recover() { return this.perform('recover') },
  seal() { return this.perform('seal') },
  async perform(kind) {
    if (!this._visible || this.data.busy || this.data.loading || !this._context || !this._matches || !this._matches()) return
    if (['preview', 'submit_return'].includes(kind) && !this.draftReady()) return
    if (kind === 'cancel_return' && (!this.data.canCancel || !this.data.cancellationId)) return
    if (['recover', 'seal'].includes(kind) && (!this.data.pending || kind === 'seal' && !this.data.canSeal)) return
    const generation = this._generation, revision = this._revision, context = this._context, matches = this._matches
    const active = () => this._visible && generation === this._generation
    const authorize = async () => {
      if (!active() || revision !== this._revision || !matches()) throw new Error('session changed')
      const result = await context(), access = JSON.parse(result).access
      const action = kind === 'preview' ? 'submit_return' : kind === 'seal' ? this._store.read({ work_order_id: this._order }).value.operation_type : kind
      if (action !== 'recover' && !hasFormalPermission(access, 'stock_operation', action)) throw new Error('access changed')
      return result
    }
    const args = { api, store: this._store, workOrderId: this._order, personId: this._person, authorizationVersion: this._version, authorize,
      confirm: review => new Promise(resolve => { this._confirmFinish = resolve; this.setData({ confirming: true, review }) }) }
    this.setData({ busy: true, feedback: '正在核验原记录和当前权限。' })
    let feedback
    try {
      let result
      if (['preview', 'submit_return'].includes(kind)) {
        const route = this.data.destinations[this.data.destinationIndex]; if (!route) throw new Error('请先选择接收仓和在途位置。')
        Object.assign(args, { targetLocationId: route.target_location_id, transitLocationId: route.transit_location_id, reason: this.data.reason, drafts: this._drafts })
        if (kind === 'preview') {
          const before = await authorize()
          const prepared = await prepareReturn({ ...args, current: async () => { if (await authorize() !== before) throw new Error('access changed') } })
          feedback = `已核验 ${prepared.review.rows.length} 行退回明细，库存尚未变动。提交前会再次核验并请你确认。`
        } else result = await submitReturn(args)
      } else if (kind === 'cancel_return') result = await cancelReturn({ ...args, operationId: this.data.cancellationId, reason: this.data.cancelReason })
      else result = await (kind === 'seal' ? sealPending : recoverPending)({ ...args, confirm: () => new Promise((resolve, reject) => wx.showModal({
        title: '结束原退回请求', content: '先核验原请求。已经执行就返回原记录；尚未执行则永久封存，迟到请求也无法再执行。',
        confirmText: '确认核验', success: value => resolve(value.confirm === true), fail: reject })) })
      if (result) feedback = result.status === 'confirmed' ? result.command.status === 'outbound' ? '实物发出已确认，请进入原退回的发出记录；接收和入账仍需独立确认。'
        : result.command.status === 'cancelled' ? '取消事实已确认，原回收件的应退责任仍保留。'
        : '退回占用已确认，尚未记录实物发出或接收。' : result.status === 'sealed' ? '原请求已永久封存且未执行。可以重新准备退回。'
          : result.status === 'cancelled' ? '已停止本次操作。' : '原请求结果仍待确认，请保留记录并读取原结果。'
    } catch (error) {
      if (['session changed', 'access changed'].includes(error.message)) {
        if (active()) { this.clearView(); this.setData({ message: '身份或权限已变化，请重新进入退回页面；原请求仍保留。' }) }
      } else feedback = error.message || '本次操作未完成核验，请读取原结果。'
    } finally {
      if (active()) {
        if (!matches()) { this.clearView(); this.setData({ message: '身份已变化，请重新进入退回页面。' }) }
        else if (kind !== 'preview') { this.clearDraft(); await this.load(); if (this._visible) this.setData({ feedback }) }
        else this.setData({ busy: false, feedback })
      }
    }
  }
})
