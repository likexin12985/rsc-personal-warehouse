from decimal import Decimal
import pytest
from sqlalchemy import event, select
from app.foundation_models import Permission, RolePermission, RoleAssignment
from app.inventory_models import StockLocation
from app.formal_services import material_request_rejection_warehouse as service
from app.formal_services.material_request_query import MaterialRequestReadError
from test_material_request_rejection_inbound import (world, posting_world, acceptance_world, receiving_case,
    progress_world, registration_world, receipt_world, receiving_world, outbound_world, post, command, recover, KEY, SECRET)
pytest_plugins = ('test_material_request_picking',)


def read(world):
    values, receipt = world
    return service.detail(values[0], actor=values[1], return_id=receipt.return_id)


def test_current_custodian_reads_independent_accepted_and_pending_quantities(world):
    values, receipt = world
    detail = read(world)
    assert detail.source.status == 'handed_over'
    assert Decimal(detail.accepted_qty) == receipt.amounts.accepted_qty
    assert detail.accepted_qty == detail.pending_inbound_qty
    assert detail.posted_qty == detail.unconfirmed_qty == '0.000'
    assert not detail.receive_permitted and detail.receipts[0].post_permitted
    page = service.inbox(values[0], actor=values[1])
    assert len(page.items) == 1 and page.items[0].detail == detail
    preview = service.preview(values[0], actor=values[1], return_id=receipt.return_id, receipt_id=receipt.receipt_id)
    assert sum(Decimal(p.quantity) for p in preview.parts) == receipt.amounts.accepted_qty
    assert preview.target_location_id == detail.source.target_location_id
    document = preview.model_dump(mode='json')
    assert 'plan' not in document and 'source_account_id' not in document
    assert all('target_account_id' not in p for p in document['parts'])


def test_posted_history_stays_readonly_after_receiving_permission_revoked(posting_world):
    values, receipt = posting_world; db, actor = values[:2]
    value = command(posting_world); result = post(posting_world, value)
    context = service.acceptance._context(db, actor, receipt.return_id)
    role = db.scalar(select(RoleAssignment.role_id).where(RoleAssignment.id == context[-1][1]))
    permission = db.scalar(select(Permission.id).where(Permission.resource == 'stock_operation', Permission.action == 'receive_return', Permission.field_code == ''))
    db.scalar(select(RolePermission).where(RolePermission.role_id == role, RolePermission.permission_id == permission)).effect = 'deny'
    db.flush()
    statements = []
    def capture(_conn, _cursor, statement, *_): statements.append(statement)
    event.listen(db.bind, 'before_cursor_execute', capture)
    try:
        detail = read(posting_world)
        assert detail.posted_qty == detail.accepted_qty and detail.pending_inbound_qty == '0.000'
        assert detail.receipts[0].inbound.inbound_id == result.inbound_id
        assert not detail.receive_permitted and not detail.receipts[0].post_permitted
        assert recover(posting_world, idempotency_key=KEY, secret=SECRET,
            request_fingerprint=service.acceptance.lifecycle._canonical_hash(value.model_dump(mode='json'))).inbound_id == result.inbound_id
        with pytest.raises(MaterialRequestReadError, match='指纹不一致'):
            recover(posting_world, idempotency_key=KEY, secret=SECRET, request_fingerprint='0' * 64)
    finally: event.remove(db.bind, 'before_cursor_execute', capture)
    assert all(s.lstrip().upper().startswith('SELECT') for s in statements)


def test_custody_change_hides_inbox_and_refuses_exact_detail(world):
    values, receipt = world; db, actor = values[:2]
    source = read(world).source
    db.get(StockLocation, source.target_location_id).custodian_person_id = values[3].requester_person_id
    db.flush()
    assert service.inbox(db, actor=actor).items == ()
    with pytest.raises(MaterialRequestReadError, match='范围内没有'):
        read(world)


def test_corrupt_history_is_object_local_block_not_empty_or_postable(world):
    values, receipt = world; db, actor = values[:2]
    db.execute(service.acceptance.receipts.update().where(service.acceptance.receipts.c.id == receipt.receipt_id).values(reason='tampered'))
    page = service.inbox(db, actor=actor)
    assert len(page.items) == 1 and page.items[0].verification_status == 'blocked'
    assert page.items[0].detail is None
    with pytest.raises(MaterialRequestReadError, match='不一致'):
        read(world)


def test_collection_refuses_audit_change_and_requester_scope(world, monkeypatch):
    values, _ = world
    from app.formal_access import load_formal_principal
    requester = load_formal_principal(values[0], values[3].requester_user_id)
    with pytest.raises(MaterialRequestReadError, match='仅当前来源仓'):
        service.inbox(values[0], actor=requester)
    old = service._locations; calls = 0
    def changed(db, actor):
        nonlocal calls
        calls += 1
        return old(db, actor) if calls == 1 else ()
    monkeypatch.setattr(service, '_locations', changed)
    with pytest.raises(MaterialRequestReadError, match='读取期间'):
        service.inbox(values[0], actor=values[1])
