"""Synthetic personal stock via real count, independent reviews and posting."""
from decimal import Decimal
from uuid import uuid4
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.formal_access import load_formal_principal
from app.formal_services import opening_stocktake as opening, opening_stocktake_count as count
from app.formal_services import opening_stocktake_review as review, opening_stocktake_finalize as final
from app.formal_services import opening_control_reconciliation as reconciliation
from app.stocktake_models import FormalStocktakeTask, FormalStocktakeScope, StocktakeDifference


def establish_personal_stock(api, fixture, *, admin, manager, engineer, reviewer):
    token = uuid4().hex
    def write(service, actor_id, command, step):
        with Session(api, expire_on_commit=False) as db:
            result = service(db, actor=load_formal_principal(db, actor_id), command=command,
                             idempotency_key=token+'-'+step, request_id=token+'-'+step)
            db.commit()
            return result
    started = write(opening.start_opening_stocktake, manager, opening.StartOpeningStocktakeCommand(
        task_no='LOSS-OPENING-'+token, region_org_id=fixture['region_org_id'],
        control_source_system_id=fixture['control_source_system_id'], control_sync_run_id=fixture['control_sync_run_id'],
        control_sync_scope_key=fixture['control_scope_key'], control_lines=fixture['control_lines'],
        scopes=(opening.OpeningStocktakeScopeInput(owner_org_id=fixture['region_org_id'],
            location_id=fixture['difference_peer_location_id'], assignee_user_id=engineer, freeze_mode='hard'),),
        deadline=fixture['deadline'], note='Synthetic independent personal opening'), 'start')
    with Session(api) as db:
        scope = db.scalar(select(FormalStocktakeScope.id).where(FormalStocktakeScope.task_id==started.task_id))
    counted = write(count.submit_opening_stocktake_scope_count, engineer, count.SubmitOpeningStocktakeScopeCountCommand(
        task_id=started.task_id, round_id=started.initial_round_id, scope_id=scope,
        physical_observations=(count.OpeningPhysicalObservationInput(material_identifier_raw=fixture['material_sku_code'],
            material_identifier_type='sku_code', condition_code='new', availability_bucket='available',
            counted_qty=Decimal(1), serial_no_raw=fixture.get('selected_serial_no'),
            serial_identifier_type='serial_no' if fixture.get('selected_serial_no') else None, count_method='manual'),)), 'count')
    assert counted.round_sealed and counted.task_status=='submitted'
    with Session(api) as db:
        differences = list(db.scalars(select(StocktakeDifference).where(StocktakeDifference.task_id==started.task_id)))
        assert len(differences)==2
        items = tuple(review.OpeningStocktakeReviewItemInput(difference_id=row.id,
            decision='pending_verification' if row.difference_type=='control_unassigned' else 'accept_for_posting',
            comment='Synthetic physical one and independent zero control retained') for row in differences)
    command = review.SubmitOpeningStocktakeReviewCommand(task_id=started.task_id, round_id=started.initial_round_id,
        decision='approve', items=items, comment='Independent synthetic count review')
    regional = write(review.submit_opening_region_review, manager, command, 'region')
    headquarters = write(review.submit_opening_headquarters_review, admin, command, 'hq')
    assert regional.resulting_task_status=='hq_review' and headquarters.resulting_task_status=='approved'
    with Session(api) as db:
        version = db.get(FormalStocktakeTask, started.task_id).version
    posted = write(final.post_approved_opening_stocktake, admin,
        final.PostOpeningStocktakeCommand(task_id=started.task_id, expected_version=version), 'post')
    assert posted.total_quantity==Decimal(1) and posted.pending_control_difference_count==1
    run = write(reconciliation.start_opening_control_reconciliation, admin,
        reconciliation.StartOpeningControlReconciliationCommand(task_id=started.task_id,
            expected_task_version=posted.task_version), 'reconciliation-start')
    with Session(api) as db:
        detail = reconciliation.opening_control_reconciliation_detail(db, actor=load_formal_principal(db, manager),
            reconciliation_run_id=run.reconciliation_run_id)
    explained = write(reconciliation.explain_opening_control_reconciliation, manager,
        reconciliation.ExplainOpeningControlReconciliationCommand(reconciliation_run_id=run.reconciliation_run_id,
            expected_version=run.version, items=tuple(reconciliation.OpeningControlExplanationInput(
                reconciliation_item_id=item.reconciliation_item_id, expected_version=item.version,
                explanation='Synthetic physical stock one differs from independent zero control',
                evidence_reference='synthetic-personal-count:'+str(started.task_id)) for item in detail.items)), 'explain')
    approved = write(reconciliation.approve_opening_control_reconciliation, reviewer,
        reconciliation.ApproveOpeningControlReconciliationCommand(reconciliation_run_id=run.reconciliation_run_id,
            expected_version=explained.version, comment='Independent synthetic evidence review'), 'reconcile-approve')
    assert approved.status=='approved'
    closed = write(final.close_posted_opening_stocktake, admin,
        final.CloseOpeningStocktakeCommand(task_id=started.task_id, expected_version=posted.task_version), 'close')
    assert closed.resulting_task_status=='closed'
    print('Synthetic personal stock: engineer count, independent region/HQ reviews, posting, reconciliation and close PASS', flush=True)
