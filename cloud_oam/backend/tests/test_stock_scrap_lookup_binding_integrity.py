"""Found is not trustworthy when immutable request provenance is incomplete."""
from uuid import uuid4
import pytest
from sqlalchemy import delete, update
from app.formal_services.inventory_query import InventoryReadError
from scrap_lookup_binding_fixture import REGISTRY
from test_stock_scrap_recovery_lookup import (
    db, world, stock, allowed, evidence, regional, headquarters, approved, found, readable,
    submit, lookup as recovery_lookup, snapshot,
)
from test_stock_scrap_request_lookup import original, execute, lookup as scrap_lookup


def corrupt(db, field):
    if field == 'missing':
        db.execute(delete(REGISTRY))
    else:
        value = '0'*64 if field == 'key_token' else uuid4()
        db.execute(update(REGISTRY).values(**{field: value}))
    db.commit()


@pytest.mark.parametrize('field', ['missing','actor_person_id','root_disposition_id','key_token'])
def test_original_provenance_damage_is_unknown(db, original, field):
    execute(db, actor=original.actor, request=original.command)
    db.commit()
    corrupt(db, field)
    before = snapshot(db)
    with pytest.raises(InventoryReadError) as error:
        scrap_lookup(db, original)
    assert error.value.status_code == 503 and 'outcome_unknown' in error.value.code
    assert snapshot(db) == before


@pytest.mark.parametrize('field', ['missing','actor_person_id','root_disposition_id','key_token'])
def test_approval_provenance_damage_is_unknown(db, readable, field):
    submit(db, readable, 'apply', readable.application)
    corrupt(db, field)
    before = snapshot(db)
    with pytest.raises(InventoryReadError) as error:
        recovery_lookup(db, readable, 'apply', readable.application)
    assert error.value.status_code == 503 and 'outcome_unknown' in error.value.code
    assert snapshot(db) == before
