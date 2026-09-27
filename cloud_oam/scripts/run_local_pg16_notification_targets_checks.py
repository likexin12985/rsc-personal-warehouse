"""Reproduce the notification audience gate on a new owned PG16 only."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import traceback
from unittest.mock import patch

CLOUD = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CLOUD / 'scripts'))
from run_local_pg16_stock_loss_sources_checks import manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--postgres-bin', required=True)
    args = parser.parse_args()
    sys.path[:0] = [str(CLOUD / 'backend'), str(CLOUD / 'backend/tests')]
    runpy.run_path(str(CLOUD / 'backend/tests/conftest.py'))
    from local_pg16_cluster import native_cluster
    from sqlalchemy import text
    from sqlalchemy.orm import Session
    from app.database_security import validate_production_database_security
    from app.formal_services import notification_identities
    from notification_identity_fixtures import POLICY, prepare_pg16_notification_identities
    from test_formal_access import make_organization, make_user
    source = manifest(); directory = None
    helper = CLOUD / 'backend/tests/pg16_notification_targets_gate.py'
    spec = importlib.util.spec_from_file_location('notification_target_gate', helper)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    try:
        with native_cluster(postgres_bin=args.postgres_bin,
                artifact_root=CLOUD / 'artifacts/local-notification-targets-pg16/checks') as (directory, engines):
            print(json.dumps(dict(event='owned_cluster_started', directory=str(directory.relative_to(CLOUD)))), flush=True)
            (directory / 'source-manifest.json').write_text(json.dumps(source, indent=2)+'\n')
            owner, api = engines['star_oam_migrator'], engines['star_oam_api']
            url = owner.url.render_as_string(hide_password=False)
            env = dict(os.environ, OAM_ENVIRONMENT='production', OAM_DATABASE_URL=url,
                OAM_DATABASE_EXPECTED_MIGRATION_ROLE='star_oam_migrator', OAM_DATABASE_EXPECTED_RUNTIME_ROLE='star_oam_api')
            with (directory / 'migration.log').open('wb') as log:
                subprocess.run([sys.executable, '-m', 'alembic', '-c', 'alembic.ini', 'upgrade', 'head'],
                    cwd=CLOUD, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=600, check=True)
            with (directory / 'edge-provision.log').open('wb') as log:
                subprocess.run([str(Path(args.postgres_bin).resolve()/'psql'), '-X', '-w', '--set=ON_ERROR_STOP=1',
                    '--dbname', url.replace('postgresql+psycopg:', 'postgresql:', 1), '-v', 'edge_role=edge_inbox',
                    '-f', str(CLOUD/'deployment/create_oam_edge_staging.sql')], cwd=CLOUD, env=env,
                    stdout=log, stderr=subprocess.STDOUT, timeout=120, check=True)
            def security():
                validate_production_database_security(api, expected_runtime_role='star_oam_api',
                    expected_migration_role='star_oam_migrator')
            security()
            with Session(owner) as db:
                organization = make_organization(db, name='Synthetic notification targets')
                make_user(db, organization, name='Synthetic notification recipient')
                db.commit()
            prepare_pg16_notification_identities(owner)
            with patch.object(notification_identities, 'identity_policy', lambda: POLICY):
                module.assert_notification_targets_gate(api, owner)
            security()
            assert source == manifest()
            report = dict(status='passed', actualSource=True, helperSha256=hashlib.sha256(helper.read_bytes()).hexdigest(),
                audienceGate=True, apiColumnAclAndOwnerImmutability=True, securityBeforeAndAfter=True,
                sourceDrift=[], providerCalls=0, githubReleaseGate=False, productionAcceptance=False)
            (directory/'checks.json').write_text(json.dumps(report,indent=2)+'\n')
        state = json.loads((directory/'cluster-state.json').read_text())
        assert state['status']=='stopped' and state['checks']=='passed' and state['serverExitCode']==0
        print(json.dumps(dict(directory=str(directory.relative_to(CLOUD)), **report)), flush=True)
        return 0
    except BaseException as error:
        detail=dict(status='failed',errorType=type(error).__name__,message=str(getattr(error,'orig',error))[:1200],
            directory=str(directory.relative_to(CLOUD)) if directory else None,
            frames=[dict(file=f.filename,line=f.lineno,function=f.name) for f in traceback.extract_tb(error.__traceback__)])
        if directory:(directory/'failure.json').write_text(json.dumps(detail,indent=2)+'\n')
        print(json.dumps(detail),flush=True)
        return 1

if __name__=='__main__':
    raise SystemExit(main())
