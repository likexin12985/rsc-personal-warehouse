"""A missing response is recovered or sealed, never blindly replayed."""
from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select, text

from app.stock_operation_models import StockOperationCommandSeal
from app.stock_return_schemas import StockReturnCancelIn
from app.formal_services.stock_return_plan import intent
from app.formal_services.stock_return_recovery import lookup_return_request, seal_return_request
from app.formal_services.stock_return_commands import submit_return, cancel_return
from app.formal_services.work_order_return_sources import _hash
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services.audit_chain import append_audit_event
from test_stock_return_commands import db, world, stock, recovered, destination, submission, inventory, change_status


@pytest.fixture(params=["submit_return", "cancel_return"])
def command(db, stock, recovered, destination, request):
    value = submission(db, stock, recovered, destination)
    kind = request.param
    coordinates = dict(actor=stock.actor, work_order_id=stock.orders[0].id, operation_type=kind, request_id=value.request_id)
    if kind == "submit_return":
        digest = _hash(intent(stock.orders[0].id, value))
        def execute(item=value): return submit_return(db, actor=stock.actor, work_order_id=stock.orders[0].id, request=item)
    else:
        original = submit_return(db, actor=stock.actor, work_order_id=stock.orders[0].id, request=value); db.commit()
        value = StockReturnCancelIn(operator_person_id=stock.actor.person_id, reason="原取消请求恢复验证",
            idempotency_key=uuid4().hex, request_id=uuid4().hex)
        coordinates.update(operation_id=original.operation_id, request_id=value.request_id)
        digest = _hash({"operation_id": str(original.operation_id), "operator_person_id": str(stock.actor.person_id), "reason": value.reason})
        def execute(item=value): return cancel_return(db, actor=stock.actor, operation_id=original.operation_id, request=item)
    return value, coordinates, digest, execute


def test_missing_request_can_be_sealed_once_and_late_keys_cannot_post(db, stock, command):
    value, coordinates, digest, execute = command
    before = inventory(db)
    db.execute(text("PRAGMA query_only=ON"))
    assert lookup_return_request(db, **coordinates) is None and inventory(db) == before
    db.execute(text("PRAGMA query_only=OFF"))
    seal = seal_return_request(db, **coordinates, request_hash=digest); db.commit()
    assert seal.lookup_status == "sealed" and seal.seal.request_hash == digest
    assert inventory(db) == before
    assert seal_return_request(db, **coordinates, request_hash=digest) == seal
    assert len(tuple(db.scalars(select(StockOperationCommandSeal)))) == 1
    for item in (value, value.model_copy(update={"idempotency_key": uuid4().hex})):
        with pytest.raises(InventoryReadError) as exc: execute(item)
        assert exc.value.code == "stock_return_request_sealed"
        assert inventory(db) == before
    with pytest.raises(InventoryReadError) as exc: seal_return_request(db, **coordinates, request_hash="0" * 64)
    assert exc.value.code == "stock_return_seal_conflict"
    db.execute(text("PRAGMA query_only=ON"))
    assert lookup_return_request(db, **coordinates) == seal


def test_lost_success_response_reads_original_and_cannot_be_sealed(db, stock, command):
    _value, coordinates, digest, execute = command
    result = execute(); db.commit()
    change_status(db, stock.orders[0], "closed")
    before = inventory(db)
    db.execute(text("PRAGMA query_only=ON"))
    assert lookup_return_request(db, **coordinates) == result
    assert inventory(db) == before
    db.execute(text("PRAGMA query_only=OFF"))
    assert seal_return_request(db, **coordinates, request_hash=digest) == result
    assert not tuple(db.scalars(select(StockOperationCommandSeal)))
    assert inventory(db) == before


def test_current_read_and_seal_permissions_are_independent(db, stock, command):
    _value, coordinates, digest, _execute = command
    original = stock.world.current_principal
    stock.world.current_principal = replace(original, entitlements=tuple(row for row in original.entitlements
        if not (row.resource == "stock_operation" and row.action == "read")))
    with pytest.raises(InventoryReadError) as exc: lookup_return_request(db, **coordinates)
    assert exc.value.code == "stock_return_forbidden"
    stock.world.current_principal = replace(original, entitlements=tuple(row for row in original.entitlements
        if not (row.resource == "stock_operation" and row.action == coordinates["operation_type"])))
    assert lookup_return_request(db, **coordinates) is None
    with pytest.raises(InventoryReadError) as exc: seal_return_request(db, **coordinates, request_hash=digest)
    assert exc.value.code == "stock_return_forbidden"


def test_orphaned_request_evidence_is_never_treated_as_absence(db, stock, command):
    _value, coordinates, digest, _execute = command
    append_audit_event(db, stream_key="material_request", actor_user_id=stock.actor.user_id, action="stock_return_submitted",
        aggregate_type="stock_operation_order", aggregate_id=str(uuid4()), request_id=coordinates["request_id"],
        before_jsonb={}, after_jsonb={"synthetic": "missing original"}, occurred_at=datetime.now(timezone.utc))
    db.commit()
    before = inventory(db)
    for call in (lambda: lookup_return_request(db, **coordinates), lambda: seal_return_request(db, **coordinates, request_hash=digest)):
        with pytest.raises(InventoryReadError) as exc: call()
        assert exc.value.code == "stock_return_evidence_invalid"
        assert inventory(db) == before
