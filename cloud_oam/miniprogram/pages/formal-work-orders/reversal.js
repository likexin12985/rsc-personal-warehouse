const reversal = require('../../utils/work-order-reversal-command')
const { optionsFor } = require('../../utils/work-order-replacement-submit')
const { prepareReversal } = require('../../utils/work-order-reversal-submit')
const READ = { method: 'GET', noRefresh: true, header: { 'Cache-Control': 'no-store', Pragma: 'no-cache' } }

module.exports = ({ api, session }) => ({
  resetReversalDraft() {
    this._reversalOriginals = new Map(); this._reversalSelection = null
    this.setData({ reversalOriginals: [], reversalLoaded: false, reversalSelectedId: '', reversalReason: '' })
  },
  async loadReversalOriginals() {
    if (!this._visible || !this._selected || !this.data.workOrder || this.data.busy || this.data.loading
      || !this._recoveryContext || !this._sessionMatches || !this._sessionMatches()) return
    // A history refresh invalidates the previous selection and explanation.
    this.resetReversalDraft(); this._draftRevision++
    return this.runReplacementRead(async current => {
      const { expected } = await optionsFor({ api, workOrderId: this._selected, personId: this._recoveryPerson,
        authorizationVersion: session.getUser().authorization_version, current })
      const raw = await api.request(`/v1/work-orders/${this._selected}/material-reversals/originals`, READ)
      await current()
      const items = reversal.validateOriginals(raw, expected)
      this._reversalOriginals = new Map(items.map(row => [row.id, row]))
      this.setData({ reversalOriginals: items, reversalLoaded: true,
        previewMessage: items.length ? '原记录已核验。选择整笔记录并说明原因后，再核验当前是否允许冲销。' : '该工单暂无本人可读取的原操作。' })
    })
  },
  chooseReversalOriginal(event) {
    if (!this.draftReady()) return
    const id = event.currentTarget.dataset.id, row = this._reversalOriginals.get(id)
    if (!row || !row.selectable) return
    const originals = this._reversalOriginals, items = this.data.reversalOriginals
    this.eraseDraft()
    this._reversalOriginals = originals
    this._reversalSelection = Object.freeze({ original_operation_id: row.original_operation_id, original_replacement_id: row.original_replacement_id })
    this.setData({ operationKind: 'reverse', draftRows: [], reversalOriginals: items, reversalLoaded: true,
      reversalSelectedId: id, reversalReason: '', previewMessage: '请填写冲销原因；成对更换将作为整组处理。' })
  },
  editReversalReason(event) {
    if (!this.draftReady() || this.data.operationKind !== 'reverse' || !this._reversalSelection) return
    this._draftRevision++
    this.setData({ reversalReason: [...String(event.detail.value || '')].slice(0, 500).join(''), previewMessage: '' })
  },
  cancelReversal() {
    if (!this.draftReady() || this.data.operationKind !== 'reverse') return
    this.eraseDraft(); this.setData({ operationKind: 'occupy', draftRows: [], previewMessage: '' })
  },
  async previewReversal() {
    if (!this.draftReady() || !this._recoveryContext || this.data.operationKind !== 'reverse' || !this._reversalSelection) return
    return this.runReplacementRead(async current => {
      const result = await prepareReversal({ api, workOrderId: this._selected, personId: this._recoveryPerson,
        authorizationVersion: session.getUser().authorization_version, selection: this._reversalSelection,
        reason: this.data.reversalReason, current })
      this.setData({ previewMessage: `整笔冲销预检通过：${result.preview.children.length} 笔原操作、${result.review.rows.length} 行物料。库存尚未变动，提交前将再次核验并展示明细。` })
    })
  }
})
