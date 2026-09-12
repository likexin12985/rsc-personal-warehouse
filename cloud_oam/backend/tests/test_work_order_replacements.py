"""Replacement input boundaries; full posting and races run against PG16."""
from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

import pytest

from app.formal_services import work_order_material as material
from app.formal_services import work_order_replacements as service
from test_work_order_material_evidence import db,evidence,world


def inputs():
    material_id,account,installed,removed = (uuid4() for _ in range(4))
    consume = material.WorkOrderMaterialLineInput(material_id,account,Decimal(1),(installed,),"new",
        (material.SerialVerificationInput(installed,"SKU","NEW","QR-NEW"),))
    recover = service.RecoveryLineInput(account,material_id,Decimal(1),"used",serial_ids=(removed,),
        serial_verifications=(material.SerialVerificationInput(removed,"SKU","OLD","QR-OLD"),))
    return dict(work_order_id=uuid4(),operator_person_id=uuid4(),consume_lines=(consume,),recover_lines=(recover,),
        pairs=(material.WorkOrderReplacementPairInput(installed,removed),))


@pytest.mark.parametrize("corruption,code",[("missing_recovery","replacement_recovery_required"),
    ("missing_pairs","replacement_pairs_incomplete"),("wrong_basis","replacement_lines_incomplete"),
    ("new_recovery","recover_condition_invalid"),("same_serial","replacement_serial_duplicate"),
    ("missing_codes","serial_verification_missing"),("duplicate_pair","replacement_pair_duplicate")])
def test_replacement_rejects_incomplete_or_ambiguous_binding(corruption,code):
    args = inputs(); recovered = args["recover_lines"][0]
    if corruption=="missing_recovery": args["recover_lines"]=()
    elif corruption=="missing_pairs": args["pairs"]=()
    elif corruption=="wrong_basis": args["recover_lines"]=(replace(recovered,basis_stock_account_id=uuid4()),)
    elif corruption=="new_recovery": args["recover_lines"]=(replace(recovered,condition_before="new"),)
    elif corruption=="same_serial": args["recover_lines"]=(replace(recovered,serial_ids=args["consume_lines"][0].serial_ids),)
    elif corruption=="missing_codes": args["recover_lines"]=(replace(recovered,serial_verifications=()),)
    elif corruption=="duplicate_pair": args["pairs"]*=2
    with pytest.raises(material.InventoryPostingError) as exc:
        service.replacement_request_payload(**args)
    assert exc.value.code==code


def test_each_serial_pair_must_match_the_explicit_consume_line():
    args=inputs(); other=inputs()
    args["consume_lines"]+=other["consume_lines"];args["recover_lines"]+=other["recover_lines"]
    first,second=args["pairs"][0],other["pairs"][0]
    args["pairs"]=(replace(first,removed_serial_id=second.removed_serial_id),replace(second,removed_serial_id=first.removed_serial_id))
    with pytest.raises(material.InventoryPostingError) as exc:
        service.replacement_request_payload(**args)
    assert exc.value.code=="replacement_pair_basis_mismatch"


def test_whole_reservation_preflight_precedes_any_recovery_account_creation(db,evidence,monkeypatch):
    def unexpected(*args,**kwargs):
        raise AssertionError("insufficient replacement must not create recovery dimensions")
    monkeypatch.setattr(service,"resolve_recovery_lines",unexpected)
    with pytest.raises(material.InventoryPostingError) as exc:
        service.execute_replacement(db,actor=evidence.world.current_principal,work_order_id=evidence.order.id,
            consume_lines=(replace(evidence.line,quantity=Decimal(2)),),recover_lines=(service.RecoveryLineInput(
                evidence.account.id,evidence.line.material_id,Decimal(1),"used"),),pairs=(),
            idempotency_key="replacement-preflight",request_id="replacement-preflight")
    assert exc.value.code=="work_order_reservation_insufficient"
