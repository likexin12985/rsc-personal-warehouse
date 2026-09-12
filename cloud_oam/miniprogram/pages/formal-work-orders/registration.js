const { submitRegistration } = require('../../utils/work-order-removed-registration-submit')
const { hasFormalPermission } = require('../../utils/production-guard')
module.exports = ({ api, session }) => ({
  async registerRemoved(event) {
    if (!this.removedReady() || !this._recoveryContext) return
    const row = this._removedDrafts.find(item => item.id === event.currentTarget.dataset.id)
    if (!row || !row.registrationAvailable || row.candidate) return
    const generation = this._generation, revision = this._draftRevision, order = this._selected
    const context = this._recoveryContext, matches = this._sessionMatches, person = this._recoveryPerson
    const active = () => this._visible && generation === this._generation
    const current = () => active() && matches() && revision === this._draftRevision
    this.setData({ busy: true, previewMessage: '正在核验拆回件身份，请确认登记内容。' })
    try {
      const result = await submitRegistration({ api, store: this._store, workOrderId: order, personId: person,
        authorizationVersion: session.getUser().authorization_version, row,
        authorize: async () => {
          if (!current()) throw new Error('session changed')
          const value = await context()
          if (!current()) throw new Error('session changed')
          if (!hasFormalPermission(JSON.parse(value).access, 'work_order_material', 'operate')) throw new Error('access changed')
          return value
        }, confirm: review => {
          if (!current()) return false
          return new Promise(resolve => {
            this._confirmFinish = resolve
            this.setData({ confirming: true, reviewTitle: review.title, reviewWorkOrderNo: review.workOrderNo,
              reviewDescription: review.description, reviewRows: review.rows, reviewPairs: [], previewMessage: '' })
          })
        }
      })
      if (!active()) return
      if (!matches()) { this.clearView(); return }
      if (result.status === 'cancelled') { this.setData({ previewMessage: '已取消登记，可继续核对拆回件。' }); return }
      if (result.status === 'confirmed') {
        row.registrationAvailable = false; row.candidate = null; row.installedSerialId = null
        this.renderDraft(); this.refreshPendingRequests()
        this.setData({ previewMessage: `拆回 SN 登记已确认：${result.command.registration_no}。库存未变动，请再次识别拆回件后完成配对。` })
        return
      }
      this.eraseDraft(); this.refreshPendingRequests()
      const stored = this._store.read({ work_order_id: order })
      this.setData({ canDraft: false, canRecover: stored.kind === 'valid' && stored.value.person_id === person,
        draftRows: [], previewMessage: '', recoveryMessage: result.status === 'sealed'
          ? '原登记请求已关闭且未执行，请刷新工单后重新准备。'
          : '登记结果尚未确认，已保留原请求。请先读取原结果，不要再次登记。' })
    } catch (error) {
      if (active()) {
        if (!matches() || ['session changed', 'access changed'].includes(error.message)) {
          this.clearView(); this.setData({ state: 'error', message: '身份或权限已变化，请重新进入工单页面。' })
        } else {
          const stored = this._store.read({ work_order_id: order })
          if (stored.kind === 'missing') this.setData({ previewMessage: error.message || '登记内容未通过核验，请核对后继续。' })
          else {
            this.eraseDraft(); this.refreshPendingRequests()
            this.setData({ canDraft: false, canRecover: stored.kind === 'valid' && stored.value.person_id === person,
              draftRows: [], previewMessage: '', recoveryMessage: '原登记结果或恢复记录尚未完成核验，请保留记录并读取原结果。' })
          }
        }
      }
    } finally {
      if (active()) {
        this.finishReview(false)
        if (!matches()) this.clearView()
        else this.setData({ busy: false })
      }
    }
  }
})
