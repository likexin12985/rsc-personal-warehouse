"""Resolve an approved loss line against current stock without writing.

This is an internal development service. Public activation requires the full
SQL disposition proof, request recovery, reversal and client workflow.
"""
from datetime import datetime, timezone
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import or_, select

from ..inventory_models import (CustodyAssignment, FormalMaterial, StockAccount,
    StockBalance, StockLocation, SerialCurrentPosition)
from ..models import User
from ..stock_operation_models import (StockOperationOrder as Order, StockOperationLine as Line,
    StockOperationSerial as Serial, StockLossHeadquartersDecision as Decision,
    StockLossHeadquartersReview as Review, StockLossDisposition as Disposition)
from ..stock_loss_schemas import StockLossDispositionPreviewIn
from . import inventory_posting as posting, inventory_query as inventory
from . import stock_loss_facts as facts, stock_loss_sources as sources
from . import stock_loss_headquarters_reviews as headquarters
from . import stock_loss_planning as planning, stock_loss_holds as holds

ACTION = 'dispose_loss'
SUPPORTED = frozenset({'restore_available', 'convert_used', 'convert_damaged'})


def intent(request):
    return StockLossDispositionPreviewIn.model_validate({k:getattr(request,k)
        for k in StockLossDispositionPreviewIn.model_fields}).model_dump(mode='json')


def authorize(db, actor, order):
    current = posting._require_current_actor(db, posting._validate_supplied_actor(actor))
    user = db.get(User, current.user_id, populate_existing=True)
    location = db.get(StockLocation, order.source_location_id, populate_existing=True)
    if user is None or not user.is_active or location is None or order.operation_type != 'loss_report':
        sources._fail('stock_loss_disposition_forbidden', '当前报损处置身份或原单无效', 403)
    if (not any(g.role_code=='admin' and g.scope_type=='national' and g.scope_id=='*'
                for g in current.assignments)
            or not current.allows(db, 'stock_operation', ACTION,
                target_scope_type='organization', target_scope_id=str(location.owner_org_id))
            or not any(g.role_code=='admin' and g.scope_type=='national' and g.scope_id=='*'
                and g.resource=='stock_operation' and g.action==ACTION and g.field_code==''
                and g.effect=='allow' for g in current.entitlements)):
        sources._fail('stock_loss_disposition_forbidden', '没有当前总部库存处置权限', 403)
    return current


def approved_line(db, *, actor, request):
    decision = db.get(Decision, request.headquarters_decision_id, populate_existing=True)
    review = db.get(Review, decision.review_id, populate_existing=True) if decision else None
    line = db.get(Line, decision.line_id, populate_existing=True) if decision else None
    order = db.get(Order, line.operation_id, populate_existing=True) if line else None
    if decision is None or review is None or line is None or order is None:
        sources._fail('stock_loss_disposition_not_found', '准确报损终审明细不存在', 404)
    current = authorize(db, actor, order)
    if (review.operation_id!=order.id or request.expected_headquarters_review_hash!=review.request_hash
            or request.expected_submission_plan_hash!=order.plan_hash):
        sources._fail('stock_loss_disposition_approval_changed', '原报损或总部终审记录与本次处置不一致')
    facts.submission_evidence(db, order=order)
    headquarters.verified(db, row=review, order=order)
    return current, order, line, decision


def target_account(db, source, disposition):
    condition = {'restore_available':source.condition_code, 'convert_used':'used',
        'convert_damaged':'damaged'}[disposition]
    fields = {k:getattr(source,k) for k in
        ('owner_org_id','location_id','custodian_person_id','material_id','lot_id')}
    fields.update(condition_code=condition, availability_bucket='available')
    rows = tuple(db.scalars(select(StockAccount).filter_by(**fields).limit(2)
        .execution_options(populate_existing=True)))
    if len(rows)>1:
        sources._fail('stock_loss_disposition_target_ambiguous', '目标库存维度不唯一')
    if rows:return rows[0]
    identifier = uuid5(NAMESPACE_URL, 'rsc:stock-loss-disposition-account:v1:'+sources._hash(
        {key:str(value) if value is not None else None for key,value in fields.items()}))
    if db.get(StockAccount, identifier, populate_existing=True) is not None:
        sources._fail('stock_loss_disposition_target_conflict', '目标库存标识与维度冲突')
    return StockAccount(id=identifier, **fields)


def _remaining(db, source):
    """Only exact, fully verified disposition facts release original holds."""
    from .stock_loss_disposition_facts import verified
    originals, releases, basis = [], [], []
    proof_cache = {}
    lines = tuple(db.scalars(select(Line).where(Line.operation_type=='loss_report',
        Line.reserved_account_id==source.id).order_by(Line.id)
        .execution_options(populate_existing=True)))
    for line in lines:
        order = db.get(Order, line.operation_id, populate_existing=True)
        facts.submission_evidence(db, order=order)
        serials = frozenset(db.scalars(select(Serial.serial_id).where(Serial.line_id==line.id)))
        originals.append(holds.Hold(line.id, order.id, source.id, line.quantity, serials))
        disposition = db.scalar(select(Disposition).where(Disposition.line_id==line.id)
            .execution_options(populate_existing=True))
        if disposition is not None:
            verified(db, row=disposition, _proof_cache=proof_cache)
            releases.append(holds.Release(disposition.id, line.id, order.id,
                disposition.posting_transaction_id, disposition.posting_movement_id,
                disposition.quantity, serials))
        basis.append(dict(line_id=str(line.id), operation_id=str(order.id), quantity=format(line.quantity,'.3f'),
            serial_ids=sorted(str(s) for s in serials), disposition_id=str(disposition.id) if disposition else None))
    try:
        remaining = holds.remaining_holds(tuple(originals), tuple(releases))
    except planning.ContractError:
        inventory._invalid_current_projection()
    balance = db.get(StockBalance, source.id, populate_existing=True)
    if balance is None:inventory._invalid_current_projection()
    serial_ids = {sn for row in remaining for sn in row.serial_ids}
    positions = dict(db.execute(select(SerialCurrentPosition.serial_id, SerialCurrentPosition.stock_account_id)
        .where(SerialCurrentPosition.serial_id.in_(serial_ids))).all())
    try:
        holds.assert_holds_preserved(remaining, balances={source.id:balance.quantity}, serial_positions=positions)
    except planning.ContractError:
        inventory._invalid_current_projection()
    return remaining, basis, balance


def _account(row):
    return planning.Account(row.id,row.owner_org_id,row.custodian_person_id,row.location_id,
        row.material_id,row.condition_code,row.availability_bucket,row.lot_id)


def preview_disposition(db, *, actor, request):
    request = StockLossDispositionPreviewIn.model_validate(intent(request))
    with db.no_autoflush:
        current, order, line, decision = approved_line(db, actor=actor, request=request)
        if decision.disposition not in SUPPORTED:
            sources._fail('stock_loss_disposition_requires_dedicated_flow', '退回和报废必须使用各自完整的独立作业流程', 412)
        if db.scalar(select(Disposition.id).where(Disposition.line_id==line.id)):
            sources._fail('stock_loss_disposition_already_posted', '该原明细已有处置事实，请回查原请求')
        at = datetime.now(timezone.utc)
        original = db.get(StockAccount, line.stock_account_id, populate_existing=True)
        source = db.get(StockAccount, line.reserved_account_id, populate_existing=True)
        location = db.get(StockLocation, source.location_id, populate_existing=True)
        material = db.get(FormalMaterial, source.material_id, populate_existing=True)
        if (location is None or location.status!='active' or location.location_type!='personal'
                or location.owner_org_id!=source.owner_org_id or location.custodian_person_id!=order.requester_id
                or material is None or material.status!='active'):
            sources._fail('stock_loss_disposition_source_changed', '原保管位置或物料状态已变化')
        assignments = tuple(db.scalars(select(CustodyAssignment).where(
            CustodyAssignment.location_id==location.id,
            CustodyAssignment.valid_from<=at, or_(CustodyAssignment.valid_to.is_(None),CustodyAssignment.valid_to>at))
            .execution_options(populate_existing=True)))
        if len(assignments)!=1 or assignments[0].custodian_person_id!=order.requester_id:
            sources._fail('stock_loss_disposition_custody_changed', '原冻结库存的当前保管责任不唯一或已失效')
        target = target_account(db, source, decision.disposition)
        snapshot = inventory._projection_snapshot(db)
        opening = inventory._validated_opening_evidence(db, actor=current, snapshot=snapshot,
            required_pairs={(source.owner_org_id,source.location_id)}, discover_authorized_zero_scopes=False)
        if not opening.complete:
            sources._fail('stock_loss_disposition_opening_required', '原冻结库存缺少完整期初证据', 412)
        ids = {source.id, original.id}
        if target in db:ids.add(target.id)
        inventory._validate_current_projection_integrity(db, snapshot=snapshot, account_ids=ids)
        remaining, hold_basis, balance = _remaining(db, source)
        selected = next((r for r in remaining if r.line_id==line.id),None)
        if selected is None or selected.quantity!=line.quantity:
            sources._fail('stock_loss_disposition_hold_changed', '原明细冻结份额已变化')
        policies, fingerprint = sources._policies(db,{source.material_id},at)
        policy = policies[source.material_id]
        outline = planning.plan_dispositions(approval_fact_id=decision.review_id, approval_stage='approved',
            disposition_fact_ids={line.id:line.id}, held_lines=(planning.HeldLine(line.id,_account(original),_account(source),
                planning.TrackedQuantity(line.quantity,policy.tracking_mode,policy.quantity_scale,policy.allow_fraction,
                    tuple(sorted(selected.serial_ids,key=str))),selected.quantity,selected.serial_ids),),
            decisions=(planning.Disposition(line.id,planning.DispositionKind(decision.disposition),decision.reason,_account(target)),))[0]
        posting._require_no_active_hard_freezes(db,{source.id:source,target.id:target},effective_at=at)
        if sources._policies(db,{source.material_id},datetime.now(timezone.utc))[1]!=fingerprint:
            sources._fail('stock_loss_disposition_policy_changed', '库存策略已变化，请重新预览')
        inventory._ensure_projection_snapshot_current(db,snapshot)
        authorize(db,current,order)
        document = dict(schema_version='1.0', intent=intent(request), operation_id=str(order.id),line_id=str(line.id),
            executor_person_id=str(current.person_id),authorization_version=current.authorization_version,
            disposition=decision.disposition,reason=decision.reason,source_account_id=str(source.id),target_account_id=str(target.id),
            owner_org_id=str(source.owner_org_id),custodian_person_id=str(source.custodian_person_id),location_id=str(source.location_id),
            material_id=str(source.material_id),lot_id=str(source.lot_id) if source.lot_id else None,
            source_condition=source.condition_code,target_condition=target.condition_code,
            custody_assignment_id=str(assignments[0].id),quantity=format(line.quantity,'.3f'),
            serial_ids=sorted(str(s) for s in selected.serial_ids),movement_type=outline.movement.movement_type,
            ledger_cursor=snapshot.ledger_cursor,source_balance_version=balance.version,
            source_balance_quantity=format(balance.quantity,'.3f'),policy_fingerprint=[list(p) for p in fingerprint],holds=hold_basis)
        return dict(**document,plan_hash=sources._hash(document),checked_at=at)


def plan_document(preview):
    return {key:value for key,value in preview.items() if key not in {'plan_hash','checked_at'}}
