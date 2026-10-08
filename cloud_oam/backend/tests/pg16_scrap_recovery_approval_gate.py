"""Real PG16 deferred approval proof; external stock parents remain stubs.

Tests real non-superuser API INSERT/COMMIT and concurrent forged requests. This
is not the full 0164 upgrade, stock/event/SN proof or a production permission.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
from threading import Barrier
from uuid import UUID, uuid4

from sqlalchemy import Column, BigInteger, Table, select, text
from sqlalchemy.exc import DBAPIError
from app.stock_scrap_persistence_schema import build_schema
from app.stock_scrap_recovery_schemas import ScrapRecoveryApply, ScrapRecoveryRegionalReview, ScrapRecoveryHeadquartersReview
from app.formal_services.stock_scrap.recovery_facts import intent
from stock_scrap_relational_fixture import fixture_schema, scenario, insert_scrap
from pg16_stock_scrap_bindings_gate import snapshot
from scrap_recovery_evidence_fixture import extend, seed_file, seed_head, insert_events, EVENT_TABLES

CLOUD = Path(__file__).resolve().parents[2]
SQL_PATH = CLOUD / 'backend/alembic/stock_scrap_0165/recovery_approval.sql'
NAMES = dict(apply='stock_scrap_recovery_requests', regional='stock_scrap_recovery_regional_reviews',
    headquarters='stock_scrap_recovery_headquarters_reviews')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def install(owner, api, *, schema_transform=None):
    metadata = fixture_schema()
    full, _, _ = build_schema()
    for name, columns in {
        'stock_loss_dispositions': ('operation_id', 'request_hash'),
        'stock_loss_correction_executions': ('request_hash',),
        'stock_operation_orders': ('requester_id',),
    }.items():
        for name_ in columns:
            c = full.tables[name].c[name_]
            metadata.tables[name].append_column(Column(name_, c.type, nullable=c.nullable))
    Table('inventory_ledger_heads', metadata, Column('id', BigInteger, primary_key=True))
    extend(metadata, full)
    if schema_transform is not None:
        metadata = schema_transform(metadata, full)
    metadata.create_all(owner)
    historical = (CLOUD / 'backend/alembic/versions/20260831_0026_opening_control_reconciliation.py').read_text()
    canonical = re.search(r'CREATE FUNCTION public\.\{PG_CANONICAL_JSON_FUNCTION\}.*?\n\$\$', historical, re.S).group()
    canonical = canonical.replace('{PG_CANONICAL_JSON_FUNCTION}', 'rsc_canonical_reconciliation_json_0026').replace('{{', '{').replace('}}', '}')
    with owner.begin() as db:
        db.execute(text(canonical))
        db.execute(text('INSERT INTO inventory_ledger_heads VALUES (1)'))
        assert db.scalar(text('SELECT current_user')) == 'star_oam_migrator'
        assert 160000 <= int(db.scalar(text('SHOW server_version_num'))) < 170000
        assert not db.scalar(text('SELECT rolsuper FROM pg_roles WHERE rolname=current_user'))
        seed_head(db, metadata)
        catalog_0159 = json.loads((CLOUD / 'backend/alembic/stock_loss_corrections_0159/frozen-catalog.json').read_text())
        audit_function = next(f for f in catalog_0159['newFunctions'] if f['proname'] == 'rsc_loss_inventory_audit_members_0159')
        db.execute(text(audit_function['definition']))
        db.execute(text((SQL_PATH.parent / 'recovery_evidence.sql').read_text()))
        db.execute(text(SQL_PATH.read_text()))
        catalog = db.execute(text("SELECT c.relname,t.tgname,t.tgdeferrable,t.tginitdeferred,t.tgenabled FROM pg_trigger t "
            "JOIN pg_class c ON c.oid=t.tgrelid WHERE t.tgname LIKE 'trg_%scrap%0165' ORDER BY 1,2")).all()
        assert len(catalog) == 26
        assert all(r.tgenabled == 'A' for r in catalog)
        assert sum(bool(r.tgdeferrable and r.tginitdeferred) for r in catalog) == 10
        functions = db.execute(text("SELECT p.proname,p.prosecdef,p.proconfig,r.rolname FROM pg_proc p "
            "JOIN pg_roles r ON r.oid=p.proowner WHERE p.proname IN "
            "('rsc_check_scrap_recovery_command_0165','rsc_check_scrap_recovery_approvals_0165',"
            "'rsc_fence_scrap_recovery_approvals_0165','rsc_scrap_facts_append_only_0165',"
            "'rsc_check_scrap_recovery_files_0165','rsc_check_scrap_recovery_events_0165','rsc_fence_scrap_recovery_events_0165')")).all()
        assert len(functions) == 7
        for f in functions:
            assert f.rolname == 'star_oam_migrator' and f.proconfig == ['search_path=pg_catalog, public']
            assert f.prosecdef == (f.proname != 'rsc_scrap_facts_append_only_0165')
            for role in ('star_oam_api', 'star_oam_projector', 'star_oam_edge', 'edge_inbox'):
                assert not db.scalar(text("SELECT has_function_privilege(:role,p.oid,'EXECUTE') "
                    'FROM pg_proc p WHERE p.proname=:name'), dict(role=role,name=f.proname))
        # Deliberate, test-only insertion capability. SQL proof functions remain
        # private; no UPDATE/DELETE/TRUNCATE or stock posting grants are given.
        for name in (*NAMES.values(), 'stock_scrap_recovery_files', *EVENT_TABLES):
            db.execute(text('GRANT SELECT,INSERT ON public.' + name + ' TO star_oam_api'))
        db.execute(text('GRANT SELECT ON public.files,public.inventory_ledger_heads TO star_oam_api'))
        db.execute(text('GRANT UPDATE ON public.audit_chain_heads,public.inventory_ledger_heads TO star_oam_api'))
    return metadata


def base(owner, metadata, kind):
    data = scenario(fixture_schema(), kind)
    now = datetime.now(timezone.utc) - timedelta(hours=1)
    data['line']['created_at'] = now
    source_hash = digest(dict(source=str(data['line']['id'])))
    user, person = data['ids']['users'], data['ids']['people']
    for name, row in data['parents']:
        if name == 'stock_operation_orders':
            row['requester_id'] = person
        if name in ('stock_loss_dispositions', 'stock_loss_correction_executions'):
            row['request_hash'] = source_hash
        if name == 'stock_loss_dispositions':
            row['operation_id'] = data['ids']['stock_operation_orders']
    with owner.begin() as db:
        data['parents'] = [(name, row) for name, row in data['parents'] if name != 'files']
        insert_scrap(db, metadata, data)
        seed_file(db, metadata, identifier=data['ids']['files'], user=user, person=person, at=now)
        actors = [(user, person)]
        for _ in range(3):
            u, p = str(uuid4()), uuid4()
            db.execute(metadata.tables['users'].insert(), dict(id=u))
            db.execute(metadata.tables['people'].insert(), dict(id=p))
            actors.append((u, p))
    return dict(data=data, time=now, actors=actors, source=dict(scrap_line_id=str(data['line']['id']),
        expected_scrap_request_hash=source_hash))


def row_for(context, stage, *, application=None, regional=None, decision=None, offset=1, actor=None):
    common = dict(source=context['source'], request_id=uuid4().hex, idempotency_key=uuid4().hex,
        reason='核验准确原报废；实物恢复另行执行')
    if stage == 'apply':
        request = ScrapRecoveryApply(**common, action='apply_scrap_recovery', evidence_file_ids=(context['data']['ids']['files'],))
    else:
        common.update(recovery_request_id=application['id'], expected_request_hash=application['request_hash'])
        if stage == 'regional':
            request = ScrapRecoveryRegionalReview(**common, action='review_scrap_recovery_region', decision=decision or 'verified')
        else:
            request = ScrapRecoveryHeadquartersReview(**common, action='review_scrap_recovery_headquarters',
                decision=decision or 'approve', regional_review_id=regional['id'], expected_regional_hash=regional['request_hash'])
    body = intent(request)
    u, p = context['actors'][{'apply': 0, 'regional': 1, 'headquarters': 2}[stage] if actor is None else actor]
    row = dict(id=uuid4(), created_at=context['time'] + timedelta(seconds=offset), actor_user_id=u,
        actor_person_id=p, authorization_version=1, request_id=request.request_id,
        idempotency_key_hash=digest(request.idempotency_key), request_hash=digest(body), reason=request.reason,
        command_jsonb=body, scrap_line_id=context['data']['line']['id'])
    if stage == 'apply':
        row['expected_scrap_request_hash'] = context['source']['expected_scrap_request_hash']
    else:
        row.update(recovery_request_id=application['id'], decision=request.decision)
        if stage == 'regional':
            row['expected_request_hash'] = application['request_hash']
        else:
            row.update(regional_review_id=regional['id'], regional_decision='verified', expected_regional_hash=regional['request_hash'])
    return row


def insert(db, metadata, stage, row, *, evidence=True, events=True, omit=None, mutate=None):
    db.execute(text('SELECT id FROM inventory_ledger_heads FOR UPDATE')).all()
    db.execute(metadata.tables[NAMES[stage]].insert(), row)
    if stage == 'apply' and evidence:
        db.execute(metadata.tables['stock_scrap_recovery_files'].insert(), [dict(recovery_request_id=row['id'],
            file_id=identifier, metadata_sha256=digest(db.scalar(select(metadata.tables['files'].c.metadata_jsonb).where(metadata.tables['files'].c.id==identifier))), created_at=row['created_at'])
            for identifier in (UUID(i) for i in row['command_jsonb']['evidence_file_ids'])])
    if events: insert_events(db, metadata, stage, row, omit=omit, mutate=mutate)


def run(engines):
    owner, api = engines['star_oam_migrator'], engines['star_oam_api']
    metadata = install(owner, api)
    rejected, successful, races = [], [], []

    def reject(name, action, *, role=api, commit=True, state='23514', message=None):
        before = snapshot(owner, metadata)
        inserted = False
        try:
            with role.begin() as db:
                action(db)
                inserted = True
        except DBAPIError as error:
            assert error.orig.sqlstate == state, (name, str(error))
            if message is not None: assert message in str(error.orig), (name, str(error))
            assert inserted == commit, (name, 'unexpected failure phase')
            assert snapshot(owner, metadata) == before, name + ': changed persisted facts'
            rejected.append(dict(case=name, sqlstate=state, phase='commit' if inserted else 'statement', rollbackPreserved=True))
        else:
            raise AssertionError(name + ': invalid data accepted')

    for kind in ('original', 'correction'):
        for attack in ('valid', 'region_self_user', 'region_self_person', 'region_time', 'region_hash',
                       'hq_self_applicant', 'hq_self_region', 'hq_time', 'hq_hash', 'hq_extra_command',
                       'region_rekeyed_command', 'cross_stage_key', 'duplicate_region'):
            c = base(owner, metadata, kind)
            app = row_for(c, 'apply')
            with api.begin() as db:
                insert(db, metadata, 'apply', app)
            region = row_for(c, 'regional', application=app, offset=2)
            hq = row_for(c, 'headquarters', application=app, regional=region, offset=3)
            if attack == 'region_self_user': region['actor_user_id'] = app['actor_user_id']
            if attack == 'region_self_person': region['actor_person_id'] = app['actor_person_id']
            if attack == 'region_time': region['created_at'] = app['created_at']
            if attack == 'region_hash': region['expected_request_hash'] = 'f'*64
            if attack == 'hq_self_applicant': hq['actor_user_id'] = app['actor_user_id']
            if attack == 'hq_self_region': hq['actor_person_id'] = region['actor_person_id']
            if attack == 'hq_time': hq['created_at'] = region['created_at']
            if attack == 'hq_hash': hq['expected_regional_hash'] = 'f'*64
            if attack == 'hq_extra_command':
                hq['command_jsonb']['quantity'] = 999
                hq['request_hash'] = digest(hq['command_jsonb'])
            if attack == 'region_rekeyed_command':
                region['command_jsonb']['source']['expected_scrap_request_hash'] = 'f'*64
                region['request_hash'] = digest(region['command_jsonb'])
            if attack == 'cross_stage_key': hq['idempotency_key_hash'] = region['idempotency_key_hash']
            def write(db):
                insert(db, metadata, 'regional', region)
                insert(db, metadata, 'headquarters', hq)
                if attack == 'duplicate_region':
                    insert(db, metadata, 'regional', row_for(c, 'regional', application=app, offset=4))
            if attack != 'valid':
                reject(kind + ':' + attack, write, message='0165 recovery request coordinates reused' if attack == 'cross_stage_key' else None)
                continue
            with api.begin() as db: write(db)
            with owner.connect() as db:
                result = db.scalar(text('SELECT rsc_check_scrap_recovery_approvals_0165(:id)'), dict(id=c['data']['line']['id']))
            assert result == [dict(recovery_request_id=str(app['id']), status='approved_pending_execution', headquarters_review_id=str(hq['id']))]
            successful.append(dict(source=kind, status=result[0]['status']))
            second = row_for(c, 'apply', offset=4)
            # A different, existing file keeps duplicate-file constraints from
            # hiding the deferred previous-application stage proof.
            file_id = uuid4()
            with owner.begin() as db: seed_file(db, metadata, identifier=file_id, user=c['actors'][0][0], person=c['actors'][0][1], at=c['time'])
            second['command_jsonb']['evidence_file_ids'] = [str(file_id)]
            second['request_hash'] = digest(second['command_jsonb'])
            reject(kind + ':second_pending_application', lambda db: insert(db, metadata, 'apply', second))

        for attack in ('no_evidence', 'wrong_requester', 'source', 'unexpected_command', 'future_time', 'untrimmed_reason'):
            c = base(owner, metadata, kind)
            app = row_for(c, 'apply')
            if attack == 'wrong_requester': app['actor_person_id'] = c['actors'][1][1]
            if attack == 'source': app['expected_scrap_request_hash'] = 'f'*64
            if attack == 'unexpected_command': app['command_jsonb']['allow'] = True
            if attack == 'future_time': app['created_at'] = datetime.now(timezone.utc) + timedelta(days=1)
            if attack == 'untrimmed_reason':
                app['reason'] = app['command_jsonb']['reason'] = '\u3000reason'
            app['request_hash'] = digest(app['command_jsonb'])
            reject(kind + ':' + attack, lambda db: insert(db, metadata, 'apply', app, evidence=attack != 'no_evidence'))

        # HQ return followed by a fresh region/HQ cycle is legitimate history.
        c = base(owner, metadata, kind)
        app = row_for(c, 'apply')
        r1 = row_for(c, 'regional', application=app, offset=2)
        h1 = row_for(c, 'headquarters', application=app, regional=r1, offset=3, decision='request_regional_review')
        r2 = row_for(c, 'regional', application=app, offset=4)
        h2 = row_for(c, 'headquarters', application=app, regional=r2, offset=5)
        with api.begin() as db:
            for stage, row in [('apply', app), ('regional', r1), ('headquarters', h1), ('regional', r2), ('headquarters', h2)]:
                insert(db, metadata, stage, row)
        successful.append(dict(source=kind, returnedAndRereviewed=True))

    # Reuse actor/request coordinates across application and region tables.
    # The region reviewer is independent of the second applicant, so only the
    # shared request namespace can reject this otherwise valid approval.
    c1, c2 = (base(owner, metadata, kind) for kind in ('original', 'correction'))
    a1, a2 = row_for(c1, 'apply'), row_for(c2, 'apply')
    region = row_for(c2, 'regional', application=a2, offset=2)
    region.update(actor_user_id=a1['actor_user_id'], actor_person_id=a1['actor_person_id'], request_id=a1['request_id'])
    region['command_jsonb']['request_id'] = a1['request_id']
    region['request_hash'] = digest(region['command_jsonb'])
    with api.begin() as db:
        insert(db, metadata, 'apply', a1)
        insert(db, metadata, 'apply', a2)
    reject('cross_stage_request_coordinates', lambda db: insert(db, metadata, 'regional', region),
        message='0165 recovery request coordinates reused')

    # Evidence rejection closes one application, allowing a new application
    # with new evidence, but never another review under the closed request.
    c = base(owner, metadata, 'original')
    app = row_for(c, 'apply')
    region = row_for(c, 'regional', application=app, offset=2, decision='needs_evidence')
    with api.begin() as db:
        insert(db, metadata, 'apply', app)
        insert(db, metadata, 'regional', region)
    reject('review_after_evidence_rejection', lambda db: insert(db, metadata, 'regional',
        row_for(c, 'regional', application=app, offset=3)))
    new = row_for(c, 'apply', offset=4)
    file_id = uuid4()
    with owner.begin() as db: seed_file(db, metadata, identifier=file_id, user=c['actors'][0][0], person=c['actors'][0][1], at=c['time'])
    new['command_jsonb']['evidence_file_ids'] = [str(file_id)]
    new['request_hash'] = digest(new['command_jsonb'])
    with api.begin() as db: insert(db, metadata, 'apply', new)
    with owner.connect() as db:
        result = db.scalar(text('SELECT rsc_check_scrap_recovery_approvals_0165(:id)'), dict(id=c['data']['line']['id']))
    assert [r['status'] for r in result] == ['needs_evidence', 'awaiting_regional']
    successful.append(dict(rejectedEvidenceThenNewApplication=True))
    c = base(owner, metadata, 'correction')
    app = row_for(c, 'apply')
    def repeatable(db):
        db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ'))
        insert(db, metadata, 'apply', app)
    reject('repeatable_read_write', repeatable)

    # Direct function calls remain denied to every runtime role.
    for role in ('star_oam_api', 'star_oam_projector', 'edge_inbox'):
        for function, arguments in [('rsc_check_scrap_recovery_approvals_0165', "'00000000-0000-0000-0000-000000000001'::uuid"),
                ('rsc_check_scrap_recovery_command_0165', "'{}'::jsonb,'{}'::jsonb"),
                ('rsc_fence_scrap_recovery_approvals_0165', ''), ('rsc_scrap_facts_append_only_0165', '')]:
            reject(role + ':' + function, lambda db: db.execute(text('SELECT ' + function + '(' + arguments + ')')),
                role=engines[role], commit=False, state='42501')
    for operation in ('UPDATE', 'DELETE', 'TRUNCATE'):
        table = 'stock_scrap_recovery_requests'
        sql = f'UPDATE {table} SET reason=reason' if operation == 'UPDATE' else f'{operation} ' + ('FROM ' if operation == 'DELETE' else '') + table
        if operation == 'TRUNCATE': sql += ' CASCADE'
        reject('owner_append_only:' + operation, lambda db: db.execute(text(sql)), role=owner, commit=False)

    for case in ('same_scrap', 'shared_key'):
        c1 = base(owner, metadata, 'original')
        c2 = c1 if case == 'same_scrap' else base(owner, metadata, 'correction')
        a1, a2 = row_for(c1, 'apply'), row_for(c2, 'apply', offset=2)
        file_id = uuid4()
        with owner.begin() as db: seed_file(db, metadata, identifier=file_id, user=c2['actors'][0][0], person=c2['actors'][0][1], at=c2['time'])
        a2['command_jsonb']['evidence_file_ids'] = [str(file_id)]
        a2['request_hash'] = digest(a2['command_jsonb'])
        barrier = Barrier(2)
        def race(row):
            try:
                with api.begin() as db:
                    db.execute(text("SET LOCAL lock_timeout='10s'"))
                    barrier.wait(timeout=10)
                    insert(db, metadata, 'apply', row)
                return 'committed'
            except DBAPIError as error:
                return error.orig.sqlstate
        if case == 'shared_key':
            # Same-table UNIQUE would serialize INSERT before the barrier.
            # Test cross-table collision: the second transaction inserts a
            # regional review of a separately committed application instead.
            with api.begin() as db: insert(db, metadata, 'apply', a2)
            regional = row_for(c2, 'regional', application=a2, offset=3)
            regional['idempotency_key_hash'] = a1['idempotency_key_hash']
            def race_region():
                try:
                    with api.begin() as db:
                        db.execute(text("SET LOCAL lock_timeout='10s'"))
                        barrier.wait(timeout=10)
                        insert(db, metadata, 'regional', regional)
                    return 'committed'
                except DBAPIError as error: return error.orig.sqlstate
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(race, a1), pool.submit(race_region)]
                result = [f.result(timeout=20) for f in futures]
        else:
            with ThreadPoolExecutor(max_workers=2) as pool:
                result = [f.result(timeout=20) for f in [pool.submit(race, a1), pool.submit(race, a2)]]
        assert sorted(result) == ['23514', 'committed'], (case, result)
        races.append(dict(case=case, outcomes=result))
    from pg16_scrap_recovery_evidence_gate import run as evidence_gate
    evidence_report=evidence_gate(owner,api,metadata,reject)
    return dict(passed=True, successful=successful, rejected=rejected, races=races,evidence=evidence_report,
        scope='native ordered approval and request binding component with minimal external parents',
        fullBusinessPosting=False, formalMigration=False, productionAcceptance=False)
