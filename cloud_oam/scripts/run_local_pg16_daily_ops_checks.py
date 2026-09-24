#!/usr/bin/env python3
"""Exercise the daily mapping process entry in a new, owned PG16 cluster."""
import argparse
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import runpy
from secrets import token_urlsafe
import subprocess
import sys
from tempfile import TemporaryDirectory
import traceback
from unittest.mock import patch

from sqlalchemy import URL, create_engine, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

CLOUD = Path(__file__).resolve().parents[1]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--postgres-bin', required=True)
    args = parser.parse_args(argv)
    sys.path[:0] = [str(CLOUD/'backend'), str(CLOUD/'backend/tests')]
    from local_pg16_cluster import native_cluster
    directory = None
    try:
        with native_cluster(postgres_bin=args.postgres_bin,
                            artifact_root=CLOUD/'artifacts/local-daily-ops-pg16/checks') as (directory, engines):
            url = engines['star_oam_migrator'].url.render_as_string(hide_password=False)
            environment = dict(os.environ, OAM_ENVIRONMENT='production', OAM_DATABASE_URL=url,
                OAM_DATABASE_EXPECTED_MIGRATION_ROLE='star_oam_migrator',
                OAM_DATABASE_EXPECTED_RUNTIME_ROLE='star_oam_api')
            def command(name, argv):
                with (directory/(name+'.log')).open('wb') as output:
                    subprocess.run(argv,cwd=CLOUD,env=environment,stdout=output,
                                   stderr=subprocess.STDOUT,timeout=600,check=True)
            command('upgrade-head', [sys.executable,'-m','alembic','-c','alembic.ini','upgrade','head'])
            with engines['star_oam_migrator'].connect() as connection:
                assert connection.scalar(text('SELECT version_num FROM alembic_version'))=='20261119_0140'
            command('edge-grants', [str(Path(args.postgres_bin)/'psql'),'-X','-w',
                '--set=ON_ERROR_STOP=1','--dbname',url.replace('postgresql+psycopg:','postgresql:',1),
                '-v','edge_role=edge_inbox','-f',str(CLOUD/'deployment/create_oam_edge_staging.sql')])
            runpy.run_path(str(CLOUD/'backend/tests/conftest.py'))
            from app.daily_reconciliation.capture_provisioning import provision_capture_roles
            from app.daily_reconciliation.capture_role_contract import ROLES
            from app.daily_reconciliation.capture_security import validate_capture_roles
            from app.daily_reconciliation import ops_cli
            from app.daily_reconciliation.mapping_models import DailyMappingDecision
            from app.daily_reconciliation.cutoff_models import DailyCutoff
            from app.database_security import validate_production_database_security
            from pg16_daily_review_fixture import prepare
            from pg16_daily_review_fixture import _conninfo
            socket = json.loads((directory/'cluster-state.json').read_text())['socketDirectory']
            admin = create_engine(URL.create('postgresql+psycopg',username='postgres',
                database='rsc_pg16_release_gate',query={'host':socket}),poolclass=NullPool)
            try:
                with admin.begin() as connection:
                    passwords={role:token_urlsafe(40) for role in ROLES}
                    assert provision_capture_roles(connection,database='rsc_pg16_release_gate',
                        passwords=passwords,apply=True)['configured']
                    assert validate_capture_roles(connection)
                validate_production_database_security(engines['star_oam_api'],
                    expected_runtime_role='star_oam_api',expected_migration_role='star_oam_migrator')
                readers={role:create_engine(engines['star_oam_migrator'].url.set(
                    username=role,password=passwords[role]),poolclass=NullPool,hide_parameters=True)
                    for role in ROLES}
                try:
                    identities,cutoffs=prepare(engines['star_oam_migrator'],engines['edge_inbox'],readers)
                    assert len(cutoffs)==3 and 'hq' in identities
                    with Session(engines['star_oam_migrator']) as db:
                        mapping=db.scalar(select(DailyMappingDecision))
                        cutoff=db.get(DailyCutoff,cutoffs[0])
                        assert mapping.actor_user_id==cutoff.actor_user_id==identities['hq']['user_id']
                        mapping_doc=dict(command=mapping.payload_jsonb['request'],
                            expected_authorization_version=mapping.actor_authorization_version,
                            review_sha256=mapping.review_sha256)
                        cutoff_doc=dict(command=cutoff.payload_jsonb['command'],
                            expected_authorization_version=cutoff.actor_authorization_version)
                        mapping_id=str(mapping.id);cutoff_id=str(cutoff.id)
                    with TemporaryDirectory(prefix='rsc-daily-cli-') as scratch:
                        private=Path(scratch)
                        paths={}
                        for label,content in (
                            ('owner',_conninfo(engines['star_oam_migrator'])),
                            ('source',_conninfo(readers['rsc_control_capture'])),
                            ('ledger',_conninfo(readers['rsc_reconciliation_capture'])),
                            ('mapping',json.dumps(mapping_doc)),('cutoff',json.dumps(cutoff_doc))):
                            path=private/(label+'.json' if label in ('mapping','cutoff') else label+'.conninfo')
                            path.write_text(content);path.chmod(0o600);paths[label]=path
                        settings=dict(OAM_DAILY_RECONCILIATION_DATABASE_NAME='rsc_pg16_release_gate',
                            OAM_DAILY_RECONCILIATION_MAXIMUM_SECONDS='45',
                            OAM_DAILY_RECONCILIATION_OWNER_CONNINFO_FILE=str(paths['owner']),
                            OAM_DAILY_RECONCILIATION_SOURCE_CONNINFO_FILE=str(paths['source']),
                            OAM_DAILY_RECONCILIATION_LEDGER_CONNINFO_FILE=str(paths['ledger']))
                        def invoke(operation,label):
                            reader,writer=os.pipe()
                            try:
                                os.write(writer,identities['hq']['token'].encode());os.close(writer)
                                writer=None
                                output=io.StringIO()
                                with redirect_stdout(output):
                                    code=ops_cli.main([operation,'--command-file',str(paths[label]),
                                        '--access-token-fd',str(reader)])
                                assert code==0
                                return json.loads(output.getvalue())
                            finally:
                                os.close(reader)
                                if writer is not None:os.close(writer)
                        with patch.dict(os.environ,settings):
                            mapping_read=invoke('mapping-status','mapping')
                            cutoff_read=invoke('cutoff-recover','cutoff')
                        assert mapping_read['decision_id']==mapping_id and mapping_read['recorded']
                        assert cutoff_read['receipt']['cutoff_id']==cutoff_id and cutoff_read['receipt']['recorded']
                    with engines['star_oam_migrator'].connect() as connection:
                        assert connection.scalar(text('SELECT count(*) FROM daily_comparison_mapping_decisions'))==1
                        assert connection.scalar(text("SELECT count(*) FROM audit_events WHERE action='daily_reconciliation.mapping.grant'"))==1
                    result=dict(status='passed',head='20261119_0140',mappingDecisions=1,
                        mappingAudits=1,cutoffs=len(cutoffs),cliExactMappingRead=True,
                        cliExactCutoffRecovery=True,ciReleaseGate=False)
                    (directory/'checks.json').write_text(json.dumps(result,indent=2)+'\n')
                    print(json.dumps(dict(result,evidenceDirectory=str(directory))),flush=True)
                finally:
                    for engine in readers.values():engine.dispose()
            finally:
                admin.dispose()
    except BaseException as error:
        failure=dict(status='failed',errorType=type(error).__name__,evidenceDirectory=str(directory) if directory else None,
            frames=[dict(file=f.filename,line=f.lineno,function=f.name) for f in traceback.extract_tb(error.__traceback__)])
        if directory is not None:
            (directory/'failure.json').write_text(json.dumps(failure,indent=2)+'\n')
        print(json.dumps(failure),flush=True)
        return 1
    return 0


if __name__=='__main__':
    raise SystemExit(main())
