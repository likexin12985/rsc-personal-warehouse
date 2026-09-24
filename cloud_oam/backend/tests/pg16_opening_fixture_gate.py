"""Actual published fixture and zero-opening prerequisites on owned PG16."""
from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.formal_access import load_formal_principal
from app.foundation_models import Role
from app.inventory_models import MaterialInventoryPolicy, StockBalance, StockLocation
from app.stocktake_models import FormalStocktakeTask
from app.database_security import validate_production_database_security
from app.edge_database_security import verify_edge_database_boundary
from pg16_opening_publication_fixture import prepare_stocktake_inventory
from pg16_release_gate_diagnostics import run_with_sanitized_database_diagnostics
from test_formal_access import make_organization, make_user, assign
from test_postgresql16_release_gate import _establish_multiround_stocktake_location


def run(engines, *, establish_dynamic_peer=False):
    owner=engines['star_oam_migrator'];api=engines['star_oam_api'];edge=engines['edge_inbox']
    with Session(owner) as db:
        hq=make_organization(db,name='Synthetic opening fixture HQ')
        region=make_organization(db,name='Synthetic opening fixture region',parent=hq)
        admin,_=make_user(db,hq,name='Synthetic opening reviewer')
        manager,_=make_user(db,region,name='Synthetic opening counter')
        reviewer,_=make_user(db,hq,name='Synthetic independent reconciliation approver')
        roles={r.code:r for r in db.scalars(select(Role))}
        assign(db,admin,roles['admin'],scope_type='national',scope_id='*')
        assign(db,reviewer,roles['admin'],scope_type='national',scope_id='*')
        assign(db,manager,roles['provincial_manager'],scope_type='organization',scope_id=str(region.id))
        db.commit();admin_id=admin.id;manager_id=manager.id;reviewer_id=reviewer.id
    fixture=run_with_sanitized_database_diagnostics(owner,
        lambda:prepare_stocktake_inventory(owner,edge,actor_user_id=admin_id,assignee_user_id=manager_id),
        replace_unlinked_database_failure_when=None)
    fixture['reconciliation_reviewer_id']=reviewer_id
    with Session(owner) as db:
        assert not db.scalar(text('SELECT EXISTS (SELECT 1 FROM inventory_transactions)'))
        assert not db.scalar(select(StockBalance.stock_account_id).limit(1))
        policies={r.material_id:r for r in db.scalars(select(MaterialInventoryPolicy))}
        assert (policies[fixture['material_id']].tracking_mode,policies[fixture['material_id']].quantity_scale,
            policies[fixture['material_id']].allow_fraction)==('none',3,True)
        assert (policies[fixture['concurrency_material_id']].tracking_mode,policies[fixture['concurrency_material_id']].quantity_scale,
            policies[fixture['concurrency_material_id']].allow_fraction)==('serial',0,False)
        assert len(fixture['control_lines'])==1 and fixture['control_lines'][0].control_qty==Decimal(0)
    with owner.connect() as db:
        with pytest.raises(DBAPIError) as caught:
            db.execute(text("UPDATE material_inventory_policies SET tracking_mode='serial' WHERE id=:id"),{'id':fixture['material_policy_id']})
            db.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
        assert caught.value.orig.sqlstate=='23514';db.rollback()
    print('Signed fixture: two published SKU policies, one explicit zero control row; stock untouched and policy tampering refused PASS',flush=True)
    with Session(owner) as db:
        transit=StockLocation(id=uuid4(),code='PG16-PUBLISHED-TRANSIT-'+uuid4().hex,name='Synthetic empty transit',
            location_type='transit',owner_org_id=fixture['region_org_id'],parent_id=fixture['location_id'],status='active')
        db.add(transit);db.commit();fixture['transit_location_id']=transit.id
    result=[]
    opening_cases=[('quantity','location_id',1),('serial','serial_replay_location_id',1),
                   ('recount','recount_location_id',1),('transit','transit_location_id',0)]
    if establish_dynamic_peer:
        # The report covers every visible account, including this normally
        # unestablished cutoff-replay fixture. Prove it through the same real
        # start/count/review/post/close path before requesting an export.
        opening_cases.append(('dynamic-peer','dynamic_peer_location_id',1))
    for label,key,count in opening_cases:
        task=_establish_multiround_stocktake_location(api,fixture={**fixture,'recount_location_id':fixture[key]},
            actor_user_id=admin_id,assignee_user_id=manager_id,expected_snapshot_line_count=count)
        with Session(api) as db:
            row=db.get(FormalStocktakeTask,task)
            assert row.status=='closed' and row.opening_authorization_version==load_formal_principal(db,manager_id).authorization_version
        result.append(dict(case=label,bookSnapshotLines=count,controlSnapshotLines=1,taskClosed=True))
        print('Published fixture '+label+': real start/count/two reviews/post/close PASS',flush=True)
    result.append(_exercise_opening(owner,api,fixture,admin_id,manager_id,positive=False))
    result.append(_exercise_opening(owner,api,fixture,admin_id,manager_id,positive=True))
    assert_reconciliation_event_migration(owner,api,edge)
    validate_production_database_security(api,expected_runtime_role='star_oam_api',expected_migration_role='star_oam_migrator')
    verify_edge_database_boundary(edge)
    with owner.connect() as revision_connection:
        migration_head=revision_connection.scalar(text('SELECT version_num FROM alembic_version'))
    return dict(status='passed',scope='local-native-pg16-opening-release-fixtures',migrationHead=migration_head,
        actualPostgreSQL16=True,reconciliationEventMigrationDriftRefused=True,publishedFixture=True,unmanagedControlSeedRemoved=True,policyTamperingRefused=True,
        openingPrerequisites=result,githubReleaseGate=False,fullReleaseGate=False,productionAcceptance=False)


def _count_facts(engine):
    from pg16_opening_actor_commit_gate import facts
    rows=facts(engine)
    tables=('stocktake_count_lines','stocktake_count_serials','stocktake_count_observations',
            'stocktake_scope_count_completions','stocktake_difference_set_completions',
            'stocktake_round_submissions','stocktake_differences','inventory_movements',
            'inventory_movement_serials','serial_current_positions')
    with engine.connect() as db:
        for table in tables:
            rows[table]=db.scalar(text("SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),'[]'::jsonb) FROM public."+table+' t'))
    return rows


def _exercise_opening(owner, api, fixture, admin, manager, *, positive):
    from app.formal_services import opening_stocktake as opening,opening_stocktake_count as count
    from app.formal_services import opening_stocktake_review as review,opening_stocktake_finalize as final
    from app.stocktake_models import FormalStocktakeScope,StocktakeDifference,InventoryFreeze
    from pg16_opening_serial_fixture_gate import assert_duplicate_serial_guards
    token=uuid4().hex
    def write(service, actor_id, command, step):
        with Session(api,expire_on_commit=False) as db:
            actor=load_formal_principal(db,actor_id)
            result=run_with_sanitized_database_diagnostics(api,lambda:service(db,actor=actor,command=command,
                idempotency_key=token+'-'+step,request_id=token+'-'+step),replace_unlinked_database_failure_when=None)
            db.commit();return result
    locations=(fixture['difference_peer_location_id'],) if positive else (
        fixture['concurrency_location_id'],fixture['concurrency_competing_location_id'])
    started=write(opening.start_opening_stocktake,manager,opening.StartOpeningStocktakeCommand(
        task_no='PG16-PUBLISHED-'+token,region_org_id=fixture['region_org_id'],
        control_source_system_id=fixture['control_source_system_id'],control_sync_run_id=fixture['control_sync_run_id'],
        control_sync_scope_key=fixture['control_scope_key'],control_lines=fixture['control_lines'],
        scopes=tuple(opening.OpeningStocktakeScopeInput(owner_org_id=fixture['region_org_id'],location_id=location,
            assignee_user_id=manager,freeze_mode='hard') for location in locations),
        blind_count=True,deadline=fixture['deadline'],note='Real published fixture lifecycle'),'start')
    assert started.control_line_count==1 and started.snapshot_line_count==1
    with Session(api) as db:
        scopes={r.location_id:r.id for r in db.scalars(select(FormalStocktakeScope).where(FormalStocktakeScope.task_id==started.task_id))}
    if not positive:
        assert_duplicate_serial_guards(api,fixture=fixture,assignee_user_id=manager,task_id=started.task_id,
            round_id=started.initial_round_id,scope_id=scopes[locations[0]],snapshot=_count_facts)
        print('Immutable SN fixture: cross-table serial and case-folded QR duplicate writes rejected by real PG guards; all facts unchanged PASS',flush=True)
    for index,location in enumerate(locations):
        observations=(count.OpeningPhysicalObservationInput(material_id=fixture['material_id'],
            material_identifier_raw=fixture['material_sku_code'],material_identifier_type='sku_code',condition_code='new',
            availability_bucket='available',counted_qty=Decimal(1),count_method='import'),) if positive else ()
        count_command=count.SubmitOpeningStocktakeScopeCountCommand(
            task_id=started.task_id,round_id=started.initial_round_id,scope_id=scopes[location],physical_observations=observations,
            zero_confirmed=index==1)
        if positive:
            before_prevalidation=_count_facts(owner)
            with Session(api) as db:
                preview=count.prevalidate_opening_stocktake_scope_count(
                    db,actor=load_formal_principal(db,manager),command=count_command,
                    idempotency_key=token+'-count-'+str(index),
                    request_id=token+'-count-'+str(index))
                assert preview.observation_count==1
                assert preview.pending_verification_input_ordinals==()
                assert len(preview.binding_sha256)==64
                db.commit()
            with Session(api) as db:
                repeated=count.prevalidate_opening_stocktake_scope_count(
                    db,actor=load_formal_principal(db,manager),command=count_command,
                    idempotency_key=token+'-count-'+str(index),
                    request_id=token+'-count-recheck-'+str(index))
                assert repeated.binding_sha256==preview.binding_sha256
                db.commit()
            assert _count_facts(owner)==before_prevalidation
            print('Opening import business prevalidation: API role read-only, stable resolved binding across transactions and key reusable PASS',flush=True)
            with Session(api) as db:
                try:
                    count.confirm_prevalidated_opening_stocktake_scope_count(
                        db,actor=load_formal_principal(db,manager),command=count_command,
                        expected_prevalidation=replace(preview,binding_sha256='0'*64),
                        idempotency_key=token+'-count-'+str(index),request_id=token+'-confirm-stale')
                except count.OpeningStocktakeCountError as error:
                    assert error.code=='opening_import_confirmation_preview_changed'
                else:
                    raise AssertionError('changed import preview unexpectedly accepted')
                db.rollback()
            assert _count_facts(owner)==before_prevalidation
            with Session(api) as db:
                proposed=count.confirm_prevalidated_opening_stocktake_scope_count(
                    db,actor=load_formal_principal(db,manager),command=count_command,
                    expected_prevalidation=preview,idempotency_key=token+'-count-'+str(index),
                    request_id=token+'-confirm-rollback')
                assert proposed.scope_completed and not proposed.replayed
                db.rollback()
            assert _count_facts(owner)==before_prevalidation
            with Session(api) as db:
                counted=count.confirm_prevalidated_opening_stocktake_scope_count(
                    db,actor=load_formal_principal(db,manager),command=count_command,
                    expected_prevalidation=preview,idempotency_key=token+'-count-'+str(index),
                    request_id=token+'-confirm-commit')
                db.commit()
            print('Opening import confirmation: API role rejects stale preview; complete count rollback preserves all facts; same-key fresh transaction commits PASS',flush=True)
        else:
            counted=write(count.submit_opening_stocktake_scope_count,manager,count_command,'count-'+str(index))
    assert counted.round_sealed and counted.task_status=='submitted'
    with Session(api) as db:
        differences=list(db.scalars(select(StocktakeDifference).where(StocktakeDifference.task_id==started.task_id)))
        assert len(differences)==(2 if positive else 0)
        assert sum(r.difference_type=='control_unassigned' for r in differences)==int(positive)
        items=tuple(review.OpeningStocktakeReviewItemInput(difference_id=r.id,
            decision='pending_verification' if r.difference_type=='control_unassigned' else 'accept_for_posting',
            comment='Synthetic physical +1 is retained separately from zero OAM control') for r in differences)
    command=review.SubmitOpeningStocktakeReviewCommand(task_id=started.task_id,round_id=started.initial_round_id,
        decision='approve',items=items,comment='Exact synthetic physical count reviewed')
    regional=write(review.submit_opening_region_review,manager,command,'region')
    headquarters=write(review.submit_opening_headquarters_review,admin,command,'hq')
    assert regional.resulting_task_status=='hq_review' and headquarters.resulting_task_status=='approved'
    with Session(api) as db:version=db.get(FormalStocktakeTask,started.task_id).version
    posting=final.PostOpeningStocktakeCommand(task_id=started.task_id,expected_version=version)
    posted=write(final.post_approved_opening_stocktake,admin,posting,'post')
    assert posted.total_quantity==Decimal(int(positive)) and posted.pending_control_difference_count==int(positive)
    after=_count_facts(owner)
    replay=write(final.post_approved_opening_stocktake,admin,posting,'post')
    assert replay.replayed and replace(replay,replayed=False)==posted
    assert _count_facts(owner)==after
    if positive:
        from app.formal_services import opening_control_reconciliation as reconciliation
        close_command=final.CloseOpeningStocktakeCommand(task_id=started.task_id,expected_version=posted.task_version)
        before_reconciliation=_count_facts(owner)
        with pytest.raises(final.OpeningStocktakeFinalizeError) as blocked:
            write(final.close_posted_opening_stocktake,admin,close_command,'close-before-reconciliation')
        assert blocked.value.code=='opening_close_reconciliation_pending'
        assert _count_facts(owner)==before_reconciliation
        run=write(reconciliation.start_opening_control_reconciliation,admin,
            reconciliation.StartOpeningControlReconciliationCommand(task_id=started.task_id,
                expected_task_version=posted.task_version),'reconciliation-create')
        assert run.item_count==1 and run.status=='differences'
        with Session(api) as db:
            detail=reconciliation.opening_control_reconciliation_detail(db,actor=load_formal_principal(db,manager),
                reconciliation_run_id=run.reconciliation_run_id)
        explained=write(reconciliation.explain_opening_control_reconciliation,manager,
            reconciliation.ExplainOpeningControlReconciliationCommand(reconciliation_run_id=run.reconciliation_run_id,
                expected_version=run.version,items=tuple(reconciliation.OpeningControlExplanationInput(
                    reconciliation_item_id=item.reconciliation_item_id,expected_version=item.version,
                    explanation='Synthetic physical count proves one item; OAM zero remains an independent source fact',
                    evidence_reference='synthetic-count-evidence:'+str(started.task_id)) for item in detail.items)),
            'reconciliation-explain')
        approved=write(reconciliation.approve_opening_control_reconciliation,fixture['reconciliation_reviewer_id'],
            reconciliation.ApproveOpeningControlReconciliationCommand(reconciliation_run_id=run.reconciliation_run_id,
                expected_version=explained.version,comment='Independent synthetic evidence review; no inventory overwrite'),
            'reconciliation-approve')
        assert approved.status=='approved' and approved.resolved_item_count==1
        after_reconciliation=_count_facts(owner)
        for table in ('inventory_transactions','inventory_movements','inventory_movement_serials',
                      'stock_balances','inventory_ledger_heads','serial_current_positions'):
            assert after_reconciliation[table]==before_reconciliation[table]
        print('Positive opening: unresolved control prevents close; independent reconciliation approved without stock changes PASS',flush=True)
    closed=write(final.close_posted_opening_stocktake,admin,
        final.CloseOpeningStocktakeCommand(task_id=started.task_id,expected_version=posted.task_version),'close')
    with Session(api) as db:
        assert closed.resulting_task_status=='closed' and db.get(FormalStocktakeTask,started.task_id).status=='closed'
        assert not db.scalar(select(InventoryFreeze.id).where(InventoryFreeze.task_id==started.task_id,InventoryFreeze.status=='active'))
        if positive:
            assert db.get(StockBalance,fixture['difference_peer_account_id']).quantity==Decimal(1)
            assert posted.inventory_transaction_id is not None
            assert db.scalar(text('SELECT SUM(quantity) FROM inventory_movements WHERE transaction_id=:id'),
                {'id':posted.inventory_transaction_id})==Decimal(1)
            assert db.scalar(select(StocktakeDifference.id).where(StocktakeDifference.task_id==started.task_id,
                StocktakeDifference.difference_type=='control_unassigned')) is not None
        else:assert posted.inventory_transaction_id is None
    print('Published '+('positive' if positive else 'SN')+' opening: reviews, posting replay, close and control difference retention PASS',flush=True)
    return dict(case='positive' if positive else 'serial-duplicate',bookSnapshotLines=1,controlSnapshotLines=1,
        postedQuantity=str(posted.total_quantity),controlDifferencesAtPosting=int(positive),independentReconciliationApproved=positive,
        unresolvedControlAtClose=0,postReplayNoDuplicates=True,taskClosed=True,
        importBusinessPrevalidationReadOnly=positive,
        importConfirmation=(dict(stalePreviewRejected=True,rollbackPreservesFacts=True,
            originalKeyReusableAfterRollback=True,apiRoleCommitted=True) if positive else None))


def all_reconciliation_facts(engine):
    result=_count_facts(engine)
    with engine.connect() as db:
        for table in ('reconciliation_runs','reconciliation_items','reconciliation_commands',
            'opening_control_reconciliation_runs','opening_control_reconciliation_items',
            'opening_control_reconciliation_command_consumptions','inventory_opening_establishments'):
            result[table]=db.scalar(text("SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),'[]'::jsonb) FROM public."+table+' t'))
    return result


def assert_reconciliation_event_migration(owner,api,edge):
    from pathlib import Path
    import runpy
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    m=runpy.run_path(str(Path(__file__).parents[1]/'alembic/versions/20261106_0127_reconciliation_event_binding.py'))
    before=all_reconciliation_facts(owner)
    signature=m['SIGNATURE']
    for engine in (api,edge):
        with engine.connect() as db:
            with pytest.raises(DBAPIError) as caught:db.execute(text('SELECT '+signature))
            assert caught.value.orig.sqlstate=='42501'
    for fault in ('source','acl','trigger','alias'):
        with owner.connect() as db,Operations.context(MigrationContext.configure(db)):
            if fault=='source':
                definition=db.scalar(text('SELECT pg_get_functiondef(CAST(:signature AS regprocedure))'),{'signature':signature})
                assert definition.count('DECLARE\n')==1
                db.execute(text(definition.replace('DECLARE\n','DECLARE\n-- synthetic source drift\n',1)))
            elif fault=='acl':db.execute(text('GRANT EXECUTE ON FUNCTION '+signature+' TO star_oam_api'))
            elif fault=='trigger':db.execute(text('ALTER TABLE opening_control_reconciliation_command_consumptions DISABLE TRIGGER trg_opening_reconciliation_consumptions_guard_0026'))
            else:db.execute(text('CREATE TRIGGER synthetic_0127_alias BEFORE INSERT ON opening_control_reconciliation_command_consumptions FOR EACH ROW EXECUTE FUNCTION '+signature))
            with pytest.raises(DBAPIError):m['downgrade']()
            db.rollback()
    assert all_reconciliation_facts(owner)==before
    return True
