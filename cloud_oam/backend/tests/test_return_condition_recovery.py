"""Exact original recovery over real source, inventory and business evidence."""
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.formal_services.stock_loss_corrections import return_condition_submission as writer
from app.formal_services.stock_loss_corrections import return_condition_recovery as recovery
from app.formal_services.inventory_query import InventoryReadError
from test_return_condition_ledger_facts import db,quantity_command,serial_command,authority_template
from test_return_condition_submission import snapshot
from test_return_condition_submission_source import (
    world,stock,allowed,evidence,regional,headquarters,approved,route,derived,ready,parcel,
    acceptance,prepared,regional_opening,reader_tables, context,regional_source,ERRORS,
)


@pytest.mark.parametrize('stock,command_name',[('quantity','quantity_command'),('serial','serial_command')],indirect=['stock'])
def test_exact_original_result_and_unknown_never_authorize_replay(db,regional_source,request,command_name):
    c=regional_source;command=request.getfixturevalue(command_name)
    before=snapshot(db)
    missing=recovery.lookup(db,actor=c.actor,request=command)
    assert missing['request_state']=='unknown' and missing['result'] is None
    assert missing['retry_allowed'] is False and missing['absence_sealed'] is False
    assert snapshot(db)==before
    db.rollback()
    result=writer.submit(db,actor=c.actor,request=command);db.commit();before=snapshot(db)
    db.execute(text('PRAGMA query_only=ON'))
    found=recovery.lookup(db,actor=c.actor,request=command)
    db.execute(text('PRAGMA query_only=OFF'))
    assert found['result']==result and found['request_state']=='found'
    assert found['result_scope']=='historical_original_outcome' and found['current_case_status']=='awaiting_regional'
    assert found['current_stock_verified'] is False and found['retry_allowed'] is False
    assert snapshot(db)==before
    for change in ({'reason':'not the original reason'},{'idempotency_key':uuid4().hex}):
        with pytest.raises(InventoryReadError) as error:
            recovery.lookup(db,actor=c.actor,request=command.model_copy(update=change))
        assert error.value.code=='return_condition_original_input_conflict'
        db.rollback();assert snapshot(db)==before
    different=command.model_copy(update={'request_id':uuid4().hex,'idempotency_key':uuid4().hex})
    missing=recovery.lookup(db,actor=c.actor,request=different)
    assert missing['request_state']=='unknown' and missing['retry_allowed'] is False
    assert snapshot(db)==before
