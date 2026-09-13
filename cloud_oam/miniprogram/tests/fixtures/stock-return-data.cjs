const c = require('../../utils/stock-return-contract')
const { id, time = '2026-09-13T00:00:00Z', order } = require('./work-order-reversal-data.cjs')
function input(tracked = false) { return { workOrderId: id(1), personId: id(2), authorizationVersion: 7,
  targetLocationId: id(7), transitLocationId: id(8), reason: '拆回物料退回🔧\n保留责任', lines: [{ source_recovery_line_id: id(11), stock_account_id: id(12),
    quantity: '1.000', serial_verifications: tracked ? [{ serial_id: id(15), serial_no: 'RETURN-SN', sku_code: 'RETURN-SKU', qr_code: 'PRIVATE-RETURN-QR' }] : [] }] } }
function destination() { return { source_location_id: id(6), target_location_id: id(7), target_location_code: 'REGION', target_location_name: '区域仓',
  transit_location_id: id(8), transit_location_code: 'TRANSIT', transit_location_name: '退回在途位置', region_org_id: id(5),
  custody_assignment_id: id(10), custodian_person_id: id(9), custody_effective_from: time } }
function source(value = input()) { return { source_recovery_line_id: id(11), recovery_operation_id: id(14), recovery_operation_no: 'RECOVER-TEST',
  stock_account_id: id(12), owner_org_id: id(5), custodian_person_id: value.personId, location_id: id(6), material_id: id(13), sku_code: 'RETURN-SKU',
  material_name: '测试拆回件', base_unit: '件', condition_code: 'damaged', lot_id: null, lot_no: null, owed_quantity: '1.000', committed_quantity: '0.000',
  available_quantity: '1.000', selectable_quantity: '1.000', serials: value.lines[0].serial_verifications.map(sn => ({ serial_id: sn.serial_id, serial_no: sn.serial_no, selectable: true })) } }
function options(value = input()) { return { schema_version: '1.0', sources: { schema_version: '1.0', person_id: value.personId,
  authorization_version: value.authorizationVersion, work_order: { ...order(value), status: 'closed', can_operate: false }, location_id: id(6),
  custody_effective_from: time, ledger_cursor: 8, projected_at: time, queried_at: time, blockers: [], items: [source(value)] }, destinations: [destination()] } }
function preview(value = input()) { return { schema_version: '1.0', planning_status: 'preview_only', operator_person_id: value.personId,
  authorization_version: value.authorizationVersion, work_order_id: value.workOrderId, reason: value.reason, ledger_cursor: 8, checked_at: time,
  destination: destination(), request_hash: c.requestHash(value), plan_hash: 'a'.repeat(64), lines: [{ source: source(value), selected_quantity: '1.000',
    selected_serials: value.lines[0].serial_verifications.map(sn => ({ serial_id: sn.serial_id, serial_no: sn.serial_no })) }] } }
function marker(value = input(), cancel = false) { return { v: 1, kind: c.KIND, work_order_id: value.workOrderId, person_id: value.personId,
  authorization_version: value.authorizationVersion, operation_type: cancel ? 'cancel_return' : 'submit_return', trace_request_id: 'wxreq-' + (cancel ? 'd' : 'b').repeat(36),
  request_hash: cancel ? c.hash({ operation_id: id(20), operator_person_id: value.personId, reason: value.reason }) : c.requestHash(value),
  plan_hash: cancel ? null : 'a'.repeat(64), operation_id: cancel ? id(20) : null } }
function result(value = input(), request = marker(value)) { return request.operation_type === 'cancel_return' ? {
  schema_version: '1.0', cancellation_id: id(22), operation_id: id(20), operator_person_id: value.personId, reason: value.reason,
  request_id: request.trace_request_id, request_hash: request.request_hash, status: 'cancelled', posting_transaction_id: id(23), cancelled_at: time
} : { schema_version: '1.0', operation_id: id(20), operation_no: 'RET-TEST', work_order_id: value.workOrderId, requester_id: value.personId, status: 'submitted',
  reason: value.reason, request_id: request.trace_request_id, request_hash: request.request_hash, plan_hash: request.plan_hash,
  posting_transaction_id: id(21), submitted_at: time, destination: destination(), lines: preview(value).lines } }
function history(value = input(), cancelled = false) { return { schema_version: '1.0', person_id: value.personId, work_order_id: value.workOrderId,
  authorization_version: value.authorizationVersion, queried_at: time, items: [{ original: result(value), cancellation: cancelled ? result(value, marker(value, true)) : null }] } }
function sealed(request = marker()) { return { schema_version: '1.0', lookup_status: 'sealed', seal: { seal_id: id(25), operator_person_id: request.person_id,
  work_order_id: request.work_order_id, operation_id: request.operation_id, operation_type: request.operation_type, request_id: request.trace_request_id,
  request_hash: request.request_hash, sealed_at: time } } }
function drafts(value = input()) { return Object.fromEntries(value.lines.map(row => [row.source_recovery_line_id, { quantity: row.quantity, serial_verifications: row.serial_verifications }])) }
module.exports = { id, input, destination, source, options, preview, marker, result, history, sealed, drafts }
