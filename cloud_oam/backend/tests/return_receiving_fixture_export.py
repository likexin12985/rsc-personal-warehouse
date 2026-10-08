"""Export synthetic service contracts from isolated SQLite test fixtures.

No network or existing database is used. These samples do not prove PG16
business COMMIT; its native gate remains a separate release requirement.
"""
from datetime import timedelta
import json
import os
from pathlib import Path
from uuid import uuid4

from app.inventory_models import StockAccount
from app.formal_services import stock_return_inbound_commands as commands
from app.formal_services import stock_return_receiving as receiving
from app.formal_services.stock_return_inbound_queries import read_return_inbound_state
from app.formal_services.stock_return_receipt_plan import preview_receipt
from app.formal_services.stock_return_receipt_queries import receipt_history
from app.stock_return_inbound_schemas import (
    StockReturnInboundPreviewOut, LossReturnInboundPreviewOut, StockReturnInboundOut,
    StockReturnInboundStateOut, StockReturnInboundSubmitIn,
)
from test_inventory_posting import establish_account_for_posting, NOW


def export(db, stock, acceptance, parcel, submit, execute, *, origin):
    assert origin in ('loss','work-order')
    assert db.get_bind().dialect.name=='sqlite'
    assert db.get_bind().url.database==':memory:'
    root = Path(os.environ['RSC_TEST_RECEIVING_OUTPUT']).resolve(strict=True)
    assert root.is_relative_to(Path(__file__).resolve().parents[2]/'artifacts')
    actor=acceptance.actor
    coordinates=dict(actor=actor,shipment_id=acceptance.package.shipment_id)
    directory=receiving.list_my_return_receiving(db,actor=actor)
    before=receipt_history(db,**coordinates)
    command=submit(db,acceptance)
    preview,_=preview_receipt(db,**coordinates,request=command)
    result=execute(db,acceptance,command);db.commit()
    after=receipt_history(db,**coordinates)
    value=dict(identity=dict(person_id=str(actor.person_id),authorization_version=actor.authorization_version),
        directory=directory.model_dump(mode='json'),before=before.model_dump(mode='json'),
        preview=preview.model_dump(mode='json'),command=command.model_dump(mode='json'),
        receipt=result.model_dump(mode='json'),after=after.model_dump(mode='json'))
    source=db.get(StockAccount,parcel.line.transit_stock_account_id)
    target=StockAccount(id=uuid4(),owner_org_id=source.owner_org_id,custodian_person_id=actor.person_id,
        location_id=acceptance.package.target_location_id,material_id=source.material_id,
        condition_code=source.condition_code,availability_bucket='available',lot_id=source.lot_id,
        created_at=NOW-timedelta(days=1),updated_at=NOW-timedelta(days=1))
    db.add(target);db.flush();establish_account_for_posting(db,stock.world,target)
    stock.world.current_principal=actor;db.commit()
    scope=dict(actor=actor,receipt_id=result.receipt_id)
    inbound_before=StockReturnInboundStateOut.model_validate(read_return_inbound_state(db,**scope))
    inbound_preview=commands.preview_return_inbound(db,**scope)
    schema=LossReturnInboundPreviewOut if origin=='loss' else StockReturnInboundPreviewOut
    view=schema.model_validate(inbound_preview)
    inbound_command=StockReturnInboundSubmitIn(operator_person_id=actor.person_id,
        expected_plan_hash=inbound_preview['plan_hash'],request_id=uuid4().hex,idempotency_key=uuid4().hex)
    posted=commands.execute_return_inbound(db,**scope,**inbound_command.model_dump(exclude={'operator_person_id'}))
    db.commit()
    inbound_after=StockReturnInboundStateOut.model_validate(read_return_inbound_state(db,**scope))
    value['inbound']=dict(before=inbound_before.model_dump(mode='json'),preview=view.model_dump(mode='json'),
        command=inbound_command.model_dump(mode='json'),posted=StockReturnInboundOut.model_validate(posted).model_dump(mode='json'),
        after=inbound_after.model_dump(mode='json'))
    assert value['inbound']['preview']['schema_version']=='2.0'
    mode='serial' if stock.tracked else 'quantity'
    with (root/f'{origin}-receiving-{mode}.json').open('x') as output:
        output.write(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
