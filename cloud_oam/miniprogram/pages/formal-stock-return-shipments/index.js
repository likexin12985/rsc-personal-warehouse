const api = require('../../utils/api')
const session = require('../../utils/session')
const { uuid } = require('../../utils/work-order-query-contract')
const { inventoryAccessDecision, hasFormalPermission } = require('../../utils/production-guard')
const contract = require('../../utils/stock-return-shipment-contract')
const { readShipments, prepareShipment, submitShipment, reviewRows, displayTime } = require('../../utils/stock-return-shipment-submit')
const { getStore } = require('../../utils/work-order-recovery-store')
const { recoverPending, sealPending } = require('../../utils/work-order-recovery')
const { conditionLabel } = require('../../utils/inventory-contract')
const READ = { method: 'GET', noRefresh: true, header: { 'Cache-Control': 'no-store', Pragma: 'no-cache' } }
const STATUS = { not_shipped: '尚未发运', partially_shipped: '部分发运', shipped: '全部发运' }
const DEPARTURE = { not_outbound: '尚未实物发出', partially_outbound: '部分实物发出', outbound: '全部实物发出' }
function empty() { return { loading: false, busy: false, ready: false, message: '', feedback: '', number: '', target: '', transit: '',
  status: '', outboundStatus: '', cancellation: null, items: [], history: [], canSubmit: false, noChoices: false, draftRows: [], carrier: '', trackingNo: '', reason: '', date: '', clock: '', seconds: '00',
  pending: false, pendingMessage: '', canSeal: false, confirming: false, review: null } }
Page({
  data: empty(),
  onLoad(options) { try { this._order = uuid(options.workOrderId); this._operation = uuid(options.operationId) } catch (_) { this._order = null; this._operation = null } },
  onShow() { this._visible = true; this._store = getStore(); return this.load() },
  onHide() { this.clearView() },
  onUnload() { this.clearView() },
  onPullDownRefresh() { return this.load().finally(() => wx.stopPullDownRefresh()) },
  refresh() { return this.load() },
  openDepartures() { if (this._visible && this.data.ready && !this.data.busy && this._matches && this._matches())
    wx.navigateTo({ url: `/pages/formal-stock-return-outbounds/index?workOrderId=${this._order}&operationId=${this._operation}` }) },
  finishReview(value) { const finish = this._confirmFinish; this._confirmFinish = null; if (finish) finish(value); this.setData({ confirming: false, review: null }) },
  clearDraft() { this.finishReview(false); this._drafts = {}; this._revision = (this._revision || 0) + 1;
    this.setData({ draftRows: [], carrier: '', trackingNo: '', reason: '', date: '', clock: '', seconds: '00' }) },
  clearView() { this._visible = false; this._generation = (this._generation || 0) + 1; this.clearDraft(); this._choices = null;
    this._context = null; this._matches = null; this._person = null; this.setData(empty()) },
  async load() {
    if (!this._visible) return
    const generation = (this._generation || 0) + 1; this._generation = generation
    const active = () => this._visible && generation === this._generation
    this.clearDraft(); this._choices = null; this._context = null; this._matches = null
    this.setData({ ...empty(), loading: true, message: '正在核验原退回、实物发出及每包运单记录。' })
    if (!this._order || !this._operation || !session.ensureLogin()) { this.setData({ loading: false, message: '请从本人的原退回记录进入，并确认已登录。' }); return }
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
      const current = async () => { if (await context() !== before) throw new Error('access changed') }
      const expected = { workOrderId: this._order, operationId: this._operation, personId: person, authorizationVersion: version }
      const history = await readShipments({ api, current, ...expected }), original = history.departures.original
      let choices = null, optionMessage = ''
      if (hasFormalPermission(access, 'stock_operation', contract.ACTION) && !history.departures.cancellation && history.shipment_status !== 'shipped') {
        try { choices = contract.validateOptions(await api.request(`/v1/work-orders/${this._order}/returns/${this._operation}/shipments/options`, READ), expected, history) }
        catch (_) { optionMessage = '当前剩余分包数量或接收责任暂未核验，请刷新后再准备交运。' }
      }
      await current()
      this._person = person; this._version = version; this._context = context; this._matches = matches; this._choices = choices
      this.setData({ loading: false, ready: true, number: original.operation_no, target: original.destination.target_location_name,
        transit: original.destination.transit_location_name, status: STATUS[history.shipment_status], outboundStatus: DEPARTURE[history.departures.outbound_status],
        cancellation: history.departures.cancellation ? { ...history.departures.cancellation, cancelledAtLabel: displayTime(history.departures.cancellation.cancelled_at) } : null,
        items: choices ? choices.lines.map(row => ({ ...row, condition: conditionLabel(row.condition_code), outboundAtLabel: displayTime(row.outbound_at), selectable: row.selectable_quantity !== '0.000' })) : [],
        canSubmit: !!(choices && choices.lines.some(row => row.selectable_quantity !== '0.000')),
        noChoices: !!(choices && choices.lines.every(row => row.selectable_quantity === '0.000')),
        history: history.items.map(row => ({ id: row.shipment_id, number: row.shipment_no, carrier: row.carrier, trackingNo: row.tracking_no,
          shippedAt: displayTime(row.shipped_at), recordedAt: displayTime(row.recorded_at), reason: row.reason, rows: reviewRows(row.lines) })),
        message: optionMessage || '按原发出批次登记交给承运商的物料。接收仓仍需独立验收和入账；个人保管责任在交接前保留。' })
      this.refreshPending(access)
    } catch (_) { if (active()) { this._choices = null; this._context = null; this._matches = null; this.clearDraft();
      this.setData({ ...empty(), message: '原退回分包数据或权限暂时无法核验，请刷新；本机恢复记录仍保留。' }) } }
  },
  refreshPending(access) {
    const record = this._store.read({ work_order_id: this._order })
    const own = record.kind === 'valid' && record.value.person_id === this._person && record.value.kind === contract.KIND
    this.setData({ pending: own, canSeal: !!(own && hasFormalPermission(access, 'stock_operation', record.value.operation_type)),
      pendingMessage: own ? '该工单有退回操作结果待确认，请先读取原请求，不要重复登记。'
        : record.kind !== 'missing' ? '该工单有其他操作待核验，或恢复记录不可读取，请回到工单处理原请求。' : '' })
  },
  draftReady() { return this._visible && this.data.ready && this.data.canSubmit && !this.data.busy && !this.data.loading
    && this._matches && this._matches() && this._store.read({ work_order_id: this._order }).kind === 'missing' },
  renderDraft() {
    this._revision++
    this.setData({ feedback: '', draftRows: Object.entries(this._drafts).map(([id, value]) => {
      const row = this._choices.lines.find(item => item.outbound_line_id === id)
      return { id, outboundNo: row.outbound_no, materialName: row.material_name, sku: row.sku_code, maximum: row.selectable_quantity, unit: row.base_unit,
        tracked: contract.tracked(row), quantity: value.quantity, selectedCount: value.serial_ids.length,
        serialOptions: row.serials.map(sn => ({ id: sn.serial_id, number: sn.serial_no, checked: value.serial_ids.includes(sn.serial_id) })) }
    }) })
  },
  addSource(event) { if (!this.draftReady()) return; const id = event.currentTarget.dataset.id;
    if (!this._choices.lines.some(row => row.outbound_line_id === id && row.selectable_quantity !== '0.000') || this._drafts[id]) return
    this._drafts[id] = { quantity: '', serial_ids: [] }; this.renderDraft() },
  removeSource(event) { if (!this.draftReady()) return; delete this._drafts[event.currentTarget.dataset.id]; this.renderDraft() },
  editQuantity(event) { if (!this.draftReady()) return; const row = this._drafts[event.currentTarget.dataset.id]; if (!row) return;
    row.quantity = String(event.detail.value || '').slice(0, 19); this.renderDraft() },
  chooseSerials(event) {
    if (!this.draftReady()) return
    const id = event.currentTarget.dataset.id, row = this._choices.lines.find(item => item.outbound_line_id === id), ids = event.detail.value
    if (!row || !this._drafts[id] || !contract.tracked(row) || !Array.isArray(ids) || new Set(ids).size !== ids.length
      || ids.some(value => !row.serials.some(sn => sn.serial_id === value))) return
    this._drafts[id].serial_ids = ids.slice(); this.renderDraft()
  },
  editCarrier(event) { this.editText('carrier', event, 100) },
  editTrackingNo(event) { this.editText('trackingNo', event, 100) },
  editReason(event) { this.editText('reason', event, 500) },
  editText(field, event, limit) { if (!this.draftReady() || !['carrier', 'trackingNo', 'reason'].includes(field)) return;
    this._revision++; this.setData({ [field]: [...String(event.detail.value || '')].slice(0, limit).join(''), feedback: '' }) },
  chooseDate(event) { if (!this.draftReady()) return; const value = event.detail.value; if (!/^\d{4}-\d\d-\d\d$/.test(value)) return;
    this._revision++; this.setData({ date: value, feedback: '' }) },
  chooseTime(event) { if (!this.draftReady()) return; const value = event.detail.value; if (!/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(value)) return;
    this._revision++; this.setData({ clock: value, feedback: '' }) },
  editSeconds(event) { if (!this.draftReady()) return; this._revision++;
    this.setData({ seconds: String(event.detail.value || '').slice(0, 2), feedback: '' }) },
  holdReview() {},
  confirmSubmission() { if (!this._visible || !this.data.confirming || !this._matches || !this._matches()) return this.finishReview(false); this.finishReview(true) },
  cancelSubmission() { this.finishReview(false) },
  preview() { return this.perform('preview') },
  submit() { return this.perform(contract.ACTION) },
  recover() { return this.perform('recover') },
  seal() { return this.perform('seal') },
  async perform(kind) {
    if (!this._visible || this.data.busy || this.data.loading || !this._context || !this._matches || !this._matches()) return
    if (['preview', contract.ACTION].includes(kind) && !this.draftReady()) return
    if (['recover', 'seal'].includes(kind) && (!this.data.pending || kind === 'seal' && !this.data.canSeal)) return
    const generation = this._generation, revision = this._revision, context = this._context, matches = this._matches
    const active = () => this._visible && generation === this._generation
    const authorize = async () => {
      if (!active() || revision !== this._revision || !matches()) throw new Error('session changed')
      const result = await context(), access = JSON.parse(result).access
      const action = kind === 'preview' ? contract.ACTION : kind === 'seal' ? this._store.read({ work_order_id: this._order }).value.operation_type : kind
      if (action !== 'recover' && !hasFormalPermission(access, 'stock_operation', action)) throw new Error('access changed')
      return result
    }
    const args = { api, store: this._store, workOrderId: this._order, operationId: this._operation, personId: this._person, authorizationVersion: this._version,
      authorize, confirm: review => new Promise(resolve => { this._confirmFinish = resolve; this.setData({ confirming: true, review }) }) }
    this.setData({ busy: true, feedback: '正在核验原发出、分包数量和当前权限。' }); let feedback
    try {
      let result
      if (['preview', contract.ACTION].includes(kind)) {
        if (!this.data.date || !this.data.clock) throw new Error('请选择实际交运的日期和时间。')
        if (!/^(?:[0-5]?\d)$/.test(this.data.seconds)) throw new Error('交运时间的秒数应为 00–59。')
        Object.assign(args, { shippedAt: `${this.data.date}T${this.data.clock}:${this.data.seconds.padStart(2, '0')}+08:00`, carrier: this.data.carrier, trackingNo: this.data.trackingNo, reason: this.data.reason, drafts: this._drafts })
        if (kind === 'preview') {
          const before = await authorize(), prepared = await prepareShipment({ ...args, current: async () => { if (await authorize() !== before) throw new Error('access changed') } })
          feedback = `已核验 ${prepared.review.rows.length} 行本次分包，尚未登记运单。提交前会再次核验并展示整组明细。`
        } else result = await submitShipment(args)
      } else result = await (kind === 'seal' ? sealPending : recoverPending)({ ...args, confirm: () => new Promise((resolve, reject) => wx.showModal({
        title: '结束原请求', content: '先核验原请求。已经执行则返回原事实；尚未执行则永久封存，迟到请求也无法再执行。',
        confirmText: '确认核验', success: value => resolve(value.confirm === true), fail: reject })) })
      if (result) feedback = result.status === 'confirmed' ? result.command.status === 'shipped' ? '本次分包交运已确认，接收仓尚需独立验收和入账。'
        : '原退回操作结果已确认，请按原记录查看。' : result.status === 'sealed' ? '原请求已永久封存且未执行，可重新准备分包。'
          : result.status === 'cancelled' ? '已停止本次操作。' : '结果仍待确认，请读取原请求，不要再次登记。'
    } catch (error) {
      if (['session changed', 'access changed'].includes(error.message)) { if (active()) { this.clearView(); this.setData({ message: '身份或权限已变化，请重新进入；原请求仍保留。' }) } }
      else feedback = error.message || '本次分包未完成核验，请读取原请求。'
    } finally {
      if (active()) {
        if (!matches()) { this.clearView(); this.setData({ message: '身份已变化，请重新进入分包页面。' }) }
        else if (kind !== 'preview') { this.clearDraft(); await this.load(); if (this._visible) this.setData({ feedback }) }
        else this.setData({ busy: false, feedback })
      }
    }
  }
})
