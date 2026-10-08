#!/usr/bin/env python3
"""Current request-to-personal-inbound HTTP on a newly owned PG16 cluster.

No external DSN is accepted. Identities and external approval evidence are
synthetic; actual application routes, transactions and database roles are used.
"""
import argparse
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
from unittest.mock import patch

CLOUD = Path(__file__).resolve().parents[1]
HEAD = '20261228_0179'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--postgres-bin', required=True)
    parser.add_argument('--tracking', choices=('quantity', 'serial'),
                        help='one CI leg; by default exercise both independent clusters')
    parser.add_argument('--supply-allocation-recovery', action='store_true',
                        help='four committed allocations after supply planning, including unchanged partial status and read-only HTTP recovery')
    parser.add_argument('--partial-release', action='store_true', help='commit a release after partial shipment and inbound')
    parser.add_argument('--rejected-receipt', action='store_true', help='verify rejected receipt history and retained in-transit stock; no inbound or closure')
    parser.add_argument('--rejection-return', '--rejection-return-candidate', dest='rejection_return_candidate', action='store_true', help='verify formal return registration after original rejection; no public HTTP activation')
    parser.add_argument('--rejection-progress', '--rejection-progress-candidate', dest='rejection_progress_candidate', action='store_true', help='verify formal cancellation, re-registration and independent physical return progress')
    parser.add_argument('--rejection-receipt', '--rejection-receipt-candidate', dest='rejection_receipt_candidate', action='store_true', help='verify formal warehouse acceptance after committed physical return; no inventory posting')
    parser.add_argument('--rejection-inbound', action='store_true', help='verify formal 0175 warehouse posting, full runtime admission and retained-history downgrade refusal')
    parser.add_argument('--rejection-inbound-split', action='store_true', help='three items; first warehouse posting split into normal/damaged, second receipt independently posted')
    parser.add_argument('--return-compensation', action='store_true', help='after real split warehouse posting, verify forward original-requester compensation; not formal activation')
    parser.add_argument('--return-compensation-mixed', action='store_true', help='same approved line: one normal personal inbound and one rejected return, compensation and independent closure')
    parser.add_argument('--return-compensation-remaining', action='store_true', help='mixed normal/returned line plus late release, versioned remaining cancellation and independent closure')
    parser.add_argument('--remaining-cancellation', action='store_true',
                        help='after partial release, commit formal remaining cancellation and business closure')
    parser.add_argument('--browser-supply-create', action='store_true',
                        help='real UI creates remaining plan after partial allocation; same request posts one and cancels one')
    parser.add_argument('--browser-supply', action='store_true',
                        help='update and cancel an allocated plan in the real UI, then complete the same fulfillment')
    parser.add_argument('--browser-tail', action='store_true',
                        help='pause on loopback for actual receipt/inbound browser operations; requires --tracking')
    parser.add_argument('--browser-closure', action='store_true',
                        help='pause after actual inbound for browser closure only; requires --tracking')
    parser.add_argument('--browser-cancellation', action='store_true',
                        help='pause after partial release for original-requester cancellation; requires --tracking')
    parser.add_argument('--browser-personal', action='store_true',
                        help='original-requester browser receipt and independent inbound; requires --tracking')
    parser.add_argument('--browser-rejection-warehouse', action='store_true',
                        help='browser performs first source-warehouse acceptance and posting after physical return; requires --tracking')
    parser.add_argument('--browser-timeout', type=int, default=1200,
                        help='browser interaction deadline, 60..3600 seconds; no automatic replay')
    args = parser.parse_args(argv)
    if args.supply_allocation_recovery and any(value for key, value in vars(args).items()
            if key not in {'supply_allocation_recovery', 'postgres_bin', 'tracking', 'browser_timeout'}):
        parser.error('--supply-allocation-recovery is an independent trace')
    if args.browser_rejection_warehouse:
        if args.rejection_receipt_candidate or args.rejection_inbound or args.rejection_inbound_split or args.return_compensation or args.return_compensation_mixed or args.return_compensation_remaining:
            parser.error('browser warehouse requires unaccepted goods, not a preposted gate')
        args.rejection_progress_candidate = True
    if args.return_compensation_remaining:
        args.return_compensation_mixed = True
    if args.return_compensation_mixed:
        args.return_compensation = True
        args.rejection_inbound = True
    if args.return_compensation and not args.return_compensation_mixed:
        args.rejection_inbound_split = True
    if args.rejection_inbound_split:
        args.rejection_inbound = True
    if args.rejection_inbound:
        args.rejection_receipt_candidate = True
    if args.rejection_receipt_candidate:
        args.rejection_progress_candidate = True
    if args.rejection_progress_candidate:
        args.rejection_return_candidate = True
    if args.rejection_return_candidate:
        args.rejected_receipt = True
    if args.browser_cancellation:
        args.remaining_cancellation = True
    if args.remaining_cancellation:
        args.partial_release = True
    if (args.browser_tail or args.browser_closure or args.browser_cancellation or args.browser_personal or args.browser_rejection_warehouse or args.browser_supply or args.browser_supply_create) and not args.tracking:
        parser.error('browser operation requires one explicit --tracking mode')
    if args.partial_release and (args.browser_tail or args.browser_closure or args.browser_personal or args.browser_supply or args.browser_supply_create):
        parser.error('--partial-release is an unattended separate business trace')
    if sum((args.browser_tail, args.browser_closure, args.browser_cancellation, args.browser_personal, args.browser_rejection_warehouse, args.browser_supply, args.browser_supply_create)) > 1:
        parser.error('select one browser operation at a time')
    if args.rejected_receipt and (args.partial_release or args.browser_tail or args.browser_closure or args.browser_cancellation or args.browser_personal or args.browser_supply or args.browser_supply_create):
        parser.error('--rejected-receipt is an unattended independent receipt trace')
    if not 60 <= args.browser_timeout <= 3600:
        parser.error('--browser-timeout must be 60..3600 seconds')
    sys.path[:0] = [str(CLOUD/'backend'), str(CLOUD/'backend/tests')]
    runpy.run_path(str(CLOUD/'backend/tests/conftest.py'))
    from local_pg16_cluster import native_cluster
    from sqlalchemy import text
    import test_postgresql16_release_gate as gate
    from pg16_material_request_http_gate import run
    from pg16_stock_scrap_structure_gate import original_columns, facts
    from run_local_pg16_scrap_business_checks import manifest

    supply_create_browser = None
    supply_browser = None
    browser_tail = None
    browser_sources = None
    if args.browser_tail or args.browser_closure or args.browser_cancellation or args.browser_personal or args.browser_rejection_warehouse or args.browser_supply or args.browser_supply_create:
        from app.main import app
        from pg16_fulfillment_browser import frontend_manifest, serve
        browser_sources = frontend_manifest(CLOUD)
        if args.browser_supply_create:
            from pg16_fulfillment_browser import supply_in_browser
            supply_create_browser = lambda **context: supply_in_browser(app, CLOUD,
                timeout_seconds=args.browser_timeout, create_only=True, **context)
        if args.browser_supply:
            from pg16_fulfillment_browser import supply_in_browser
            supply_browser = lambda **context: supply_in_browser(app, CLOUD,
                timeout_seconds=args.browser_timeout, **context)
        if args.browser_tail or args.browser_personal:
            browser_tail = lambda **context: serve(app, CLOUD, timeout_seconds=args.browser_timeout,
                                                  personal_only=args.browser_personal, **context)
    sources = manifest()
    for tracking in ((args.tracking,) if args.tracking else ('quantity', 'serial')):
        with native_cluster(postgres_bin=args.postgres_bin,
                artifact_root=CLOUD/'artifacts/local-fulfillment-http-pg16') as (directory, engines):
            (directory/'source-manifest.json').write_text(json.dumps(sources, indent=2)+'\n')
            url = engines['star_oam_migrator'].url.render_as_string(hide_password=False)
            environment = dict(os.environ, OAM_ENVIRONMENT='production', OAM_DATABASE_URL=url,
                OAM_DATABASE_SCHEMA_MODE='migrations', OAM_DATABASE_EXPECTED_MIGRATION_ROLE='star_oam_migrator',
                OAM_DATABASE_EXPECTED_RUNTIME_ROLE='star_oam_api', OAM_MIGRATION_CACHE_EXECUTION='1')
            def migrate(label, action, revision, expected_failure=None):
                path = directory/(label+'.log')
                with path.open('wb') as log:
                    result = subprocess.run([sys.executable, '-m', 'alembic', '-c', 'alembic.ini', action, revision],
                        cwd=CLOUD, env=environment, stdout=log, stderr=subprocess.STDOUT, timeout=600)
                if expected_failure:
                    assert result.returncode != 0 and expected_failure in path.read_text(), label
                else:
                    assert result.returncode == 0, label
            migrate('upgrade-current-head', 'upgrade', 'head')
            migrate('empty-closure-downgrade', 'downgrade', '20261217_0168')
            migrate('empty-closure-reupgrade', 'upgrade', HEAD)
            with engines['star_oam_migrator'].connect() as db:
                assert db.scalar(text('SELECT version_num FROM alembic_version')) == HEAD
            with (directory/'edge-provision.log').open('wb') as log:
                subprocess.run([str(Path(args.postgres_bin).resolve()/'psql'), '-X', '-w', '--set=ON_ERROR_STOP=1',
                    '--dbname', url.replace('postgresql+psycopg:', 'postgresql:', 1), '-v', 'edge_role=edge_inbox',
                    '-f', str(CLOUD/'deployment/create_oam_edge_staging.sql')], cwd=CLOUD,
                    env=environment, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=120)
            with patch.object(gate, '_sqlalchemy_url', side_effect=lambda *, role, **kw: engines[role].url), \
                    patch.object(gate, '_role_password', return_value=''):
                _, _, requester, manager, admin, verifier = gate._seed_material_request_approval_world()
            gate._validate_runtime_security(engines['star_oam_api'])
            print(f'{tracking}: {HEAD} complete runtime admission PASS', flush=True)
            def supply_upgrade_check():
                with engines['star_oam_migrator'].connect() as db:
                    before_columns = original_columns(db); before_rows = facts(db, before_columns)
                migrate('old-supply-downgrade-0176', 'downgrade', '20261225_0176')
                migrate('populated-supply-upgrade-0177', 'upgrade', HEAD)
                with engines['star_oam_migrator'].connect() as db:
                    assert facts(db, before_columns) == before_rows
                gate._validate_runtime_security(engines['star_oam_api'])
            result = run(engines, requester=requester, manager=manager, admin=admin, verifier=verifier,
                         directory=directory, tracking=tracking, browser_tail=browser_tail, supply_browser=supply_browser, supply_create_browser=supply_create_browser,
                         partial_release=args.partial_release, rejected_receipt=args.rejected_receipt,
                         rejection_split=args.rejection_inbound_split, mixed_return=args.return_compensation_mixed,
                         unfulfilled_return=args.return_compensation_remaining,
                         supply_allocation_recovery=args.supply_allocation_recovery,
                         supply_upgrade_check=supply_upgrade_check if args.supply_allocation_recovery else None)
            if args.supply_allocation_recovery:
                gate._validate_runtime_security(engines['star_oam_api'])
                with engines['star_oam_migrator'].connect() as db:
                    columns = original_columns(db); retained = facts(db, columns)
                migrate('late-plan-retention', 'downgrade', '20261226_0177',
                        expected_failure='0178 downgrade blocked: late supply creation facts exist')
                with engines['star_oam_migrator'].connect() as db:
                    assert facts(db, columns) == retained
                    assert db.scalar(text('SELECT version_num FROM alembic_version')) == HEAD
                gate._validate_runtime_security(engines['star_oam_api'])
                assert manifest() == sources, 'source drift'
                result.update(migrationHead=HEAD, sourceFiles=len(sources), runtimeAdmission=True,
                              retainedFactsUnchanged=True, latePlanDowngradeDenied=True, productionAcceptance=False)
                (directory/'checks.json').write_text(json.dumps(result, indent=2)+'\n')
            elif len(result['httpCommands']) != (10 if args.return_compensation_remaining else 9 if args.return_compensation_mixed else 7 if args.rejected_receipt else 8 if args.browser_tail or args.partial_release or args.browser_supply or args.browser_supply_create else 7):
                raise AssertionError('unexpected HTTP command count')
            if args.supply_allocation_recovery:
                pass  # Independent trace was verified above.
            elif args.rejected_receipt:
                gate._validate_runtime_security(engines['star_oam_api'])
                with engines['star_oam_migrator'].connect() as db:
                    columns=original_columns(db); retained=facts(db,columns)
                migrate('retained-rejected-shipment-downgrade-denied', 'downgrade', '20261216_0167',
                        expected_failure='0170 downgrade blocked: partial release facts exist' if args.return_compensation_remaining
                        else 'shipment projection facts exist')
                with engines['star_oam_migrator'].connect() as db:
                    assert facts(db,columns) == retained
                    assert db.scalar(text('SELECT version_num FROM alembic_version')) == HEAD
                gate._validate_runtime_security(engines['star_oam_api'])
                assert manifest() == sources, 'source drift'
                result.update(migrationHead=HEAD,sourceFiles=len(sources),runtimeAdmission=True,
                    retainedShipmentDowngradeDenied=True,
                    retentionGuard='0170 partial release' if args.return_compensation_remaining else '0168 shipment projection')
                if args.rejection_return_candidate:
                    from pg16_rejection_return_gate import run as registration_gate
                    result['rejectionReturnCandidate'] = registration_gate(engines,request_id=result['requestId'],directory=directory)
                    gate._validate_runtime_security(engines['star_oam_api'])
                    with engines['star_oam_migrator'].connect() as db:
                        return_columns=original_columns(db); return_facts=facts(db,return_columns)
                    migrate('retained-rejection-return-downgrade-denied','downgrade','20261220_0171',
                        expected_failure='0172 immutable history requires retention:')
                    with engines['star_oam_migrator'].connect() as db:
                        assert facts(db,return_columns)==return_facts
                        assert db.scalar(text('SELECT version_num FROM alembic_version'))==HEAD
                    gate._validate_runtime_security(engines['star_oam_api'])
                    result.update(runtimeAdmission=True,retainedReturnDowngradeDenied=True,
                        scope='formal refusal-return registration')
                    if args.rejection_progress_candidate:
                        from pg16_rejection_progress_gate import run as progress_gate
                        result['rejectionProgressCandidate'] = progress_gate(engines, request_id=result['requestId'], directory=directory)
                        gate._validate_runtime_security(engines['star_oam_api'])
                        with engines['star_oam_migrator'].connect() as db:
                            progress_columns=original_columns(db); progress_facts=facts(db,progress_columns)
                        migrate('retained-rejection-progress-downgrade-denied','downgrade','20261221_0172',
                            expected_failure='0173 immutable history requires retention:')
                        with engines['star_oam_migrator'].connect() as db:
                            assert facts(db,progress_columns)==progress_facts
                            assert db.scalar(text('SELECT version_num FROM alembic_version'))==HEAD
                        gate._validate_runtime_security(engines['star_oam_api'])
                        result.update(runtimeAdmission=True, retainedProgressDowngradeDenied=True,
                            scope='formal 0173 cancellation, re-registration and physical progress')
                        if args.browser_rejection_warehouse:
                            from pg16_rejection_warehouse_browser import run as browser_warehouse
                            result['browserWarehouse'] = browser_warehouse(app, CLOUD, engines=engines, directory=directory,
                                return_id=result['rejectionProgressCandidate']['returnId'], timeout_seconds=args.browser_timeout)
                            gate._validate_runtime_security(engines['star_oam_api'])
                            with engines['star_oam_migrator'].connect() as db:
                                browser_columns = original_columns(db); browser_facts = facts(db, browser_columns)
                            migrate('browser-inbound-retention', 'downgrade', '20261223_0174',
                                expected_failure='0175 immutable history requires retention:')
                            with engines['star_oam_migrator'].connect() as db:
                                assert facts(db, browser_columns) == browser_facts
                                assert db.scalar(text('SELECT version_num FROM alembic_version')) == HEAD
                            gate._validate_runtime_security(engines['star_oam_api'])
                            assert frontend_manifest(CLOUD) == browser_sources, 'browser source drift'
                            result.update(scope='formal source-warehouse browser acceptance and independent inbound',
                                retainedWarehouseInboundDowngradeDenied=True)
                        if args.rejection_receipt_candidate:
                            from pg16_rejection_receipt_gate import run as acceptance_gate
                            result['rejectionReceiptCandidate'] = acceptance_gate(engines,
                                return_id=result['rejectionProgressCandidate']['returnId'], directory=directory, mixed_split=args.rejection_inbound_split)
                            gate._validate_runtime_security(engines['star_oam_api'])
                            with engines['star_oam_migrator'].connect() as db:
                                acceptance_columns=original_columns(db); acceptance_facts=facts(db,acceptance_columns)
                            migrate('retained-rejection-receipt-downgrade-denied','downgrade','20261222_0173',
                                expected_failure='0174 immutable history requires retention:')
                            with engines['star_oam_migrator'].connect() as db:
                                assert facts(db,acceptance_columns)==acceptance_facts
                                assert db.scalar(text('SELECT version_num FROM alembic_version'))==HEAD
                            gate._validate_runtime_security(engines['star_oam_api'])
                            result.update(runtimeAdmission=True, retainedWarehouseReceiptDowngradeDenied=True,
                                scope='formal 0174 warehouse acceptance, runtime admission and retained history')
                            if args.rejection_inbound:
                                from pg16_rejection_inbound_gate import run as inbound_gate
                                result['rejectionInboundCandidate'] = inbound_gate(engines,
                                    receipt_id=result['rejectionReceiptCandidate']['acceptedReceiptId'], directory=directory,mixed_split=args.rejection_inbound_split)
                                gate._validate_runtime_security(engines['star_oam_api'])
                                with engines['star_oam_migrator'].connect() as db:
                                    inbound_columns=original_columns(db); inbound_facts=facts(db,inbound_columns)
                                migrate('retained-rejection-inbound-downgrade-denied','downgrade','20261223_0174',
                                    expected_failure='0175 immutable history requires retention:')
                                with engines['star_oam_migrator'].connect() as db:
                                    assert facts(db,inbound_columns)==inbound_facts
                                    assert db.scalar(text('SELECT version_num FROM alembic_version'))==HEAD
                                gate._validate_runtime_security(engines['star_oam_api'])
                                result.update(runtimeAdmission=True, retainedWarehouseInboundDowngradeDenied=True,
                                    scope='formal 0175 warehouse posting, runtime admission and retained history')
                                if args.return_compensation:
                                    from pg16_return_compensation_gate import run as compensation_gate
                                    result['returnCompensationCandidate'] = compensation_gate(engines,
                                        inbound_id=result['rejectionInboundCandidate']['inboundId'], directory=directory,
                                        admin_id=admin, mixed=args.return_compensation_mixed, unfulfilled=args.return_compensation_remaining)
                                    gate._validate_runtime_security(engines['star_oam_api'])
                                    with engines['star_oam_migrator'].connect() as db:
                                        compensation_columns = original_columns(db)
                                        compensation_facts = facts(db, compensation_columns)
                                    migrate('retained-return-compensation-downgrade-denied', 'downgrade', '20261224_0175',
                                        expected_failure='0176 immutable history requires retention:')
                                    with engines['star_oam_migrator'].connect() as db:
                                        assert facts(db, compensation_columns) == compensation_facts
                                        assert db.scalar(text('SELECT version_num FROM alembic_version')) == HEAD
                                    gate._validate_runtime_security(engines['star_oam_api'])
                                    result.update(runtimeAdmission=True, retainedCompensationDowngradeDenied=True,
                                        scope='formal 0176 compensation, closure, runtime admission and retained history')
                    assert manifest() == sources, 'source drift'
                (directory/'checks.json').write_text(json.dumps(result,indent=2)+'\n')
                print(f'{tracking}: '+('formal 0176 compensation, runtime admission and retained history PASS' if args.return_compensation else 'formal warehouse posting, runtime admission and retained history PASS' if args.rejection_inbound else 'formal warehouse acceptance, runtime admission and retained history PASS' if args.rejection_receipt_candidate else 'formal rejection progress, runtime admission and retained history PASS' if args.rejection_progress_candidate else 'formal return registration, runtime admission and retention PASS'
                    if args.rejection_return_candidate else 'rejected receipt, unchanged in-transit stock and runtime admission PASS'),flush=True)
            elif args.browser_supply_create:
                from pg16_material_request_remaining_cancel_gate import run as cancel_gate
                result['remainingCancellation'] = cancel_gate(engines, request_id=result['requestId'],
                    actor_id_for_close=admin, directory=directory)
                gate._validate_runtime_security(engines['star_oam_api'])
                with engines['star_oam_migrator'].connect() as db:
                    columns = original_columns(db); retained = facts(db, columns)
                migrate('late-create-retention', 'downgrade', '20261226_0177',
                    expected_failure='0178 downgrade blocked: late supply creation facts exist')
                with engines['star_oam_migrator'].connect() as db:
                    assert facts(db, columns) == retained
                    assert db.scalar(text('SELECT version_num FROM alembic_version')) == HEAD
                gate._validate_runtime_security(engines['star_oam_api'])
                assert frontend_manifest(CLOUD) == browser_sources, 'frontend source drift'
                assert manifest() == sources, 'source drift'
                result.update(migrationHead=HEAD, sourceFiles=len(sources), runtimeAdmission=True,
                    lateCreateDowngradeDenied=True, retainedFactsUnchanged=True, businessClosed=True)
                (directory/'checks.json').write_text(json.dumps(result, indent=2)+'\n')
            elif args.partial_release:
                gate._validate_runtime_security(engines['star_oam_api'])
                with engines['star_oam_migrator'].connect() as db:
                    preserved = facts(db, original_columns(db))
                migrate('retained-partial-release-downgrade-denied', 'downgrade', '20261218_0169',
                        expected_failure='0170 downgrade blocked: partial release facts exist')
                with engines['star_oam_migrator'].connect() as db:
                    assert facts(db, original_columns(db)) == preserved
                    assert db.scalar(text('SELECT version_num FROM alembic_version')) == HEAD
                gate._validate_runtime_security(engines['star_oam_api'])
                if args.remaining_cancellation:
                    if args.browser_cancellation:
                        from pg16_fulfillment_browser import cancel_remaining_in_browser
                        result['remainingCancellationBrowser'] = cancel_remaining_in_browser(app, CLOUD,
                            engines=engines, directory=directory, request_id=result['requestId'], admin=admin,
                            timeout_seconds=args.browser_timeout)
                        assert frontend_manifest(CLOUD) == browser_sources, 'frontend drift during browser cancellation'
                    else:
                        from pg16_material_request_remaining_cancel_gate import run as cancel_gate
                        result['remainingCancellation'] = cancel_gate(engines,request_id=result['requestId'],actor_id_for_close=admin,directory=directory)
                    gate._validate_runtime_security(engines['star_oam_api'])
                    with engines['star_oam_migrator'].connect() as db:
                        retained = facts(db, original_columns(db))
                    migrate('retained-cancellation-downgrade-denied', 'downgrade', '20261219_0170',
                            expected_failure='0171 immutable history requires retention:')
                    with engines['star_oam_migrator'].connect() as db:
                        assert facts(db, original_columns(db)) == retained
                        assert db.scalar(text('SELECT version_num FROM alembic_version')) == HEAD
                    gate._validate_runtime_security(engines['star_oam_api'])
                assert manifest() == sources, 'source drift'
                result.update(migrationHead=HEAD,sourceFiles=len(sources),runtimeAdmission=True,
                    predecessorRuntimeAdmission=True,
                    retainedPartialReleaseDowngradeDenied=True,productionAcceptance=False)
                (directory/'checks.json').write_text(json.dumps(result,indent=2)+'\n')
                print(f'{tracking}: ' + ('formal remaining cancellation and closure PASS' if args.remaining_cancellation else 'committed partial release, retained history and runtime admission PASS'),flush=True)
            else:
                if args.browser_tail or args.browser_personal or args.browser_supply or args.browser_supply_create:
                    assert frontend_manifest(CLOUD) == browser_sources, 'frontend drift during native gate'
                with engines['star_oam_migrator'].connect() as db:
                    columns = original_columns(db)
                    before = facts(db, columns)
                migrate('retained-shipment-downgrade-denied', 'downgrade', '20261216_0167',
                        expected_failure='0177 downgrade blocked: late supply command facts exist' if args.browser_supply
                        else 'shipment projection facts exist')
                with engines['star_oam_migrator'].connect() as db:
                    assert facts(db, columns) == before
                gate._validate_runtime_security(engines['star_oam_api'])
                # Empty closure schema may roll back even with fully posted old
                # fulfillment. Reupgrade must retain all prior business rows.
                if not args.browser_supply:
                    migrate('fulfilled-closure-downgrade', 'downgrade', '20261217_0168')
                    migrate('fulfilled-closure-reupgrade', 'upgrade', HEAD)
                with engines['star_oam_migrator'].connect() as db:
                    assert facts(db, columns) == before
                gate._validate_runtime_security(engines['star_oam_api'])
                from pg16_material_request_closure_gate import run as closure_gate
                if args.browser_closure:
                    from uuid import UUID
                    from sqlalchemy import select
                    from sqlalchemy.orm import Session
                    from app.formal_access import load_formal_principal
                    from app.formal_services.material_request_closure import read_closure
                    from app.material_request_closure_schema import closures
                    business_columns = {k: v for k, v in columns.items() if not k.startswith('audit_') and k != 'material_request_closures'}
                    with engines['star_oam_migrator'].connect() as db:
                        business_before = facts(db, business_columns)
                    result['closureBrowser'] = serve(app, CLOUD, api=engines['star_oam_api'], admin=admin,
                        directory=directory, request_id=result['requestId'], closure_only=True,
                        timeout_seconds=args.browser_timeout)
                    with Session(engines['star_oam_api']) as db:
                        db.execute(text('SET TRANSACTION READ ONLY'))
                        state = read_closure(db, actor=load_formal_principal(db, admin), request_id=UUID(result['requestId']))
                        assert state.business_status == 'closed' and state.close_permitted is False
                        assert len(db.scalars(select(closures.c.id).where(closures.c.request_id == UUID(result['requestId']))).all()) == 1
                        result['closureBrowser']['readback'] = state.model_dump(mode='json')
                    with engines['star_oam_migrator'].connect() as db:
                        assert facts(db, business_columns) == business_before
                    assert frontend_manifest(CLOUD) == browser_sources, 'frontend drift during browser closure'
                else:
                    result['closureDatabase'] = closure_gate(engines,
                        request_id=result['requestId'], actor_id=admin, directory=directory)
                gate._validate_runtime_security(engines['star_oam_api'])
                with engines['star_oam_migrator'].connect() as db:
                    closed_facts = facts(db, columns)
                migrate('retained-closure-downgrade-denied', 'downgrade', '20261217_0168',
                        expected_failure='0177 downgrade blocked: late supply command facts exist' if args.browser_supply
                        else 'immutable history requires retention: material_request_closures')
                with engines['star_oam_migrator'].connect() as db:
                    assert facts(db, columns) == closed_facts
                    assert db.scalar(text('SELECT version_num FROM alembic_version')) == HEAD
                gate._validate_runtime_security(engines['star_oam_api'])
                print(f'{tracking}: formal closure, retention and runtime admission PASS', flush=True)
                assert manifest() == sources, 'source drift'
                result.update(migrationHead=HEAD, sourceFiles=len(sources),
                              runtimeAdmission=True, emptyClosureRoundtrip=True,
                              populatedClosureRoundtrip=not args.browser_supply,
                              lateSupplyRetentionBarrier=args.browser_supply,
                              retainedFulfillmentUnchanged=True, retainedClosureDowngradeDenied=True,
                              retainedShipmentDowngradeDenied=not args.browser_supply, productionAcceptance=False)
                (directory/'checks.json').write_text(json.dumps(result, indent=2)+'\n')
        state = json.loads((directory/'cluster-state.json').read_text())
        assert state['status'] == 'stopped' and state['checks'] == 'passed' and state['serverExitCode'] == 0
        print(json.dumps(dict(passed=True, tracking=tracking, migrationHead=HEAD,
            directory=str(directory.relative_to(CLOUD)), sourceFiles=len(sources))), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
