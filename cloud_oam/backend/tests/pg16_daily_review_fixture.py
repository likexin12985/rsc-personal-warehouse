"""Synthetic published sources and real immutable cutoffs for the PG16 gate.

No archived database, fixed business IDs, disabled guards or fabricated cutoff
rows. External capture transport is synthetic; authorization and SQL are real.
"""
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import psycopg
from psycopg.conninfo import make_conninfo
import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app import inventory_control_projection as controls
from app.daily_reconciliation import cutoff_service, deadline_entry, kernel, mapping as daily_mapping, mapping_entry, process_entry
from app.daily_reconciliation.control_source import capture_published_control
from app.daily_reconciliation.ledger_capture import capture_ledger
from app.foundation_models import Role, SourceSystem
from app.inventory_control_projection_models import ControlProjectionPublication
from app.inventory_models import StockLocation
from app.models import AuthSession
from app.security import create_access_token
from pg16_opening_publication_fixture import _publish_materials
from pg16_inventory_control_normalization_gate import _capture_pipeline, _configuration, _mapping, RULES
from test_formal_access import assign, make_organization, make_user
from test_inventory_control_authority import command as authority_command
from test_inventory_control_mapping import command as mapping_command
from test_inventory_control_projection import publication_command
from cloud_oam.edge_sync.test_inventory_control_capture import row as source_row


def ledger_connection(engine):
    return psycopg.connect(engine.url.set(drivername='postgresql').render_as_string(hide_password=False), autocommit=True)


def _conninfo(engine):
    return make_conninfo(host=engine.url.host or engine.url.query.get('host'),port=engine.url.port or 5432,
        dbname=engine.url.database,user=engine.url.username,
        password=engine.url.password or 'synthetic-local-trust-only')


def prepare(owner, edge, readers):
    """Build three new cutoffs, each with two nonzero source differences."""
    with Session(owner) as db:
        assert db.execute(text('SELECT current_database(),current_user,session_user')).one()==(
            'rsc_pg16_release_gate','star_oam_migrator','star_oam_migrator')
        assert int(db.scalar(text('SHOW server_version_num')))//10000==16
        now=db.scalar(text('SELECT clock_timestamp()'))
        hq=make_organization(db,name='Synthetic daily gate HQ')
        region=make_organization(db,name='Synthetic daily gate region',parent=hq)
        other=make_organization(db,name='Synthetic explicitly excluded fixture region',parent=hq)
        roles={r.code:r for r in db.scalars(select(Role))}
        identities={}
        for key,org,role in [('hq',hq,'admin'),('region',region,'provincial_manager'),('other',other,'provincial_manager')]:
            user,person=make_user(db,org,name='Synthetic daily gate '+key)
            user.role=role;user.province='synthetic'
            grant=assign(db,user,roles[role],scope_type='national' if key=='hq' else 'organization',
                scope_id='*' if key=='hq' else str(org.id),valid_from=now-timedelta(minutes=1),valid_to=now+timedelta(hours=1))
            auth=AuthSession(user_id=user.id,refresh_token_hash=uuid4().hex+uuid4().hex,device_id=uuid4().hex,
                client_type='web',ip_address='hmac:1:'+'a'*64,created_at=now-timedelta(seconds=1),expires_at=now+timedelta(hours=1))
            db.add(auth);db.flush()
            identities[key]=dict(user_id=user.id,person_id=person.id,session_id=auth.id,grant_id=grant.id,
                token=create_access_token(user.id,auth.id))
        source=db.scalar(select(SourceSystem).where(SourceSystem.code=='oam'))
        if source is None:
            source=SourceSystem(code='oam',name='Synthetic daily gate source',mode='read_only',enabled=True)
            db.add(source);db.flush()
        assert source.enabled and source.mode=='read_only'
        location=StockLocation(id=uuid4(),owner_org_id=region.id,parent_id=None,location_type='region',
            status='active',code='daily-gate-'+uuid4().hex,name='Synthetic explicit W-A location')
        db.add(location);db.commit()
        target_location=str(location.id);region_id=str(region.id);other_region_id=str(other.id)
        world,_,_=_publish_materials(db,edge,actor_user_id=identities['hq']['user_id'],source_id=source.id,region_id=region.id)
    rows=[]
    for key,condition,quantity in [('good','usable','5.000'),('old','broken','3.000')]:
        row=source_row('daily-gate-'+key,materialStatus=condition,materialStockType='stock')
        row.update(materialCode=world.code,qtyStock=quantity,qtyLock='0.000');rows.append(row)
    with pytest.MonkeyPatch.context() as patch, TemporaryDirectory(prefix='daily-gate-capture-') as directory:
        capture=_capture_pipeline(owner,edge,world,Path(directory),patch)
        capture([],full=True)
        with Session(owner) as db:
            authority=_configuration(db,world.login,authority_command(db,world));db.commit()
            _configuration(db,world.login,authority_command(db,world,action='catalog_grant',grant=authority));db.commit()
            mapping=_mapping(db,world.login,mapping_command(db,world,rules=RULES));db.commit()
            world.mapping=UUID(mapping['decision_id'])
        def publish():
            capture(rows,full=True)
            with Session(owner) as db:
                command=publication_command(db,world,mapping)
                preview=controls.preview_control_publication(db,**world.login,command=command)
                result=controls.execute_control_publication(db,**world.login,command=command,review_sha256=preview['review_sha256'])
                db.commit();publication=db.get(ControlProjectionPublication,UUID(result['publication_id']))
                return dict(publication_id=publication.id,expected_hash=publication.payload_sha256,
                    source_id=publication.source_system_id,region_id=publication.region_org_id,maximum_origins=100)
        source=capture_published_control(readers['rsc_control_capture'],**publish())
        with ledger_connection(readers['rsc_reconciliation_capture']) as connection:
            ledger=capture_ledger(connection,maximum_rows=50000)
        # Deliberate fixture mapping: the new location is W-A; every preexisting
        # synthetic location is explicitly outside this newly-created region.
        mapping_document=dict(schema='rsc.daily_comparison_mapping_candidate.v1',version_id=str(uuid4()),
            source_system_id=source['source_system_id'],region_id=region_id,catalog_sha256=source['catalog_sha256'],
            warehouses=['W-A'],location_bindings=[dict(location_id=row['id'],
                region_id=region_id if row['id']==target_location else other_region_id,
                warehouse_code='W-A' if row['id']==target_location else None)
                for row in sorted(ledger['facts']['stock_locations'],key=lambda row:row['id'])],
            included_buckets=['available','reserved'],scope_policy='explicit_physical_location',
            source_quantity_policy='published_quantity_locked_separate',
            location_facts_sha256=kernel.digest(sorted(ledger['facts']['stock_locations'],key=lambda row:row['id'])),
            organization_facts_sha256=kernel.digest(sorted(ledger['facts']['organizations'],key=lambda row:row['id'])))
        mapping_document['content_sha256']=kernel.digest(mapping_document)
        command=daily_mapping.MappingCommand.model_validate(dict(action='grant',binding_id=world.binding,catalog_id=world.catalog,
            rules=dict(revision=mapping_document['version_id'],mapping=mapping_document),expected_subject_sha256=source['catalog_sha256'],
            evidence_file_id=world.file,evidence_sha256='a'*64,reason='Explicit synthetic daily gate locations',
            idempotency_key=uuid4().hex,request_id=uuid4().hex))
        owner_conninfo=_conninfo(owner)
        mapping_kwargs=dict(conninfo=owner_conninfo,database_name=owner.url.database,
            command=command.model_dump(mode='json'),access_token=world.login['access_token'],
            expected_authorization_version=world.login['expected_authorization_version'],maximum_seconds=30)
        with owner.connect() as connection:
            before_mapping=connection.execute(text('SELECT (SELECT count(*) FROM daily_comparison_mapping_decisions),'
                '(SELECT count(*) FROM audit_events)')).one()
        preview=mapping_entry.execute(**mapping_kwargs,operation='preview')
        assert preview['operation']=='preview' and not preview['start_ready']
        with owner.connect() as connection:
            assert connection.execute(text('SELECT (SELECT count(*) FROM daily_comparison_mapping_decisions),'
                '(SELECT count(*) FROM audit_events)')).one()==before_mapping
        approved=mapping_entry.execute(**mapping_kwargs,operation='apply',review_sha256=preview['review_sha256'])
        assert approved['recorded'] and not approved['projection_published']
        status=mapping_entry.execute(**mapping_kwargs,operation='status',review_sha256=preview['review_sha256'])
        assert status['decision_id']==approved['decision_id'] and status['payload_sha256']==approved['payload_sha256']
        cutoffs=[]
        for index in range(3):
            source=publish()
            command=cutoff_service.CaptureCommand(business_date=datetime.now(ZoneInfo('Asia/Shanghai')).date(),
                source_publication_id=source['publication_id'],source_publication_sha256=source['expected_hash'],
                mapping_decision_id=UUID(approved['decision_id']),mapping_decision_sha256=approved['payload_sha256'],
                idempotency_key=uuid4().hex,request_id=uuid4().hex)
            if index==0:
                database=process_entry.DatabaseConfig(owner_conninfo,
                    _conninfo(readers['rsc_control_capture']),_conninfo(readers['rsc_reconciliation_capture']))
                kwargs=dict(database=database,command=command.model_dump(mode='json'),
                    **world.login,maximum_seconds=45)
                with owner.connect() as connection:
                    before_cutoff=connection.execute(text('SELECT (SELECT count(*) FROM daily_reconciliation_cutoffs),'
                        '(SELECT count(*) FROM audit_events),(SELECT count(*) FROM inventory_transactions)')).one()
                inspected=process_entry.execute(**kwargs,operation='preview')
                assert inspected['outcome']=='inspected' and inspected['receipt']['inspection_only']
                assert not inspected['receipt']['recorded'] and not inspected['receipt']['capture_performed']
                with owner.connect() as connection:
                    assert connection.execute(text('SELECT (SELECT count(*) FROM daily_reconciliation_cutoffs),'
                        '(SELECT count(*) FROM audit_events),(SELECT count(*) FROM inventory_transactions)')).one()==before_cutoff
                result=process_entry.execute(**kwargs,operation='capture')
                recovered=process_entry.execute(**kwargs,operation='recover')
                assert recovered['receipt']==result['receipt']
            else:
                owner_connection=owner.connect();source_connection=readers['rsc_control_capture'].connect()
                ledger_reader=ledger_connection(readers['rsc_reconciliation_capture'])
                try:
                    result=deadline_entry.execute_ready(owner=owner_connection,source=source_connection,ledger=ledger_reader,
                        command=command,**world.login,maximum_seconds=15)
                finally:
                    owner_connection.close();source_connection.close();ledger_reader.close()
            assert result['outcome']=='committed' and result['receipt']['recorded'] and not result['receipt']['stock_written']
            cutoffs.append(UUID(result['receipt']['cutoff_id']))
    return identities,cutoffs
