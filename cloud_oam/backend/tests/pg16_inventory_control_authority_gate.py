"""0113 authority checks inside the already protected disposable PostgreSQL 16."""
from source_configuration_file_fixtures import source_evidence
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app import inventory_control_authority as authority
from app.formal_access import FormalAccessError, load_formal_principal
from app.foundation_models import FileObject, Organization, Person
from app.models import User
from app.inventory_control_models import InventoryControlPreparation as Preparation, InventoryControlSourceBinding as Binding
from app.inventory_control_authority_models import InventoryControlAuthorityDecision as Decision
from test_inventory_control_authority import command
from pg16_inventory_control_preparation_gate import _formal_stock, _rejected


def snapshot(engine):
    with engine.connect() as connection:
        return tuple(connection.scalars(text('SELECT to_jsonb(t) FROM public.inventory_control_authority_decisions t ORDER BY id')))


def assert_inventory_control_authority_gate(owner_engine, api_engine, edge_engine, projector_engine, backup_engine):
    with Session(owner_engine) as db:
        assert db.scalar(text('SELECT current_database()')) == 'rsc_pg16_release_gate'
        assert int(db.scalar(text('SHOW server_version_num'))) // 10000 == 16
        assert db.scalar(text('SELECT session_user')) == 'star_oam_migrator'
        actor = None
        for user_id in db.scalars(select(User.id).join(Person,Person.id==User.person_id).join(Organization,Organization.id==Person.organization_id)
                .where(Organization.org_type=='headquarters',Organization.status=='active',User.account_status=='active',User.is_active.is_(True))
                .order_by(User.id)):
            try:
                candidate=load_formal_principal(db,user_id)
                authority._operator(db,candidate,authority._now(db))
            except (FormalAccessError,authority.ControlAuthorityError):
                continue
            actor=candidate
            break
        assert actor is not None, 'Existing protected PG16 fixtures need an active headquarters reviewer'
        binding=db.scalar(select(Binding).where(Binding.binding_jsonb['source_instance'].as_string()=='pg16-control-preparation'))
        assert binding is not None
        root=db.scalar(select(Preparation).where(Preparation.binding_id==binding.id).order_by(Preparation.id).limit(1))
        file, _ = source_evidence(db, actor.user_id)
        db.add(file);db.flush()
        world=SimpleNamespace(actor=actor,root=root.id,binding=binding.id,catalog=root.catalog_id,
                              file=file.id,source=binding.source_system_id)
        db.commit()
        source_command=command(db,world)
    before_stock=_formal_stock(owner_engine)
    barrier=Barrier(2)
    def submit():
        with Session(owner_engine) as db:
            barrier.wait(timeout=20)
            value=authority.record_inventory_control_authority(db,actor=actor,command=source_command)
            db.commit()
            return value
    with ThreadPoolExecutor(max_workers=2) as workers:
        first,second=list(workers.map(lambda _:submit(),range(2)))
    assert first==second and len(snapshot(owner_engine))==1
    with Session(owner_engine) as db:
        catalog_command=command(db,world,action='catalog_grant',grant=first)
        catalogue=authority.record_inventory_control_authority(db,actor=actor,command=catalog_command)
        db.commit()
        current=authority.resolve_inventory_control_authority(db,preparation_id=world.root)
        assert current['source_authorized'] and current['catalog_authorized']
        assert not any(current[key] for key in ('source_authenticated','capture_attested','projection_published','start_ready'))
        with pytest.raises(authority.ControlAuthorityError,match='overlapping_grant'):
            authority.record_inventory_control_authority(db,actor=actor,command=command(db,world))
        db.commit()
    saved=snapshot(owner_engine)
    assert snapshot(backup_engine)==saved
    for engine in (api_engine,edge_engine,projector_engine):
        for sql in ('SELECT * FROM inventory_control_authority_decisions','INSERT INTO inventory_control_authority_decisions DEFAULT VALUES'):
            _rejected(engine,sql,state='42501')
    _rejected(backup_engine,'INSERT INTO inventory_control_authority_decisions DEFAULT VALUES',state='42501')
    for sql in ('UPDATE inventory_control_authority_decisions SET id=id','DELETE FROM inventory_control_authority_decisions','TRUNCATE inventory_control_authority_decisions'):
        _rejected(owner_engine,sql,message='authority decisions are append-only')
    # Owner privileges cannot forge a new decision merely by cloning its body.
    _rejected(owner_engine,'''INSERT INTO inventory_control_authority_decisions
        SELECT (jsonb_populate_record(NULL::inventory_control_authority_decisions,
            to_jsonb(original)||jsonb_build_object('id',CAST(:id AS text),'created_at',clock_timestamp(),
                'valid_from',clock_timestamp(),'idempotency_key',CAST(:key AS text),'request_id',CAST(:request AS text)))).*
        FROM inventory_control_authority_decisions original WHERE original.id=:original''',
        {'id':str(uuid4()),'key':'forged-'+uuid4().hex,'request':'forged-'+uuid4().hex,'original':first['decision_id']},
        message='authority evidence mismatch')
    assert snapshot(owner_engine)==saved
    with Session(owner_engine) as db:
        revoke_command=command(db,world,action='revoke',grant=first)
        revoked=authority.record_inventory_control_authority(db,actor=actor,command=revoke_command)
        db.commit()
        current=authority.resolve_inventory_control_authority(db,preparation_id=world.root)
        assert not current['source_authorized'] and not current['catalog_authorized']
        assert authority.record_inventory_control_authority(db,actor=actor,command=source_command)==first
        db.commit()
        for result in (first,catalogue,revoked):
            authority._prove(db,db.get(Decision,result['decision_id']))
    with Session(owner_engine) as db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        assert not authority.resolve_inventory_control_authority(db,preparation_id=world.root)['catalog_authorized']
    assert _formal_stock(owner_engine)==before_stock
    print('PG16 control authority: concurrent exact replay, separate catalogue grant, revocation, private ACL and immutable audit evidence PASS',flush=True)
    from pg16_inventory_control_configuration_gate import assert_inventory_control_configuration_gate
    assert_inventory_control_configuration_gate(owner_engine, world)
    from pg16_inventory_control_handoff_gate import assert_inventory_control_handoff_gate
    assert_inventory_control_handoff_gate(owner_engine, api_engine, world)
