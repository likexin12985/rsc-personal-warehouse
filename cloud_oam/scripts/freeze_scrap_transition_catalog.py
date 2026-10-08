#!/usr/bin/env python3
"""Record and replay 0165 DDL on a newly owned PG16; output stays in artifacts.

No supplied DSN, production write, Alembic head change or automatic replacement
of a frozen release catalog. A reviewer promotes the verified artifact later.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import traceback

CLOUD = Path(__file__).resolve().parents[1]
PREVIOUS = '20261213_0164'


class Recorder:
    def __init__(self, connection):
        self.connection = connection
        self.statements = []

    def __getattr__(self, key):
        return getattr(self.connection, key)

    def execute(self, statement, *args, **kwargs):
        # Persist SQL, not psycopg's wire representation (which doubles %).
        from sqlalchemy.dialects.postgresql import dialect
        compiled = statement.compile(dialect=dialect(paramstyle='named'))
        sql = str(compiled)
        if not sql.lstrip().upper().startswith('SELECT'):
            if args or kwargs or compiled.params:
                raise ValueError('migration mutations must be literal DDL')
            self.statements.append(sql)
        return self.connection.execute(statement, *args, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--postgres-bin', required=True)
    args = parser.parse_args()
    sys.path[:0] = [str(CLOUD / 'backend'), str(CLOUD / 'backend/tests'), str(CLOUD.parent)]
    from sqlalchemy import text
    from local_pg16_cluster import native_cluster
    runpy.run_path(str(CLOUD / 'backend/tests/conftest.py'))
    from pg16_scrap_candidate_install import install
    probe = runpy.run_path(str(CLOUD / 'backend/alembic/stock_scrap_0165/catalog_probe.py'))
    manifest = runpy.run_path(str(CLOUD / 'scripts/run_local_pg16_scrap_business_checks.py'))['manifest']
    sources = manifest()
    directory = None
    try:
        with native_cluster(postgres_bin=args.postgres_bin,
                artifact_root=CLOUD / 'artifacts/local-scrap-catalog-pg16') as (directory, engines):
            (directory / 'source-manifest.json').write_text(json.dumps(sources, indent=2) + '\n')
            owner = engines['star_oam_migrator']
            environment = dict(os.environ, OAM_ENVIRONMENT='production',
                OAM_DATABASE_URL=owner.url.render_as_string(hide_password=False),
                OAM_DATABASE_EXPECTED_MIGRATION_ROLE='star_oam_migrator',
                OAM_DATABASE_EXPECTED_RUNTIME_ROLE='star_oam_api')
            with (directory / 'predecessor.log').open('wb') as output:
                result = subprocess.run([sys.executable, '-m', 'alembic', '-c', 'alembic.ini',
                    'upgrade', PREVIOUS], cwd=CLOUD, env=environment, stdout=output,
                    stderr=subprocess.STDOUT, timeout=600)
            assert result.returncode == 0, 'exact 0164 predecessor upgrade failed'
            print('exact 0164 predecessor PASS', flush=True)
            with owner.connect() as db:
                before = probe['snapshot'](db)
                db.rollback()
                recorder = Recorder(db)
                install(recorder)
                after = probe['snapshot'](db)
                db.rollback()
                assert probe['snapshot'](db) == before, 'candidate transaction rollback changed predecessor'
                db.rollback()
                for statement in recorder.statements:
                    db.execute(text(statement))
                db.commit()
                assert probe['snapshot'](db) == after, 'literal replay differs from compiler result'
                assert db.scalar(text('SELECT version_num FROM alembic_version')) == PREVIOUS
            changes = probe['difference'](before, after)
            assert all(row['after'] is not None for kind in changes.values() for row in kind.values())
            new_tables = [name for name, row in changes['tables'].items() if row['before'] is None]
            assert len(new_tables) == 10, new_tables
            data = dict(formatVersion=1, proposedRevision='20261214_0165', previousRevision=PREVIOUS,
                statements=recorder.statements, **changes)
            raw = (json.dumps(data, indent=2, ensure_ascii=False) + '\n').encode()
            (directory / 'catalog-candidate.json').write_bytes(raw)
            assert manifest() == sources, 'source drift during catalog generation'
            receipt = dict(passed=True, previousRevision=PREVIOUS, alembicAdvanced=False,
                literalReplayVerified=True, compilerRollbackVerified=True, sourceDrift=[],
                catalogSha256=hashlib.sha256(raw).hexdigest(), statementCount=len(recorder.statements),
                newTableCount=len(new_tables), changedExistingTables=len(changes['tables']) - len(new_tables),
                newFunctions=sum(row['before'] is None for row in changes['functions'].values()),
                replacedFunctions=sum(row['before'] is not None for row in changes['functions'].values()),
                productionAcceptance=False)
            (directory / 'checks.json').write_text(json.dumps(receipt, indent=2) + '\n')
        state = json.loads((directory / 'cluster-state.json').read_text())
        assert (state['status'], state['checks'], state['serverExitCode']) == ('stopped', 'passed', 0)
        receipt['directory'] = str(directory.relative_to(CLOUD))
        (directory / 'terminal-v1.json').write_text(json.dumps(receipt, indent=2) + '\n')
        print(json.dumps(receipt), flush=True)
        return 0
    except BaseException as error:
        detail = dict(passed=False, errorType=type(error).__name__, message=str(error)[:1600],
            directory=str(directory.relative_to(CLOUD)) if directory else None)
        if directory:
            (directory / 'failure.json').write_text(json.dumps(detail, indent=2) + '\n')
        traceback.print_exc()
        print(json.dumps(detail), flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
