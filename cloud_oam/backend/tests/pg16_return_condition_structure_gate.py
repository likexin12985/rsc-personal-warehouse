"""Apply and transactionally roll back the structural candidate over real old history.

Only an owned-cluster migrator can call this. New facts remain empty and no
business privilege is granted. This is not correction posting or a released
Alembic transition; the application head deliberately stays at 0165.
"""
import hashlib
import json
import runpy
from pathlib import Path

from sqlalchemy import text

from pg16_return_condition_schema import compile_structure
from pg16_stock_scrap_structure_gate import (
    existing_foreign_keys, facts, functions, original_columns,
)


def run(owner, *, original, artifact_directory, invariant_candidate=False, posting_candidate=False,
        identity_candidate=False, evidence_candidate=False):
    identity_candidate = identity_candidate or evidence_candidate
    posting_candidate = posting_candidate or identity_candidate
    invariant_candidate = invariant_candidate or posting_candidate
    metadata, tables, parents, statements = compile_structure()
    if invariant_candidate:
        from app.return_condition_guards import statements as guard_statements
        statements.extend(guard_statements(posting=posting_candidate, identity=identity_candidate))
    if evidence_candidate:
        compiler = runpy.run_path(str(Path(__file__).resolve().parents[1]
            / 'alembic/return_condition_candidate/evidence.py'))
        statements.extend(compiler['statements']())
    folder = Path(artifact_directory).resolve()
    if not folder.is_relative_to(Path(__file__).resolve().parents[2] / 'artifacts/local-return-condition-pg16'):
        raise ValueError('owned condition-check artifact directory required')
    compiled = '\n\n'.join(s.rstrip().rstrip(';') + ';' for s in statements) + '\n'
    # Record the exact DDL executed by this process, including constraint order.
    (folder / 'condition-candidate.sql').write_text(compiled)
    with owner.connect() as db:
        assert db.scalar(text('SELECT current_database()')) == 'rsc_pg16_release_gate'
        assert db.scalar(text('SELECT current_user')) == 'star_oam_migrator'
        assert 160000 <= int(db.scalar(text('SHOW server_version_num'))) < 170000
        assert db.scalar(text('SELECT version_num FROM alembic_version')) == '20261215_0166'
        assert db.scalar(text('SELECT count(*) FROM stock_operation_return_inbounds WHERE id=:id'),
                         {'id': original['inboundId']}) == 1
        columns = original_columns(db)
        assert not set(metadata.tables[t.name].name for t in tables).intersection(columns)
        before, old_functions = facts(db, columns), functions(db)
        old_fks = existing_foreign_keys(db, columns)
        assert before['stock_operation_return_inbounds'] and before['stock_operation_receipts']
        db.rollback()
        transaction = db.begin()
        try:
            db.execute(text("SET LOCAL lock_timeout='5s'"))
            db.execute(text("SET LOCAL statement_timeout='60s'"))
            db.execute(text('SET LOCAL search_path=public'))
            for statement in statements:
                db.execute(text(statement))
            assert facts(db, columns) == before
            current_functions = functions(db)
            assert set(old_functions) <= set(current_functions)
            assert len(current_functions) == len(old_functions) + (4 if invariant_candidate else 0) + (2 if posting_candidate else 0) + (2 if identity_candidate else 0) + (4 if evidence_candidate else 0)
            if invariant_candidate:
                # Execute against actual old-service acceptance facts, rather
                # than merely compiling PL/pgSQL with unchecked column names.
                source_ids = db.scalars(text('SELECT id FROM stock_operation_return_inbound_lines WHERE inbound_id=:id'),
                    {'id': original['inboundId']}).all()
                assert source_ids
                for source_id in source_ids:
                    db.execute(text('SELECT public.rsc_condition_check_source(:id)'), {'id': source_id})
                private_functions = ('rsc_condition_reject_mutation()', 'rsc_condition_lock_source()',
                    'rsc_condition_check_source(uuid)', 'rsc_condition_validate_insert()')
                if posting_candidate:
                    private_functions += ('rsc_condition_check_posting(uuid)', 'rsc_condition_posting_fence()')
                if identity_candidate:
                    private_functions += ('rsc_condition_check_identity(uuid)', 'rsc_condition_identity_fence()')
                if evidence_candidate:
                    private_functions += ('rsc_condition_check_file(uuid)', 'rsc_condition_check_event_files(uuid)',
                        'rsc_condition_evidence_fence()', 'rsc_condition_foreign_evidence()')
                for name in private_functions:
                    for role in ('star_oam_api', 'star_oam_projector', 'star_oam_edge', 'edge_inbox', 'star_oam_backup'):
                        assert not db.scalar(text('SELECT has_function_privilege(:role,:name,:priv)'),
                            dict(role=role, name='public.'+name, priv='EXECUTE'))
                assert db.scalar(text("SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal "
                    "AND tgname IN ('condition_immutable','condition_source_lock','condition_source_complete',"
                    "'condition_posting_complete','condition_identity_complete')")) == 12 + (8 if posting_candidate else 0) + (8 if identity_candidate else 0)
                if evidence_candidate:
                    assert db.scalar(text("SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal "
                        "AND tgname IN ('condition_evidence_complete','condition_evidence_exclusive') "
                        "AND tgenabled='A'")) == 3 + len(compiler['references']()) + 1
                    # Actual old completed files remain outside the new purpose;
                    # call the proof, not just CREATE unchecked PL/pgSQL text.
                    for file_id in db.scalars(text('SELECT id FROM files ORDER BY id')):
                        db.execute(text('SELECT public.rsc_condition_check_file(:id)'),dict(id=file_id))
            assert set(old_fks) <= set(existing_foreign_keys(db, columns))
            assert db.scalar(text('SELECT version_num FROM alembic_version')) == '20261215_0166'
            checked_privileges = []
            for table in tables:
                assert db.scalar(text('SELECT count(*) FROM public.' + table.name)) == 0
                for role in ('star_oam_api', 'star_oam_projector', 'star_oam_edge', 'edge_inbox'):
                    assert not db.scalar(text('SELECT has_table_privilege(:role,:table,:privileges)'),
                        dict(role=role, table='public.' + table.name,
                             privileges='SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER'))
                    checked_privileges.append(dict(role=role, table=table.name))
        finally:
            transaction.rollback()
        assert original_columns(db) == columns
        assert facts(db, columns) == before
        assert functions(db) == old_functions
        assert existing_foreign_keys(db, columns) == old_fks
        for table in tables:
            assert db.scalar(text('SELECT to_regclass(:name)'), dict(name='public.' + table.name)) is None
    return dict(passed=True, newTables=len(tables), extendedParents=len(parents),
        executedDdlSha256=hashlib.sha256(compiled.encode()).hexdigest(),
        statements=len(statements), originalTables=len(columns),
        preservedRows=sum(len(rows) for rows in before.values()),
        originalFactsSha256=hashlib.sha256(json.dumps(before, sort_keys=True).encode()).hexdigest(),
        preservedFunctions=len(old_functions), preservedForeignKeys=len(old_fks),
        aclCatalogDenied=checked_privileges, candidateNewFactsEmpty=True,
        ddlTransactionRolledBack=True, fullOriginalSchemaRestored=True,
        migrationHeadUnchanged=True, formalMigrationImplemented=False,
        invariantCandidateInstalled=invariant_candidate,
        postingCandidateInstalled=posting_candidate,
        identityCandidateInstalled=identity_candidate,
        evidenceCandidateInstalled=evidence_candidate,
        oldAcceptanceCheckedByCandidate=invariant_candidate,
        apiBusinessWritesTested=False, productionAcceptance=False)
