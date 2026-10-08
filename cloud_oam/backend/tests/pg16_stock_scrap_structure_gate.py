"""Structural draft only, on caller-created synthetic PG16 at unchanged 0164.

Uses real report/original execution/inverse/approval facts. No production DSN,
trigger disable, ledger overwrite, new business privilege or scrap post occurs.
This is not a released Alembic upgrade, ACL acceptance or full migration gate.
"""
from uuid import UUID, uuid4
from hashlib import sha256
import json
from sqlalchemy import text, inspect
from sqlalchemy.orm import Session
from sqlalchemy.exc import DBAPIError
from app.formal_access import load_formal_principal
from app.formal_services.stock_loss_corrections import bound_commands, reversal_stock
from app.formal_services.stock_loss_corrections.request_contracts import ReversalPreview, ReversalExecute, CorrectionApprove
from pg16_loss_multigeneration_fixture import exercise as original_fixture
from pg16_loss_correction_recovery import readonly, readonly_original
from test_postgresql16_release_gate import HEAD_REVISION
from pg16_stock_scrap_schema import compile_structure


def original_columns(db):
    inspector = inspect(db)
    return {name: [column['name'] for column in inspector.get_columns(name, schema='public')]
            for name in inspector.get_table_names(schema='public')}


def facts(db, columns):
    q = db.dialect.identifier_preparer.quote
    return {table: db.execute(text('SELECT row_to_json(t)::text FROM (SELECT ' +
        ','.join(q(column) for column in names) + ' FROM public.' + q(table) +
        ') t ORDER BY row_to_json(t)::text COLLATE "C"')).scalars().all()
        for table, names in sorted(columns.items())}


def functions(db):
    return db.execute(text("SELECT p.oid,pg_get_functiondef(p.oid) FROM pg_proc p "
        "JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname IN ('public','rsc_private') "
        "AND p.prokind IN ('f','p') ORDER BY p.oid")).all()


def existing_foreign_keys(db, names):
    return db.execute(text("SELECT c.conrelid::regclass::text,c.conname,pg_get_constraintdef(c.oid) "
        "FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid JOIN pg_namespace n ON n.oid=t.relnamespace "
        "WHERE n.nspname='public' AND c.contype='f' AND t.relname=ANY(:names) ORDER BY 1,2"),
        dict(names=list(names))).all()


def run(context):
    original = original_fixture(context)
    owner, api = (context['engines'][name] for name in ('star_oam_migrator', 'star_oam_api'))
    with Session(api) as db:
        actor = load_formal_principal(db, context['admin_id'])
        selection = ReversalPreview(**original['binding'], reversed_correction_id=None,
            expected_execution_request_hash=original['binding']['expected_root_request_hash'])
        preview = reversal_stock.prepare(db, actor=actor, request=selection)
        inverse_command = ReversalExecute(**selection.model_dump(), expected_plan_hash=preview.plan_hash,
            request_id=uuid4().hex, idempotency_key=uuid4().hex)
        inverse = bound_commands.inverse(db, actor=actor, request=inverse_command)
        db.commit()
        approval_command = CorrectionApprove(**original['binding'], reversal_id=UUID(inverse['reversal_id']),
            expected_reversal_hash=inverse['request_hash'], disposition='scrap',
            request_id=uuid4().hex, idempotency_key=uuid4().hex)
        bound_commands.approve(db, actor=load_formal_principal(db, context['admin_id']), request=approval_command)
        db.commit()
    bundle_proof = _preparation_bundle(context, original, inverse, approval_command)
    requests_before = [readonly_original(api, context, original['original_command']),
                       readonly(api, context, inverse_command), readonly(api, context, approval_command)]
    assert requests_before[0]['lookup_status'] == 'found'
    assert all(result['request_state'] == 'found' for result in requests_before[1:])
    metadata, tables, parents, statements = compile_structure()
    with owner.connect() as db:
        assert db.scalar(text('SELECT current_database()')) == 'rsc_pg16_release_gate'
        assert db.scalar(text('SELECT current_user')) == 'star_oam_migrator'
        assert 160000 <= int(db.scalar(text('SHOW server_version_num'))) < 170000
        assert db.scalar(text('SELECT version_num FROM alembic_version')) == HEAD_REVISION == '20261213_0164'
        columns = original_columns(db)
        assert not any(table.name in columns for table in tables)
        before, funcs, foreign_keys = facts(db, columns), functions(db), existing_foreign_keys(db, columns)
        assert before['stock_loss_dispositions'] and before['stock_loss_disposition_reversals']
        assert before['stock_loss_correction_decisions']
    with owner.begin() as db:
        db.execute(text("SET LOCAL lock_timeout='5s'"))
        for statement in statements:
            db.execute(text(statement))
        assert facts(db, columns) == before, 'draft structure changed existing historical columns'
        assert functions(db) == funcs, 'draft structure changed a business or security function'
        current_fks = set(existing_foreign_keys(db, columns))
        assert set(foreign_keys) <= current_fks, 'draft structure changed an existing FK'
        assert db.scalar(text('SELECT version_num FROM alembic_version')) == HEAD_REVISION
        for table in tables:
            assert db.scalar(text('SELECT count(*) FROM public.' + table.name)) == 0
            for role in ('star_oam_api', 'star_oam_projector', 'star_oam_edge', 'edge_inbox'):
                assert not db.scalar(text('SELECT has_table_privilege(:role,:table,:privileges)'),
                    dict(role=role, table='public.' + table.name,
                         privileges='SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER'))
    # Actual API role rejection, not only metadata ACL checks.
    rejected = []
    for table in tables:
        with api.connect() as db:
            try:
                db.execute(text('INSERT INTO public.' + table.name + ' DEFAULT VALUES'))
            except DBAPIError as error:
                assert error.orig.sqlstate == '42501'
                rejected.append(table.name)
                db.rollback()
            else:
                raise AssertionError('unfinished scrap fact accepted by API role')
    requests_after = [readonly_original(api, context, original['original_command']),
                      readonly(api, context, inverse_command), readonly(api, context, approval_command)]
    assert requests_after == requests_before, 'historical exact request recovery changed'
    with owner.connect() as db:
        assert facts(db, columns) == before
        assert functions(db) == funcs
        for table in tables:
            assert db.scalar(text('SELECT count(*) FROM public.' + table.name)) == 0
    return dict(passed=True, scope='candidate structural DDL only; no business activation',
        tracking=context['tracking'], preparationAndBundle=bundle_proof, existingTablesCompared=len(columns),
        originalRowsPreserved=sum(len(rows) for rows in before.values()),
        oldFactsSha256=sha256(json.dumps(before, sort_keys=True).encode()).hexdigest(),
        structureStatements=len(statements), newTables=len(tables), extendedParents=len(parents),
        existingForeignKeysPreserved=len(foreign_keys), existingFunctionsPreserved=len(funcs),
        exactOriginalInverseApprovalRecoveryUnchanged=True, apiInsertDeniedTables=rejected,
        newFactsEmpty=True, migrationHeadUnchanged=True, formalMigrationImplemented=False,
        scrapPostingImplemented=False, productionAcceptance=False)


def _preparation_bundle(context, original, inverse, approval):
    """Use actual API-role preparation; compiling records never writes stock."""
    from sqlalchemy import event
    from app.stock_scrap_schemas import ScrapPreview, ScrapExecute
    from app.formal_services import stock_scrap_plan
    from app.formal_services.stock_scrap.execution_bundle import build
    from test_stock_scrap_plan import _upload
    from pg16_loss_correction_gate import stock_snapshot
    owner, api = (context['engines'][name] for name in ('star_oam_migrator','star_oam_api'))
    with Session(api) as db:
        actor = load_formal_principal(db, context['admin_id'])
        uploaded = _upload(db, actor)
        from app.stock_loss_correction_models import StockLossCorrectionDecision
        from sqlalchemy import select
        decision = db.scalars(select(StockLossCorrectionDecision).where(
            StockLossCorrectionDecision.reversal_id == approval.reversal_id)).one()
        request = ScrapPreview(source=dict(kind='correction',
            **{k:v for k,v in original['binding'].items() if k!='reason'},
            reversal_id=approval.reversal_id, expected_reversal_hash=inverse['request_hash'],
            correction_decision_id=decision.id, expected_correction_decision_hash=decision.request_hash),
            execution_reason='Synthetic native scrap preparation with exact serial admission',
            evidence_file_ids=(uploaded.id,))
    before = stock_snapshot(owner)
    statements = []
    def only_reads(connection,cursor,statement,parameters,execution,executemany):
        assert statement.lstrip().upper().startswith(('SELECT ','SAVEPOINT ','RELEASE SAVEPOINT ','ROLLBACK TO SAVEPOINT '))
        statements.append(statement)
    with Session(api) as db:
        event.listen(db.connection(),'before_cursor_execute',only_reads)
        actor = load_formal_principal(db,context['admin_id'])
        prepared = stock_scrap_plan.prepare(db,actor=actor,request=request)
        for item in prepared.document['serials']:
            first = db.scalar(text('SELECT s.movement_id FROM inventory_movement_serials s '
                'JOIN inventory_movements m ON m.id=s.movement_id '
                'JOIN inventory_transactions t ON t.id=m.transaction_id '
                'WHERE s.serial_id=:serial AND t.status=\'posted\' AND m.to_account_id IS NOT NULL '
                'ORDER BY t.ledger_cursor,m.line_no LIMIT 1'),dict(serial=UUID(item['serial_id'])))
            assert str(first)==item['admission_movement_id']
        command=ScrapExecute(**request.model_dump(),expected_plan_hash=prepared.plan_hash,
            request_id=uuid4().hex,idempotency_key=uuid4().hex)
        draft=build(actor=actor,request=command,preparation=prepared)
        rows=draft.rows(transaction_id=uuid4(),movement_id=uuid4())
        assert len(rows['stock_scrap_serials'])==int(context['tracking']=='serial')
        assert 'stock_loss_dispositions' not in rows
        assert rows['stock_loss_correction_executions'][0]['root_disposition_id']==original['root_id']
        assert not db.new and not db.dirty and not db.deleted
    assert stock_snapshot(owner)==before
    with owner.begin() as db:
        db.execute(text('SELECT id FROM inventory_ledger_heads FOR UPDATE NOWAIT')).all()
        db.execute(text('SELECT stream_key FROM audit_chain_heads FOR UPDATE NOWAIT')).all()
    return dict(passed=True,scope='correction-origin preparation and unpersisted execution bundle',
        nonmutatingStatements=len(statements),actualApiRole=True,sourceStockUnchanged=True,
        locksReleased=True,admissionFromImmutableLedger=True,postingImplemented=False)
