"""New-start and historical selection under actual restricted PostgreSQL roles."""
from dataclasses import replace
from datetime import datetime,timezone
from pathlib import Path
import runpy,time
from uuid import UUID,uuid4
import pytest
from sqlalchemy import select,text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.formal_services import opening_publication_admission as admission,opening_stocktake as opening
from app.formal_access import load_formal_principal
from app.inventory_control_projection_models import ControlProjectionPublication as Publication,ControlProjectionOrigin as Origin
from app.material_projection_models import MaterialProjectionLine,MaterialProjectionPublication
from pg16_inventory_control_preparation_gate import _formal_stock
from pg16_control_start_concurrency_gate import _command


def assert_current_admission(owner,api,edge,projector,backup,world,published):
    publication=UUID(published['publication_id'])
    args=dict(user=world.actor.user_id,version=world.actor.authorization_version,region=world.region,
              publication=publication,source=None,run=None,current=False)
    with Session(api) as db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        actor=load_formal_principal(db,world.actor.user_id)
        doc,lines=admission.selection(db,actor=actor,region=world.region,publication=publication)
        assert lines and lines[0].source_updated_at is None and doc['publication_id']==str(publication)
    with Session(api) as holder:
        actor=load_formal_principal(holder,world.actor.user_id)
        doc,lines=admission.selection(holder,actor=actor,region=world.region,publication=publication,current=True)
        with Session(owner) as db:
            pub=db.get(Publication,publication)
            origin=db.scalars(select(Origin).where(Origin.publication_id==pub.id)).first()
            material=db.get(MaterialProjectionLine,origin.material_line_id)
            material_pub=db.get(MaterialProjectionPublication,material.publication_id)
            coordinates={'source':pub.source_system_id,'file':pub.evidence_file_id,'material':material.material_id,
                         'binding':material_pub.binding_id,'policy':material.policy_id}
        for sql,key in (
            ('UPDATE public.source_systems SET enabled=false WHERE id=:id','source'),
            ('UPDATE public.files SET status=status WHERE id=:id','file'),
            ('UPDATE public.materials SET name=name WHERE id=:id','material'),
            ('UPDATE public.material_inventory_policies SET tracking_mode=tracking_mode WHERE id=:id','policy'),
            ('UPDATE public.oam_material_capture_bindings SET revoked_at=clock_timestamp() WHERE id=:id','binding')):
            with owner.connect() as contender:
                contender.execute(text("SET LOCAL lock_timeout='150ms'"))
                with pytest.raises(DBAPIError) as error:contender.execute(text(sql),{'id':coordinates[key]})
                assert error.value.orig.sqlstate=='55P03';contender.rollback()
    for engine in (edge,projector,backup):
        with engine.connect() as db:
            with pytest.raises(DBAPIError) as error:db.execute(text(admission.SQL),args)
            assert error.value.orig.sqlstate=='42501'
    for changes,code in [({'version':args['version']+1},'42501'),({'region':uuid4()},'42501'),({'publication':uuid4()},'23514')]:
        with api.connect() as db:
            with pytest.raises(DBAPIError) as error:db.execute(text(admission.SQL),args|changes)
            assert error.value.orig.sqlstate==code
    with api.connect() as db:
        with pytest.raises(DBAPIError) as error:db.execute(text('SELECT public.rsc_assert_opening_publication_0125(:source,:run,:region)'),
            dict(source=doc['source_system_id'],run=doc['sync_run_id'],region=world.region))
        assert error.value.orig.sqlstate=='42501'
    for sql,key in [('UPDATE public.source_systems SET enabled=false WHERE id=:id','source'),
                    ('UPDATE public.oam_material_capture_bindings SET revoked_at=clock_timestamp() WHERE id=:id','binding')]:
        with owner.connect() as db:
            db.execute(text(sql),{'id':coordinates[key]})
            with pytest.raises(DBAPIError) as error:db.execute(text(admission.SQL),args|{'current':True})
            assert error.value.orig.sqlstate=='23514';db.rollback()
    from pg16_inventory_control_normalization_gate import _mapping
    from test_inventory_control_mapping import command as mapping_command
    from app.inventory_control_mapping_models import InventoryControlMappingDecision
    with Session(owner) as db:
        pub=db.get(Publication,publication);grant=db.get(InventoryControlMappingDecision,pub.mapping_decision_id)
        _mapping(db,world.login,mapping_command(db,world,action='revoke',rules=None,revoked_grant_id=grant.id,
            expected_subject_sha256=grant.payload_sha256,evidence_file_id=world.mapping_file))
        with pytest.raises(DBAPIError) as error:db.execute(text(admission.SQL),args|{'current':True})
        assert error.value.orig.sqlstate=='23514';db.rollback()
    with owner.connect() as db:
        db.execute(text('UPDATE public.auth_sessions SET revoked_at=clock_timestamp() WHERE id=(SELECT auth_session_id FROM public.control_projection_publications WHERE id=:id)'),{'id':publication})
        assert db.scalar(text(admission.SQL),args|{'current':True})['publication_id']==str(publication)
        db.rollback()
    # Legacy API/service input must match the complete server-owned publication.
    command=_command(owner,world,published);before=_formal_stock(owner)
    for corrupt in [replace(command,control_lines=()),replace(command,control_lines=(replace(command.control_lines[0],control_qty=command.control_lines[0].control_qty+1),))]:
        with Session(api) as db:
            actor=load_formal_principal(db,world.actor.user_id)
            with pytest.raises(opening.OpeningStocktakeError) as error:opening.start_opening_stocktake(db,actor=actor,command=corrupt,idempotency_key=uuid4().hex,request_id=uuid4().hex)
            assert error.value.code=='control_publication_lines_mismatch';db.rollback()
    # Real nonzero task creation executes BEFORE INSERT and the full old graph;
    # force deferred constraints now and then roll back this isolated trial.
    with Session(api) as db:
        actor=load_formal_principal(db,world.actor.user_id)
        result=opening.start_opening_stocktake(db,actor=actor,command=command,idempotency_key=uuid4().hex,request_id=uuid4().hex)
        assert result.control_line_count==1 and not result.replayed
        db.execute(text('SET CONSTRAINTS ALL IMMEDIATE'));db.rollback()
    assert _formal_stock(owner)==before
    print('PG16 0125 current nonzero admission: actual API, private ACL, source/material revocation, evidence locks, raw-row tamper refusal and insert/deferred graph PASS',flush=True)


def assert_expired_new_task_commit(owner,api,world,published):
    command=_command(owner,world,published);before=_formal_stock(owner)
    with Session(owner) as db:end=db.get(Publication,UUID(published['publication_id'])).valid_until
    with Session(api) as db:
        actor=load_formal_principal(db,world.actor.user_id)
        opening.start_opening_stocktake(db,actor=actor,command=command,idempotency_key=uuid4().hex,request_id=uuid4().hex)
        remaining=(end-datetime.now(timezone.utc)).total_seconds()
        assert 0<remaining<40,'expiry fixture must be finite and current'
        time.sleep(remaining+.03)
        with pytest.raises(DBAPIError) as error:db.commit()
        assert error.value.orig.sqlstate=='23514';db.rollback()
    assert _formal_stock(owner)==before
    with Session(api) as db:
        actor=load_formal_principal(db,world.actor.user_id)
        assert admission.selection(db,actor=actor,region=world.region,publication=UUID(published['publication_id']))[1]
        with pytest.raises(opening.OpeningStocktakeError) as error:admission.selection(db,actor=actor,region=world.region,publication=UUID(published['publication_id']),current=True)
        assert error.value.code=='control_publication_not_admissible'
    print('PG16 0125 COMMIT after publication expiry: task/freeze/audit/outbox rolled back; immutable selection retained PASS',flush=True)


def assert_admission_migration(owner):
    m=runpy.run_path(str(Path(__file__).parents[1]/'alembic/versions/20261104_0125_opening_publication_admission.py'))
    before=_formal_stock(owner)
    for name,(args,*_) in m['FUNCTIONS'].items():
        for mutation in ('body','acl'):
            with owner.connect() as db:
                signature=f'public.{name}({args})'
                if mutation=='acl':db.exec_driver_sql('GRANT EXECUTE ON FUNCTION '+signature+' TO PUBLIC')
                else:
                    definition=db.scalar(text('SELECT pg_get_functiondef(CAST(:signature AS regprocedure))'),{'signature':signature})
                    db.exec_driver_sql(definition.replace('BEGIN\n','BEGIN\n    -- drift probe\n',1),execution_options={'no_parameters':True})
                with Operations.context(MigrationContext.configure(db)),pytest.raises(DBAPIError) as error:m['downgrade']()
                assert '0125 function source or ACL drift' in str(error.value.orig);db.rollback()
    for name in m['TRIGGERS']:
        with owner.connect() as db:
            db.exec_driver_sql('ALTER TABLE public.stocktake_tasks DISABLE TRIGGER '+name)
            with Operations.context(MigrationContext.configure(db)),pytest.raises(DBAPIError) as error:m['downgrade']()
            assert '0125 admission trigger drift' in str(error.value.orig);db.rollback()
    # With new actor evidence, 0126 intentionally prevents downgrade below it.
    # The runner exercises the full empty chain; populated proof remains here.
    with owner.begin() as db,Operations.context(MigrationContext.configure(db)):m['_verify']()
    assert _formal_stock(owner)==before
    print('PG16 0125 source/ACL/trigger ownership boundary and retained populated proof PASS',flush=True)
