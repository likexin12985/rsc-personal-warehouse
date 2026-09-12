const api = require('../../utils/api')
const session = require('../../utils/session')
const { formalMaterialRequestAdapter: identityAdapter } = require('../../utils/material-request-adapter')
const { uuid } = require('../../utils/my-receiving-contract')
const contract = require('../../utils/my-inbound-contract')
const recovery = require('../../utils/my-inbound-recovery-store')
const NO_STORE = { method: 'GET', noRefresh: true, header: { 'Cache-Control': 'no-store', Pragma: 'no-cache' } }
function empty() { return { state: 'idle', loading: false, busy: false, requestNo: '', items: [], canPost: false, hasNext: false, hasPrevious: false, pageNumber: 1, message: '', inboundNo: '' } }
function rejected(error) {
  return error?.responseReceived === true && ((error.status === 409 && error.category === 'conflict' && error.code === 'my_inbound_version_conflict')
    || (error.status === 412 && error.category === 'precondition_failed' && ['my_inbound_state_invalid', 'my_inbound_empty'].includes(error.code)))
}
Page({
  data: empty(),
  onLoad(options) { try { this._requestId = uuid(options.request_id) } catch (_) { this._requestId = null }; this._store = recovery.getStore(); this._cursors = [null] },
  onShow() { this._visible = true; this._cursors = [null]; return this.load() },
  onHide() { this.clearView() }, onUnload() { this.clearView() },
  clearView() { this._visible = false; this._generation = (this._generation || 0) + 1; this._candidate = null; this._session = null; this._pending = null; this.setData(empty()) },
  anchors() { return { request_id: this._requestId } },
  sameSession(s) { const u = session.getUser(); return !!u && session.getToken() === s.token && u.person_id === s.person && u.authorization_version === s.version },
  async authority(s, current) {
    if (!current() || !this.sameSession(s)) throw new Error('身份已变化，请刷新。')
    const identity = await identityAdapter.loadIdentityNoReplay()
    if (!current() || !this.sameSession(s)) throw new Error('身份已变化，请刷新。')
    const access = await identityAdapter.loadAccessNoReplay(identity)
    if (!current() || !this.sameSession(s) || identity.person_id !== s.person || identity.authorization_version !== s.version
      || !access.can_read || !access.can_read_material_catalog) throw new Error('身份或读取权限已变化，请刷新。')
    return JSON.stringify({ identity, access })
  },
  onPullDownRefresh() { return this.refresh().finally(() => wx.stopPullDownRefresh()) },
  refresh() { if (this.data.busy) return Promise.resolve(); this._cursors = [null]; return this.load() },
  async readCandidate(s, current) {
    const before = await this.authority(s, current), after = this._cursors[this._cursors.length - 1]
    const raw = await api.request(`/v1/material-requests/${this._requestId}/my-inbounds/candidates?limit=5${after ? '&after_id=' + after : ''}`, NO_STORE)
    if (await this.authority(s, current) !== before) throw new Error('查询期间权限已变化，请刷新。')
    const result = contract.validateCandidates(raw, this._requestId, s.person, after)
    if (result.items.length > 5 || (after && result.requestVersion !== this._version)) throw new Error('需求已变化，请刷新第一页。')
    return result
  },
  async load() {
    if (!this._visible || this.data.busy) return
    const generation = (this._generation || 0) + 1; this._generation = generation
    const current = () => this._visible && this._generation === generation
    this._candidate = null; this._session = null; this._pending = null; this._next = null
    this.setData({ ...empty(), state: 'loading', loading: true, message: '正在核验本人验收与个人仓入账记录。' })
    try {
      if (!this._requestId || !session.ensureLogin()) throw new Error('请登录后从本人收货进入。')
      const user = session.getUser(), s = { token: session.getToken(), person: uuid(user.person_id), version: user.authorization_version }
      await this.authority(s, current)
      const stored = this._store.read(this.anchors())
      if (stored.kind === 'unavailable') throw new Error('入账恢复记录不可用，暂勿重新提交。')
      if (stored.kind === 'valid') {
        if (stored.value.person_id !== s.person) throw new Error('存在其他人员的原入账，请使用原身份核验。')
        this._pending = stored.value; this._session = s
        this.setData({ state: 'pending', message: '有一笔入账结果待核验，请读取原结果，暂勿重复提交。' }); return
      }
      const candidate = await this.readCandidate(s, current)
      if (!current()) return
      this._candidate = candidate; this._session = s; this._version = candidate.requestVersion; this._next = candidate.nextAfterId
      this.setData({ state: 'ready', requestNo: candidate.requestNo, canPost: candidate.canPost,
        items: candidate.items.map(item => ({ ...item, detail: item.detail && { ...item.detail, lines: item.detail.lines.map(line => ({ ...line, shownSerials: line.accepted_serials.slice(0, 20), serialCount: line.accepted_serials.length })) } })),
        hasNext: !!this._next, hasPrevious: this._cursors.length > 1, pageNumber: this._cursors.length,
        message: candidate.canPost ? '按每次验收独立入账，仅合格数量及合格 SN 转入本人个人仓。拒收及异常继续单独处理。' : '当前仅可查看验收和入账记录，暂不能新增入账。' })
    } catch (error) {
      if (current()) { this._cursors = [null]; this._version = null; this.setData({ ...empty(), state: 'error', message: error.message || '入账记录未通过核验。' }) }
    } finally { if (current()) this.setData({ loading: false }) }
  },
  nextPage() { if (!this.data.loading && !this.data.busy && this._next && this.data.state === 'ready') { this._cursors.push(this._next); return this.load() } },
  previousPage() { if (!this.data.loading && !this.data.busy && this._cursors.length > 1) { this._cursors.pop(); return this.load() } },
  async latest(s, current) {
    const result = await this.readCandidate(s, current)
    if (contract.canonical(result) !== contract.canonical(this._candidate)) throw new Error('验收、入账或需求版本已变化，请刷新后重新核对。')
    return result
  },
  confirmed(result) { this._pending = null; this._candidate = null; this.setData({ ...empty(), state: 'confirmed', inboundNo: result.inbound_no, message: '本次个人仓入账已完成并核验。通知送达及 OAM 收货记录继续单独跟进。' }) },
  invalidate(s, generation) {
    if (this._visible && this._generation === generation && !this.sameSession(s)) {
      this._generation++; this._candidate = null; this._session = null; this._pending = null
      this.setData({ ...empty(), state: 'error', message: '身份已变化，请刷新。原入账恢复记录继续保留。' })
    }
  },
  async submit(event) {
    if (!this._visible || this.data.busy || this.data.loading || this.data.state !== 'ready' || !this._candidate?.canPost) return
    const id = event.currentTarget.dataset.receiptId
    if (!this._candidate.items.some(row => row.receipt_id === id && row.status === 'pending')) return
    const generation = this._generation, s = this._session
    const current = () => this._visible && this._generation === generation && this.sameSession(s)
    this.setData({ busy: true })
    try {
      const latest = await this.latest(s, current), row = latest.items.find(item => item.receipt_id === id)
      const body = contract.payload(latest, row)
      const summary = row.detail.lines.map(line => `${line.sku_code}：合格 ${line.accepted_qty} ${line.base_unit}，拒收 ${line.rejected_qty}`).join('\n')
      const yes = await new Promise((resolve, reject) => wx.showModal({ title: '确认本次个人仓入账',
        content: `${row.detail.receipt_no}\n${summary}\n仅合格部分转入 ${row.detail.target_location_name}。`, confirmText: '确认入账',
        success: result => resolve(result.confirm), fail: () => reject(new Error('确认窗口未完成。')) }))
      if (!yes || !current()) return
      await this.latest(s, current)
      const key = api.createIdempotencyKey(), trace = api.createRequestId()
      const marker = recovery.validateMarker({ v: 1, kind: 'my_inbound', request_id: this._requestId, receipt_id: id,
        person_id: s.person, authorization_version: s.version, expected_request_version: body.expected_request_version,
        receipt_request_hash: body.receipt_request_hash, trace_request_id: trace, request_hash: contract.requestHash(this._requestId, s.person, body) })
      await this._store.withLease(this.anchors(), async lease => {
        if (!current()) return
        lease.persist(marker); this._pending = marker
        let result
        try { result = await api.request(`/v1/material-requests/${this._requestId}/my-inbounds`, {
          method: 'POST', data: body, noRefresh: true, requestId: trace, idempotencyKey: key, header: { 'Cache-Control': 'no-store' }
        }) } catch (error) {
          if (current() && rejected(error)) {
            lease.clearExact(marker); this._pending = null; this._candidate = null
            this.setData({ ...empty(), state: 'error', busy: true })
            throw new Error('本次入账已被拒绝，请刷新后重新核对。')
          }
          throw error
        }
        contract.validateResult(result, marker)
        await this.authority(s, current)
        if (!current()) return
        lease.clearExact(marker); this.confirmed(result)
      })
    } catch (error) {
      if (current()) {
        if (this._pending) { this._candidate = null; this.setData({ state: 'pending', items: [], canPost: false, message: '入账结果尚未核验，原请求已保留。请读取原结果，暂勿重复提交。' }) }
        else this.setData({ message: error.message || '入账预检未通过，请刷新。' })
      }
    } finally { if (current()) this.setData({ busy: false }); this.invalidate(s, generation) }
  },
  async recover() {
    if (!this._visible || this.data.busy || !this._pending || !this._session) return
    const s = this._session, generation = this._generation
    const current = () => this._visible && this._generation === generation && this.sameSession(s)
    this.setData({ busy: true })
    try {
      await this._store.withLease(this.anchors(), async lease => {
        const stored = lease.read()
        if (stored.kind !== 'valid' || stored.value.person_id !== s.person) throw new Error('原入账记录或身份不一致。')
        const marker = stored.value, before = await this.authority(s, current)
        const raw = await api.request(`/v1/material-requests/${this._requestId}/my-inbounds/trace-status`, {
          ...NO_STORE, header: { ...NO_STORE.header, 'X-Original-Request-ID': marker.trace_request_id }
        })
        const result = contract.validateLookup(raw, marker)
        if (await this.authority(s, current) !== before) throw new Error('核验期间权限已变化。')
        if (!current()) return
        if (!result) { this.setData({ message: '暂未读取到原入账结果，仍保留恢复记录；这不代表未执行，请稍后继续核验。' }); return }
        lease.clearExact(marker); this.confirmed(result)
      })
    } catch (error) { if (current()) this.setData({ message: error.message || '原入账尚未完成核验。' }) }
    finally { if (current()) this.setData({ busy: false }); this.invalidate(s, generation) }
  }
})
