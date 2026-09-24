"""Actual control publishing on protected CI or freshly owned native PG16."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from uuid import UUID
from datetime import timedelta
import time

import pytest
from sqlalchemy import select,text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app import inventory_control_projection as service
from app.inventory_control_projection_models import TABLES, ControlProjectionPublication as Publication, ControlProjectionLine as Line
from test_inventory_control_projection import publication_command, consume
from pg16_inventory_control_preparation_gate import _formal_stock
from cloud_oam.edge_sync.test_inventory_control_capture import row as source_row


def publication_snapshot(engine):
    with engine.connect() as db:
        return tuple(tuple(db.scalars(text('SELECT to_jsonb(t) FROM public.'+table+' t ORDER BY id'))) for table in TABLES)


def assert_control_publication_gate(owner,edge,api,projector,backup,world,capture):
    from pg16_control_start_concurrency_gate import assert_return_migration_drift_refused
    assert_return_migration_drift_refused(owner)
    from pg16_inventory_control_normalization_gate import _mapping,RULES
    from test_inventory_control_mapping import command as mapping_command
    from app.inventory_control_mapping_models import InventoryControlMappingDecision
    before=_formal_stock(owner)[1:]; history=[]
    from pg16_zero_control_opening_gate import start_zero_opening, replay_zero_opening
    zero_opening=None
    good=source_row('pub-one',materialStatus='usable',materialStockType='stock');good['materialCode']=world.code
    for label,rows,full in [('full',[good],True),('delta',[dict(good,qtyStock='4.000')],False),
                            ('zero',[],False),('reappear',[good],False)]:
        capture(rows,full=full)
        with Session(owner) as db:
            selected_mapping={'decision_id':str(world.mapping)}
            if label=='full':
                original=db.get(InventoryControlMappingDecision,world.mapping)
                _mapping(db,world.login,mapping_command(db,world,action='revoke',rules=None,
                    revoked_grant_id=original.id,expected_subject_sha256=original.payload_sha256,evidence_file_id=world.mapping_file))
                selected_mapping=_mapping(db,world.login,mapping_command(db,world,
                    rules=dict(RULES,revision='pg16-start-expiry'),valid_to=service.authority._now(db)+timedelta(seconds=30),
                    evidence_file_id=world.mapping_file))
                db.commit()
            cmd=publication_command(db,world,selected_mapping)
            args=dict(**world.login,command=cmd)
            review=service.preview_control_publication(db,**args)
            args['review_sha256']=review['review_sha256']
        def run():
            with Session(owner) as db:
                result=service.execute_control_publication(db,**args);db.commit();return result
        if label=='full':
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures=[executor.submit(run) for _ in range(2)]
                result=futures[0].result(timeout=60)
                assert futures[1].result(timeout=60)==result
        elif label=='delta':
            from pg16_control_start_concurrency_gate import publish_while_start_waits
            result=publish_while_start_waits(owner,api,world,history[-1][0],args)
        else:result=run()
        with Session(owner) as db:
            consume(db,result)
            assert service.read_control_publication(db,**args)==result
            for old,old_args in history:
                consume(db,old,historical=True)
                assert service.read_control_publication(db,**old_args)==old
        if label=='full':
            from pg16_opening_publication_admission_gate import assert_current_admission,assert_expired_new_task_commit
            assert_current_admission(owner,api,edge,projector,backup,world,result)
            assert_expired_new_task_commit(owner,api,world,result)
            with Session(owner) as db:
                restored=_mapping(db,world.login,mapping_command(db,world,
                    rules=dict(RULES,revision='pg16-after-start-expiry'),evidence_file_id=world.mapping_file))
                db.commit();world.mapping=UUID(restored['decision_id'])
        if label=='delta':
            from pg16_opening_actor_commit_gate import assert_actor_commit
            assert_actor_commit(owner,api,edge,projector,backup,world,result)
        history.append((result,args))
        assert result['record_count']==(0 if label=='zero' else 1)
        assert _formal_stock(owner)[1:]==before
        if label=='zero':
            zero_opening=start_zero_opening(owner,api,world,result)
            replay_zero_opening(owner,api,world,zero_opening)
            before=_formal_stock(owner)[1:]
        if label=='reappear':
            replay_zero_opening(owner,api,world,zero_opening)
    with Session(owner) as db:
        result,args=history[-1];publication=db.get(Publication,UUID(result['publication_id']))
        line=db.scalars(select(Line).where(Line.publication_id==publication.id)).one()
        version_id=line.version_id;object_id=line.external_object_id;batch_id=publication.sync_batch_id
    for sql,values in [
        ('UPDATE public.control_projection_publications SET record_count=record_count WHERE id=:id',{'id':publication.id}),
        ('DELETE FROM public.control_projection_origins WHERE publication_id=:id',{'id':publication.id}),
        ('UPDATE public.external_object_versions SET is_current=false WHERE id=:id',{'id':version_id}),
        ('UPDATE public.external_objects SET current_version_id=NULL WHERE id=:id',{'id':object_id}),
        ("UPDATE public.sync_batches SET record_count=99 WHERE id=:id",{'id':batch_id}),
    ]:
        with owner.connect() as db:
            with pytest.raises(DBAPIError) as error:
                db.execute(text(sql),values);db.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
            assert error.value.orig.sqlstate in ('23514','P0001');db.rollback()
    for engine in (api,edge,projector,backup):
        with Session(engine) as db:
            with pytest.raises(service.admission.ControlAdmissionError,match='requires_direct_owner'):
                service.read_control_publication(db,**history[-1][1])
        if engine is not backup:
            for table in TABLES:
                with engine.connect() as db:
                    with pytest.raises(DBAPIError) as error:db.execute(text('SELECT 1 FROM public.'+table))
                    assert error.value.orig.sqlstate=='42501'
    for table in TABLES:
        with owner.connect() as db,backup.connect() as copy:
            assert db.scalar(text('SELECT count(*) FROM public.'+table))==copy.scalar(text('SELECT count(*) FROM public.'+table))
    assert publication_snapshot(owner)==publication_snapshot(backup)
    # A valid execution can expire before COMMIT. The deferred database guard
    # must roll back its entire publication, rather than trust the service clock.
    from pg16_inventory_control_normalization_gate import _mapping,RULES
    from test_inventory_control_mapping import command as mapping_command
    from app.inventory_control_mapping_models import InventoryControlMappingDecision
    capture([good],full=False)
    preserved=publication_snapshot(owner)
    with Session(owner) as db:
        original=db.get(InventoryControlMappingDecision,world.mapping)
        _mapping(db,world.login,mapping_command(db,world,action='revoke',rules=None,
            revoked_grant_id=original.id,expected_subject_sha256=original.payload_sha256,evidence_file_id=world.mapping_file))
        end=service.authority._now(db)+timedelta(seconds=4)
        grant=_mapping(db,world.login,mapping_command(db,world,rules=dict(RULES,revision='pg16-commit-expiry'),
            valid_to=end,evidence_file_id=world.mapping_file))
        cmd=publication_command(db,world,grant)
        preview=service.preview_control_publication(db,**world.login,command=cmd)
        service.execute_control_publication(db,**world.login,command=cmd,review_sha256=preview['review_sha256'])
        time.sleep(max(0,(end-service.authority._now(db)).total_seconds())+0.025)
        with pytest.raises(DBAPIError) as error:db.commit()
        assert error.value.orig.sqlstate=='23514';db.rollback()
    assert publication_snapshot(owner)==preserved
    assert _formal_stock(owner)[1:]==before
    from pg16_control_start_concurrency_gate import assert_return_event_locks_retained
    assert_return_event_locks_retained(owner,api,world)
    from pg16_opening_publication_admission_gate import assert_admission_migration
    assert_admission_migration(owner)
    from pg16_opening_actor_commit_gate import assert_actor_migration
    assert_actor_migration(owner)
    from pg16_opening_control_directory_gate import assert_opening_control_directory_gate
    assert_opening_control_directory_gate(owner,api,edge,projector,backup,world,history)
    print('PG16 actual control publication: concurrent replay, full/delta/zero/reappearance, API zero-opening commit/replay, history, SQL graph guards and private ACL PASS',flush=True)
    return dict(case='actual-control-publication',publicationCount=len(history),concurrentReplay=True,
        openingConsumer=True,historicalRecovery=True,inventoryUnchanged=True,privateRoleBoundary=True,expiredCommitRolledBack=True,
        currentActorCommitExpiryRolledBack=True,actorCommitHttp403Verified=True,scheduledGlobalDenyCommitRolledBack=True,actorVersionImmutable=True,actorMigrationDriftRefused=True,
        serverControlledSelectedStart=True,rawStartAdmissionEnforced=True,readOnlyStartResultRecovery=True,
        newTaskCommitExpiryRolledBack=True,originalPublisherSessionNotRequired=True,openingAdmissionMigrationDriftRefused=True,
        zeroOpeningCommittedByApi=True,zeroOpeningReplayAfterReplacement=True,
        concurrentZeroOpeningSingleTask=True,unprovenEmptyRunRefused=True,
        publisherOpeningContentionObserved=True,replacedOpeningRefusedWithoutDeadlock=True,
        relevantReturnEventLedgerLocksRetained=True,migrationSourceAclTriggerDriftRefused=True,
        apiControlDirectoryScopedAndRedacted=True,controlDirectoryPrivateRolesRefused=True,
        controlDirectoryMigrationDriftRefused=True)
