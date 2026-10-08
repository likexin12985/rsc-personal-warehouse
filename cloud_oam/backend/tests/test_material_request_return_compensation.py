"""Forward compensation composition; inherited opening is synthetic, not PG16/UAT."""
from decimal import Decimal
from uuid import uuid4
import pytest
from sqlalchemy import select, event
from sqlalchemy.exc import IntegrityError

from app.demand_models import MaterialRequestLine
from app.foundation_models import AuditEvent, Permission, RolePermission
from app.inventory_models import InventoryMovement
from app.material_request_return_compensation_schema import compensations
from app.material_request_return_compensation_schemas import ReturnCompensationIn
from app.formal_services import material_request_return_compensation as service
from app.formal_services.material_request_query import MaterialRequestReadError
from app.formal_services.material_request_lifecycle import MaterialRequestLifecycleError
from app.formal_services.material_request_return_posted_history import verified_posted_returns
from app.formal_services.material_request_return_quantities import partition_returned
from app.formal_services.material_request_remainder import partition
from test_material_request_rejection_inbound import (
    world, posting_world, acceptance_world, receiving_case, progress_world, registration_world,
    receipt_world, receiving_world, outbound_world, post, stock,
)
from test_material_request_draft_service import SECRET

pytest_plugins = ('test_material_request_picking',)
KEY = 'return-compensation-original-0001'


@pytest.fixture
def case(posting_world):
    values, _ = posting_world
    db, _, sender, request, *_ = values
    compensations.create(db.get_bind(), checkfirst=True)
    inbound = post(posting_world)
    row = verified_posted_returns(db, request=request)[inbound.inbound_id]
    value = ReturnCompensationIn(expected_request_version=request.version, inbound_id=inbound.inbound_id,
        inbound_request_hash=inbound.request_hash, inbound_plan_hash=inbound.plan_hash,
        cancelled_qty=row.origin['accepted_qty'], reason='来源仓已实际入账，本次需求不再补发')
    return db, sender, request, value, posting_world


def cancel(case, value=None, key=KEY, actor=None, trace=None):
    db, sender, request, payload, _ = case
    return service.cancel_returned_demand(db, actor=actor or sender, request_id=request.id, payload=value or payload,
        idempotency_key=key, secret=SECRET, trace_request_id=trace or 'trace-' + key)


def recover(case, **kwargs):
    db, sender, request, _, _ = case
    return service.command_status(db, actor=sender, request_id=request.id, **kwargs)


def test_exact_compensation_and_readonly_recovery_preserve_stock_and_original_demand(case):
    db, sender, request, value, _ = case
    lines = tuple(db.scalars(select(MaterialRequestLine).where(MaterialRequestLine.request_id == request.id)))
    expected = stock(db), request.version, request.status, [(r.id, r.final_approved_qty, r.cancelled_qty) for r in lines]
    first = cancel(case)
    assert not first.replayed and first.inbound_id == value.inbound_id and first.cancelled_qty == value.cancelled_qty
    assert first.cancellation_scope == 'posted_return_compensation'
    statements = []
    def capture(_conn, _cursor, sql, *_): statements.append(sql)
    event.listen(db.bind, 'before_cursor_execute', capture)
    try:
        by_key = recover(case, idempotency_key=KEY, secret=SECRET)
        by_trace = recover(case, trace_request_id='trace-' + KEY)
        quantities = service.verified_return_compensated_quantities(db, request=request)
    finally:
        event.remove(db.bind, 'before_cursor_execute', capture)
    assert by_key == by_trace and by_key.compensation_id == first.compensation_id and by_key.replayed
    assert all(s.lstrip().upper().startswith('SELECT') and 'FOR UPDATE' not in s.upper() for s in statements)
    assert quantities == {first.request_line_id: Decimal(first.cancelled_qty)}
    assert cancel(case).compensation_id == first.compensation_id
    assert (stock(db), request.version, request.status, [(r.id, r.final_approved_qty, r.cancelled_qty) for r in lines]) == expected


@pytest.mark.parametrize('change', ['version', 'inbound', 'hash', 'plan', 'under', 'over'])
def test_unposted_cross_or_stale_compensation_refused_before_write(case, change):
    db, _, _, value, _ = case
    updates = dict(version={'expected_request_version': value.expected_request_version - 1}, inbound={'inbound_id': uuid4()},
        hash={'inbound_request_hash': '0' * 64}, plan={'inbound_plan_hash': '0' * 64},
        under={'cancelled_qty': '0.001'}, over={'cancelled_qty': '999.000'})
    before = stock(db)
    with pytest.raises(MaterialRequestReadError): cancel(case, value.model_copy(update=updates[change]))
    assert db.execute(select(compensations)).first() is None and stock(db) == before


def test_original_requester_owns_compensation_not_warehouse_custodian(case):
    db, _, request, _, posted = case
    warehouse = posted[0][1]
    with pytest.raises((MaterialRequestLifecycleError, MaterialRequestReadError)):
        cancel(case, actor=warehouse)
    assert db.execute(select(compensations)).first() is None
    original = cancel(case)
    assert service.command_status(db, actor=warehouse, request_id=request.id, trace_request_id='trace-' + KEY) is None
    assert original.inbound_id == case[3].inbound_id


def test_full_source_history_does_not_require_current_warehouse_grants(case, monkeypatch):
    def forbidden(*args, **kwargs): raise AssertionError('warehouse current authorization must not be called')
    monkeypatch.setattr(service.facts.acceptance.receiving, '_authorized', forbidden)
    result = cancel(case)
    assert result.inbound_id == case[3].inbound_id


def test_revoked_cancel_denies_new_command_preserves_original_read_only_recovery(case):
    db = case[0]; result = cancel(case)
    permission = db.scalar(select(Permission.id).where(Permission.resource == 'material_request', Permission.action == 'cancel'))
    assert permission is not None
    db.query(RolePermission).filter(RolePermission.permission_id == permission).update({'effect': 'deny'}); db.flush()
    assert recover(case, trace_request_id='trace-' + KEY).compensation_id == result.compensation_id
    assert cancel(case).compensation_id == result.compensation_id
    with pytest.raises((MaterialRequestLifecycleError, MaterialRequestReadError)) as exc:
        cancel(case, key=KEY + '-new')
    assert exc.value.category == 'forbidden'


def test_repeat_source_and_changed_key_contents_are_rejected(case):
    cancel(case)
    with pytest.raises(MaterialRequestReadError) as exc: cancel(case, key=KEY + '-again')
    assert exc.value.code.endswith('already_compensated')
    with pytest.raises(MaterialRequestReadError) as exc:
        cancel(case, case[3].model_copy(update={'reason': '不同补偿内容'}))
    assert exc.value.code.endswith('key_reused')


@pytest.mark.parametrize('kind', ['quantity', 'request', 'line', 'evidence', 'audit', 'source_movement'])
def test_recovery_rejects_corrupt_original_or_stock_fact(case, kind):
    db, _, _, _, _ = case; original = cancel(case)
    updates = dict(quantity={'cancelled_qty': Decimal('999')}, request={'request_id': uuid4()},
        line={'request_line_id': uuid4()}, evidence={'evidence_sha256': '0' * 64})
    if kind in ('request', 'line'):
        with pytest.raises(IntegrityError, match='FOREIGN KEY constraint failed'), db.begin_nested():
            db.execute(compensations.update().where(compensations.c.id == original.compensation_id).values(**updates[kind]))
        assert recover(case, trace_request_id='trace-' + KEY).compensation_id == original.compensation_id
        return
    if kind in updates:
        db.execute(compensations.update().where(compensations.c.id == original.compensation_id).values(**updates[kind]))
    elif kind == 'audit':
        db.scalar(select(AuditEvent).where(AuditEvent.aggregate_type == service.AGGREGATE)).event_hash = '0' * 64
    else:
        source = verified_posted_returns(db, request=case[2])[case[3].inbound_id]
        db.scalar(select(InventoryMovement).where(InventoryMovement.transaction_id == source.row['posting_transaction_id'])).quantity += Decimal('.001')
    db.flush()
    # Full scoped history detects even a header re-bound to a different request.
    with pytest.raises(MaterialRequestReadError): service.verified_return_compensated_quantities(db, request=case[2])


def test_failed_audit_rolls_back_new_compensation_without_stock_changes(case, monkeypatch):
    db = case[0]; db.commit(); before = stock(db)
    def broken(*args, **kwargs): raise RuntimeError('injected compensation audit failure')
    monkeypatch.setattr(service, 'append_audit_event', broken)
    with pytest.raises(RuntimeError, match='injected compensation'): cancel(case)
    db.rollback()
    assert db.execute(select(compensations)).first() is None and stock(db) == before


def test_posted_return_and_compensation_change_only_forward_quantity_evidence(case):
    from app.formal_services.material_request_completion import completion_quantities
    from app.formal_services.material_request_return_quantities import remaining_with_returns
    db, actor, request, value, _ = case
    legacy = completion_quantities(db, actor=actor, request_id=request.id)
    before = remaining_with_returns(db, actor=actor, request_id=request.id)
    assert before.schema_version == '2.0'
    assert before.lines[0].returned_pending_compensation_qty == value.cancelled_qty
    assert before.lines[0].return_compensated_qty == '0.000'
    fact = cancel(case)
    forward = completion_quantities(db, actor=actor, request_id=request.id, _include_returns=True)
    after = remaining_with_returns(db, actor=actor, request_id=request.id)
    old_line = next(x for x in legacy.lines if x.request_line_id == fact.request_line_id)
    new_line = next(x for x in forward.lines if x.request_line_id == fact.request_line_id)
    assert new_line.posted_qty == old_line.posted_qty == '0.000'
    assert Decimal(new_line.cancelled_qty) == Decimal(old_line.cancelled_qty) + Decimal(fact.cancelled_qty)
    assert Decimal(new_line.remaining_qty) == Decimal(old_line.remaining_qty) - Decimal(fact.cancelled_qty)
    assert after.lines[0].returned_pending_compensation_qty == '0.000'
    assert after.lines[0].return_compensated_qty == fact.cancelled_qty
    assert completion_quantities(db, actor=actor, request_id=request.id) == legacy


def test_return_settlement_requires_independent_compensation_for_exact_original_receipt(case):
    from app.formal_services.material_request_closure import _return_settlement
    assert _return_settlement(case[0], case[2]) == []
    result = cancel(case)
    rows = _return_settlement(case[0], case[2])
    assert len(rows) == 1 and rows[0]['compensation_id'] == str(result.compensation_id)
    assert rows[0]['original_receipt_line_id'] == verified_posted_returns(
        case[0], request=case[2])[result.inbound_id].origin['original_receipt_line_id']


def quantities(**updates):
    return dict(line_id=uuid4(), **({k: Decimal(v) for k, v in dict(approved='3', cancelled='1', posted='1',
        reserved='2', released='0', picked='2', dispatched='2', shipped='2', accepted='1', rejected='1').items()} | updates))


def test_return_partition_keeps_unfulfilled_cancellation_and_personal_inbound_distinct():
    base = quantities()
    legacy = partition(**base)
    pending = partition_returned(**base, returned=Decimal(1), return_compensated=Decimal(0))
    complete = partition_returned(**base, returned=Decimal(1), return_compensated=Decimal(1))
    assert legacy.rejected_unsettled_qty == '1.000' and legacy.cancelled_qty == '1.000'
    assert pending.returned_pending_compensation_qty == '1.000' and pending.rejected_unsettled_qty == '0.000'
    assert complete.posted_qty == '1.000' and complete.cancelled_qty == '2.000'
    assert complete.unfulfilled_cancelled_qty == '1.000' and complete.return_compensated_qty == '1.000'
    assert complete.unreserved_qty == '0.000' and complete.returned_pending_compensation_qty == '0.000'
    assert partition(**base) == legacy


@pytest.mark.parametrize(('returned', 'compensated'), [('2', '1'), ('1', '2'), ('-1', '0'), ('1.0001', '0'), ('NaN', '0')])
def test_contradictory_return_quantities_never_clamp_to_zero(returned, compensated):
    with pytest.raises(MaterialRequestReadError):
        partition_returned(**quantities(), returned=Decimal(returned), return_compensated=Decimal(compensated))


def returned_remaining_fixture():
    from app.material_request_return_compensation_schemas import ReturnedRemainderAssessmentOut
    from app.material_request_remaining_cancel_schemas import RemainingCancelLine
    line=partition_returned(**quantities(cancelled=Decimal(0)), returned=Decimal(1), return_compensated=Decimal(1))
    assessment=ReturnedRemainderAssessmentOut(request_id=uuid4(),revision_id=uuid4(),request_version=13,
        open_supply_tasks=0,pending_substitutions=0,lines=(line,))
    return assessment,(RemainingCancelLine(request_line_id=line.request_line_id,cancelled_qty='1.000'),)


def test_versioned_remaining_cancel_covers_only_unfulfilled_part_after_return_compensation():
    from app.material_request_return_compensation_schemas import require_settled_returned_remainder
    from app.material_request_remaining_cancel_schemas import require_settled_remainder
    assessment, requested=returned_remaining_fixture()
    assert require_settled_returned_remainder(assessment,requested)==assessment
    assert assessment.lines[0].posted_qty=='1.000' and assessment.lines[0].return_compensated_qty=='1.000'
    with pytest.raises(ValueError): require_settled_remainder(assessment.model_dump(),requested)


@pytest.mark.parametrize('kind',['under','over','foreign_line','pending_return','prior_unfulfilled_cancel','no_return','open_supply'])
def test_versioned_remaining_cancel_refuses_incomplete_or_duplicate_compensation(kind):
    from app.material_request_return_compensation_schemas import require_settled_returned_remainder
    from app.material_request_remaining_cancel_schemas import RemainingCancelLine
    assessment,requested=returned_remaining_fixture()
    data=assessment.model_dump(); line=data['lines'][0]
    if kind in ('under','over','foreign_line'):
        requested=(RemainingCancelLine(request_line_id=uuid4() if kind=='foreign_line' else requested[0].request_line_id,
            cancelled_qty='0.500' if kind=='under' else '2.000' if kind=='over' else '1.000'),)
    if kind=='pending_return': line.update(return_compensated_qty='0.000',cancelled_qty='0.000',returned_pending_compensation_qty='1.000')
    if kind=='prior_unfulfilled_cancel': line.update(unfulfilled_cancelled_qty='1.000',cancelled_qty='2.000',unreserved_qty='0.000')
    if kind=='no_return': line.update(return_compensated_qty='0.000',cancelled_qty='0.000',posted_qty='2.000')
    if kind=='open_supply': data['open_supply_tasks']=1
    with pytest.raises(ValueError): require_settled_returned_remainder(data,requested)


def test_public_detail_remainder_and_completion_agree_after_posted_return(case):
    from app.formal_services import material_request_query as query
    from app.formal_services.material_request_completion import completion_quantities
    from app.formal_services.material_request_return_quantities import remaining_with_returns
    db, actor, request, payload, _ = case
    before = query.material_request_detail(db, actor=actor, request_id=request.id)
    compensation = cancel(case)
    stock_before = stock(db)
    statements = []
    def capture(_conn, _cursor, sql, *_): statements.append(sql)
    event.listen(db.bind, 'before_cursor_execute', capture)
    try:
        detail = query.material_request_detail(db, actor=actor, request_id=request.id)
        page = query.list_material_requests(db, actor=actor, limit=100)
        completion = completion_quantities(db, actor=actor, request_id=request.id, _include_returns=True)
        remainder = remaining_with_returns(db, actor=actor, request_id=request.id)
    finally:
        event.remove(db.bind, 'before_cursor_execute', capture)
    line = next(r for r in detail.lines if r.request_line_id == compensation.request_line_id)
    assert Decimal(line.cancelled_qty) == Decimal(payload.cancelled_qty)
    assert next(r for r in before.lines if r.request_line_id == line.request_line_id).cancelled_qty == Decimal('0.000')
    assert Decimal(completion.lines[0].cancelled_qty) == Decimal(remainder.lines[0].cancelled_qty) == line.cancelled_qty
    assert remainder.schema_version == '2.0'
    assert remainder.lines[0].return_compensated_qty == payload.cancelled_qty
    assert remainder.lines[0].unfulfilled_cancelled_qty == '0.000'
    assert next(r for r in page.items if r.request_id == request.id).allowed_actions == detail.allowed_actions
    assert 'cancel' not in detail.allowed_actions
    assert stock(db) == stock_before
    assert all(s.lstrip().upper().startswith('SELECT') and 'FOR UPDATE' not in s.upper() for s in statements)


@pytest.mark.parametrize('page', [False, True])
def test_public_query_never_hides_corrupt_return_compensation(case, page):
    from app.formal_services import material_request_query as query
    db, actor, request, *_ = case
    fact = cancel(case)
    db.execute(compensations.update().where(compensations.c.id == fact.compensation_id)
        .values(evidence_sha256='0' * 64))
    with pytest.raises(MaterialRequestReadError):
        if page:
            query.list_material_requests(db, actor=actor, limit=100)
        else:
            query.material_request_detail(db, actor=actor, request_id=request.id)


def test_compensation_candidates_show_real_sources_and_keep_history_after_revocation(case):
    db, actor, request, payload, _ = case
    before = service.candidates(db, actor=actor, request_id=request.id)
    assert len(before.items) == 1 and before.items[0].compensate_permitted
    assert before.items[0].inbound_id == payload.inbound_id
    assert before.items[0].inbound_plan_hash == payload.inbound_plan_hash
    assert before.items[0].quantity == payload.cancelled_qty
    fact = cancel(case)
    permission = db.scalar(select(Permission.id).where(Permission.resource == 'material_request', Permission.action == 'cancel'))
    db.query(RolePermission).filter(RolePermission.permission_id == permission).update({'effect': 'deny'}); db.flush()
    after = service.candidates(db, actor=actor, request_id=request.id)
    assert not after.items[0].compensate_permitted and after.items[0].compensation.compensation_id == fact.compensation_id
    fingerprint = service.lifecycle._canonical_hash(payload.model_dump(mode='json'))
    assert recover(case, trace_request_id='trace-' + KEY, request_fingerprint=fingerprint).compensation_id == fact.compensation_id
    with pytest.raises(MaterialRequestReadError) as caught:
        recover(case, trace_request_id='trace-' + KEY, request_fingerprint='0' * 64)
    assert caught.value.code.endswith('fingerprint_mismatch')
