from dataclasses import replace
from uuid import uuid4
from unittest.mock import Mock

import pytest
from sqlalchemy import event, select

from app.inventory_models import Receipt, InventoryMovement
from app.formal_services import material_request_my_inbound_candidates as service
from app.formal_services.material_request_query import MaterialRequestReadError
from test_material_request_my_inbound import world, receipt_world, receiving_world, outbound_world, post, facts
from test_material_request_my_receipt import create as accept, payload, evidence
from test_formal_material_request_api import api_client

pytest_plugins = ('test_material_request_picking',)


def listing(world, **kwargs):
    db, actor, request, *_ = world
    return service.list_my_inbound_candidates(db, actor=actor, request_id=request.id, **kwargs)


def test_pending_projection_has_exact_accepted_details_without_creating_stock_accounts(world):
    db, actor, request, value = world
    before = facts(db)
    statements = []
    def capture(_conn, _cursor, sql, *_): statements.append(sql)
    event.listen(db.bind, 'before_cursor_execute', capture)
    try:
        result = listing(world)
    finally:
        event.remove(db.bind, 'before_cursor_execute', capture)
    assert result.can_post and result.request_version == request.version and result.person_id == actor.person_id
    assert len(result.items) == 1 and result.items[0].status == 'pending'
    row = result.items[0]
    assert row.receipt_id == value.receipt_id and row.detail.receipt_request_hash == value.receipt_request_hash
    assert row.detail.lines and all(line.accepted_qty != '0.000' for line in row.detail.lines)
    assert row.detail.inventory_transaction_id is None and row.detail.posted_at is None
    raw = result.model_dump_json()
    for field in ('from_account_id', 'to_account_id', 'stock_account_id', 'owner_org_id', 'idempotency_key', 'source_location_id'):
        assert field not in raw
    assert facts(db) == before and all(sql.lstrip().upper().startswith('SELECT') for sql in statements)


def test_posted_projection_requires_actual_ledger_and_original_command(world):
    db, _, _, _ = world
    posted = post(world)
    row = listing(world).items[0]
    assert row.status == 'posted' and row.detail.inventory_transaction_id == posted.inventory_transaction_id
    move = db.scalar(select(InventoryMovement).where(InventoryMovement.transaction_id == posted.inventory_transaction_id))
    move.quantity += 1; db.flush()
    row = listing(world).items[0]
    assert row.status == 'blocked' and row.detail is None


def test_corrupt_receipt_blocks_only_it_and_does_not_leak_details(world):
    db, _, request, value = world
    receipt = db.get(Receipt, value.receipt_id)
    corrupted = Receipt(id=uuid4(), receipt_no='CORRUPT-OWN', shipment_id=receipt.shipment_id,
        receiver_person_id=receipt.receiver_person_id, status=receipt.status, received_at=receipt.received_at,
        request_hash='0'*64, idempotency_key_hash='broken-receipt-idempotency-001', created_at=receipt.created_at)
    db.add(corrupted); db.flush()
    rows = {item.receipt_id: item for item in listing(world).items}
    assert rows[value.receipt_id].status == 'pending'
    assert rows[corrupted.id].status == 'blocked' and rows[corrupted.id].detail is None
    first = listing(world, limit=1)
    assert len(first.items) == 1 and first.next_after_id == first.items[0].receipt_id
    second = listing(world, limit=1, after_id=first.next_after_id)
    assert len(second.items) == 1 and second.next_after_id is None
    assert first.items[0].receipt_id.int < second.items[0].receipt_id.int


def test_rejected_only_receipt_stays_outside_inbound(receipt_world):
    value = payload(receipt_world).model_dump()
    line = value['lines'][0]
    file, _ = evidence(receipt_world)
    line.update(condition='rejected', rejected_qty=line['accepted_qty'], accepted_qty='0.000',
        rejected_serial_ids=line['accepted_serial_ids'], accepted_serial_ids=(), exception_evidence_file_id=file.id)
    accept(receipt_world, value)
    row = listing(receipt_world).items[0]
    assert row.status == 'no_accepted' and all(line.accepted_qty == '0.000' for line in row.detail.lines)


def test_no_receive_permission_still_reads_own_inbound(world, monkeypatch):
    db, actor, request, value = world
    current = replace(actor, entitlements=tuple(p for p in actor.entitlements if not (p.resource == 'material_request' and p.action == 'receive')))
    load = service.query._load_read_context
    monkeypatch.setattr(service.query, '_load_read_context', lambda *a, **kw: replace(load(*a, **kw), principal=current))
    result = listing(world)
    assert not result.can_post and result.items[0].status == 'pending'


def test_candidates_reject_unknown_request_and_bad_limit(world):
    db, actor, request, *_ = world
    for limit in (0, 21, True):
        with pytest.raises(MaterialRequestReadError): listing(world, limit=limit)
    with pytest.raises(MaterialRequestReadError):
        service.list_my_inbound_candidates(db, actor=actor, request_id=uuid4())


def test_candidate_http_is_no_store_and_never_commits(api_client, monkeypatch):
    client, db, principals, _, settings = api_client
    settings.material_request_writes_enabled = False
    request_id = uuid4()
    out = service.MyInboundCandidatesOut(request_id=request_id, request_no='REQ-TEST', request_version=1,
        person_id=principals['value'].person_id, can_post=True, items=(), next_after_id=None)
    mocked = Mock(return_value=out)
    monkeypatch.setattr(service, 'list_my_inbound_candidates', mocked)
    root = f'/api/v1/material-requests/{request_id}/my-inbounds/candidates'
    result = client.get(root)
    assert result.status_code == 200 and result.json()['items'] == [] and result.json()['can_post'] is False
    assert 'no-store' in result.headers['cache-control']
    assert client.get(root+'?limit=21').status_code == 422
    assert client.get(root+'?after_id=invalid').status_code == 422
    assert mocked.call_count == 1
    db.commit.assert_not_called()


@pytest.mark.parametrize('changed_field', ['recipient', 'shipment_target'])
def test_other_recipients_never_appear(world, changed_field):
    from app.inventory_models import Shipment
    db, _, _, value = world
    receipt = db.get(Receipt, value.receipt_id)
    if changed_field == 'recipient':
        receipt.receiver_person_id = uuid4()
    else:
        db.get(Shipment, receipt.shipment_id).target_person_id = uuid4()
    db.flush()
    assert listing(world).items == ()


def test_reader_rejects_stale_principal_and_revoked_inventory_permission(world):
    from app.foundation_models import Permission, Role, RolePermission
    db, actor, request, _ = world
    with pytest.raises(MaterialRequestReadError):
        service.list_my_inbound_candidates(db, actor=replace(actor, authorization_version=actor.authorization_version+1), request_id=request.id)
    role = db.scalar(select(Role.id).where(Role.code == 'technician'))
    permission = db.scalar(select(Permission.id).where(Permission.resource == 'inventory', Permission.action == 'read'))
    db.query(RolePermission).filter(RolePermission.role_id == role, RolePermission.permission_id == permission).update({'effect': 'deny'})
    db.flush()
    with pytest.raises(MaterialRequestReadError): listing(world)
