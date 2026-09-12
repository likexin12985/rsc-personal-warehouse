"""A parent replacement is either proven posted or sealed, never replayed."""
from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select, text

from app.demand_models import WorkOrderCommandSeal
from app.foundation_models import AuditEvent
from app.formal_services import work_order_command_seal as ordinary
from app.formal_services import work_order_replacement_seal as seals
from app.formal_services import work_order_replacements as replacement_service
from app.formal_services import work_order_material as material
from app.inventory_models import InventoryTransaction, StockAccount
from test_work_order_material_options import db, world, stock
from test_work_order_replacement_read import replacement


def command(stock, trace=None):
    consumed = stock.line("1", stock.serials[1:2], identifier=stock.reserved.id)
    removed = stock.serials[:1]
    return dict(actor=stock.actor, work_order_id=stock.orders[0].id,
        consume_lines=(consumed,), recover_lines=(replacement_service.RecoveryLineInput(stock.reserved.id,
            stock.world.material.id, Decimal(1), "damaged", serial_ids=tuple(row.id for row in removed),
            serial_verifications=tuple(material.SerialVerificationInput(row.id, stock.world.material.sku_code,
                row.serial_no, row.qr_code) for row in removed)),),
        pairs=(material.WorkOrderReplacementPairInput(consumed.serial_ids[0], removed[0].id),) if removed else (),
        idempotency_key=uuid4().hex, request_id=trace or uuid4().hex)


def arguments(stock, value):
    payload=replacement_service.replacement_request_payload(operator_person_id=stock.actor.person_id,
        **{key:value[key] for key in ("work_order_id","consume_lines","recover_lines","pairs")})
    return {key:value[key] for key in ("actor","work_order_id","request_id")} | {"request_hash":replacement_service._hash(payload)}


def test_parent_seal_has_no_stock_or_target_creation_and_excludes_late_keys(db, stock):
    value=command(stock); args=arguments(stock,value)
    before=(tuple(db.scalars(select(InventoryTransaction.id))),tuple(db.scalars(select(StockAccount.id))))
    result=seals.seal_replacement(db,**args); db.commit()
    assert result.lookup_status=="sealed_not_executed" and result.seal.operation_type=="replace"
    assert (tuple(db.scalars(select(InventoryTransaction.id))),tuple(db.scalars(select(StockAccount.id))))==before
    for _ in range(2):
        with pytest.raises(material.InventoryPostingError) as exc:
            replacement_service.execute_replacement(db,**{**value,"idempotency_key":uuid4().hex})
        assert exc.value.code=="work_order_request_sealed";db.rollback()
    assert seals.seal_replacement(db,**args).seal.seal_id==result.seal.seal_id;db.rollback()
    db.execute(text("PRAGMA query_only=ON"))
    assert seals.lookup_replacement_result(db,**{k:v for k,v in args.items() if k!="request_hash"}).seal.seal_id==result.seal.seal_id
    assert not db.new and not db.dirty and not db.deleted


def test_seal_returns_both_original_transactions_even_after_reassignment_and_closure(db,stock,replacement):
    stock.orders[0].engineer_person_id=stock.world.headquarters_reviewer_person.id
    stock.orders[0].status="closed";db.commit()
    stock.world.current_principal=replace(stock.actor,authorization_version=stock.actor.authorization_version+1)
    args=dict(actor=stock.world.current_principal,work_order_id=replacement.oam_work_order_id,
        request_id=replacement.request_id,request_hash=replacement.request_hash)
    result=seals.seal_replacement(db,**args)
    assert result.replacement_id==replacement.id and result.consume_transaction_id!=result.recover_transaction_id
    assert tuple(db.scalars(select(WorkOrderCommandSeal.id)))==()
    with pytest.raises(material.InventoryPostingError) as exc:
        seals.seal_replacement(db,**{**args,"request_hash":"0"*64})
    assert exc.value.code=="work_order_seal_request_conflict"


def test_parent_seal_does_not_block_ordinary_commands_or_another_order(db,stock):
    value=command(stock);args=arguments(stock,value)
    seals.seal_replacement(db,**args);db.commit()
    for kind in ("occupy","consume","release"):
        ordinary.require_unsealed_request(db,actor=stock.actor,work_order_id=stock.orders[0].id,
            operation_type=kind,request_id=value["request_id"])
    ordinary.require_unsealed_request(db,actor=stock.actor,work_order_id=stock.orders[1].id,
        operation_type="replace",request_id=value["request_id"])
    ordinary.seal_command(db,actor=stock.actor,work_order_id=stock.orders[0].id,
        operation_type="consume",request_id=uuid4().hex,request_hash="a"*64);db.commit()
    posted=replacement_service.execute_replacement(db,**{**value,"request_id":uuid4().hex})
    assert posted is not None;db.rollback()


@pytest.mark.parametrize("action",["read","operate"])
def test_parent_sealing_requires_both_current_permissions(db,stock,action):
    actor=replace(stock.actor,entitlements=tuple(row for row in stock.actor.entitlements if row.action!=action))
    stock.world.current_principal=actor
    with pytest.raises(material.InventoryPostingError) as exc:
        seals.seal_replacement(db,**{**arguments(stock,command(stock)),"actor":actor})
    assert exc.value.category=="forbidden" and not db.new


def test_parent_seal_history_survives_version_change_but_foreign_users_cannot_read(db,stock):
    args=arguments(stock,command(stock));original=seals.seal_replacement(db,**args);db.commit()
    stock.orders[0].engineer_person_id=stock.world.headquarters_reviewer_person.id
    stock.orders[0].status="closed";db.commit()
    stock.world.current_principal=replace(stock.actor,authorization_version=stock.actor.authorization_version+1)
    lookup={k:v for k,v in args.items() if k!="request_hash"};lookup["actor"]=stock.world.current_principal
    assert seals.lookup_replacement_result(db,**lookup).seal.seal_id==original.seal.seal_id
    foreign=replace(stock.actor,user_id=str(uuid4()),person_id=stock.world.headquarters_reviewer_person.id,
        entitlements=tuple(replace(row,scope_type="national",scope_id="*") for row in stock.actor.entitlements))
    stock.world.current_principal=foreign
    assert seals.lookup_replacement_result(db,**{**lookup,"actor":foreign}) is None


def test_parent_seal_rejects_bad_digest_audit_or_a_coexisting_posted_proof(db,stock,monkeypatch):
    args=arguments(stock,command(stock));result=seals.seal_replacement(db,**args);db.commit()
    with pytest.raises(material.InventoryPostingError) as exc:
        seals.seal_replacement(db,**{**args,"request_hash":"b"*64})
    assert exc.value.code=="work_order_seal_request_conflict";db.rollback()
    lookup={k:v for k,v in args.items() if k!="request_hash"}
    with monkeypatch.context() as context:
        context.setattr(seals,"lookup_replacement",lambda *a,**k:object())
        with pytest.raises(material.InventoryPostingError) as exc:
            seals.lookup_replacement_result(db,**lookup)
        assert exc.value.code=="work_order_seal_evidence_invalid"
    event=db.scalar(select(AuditEvent).where(AuditEvent.aggregate_id==str(result.seal.seal_id)))
    event.after_jsonb={};db.commit()
    with pytest.raises(material.InventoryPostingError) as exc:
        seals.lookup_replacement_result(db,**lookup)
    assert exc.value.code=="work_order_seal_evidence_invalid"
    assert db.get(WorkOrderCommandSeal,result.seal.seal_id) is not None


@pytest.mark.parametrize("change",[{"request_id":"bad"},{"request_hash":"x"*64}])
def test_invalid_parent_seal_coordinates_do_not_write(db,stock,change):
    with pytest.raises(material.InventoryPostingError) as exc:
        seals.seal_replacement(db,**{**arguments(stock,command(stock)),**change})
    assert exc.value.code=="work_order_seal_input_invalid" and not db.new
