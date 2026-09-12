const reversal = require('../../utils/work-order-reversal-command')
const id = n => `abcdef00-0000-4000-8000-${String(n).padStart(12, '0')}`
const time = '2026-09-13T00:00:00Z'
function input(paired = false) { return { workOrderId: id(1), personId: id(2), authorizationVersion: 7,
  selection: { original_operation_id: paired ? null : id(3), original_replacement_id: paired ? id(4) : null }, reason: '核对实物后冲销🔧\n保留原始记录' } }
function order(value = input()) { return { work_order_id: value.workOrderId, work_order_no: 'WO-TEST', status: 'active', engineer_person_id: value.personId,
  organization_id: id(5), source_system: 'starcharge_oam', source_external_id: 'OAM-TEST', source_version: 'wo-v2:test',
  source_updated_at: time, synced_at: time, freshness: 'fresh', can_operate: true } }
function options(value = input()) { return { schema_version: '1.0', projection_status: 'ready', opening_balance_status: 'established', ledger_cursor: 8,
  projected_at: time, person_id: value.personId, authorization_version: value.authorizationVersion, work_order: order(value),
  location_id: id(6), location_code: 'PERSON-TEST', location_name: '测试个人仓', location_status: 'active', custody_effective_from: time, items: [] } }
function preview(value = input(), kind = 'consume', tracked = false) {
  const paired = !!value.selection.original_replacement_id
  function child(type, n) {
    const [from, to] = { occupy: ['reserved', 'available'], consume: [null, 'reserved'], release: ['available', 'reserved'], recover: ['available', null] }[type]
    return { original_operation_id: paired ? id(n) : value.selection.original_operation_id, original_operation_no: `WOM-${type}`, original_operation_type: type,
      original_transaction_id: id(n + 1), original_ledger_cursor: 7, movements: [{ original_movement_id: id(n + 2), line_no: 1,
        material_id: id(10), sku_code: 'SKU-测试', material_name: '测试物料', base_unit: '件', condition_code: type === 'recover' ? 'damaged' : 'new',
        lot_id: id(12), lot_no: 'LOT-01', from_account_id: from ? id(n + 3) : null, to_account_id: to ? id(n + 4) : null,
        from_bucket: from, to_bucket: to, quantity: '1.000', reservation_delta: type === 'occupy' ? '-1.000' : type === 'recover' ? '0.000' : '1.000',
        serials: tracked ? [{ serial_id: id(n + 5), serial_no: `SN-${n + 5}`, lifecycle_before: type === 'consume' ? 'consumed' : 'active', lifecycle_after: 'active',
          previous_movement_id: type === 'recover' ? null : id(n + 6), previous_ledger_cursor: type === 'recover' ? 0 : 6, registration_id: type === 'recover' ? id(n + 7) : null }] : [] }] }
  }
  return { schema_version: '1.0', planning_status: 'preview_only', operator_person_id: value.personId, authorization_version: value.authorizationVersion,
    work_order: order(value), ledger_cursor: 8, checked_at: time, ...value.selection, reason: value.reason,
    request_hash: reversal.requestHash(value), plan_hash: 'a'.repeat(64), children: paired ? [child('recover', 20), child('consume', 40)] : [child(kind, 20)],
    replacement_pairs: paired && tracked ? [{ installed_serial_id: id(45), removed_serial_id: id(25) }] : [] }
}
function marker(value = input()) { return { v: 1, kind: 'work_order_reversal', work_order_id: value.workOrderId, person_id: value.personId,
  authorization_version: value.authorizationVersion, operation_type: 'reverse', trace_request_id: 'wxreq-' + 'b'.repeat(36), request_hash: reversal.requestHash(value), plan_hash: 'a'.repeat(64) } }
function result(value = input(), plan = preview(value), request = marker(value)) { return { schema_version: '1.0', status: 'posted', reversal_id: id(80), reversal_no: 'WOR-TEST',
  work_order_id: value.workOrderId, operator_person_id: value.personId, request_id: request.trace_request_id, request_hash: request.request_hash, plan_hash: request.plan_hash,
  ...value.selection, reason: value.reason, posted_at: time, items: plan.children.map((child, index) => ({ original_operation_id: child.original_operation_id,
    original_transaction_id: child.original_transaction_id, inverse_operation_id: id(81 + index * 2), inverse_transaction_id: id(82 + index * 2) })) } }
function originals(value = input()) { return { schema_version: '1.0', person_id: value.personId, authorization_version: value.authorizationVersion, work_order: order(value),
  queried_at: time, ledger_cursor: 8, items: [{ ...value.selection, original_no: 'WOM-TEST', original_type: value.selection.original_replacement_id ? 'replace' : 'consume',
    posted_at: time, operation_count: value.selection.original_replacement_id ? 2 : 1, line_count: value.selection.original_replacement_id ? 2 : 1,
    material_names: ['测试物料'], reversal_id: null }] } }
function sealed(value = input(), request = marker(value)) { return { schema_version: '1.0', lookup_status: 'sealed_not_executed', command: null, seal: {
  seal_id: id(90), work_order_id: value.workOrderId, operator_person_id: value.personId, operation_type: 'reverse', request_id: request.trace_request_id,
  request_hash: request.request_hash, sealed_at: time } } }
module.exports = { id, input, order, options, preview, marker, result, originals, sealed }
