"""Prepare an approved loss return without releasing stock or creating an order.

The final integration still requires provenance tables, atomic derivation,
deferred SQL proofs, fulfillment, recovery/seals and dedicated correction.
This module must never manufacture a work-order recovery to fit old returns.
"""
from datetime import datetime, timezone
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import or_, select

from app.inventory_models import (
    CustodyAssignment, FormalMaterial, StockAccount, StockLocation,
)
from app.stock_operation_models import StockLossDisposition, StockOperationOrder
from app.stock_loss_return_schemas import StockLossReturnPreviewIn
from app.formal_services import inventory_posting as posting, inventory_query as inventory
from app.formal_services import stock_loss_disposition_plan as approved
from app.formal_services import stock_loss_planning as planning, stock_loss_sources as sources
from app.formal_services.stock_return_plan import destination


def intent(request):
    return StockLossReturnPreviewIn.model_validate({
        name: getattr(request, name) for name in StockLossReturnPreviewIn.model_fields
    }).model_dump(mode='json')


def pending_account(db, source):
    dimensions = {name: getattr(source, name) for name in (
        'owner_org_id', 'custodian_person_id', 'location_id', 'material_id',
        'condition_code', 'lot_id',
    )}
    dimensions['availability_bucket'] = 'return_pending'
    rows = tuple(db.scalars(select(StockAccount).filter_by(**dimensions).limit(2)
        .execution_options(populate_existing=True)))
    if len(rows) > 1:
        sources._fail('stock_loss_return_target_ambiguous', '原成色的待退回账户维度不唯一')
    if rows:
        return rows[0]
    identifier = uuid5(NAMESPACE_URL, 'rsc:stock-loss-return-account:v1:' + sources._hash({
        key: str(value) if value is not None else None for key, value in dimensions.items()
    }))
    if db.get(StockAccount, identifier, populate_existing=True) is not None:
        sources._fail('stock_loss_return_target_conflict', '待退回账户标识与完整维度冲突')
    return StockAccount(id=identifier, **dimensions)


def _basis(db, *, actor, request):
    current, order, line, decision = approved.approved_line(db, actor=actor, request=request)
    if decision.disposition != 'return_to_region':
        sources._fail('stock_loss_return_decision_required', '必须使用原总部批准的退回决定', 412)
    if db.scalar(select(StockLossDisposition.id).where(StockLossDisposition.line_id == line.id)):
        sources._fail('stock_loss_return_already_disposed', '原报损行已处置，请回查原请求')
    child_id = uuid5(NAMESPACE_URL, 'rsc:stock-loss-return:v1:' + str(decision.id))
    if db.get(StockOperationOrder, child_id, populate_existing=True) is not None:
        sources._fail('stock_loss_return_already_derived', '原批准已经派生退回单，请回查原请求')
    source = db.get(StockAccount, line.reserved_account_id, populate_existing=True)
    original = db.get(StockAccount, line.stock_account_id, populate_existing=True)
    if source is None or original is None:
        sources._fail('stock_loss_return_source_changed', '原报损冻结账户不存在')
    location = db.get(StockLocation, source.location_id, populate_existing=True)
    material = db.get(FormalMaterial, source.material_id, populate_existing=True)
    if (location is None or location.status != 'active' or location.location_type != 'personal'
            or location.owner_org_id != source.owner_org_id
            or location.custodian_person_id != order.requester_id
            or material is None or material.status != 'active'):
        sources._fail('stock_loss_return_source_changed', '原保管位置或物料已变化')
    at = datetime.now(timezone.utc)
    custody = tuple(db.scalars(select(CustodyAssignment).where(
        CustodyAssignment.location_id == location.id,
        CustodyAssignment.valid_from <= at,
        or_(CustodyAssignment.valid_to.is_(None), CustodyAssignment.valid_to > at),
    ).execution_options(populate_existing=True)))
    if len(custody) != 1 or custody[0].custodian_person_id != order.requester_id:
        sources._fail('stock_loss_return_custody_changed', '原冻结库存没有唯一有效的保管责任')
    # HQ executes the approved decision; the source remains the engineer's
    # personal warehouse until independent physical return facts establish more.
    route = destination(db, person_id=order.requester_id, source_location_id=source.location_id,
        target_location_id=request.target_location_id, transit_location_id=request.transit_location_id, at=at)
    target = pending_account(db, source)
    snapshot = inventory._projection_snapshot(db)
    opening = inventory._validated_opening_evidence(db, actor=current, snapshot=snapshot,
        required_pairs={(source.owner_org_id, source.location_id)}, discover_authorized_zero_scopes=False)
    if not opening.complete:
        sources._fail('stock_loss_return_opening_required', '原冻结库存缺少完整期初证明', 412)
    account_ids = {source.id, original.id}
    if target in db:
        account_ids.add(target.id)
    inventory._validate_current_projection_integrity(db, snapshot=snapshot, account_ids=account_ids)
    remaining, hold_basis, balance = approved._remaining(db, source)
    selected = next((row for row in remaining if row.line_id == line.id), None)
    if selected is None or selected.quantity != line.quantity:
        sources._fail('stock_loss_return_hold_changed', '原行冻结份额已变化')
    policies, fingerprint = sources._policies(db, {source.material_id}, at)
    policy = policies[source.material_id]
    outline = planning.plan_dispositions(
        approval_fact_id=decision.review_id, approval_stage='approved',
        disposition_fact_ids={line.id: line.id},
        held_lines=(planning.HeldLine(line.id, approved._account(original), approved._account(source),
            planning.TrackedQuantity(line.quantity, policy.tracking_mode, policy.quantity_scale,
                policy.allow_fraction, tuple(sorted(selected.serial_ids, key=str))),
            selected.quantity, selected.serial_ids),),
        decisions=(planning.Disposition(line.id, planning.DispositionKind.RETURN, decision.reason,
            target=approved._account(target), return_operation_id=child_id),),
    )[0]
    posting._require_no_active_hard_freezes(db, {source.id: source, target.id: target}, effective_at=at)
    inventory._ensure_projection_snapshot_current(db, snapshot)
    approved.authorize(db, current, order)
    document = dict(
        schema_version='1.0', origin_kind='loss_report', intent=intent(request),
        loss_operation_id=str(order.id), loss_line_id=str(line.id),
        headquarters_decision_id=str(decision.id), derived_return_operation_id=str(child_id),
        executor_person_id=str(current.person_id), requester_id=str(order.requester_id),
        authorization_version=current.authorization_version, reason=decision.reason,
        source_account_id=str(source.id), pending_account_id=str(target.id),
        owner_org_id=str(source.owner_org_id), custodian_person_id=str(source.custodian_person_id),
        location_id=str(source.location_id), material_id=str(source.material_id),
        lot_id=str(source.lot_id) if source.lot_id else None,
        condition_code=source.condition_code, source_custody_assignment_id=str(custody[0].id),
        quantity=format(line.quantity, '.3f'), serial_ids=sorted(str(sn) for sn in selected.serial_ids),
        destination=route.model_dump(mode='json'), movement_type=outline.movement.movement_type,
        source_document_type=outline.source_document_type, lifecycle_after=outline.lifecycle_after,
        return_fulfillment_required=outline.return_fulfillment_required,
        ledger_cursor=snapshot.ledger_cursor, source_balance_version=balance.version,
        source_balance_quantity=format(balance.quantity, '.3f'),
        policy_fingerprint=[list(item) for item in fingerprint], holds=hold_basis,
    )
    return document, at


def preview_loss_return(db, *, actor, request):
    request = StockLossReturnPreviewIn.model_validate(intent(request))
    with db.no_autoflush:
        first, _ = _basis(db, actor=actor, request=request)
        latest, at = _basis(db, actor=actor, request=request)
        if first != latest:
            sources._fail('stock_loss_return_plan_changed', '原冻结、批准、权限或接收责任在预检期间变化，请重新预览')
        return dict(**latest, plan_hash=sources._hash(latest), checked_at=at,
            planning_status='preview_only', stock_effect='none')
