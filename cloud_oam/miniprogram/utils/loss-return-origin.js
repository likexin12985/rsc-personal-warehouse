// A loss return has explicit provenance; absence of a work order proves nothing.
const { uuid } = require('./work-order-query-contract')
const { exact } = require('./work-order-replacement-command')
const { canonical } = require('./work-order-command')
const IDS = ['loss_operation_id', 'loss_line_id', 'headquarters_decision_id', 'disposition_id']
function origin(value) {
  exact(value, ['origin_kind', ...IDS])
  if (value.origin_kind !== 'loss_report') throw new Error('报损来源未通过核验。')
  return Object.freeze({ origin_kind: 'loss_report', ...Object.fromEntries(IDS.map(key => [key, uuid(value[key])])) })
}
function isLoss(value) { return !!value && Object.prototype.hasOwnProperty.call(value, 'origin') }
function sourceFields(value) {
  if (isLoss(value)) {
    if (Object.prototype.hasOwnProperty.call(value, 'work_order_id')) throw new Error('退回来源冲突。')
    return { origin: origin(value.origin) }
  }
  return { work_order_id: uuid(value.work_order_id) }
}
function sourceKeys(value) { sourceFields(value); return [isLoss(value) ? 'origin' : 'work_order_id'] }
function sameSource(a, b) { return canonical(sourceFields(a)) === canonical(sourceFields(b)) }
function scope(workOrderId, lossOrigin, shipmentId) {
  return { ...(lossOrigin === undefined ? { work_order_id: uuid(workOrderId) } : { origin: origin(lossOrigin) }),
    ...(shipmentId ? { shipment_id: uuid(shipmentId) } : {}) }
}
module.exports = { origin, isLoss, sourceFields, sourceKeys, sameSource, scope }
