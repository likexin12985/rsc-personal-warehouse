"""Published synthetic SKU, real personal opening, then API ledger stock."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.formal_access import load_formal_principal
from app.foundation_models import Organization, Role
from app.inventory_models import CustodyAssignment, InventorySerial, StockAccount, StockLocation
from app.formal_services import inventory_posting as posting
from pg16_opening_publication_fixture import prepare_stocktake_inventory
from test_formal_access import make_organization, make_user, assign
from test_postgresql16_release_gate import _establish_multiround_stocktake_location
from work_order_fixtures import canonical_source


def prepare(engines):
    owner,api,edge=(engines[key] for key in ('star_oam_migrator','star_oam_api','edge_inbox'))
    with Session(owner) as db:
        assert db.scalar(text('SELECT current_database()'))=='rsc_pg16_release_gate'
        assert not db.scalar(text('SELECT EXISTS (SELECT 1 FROM inventory_transactions)'))
        hq=make_organization(db,name='Synthetic return recovery HQ')
        region=make_organization(db,name='Synthetic return recovery region',parent=hq)
        admin,_=make_user(db,hq,name='Synthetic return recovery reviewer')
        manager,_=make_user(db,region,name='Synthetic return recovery manager')
        roles={row.code:row for row in db.scalars(select(Role))}
        assign(db,admin,roles['admin'],scope_type='national',scope_id='*')
        assign(db,manager,roles['provincial_manager'],scope_type='organization',scope_id=str(region.id))
        db.commit();admin_id,manager_id=admin.id,manager.id
    fixture=prepare_stocktake_inventory(owner,edge,actor_user_id=admin_id,assignee_user_id=manager_id)
    with Session(owner) as db:
        engineer,person=make_user(db,db.get(Organization,fixture['region_org_id']),name='Synthetic return engineer')
        assign(db,engineer,db.scalar(select(Role).where(Role.code=='technician')),
            scope_type='person',scope_id=str(person.id))
        at=datetime.now(timezone.utc)
        location=StockLocation(id=uuid4(),code='RETURN-GATE-'+uuid4().hex,name='Synthetic return personal stock',
            location_type='personal',owner_org_id=fixture['region_org_id'],parent_id=fixture['location_id'],
            custodian_person_id=person.id,status='active')
        db.add(location);db.flush()
        db.add(CustodyAssignment(location_id=location.id,custodian_person_id=person.id,valid_from=at-timedelta(days=1)))
        accounts=[]
        for material_id in (fixture['material_id'],fixture['concurrency_material_id']):
            account=StockAccount(id=uuid4(),owner_org_id=fixture['region_org_id'],location_id=location.id,
                custodian_person_id=person.id,material_id=material_id,condition_code='new',availability_bucket='available')
            db.add(account);accounts.append(account.id)
        serials=tuple(uuid4() for _ in range(2))
        db.add_all(InventorySerial(id=identifier,material_id=fixture['concurrency_material_id'],
            serial_no='RETURN-GATE-SN-'+identifier.hex,qr_code='RETURN-GATE-QR-'+identifier.hex,
            lifecycle_status='active',created_at=at,updated_at=at) for identifier in serials)
        canonical_source(db)
        db.commit();engineer_id,location_id=engineer.id,location.id
    _establish_multiround_stocktake_location(api,fixture={**fixture,'recount_location_id':location_id},
        actor_user_id=admin_id,assignee_user_id=manager_id,count_user_id=engineer_id,expected_snapshot_line_count=2)
    # Same API ledger path used by the full CI's supplemental work-order stock.
    # No balance/projection seeding, direct SQL stock, or production permission.
    with Session(api) as db:
        for account,quantity,identifiers in ((accounts[0],Decimal(3),()),(accounts[1],Decimal(2),serials)):
            key='synthetic-return-fixture-'+uuid4().hex
            posting.post_inventory_transaction(db,actor=load_formal_principal(db,admin_id),
                command=posting.InventoryPostingCommand(transaction_no=key,movement_type='inbound',
                    source_document_type='pg16_work_order_fixture',source_document_id=key,posting_key=key,
                    effective_at=datetime.now(timezone.utc),movements=(posting.InventoryMovementCommand(
                        from_account_id=None,to_account_id=account,quantity=quantity,serial_ids=identifiers,
                        external_boundary_code='PG16_WORK_ORDER_FIXTURE'),)),
                idempotency_key=key,request_id=key)
        db.commit()
    print('PG16 return fixture: published SKUs, personal zero opening and quantity/SN API postings PASS',flush=True)
