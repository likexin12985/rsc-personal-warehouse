#!/usr/bin/env python3
"""Actual 0157 damaged inbound -> current condition-source proof on owned PG16."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import traceback

from run_local_pg16_scrap_business_checks import manifest

CLOUD = Path(__file__).resolve().parents[1]


def expected_authorization_versions(versions, assignments, existing_grants):
    """Independent expectation for the two additive policies after 0166.

    Existing grants, including explicit denies, are retained. Each migration
    bumps each affected user once, even if several of their roles change.
    """
    policies = (
        [('provincial_manager', 'stock_operation', action) for action in (
            'submit_return_condition', 'supplement_return_condition',
            'withdraw_return_condition', 'execute_return_condition',
            'release_return_condition', 'review_return_condition_regional')]
        + [('admin', 'stock_operation', action) for action in (
            'review_return_condition_headquarters', 'cancel_return_condition_approval')],
        [(role, 'material_request', 'close') for role in ('admin', 'provincial_manager')],
    )
    expected = dict(versions)
    for policy in policies:
        changed_roles = {role for role, resource, action in policy
                         if (role, resource, action) not in existing_grants}
        for user in {user for user, role in assignments if role in changed_roles}:
            expected[user] += 1
    return expected


def predecessor(path):
    source = Path(path).resolve()
    root = source.parent.parent
    receipt = json.loads((root/'receipt.json').read_text())
    raw = (root/'manifest.json').read_bytes()
    entries = json.loads(raw)
    if (source == CLOUD or source.name != 'cloud_oam' or receipt['formalHead'] != '20261206_0157'
            or receipt['sourceFiles'] != len(entries)
            or receipt['manifestSha256'] != hashlib.sha256(raw).hexdigest()):
        raise ValueError('verified preserved 0157 source required')
    for relative, digest in entries.items():
        copied = source.parent/relative
        if not copied.resolve().is_relative_to(source.parent) or hashlib.sha256(copied.read_bytes()).hexdigest() != digest:
            raise ValueError('preserved source drift')
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--postgres-bin', required=True)
    parser.add_argument('--predecessor-source', required=True)
    parser.add_argument('--tracking', choices=('quantity', 'serial'))
    parser.add_argument('--structure-candidate', action='store_true',
        help='also apply/rollback the isolated condition schema over preserved old history; no business activation')
    parser.add_argument('--invariant-candidate', action='store_true',
        help='include candidate source locks and invariant triggers; implies --structure-candidate')
    parser.add_argument('--posting-candidate', action='store_true',
        help='include candidate posting-edge and reverse-parent guards; implies --invariant-candidate')
    parser.add_argument('--identity-candidate', action='store_true',
        help='include canonical command and common operation guards; implies --posting-candidate')
    parser.add_argument('--authority-candidate', action='store_true',
        help='verify new submission admission as API role with temporary grants fully rolled back')
    parser.add_argument('--regional-source-candidate', action='store_true',
        help='verify regional source preparation and unchanged HQ scope; implies --authority-candidate')
    parser.add_argument('--file-candidate', action='store_true',
        help='verify isolated dedicated upload guard and deferred authority; implies --authority-candidate')
    parser.add_argument('--evidence-candidate', action='store_true',
        help='apply/rollback completed event evidence and exclusive purpose guards; implies --identity-candidate')
    parser.add_argument('--submission-candidate', action='store_true',
        help='commit real private submission as API in the owned disposable database; includes all candidate guards')
    parser.add_argument('--receipt-history-candidate', action='store_true',
        help='focused historical receipt forward patch only; no condition submission or seal COMMIT')
    parser.add_argument('--decision-candidate', action='store_true',
        help='also commit non-posting condition reviews and exact recovery; implies --submission-candidate')
    parser.add_argument('--settlement-candidate',action='store_true',
        help='actual execute/release posting and exact recovery; implies --submission-candidate')
    parser.add_argument('--complete-candidate', action='store_true',
        help='shared schema, nine decision closures, actual release and fresh-case execute in one native lifecycle')
    parser.add_argument('--formal', action='store_true',
        help='use actual 0167 Alembic/default grants, preserve old facts and reject retained-history downgrade')
    parser.add_argument('--formal-http', action='store_true',
        help='actual full application HTTP quantity/SN lifecycle on formal default grants; authentication fixture and fake OSS only')
    args = parser.parse_args(argv)
    if args.formal_http:
        if args.formal:
            parser.error('select either --formal or --formal-http')
        args.formal = True
    if args.formal:
        selected = [key for key, value in vars(args).items() if key.endswith('_candidate') and value]
        if selected:
            parser.error('--formal is the complete formal gate; do not combine candidate selectors')
        args.complete_candidate = True
    if args.complete_candidate:
        if args.decision_candidate or args.settlement_candidate:
            parser.error('--complete-candidate includes decisions and settlement; select it alone')
        args.submission_candidate = True
    if args.settlement_candidate:
        if args.decision_candidate: parser.error('select either decision or settlement gate')
        args.submission_candidate=True
    if args.decision_candidate:
        args.submission_candidate = True
    old = Path(args.predecessor_source).resolve()
    old_proof = predecessor(old)
    sys.path[:0] = [str(CLOUD/'backend'), str(CLOUD/'backend/tests'), str(CLOUD.parent)]
    runpy.run_path(str(CLOUD/'backend/tests/conftest.py'))
    from local_pg16_cluster import native_cluster
    from sqlalchemy import event, select, text
    from sqlalchemy.orm import Session
    from uuid import UUID
    from app.formal_access import load_formal_principal
    from app.foundation_models import Permission, Role, RolePermission
    from app.database_security import validate_production_database_security
    from app.formal_services.stock_loss_corrections import return_history, return_condition_source as source
    from app.formal_services import stock_return_inbound_recovery as recovery
    from pg16_return_quality_business import facts_snapshot
    sources = manifest(); directory = None
    try:
        for tracking in ((args.tracking,) if args.tracking else ('quantity', 'serial')):
            with native_cluster(postgres_bin=args.postgres_bin,
                    artifact_root=CLOUD/'artifacts/local-return-condition-pg16') as (directory, engines):
                print(json.dumps(dict(event='owned_cluster_started', tracking=tracking,
                    directory=str(directory.relative_to(CLOUD)))), flush=True)
                (directory/'source-manifest.json').write_text(json.dumps(sources, indent=2)+'\n')
                owner, api = engines['star_oam_migrator'], engines['star_oam_api']
                url = owner.url.render_as_string(hide_password=False)
                environment = dict(os.environ, OAM_ENVIRONMENT='production', OAM_DATABASE_URL=url,
                    OAM_DATABASE_EXPECTED_MIGRATION_ROLE='star_oam_migrator', OAM_DATABASE_EXPECTED_RUNTIME_ROLE='star_oam_api')

                def run(label, command, *, cwd, env):
                    with (directory/(label+'.log')).open('wb') as log:
                        result = subprocess.run(command, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=900)
                    assert result.returncode == 0, label
                    print(label+' PASS', flush=True)

                run('old-upgrade', [sys.executable, '-m', 'alembic', '-c', 'alembic.ini', 'upgrade', 'head'],
                    cwd=old, env=environment)
                run('edge-provision', [str(Path(args.postgres_bin).resolve()/'psql'), '-X', '-w', '--set=ON_ERROR_STOP=1',
                    '--dbname', url.replace('postgresql+psycopg:', 'postgresql:', 1), '-v', 'edge_role=edge_inbox',
                    '-f', str(old/'deployment/create_oam_edge_staging.sql')], cwd=old, env=environment)
                child_env = dict(environment, RSC_OWNED_CONDITION_URLS=json.dumps({role:engine.url.render_as_string(hide_password=False)
                    for role, engine in engines.items()}), RSC_OWNED_CONDITION_TRACKING=tracking,
                    RSC_OWNED_CONDITION_OUTPUT=str(directory/'old-facts.json'))
                run('old-business', [sys.executable, str(CLOUD/'backend/tests/pg16_return_condition_legacy_writer.py')],
                    cwd=old, env=child_env)
                predecessor(old)
                original = json.loads((directory/'old-facts.json').read_text())
                run('current-upgrade', [sys.executable, '-m', 'alembic', '-c', 'alembic.ini', 'upgrade',
                    '20261215_0166' if args.formal else 'head'], cwd=CLOUD, env=environment)
                migration_retention = None
                if args.formal:
                    from pg16_stock_scrap_structure_gate import original_columns, facts
                    with owner.connect() as connection:
                        old_columns = original_columns(connection)
                        old_columns.pop('alembic_version')
                        old_columns['users'].remove('authorization_version')
                        old_rows = facts(connection, old_columns)
                        old_versions = dict(connection.execute(text('SELECT id,authorization_version FROM users')).all())
                        assignments = connection.execute(text('SELECT a.user_id,r.code FROM role_assignments a '
                            'JOIN roles r ON r.id=a.role_id')).all()
                        grants = set(connection.execute(text('SELECT r.code,p.resource,p.action FROM role_permissions g '
                            'JOIN roles r ON r.id=g.role_id JOIN permissions p ON p.id=g.permission_id '
                            "WHERE p.field_code=''" )).all())
                        expected_versions = expected_authorization_versions(old_versions, assignments, grants)
                    run('formal-condition-upgrade', [sys.executable, '-m', 'alembic', '-c', 'alembic.ini',
                        'upgrade', 'head'], cwd=CLOUD, env=environment)
                    with owner.connect() as connection:
                        assert connection.scalar(text('SELECT version_num FROM alembic_version')) == '20261228_0179'
                        upgraded_rows = facts(connection, old_columns)
                        for table, rows in old_rows.items():
                            if table in ('permissions', 'role_permissions'):
                                assert set(rows) <= set(upgraded_rows[table]), table
                            else:
                                assert rows == upgraded_rows[table], 'formal upgrade changed old facts: '+table
                        new_versions = dict(connection.execute(text('SELECT id,authorization_version FROM users')).all())
                        assert set(old_versions) == set(new_versions)
                        assert new_versions == expected_versions, 'authorization version changes differ from 0167/0169 policies'
                    migration_retention = dict(oldTableCount=len(old_rows),
                        oldRowCount=sum(map(len,old_rows.values())), immutableOldColumnsUnchanged=True,
                        priorPermissionRowsRetained=True, authorizationVersionsMatchAdditivePolicies=True)
                    print('formal condition upgrade preserves old facts PASS', flush=True)
                validate_production_database_security(api, expected_runtime_role='star_oam_api',
                    expected_migration_role='star_oam_migrator')
                # The current migration already supplies these read grants.
                # A missing/denied grant must fail; do not repair the fixture.
                with Session(owner) as db:
                    for resource in ('stock_operation', 'inventory'):
                        grant = db.scalars(select(RolePermission)
                            .join(Role, Role.id == RolePermission.role_id)
                            .join(Permission, Permission.id == RolePermission.permission_id)
                            .where(Role.code == 'admin', Role.status == 'active', Role.is_external.is_(False),
                                Permission.resource == resource, Permission.action == 'read',
                                Permission.field_code == '')).one_or_none()
                        assert grant is not None and grant.effect == 'allow', 'formal condition-source read grant missing or denied'
                    db.rollback()
                before = facts_snapshot(owner)
                with Session(api) as db:
                    db.execute(text('SET TRANSACTION READ ONLY'))
                    actor = load_formal_principal(db, original['administratorUserId'])
                    history = return_history.read(db, actor=actor, root_disposition_id=UUID(original['rootDispositionId']))
                    assert len(history.classification_exceptions) == 1
                    issue = history.classification_exceptions[0]
                    selection = source.ConditionSourceSelection(root_disposition_id=history.root_disposition_id,
                        inbound_line_id=issue.inbound_line_id, expected_history_fingerprint=history.evidence_fingerprint)
                    db.rollback()
                # The complete opening proof intentionally uses row locks. A
                # short ordinary transaction must be non-mutating, not falsely
                # described as a PostgreSQL READ ONLY transaction.
                with Session(api) as db:
                    actor = load_formal_principal(db, original['administratorUserId'])
                    statements = []
                    connection = db.connection()
                    def capture(_c, _cu, statement, _p, _ctx, _many):
                        statements.append(statement.lstrip().split()[0].upper())
                    event.listen(connection, 'before_cursor_execute', capture)
                    try:
                        value = source.inspect_source(db, actor=actor, selection=selection).document
                    finally:
                        event.remove(connection, 'before_cursor_execute', capture)
                    assert value['historical_damaged_quantity'] == original['damagedQuantity']
                    assert value['source_status'] == 'recorded_stock_retained'
                    assert value['current_projection_verified'] and not value['posting_allowed']
                    assert statements and set(statements) == {'SELECT'}, set(statements)
                    assert not db.new and not db.dirty and not db.deleted
                    db.rollback()
                with Session(api) as db:
                    db.execute(text('SET TRANSACTION READ ONLY'))
                    receiver = load_formal_principal(db, original['receiverUserId'])
                    from app.stock_operation_models import StockOperationReturnInbound
                    fact = db.get(StockOperationReturnInbound, UUID(original['inboundId']))
                    recovered = recovery.lookup_return_inbound_request(db, actor=receiver,
                        receipt_id=fact.receipt_id, request_id=original['inboundRequestId'])
                    assert recovered['request_hash'] == original['inboundRequestHash']
                    db.rollback()
                assert facts_snapshot(owner) == before
                authority = None
                if args.authority_candidate or args.regional_source_candidate or args.file_candidate:
                    from pg16_return_condition_authority_gate import run as check_authority
                    authority = check_authority(owner, original=original, source=value, directory=directory,
                        regional_source=args.regional_source_candidate, file_candidate=args.file_candidate)
                    assert facts_snapshot(owner) == before
                    print('condition submission authority and rollback PASS', flush=True)
                structure = None
                if args.structure_candidate or args.invariant_candidate or args.posting_candidate or args.identity_candidate or args.evidence_candidate:
                    from pg16_return_condition_structure_gate import run as check_structure
                    structure = check_structure(owner, original=original, artifact_directory=directory,
                        invariant_candidate=args.invariant_candidate, posting_candidate=args.posting_candidate,
                        identity_candidate=args.identity_candidate, evidence_candidate=args.evidence_candidate)
                    validate_production_database_security(api, expected_runtime_role='star_oam_api',
                        expected_migration_role='star_oam_migrator')
                    assert facts_snapshot(owner) == before
                    print('condition structural candidate and rollback PASS', flush=True)
                submission = None
                decision_result = None
                settlement_result = None
                http_result = None
                if args.formal_http:
                    from pg16_return_condition_http_gate import run as check_http
                    http_result = check_http(owner, api, original=original, source=value, directory=directory)
                    (directory/'condition-http-checks.json').write_text(json.dumps(http_result, indent=2)+'\n')
                if not args.formal_http and (args.submission_candidate or args.receipt_history_candidate):
                    from pg16_return_condition_submission_gate import run as check_submission
                    submission = check_submission(owner, api, original=original, source=value, directory=directory,
                        receipt_history_only=args.receipt_history_candidate and not args.submission_candidate,
                        complete_schema=args.complete_candidate, formal=args.formal)
                    if args.complete_candidate:
                        from pg16_return_condition_decision_seals_business import run as check_all_decisions
                        from pg16_return_condition_settlement_gate import run as check_settlement
                        def settle_after_cancelled(cancelled):
                            return check_settlement(owner, api, original=original, source=value,
                                directory=directory, submitted=cancelled, start_cancelled=True, seal_settlements=True, formal=args.formal)
                        decision_result = check_all_decisions(owner, api, original=original,
                            source=value, directory=directory, submitted=submission['result'],
                            schema_preinstalled=True, settlement_check=settle_after_cancelled, formal=args.formal)
                        settlement_result = decision_result['businessSettlement']
                        (directory/'condition-decision-checks.json').write_text(json.dumps(decision_result, indent=2)+'\n')
                        (directory/'condition-settlement-checks.json').write_text(json.dumps(settlement_result, indent=2)+'\n')
                    if args.settlement_candidate:
                        from pg16_return_condition_settlement_gate import run as check_settlement
                        settlement_result=check_settlement(owner,api,original=original,source=value,
                            directory=directory,submitted=submission['result'])
                        (directory/'condition-settlement-checks.json').write_text(json.dumps(settlement_result,indent=2)+'\n')
                    if args.decision_candidate:
                        from pg16_return_condition_decisions_gate import run as check_decisions
                        decision_result = check_decisions(owner, api, original=original,
                            source=value, directory=directory, submitted=submission['result'])
                        (directory/'condition-decision-checks.json').write_text(
                            json.dumps(decision_result, indent=2)+'\n')
                    print(('condition actual business commit and rollback' if args.submission_candidate
                        else 'condition historical receipt focused check')+' PASS', flush=True)
                retained_downgrade = None
                if args.formal:
                    with owner.connect() as connection:
                        retained_columns = original_columns(connection)
                        retained_rows = facts(connection, retained_columns)
                        assert retained_rows['stock_condition_cases'] and retained_rows['stock_condition_decision_seals']
                    log_path = directory/'retained-downgrade.log'
                    with log_path.open('wb') as log:
                        refused = subprocess.run([sys.executable, '-m', 'alembic', '-c', 'alembic.ini',
                            'downgrade', '20261215_0166'], cwd=CLOUD, env=environment,
                            stdout=log, stderr=subprocess.STDOUT, timeout=900)
                    assert refused.returncode != 0 and '0167 immutable history requires retention:' in log_path.read_text()
                    with owner.connect() as connection:
                        assert facts(connection, retained_columns) == retained_rows
                        assert connection.scalar(text('SELECT version_num FROM alembic_version')) == '20261228_0179'
                    validate_production_database_security(api, expected_runtime_role='star_oam_api',
                        expected_migration_role='star_oam_migrator')
                    retained_downgrade = dict(refused=True, factsUnchanged=True,
                        exactHead='20261228_0179', apiStartupAccepted=True)
                    print('formal retained business downgrade refused without changes PASS', flush=True)
                assert sources == manifest()
                result = dict(passed=True, tracking=tracking, actualOldApplication=old_proof,
                    currentSource=value, sourceSelectOnlyOrdinaryTransaction=True,
                    historicalAndOriginalRequestSqlReadOnly=True,
                    originalInboundRequestRetained=True, readPhaseFactsUnchanged=True,
                    sourceDrift=[], formalMigration=args.formal, formalDefaultPermissions=args.formal,
                    migrationRetention=migration_retention, retainedDowngrade=retained_downgrade,
                    structureCandidate=structure, submissionAuthority=authority,
                    businessHttp=http_result, businessSubmission=submission, businessDecisions=decision_result, businessSettlement=settlement_result, productionAcceptance=False)
                (directory/'checks.json').write_text(json.dumps(result, indent=2)+'\n')
            state = json.loads((directory/'cluster-state.json').read_text())
            assert (state['status'], state['checks'], state['serverExitCode']) == ('stopped', 'passed', 0)
            print(json.dumps(dict(directory=str(directory.relative_to(CLOUD)), passed=True, tracking=tracking)), flush=True)
        return 0
    except BaseException as error:
        failure = dict(passed=False, errorType=type(error).__name__, message=str(error)[:800],
            frames=[dict(file=f.filename, line=f.lineno, function=f.name) for f in traceback.extract_tb(error.__traceback__)])
        if directory is not None:
            (directory/'failure.json').write_text(json.dumps(failure, indent=2)+'\n')
        print(json.dumps(failure), flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
