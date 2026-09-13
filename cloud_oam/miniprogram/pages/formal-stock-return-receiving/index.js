const api = require('../../utils/api')
const session = require('../../utils/session')
const { uuid } = require('../../utils/work-order-query-contract')
const { inventoryAccessDecision, hasFormalPermission } = require('../../utils/production-guard')
const contract = require('../../utils/stock-return-receiving-contract')
const { displayTime } = require('../../utils/stock-return-shipment-submit')
const { conditionLabel } = require('../../utils/inventory-contract')
const READ = { method: 'GET', noRefresh: true, header: { 'Cache-Control': 'no-store', Pragma: 'no-cache' } }
const EXCEPTIONS = { shortage: '短少', damaged: '破损', wrong_material: '错料', wrong_serial: '错 SN', rejected: '拒收' }
const NOTICE = '验收记录与库存入账分别保存。短少数量仍待确认；是否入账和完成保管责任交接，须核验各自记录。'
function empty() { return { ready: false, loading: false, message: '', detail: false, packages: [], next: false,
  package: null, progress: [], moreLines: false, batches: [], moreBatches: false, selectedReceipt: null } }
Page({
  data: empty(),
  onLoad(options = {}) { try { this._shipment = options.shipmentId ? uuid(options.shipmentId) : null }
    catch (_) { this._invalid = true } },
  onShow() { this._visible = true; return this.load() },
  onHide() { this.clearView() },
  onUnload() { this.clearView() },
  clearView() { this._visible = false; this._generation = (this._generation || 0) + 1;
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
    this._history = null; this._directory = null; this._matches = null; this._context = null; this._selected = null
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
      const before = await context(), expected = { personId: person, authorizationVersion: version, shipmentId: this._shipment }
      const prefix = '/v1/stock-returns/my-receiving'
      const raw = await api.request(this._shipment ? `${prefix}/${this._shipment}/receipts`
        : `${prefix}?limit=10${afterId ? '&after_id=' + uuid(afterId) : ''}`, READ)
      const result = this._shipment ? contract.validateHistory(raw, expected) : contract.validateDirectory(raw, expected, afterId)
      if (await context() !== before) throw new Error('access changed')
      this._matches = matches; this._context = context
      if (this._shipment) { this._history = result; this.renderHistory() }
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
