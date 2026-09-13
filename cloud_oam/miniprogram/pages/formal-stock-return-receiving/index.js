const api = require('../../utils/api')
const session = require('../../utils/session')
const { uuid } = require('../../utils/work-order-query-contract')
const { inventoryAccessDecision, hasFormalPermission } = require('../../utils/production-guard')
const contract = require('../../utils/stock-return-receiving-contract')
const { displayTime } = require('../../utils/stock-return-shipment-submit')
const { conditionLabel } = require('../../utils/inventory-contract')
const receiptSubmit = require('../../utils/stock-return-receipt-submit')
const receiptContract = require('../../utils/stock-return-receipt-contract')
const uploads = require('../../utils/formal-file-upload')
const { getStore } = require('../../utils/work-order-recovery-store')
const { recoverPending, sealPending } = require('../../utils/work-order-recovery')
const READ = { method: 'GET', noRefresh: true, header: { 'Cache-Control': 'no-store', Pragma: 'no-cache' } }
const EXCEPTIONS = { shortage: '短少', damaged: '破损', wrong_material: '错料', wrong_serial: '错 SN', rejected: '拒收' }
const NOTICE = '验收记录与库存入账分别保存。短少数量仍待确认；是否入账和完成保管责任交接，须核验各自记录。'
function empty() { return { ready: false, loading: false, busy: false, scanning: false, message: '', detail: false, packages: [], next: false,
  package: null, progress: [], moreLines: false, batches: [], moreBatches: false, selectedReceipt: null,
  formReady: false, formMessage: '', formReason: '', formReceivedAt: '', formLines: [], review: null, confirming: false,
  pending: false, canSeal: false, pendingMessage: '' } }
Page({
  data: empty(),
  onLoad(options = {}) { this._store = getStore(); this._files = {}; this._drafts = {}; try { this._shipment = options.shipmentId ? uuid(options.shipmentId) : null }
    catch (_) { this._invalid = true } },
  onShow() { this._visible = true; return this.load() },
  onHide() { this.clearView() },
  onUnload() { this.clearView() },
  clearView() { this._visible = false; this._generation = (this._generation || 0) + 1;
    Object.values(this._files || {}).forEach(controller => controller.clear()); this._files = {}; this._drafts = {};
    this._history = null; this._directory = null; this._matches = null; this._context = null; this._selected = null; this.setData(empty()) },
  onPullDownRefresh() { return this.load().finally(() => wx.stopPullDownRefresh()) },
  refresh() { return this.load() },
  nextPage() { if (this.readable() && this._directory && this._directory.next_after_id) return this.load(this._directory.next_after_id) },
  readable() {
    if (this._visible && this._matches && !this._matches()) { this.clearView(); this.setData({ message: '身份已变化，请重新进入退回收货。' }); return false }
    return this._visible && this.data.ready && !this.data.loading && this._matches && this._matches()
  },
  async load(afterId = null) {
    if (!this._visible) return
    const generation = (this._generation || 0) + 1; this._generation = generation
    const active = () => this._visible && this._generation === generation
    this._history = null; this._directory = null; this._matches = null; this._context = null; this._selected = null; this._session = null
    this._lineLimit = 20; this._batchLimit = 20; this._receiptLineLimit = 20; this._serialLimits = {}
    this.setData({ ...empty(), loading: true, detail: !!this._shipment, message: '正在核验本人接收仓、原包裹与验收记录。' })
    try {
      if (this._invalid || !session.ensureLogin()) throw new Error('login required')
      const token = session.getToken(), user = session.getUser(), person = uuid(user.person_id), version = user.authorization_version
      const matches = () => { try { const now = session.getUser(); return token === session.getToken() && uuid(now.person_id) === person && now.authorization_version === version } catch (_) { return false } }
      const context = async () => {
        if (!active() || !matches()) throw new Error('session changed')
        const current = await api.request('/auth/me', READ), access = await api.request('/access/context', READ)
        if (!active() || !matches() || uuid(current.person_id) !== person || current.authorization_version !== version
          || !Array.isArray(current.role_codes) || !current.role_codes.some(role => ['admin', 'provincial_manager'].includes(role))
          || !inventoryAccessDecision(access, current).allowed || !hasFormalPermission(access, 'stock_operation', 'read')) throw new Error('access changed')
        return JSON.stringify({ access, roles: current.role_codes })
      }
      const before = await context(); this._access = JSON.parse(before).access
      const expected = { personId: person, authorizationVersion: version, shipmentId: this._shipment }
      const prefix = '/v1/stock-returns/my-receiving'
      const raw = await api.request(this._shipment ? `${prefix}/${this._shipment}/receipts`
        : `${prefix}?limit=10${afterId ? '&after_id=' + uuid(afterId) : ''}`, READ)
      const result = this._shipment ? contract.validateHistory(raw, expected) : contract.validateDirectory(raw, expected, afterId)
      if (await context() !== before) throw new Error('access changed')
      this._matches = matches; this._context = context
      if (this._shipment) { this._history = result; this._session = { token, person, version }; this.renderHistory(); this.prepareForm(result, person, version) }
      else {
        this._directory = result
        this.setData({ packages: result.items.map(row => row.verification_status === 'verified'
          ? { id: row.shipment_id, verified: true, number: row.shipment_no, returnNo: row.operation_no, target: row.target_location_name,
            carrier: row.carrier, trackingNo: row.tracking_no, shippedAt: displayTime(row.shipped_at), lineCount: row.lines.length }
          : { id: row.shipment_id, verified: false, message: row.message }), next: result.next_after_id !== null })
      }
      this.setData({ ready: true, loading: false, message: NOTICE })
    } catch (_) { if (active()) { this._history = null; this._directory = null; this._matches = null; this._context = null;
      this.setData({ ...empty(), detail: !!this._shipment, message: '当前身份、接收责任或原包裹暂时无法核验，请刷新。' }) } }
  },
  openPackage(event) {
    if (!this.readable() || !this._directory) return
    const id = event.currentTarget.dataset.id
    if (!this._directory.items.some(row => row.shipment_id === id && row.verification_status === 'verified')) return
    wx.navigateTo({ url: `/pages/formal-stock-return-receiving/index?shipmentId=${id}` })
  },
  serialView(id, rows, group) {
    const key = `${group === 'remaining' ? 'progress' : this._selected}:${id}:${group}`, limit = this._serialLimits[key] || 20
    return { key, rows: rows.slice(0, limit).map(sn => ({ id: sn.serial_id, number: sn.serial_no })), more: limit < rows.length, total: rows.length }
  },
  renderHistory() {
    const history = this._history, parcel = history.package
    this.setData({ package: { number: parcel.shipment_no, returnNo: parcel.operation_no, target: parcel.target_location_name,
      carrier: parcel.carrier, trackingNo: parcel.tracking_no, shippedAt: displayTime(parcel.shipped_at) },
      progress: history.lines.slice(0, this._lineLimit).map(row => {
        const source = parcel.lines.find(line => line.shipment_line_id === row.shipment_line_id)
        return { id: row.shipment_line_id, materialName: source.material_name, sku: source.sku_code, unit: source.base_unit,
          condition: conditionLabel(source.condition_code), lot: source.lot_no, outboundNo: source.outbound_no,
          shipped: row.shipped_qty, accepted: row.accepted_qty, rejected: row.rejected_qty, damaged: row.damaged_qty,
          remaining: row.unconfirmed_qty, serials: this.serialView(row.shipment_line_id, row.unconfirmed_serials, 'remaining') }
      }), moreLines: this._lineLimit < history.lines.length,
      batches: history.receipts.slice(0, this._batchLimit).map(row => ({ id: row.receipt_id, number: row.receipt_no,
        status: row.status === 'accepted' ? '正常验收' : '含验收异常', receivedAt: displayTime(row.received_at), reason: row.reason })),
      moreBatches: this._batchLimit < history.receipts.length })
    this.renderReceipt()
  },
  prepareForm(history, person, version) {
    let stored = { kind: 'missing' }
    try { if (this._store) stored = this._store.read({ work_order_id: history.package.work_order_id, shipment_id: history.package.shipment_id }) } catch (_) { stored = { kind: 'missing' } }
    this._workOrder = history.package.work_order_id; this._operation = history.package.operation_id; this._person = person; this._version = version
    this._drafts = Object.fromEntries(history.package.lines.map(line => [line.shipment_line_id, {
      accepted_qty: '0.000', rejected_qty: '0.000', shortage_qty: '0.000', damaged_qty: '0.000', serials: {}, proofs: {}, damaged_serial_ids: [], exceptions: {}
    }]))
    this._context = this._context || (() => Promise.resolve(''))
    const canSeal = !!this._access && hasFormalPermission(this._access, 'stock_operation', 'receive_return')
    if (stored.kind === 'valid') this.setData({ formReady: false, pending: true, canSeal, pendingMessage: '有一笔验收结果待核验，请读取原请求，暂勿重复提交。' })
    else this.setData({ formReady: hasFormalPermission(this._access, 'stock_operation', 'receive_return'), formReceivedAt: new Date().toISOString(), formMessage: hasFormalPermission(this._access, 'stock_operation', 'receive_return') ? '扫码证明仅保留在当前页面内；验收记录不会直接增加个人仓库存。' : '当前账号只有退回包裹查询权限，不能登记验收。' })
    this.renderForm()
  },
  renderForm() {
    if (!this._history) return
    const byId = new Map(this._history.lines.map(row => [row.shipment_line_id, row]))
    const lines = this._history.package.lines.map(source => {
      const progress = byId.get(source.shipment_line_id), draft = this._drafts[source.shipment_line_id]
      if (!draft) return null
      const remaining = progress ? progress.unconfirmed_qty : source.shipped_quantity
      const serials = (progress ? progress.unconfirmed_serials : source.serials).map(sn => ({
        id: sn.serial_id, number: sn.serial_no, status: draft.serials[sn.serial_id] || '',
        scanned: !!draft.proofs[sn.serial_id], damaged: draft.damaged_serial_ids.includes(sn.serial_id)
      }))
      return { id: source.shipment_line_id, materialName: source.material_name, sku: source.sku_code, unit: source.base_unit,
        remaining, tracked: source.serials.length > 0, acceptedQty: draft.accepted_qty, rejectedQty: draft.rejected_qty,
        shortageQty: draft.shortage_qty, damagedQty: draft.damaged_qty, serials,
        exceptionChoices: receiptContract.TYPES.filter(type => !draft.exceptions[type]),
        exceptions: Object.entries(draft.exceptions).map(([type, value]) => ({ type, description: value.description || '', file: !!value.evidence_file_id })) }
    }).filter(Boolean)
    const blocking = Object.values(this._files).some(controller => controller.snapshot().blocking)
    this.setData({ formLines: lines, formReady: this.data.formReady && !blocking, formMessage: blocking ? '异常凭证尚未完成确认。' : this.data.formMessage })
  },
  editable() { return this._visible && this.data.detail && this.data.formReady && !this.data.busy && this._matches && this._matches() },
  editFormField(event) {
    if (!this.editable()) return
    const { id, field } = event.currentTarget.dataset
    if (!['accepted_qty', 'rejected_qty', 'shortage_qty', 'damaged_qty'].includes(field) || !this._drafts[id]) return
    const value = String(event.detail.value || '').slice(0, 19); if (!/^(?:0|[1-9][0-9]{0,14})(?:\.[0-9]{0,3})?$/.test(value || '0')) return
    this._drafts[id][field] = value || '0.000'; this.renderForm()
  },
  toggleSerial(event) {
    if (!this.editable()) return
    const { lineId, serialId } = event.currentTarget.dataset, draft = this._drafts[lineId]
    if (!draft) return
    const next = { '': 'accepted', accepted: 'rejected', rejected: 'shortage', shortage: '' }[draft.serials[serialId] || '']
    if (next === 'accepted' && !draft.proofs[serialId]) { this.setData({ formMessage: '请先完成该 SN 的物料码、SN、二维码三码扫描。' }); return }
    draft.serials[serialId] = next; if (next !== 'accepted') draft.damaged_serial_ids = draft.damaged_serial_ids.filter(id => id !== serialId); this.renderForm()
  },
  toggleDamaged(event) {
    if (!this.editable()) return
    const { lineId, serialId } = event.currentTarget.dataset, draft = this._drafts[lineId]
    if (!draft || draft.serials[serialId] !== 'accepted') return
    draft.damaged_serial_ids = draft.damaged_serial_ids.includes(serialId) ? draft.damaged_serial_ids.filter(id => id !== serialId) : draft.damaged_serial_ids.concat(serialId)
    draft.damaged_qty = `${draft.damaged_serial_ids.length}.000`; this.renderForm()
  },
  editFormReason(event) { if (this.editable()) this.setData({ formReason: String(event.detail.value || '').slice(0, 500) }) },
  async scanThree(event) {
    if (!this.editable() || this.data.scanning) return
    const { lineId, serialId, kind } = event.currentTarget.dataset, line = this._history.package.lines.find(row => row.shipment_line_id === lineId), draft = this._drafts[lineId]
    const source = line && line.serials.find(row => row.serial_id === serialId); if (!line || !source || !draft) return
    this.setData({ scanning: true })
    try {
      const result = await new Promise((resolve, reject) => wx.scanCode({ onlyFromCamera: true, success: resolve, fail: () => reject(new Error('扫描已取消。')) }))
      const value = String(result.result || '').trim(); if (!value) throw new Error('扫描结果为空。')
      const proof = draft.proofs[serialId] || { serial_id: serialId, sku_code: '', serial_no: '', qr_code: '' }
      if (kind === 'material' && value !== line.sku_code) throw new Error('物料码与本包裹明细不一致。')
      if (kind === 'serial' && value !== source.serial_no) throw new Error('SN 与本包裹明细不一致。')
      if (kind === 'qr' && value.length > 250) throw new Error('二维码内容过长。')
      proof[kind === 'material' ? 'sku_code' : kind === 'serial' ? 'serial_no' : 'qr_code'] = value; draft.proofs[serialId] = proof
      this.setData({ formMessage: proof.sku_code && proof.serial_no && proof.qr_code ? '三码已核验，可将该 SN 标记为接受。' : '已记录本次扫描，请完成剩余三码。' }); this.renderForm()
    } catch (error) { this.setData({ formMessage: error.message || '扫描核验失败。' }) }
    finally { this.setData({ scanning: false }) }
  },
  fileController(lineId, type) {
    const key = `${lineId}:${type}`; if (this._files[key]) return this._files[key]
    const snapshot = this._session, generation = this._generation, current = () => this._visible && this._generation === generation && this._matches && this._matches()
    const controller = uploads.createFormalFileUploadController({ purpose: 'receipt_exception_evidence', multiple: false,
      execute: prepared => uploads.executeFormalFileUpload(prepared, { requestApi: (path, options) => api.request(path, { ...options, noRefresh: true }) }),
      onChange: state => { if (!current()) return; const draft = this._drafts[lineId]; if (draft) draft.exceptions[type] = { description: draft.exceptions[type]?.description || '', evidence_file_id: state.availableFiles[0]?.file_id || null }; this.renderForm() } })
    controller.bind(`${snapshot.person}:${snapshot.version}:${this._shipment}:${lineId}:${type}`); this._files[key] = controller; return controller
  },
  editException(event) { if (!this.editable()) return; const { lineId, type } = event.currentTarget.dataset, draft = this._drafts[lineId]; if (!draft) return; draft.exceptions[type] = { ...(draft.exceptions[type] || {}), description: String(event.detail.value || '').slice(0, 1000) }; this.renderForm() },
  addException(event) { if (!this.editable()) return; const { lineId, type } = event.currentTarget.dataset, draft = this._drafts[lineId]; if (!draft || !receiptContract.TYPES.includes(type)) return; draft.exceptions[type] = draft.exceptions[type] || { description: '', evidence_file_id: null }; this.renderForm() },
  async evidence(event) { if (!this.editable()) return; const { lineId, type, action, fileKey } = event.currentTarget.dataset; try { const controller = this.fileController(lineId, type); if (action === 'select') await controller.select(); else if (action === 'retry') await controller.retry(fileKey); else controller.remove(fileKey) } catch (error) { this.setData({ formMessage: error.message || '异常凭证未完成确认。' }) } },
  async recoverReceipt() { if (!this.data.pending || this.data.busy || !this._context) return; this.setData({ busy: true }); try { const result = await recoverPending({ api, store: this._store, workOrderId: this._workOrder, shipmentId: this._shipment, personId: this._person, authorize: this._context }); if (result.status === 'confirmed') { this.setData({ pending: false, pendingMessage: '原验收结果已确认，正在刷新包裹。' }); await this.load() } else if (result.status === 'pending') this.setData({ pendingMessage: '暂未读取到原验收结果；请保留恢复记录，稍后继续核验。' }) } catch (error) { this.setData({ pendingMessage: error.message || '原验收结果尚未完成核验。' }) } finally { this.setData({ busy: false }) } },
  async sealReceipt() { if (!this.data.pending || !this.data.canSeal || this.data.busy) return; const ok = await new Promise(resolve => wx.showModal({ title: '封存未执行验收', content: '仅在确认原请求未执行后封存。结果未知时不要封存。', confirmText: '确认封存', success: value => resolve(value.confirm === true), fail: () => resolve(false) })); if (!ok) return; this.setData({ busy: true }); try { const result = await sealPending({ api, store: this._store, workOrderId: this._workOrder, shipmentId: this._shipment, personId: this._person, authorize: this._context, confirm: () => true }); if (result.status === 'sealed') await this.load() } catch (error) { this.setData({ pendingMessage: error.message || '封存未完成，请保留恢复记录。' }) } finally { this.setData({ busy: false }) } },
  async submitReceipt() {
    if (!this.editable()) return
    this.setData({ busy: true }); const generation = this._generation, session = this._session, current = () => this._visible && generation === this._generation && this._matches && this._matches()
    try { if (!this.data.formReason.trim()) throw new Error('请填写本次验收原因。'); const prepared = await receiptSubmit.prepareReceipt({ api, current, shipmentId: this._shipment, personId: this._person, authorizationVersion: this._version, receivedAt: this.data.formReceivedAt, reason: this.data.formReason, drafts: this._drafts }); this._confirmFinish = null; const approved = await new Promise(resolve => { this._confirmFinish = resolve; this.setData({ confirming: true, review: prepared.review }) }); this.setData({ confirming: false, review: null }); if (!approved || !current()) return; const result = await receiptSubmit.submitReceipt({ api, store: this._store, workOrderId: this._workOrder, shipmentId: this._shipment, personId: this._person, authorizationVersion: this._version, receivedAt: this.data.formReceivedAt, reason: this.data.formReason, drafts: this._drafts, authorize: this._context, confirm: async () => true }); if (result.status === 'confirmed') await this.load(); else if (result.status === 'pending') this.setData({ pending: true, formReady: false, pendingMessage: '提交结果尚未完成核验，原请求记录已保留；请读取原结果，暂勿重复提交。' }) } catch (error) { if (current()) this.setData({ formMessage: error.message || '验收预检未通过，请重新核对。' }) } finally { if (current()) this.setData({ busy: false }); else if (this._visible && session && !this._matches()) this.clearView() }
  },
  confirmSubmission(event) { if (this._confirmFinish) { const value = event.currentTarget.dataset.confirm === 'true'; const finish = this._confirmFinish; this._confirmFinish = null; this.setData({ confirming: false, review: null }); finish(value) } },
  renderReceipt() {
    const receipt = this._history.receipts.find(row => row.receipt_id === this._selected)
    this.setData({ selectedReceipt: !receipt ? null : { id: receipt.receipt_id, number: receipt.receipt_no,
      receivedAt: displayTime(receipt.received_at), recordedAt: displayTime(receipt.recorded_at), reason: receipt.reason,
      moreLines: this._receiptLineLimit < receipt.lines.length,
      lines: receipt.lines.slice(0, this._receiptLineLimit).map(row => ({ id: row.shipment_line_id, materialName: row.material_name,
        sku: row.sku_code, unit: row.base_unit, accepted: row.accepted_qty, rejected: row.rejected_qty,
        damaged: row.damaged_qty, shortage: row.shortage_qty,
        exceptions: row.exceptions.map(value => ({ type: value.exception_type, label: EXCEPTIONS[value.exception_type], description: value.description })),
        serials: ['accepted', 'rejected', 'shortage', 'damaged'].map(group => ({ group,
          label: { accepted: '本次接受', rejected: '本次拒收', shortage: '本次短少观察', damaged: '接受件中破损' }[group],
          ...this.serialView(row.shipment_line_id, group === 'damaged' ? row.accepted_serials.filter(sn => row.damaged_serial_ids.includes(sn.serial_id)) : row[`${group}_serials`], group) })) })) } })
  },
  selectReceipt(event) { if (!this.readable() || !this._history) return; const id = event.currentTarget.dataset.id
    if (!this._history.receipts.some(row => row.receipt_id === id)) return
    this._selected = id; this._receiptLineLimit = 20; this.renderReceipt() },
  moreLines() { if (this.readable() && this._history) { this._lineLimit += 20; this.renderHistory() } },
  moreBatches() { if (this.readable() && this._history) { this._batchLimit += 20; this.renderHistory() } },
  moreReceiptLines() { if (this.readable() && this._history) { this._receiptLineLimit += 20; this.renderReceipt() } },
  moreSerials(event) { if (!this.readable() || !this._history) return
    const key = event.currentTarget.dataset.key
    const groups = [...this.data.progress.map(row => row.serials), ...(this.data.selectedReceipt ? this.data.selectedReceipt.lines.flatMap(row => row.serials) : [])]
    if (!groups.some(row => row.key === key && row.more)) return
    this._serialLimits[key] = (this._serialLimits[key] || 20) + 20; this.renderHistory()
  }
})
