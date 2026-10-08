"""Warehouse inbound composition; native posting/migration remain separate gates."""
from decimal import Decimal
from uuid import uuid4
import pytest
from sqlalchemy import select, event, func

from app.inventory_models import StockAccount, StockBalance, InventoryTransaction, InventoryLedgerHead, InventoryMovement, SerialCurrentPosition
from app.foundation_models import Permission, RolePermission, AuditEvent, OutboxEvent, NotificationEvent, NotificationPersonTarget
from app.material_request_rejection_inbound_schema import inbounds, parts, serials
from app.material_request_rejection_inbound_schemas import RejectionInboundIn
from app.formal_services import material_request_rejection_inbound as service
from app.formal_services.material_request_query import MaterialRequestReadError
from test_material_request_rejection_receipt import (
    world as acceptance_world, receiving_case, progress_world, registration_world,
    receipt_world, receiving_world, outbound_world, create as accept, payload, stock,
)
from test_material_request_my_receipt import evidence
from test_material_request_draft_service import SECRET
pytest_plugins = ('test_material_request_picking',)
KEY = 'rejection-warehouse-inbound-original-0001'


@pytest.fixture
def world(acceptance_world, request):
    db = acceptance_world[0]
    for table in (inbounds, parts, serials):
        table.create(db.get_bind(), checkfirst=True)
    value = payload(acceptance_world)
    if getattr(request, 'param', None) in ('damage', 'mixed'):
        proof, _ = evidence((db, acceptance_world[1]), key='inbound-condition-proof-0001')
        data = value.model_dump()
        # The inherited SN case has one item; mixed quantity is covered here,
        # while true multi-SN splits belong to the native expanded fixture.
        amount = (Decimal('.050') if request.param == 'mixed' and not value.amounts.accepted_serial_verifications
                  else value.amounts.accepted_qty)
        data['amounts'].update(damaged_qty=amount,
            damaged_serial_ids=tuple(p.serial_id for p in value.amounts.accepted_serial_verifications),
            exceptions=[dict(exception_type='damaged', description='验收破损份额独立转坏件', evidence_file_id=proof.id)])
        value = service.acceptance.RejectionReceiptIn.model_validate(data)
    receipt = accept(acceptance_world, value)
    return acceptance_world, receipt


def preview(world):
    values, receipt = world
    return service.plans.preview(values[0], actor=values[1], return_id=receipt.return_id, receipt_id=receipt.receipt_id)


def command(world):
    values, receipt = world
    return RejectionInboundIn(expected_request_version=values[3].version,
        receipt_request_hash=receipt.request_hash, expected_plan_hash=preview(world)['plan_hash'], reason='来源仓核验后独立入账')


def post(world, payload=None, key=KEY):
    values, receipt = world
    return service.post(values[0], actor=values[1], return_id=receipt.return_id, receipt_id=receipt.receipt_id,
        payload=payload or command(world), idempotency_key=key, secret=SECRET, trace_request_id='trace-' + key)


def recover(world, **kwargs):
    values, receipt = world
    return service.command_status(values[0], actor=values[1], return_id=receipt.return_id, receipt_id=receipt.receipt_id, **kwargs)


def test_plan_is_read_only_exact_source_custody_and_partitions(world):
    values, receipt = world
    db, actor, _, request, _, location, _, parent = values
    before = stock(db)
    count = len(tuple(db.scalars(select(StockAccount))))
    statements = []
    def capture(_conn, _cursor, sql, *_): statements.append(sql)
    event.listen(db.bind, 'before_cursor_execute', capture)
    try:
        result = preview(world)
        assert preview(world) == result
    finally:
        event.remove(db.bind, 'before_cursor_execute', capture)
    plan = result['plan']
    assert plan['source_account_id'] == str(parent['in_transit_account_id'])
    assert plan['receipt_id'] == str(receipt.receipt_id)
    assert plan['actor_person_id'] == str(actor.person_id)
    assert sum(Decimal(p['quantity']) for p in plan['parts']) == receipt.amounts.accepted_qty
    for piece in plan['parts']:
        assert piece['target_dimensions']['location_id'] == str(location.id)
        assert piece['target_dimensions']['custodian_person_id'] == str(actor.person_id)
        assert piece['target_dimensions']['availability_bucket'] == 'available'
    assert stock(db) == before and len(tuple(db.scalars(select(StockAccount)))) == count
    assert all(s.lstrip().upper().startswith('SELECT') and 'FOR UPDATE' not in s.upper() for s in statements)


@pytest.mark.parametrize('kind', ['hash', 'version', 'receipt', 'balance'])
def test_stale_plan_refuses_before_accounts_or_inventory_write(world, kind):
    values, receipt = world; db = values[0]
    value = command(world)
    if kind == 'hash': value = value.model_copy(update={'expected_plan_hash': '0' * 64})
    if kind == 'version': value = value.model_copy(update={'expected_request_version': value.expected_request_version - 1})
    if kind == 'receipt': value = value.model_copy(update={'receipt_request_hash': '0' * 64})
    if kind == 'balance':
        db.get(StockBalance, values[-1]['in_transit_account_id']).version += 1
        db.flush()
    before = stock(db); accounts = tuple(db.scalars(select(StockAccount.id)))
    with pytest.raises(MaterialRequestReadError): post(world, value)
    assert stock(db) == before and tuple(db.scalars(select(StockAccount.id))) == accounts
    assert db.execute(select(inbounds)).first() is None


def test_unestablished_fixture_cannot_bypass_real_opening_gate(world):
    db = world[0][0]
    db.commit()
    before = stock(db); accounts = tuple(db.scalars(select(StockAccount.id)))
    with pytest.raises(service.posting.InventoryPostingError) as error:
        post(world)
    assert error.value.code == 'inventory_opening_not_established'
    db.rollback()
    assert stock(db) == before and tuple(db.scalars(select(StockAccount.id))) == accounts
    assert db.execute(select(inbounds)).first() is None


@pytest.fixture
def posting_world(world, monkeypatch):
    # The inherited request chain uses synthetic earlier postings, not approved
    # opening stocktakes. Isolate only that absent opening history here. Current
    # permissions, real posting, balances/SN, ledger/audit/outbox and recovery
    # stay enabled. A full formal-opening native PG16 gate is still required.
    posting = service.posting
    monkeypatch.setattr(posting, '_plan_and_lock_terminal_opening_graphs', lambda *a, **k: ((), None, None))
    monkeypatch.setattr(posting, '_require_established_unfrozen_scopes', lambda *a, **k: None)
    db = world[0][0]
    from app.foundation_models import AuditChainHead
    db.add(AuditChainHead(id=uuid4(), stream_key='inventory', last_event_id=None, last_hash=None, version=0))
    db.scalar(select(InventoryLedgerHead)).next_cursor = db.scalar(select(func.max(InventoryTransaction.ledger_cursor))) + 1
    db.flush()
    return world


def test_posting_and_exact_read_only_recovery(posting_world):
    world = posting_world
    db = world[0][0]
    value = command(world)
    version = world[0][3].version
    result = post(world, value)
    assert not result.replayed and world[0][3].version == version
    expected = stock(db)
    statements = []
    def capture(_conn, _cursor, sql, *_): statements.append(sql)
    event.listen(db.bind, 'before_cursor_execute', capture)
    try:
        by_trace = recover(world, trace_request_id='trace-' + KEY)
        by_key = recover(world, idempotency_key=KEY, secret=SECRET)
    finally:
        event.remove(db.bind, 'before_cursor_execute', capture)
    assert by_trace == by_key and by_key.inbound_id == result.inbound_id and by_key.replayed
    assert all(s.lstrip().upper().startswith('SELECT') and 'FOR UPDATE' not in s.upper() for s in statements)
    assert post(world, value).inbound_id == result.inbound_id and stock(db) == expected
    with pytest.raises(MaterialRequestReadError): post(world, value, key=KEY + '-different')
    with pytest.raises(MaterialRequestReadError): post(world, value.model_copy(update={'reason': '不同请求内容'}))


def test_write_revocation_preserves_read_only_original_result(posting_world):
    world = posting_world; db = world[0][0]; value = command(world)
    result = post(world, value)
    permission = db.scalar(select(Permission.id).where(Permission.resource == 'stock_operation', Permission.action == 'receive_return'))
    db.query(RolePermission).filter(RolePermission.permission_id == permission).update({'effect': 'deny'}); db.flush()
    expected = stock(db)
    assert recover(world, trace_request_id='trace-' + KEY).inbound_id == result.inbound_id
    assert post(world, value).inbound_id == result.inbound_id
    with pytest.raises(MaterialRequestReadError) as error: post(world, value, key=KEY + '-new')
    assert error.value.category == 'forbidden' and stock(db) == expected


@pytest.mark.parametrize('kind', ['movement', 'transaction', 'part', 'audit', 'notification', 'outbox'])
def test_recovery_rejects_corrupt_immutable_fact(posting_world, kind):
    world = posting_world; db = world[0][0]; result = post(world)
    if kind == 'movement': db.scalar(select(InventoryMovement).where(InventoryMovement.transaction_id == result.inventory_transaction_id)).quantity += Decimal('.001')
    if kind == 'transaction': db.get(InventoryTransaction, result.inventory_transaction_id).source_document_id = str(uuid4())
    if kind == 'part': db.execute(parts.update().where(parts.c.inbound_id == result.inbound_id).values(quantity=Decimal('999')))
    if kind == 'audit': db.scalar(select(AuditEvent).where(AuditEvent.aggregate_type == service.facts.AGGREGATE)).event_hash = '0' * 64
    if kind == 'notification': db.scalar(select(NotificationEvent).where(NotificationEvent.business_type == service.facts.AGGREGATE)).payload_jsonb = {}
    if kind == 'outbox': db.scalar(select(OutboxEvent).where(OutboxEvent.aggregate_type == service.facts.AGGREGATE)).payload_jsonb = {}
    db.flush()
    with pytest.raises(MaterialRequestReadError) as error: recover(world, trace_request_id='trace-' + KEY)
    assert error.value.category == 'service_unavailable'


def test_late_notification_failure_rolls_back_inventory_and_new_accounts(posting_world, monkeypatch):
    world = posting_world; db = world[0][0]; value = command(world); db.commit()
    before = stock(db); accounts = tuple(db.scalars(select(StockAccount.id)))
    counts = {model: db.scalar(select(func.count()).select_from(model)) for model in (AuditEvent, OutboxEvent, NotificationEvent, NotificationPersonTarget)}
    def broken(*args, **kwargs): raise RuntimeError('injected final notification failure')
    monkeypatch.setattr(service, 'record_business_notification', broken)
    with pytest.raises(RuntimeError, match='injected final notification failure'): post(world, value)
    db.rollback()
    assert stock(db) == before and tuple(db.scalars(select(StockAccount.id))) == accounts
    assert db.execute(select(inbounds)).first() is None
    assert all(db.scalar(select(func.count()).select_from(model)) == count for model, count in counts.items())


def test_generic_reversal_cannot_rename_original_business_source(posting_world):
    world = posting_world; db = world[0][0]; result = post(world)
    from types import SimpleNamespace
    for kind in ('manual_adjustment', service.plans.SOURCE):
        command = SimpleNamespace(source_document_type=kind, original_transaction_id=result.inventory_transaction_id)
        with pytest.raises(service.posting.InventoryPostingError) as error:
            service.posting._require_generic_reversal_origin(db, command)
        assert error.value.code == 'rejection_inbound_reversal_requires_command'


@pytest.mark.parametrize('world', ['damage', 'mixed'], indirect=True)
def test_damaged_partition_moves_only_accepted_assets(posting_world):
    world = posting_world; db = world[0][0]
    proposal = preview(world)['plan']; value = command(world)
    result = post(world, value)
    moves = tuple(db.scalars(select(InventoryMovement).where(InventoryMovement.transaction_id == result.inventory_transaction_id)))
    assert sum(m.quantity for m in moves) == world[1].amounts.accepted_qty
    damaged = sum(m.quantity for m in moves if db.get(StockAccount, m.to_account_id).condition_code == 'damaged')
    assert damaged == world[1].amounts.damaged_qty
    for part in proposal['parts']:
        for sn in part['serial_ids']:
            from uuid import UUID
            assert db.get(SerialCurrentPosition, UUID(sn)).stock_account_id == UUID(part['target_account_id'])
    assert recover(world, idempotency_key=KEY, secret=SECRET).inbound_id == result.inbound_id


def test_recovery_uses_original_cursor_after_later_stock_movement(posting_world):
    world = posting_world; db = world[0][0]; actor = world[0][1]
    result = post(world)
    part = db.execute(select(parts).where(parts.c.inbound_id == result.inbound_id)).mappings().one()
    source = db.get(StockAccount, part['target_account_id'])
    target = StockAccount(id=uuid4(), **{k: getattr(source, k) for k in
        ('owner_org_id', 'custodian_person_id', 'location_id', 'material_id', 'condition_code', 'lot_id')}, availability_bucket='frozen')
    db.add(target); db.flush()
    sn = tuple(db.scalars(select(serials.c.serial_id).where(serials.c.inbound_id == result.inbound_id).order_by(serials.c.serial_id)))
    posting = service.posting
    later = posting.InventoryPostingCommand(transaction_no='TEST-LATER-' + uuid4().hex[:12], movement_type='transfer',
        source_document_type='test_followup_freeze', source_document_id=str(uuid4()), posting_key=uuid4().hex,
        effective_at=service.acceptance.lifecycle._database_now(db), movements=(posting.InventoryMovementCommand(
            from_account_id=source.id, to_account_id=target.id, quantity=part['quantity'], serial_ids=sn),))
    posting.post_inventory_transaction(db, actor=actor, command=later, idempotency_key=uuid4().hex, request_id=uuid4().hex)
    assert db.get(StockBalance, source.id).quantity == 0
    assert recover(world, trace_request_id='trace-' + KEY).inbound_id == result.inbound_id
