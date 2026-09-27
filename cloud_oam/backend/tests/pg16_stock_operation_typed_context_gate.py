"""Existing committed return facts across typed-schema migrations on owned PG16."""
import datetime
import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from app.foundation_models import Role
from app.formal_access import load_formal_principal
from pg16_opening_publication_fixture import prepare_stocktake_inventory
from pg16_opening_fixture_gate import all_reconciliation_facts
from test_formal_access import make_organization, make_user, assign
import test_postgresql16_release_gate as gate


def _prepare(engines):
    owner, api, edge = (engines[k] for k in ('star_oam_migrator', 'star_oam_api', 'edge_inbox'))
    with owner.connect() as db:
        assert db.scalar(text('SELECT current_database()')) == 'rsc_pg16_release_gate'
        assert db.scalar(text('SELECT current_user')) == 'star_oam_migrator'
        assert int(db.scalar(text('SHOW server_version_num')))//10000 == 16
        assert not db.scalar(text('SELECT EXISTS (SELECT 1 FROM inventory_transactions)'))
        assert not db.scalar(text('SELECT EXISTS (SELECT 1 FROM users)'))
    with Session(owner) as db:
        hq = make_organization(db, name='Synthetic typed return HQ')
        region = make_organization(db, name='Synthetic typed return region', parent=hq)
        admin, _ = make_user(db, hq, name='Synthetic typed return admin')
        manager, _ = make_user(db, region, name='Synthetic typed return manager')
        roles = {r.code: r for r in db.scalars(select(Role))}
        assign(db, admin, roles['admin'], scope_type='national', scope_id='*')
        assign(db, manager, roles['provincial_manager'], scope_type='organization', scope_id=str(region.id))
        db.commit()
        admin_id, manager_id, hq_id = admin.id, manager.id, hq.id
    fixture = prepare_stocktake_inventory(owner, edge, actor_user_id=admin_id, assignee_user_id=manager_id, control_material="quantity")
    from datetime import timedelta
    from uuid import uuid4
    from app.inventory_models import StockLocation, StockAccount, CustodyAssignment
    from pg16_personal_stock_fixture import establish_personal_stock
    with Session(owner) as db:
        engineer, person = make_user(db, db.get(type(hq), fixture['region_org_id']), name='Synthetic typed return engineer')
        assign(db, engineer, db.scalar(select(Role).where(Role.code=='technician')), scope_type='person', scope_id=str(person.id))
        person_id, engineer_id = person.id, engineer.id
        reviewer, _ = make_user(db, db.get(type(hq), hq_id), name='Synthetic source reviewer')
        assign(db, reviewer, db.scalar(select(Role).where(Role.code=='admin')), scope_type='national', scope_id='*')
        now = datetime.datetime.now(datetime.timezone.utc)
        location = StockLocation(id=uuid4(), code='TYPED-PERSONAL-'+uuid4().hex,
            name='Synthetic typed return personal warehouse', location_type='personal',
            owner_org_id=fixture['region_org_id'], parent_id=fixture['location_id'],
            custodian_person_id=person_id, status='active')
        db.add(location); db.flush()
        db.add(CustodyAssignment(location_id=location.id, custodian_person_id=person_id,
            valid_from=now-timedelta(days=1)))
        account = StockAccount(id=uuid4(), owner_org_id=fixture['region_org_id'],
            custodian_person_id=person_id, location_id=location.id, material_id=fixture['material_id'],
            condition_code='new', availability_bucket='available')
        db.add(account)
        recovered_account = StockAccount(id=uuid4(), owner_org_id=fixture['region_org_id'],
            custodian_person_id=person_id, location_id=location.id, material_id=fixture['material_id'],
            condition_code='used', availability_bucket='available')
        db.add(recovered_account)
        db.commit()
        account_id, recovery_account_id = account.id, recovered_account.id
        fixture = dict(fixture, difference_peer_location_id=location.id,
            difference_peer_account_id=account_id, reconciliation_reviewer_id=reviewer.id)
    establish_personal_stock(api, fixture, admin=admin_id, manager=manager_id,
        engineer=engineer_id, reviewer=fixture['reconciliation_reviewer_id'])
    from types import SimpleNamespace
    from work_order_fixtures import add_order
    with Session(owner) as db:
        work_order = add_order(db, SimpleNamespace(person=SimpleNamespace(id=person_id),
            organization=SimpleNamespace(id=fixture['region_org_id'])))
        db.commit()
        work_order_id = work_order.id
    return engineer_id, person_id, recovery_account_id, work_order_id, fixture['material_id']


def snapshot(owner):
    """Full business facts, normalizing only the new line discriminator."""
    result = all_reconciliation_facts(owner)
    with owner.connect() as db:
        for table in ('stock_operation_orders', 'stock_operation_lines', 'stock_operation_serials',
                      'stock_operation_cancellations', 'work_order_material_operations',
                      'work_order_material_lines', 'work_order_material_serials',
                      'stock_accounts', 'custody_assignments',
                      'notification_events', 'notification_recipients', 'notification_deliveries',
                      'notification_attempts', 'notification_person_targets', 'notification_target_bindings',
                      'permissions', 'role_permissions'):
            expression = "to_jsonb(t)-'operation_type'" if table == 'stock_operation_lines' else 'to_jsonb(t)'
            result[table] = db.scalar(text(f"SELECT COALESCE(jsonb_agg({expression} ORDER BY ({expression})::text),'[]'::jsonb) FROM {table} t"))
    return result


def run(engines, migrate):
    from decimal import Decimal
    from pathlib import Path
    import runpy
    from uuid import uuid4
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy.exc import DBAPIError
    from app.demand_models import WorkOrderMaterialLine
    from app.formal_services import work_order_material as material
    from app.formal_services.stock_return_commands import submit_return, cancel_return
    from app.formal_services.stock_return_plan import preview_return
    from app.formal_services.stock_return_facts import order_result, cancellation_result
    from app.stock_operation_models import StockOperationOrder, StockOperationCancellation
    from app.stock_return_schemas import StockReturnPreviewIn, StockReturnSubmitIn, StockReturnCancelIn
    from pg16_stock_return_gate import _destination
    owner, api = engines['star_oam_migrator'], engines['star_oam_api']
    engineer, person, account, work_order, sku = _prepare(engines)
    target, transit = _destination(owner, account)
    with Session(api) as db:
        actor = load_formal_principal(db, engineer)
        recovered, _ = material.execute_recover_operation(db, actor=actor, work_order_id=work_order,
            lines=(material.WorkOrderMaterialLineInput(material_id=sku, stock_account_id=account,
                target_stock_account_id=account, quantity=Decimal(1), condition_before='used'),),
            idempotency_key=uuid4().hex, request_id=uuid4().hex)
        origin = db.scalar(select(WorkOrderMaterialLine.id).where(WorkOrderMaterialLine.operation_id == recovered.id))
        db.commit()
    request = StockReturnPreviewIn(operator_person_id=person, target_location_id=target,
        transit_location_id=transit, reason='Synthetic return retained across typed migration',
        lines=[dict(source_recovery_line_id=origin, stock_account_id=account, quantity='1')])
    with Session(api) as db:
        actor = load_formal_principal(db, engineer)
        preview, _ = preview_return(db, actor=actor, work_order_id=work_order, request=request)
        command = StockReturnSubmitIn(**request.model_dump(), expected_plan_hash=preview.plan_hash,
            idempotency_key=uuid4().hex, request_id=uuid4().hex)
        posted = submit_return(db, actor=actor, work_order_id=work_order, request=command)
        db.commit()
    before = snapshot(owner)
    assert len(before['stock_operation_orders']) == len(before['stock_operation_lines']) == 1
    migrate('typed-populated-downgrade', 'downgrade', '20261120_0141')
    assert snapshot(owner) == before
    migrate('typed-populated-reupgrade', 'upgrade', 'head')
    assert snapshot(owner) == before
    with owner.connect() as db:
        assert db.scalar(text("SELECT operation_type FROM stock_operation_lines")) == 'return'
    print('Committed API return and all stock/audit/notification facts retained across 0141/0142 PASS', flush=True)

    migration = runpy.run_path(str(Path(__file__).parents[1]/'alembic/versions/20261121_0142_stock_operation_typed_context.py'))
    # Deliberate private-function drift in this owned transaction must abort
    # the entire downgrade, including DDL already executed before the CAS.
    with owner.connect() as db, Operations.context(MigrationContext.configure(db)):
        signature = 'public.rsc_oam_runtime_binding_ready_0044()'
        definition = db.scalar(text('SELECT pg_get_functiondef(CAST(:signature AS regprocedure))'), {'signature':signature})
        body = db.scalar(text('SELECT prosrc FROM pg_proc WHERE oid=CAST(:signature AS regprocedure)'), {'signature':signature})
        assert body and definition.count(body) == 1
        db.execute(text(definition.replace(body, '\n-- synthetic typed-migration drift\n'+body, 1)))
        with pytest.raises(DBAPIError, match='typed_stock_operation_readiness_0142 prerequisite mismatch') as drift:
            migration['downgrade']()
        assert drift.value.orig.sqlstate == 'P0001'
        db.rollback()
    assert snapshot(owner) == before
    with owner.connect() as db:
        assert db.scalar(text('SELECT version_num FROM alembic_version')) == gate.HEAD_REVISION
        assert db.scalar(text("SELECT count(*) FROM pg_trigger WHERE tgname IN ('trg_stock_operation_orders_loss_admission_0142','trg_stock_operation_lines_loss_admission_0142')")) == 2
        assert db.scalar(text("SELECT encode(sha256(convert_to(prosrc,'UTF8')),'hex') FROM pg_proc WHERE oid='public.rsc_oam_runtime_binding_ready_0044()'::regprocedure")) == migration['NEW_READY_HASH']
    for engine in (api, engines['edge_inbox'], engines['star_oam_backup']):
        with engine.connect() as db, Operations.context(MigrationContext.configure(db)):
            with pytest.raises(DBAPIError, match='0142 direct schema owner required'):
                migration['downgrade']()
            db.rollback()
        with engine.connect() as db:
            with pytest.raises(DBAPIError) as denied:
                db.execute(text('SELECT public.rsc_guard_stock_loss_admission_0142()'))
            assert denied.value.orig.sqlstate == '42501'
    for table in ('stock_operation_orders', 'stock_operation_lines'):
        with api.begin() as db:
            with pytest.raises(DBAPIError, match='complete loss posting boundary') as denied:
                with db.begin_nested():
                    db.execute(text(f"INSERT INTO {table}(operation_type) VALUES ('loss_report')"))
            assert denied.value.orig.sqlstate == '23514'
    assert snapshot(owner) == before
    with Session(api) as db:
        actor = load_formal_principal(db, engineer)
        original = db.get(StockOperationOrder, posted.operation_id)
        assert order_result(db, actor=actor, order=original) == posted
        assert submit_return(db, actor=actor, work_order_id=work_order, request=command) == posted
        cancelled = cancel_return(db, actor=actor, operation_id=original.id,
            request=StockReturnCancelIn(operator_person_id=person, reason='Synthetic migration cancellation',
                idempotency_key=uuid4().hex, request_id=uuid4().hex))
        db.commit()
    with Session(api) as db:
        actor = load_formal_principal(db, engineer)
        original = db.get(StockOperationOrder, posted.operation_id)
        assert order_result(db, actor=actor, order=original) == posted
        assert cancellation_result(db, actor=actor, order=original,
            cancellation=db.get(StockOperationCancellation, cancelled.cancellation_id)) == cancelled
    return dict(passed=True, realReturnCommitRoundtrip=True, exactReplay=True, cancellationCommitReadback=True,
        driftRollback=True, migrationOwnerRequired=True, privateFunctionDenied=True, lossAdmissionClosed=True, migrationHead=gate.HEAD_REVISION,
        productionAcceptance=False)
