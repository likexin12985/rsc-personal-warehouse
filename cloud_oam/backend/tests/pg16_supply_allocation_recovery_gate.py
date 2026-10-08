"""Committed HTTP allocations and supply recovery in the caller-owned PG16."""
from uuid import UUID
from decimal import Decimal
from unittest.mock import patch
from contextlib import nullcontext

from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from sqlalchemy.exc import DBAPIError

from app.demand_models import MaterialRequest
from app.foundation_models import AuditEvent, StateTransitionEvent
from app.inventory_models import StockAllocation, StockBalance
from app.formal_access import load_formal_principal
from app.formal_services.material_request_allocation import allocation_command_status


def run(engines, *, request_id, line_id, source_id, serial_ids, admin, allocator,
        requester, token, post, upgrade_check=None):
    from dataclasses import asdict
    from app.formal_services.material_request_supply_capacity import planning_capacity
    api = engines['star_oam_api']
    with Session(api) as db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        initial_capacity = planning_capacity(db, actor=load_formal_principal(db, admin), request_id=request_id)
        assert initial_capacity.lines[0].quantities.new_plan_qty == Decimal(4)
    with Session(api) as db:
        version = db.get(MaterialRequest, request_id).version
    plan = post('supply-tasks', dict(expected_request_version=version,
        request_line_id=str(line_id), supply_type='headquarters_replenishment',
        expected_qty='1.000', note='合成补货计划，不表示实际到货'), 'supply', admin)
    from app.main import app
    from app.database import get_db
    from app.dependencies import get_formal_principal
    from app.config import get_settings
    from pg16_stock_scrap_structure_gate import original_columns, facts
    from app.formal_services import material_request_supply as supply
    from test_material_request_draft_service import SECRET

    def lookup(identifier, expected_status=200, trace=None, detail=False, capacity=False):
        def sessions():
            with Session(api) as db:
                db.execute(text('SET TRANSACTION READ ONLY'))
                yield db
        def principal():
            with Session(api) as db:
                return load_formal_principal(db, identifier)
        # A disabled write switch must not block a historical read.
        settings = get_settings().model_copy(update={'material_request_writes_enabled': False})
        with patch.object(app, 'dependency_overrides', {**app.dependency_overrides,
                get_db: sessions, get_formal_principal: principal, get_settings: lambda: settings}), TestClient(app) as client:
            response = client.get('/api/v1/material-requests/'+str(request_id)+'/supply-planning-capacity' if capacity
                else '/api/v1/material-requests/'+str(request_id) if detail
                else '/api/v1/material-request-supply-command-status',
                params={} if detail or capacity else {'trace_request_id': trace or token+'-supply'})
        assert response.status_code == expected_status, response.text
        if expected_status == 200:
            assert 'no-store' in response.headers['cache-control']
            return response.json()

    original = lookup(admin)
    assert original['lookup_status'] == 'confirmed'
    assert original['command']['supply_task_id'] == plan['supply_task_id']
    latest_plan = plan
    plan_commands = []
    remaining_plan = None
    allocations = []
    observed = []
    denied = []
    for index in range(4):
        with Session(api) as db:
            demand = db.get(MaterialRequest, request_id)
            balance = db.get(StockBalance, source_id)
            body = dict(expected_request_version=demand.version, request_line_id=str(line_id),
                source_stock_account_id=str(source_id), allocated_qty='1.000',
                source_balance_version=balance.version, source_ledger_cursor=balance.ledger_cursor,
                serial_ids=[str(serial_ids[index])] if serial_ids else [])
        result = post('allocations', body, f'split-allocation-{index}', allocator)
        allocations.append(UUID(result['allocation_id']))
        expected = 'allocated' if index == 3 else 'partially_allocated'
        with Session(api) as db:
            db.execute(text('SET TRANSACTION READ ONLY'))
            demand = db.get(MaterialRequest, request_id)
            assert demand.allocation_status == expected
            assert db.get(StockBalance, source_id).quantity == Decimal(4)
            audit = db.scalars(select(AuditEvent).where(
                AuditEvent.action == 'material_request_allocation_created',
                AuditEvent.aggregate_id == result['allocation_id'])).one()
            assert audit.after_jsonb['allocated_qty'] == '1.000'
            events = db.scalars(select(StateTransitionEvent).where(
                StateTransitionEvent.aggregate_type == 'material_request',
                StateTransitionEvent.aggregate_id == str(request_id),
                StateTransitionEvent.reason == 'material_request_allocation_created')).all()
            assert len(events) == (2 if index == 3 else 1)
            recovered = allocation_command_status(db, actor=load_formal_principal(db, allocator),
                trace_request_id=token+f'-split-allocation-{index}')
            assert recovered.allocation_id == UUID(result['allocation_id'])
        if index == 0 and upgrade_check is not None:
            upgrade_check()
        if index == 0:
            # Deliberately forge new command rows before their first INSERT.
            # The database must reject them even with internally consistent
            # hashes; no trigger/role bypass or retained-history mutation.
            with engines['star_oam_migrator'].connect() as db:
                columns = original_columns(db); before = facts(db, columns)
            for kind in ('old_allocation_frame', 'future_allocation_frame', 'missing_audit', 'wrong_result_hash'):
                factory = supply._command_fact_from_result
                def forged(**kwargs):
                    command = factory(**kwargs)
                    if kind == 'wrong_result_hash':
                        command.result_hash = '0' * 64
                    elif kind != 'missing_audit':
                        command.result_jsonb['state_axes']['allocation_status'] = (
                            'not_allocated' if kind == 'old_allocation_frame' else 'allocated')
                        command.result_hash = supply._canonical_hash(command.result_jsonb)
                    return command
                with Session(api) as db, patch.object(supply, '_command_fact_from_result', side_effect=forged), (
                    patch.object(supply, '_append_supply_audit', return_value=None)
                    if kind == 'missing_audit' else nullcontext()
                ):
                    try:
                        supply.update_supply_task(db, actor=load_formal_principal(db, admin),
                            material_request_id=request_id, supply_task_id=UUID(plan['supply_task_id']),
                            expected_request_version=db.get(MaterialRequest, request_id).version,
                            expected_task_version=plan['task_version'],
                            update=supply.SupplyTaskUpdateInput(status='awaiting_supply', reference_no=None,
                                expected_date=None, comment='合成数据库拒绝验证'),
                            idempotency_key=token+'-'+kind, idempotency_hmac_secret=SECRET,
                            trace_request_id=token+'-'+kind)
                        db.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
                        db.commit()
                    except DBAPIError as exc:
                        assert exc.orig.sqlstate == 'P0001' and 'formal material request supply projection is invalid' in str(exc.orig), (kind, str(exc.orig))
                        db.rollback()
                        denied.append(dict(case=kind, sqlstate=exc.orig.sqlstate))
                    else:
                        raise AssertionError('unproved late supply command committed: '+kind)
                with engines['star_oam_migrator'].connect() as db:
                    assert facts(db, columns) == before, kind+' changed facts'
        if index in (0, 2, 3):
            with Session(api) as db:
                version = db.get(MaterialRequest, request_id).version
            label = f'late-plan-{index}'
            latest_plan = post('supply-tasks/'+plan['supply_task_id'], dict(
                expected_request_version=version, expected_task_version=latest_plan['task_version'],
                status='cancelled' if index == 3 else 'awaiting_supply',
                reference_no=None, expected_date=None,
                comment='已有货源，撤销预计计划' if index == 3 else '分配后核对预计计划'),
                label, admin, expected_status=200)
            restored = lookup(admin, trace=token+'-'+label)
            assert restored['command']['task_status'] == latest_plan['task_status']
            plan_commands.append((token+'-'+label, restored))
        if index == 0:
            # Prove the database independently rejects excess new plans even
            # if the application capacity result is corrupted before INSERT.
            from dataclasses import replace
            from app.formal_services import material_request_supply_capacity as capacities
            original_reader = capacities.planning_capacity
            def inflated(*args, **kwargs):
                actual = original_reader(*args, **kwargs)
                return replace(actual, lines=tuple(replace(row, quantities=replace(
                    row.quantities, new_plan_qty=Decimal(100))) for row in actual.lines))
            with engines['star_oam_migrator'].connect() as db:
                columns = original_columns(db); before = facts(db, columns)
            with Session(api) as db, patch.object(capacities, 'planning_capacity', side_effect=inflated):
                try:
                    supply.create_supply_task(db, actor=load_formal_principal(db, admin),
                        material_request_id=request_id, expected_request_version=db.get(MaterialRequest, request_id).version,
                        plan=supply.SupplyTaskCreateInput(request_line_id=line_id,
                            supply_type='headquarters_replenishment', reference_no=None, expected_date=None, expected_qty=Decimal('2.001')),
                        idempotency_key=token+'-over-capacity', idempotency_hmac_secret=SECRET,
                        trace_request_id=token+'-over-capacity')
                    db.commit()
                except supply.MaterialRequestSupplyError as exc:
                    assert exc.http_status_code == 503, exc.code
                    db.rollback()
                    denied.append(dict(case='insert_exceeds_unallocated_unplanned_capacity', boundary='database'))
                else:
                    raise AssertionError('database accepted an over-capacity new plan')
            with engines['star_oam_migrator'].connect() as db:
                assert facts(db, columns) == before
            with Session(api) as db:
                version = db.get(MaterialRequest, request_id).version
            remaining_plan = post('supply-tasks', dict(expected_request_version=version,
                request_line_id=str(line_id), supply_type='headquarters_replenishment',
                expected_qty='1.000', note='仅补尚未分配且未被旧计划覆盖部分'), 'remaining-plan', admin)
            restored = lookup(admin, trace=token+'-remaining-plan')
            assert restored['command']['supply_task_id'] == remaining_plan['supply_task_id']
            plan_commands.append((token+'-remaining-plan', restored))
        if index == 3:
            with Session(api) as db:
                version = db.get(MaterialRequest, request_id).version
            post('supply-tasks/'+remaining_plan['supply_task_id'], dict(
                expected_request_version=version, expected_task_version=remaining_plan['task_version'],
                status='cancelled', reference_no=None, expected_date=None,
                comment='已全部分配，取消剩余预计计划'), 'remaining-plan-cancel', admin, expected_status=200)
        with engines['star_oam_migrator'].connect() as db:
            columns = original_columns(db); before = facts(db, columns)
        with Session(api) as db:
            db.execute(text('SET TRANSACTION READ ONLY'))
            capacity = planning_capacity(db, actor=load_formal_principal(db, admin), request_id=request_id)
            amounts = capacity.lines[0].quantities
            assert amounts.allocated_qty == Decimal(index + 1)
            assert amounts.active_planned_qty == Decimal(0 if index == 3 else 2)
            assert amounts.new_plan_qty == Decimal((1, 0, 0, 0)[index])
        assert lookup(admin) == original
        detail = lookup(admin, detail=True)
        assert ('create_supply_task' in detail['allowed_actions']) == (amounts.new_plan_qty > 0)
        public_capacity = lookup(admin, capacity=True)
        assert public_capacity['request_version'] == detail['request_version']
        assert public_capacity['lines'] == [dict(request_line_id=str(line_id),
            **{key: format(value, '.3f') for key, value in asdict(amounts).items()})]
        lookup(requester, expected_status=403, capacity=True)
        task = next(row for row in detail['supply_tasks'] if row['id'] == plan['supply_task_id'])
        assert task['status'] == latest_plan['task_status']
        assert task['allowed_actions'] == ([] if index == 3 else ['update_supply_task', 'cancel_supply_task'])
        for trace, response in plan_commands:
            assert lookup(admin, trace=trace) == response
        assert lookup(allocator)['lookup_status'] == 'not_observed'
        lookup(requester, expected_status=403)
        with engines['star_oam_migrator'].connect() as db:
            assert facts(db, columns) == before, 'recovery changed persisted facts'
        observed.append(dict(allocation=index+1, state=expected, recoveryHttp=200,
                             transitionCount=len(events), independentAudit=True,
                             planningCapacity={k: str(v) for k, v in asdict(amounts).items()}))
    with Session(api) as db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        rows = db.scalars(select(StockAllocation).where(StockAllocation.request_id == request_id)).all()
        assert len(rows) == 4 and sum(row.allocated_qty for row in rows) == Decimal(4)
    return dict(steps=observed, currentStockUnchanged=True, readOnlyRecovery=True,
                differentAllocator=True, requesterDenied=True, writeSwitchOffRecovery=True,
                historicalResultUnchanged=True, newSupplyAfterAllocationEnabled=True,
                latePlanUpdates=True, latePlanCancelled=True, populatedUpgrade=upgrade_check is not None,
                databaseCounterexamples=denied, actualDetailActions=True,
                verifiedPreReservationCapacity=True)
