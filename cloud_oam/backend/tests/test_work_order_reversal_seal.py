"""Recovery is read-only and seals cannot be bypassed by late stock commands."""
from dataclasses import replace
from uuid import uuid4

import pytest
from sqlalchemy import select, text

from app.demand_models import WorkOrderCommandSeal, WorkOrderReversal
from app.foundation_models import AuditEvent
from app.formal_services import work_order_reversal_seal as recovery
from app.formal_services import work_order_material as material
from app.formal_services.work_order_reversal_write import execute_reversal
from app.formal_services.work_order_reversal_plan import _hash
from test_work_order_reversal_write import db, world, stock, operation, submission
from test_work_order_removed_registration import inventory


def prepare(db, stock):
    source = operation(db, stock)
    value = submission(db, stock, original=source)
    intent = {"work_order_id": str(stock.orders[0].id), **value.model_dump(mode="json", exclude={"idempotency_key", "request_id", "expected_plan_hash"})}
    return value, dict(actor=stock.actor, work_order_id=stock.orders[0].id, request_id=value.request_id, request_hash=_hash(intent))


def lookup(db, args):
    return recovery.lookup_reversal(db, **{k:v for k,v in args.items() if k != "request_hash"})


def test_absence_sealing_readback_and_late_keys_have_no_stock_effect(db, stock):
    value, args = prepare(db, stock)
    before = inventory(db)
    assert lookup(db, args) is None
    sealed = recovery.seal_reversal(db, **args); db.commit()
    assert sealed.lookup_status == "sealed_not_executed" and sealed.seal.operation_type == "reverse"
    assert inventory(db) == before
    for _ in range(2):
        with pytest.raises(material.InventoryPostingError) as exc:
            execute_reversal(db, actor=stock.actor, work_order_id=args["work_order_id"], request=value.model_copy(update={"idempotency_key":uuid4().hex}))
        assert exc.value.code == "work_order_request_sealed"
        db.rollback()
    assert recovery.seal_reversal(db, **args) == sealed
    db.rollback(); db.execute(text("PRAGMA query_only=ON"))
    assert lookup(db, args) == sealed and inventory(db) == before
    assert not db.new and not db.dirty and not db.deleted


def test_completed_result_survives_closure_reassignment_and_auth_version_change(db, stock):
    value, args = prepare(db, stock)
    posted = execute_reversal(db, actor=stock.actor, work_order_id=args["work_order_id"], request=value); db.commit()
    stock.orders[0].status = "closed"
    stock.orders[0].engineer_person_id = stock.world.headquarters_reviewer_person.id
    db.commit()
    actor = replace(stock.actor, authorization_version=stock.actor.authorization_version+1)
    stock.world.current_principal = actor; args["actor"] = actor
    assert lookup(db, args) == posted
    assert recovery.seal_reversal(db, **args) == posted
    assert tuple(db.scalars(select(WorkOrderCommandSeal.id))) == ()
    assert posted.original_operation_id == value.original_operation_id and posted.reason == value.reason
    with pytest.raises(material.InventoryPostingError) as exc:
        recovery.seal_reversal(db, **{**args, "request_hash":"0"*64})
    assert exc.value.code == "work_order_seal_request_conflict"
    db.rollback(); db.execute(text("PRAGMA query_only=ON"))
    assert lookup(db, args) == posted


@pytest.mark.parametrize("missing", ["parent", "seal", "parent_audit", "child"])
def test_partial_evidence_never_becomes_absence_or_new_seal(db, stock, missing):
    from app.demand_models import WorkOrderMaterialLine
    value, args = prepare(db, stock)
    if missing == "seal":
        result = recovery.seal_reversal(db, **args); db.commit()
        db.delete(db.get(WorkOrderCommandSeal, result.seal.seal_id))
    else:
        result = execute_reversal(db, actor=stock.actor, work_order_id=args["work_order_id"], request=value); db.commit()
        if missing == "parent": db.get(WorkOrderReversal, result.reversal_id).request_id = uuid4().hex
        elif missing == "child":
            db.scalar(select(WorkOrderMaterialLine).where(WorkOrderMaterialLine.operation_id == result.items[0].inverse_operation_id)).quantity += 1
        else:
            db.scalar(select(AuditEvent).where(AuditEvent.aggregate_id == str(result.reversal_id))).action = "invalid"
    # SQLite fixture permits injecting impossible PG corruption for read guards.
    db.commit()
    for fn in (lambda: lookup(db, args), lambda: recovery.seal_reversal(db, **args)):
        with pytest.raises(material.InventoryPostingError) as exc: fn()
        assert exc.value.category == "service_unavailable"
        db.rollback()


@pytest.mark.parametrize("action", ["read", "operate"])
def test_current_permissions_are_required_for_sealing(db, stock, action):
    _, args = prepare(db, stock)
    actor = replace(stock.actor, entitlements=tuple(row for row in stock.actor.entitlements if row.action != action))
    stock.world.current_principal = actor
    with pytest.raises(material.InventoryPostingError) as exc:
        recovery.seal_reversal(db, **{**args, "actor":actor})
    assert exc.value.category == "forbidden" and not db.new


def test_actor_namespace_is_private_and_wrong_order_is_conflict(db, stock):
    value, args = prepare(db, stock)
    execute_reversal(db, actor=stock.actor, work_order_id=args["work_order_id"], request=value); db.commit()
    with pytest.raises(material.InventoryPostingError) as exc:
        lookup(db, {**args, "work_order_id":stock.orders[1].id})
    assert exc.value.code == "request_id_conflict"
    foreign = replace(stock.actor, user_id=str(uuid4()), person_id=stock.world.headquarters_reviewer_person.id,
        entitlements=tuple(replace(row,scope_type="national",scope_id="*") for row in stock.actor.entitlements))
    stock.world.current_principal = foreign
    assert lookup(db, {**args, "actor":foreign}) is None


def test_seal_digest_conflict_and_other_operation_namespaces(db, stock):
    from app.formal_services.work_order_command_seal import require_unsealed_request
    _, args = prepare(db, stock)
    recovery.seal_reversal(db, **args); db.commit()
    with pytest.raises(material.InventoryPostingError) as exc:
        recovery.seal_reversal(db, **{**args, "request_hash":"f"*64})
    assert exc.value.code == "work_order_seal_request_conflict"
    for kind in ("occupy", "release", "consume", "replace", "register_removed"):
        require_unsealed_request(db, operation_type=kind, **{k:v for k,v in args.items() if k != "request_hash"})


def test_paired_reversal_recovery_requires_both_inverse_transactions(db, stock):
    from app.formal_services.work_order_replacements import execute_replacement
    from test_work_order_replacement_preview import inputs
    parent = execute_replacement(db, actor=stock.actor, work_order_id=stock.orders[0].id, **inputs(stock),
        idempotency_key=uuid4().hex, request_id=uuid4().hex)
    db.commit()
    value = submission(db, stock, parent=parent)
    result = execute_reversal(db, actor=stock.actor, work_order_id=stock.orders[0].id, request=value)
    db.commit()
    args = dict(actor=stock.actor, work_order_id=stock.orders[0].id, request_id=value.request_id, request_hash=result.request_hash)
    assert lookup(db, args) == result and recovery.seal_reversal(db, **args) == result
    assert result.original_replacement_id == parent.id and result.original_operation_id is None
    assert [item.original_operation_id for item in result.items] == [parent.recover_operation_id, parent.consume_operation_id]
    assert len({item.inverse_transaction_id for item in result.items}) == 2
    db.rollback(); db.execute(text("PRAGMA query_only=ON"))
    assert lookup(db, args) == result
