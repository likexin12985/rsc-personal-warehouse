#!/usr/bin/env python3
"""Install seal structural candidate over real 0164 in a newly owned PG16.

No supplied DSN, existing cluster, business grant or formal migration is used.
This verifies DDL/FKs/closed API privileges; it is not seal business acceptance.
"""
import argparse
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys

from run_local_pg16_scrap_forward_history_checks import manifest

CLOUD = Path(__file__).resolve().parents[1]


def check(engines):
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError
    from sqlalchemy.schema import CreateTable
    from app.stock_scrap_seal_schema import build_schema
    from pg16_stock_scrap_schema import compile_structure
    from pg16_stock_scrap_structure_gate import original_columns, functions, existing_foreign_keys
    from pg16_scrap_seal_request_contract import run as request_contract
    owner, api = (engines[name] for name in ('star_oam_migrator', 'star_oam_api'))
    _, seal = build_schema()
    _, _, _, statements = compile_structure()
    with owner.begin() as db:
        assert db.scalar(text('SELECT current_user')) == 'star_oam_migrator'
        assert db.scalar(text('SELECT current_database()')) == 'rsc_pg16_release_gate'
        assert 160000 <= int(db.scalar(text('SHOW server_version_num'))) < 170000
        assert db.scalar(text('SELECT version_num FROM alembic_version')) == '20261213_0164'
        columns = original_columns(db)
        old_functions = functions(db)
        old_fks = existing_foreign_keys(db, columns)
        for statement in statements:
            db.execute(text(statement))
        db.execute(CreateTable(seal))
        assert functions(db) == old_functions
        assert set(old_fks) <= set(existing_foreign_keys(db, columns))
        assert db.scalar(text('SELECT version_num FROM alembic_version')) == '20261213_0164'
        constraints = db.execute(text("SELECT contype,convalidated FROM pg_constraint "
            "WHERE conrelid='public.stock_scrap_request_seals'::regclass")).all()
        assert constraints and all(valid for _, valid in constraints)
        foreign_keys = sum(kind == 'f' for kind, _ in constraints)
        assert foreign_keys == len(seal.foreign_key_constraints)
        for role in ('star_oam_api', 'star_oam_projector', 'star_oam_edge', 'edge_inbox'):
            assert not db.scalar(text('SELECT has_table_privilege(:role,:table,:privileges)'),
                dict(role=role, table=seal.name, privileges='SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER'))
    rejected = []
    for operation in ('SELECT * FROM', 'INSERT INTO', 'UPDATE', 'DELETE FROM', 'TRUNCATE'):
        suffix = ' DEFAULT VALUES' if operation == 'INSERT INTO' else ' SET kind=kind' if operation == 'UPDATE' else ''
        with api.connect() as db:
            try:
                db.execute(text(operation+' public.stock_scrap_request_seals'+suffix))
            except DBAPIError as error:
                assert error.orig.sqlstate == '42501'
                rejected.append(operation)
                db.rollback()
            else:
                raise AssertionError('candidate table exposed before controlled activation')
    with owner.begin() as db:
        db.execute(text((CLOUD/'backend/alembic/stock_scrap_0165/seal_request.sql').read_text()))
    contract = request_contract(owner, api)
    return dict(passed=True, scope='structural candidate only', full0164Migrated=True,
        requestContract=contract,
        validatedForeignKeys=foreign_keys, validatedConstraints=len(constraints),
        oldFunctionsPreserved=len(old_functions), oldForeignKeysPreserved=len(old_fks),
        apiOperationsDenied=rejected, formalMigrationImplemented=False,
        registrarImplemented=False, sealBusinessAcceptance=False, productionAcceptance=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--postgres-bin', required=True)
    args = parser.parse_args()
    sys.path[:0] = [str(CLOUD/'backend'), str(CLOUD/'backend/tests'), str(CLOUD.parent)]
    runpy.run_path(str(CLOUD/'backend/tests/conftest.py'))
    from local_pg16_cluster import native_cluster
    sources = manifest()
    directory = None
    try:
        with native_cluster(postgres_bin=args.postgres_bin,
                artifact_root=CLOUD/'artifacts/local-scrap-seal-structure-pg16') as (directory, engines):
            (directory/'source-manifest.json').write_text(json.dumps(sources, indent=2)+'\n')
            environment = dict(os.environ, OAM_ENVIRONMENT='production',
                OAM_DATABASE_URL=engines['star_oam_migrator'].url.render_as_string(hide_password=False),
                OAM_DATABASE_EXPECTED_MIGRATION_ROLE='star_oam_migrator',
                OAM_DATABASE_EXPECTED_RUNTIME_ROLE='star_oam_api')
            with (directory/'upgrade-complete-0164.log').open('wb') as output:
                subprocess.run([sys.executable, '-m', 'alembic', '-c', 'alembic.ini', 'upgrade', 'head'],
                    cwd=CLOUD, env=environment, stdout=output, stderr=subprocess.STDOUT, check=True, timeout=600)
            print('full 0164 migration PASS', flush=True)
            result = check(engines)
            result['sourceDrift'] = sorted(set(sources.items()) ^ set(manifest().items()))
            assert not result['sourceDrift']
            (directory/'checks.json').write_text(json.dumps(result, indent=2)+'\n')
        state = json.loads((directory/'cluster-state.json').read_text())
        assert (state['status'], state['checks'], state['serverExitCode']) == ('stopped', 'passed', 0)
        print(json.dumps(dict(directory=str(directory.relative_to(CLOUD)), **result)), flush=True)
    except BaseException as error:
        if directory is not None:
            (directory/'failure.json').write_text(json.dumps(dict(errorType=type(error).__name__,
                message=str(getattr(error, 'orig', error))[:800]), indent=2)+'\n')
        raise


if __name__ == '__main__':
    main()
