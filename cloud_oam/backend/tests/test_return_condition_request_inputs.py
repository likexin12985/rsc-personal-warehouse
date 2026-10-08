"""Original input retention over the real private writer, not a recovery endpoint."""
from uuid import UUID,uuid4
from decimal import Decimal
import pytest
from sqlalchemy import select
from app.inventory_models import FormalMaterial
from app.formal_services.inventory_posting import InventoryPostingError
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services.stock_loss_corrections import return_condition_submission as subject
from app.formal_services.stock_loss_corrections import return_condition_request_inputs as inputs
from test_return_condition_submission import db,command,snapshot
from test_return_condition_submission_source import (
    world,stock,allowed,evidence,regional,headquarters,approved,route,derived,ready,parcel,
    acceptance,prepared,authority_template,regional_opening,reader_tables, context,regional_source,
)

pytestmark=pytest.mark.parametrize('stock',['quantity'],indirect=True)


def written(db,regional_source,command):
    result=subject.submit(db,actor=regional_source.actor,request=command);db.commit()
    cases=subject.tables()['stock_condition_cases'];events=subject.tables()['stock_condition_events']
    return (db.execute(select(cases).where(cases.c.id==UUID(result['case_id']))).mappings().one(),
        db.execute(select(events).where(events.c.id==UUID(result['event_id']))).mappings().one())


def test_complete_original_input_is_retained_without_a_raw_key(db,regional_source,command):
    import json
    case,event=written(db,regional_source,command)
    row=inputs.verify(db,case=case,event=event)
    assert row['input_jsonb']==inputs.canonical(command)
    assert inputs.match_original(db,request=command,case=case,event=event)==row['input_hash']
    assert command.idempotency_key not in json.dumps(row['input_jsonb'])
    before=snapshot(db)
    changes={'quantity':Decimal('.124'),'expected_source_hash':'0'*64,'request_id':uuid4().hex,
        'idempotency_key':uuid4().hex,'reason':'different original reason','evidence_file_ids':(uuid4(),)}
    for key,value in changes.items():
        with pytest.raises((InventoryPostingError,InventoryReadError)) as error:
            inputs.match_original(db,request=command.model_copy(update={key:value}),case=case,event=event)
        assert error.value.code=='return_condition_original_input_conflict'
    assert snapshot(db)==before


def test_later_material_label_does_not_rewrite_original_snapshot(db,regional_source,command):
    case,event=written(db,regional_source,command)
    saved=inputs.verify(db,case=case,event=event)
    material=db.get(FormalMaterial,regional_source.source.material_id)
    material.sku_code='changed-after-original-submission';db.commit()
    assert inputs.verify(db,case=case,event=event)==saved
    assert inputs.match_original(db,request=command,case=case,event=event)==saved['input_hash']


def test_missing_or_rehashed_wrong_input_is_unknown(db,regional_source,command):
    from app.formal_services import inventory_posting as posting
    case,event=written(db,regional_source,command)
    row=inputs.verify(db,case=case,event=event)
    changed=dict(row['input_jsonb'],quantity='0.124')
    # SQLite can emulate corrupted persistence; PG append-only/commit guards
    # are verified separately without disabling any production guard.
    db.execute(inputs.table().update().where(inputs.table().c.event_id==event['id']).values(
        input_jsonb=changed,input_hash=posting._canonical_hash(changed)))
    with pytest.raises((InventoryPostingError,InventoryReadError)) as error:
        inputs.match_original(db,request=command,case=case,event=event)
    assert error.value.code=='return_condition_original_input_unknown'
    db.rollback()
    db.execute(inputs.table().delete().where(inputs.table().c.event_id==event['id']))
    with pytest.raises((InventoryPostingError,InventoryReadError)) as error:
        inputs.match_original(db,request=command,case=case,event=event)
    assert error.value.code=='return_condition_original_input_unknown'
    db.rollback()
