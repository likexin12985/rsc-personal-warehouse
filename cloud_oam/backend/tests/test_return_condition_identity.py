from datetime import datetime, timezone
from decimal import Decimal, Inexact, Rounded, localcontext
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.formal_services import inventory_posting as posting
from app.formal_services.stock_loss_corrections.return_condition_identity import inventory_identity


def event(quantity):
    return dict(id=UUID(int=1),kind='submit',created_at=datetime(2026,10,3,1,2,3,456789,tzinfo=timezone.utc),
        actor_user_id=str(UUID(int=2)),actor_person_id=UUID(int=3),authorization_version=7,
        idempotency_key_hash='a'*64,movement_type='freeze',from_account_id=UUID(int=4),
        to_account_id=UUID(int=5),quantity=Decimal(quantity))


@pytest.mark.parametrize('quantity',['0.375','1.000','999999999999999.999','1000000000000.001','1E+3'])
@pytest.mark.parametrize('precision',[2,6,28])
def test_inventory_command_fingerprint_preserves_exact_quantity_in_any_decimal_context(quantity,precision):
    e=event(quantity)
    baseline=inventory_identity(e,())
    actor=SimpleNamespace(user_id=e['actor_user_id'],person_id=e['actor_person_id'],authorization_version=7)
    expected=posting._posting_request_hash(actor,baseline[0])
    with localcontext() as ctx:
        ctx.prec=precision
        ctx.traps[Inexact]=True
        ctx.traps[Rounded]=True
        current=inventory_identity(e,())
        assert current==baseline
        assert current[2]==expected==posting._posting_request_hash(actor,current[0])
        assert Decimal(posting._posting_document(current[0])['movements'][0]['quantity'])==e['quantity']
    assert current[1]!=e['idempotency_key_hash']  # inventory and business keys are separate


def test_equivalent_decimal_scales_share_inventory_identity_but_an_actual_quantity_change_does_not():
    a=inventory_identity(event('1.000'),())[1:]
    assert a==inventory_identity(event('1'),())[1:]
    assert a[1]!=inventory_identity(event('1.001'),())[2]


def test_review_event_cannot_become_inventory_command():
    with pytest.raises(ValueError,match='review events'):
        inventory_identity(event('1')|{'kind':'approve_hq'},())


def test_serial_order_is_canonical_and_duplicates_are_refused():
    e=event('2')
    assert inventory_identity(e,(UUID(int=8),UUID(int=7)))==inventory_identity(e,(UUID(int=7),UUID(int=8)))
    with pytest.raises(ValueError,match='duplicate'):
        inventory_identity(e,(UUID(int=7),UUID(int=7)))
