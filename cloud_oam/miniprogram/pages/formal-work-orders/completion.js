const { validateCompletion } = require('../../utils/work-order-completion-contract')
const READ = { method: 'GET', noRefresh: true, header: { 'Cache-Control': 'no-store', Pragma: 'no-cache' } }
module.exports = ({ api, session }) => ({
  async checkCompletion() {
    if (!this._visible || !this._selected || this.data.loading || this.data.busy || !this._recoveryContext || !this.data.workOrder) return
    const generation = this._generation, context = this._recoveryContext, matches = this._sessionMatches
    const order = this._selected, person = this._recoveryPerson, sourceVersion = this.data.workOrder.source_version
    const active = () => this._visible && generation === this._generation
    this.setData({ busy: true, completion: null, completionMessage: '正在核对占用、拆回和退回责任。' })
    try {
      const before = await context()
      const raw = await api.request(`/v1/work-orders/${order}/material-completion-check`, READ)
      if (!active() || !matches()) throw new Error('session changed')
      const result = validateCompletion(raw, { workOrderId: order, personId: person,
        authorizationVersion: session.getUser().authorization_version, sourceVersion })
      if (await context() !== before || !active() || !matches()) throw new Error('access changed')
      const pending = this._store.read({ work_order_id: order }).kind !== 'missing'
      this.setData({ completion: { ...result, clientPending: pending }, completionMessage: pending
        ? '本机还有待确认或不可读取的原请求，请先核验原结果。'
        : result.status === 'clear' ? '当前检查未见未完成物料项。此检查不会修改 OAM 工单状态。'
          : '仍有物料事项需要处理，请逐项核对后重新检查。' })
    } catch (error) {
      if (active()) {
        if (!matches() || ['session changed', 'access changed'].includes(error.message)) {
          this.clearView(); this.setData({ state: 'error', message: '身份或权限已变化，请重新进入工单页面。' })
        } else this.setData({ completion: null, completionMessage: '结束检查未完成，请重新读取；不能据此判断物料已处理完毕。' })
      }
    } finally {
      if (active()) { if (!matches()) this.clearView(); else this.setData({ busy: false }) }
    }
  }
})
