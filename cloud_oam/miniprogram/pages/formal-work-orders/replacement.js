const { text } = require('../../utils/work-order-replacement-command')
const { tracked } = require('../../utils/work-order-draft')
const paired = require('../../utils/work-order-replacement-submit')

// The page owns physical drafts. Only this safe projection reaches setData;
// QR proofs are erased on navigation, mode changes and uncertain submission.
module.exports = ({ api, session, scanCode }) => ({
  renderRemovedDraft() {
    this._removedDrafts = (this._removedDrafts || []).filter(row => this._drafts[row.basisStockAccountId])
    const rows = this._removedDrafts.map(row => {
      const source = this.data.items.find(item => item.stock_account_id === row.basisStockAccountId)
      const installed = this._drafts[row.basisStockAccountId].serial_verifications
      if (!installed.some(proof => proof.serial_id === row.installedSerialId)) row.installedSerialId = null
      const candidate = row.candidate
      return { id: row.id, basisName: source.material_name, basisSku: source.sku_code,
        basisLineNo: Object.keys(this._drafts).indexOf(row.basisStockAccountId) + 1,
        condition: row.condition, quantity: row.quantity, lot: row.scan.lot_no || '',
        skuCaptured: !!row.scan.sku_code, snCaptured: !!row.scan.serial_no, qrCaptured: !!row.scan.qr_code,
        registrationAvailable: row.registrationAvailable === true, identified: !!candidate, materialName: candidate ? candidate.material_name : '', sku: candidate ? candidate.sku_code : '',
        unit: candidate ? candidate.base_unit : '', identifiedLot: candidate ? candidate.lot_no || '' : '',
        serialNo: candidate ? candidate.serial_no || '' : '', tracked: candidate ? tracked(candidate) : false,
        pairChoices: ['请选择对应的投入 SN', ...installed.map(proof => proof.serial_no)],
        pairIndex: installed.findIndex(proof => proof.serial_id === row.installedSerialId) + 1 }
    })
    this.setData({ removedRows: rows })
  },
  removedReady() { return this.draftReady() && this.data.operationKind === 'replace' },
  addRemoved(event) {
    if (!this.removedReady()) return
    const id = event.currentTarget.dataset.id
    if (!this._drafts[id] || this._removedDrafts.length >= 1000) return
    this._removedSequence = (this._removedSequence || 0) + 1
    this._removedDrafts.push({ id: `removed-${this._removedSequence}`, basisStockAccountId: id,
      condition: '', quantity: '', scan: {}, candidate: null, installedSerialId: null })
    this.renderDraft()
  },
  removeRemoved(event) {
    if (!this.removedReady()) return
    this._removedDrafts = this._removedDrafts.filter(row => row.id !== event.currentTarget.dataset.id)
    this.renderDraft()
  },
  editRemoved(event) {
    if (!this.removedReady()) return
    const { id, field } = event.currentTarget.dataset
    const row = this._removedDrafts.find(row => row.id === id)
    if (!row || !['condition', 'quantity', 'lot_no'].includes(field)) return
    const value = field === 'condition' ? event.currentTarget.dataset.value : String(event.detail.value || '')
    if (field === 'condition' && !['used', 'damaged'].includes(value)) return
    if (field === 'lot_no') row.scan.lot_no = value.slice(0, 160)
    else row[field] = value.slice(0, field === 'quantity' ? 19 : 10)
    if (field !== 'quantity') { row.candidate = null; row.registrationAvailable = false; row.installedSerialId = null }
    this.renderDraft()
  },
  chooseInstalled(event) {
    if (!this.removedReady()) return
    const row = this._removedDrafts.find(row => row.id === event.currentTarget.dataset.id)
    if (!row || !row.candidate || !tracked(row.candidate)) return
    const index = Number(event.detail.value)
    const choices = this._drafts[row.basisStockAccountId].serial_verifications
    if (!Number.isSafeInteger(index) || index < 0 || index > choices.length) return
    row.installedSerialId = index ? choices[index - 1].serial_id : null
    this.renderDraft()
  },
  async scanRemoved(event) {
    if (!this.removedReady()) return
    const { id, code } = event.currentTarget.dataset
    const row = this._removedDrafts.find(row => row.id === id)
    if (!row || !['sku_code', 'serial_no', 'qr_code'].includes(code)) return
    const generation = this._generation, revision = this._draftRevision, matches = this._sessionMatches
    const active = () => this._visible && generation === this._generation
    this.setData({ busy: true })
    try {
      const result = await new Promise((resolve, reject) => scanCode({ onlyFromCamera: true, success: resolve, fail: reject }))
      if (!active() || !matches() || revision !== this._draftRevision) return
      row.scan[code] = text(result && result.result, { sku_code: 80, serial_no: 200, qr_code: 250 }[code])
      row.candidate = null; row.registrationAvailable = false; row.installedSerialId = null
      this.renderDraft()
    } catch (_) { if (active() && matches()) this.setData({ previewMessage: '未采集到有效扫码内容，请重新扫描。' }) }
    finally { if (active()) { if (!matches()) this.clearView(); else this.setData({ busy: false }) } }
  },
  async inspectRemoved(event) {
    if (!this.removedReady() || !this._recoveryContext) return
    const row = this._removedDrafts.find(row => row.id === event.currentTarget.dataset.id)
    if (!row) return
    return this.runReplacementRead(async current => {
      const { options, expected } = await paired.optionsFor({ api, workOrderId: this._selected,
        personId: this._recoveryPerson, authorizationVersion: session.getUser().authorization_version, current })
      const basis = options.items.find(item => item.stock_account_id === row.basisStockAccountId)
      if (!options.openingEstablished || !options.workOrder.can_operate || !basis || !basis.allowed_actions.includes('replace')) throw new Error('对应投入物料已不可操作，请刷新后核对。')
      let candidate
      try { candidate = await paired.resolveRemoved({ api, row, expected, current }) } catch (error) {
        await current()
        row.candidate = null; row.installedSerialId = null
        row.registrationAvailable = error.responseReceived === true && error.status === 412 && error.code === 'removed_serial_not_found'
        this.renderDraft()
        throw error
      }
      row.candidate = candidate; row.registrationAvailable = false; row.installedSerialId = null
      if (tracked(candidate)) row.quantity = '1'
      this.renderDraft()
      this.setData({ previewMessage: tracked(candidate) ? '拆回件已识别，请明确选择对应的投入 SN。' : '拆回件已识别，请填写实际拆回数量。' })
    })
  },
  async previewReplacement() {
    if (!this.removedReady() || !this._recoveryContext) return
    return this.runReplacementRead(async current => {
      const result = await paired.prepareReplacement({ api, workOrderId: this._selected, personId: this._recoveryPerson,
        authorizationVersion: session.getUser().authorization_version, drafts: this._drafts, removedDrafts: this._removedDrafts, current })
      this.setData({ previewMessage: `整批预检通过：投入 ${result.preview.consume_line_count} 行、拆回 ${result.preview.recover_line_count} 行、SN 配对 ${result.preview.pair_count} 对。库存尚未变动。` })
    })
  },
  async runReplacementRead(action) {
    const generation = this._generation, revision = this._draftRevision, matches = this._sessionMatches, context = this._recoveryContext
    const active = () => this._visible && generation === this._generation
    this.setData({ busy: true, previewMessage: '正在重新核验物料，库存尚未变动。' })
    try {
      const before = await context()
      const current = async () => {
        if (!active() || !matches() || revision !== this._draftRevision) throw new Error('session changed')
        if (await context() !== before) throw new Error('access changed')
        if (!active() || !matches() || revision !== this._draftRevision) throw new Error('session changed')
      }
      await current()
      await action(current)
    } catch (error) {
      if (active()) {
        if (!matches() || ['session changed', 'access changed'].includes(error.message)) {
          this.clearView(); this.setData({ state: 'error', message: '身份或权限已变化，请重新进入工单页面。' })
        } else this.setData({ previewMessage: error.code === 'removed_serial_not_found'
          ? '三码未匹配已登记 SN，请先核对扫码内容；确认是未登记拆回件后，可登记其身份。'
          : error.message || '拆回件核验未通过，请核对后重试。' })
      }
    } finally { if (active()) { if (!matches()) this.clearView(); else this.setData({ busy: false }) } }
  }
})
