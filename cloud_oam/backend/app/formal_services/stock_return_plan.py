"""Plan an explicit regional return without reserving or moving inventory."""
from datetime import datetime, timezone

from sqlalchemy import or_, select

from ..inventory_models import CustodyAssignment, StockLocation
from ..stock_return_schemas import StockReturnDestinationOut, StockReturnPreviewIn, StockReturnPreviewOut
from ..work_order_return_schemas import WorkOrderReturnSelectionIn
from . import inventory_posting as posting, inventory_query as inventory
from .work_order_evidence_snapshot import material_audit_cursor
from .work_order_return_sources import preview_selection, _hash, _fail
from .work_order_query import _aware


def authorize(db, actor, action):
    current = posting._require_current_actor(db, actor)
    if not current.allows(db, "stock_operation", action, target_scope_type="person", target_scope_id=str(current.person_id)):
        _fail("stock_return_forbidden", "没有本人退回单的当前操作权限", 403)
    return current


def destination(db, *, person_id, source_location_id, target_location_id, transit_location_id, at):
    source = db.get(StockLocation, source_location_id, populate_existing=True)
    target = db.get(StockLocation, target_location_id, populate_existing=True)
    transit = db.get(StockLocation, transit_location_id, populate_existing=True)
    if (source is None or target is None or transit is None
            or len({source.id, target.id, transit.id}) != 3
            or any(row.status != "active" for row in (source, target, transit))
            or source.location_type != "personal" or source.custodian_person_id != person_id
            or source.parent_id != target.id or target.location_type != "region"
            or source.owner_org_id != target.owner_org_id or transit.owner_org_id != target.owner_org_id
            or transit.location_type != "transit" or transit.parent_id != target.id):
        _fail("stock_return_destination_invalid", "请准确选择个人仓所属区域仓及其在途位置，不能替换或猜测库位绑定")
    assignments = tuple(db.scalars(select(CustodyAssignment).where(CustodyAssignment.location_id == target.id,
        CustodyAssignment.valid_from <= at, or_(CustodyAssignment.valid_to.is_(None), CustodyAssignment.valid_to > at))
        .execution_options(populate_existing=True)))
    if len(assignments) != 1 or assignments[0].custodian_person_id != target.custodian_person_id:
        _fail("stock_return_receiver_unresolved", "区域仓没有唯一且一致的有效保管人，请先核验接收责任")
    assignment = assignments[0]
    return StockReturnDestinationOut(source_location_id=source.id, target_location_id=target.id,
        target_location_code=target.code, target_location_name=target.name, transit_location_id=transit.id,
        transit_location_code=transit.code, transit_location_name=transit.name, region_org_id=target.owner_org_id,
        custody_assignment_id=assignment.id, custodian_person_id=assignment.custodian_person_id,
        custody_effective_from=_aware(assignment.valid_from))


def intent(work_order_id, request):
    data = StockReturnPreviewIn.model_validate(request.model_dump(include=set(StockReturnPreviewIn.model_fields)))
    value = data.model_dump(mode="json")
    value["lines"] = sorted(value["lines"], key=lambda line: line["source_recovery_line_id"])
    for line in value["lines"]:
        line["quantity"] = format(next(row.quantity for row in data.lines if str(row.source_recovery_line_id) == line["source_recovery_line_id"]), ".3f")
        line["serial_verifications"] = sorted(line["serial_verifications"], key=lambda proof: proof["serial_id"])
    return {"operation_type": "return", "work_order_id": str(work_order_id), **value}


def preview_return(db, *, actor, work_order_id, request):
    current = authorize(db, actor, "submit_return")
    value = intent(work_order_id, request)
    with db.no_autoflush:
        audit_cursor = material_audit_cursor(db)
        selection = preview_selection(db, actor=current, work_order_id=work_order_id,
            request=WorkOrderReturnSelectionIn(operator_person_id=request.operator_person_id, lines=request.lines))
        checked_at = datetime.now(timezone.utc)
        target = destination(db, person_id=current.person_id, source_location_id=selection.location_id,
            target_location_id=request.target_location_id, transit_location_id=request.transit_location_id, at=checked_at)
        # Destination queries may observe a later READ COMMITTED snapshot.
        # Re-prove the source after them, then compare the current routing fact.
        latest = preview_selection(db, actor=current, work_order_id=work_order_id,
            request=WorkOrderReturnSelectionIn(operator_person_id=request.operator_person_id, lines=request.lines))
        latest_target = destination(db, person_id=current.person_id, source_location_id=latest.location_id,
            target_location_id=request.target_location_id, transit_location_id=request.transit_location_id,
            at=datetime.now(timezone.utc))
        if (latest.basis_hash != selection.basis_hash or latest_target != target
                or material_audit_cursor(db) != audit_cursor):
            _fail("stock_return_plan_changed", "退回来源或接收责任在预检期间变化，请重新核验")
        inventory._ensure_projection_snapshot_current(db, inventory._ProjectionSnapshot(selection.ledger_cursor, None))
        document = {"intent": value, "authorization_version": current.authorization_version,
            "ledger_cursor": selection.ledger_cursor, "source_basis_hash": selection.basis_hash,
            "destination": target.model_dump(mode="json"), "lines": [row.model_dump(mode="json") for row in selection.lines]}
        authorize(db, current, "submit_return")
        return StockReturnPreviewOut(operator_person_id=current.person_id, authorization_version=current.authorization_version,
            work_order_id=work_order_id, reason=request.reason, ledger_cursor=selection.ledger_cursor,
            checked_at=checked_at, destination=target, request_hash=_hash(value), plan_hash=_hash(document), lines=selection.lines), document
