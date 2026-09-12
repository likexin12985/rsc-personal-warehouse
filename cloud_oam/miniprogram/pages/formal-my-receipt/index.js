const api = require('../../utils/api')
const session = require('../../utils/session')
const { formalMaterialRequestAdapter: identityAdapter } = require('../../utils/material-request-adapter')
const { uuid } = require('../../utils/my-receiving-contract')
const { validateCandidates, matchCandidateScan } = require('../../utils/my-receipt-candidates-contract')
const command = require('../../utils/my-receipt-command')
const recovery = require('../../utils/my-receipt-recovery-store')
const uploads = require('../../utils/formal-file-upload')

function empty() { return { state: 'idle', loading: false, scanning: false, busy: false, canReceive: false, canSubmit: false, canScan: false, requestNo: '', shipmentNo: '', targetLocationName: '', checkedAt: '', lines: [], matchedCount: 0, message: '', blockedMessage: '', conditionLabels: command.LABELS, receiptNo: '' } }
function signature(candidate) { const { checkedAt, ...stable } = candidate; return command.canonical(stable) }
const NO_STORE = { method: 'GET', noRefresh: true, header: { 'Cache-Control': 'no-store', Pragma: 'no-cache' } }
// Only these service rejections occur after the original key/trace checks and
// before any receipt facts. The route rolls back before returning them. Generic
// HTTP errors and an unobserved trace never prove that the command was rejected.
function definitiveRejection(error) {
  if (!error || error.responseReceived !== true) return false
  const conflicts = ['my_receipt_version_conflict', 'my_receipt_evidence_already_bound']
  const preconditions = ['my_receipt_state_invalid', 'my_receipt_shipment_not_handed_over', 'my_receipt_time_invalid',
    'my_receipt_line_invalid', 'my_receipt_quantity_exceeded', 'my_receipt_policy_invalid', 'my_receipt_quantity_invalid',
    'my_receipt_serial_invalid', 'my_receipt_evidence_unavailable']
  return (error.status === 409 && error.category === 'conflict' && conflicts.includes(error.code))
    || (error.status === 412 && error.category === 'precondition_failed' && preconditions.includes(error.code))
}

Page({
  data: empty(),
  onLoad(options) {
    try { this._requestId = uuid(options.request_id); this._shipmentId = uuid(options.shipment_id) } catch (_) { this._requestId = null; this._shipmentId = null }
    this._store = recovery.getStore(); this._files = {}; this._drafts = {}
  },
  onShow() { this._visible = true; return this.load() },
  onHide() { this.clearView() },
  onUnload() { this.clearView() },
  clearView() {
    this._visible = false; this._generation = (this._generation || 0) + 1
    this.resetDrafts(); this._pending = null
    this._candidate = null; this._session = null; this._matched = new Set(); this._limits = {}
    this.setData(empty())
  },
  resetDrafts() {
    const previous = this._files || {}; this._files = {}; this._drafts = {}
    Object.values(previous).forEach(controller => controller.clear())
  },
  anchors() { return { request_id: this._requestId, shipment_id: this._shipmentId } },
  invalidateContext() {
    this._generation = (this._generation || 0) + 1; this._candidate = null; this._session = null; this._matched = new Set(); this.resetDrafts()
    this.setData(Object.assign(empty(), { state: 'error', message: '身份或权限已变化，请刷新后重新核对。原验收恢复记录继续保留。' }))
  },
  onPullDownRefresh() { return this.load().finally(() => wx.stopPullDownRefresh()) },
  refresh() { return this.load() },
  sameSession(snapshot) {
    const user = session.getUser()
    return !!user && session.getToken() === snapshot.token && user.person_id === snapshot.person && user.authorization_version === snapshot.version
  },
  async authority(snapshot, current) {
    try {
      if (!current() || !this.sameSession(snapshot)) throw new Error('context changed')
      const identity = await identityAdapter.loadIdentityNoReplay()
      if (!current() || !this.sameSession(snapshot)) throw new Error('context changed')
      const access = await identityAdapter.loadAccessNoReplay(identity)
      if (!current() || !this.sameSession(snapshot) || identity.person_id !== snapshot.person || identity.authorization_version !== snapshot.version || !access.can_read || !access.can_read_material_catalog) throw new Error('no current access')
      return JSON.stringify({ identity, access })
    } catch (_) {
      const error = new Error('身份或读取权限已变化，请刷新后重新核对。')
      error.contextInvalid = true
      throw error
    }
  },
  async load() {
    if (!this._visible) return
    const generation = (this._generation || 0) + 1
    this._generation = generation; this._candidate = null; this._session = null; this._matched = new Set(); this._limits = {}
    this.resetDrafts(); this._pending = null
    const current = () => this._visible && generation === this._generation
    this.setData(Object.assign(empty(), { loading: true, state: 'loading', message: '正在核对本人包裹及未验收明细。' }))
    try {
      if (!this._requestId || !this._shipmentId || !session.ensureLogin()) throw new Error('请登录后从本人收货页面进入。')
      const user = session.getUser()
      const snapshot = { token: session.getToken(), person: uuid(user.person_id), version: user.authorization_version }
      const before = await this.authority(snapshot, current)
      if (!current()) return
      const stored = this._store.read(this.anchors())
      if (stored.kind === 'unavailable') throw new Error('验收恢复记录不可用，已停止新提交。')
      if (stored.kind === 'valid') {
        if (stored.value.person_id !== snapshot.person) throw new Error('原验收属于其他登录人员，请使用原身份核验。')
        this._pending = stored.value; this._session = snapshot
        this.setData({ state: 'pending', message: '有一笔验收结果待核验。请读取原结果，暂勿重复提交。' })
        return
      }
      const response = await api.request(`/v1/material-requests/${this._requestId}/my-receiving/${this._shipmentId}/candidates`, { method: 'GET', noRefresh: true, header: { 'Cache-Control': 'no-store', Pragma: 'no-cache' } })
      if (!current()) return
      const candidate = validateCandidates(response, this._requestId, this._shipmentId, snapshot.person)
      if (await this.authority(snapshot, current) !== before) throw new Error('查询期间权限已变化，请刷新。')
      if (!current()) return
      this._candidate = candidate; this._session = snapshot
      this._drafts = Object.fromEntries(candidate.lines.map(line => [line.shipment_line_id, { accepted: '', rejected: '', condition: 'normal', serials: {}, evidenceFileId: null }]))
      this.setData({ state: 'ready', requestNo: candidate.requestNo, shipmentNo: candidate.shipmentNo, targetLocationName: candidate.targetLocationName,
        checkedAt: candidate.checkedAt, blockedMessage: candidate.blockedMessage,
        message: '扫码只标记核对。填写本次合格和拒收结果后，单独提交验收；个人仓入账另行确认。' })
      this.renderLines()
    } catch (error) {
      if (current()) this.setData(Object.assign(empty(), { state: 'error', message: error.message || '身份、权限或包裹明细未通过确认，请刷新重试。' }))
    } finally { if (current()) this.setData({ loading: false }) }
  },
  renderLines() {
    const lines = this._candidate.lines.map(line => {
      const limit = this._limits[line.shipment_line_id] || 20
      const { remaining_serials, ...display } = line
      const draft = this._drafts[line.shipment_line_id]
      const fileState = this._files[line.shipment_line_id]?.snapshot() || { files: [], blocking: false, canChoose: true }
      return Object.assign({}, display, {
        enteredAccepted: draft.accepted, enteredRejected: draft.rejected,
        conditionIndex: command.CONDITIONS.indexOf(draft.condition), conditionLabel: command.LABELS[command.CONDITIONS.indexOf(draft.condition)],
        abnormal: draft.condition !== 'normal', uploadFiles: fileState.files, canChooseEvidence: fileState.canChoose,
        selectedAccepted: Object.values(draft.serials).filter(value => value === 'accepted').length,
        selectedRejected: Object.values(draft.serials).filter(value => value === 'rejected').length,
        shownSerials: line.remaining_serials.slice(0, limit).map(s => ({ serial_id: s.serial_id, serial_no: s.serial_no, checked: this._matched.has(s.serial_id), result: draft.serials[s.serial_id] || '' })),
        hasMore: limit < line.remaining_serials.length, totalSerials: line.remaining_serials.length })
    })
    const blocking = Object.values(this._files).some(controller => controller.snapshot().blocking)
    this.setData({ lines, matchedCount: this._matched.size, canScan: lines.some(line => line.totalSerials > 0), canReceive: this._candidate.canReceive, canSubmit: this._candidate.canReceive && !blocking })
  },
  showMore(event) {
    if (!this._candidate || !this._visible || !this.sameSession(this._session)) { this.clearView(); return }
    const id = event.currentTarget.dataset.lineId
    if (!this._candidate.lines.some(line => line.shipment_line_id === id)) return
    this._limits[id] = (this._limits[id] || 20) + 20; this.renderLines()
  },
  async scan() {
    if (!this._candidate || !this._visible || !this.data.canScan || this.data.scanning || this.data.loading || this.data.busy) return
    const generation = this._generation, snapshot = this._session
    const current = () => this._visible && generation === this._generation
    this.setData({ scanning: true })
    try {
      const before = await this.authority(snapshot, current)
      if (!current()) return
      const result = await new Promise((resolve, reject) => wx.scanCode({ onlyFromCamera: true, success: resolve, fail: () => reject(new Error('扫描已取消，可重新扫描。')) }))
      if (!current()) return
      if (await this.authority(snapshot, current) !== before) {
        const error = new Error('扫描期间权限已变化，请刷新。'); error.contextInvalid = true; throw error
      }
      if (!current()) return
      const matched = matchCandidateScan(this._candidate, result.result)
      const repeated = this._matched.has(matched.serialId)
      this._matched.add(matched.serialId); this.renderLines()
      this.setData({ message: repeated ? '该 SN 本次已核对，无需重复扫描。' : 'SN 与本包裹待验收明细一致，已标记本次核对。' })
    } catch (error) {
      if (!current()) return
      if (!this.sameSession(snapshot) || error.contextInvalid) {
        this.invalidateContext()
      } else this.setData({ message: error.message || '扫描核对失败，请刷新。' })
    } finally { if (current()) this.setData({ scanning: false }) }
  },
  editable() {
    if (this._session && !this.sameSession(this._session)) { this.invalidateContext(); return false }
    return this._visible && this.data.state === 'ready' && !this.data.busy && !!this._candidate?.canReceive
  },
  editQuantity(event) {
    if (!this.editable()) return
    const { lineId, field } = event.currentTarget.dataset
    const line = this._candidate.lines.find(item => item.shipment_line_id === lineId)
    if (!line || line.tracked || !['accepted', 'rejected'].includes(field) || typeof event.detail.value !== 'string' || event.detail.value.length > 20) return
    this._drafts[lineId][field] = event.detail.value; this.renderLines()
  },
  changeCondition(event) {
    if (!this.editable()) return
    const id = event.currentTarget.dataset.lineId, condition = command.CONDITIONS[Number(event.detail.value)]
    if (!this._drafts[id] || !condition) return
    if (condition === 'normal' && this._files[id]?.snapshot().blocking) { this.setData({ message: '异常凭证尚未确认，请先处理原上传。' }); return }
    this._drafts[id].condition = condition
    if (condition === 'normal') { this._drafts[id].evidenceFileId = null; this._files[id]?.clear() }
    this.renderLines()
  },
  chooseSerial(event) {
    if (!this.editable()) return
    const { lineId, serialId, result } = event.currentTarget.dataset
    const line = this._candidate.lines.find(item => item.shipment_line_id === lineId)
    if (!line?.remaining_serials.some(serial => serial.serial_id === serialId) || !['accepted', 'rejected', ''].includes(result)) return
    if (result === 'accepted' && !this._matched.has(serialId)) { this.setData({ message: '该 SN 尚未扫码核对，不能登记为合格。' }); return }
    if (result) this._drafts[lineId].serials[serialId] = result
    else delete this._drafts[lineId].serials[serialId]
    this.renderLines()
  },
  fileController(id) {
    if (this._files[id]) return this._files[id]
    const generation = this._generation, snapshot = this._session
    const current = () => this._visible && this._generation === generation && this.sameSession(snapshot)
    const controller = uploads.createFormalFileUploadController({
      purpose: 'receipt_exception_evidence', multiple: false,
      execute: prepared => uploads.executeFormalFileUpload(prepared, {
        requestApi: async (path, options) => {
          const before = await this.authority(snapshot, current)
          if (!current()) throw new Error('附件上下文已变化。')
          const result = await api.request(path, Object.assign({}, options, { noRefresh: true }))
          if (await this.authority(snapshot, current) !== before) throw new Error('上传期间权限已变化。')
          return result
        }
      }),
      onChange: state => {
        if (!current() || !this._drafts[id]) return
        this._drafts[id].evidenceFileId = state.availableFiles[0]?.file_id || null
        this.renderLines()
      }
    })
    this._files[id] = controller
    controller.bind(`${snapshot.person}:${snapshot.version}:${this._requestId}:${this._shipmentId}:${id}`)
    return controller
  },
  async evidenceAction(event) {
    if (!this.editable()) return
    const { lineId, action, fileKey } = event.currentTarget.dataset
    if (!this._drafts[lineId] || this._drafts[lineId].condition === 'normal') return
    const generation = this._generation, snapshot = this._session
    const current = () => this._visible && this._generation === generation && this.sameSession(snapshot)
    try {
      await this.authority(snapshot, current)
      const controller = this.fileController(lineId)
      if (action === 'retry') await controller.retry(fileKey)
      else if (action === 'remove') controller.remove(fileKey)
      else if (action === 'select') await controller.select()
    } catch (error) { if (current()) this.setData({ message: error.message || '异常凭证未完成确认。' }) }
    finally { if (this._visible && generation === this._generation && !this.sameSession(snapshot)) this.invalidateContext() }
  },
  async latestCandidate(snapshot, current) {
    const before = await this.authority(snapshot, current)
    const raw = await api.request(`/v1/material-requests/${this._requestId}/my-receiving/${this._shipmentId}/candidates`, NO_STORE)
    if (await this.authority(snapshot, current) !== before) throw new Error('核验期间权限已变化，请刷新。')
    const latest = validateCandidates(raw, this._requestId, this._shipmentId, snapshot.person)
    if (signature(latest) !== signature(this._candidate)) throw new Error('包裹或需求已变化，请刷新后重新核对。')
    return latest
  },
  confirmed(result) {
    this._pending = null; this._candidate = null; this.resetDrafts()
    this.setData(Object.assign(empty(), { state: 'confirmed', receiptNo: result.receipt_no,
      message: '本次验收已登记并核验。个人仓尚需独立入账，请勿把验收记录当作库存增加。' }))
  },
  openInbounds() {
    if (!this._visible || this.data.busy || this.data.state !== 'confirmed') return
    if (!this._session || !this.sameSession(this._session)) { this.invalidateContext(); return }
    wx.navigateTo({ url: `/pages/formal-my-inbound/index?request_id=${this._requestId}` })
  },
  async recover() {
    if (!this._visible || this.data.busy || !this._pending || !this._session) return
    const generation = this._generation, snapshot = this._session
    const current = () => this._visible && generation === this._generation && this.sameSession(snapshot)
    this.setData({ busy: true })
    try {
      await this._store.withLease(this.anchors(), async lease => {
        const stored = lease.read()
        if (stored.kind !== 'valid' || stored.value.person_id !== snapshot.person) throw new Error('原验收恢复记录或身份不一致。')
        const marker = stored.value, before = await this.authority(snapshot, current)
        const raw = await api.request(`/v1/material-requests/${marker.request_id}/my-receipts/trace-status`, Object.assign({}, NO_STORE, {
          header: { 'X-Original-Request-ID': marker.trace_request_id, 'Cache-Control': 'no-store', Pragma: 'no-cache' }
        }))
        const result = command.validateLookup(raw, marker)
        if (await this.authority(snapshot, current) !== before) throw new Error('核验期间身份或权限已变化。')
        if (!current()) return
        if (!result) { this.setData({ message: '暂未读取到原验收结果，仍保留恢复记录；这不代表未执行，请稍后继续核验。' }); return }
        lease.clearExact(marker); this.confirmed(result)
      })
    } catch (error) { if (current()) this.setData({ message: error.message || '原验收结果尚未完成核验。' }) }
    finally {
      if (current()) this.setData({ busy: false })
      else if (this._visible && generation === this._generation && !this.sameSession(snapshot)) this.invalidateContext()
    }
  },
  async submit() {
    if (!this.editable() || !this.data.canSubmit || this.data.scanning) return
    const generation = this._generation, snapshot = this._session
    const current = () => this._visible && generation === this._generation && this.sameSession(snapshot)
    this.setData({ busy: true })
    try {
      const latest = await this.latestCandidate(snapshot, current)
      const payload = command.buildCommand(latest, this._drafts, new Date().toISOString())
      const summary = payload.lines.map(line => {
        const sku = latest.lines.find(item => item.shipment_line_id === line.shipment_line_id)
        return `${sku.sku_code}：合格 ${line.accepted_qty} / 拒收 ${line.rejected_qty} ${sku.base_unit}（${command.LABELS[command.CONDITIONS.indexOf(line.condition)]}）`
      }).join('\n')
      const accepted = await new Promise((resolve, reject) => wx.showModal({ title: '确认登记本次验收', content: `${summary}\n本次仅登记验收，个人仓入账独立处理。`, confirmText: '登记验收', success: result => resolve(result.confirm), fail: () => reject(new Error('验收确认窗口未完成。')) }))
      if (!accepted || !current()) return
      await this.latestCandidate(snapshot, current)
      const key = api.createIdempotencyKey(), trace = api.createRequestId()
      const marker = recovery.validateMarker(Object.assign(this.anchors(), { v: 1, kind: 'my_receipt', person_id: snapshot.person,
        authorization_version: snapshot.version, expected_request_version: payload.expected_request_version, received_at: payload.received_at,
        trace_request_id: trace, request_hash: command.requestHash(this._requestId, snapshot.person, payload) }))
      await this._store.withLease(this.anchors(), async lease => {
        if (!current()) return
        lease.persist(marker); this._pending = marker
        let result
        try {
          result = await api.request(`/v1/material-requests/${this._requestId}/my-receipts`, {
            method: 'POST', data: payload, noRefresh: true, requestId: trace, idempotencyKey: key,
            header: { 'Cache-Control': 'no-store' }
          })
        } catch (error) {
          if (current() && definitiveRejection(error)) {
            lease.clearExact(marker); this._pending = null; this._candidate = null; this.resetDrafts()
            this.setData(Object.assign(empty(), { state: 'error', busy: true }))
            throw new Error('本次验收已被拒绝，未登记。请刷新后重新核对包裹、数量和异常凭证。')
          }
          throw error
        }
        command.validateResult(result, marker)
        await this.authority(snapshot, current)
        if (!current()) return
        lease.clearExact(marker); this.confirmed(result)
      })
    } catch (error) {
      if (current()) {
        if (this._pending) { this._candidate = null; this.resetDrafts(); this.setData({ state: 'pending', lines: [], canSubmit: false, message: '提交结果尚未完成核验，原请求记录已保留。请读取原结果，暂勿再次提交。' }) }
        else this.setData({ message: error.message || '验收预检未通过，请重新核对。' })
      }
    } finally {
      if (current()) this.setData({ busy: false })
      else if (this._visible && generation === this._generation && !this.sameSession(snapshot)) this.invalidateContext()
    }
  }
})
