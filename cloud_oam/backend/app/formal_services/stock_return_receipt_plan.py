"""Preview exact parcel acceptance without moving stock or assuming receipt."""
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select

from ..foundation_models import FileObject
from ..inventory_models import InventorySerial, SerialCurrentPosition, ReceiptException, CustodyAssignment
from ..stock_operation_models import StockOperationReceipt, StockOperationReceiptException, StockOperationOutboundLine, StockOperationShipmentLine
from ..stock_return_receipt_schemas import StockReturnReceiptPreviewIn, StockReturnReceiptPreviewOut, StockReturnReceiptLineOut
from . import stock_return_receiving as receiving, inventory_query as inventory
from .formal_files import is_available_formal_file_for_purpose
from .work_order_evidence_snapshot import material_audit_cursor
from .work_order_query import _aware
from .work_order_return_sources import _hash, _fail, _policies


def intent(shipment_id, request):
    clean = StockReturnReceiptPreviewIn.model_validate(request.model_dump(include=set(StockReturnReceiptPreviewIn.model_fields)))
    value = clean.model_dump(mode="json")
    value['received_at'] = clean.received_at.isoformat(timespec='microseconds').replace('+00:00', 'Z')
    value['lines'] = []
    for row in sorted(clean.lines, key=lambda item: str(item.shipment_line_id)):
        line = row.model_dump(mode='json')
        for key in ('accepted_qty', 'rejected_qty', 'damaged_qty', 'shortage_qty'): line[key] = format(getattr(row, key), '.3f')
        line['accepted_serial_verifications'].sort(key=lambda proof: proof['serial_id'])
        for key in ('damaged_serial_ids', 'rejected_serial_ids', 'shortage_serial_ids'): line[key].sort()
        line['exceptions'].sort(key=lambda item: item['exception_type'])
        value['lines'].append(line)
    return {'operation_type': 'receive_return', 'shipment_id': str(shipment_id), **value}


def authorize(db, actor, shipment_id, *, action='receive_return'):
    current = receiving._authorize(db, actor)
    detail = receiving.my_return_receiving_detail(db, actor=current, shipment_id=shipment_id)
    locations = receiving._locations(db, current)
    target = locations.get(detail.package.target_location_id)
    if target is None or not current.allows(db, 'stock_operation', action,
            target_scope_type='organization', target_scope_id=str(target[1])):
        _fail('stock_return_receipt_forbidden', '没有本区域仓的当前验收权限', 403)
    return current, detail


def _prior(db, shipment_id):
    from .stock_return_receipt_facts import verified_receipts
    accepted = defaultdict(Decimal); rejected = defaultdict(Decimal); used = defaultdict(set)
    for result in verified_receipts(db, shipment_id=shipment_id):
        for line in result.lines:
            accepted[line.shipment_line_id] += Decimal(line.accepted_qty)
            rejected[line.shipment_line_id] += Decimal(line.rejected_qty)
            selected = {sn.serial_id for sn in (*line.accepted_serials, *line.rejected_serials)}
            if used[line.shipment_line_id] & selected:
                _fail('stock_return_receipt_history_invalid', '同一包裹 SN 已重复确认', 503)
            used[line.shipment_line_id].update(selected)
    return accepted, rejected, used


def file_fingerprint(row):
    return (str(row.id), row.sha256, row.size_bytes, row.mime_type, _hash(row.metadata_jsonb))


def _evidence(db, actor, request):
    identifiers = {item.evidence_file_id for line in request.lines for item in line.exceptions}
    result = []
    for identifier in sorted(identifiers, key=str):
        row = db.get(FileObject, identifier, populate_existing=True)
        if (not is_available_formal_file_for_purpose(row, purpose='receipt_exception_evidence', uploader_user_id=actor.user_id)
                or row.metadata_jsonb.get('uploader_person_id') != str(actor.person_id)
                or row.metadata_jsonb.get('authorization_version') != actor.authorization_version
                or not _aware(row.created_at) <= datetime.fromisoformat(row.metadata_jsonb['completion']['verified_at']) <= datetime.now(timezone.utc)):
            _fail('stock_return_receipt_evidence_invalid', '异常证据必须是本人当前权限下已完成上传的验收异常文件')
        if (db.scalar(select(StockOperationReceiptException.id).where(StockOperationReceiptException.evidence_file_id == identifier).limit(1))
                or db.scalar(select(ReceiptException.id).where(ReceiptException.evidence_file_id == identifier).limit(1))):
            _fail('stock_return_receipt_evidence_bound', '异常文件已绑定原验收，请重新选择本次证据')
        result.append(file_fingerprint(row))
    return tuple(result)


def _basis(db, actor, package, request):
    accepted, rejected, used = _prior(db, package.shipment_id)
    policies, fingerprints = _policies(db, {row.material_id for row in package.lines}, datetime.now(timezone.utc))
    originals = {row.shipment_line_id: row for row in package.lines}; views = []
    for chosen in sorted(request.lines, key=lambda row: str(row.shipment_line_id)):
        original = originals.get(chosen.shipment_line_id)
        if original is None: _fail('stock_return_receipt_line_invalid', '验收明细不属于本包裹')
        remaining = Decimal(original.shipped_quantity) - accepted[original.shipment_line_id] - rejected[original.shipment_line_id]
        if remaining < 0: _fail('stock_return_receipt_history_invalid', '历史累计验收超过本包裹', 503)
        if chosen.accepted_qty + chosen.rejected_qty + chosen.shortage_qty > remaining:
            _fail('stock_return_receipt_quantity_exceeded', '本次验收及短少观察量超过本包裹尚未确认数量')
        policy = policies[original.material_id]; tracked = policy.tracking_mode in {'serial', 'lot_and_serial'}
        if bool(original.serials) != tracked or (policy.tracking_mode in {'lot', 'lot_and_serial'}) != (original.lot_id is not None):
            _fail('stock_return_receipt_policy_changed', '原包裹与当前 SN 或批次策略不一致')
        for key in ('accepted_qty', 'rejected_qty', 'damaged_qty', 'shortage_qty'):
            quantity = getattr(chosen, key)
            if quantity != quantity.quantize(Decimal(1).scaleb(-policy.quantity_scale)) or (not policy.allow_fraction and quantity != quantity.to_integral_value()):
                _fail('stock_return_receipt_precision_invalid', '验收数量不符合物料精度')
        names = {sn.serial_id: sn.serial_no for sn in original.serials}
        groups = (tuple(proof.serial_id for proof in chosen.accepted_serial_verifications), chosen.rejected_serial_ids, chosen.shortage_serial_ids, chosen.damaged_serial_ids)
        quantities = (chosen.accepted_qty, chosen.rejected_qty, chosen.shortage_qty, chosen.damaged_qty)
        for ids, quantity in zip(groups, quantities):
            if (tracked and Decimal(len(ids)) != quantity) or (not tracked and ids) or not set(ids) <= set(names) - used[original.shipment_line_id]:
                _fail('stock_return_receipt_serial_invalid', 'SN 数量、原包裹所属或未确认状态不一致')
        shipment_line = db.get(StockOperationShipmentLine, original.shipment_line_id, populate_existing=True)
        departed = db.get(StockOperationOutboundLine, shipment_line.outbound_line_id, populate_existing=True)
        for proof in chosen.accepted_serial_verifications:
            serial = db.get(InventorySerial, proof.serial_id, populate_existing=True)
            position = db.get(SerialCurrentPosition, proof.serial_id, populate_existing=True)
            if (serial is None or serial.lifecycle_status != 'active' or serial.material_id != original.material_id
                    or serial.lot_id != original.lot_id or serial.serial_no != proof.serial_no or serial.qr_code != proof.qr_code
                    or proof.sku_code != original.sku_code or position is None or position.stock_account_id != departed.transit_stock_account_id):
                _fail('stock_return_receipt_scan_invalid', '已接受物料必须按准确原包裹完成实物三码校验')
        view_serials = lambda ids: tuple(dict(serial_id=identifier, serial_no=names[identifier]) for identifier in sorted(ids, key=str))
        views.append(StockReturnReceiptLineOut(**{key: getattr(original, key) for key in
            ('shipment_line_id', 'material_id', 'sku_code', 'material_name', 'base_unit', 'condition_code', 'lot_id', 'lot_no')},
            shipped_qty=original.shipped_quantity, previously_accepted_qty=format(accepted[original.shipment_line_id], '.3f'),
            previously_rejected_qty=format(rejected[original.shipment_line_id], '.3f'), unconfirmed_qty=format(remaining, '.3f'),
            **{key: format(getattr(chosen, key), '.3f') for key in ('accepted_qty', 'rejected_qty', 'damaged_qty', 'shortage_qty')},
            accepted_serials=view_serials(groups[0]), rejected_serials=view_serials(groups[1]), shortage_serials=view_serials(groups[2]),
            damaged_serial_ids=tuple(sorted(chosen.damaged_serial_ids, key=str)),
            exceptions=tuple(sorted(chosen.exceptions, key=lambda item: item.exception_type))))
    return tuple(views), fingerprints, _evidence(db, actor, request)


def preview_receipt(db, *, actor, shipment_id, request):
    request = StockReturnReceiptPreviewIn.model_validate(request.model_dump(include=set(StockReturnReceiptPreviewIn.model_fields)))
    with db.no_autoflush:
        audit = material_audit_cursor(db); snapshot = inventory._projection_snapshot(db)
        if len(audit) != 1: _fail('stock_return_receipt_audit_invalid', '验收审计游标不可用', 503)
        current, detail = authorize(db, actor, shipment_id); package = detail.package
        if request.operator_person_id != current.person_id: _fail('operator_mismatch', '验收人必须是当前登录人员', 403)
        assignment = db.get(CustodyAssignment, package.custody_assignment_id, populate_existing=True)
        if (request.received_at < package.shipped_at or request.received_at < _aware(assignment.valid_from)
                or request.received_at > datetime.now(timezone.utc)):
            _fail('stock_return_receipt_time_invalid', '验收时刻必须在交运及接收责任生效之后，且不晚于当前时间')
        first = _basis(db, current, package, request); latest = _basis(db, current, package, request)
        final_actor, final_detail = authorize(db, current, shipment_id)
        if first != latest or current != final_actor or package != final_detail.package or material_audit_cursor(db) != audit:
            _fail('stock_return_receipt_plan_changed', '验收数量、规则、证据或责任在预检期间变化，请重新核验')
        inventory._ensure_projection_snapshot_current(db, snapshot)
        value = intent(shipment_id, request); lines, policies, evidence = latest
        plan = dict(intent=value, authorization_version=current.authorization_version, ledger_cursor=snapshot.ledger_cursor,
            audit_cursor=audit[0].version, package=package.model_dump(mode='json'), policies=policies,
            evidence=evidence, lines=[row.model_dump(mode='json') for row in lines])
        return StockReturnReceiptPreviewOut(shipment_id=shipment_id, operation_id=package.operation_id,
            work_order_id=package.work_order_id, operator_person_id=current.person_id, authorization_version=current.authorization_version,
            received_at=request.received_at, reason=request.reason, checked_at=datetime.now(timezone.utc), ledger_cursor=snapshot.ledger_cursor,
            package=package, request_hash=_hash(value), plan_hash=_hash(plan), lines=lines), plan
