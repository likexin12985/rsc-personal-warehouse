"""Recipient composition tests; the PG16 gate exercises the actual ledger."""
from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import event, func, select

from app.demand_models import MaterialRequestCommand
from app.formal_access import load_formal_principal
from app.foundation_models import AuditEvent, OutboxEvent
from app.inventory_models import (InboundOrder, InboundPosting, InventoryTransaction,
    InventoryMovement, InventoryMovementSerial, StockAccount, StockBalance)
from app.material_request_my_inbound_schemas import MyInboundIn
from app.formal_services import inventory_posting as inventory
from app.formal_services import material_request_inbound as inbound
from app.formal_services import material_request_inbound_authority as authority
from app.formal_services import material_request_my_inbound as service
from app.formal_services.material_request_query import MaterialRequestReadError
from test_material_request_my_receipt import world as receipt_world, receiving_world, outbound_world, create as accept
from test_material_request_draft_service import SECRET
from test_formal_material_request_api import api_client

pytest_plugins = ("test_material_request_picking",)
KEY = "my-inbound-test-command-001"


@pytest.fixture
def world(receipt_world, monkeypatch):
    db, actor, request, *_ = receipt_world
    accepted = accept(receipt_world)
    # Only the ledger commit is synthetic in these composition regressions.
    # Both recipient authority checks and all command recovery run real code.
    def commit(db, *, actor, command, idempotency_key_hash, request_hash, receipt_authority, **kwargs):
        authority.require_receipt_authority(db, actor=actor, command=command, proof=receipt_authority)
        now = datetime.now(timezone.utc)
        tx = InventoryTransaction(id=uuid4(), transaction_no=command.transaction_no,
            movement_type=command.movement_type, source_document_type=command.source_document_type,
            source_document_id=command.source_document_id, posting_key=command.posting_key,
            idempotency_key_hash=idempotency_key_hash, request_hash=request_hash,
            status="posted", effective_at=command.effective_at, posted_at=now, created_at=now,
            actor_user_id=actor.user_id, ledger_cursor=10000)
        db.add(tx); db.flush()
        for n, movement in enumerate(command.movements, 1):
            row = InventoryMovement(id=uuid4(), transaction_id=tx.id, line_no=n,
                from_account_id=movement.from_account_id, to_account_id=movement.to_account_id,
                quantity=movement.quantity, external_boundary_code=movement.external_boundary_code, created_at=now)
            db.add(row); db.flush()
            db.add_all(InventoryMovementSerial(movement_id=row.id, transaction_id=tx.id, serial_id=serial_id)
                for serial_id in movement.serial_ids)
        db.flush()
        return inventory._InventoryPostingCommit(inventory.InventoryPostingResult(tx.id, tx.transaction_no, tx.ledger_cursor), None)
    monkeypatch.setattr(inventory, "_post_new_transaction", commit)
    value = MyInboundIn(expected_request_version=request.version, receipt_id=accepted.receipt_id,
        receipt_request_hash=accepted.request_hash)
    return db, actor, request, value


def post(world, *, payload=None, key=KEY, trace=None):
    db, actor, request, value = world
    return service.create_my_inbound(db, actor=actor, request_id=request.id, payload=payload or value,
        idempotency_key=key, secret=SECRET, trace_request_id=trace or f"trace-{key}")


def facts(db):
    return tuple(db.scalar(select(func.count()).select_from(model)) for model in (
        InboundOrder, InboundPosting, InventoryTransaction, InventoryMovement, StockAccount,
        StockBalance, MaterialRequestCommand, AuditEvent, OutboxEvent))


def test_own_receipt_posts_once_and_recovers_without_source_permission(world):
    db, actor, request, value = world
    assert not any(p.resource == "inventory_transaction" and p.action == "post" for p in actor.entitlements)
    result = post(world)
    assert result.request_version == value.expected_request_version + 1 == request.version
    assert result.person_id == actor.person_id and result.receipt_id == value.receipt_id
    stable = facts(db)
    assert post(world).idempotency_replayed
    assert service.my_inbound_command_status(db, actor=actor, request_id=request.id,
        idempotency_key=KEY, secret=SECRET).inventory_transaction_id == result.inventory_transaction_id
    assert facts(db) == stable
    order = db.get(InboundOrder, result.inbound_order_id)
    assert order.status == "pending" and order.posting_transaction_id is None
    assert db.scalar(select(MaterialRequestCommand).where(MaterialRequestCommand.operation == "personal_inbound")).request_reference.endswith("/my-inbounds")


def test_trace_recovery_only_reads_and_survives_later_permission_version(world):
    from app.models import User
    db, actor, request, _ = world
    result = post(world)
    db.get(User, actor.user_id).authorization_version += 1; db.flush()
    actor = load_formal_principal(db, actor.user_id)
    statements = []
    def capture(_conn, _cursor, sql, *_): statements.append(sql)
    event.listen(db.bind, "before_cursor_execute", capture)
    try:
        recovered = service.my_inbound_trace_status(db, actor=actor, request_id=request.id, trace_request_id=f"trace-{KEY}")
        assert recovered.inventory_transaction_id == result.inventory_transaction_id
        assert service.my_inbound_trace_status(db, actor=actor, request_id=request.id, trace_request_id="unknown-inbound-trace") is None
    finally:
        event.remove(db.bind, "before_cursor_execute", capture)
    assert all(sql.lstrip().upper().startswith("SELECT") for sql in statements)


@pytest.mark.parametrize("change", ["version", "receipt_hash", "receipt_id"])
def test_changed_confirmation_does_not_create_order_or_account(world, change):
    db, _, _, value = world
    value = value.model_copy(update={
        "version": {"expected_request_version": value.expected_request_version - 1},
        "receipt_hash": {"receipt_request_hash": "0" * 64},
        "receipt_id": {"receipt_id": uuid4()},
    }[change])
    before = facts(db)
    with pytest.raises(MaterialRequestReadError): post(world, payload=value)
    assert facts(db) == before


def test_same_key_changed_confirmation_and_new_key_cannot_duplicate(world):
    db, _, request, value = world
    post(world)
    before = facts(db)
    with pytest.raises(MaterialRequestReadError, match="其他入账内容"):
        post(world, payload=value.model_copy(update={"expected_request_version": request.version}))
    with pytest.raises(MaterialRequestReadError, match="版本"):
        post(world, key="another-my-inbound-command")
    with pytest.raises(inventory.InventoryPostingError, match="业务过账键"):
        post(world, key="another-my-inbound-command", payload=value.model_copy(update={"expected_request_version": request.version}))
    assert facts(db) == before


def test_late_audit_failure_rolls_back_order_account_command_and_posting(world, monkeypatch):
    db, _, request, _ = world
    db.commit()
    before, version = facts(db), request.version
    def fail(*args, **kwargs): raise RuntimeError("my inbound audit failure")
    monkeypatch.setattr(service, "append_audit_event", fail)
    with pytest.raises(RuntimeError, match="audit failure"): post(world)
    db.rollback()
    assert facts(db) == before and request.version == version


def test_recovery_rejects_changed_actual_ledger(world):
    db, actor, request, _ = world
    result = post(world)
    movement = db.scalar(select(InventoryMovement).where(InventoryMovement.transaction_id == result.inventory_transaction_id))
    movement.quantity += 1; db.flush()
    with pytest.raises(MaterialRequestReadError, match="流水"):
        service.my_inbound_trace_status(db, actor=actor, request_id=request.id, trace_request_id=f"trace-{KEY}")


def test_generic_posting_still_requires_source_scope_and_authority_is_exact(receipt_world):
    db, actor, request, location, *_ = receipt_world
    accepted = accept(receipt_world)
    order = inbound._new_inbound_order(db, receipt_id=accepted.receipt_id, target_location_id=location.id, target_person_id=actor.person_id)
    command = inbound._order_posting_command(db, order, create_missing=True)
    proof = authority.receipt_inbound_authority(db, actor=actor, request_id=request.id, order_id=order.id, command=command)
    with pytest.raises(inventory.InventoryPostingError) as denied:
        inventory._authorize_account_ids(db, actor, inventory._command_account_ids(command),
            action="post", lock_rows=False)
    assert denied.value.category == "forbidden"
    for counterfeit in [None, object(), replace(proof, seal=object()), replace(proof, transaction=object())]:
        with pytest.raises(inventory.InventoryPostingError, match="授权"):
            authority.require_receipt_authority(db, actor=actor, command=command, proof=counterfeit)
    changed = replace(command, movements=(replace(command.movements[0], quantity=command.movements[0].quantity + 1),))
    with pytest.raises(inventory.InventoryPostingError, match="授权"):
        authority.require_receipt_authority(db, actor=actor, command=changed, proof=proof)


def test_http_requires_receive_permission_and_rejects_caller_stock_fields(api_client, monkeypatch):
    from unittest.mock import Mock
    client, db, principals, _, settings = api_client
    body = dict(expected_request_version=1, receipt_id=str(uuid4()), receipt_request_hash='a' * 64)
    headers = {'Idempotency-Key': KEY, 'X-Request-ID': 'trace-my-inbound-http'}
    url = f'/api/v1/material-requests/{uuid4()}/my-inbounds'
    mocked = Mock(side_effect=MaterialRequestReadError('my_inbound_forbidden', 'forbidden', '测试拒绝'))
    monkeypatch.setattr(service, 'create_my_inbound', mocked)
    assert client.post(url, json=body, headers=headers).status_code == 403
    mocked.assert_not_called()
    principals['value'].permissions.add(('material_request', 'receive', ''))
    for field, value in [('target_person_id', str(uuid4())), ('from_account_id', str(uuid4())),
                         ('to_account_id', str(uuid4())), ('quantity', '1.000'), ('authority', {})]:
        assert client.post(url, json={**body, field: value}, headers=headers).status_code == 422
    mocked.assert_not_called()
    settings.material_request_writes_enabled = False
    assert client.post(url, json=body, headers=headers).status_code == 503
    mocked.assert_not_called()
    settings.material_request_writes_enabled = True
    assert client.post(url, json=body, headers=headers).status_code == 403
    assert mocked.call_count == 1 and db.commit.call_count == 0


def test_http_recovery_is_no_store_without_enabling_writes(api_client, monkeypatch):
    from unittest.mock import Mock
    client, db, _, _, settings = api_client
    settings.material_request_writes_enabled = False
    by_key = Mock(return_value=None); by_trace = Mock(return_value=None)
    monkeypatch.setattr(service, 'my_inbound_command_status', by_key)
    monkeypatch.setattr(service, 'my_inbound_trace_status', by_trace)
    root = f'/api/v1/material-requests/{uuid4()}/my-inbounds'
    for route, headers in [('command-status', {'Idempotency-Key': KEY}),
                           ('trace-status', {'X-Original-Request-ID': 'trace-my-inbound-original'})]:
        response = client.get(f'{root}/{route}', headers=headers)
        assert response.status_code == 200 and response.json()['lookup_status'] == 'not_observed'
        assert response.headers['cache-control'] == 'no-store, max-age=0'
    assert by_key.call_count == by_trace.call_count == 1 and db.commit.call_count == 0
