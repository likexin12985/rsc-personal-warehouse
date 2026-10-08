#!/usr/bin/env python3
"""Reproduce and repair 0165 authentication/stock locking on fresh owned PG16.

Historical 0165 reproduction only; this is not the current formal gate.
No existing server/DSN is accepted; the forward patch is candidate-only.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys

from run_local_pg16_scrap_business_checks import manifest
CLOUD = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--postgres-bin', required=True)
    args = parser.parse_args()
    sys.path[:0] = [str(CLOUD/'backend'), str(CLOUD/'backend/tests')]
    from local_pg16_cluster import native_cluster
    from sqlalchemy import select, text
    from sqlalchemy.exc import DBAPIError
    from sqlalchemy.orm import Session
    from uuid import uuid4
    frozen = manifest()
    with native_cluster(postgres_bin=args.postgres_bin,
            artifact_root=CLOUD/'artifacts/local-condition-auth-fence-pg16') as (directory, engines):
        print(json.dumps(dict(event='owned_cluster_started', directory=str(directory.relative_to(CLOUD)))), flush=True)
        (directory/'source-manifest.json').write_text(json.dumps(frozen, indent=2)+'\n')
        owner, api = engines['star_oam_migrator'], engines['star_oam_api']
        environment = dict(os.environ, OAM_ENVIRONMENT='production',
            OAM_DATABASE_URL=owner.url.render_as_string(hide_password=False),
            OAM_DATABASE_EXPECTED_MIGRATION_ROLE='star_oam_migrator', OAM_DATABASE_EXPECTED_RUNTIME_ROLE='star_oam_api')
        with (directory/'migration.log').open('wb') as log:
            subprocess.run([sys.executable, '-m', 'alembic', '-c', 'alembic.ini', 'upgrade', '20261214_0165'],
                cwd=CLOUD, env=environment, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=600)
        runpy.run_path(str(CLOUD/'backend/tests/conftest.py'))
        from app.foundation_models import StateTransitionEvent
        from app.formal_services import inventory_posting as posting
        from app.formal_services.authentication_audit import append_authentication_event
        from pg16_return_condition_seal_gate import authentication_without_inventory_wait
        patch = runpy.run_path(str(CLOUD/'backend/alembic/return_condition_candidate/forward_seal_auth.py'))
        with owner.begin() as db:
            record = db.execute(text("SELECT prosrc,prosecdef,proconfig,pg_get_userbyid(proowner) AS owner FROM pg_proc WHERE oid=to_regprocedure(:s)"), dict(s=patch['SIGNATURE'])).mappings().one()
            assert record['prosrc'] == patch['EXPECTED_BODY']
            assert record['prosecdef'] and record['owner']=='star_oam_migrator'
            assert record['proconfig']==patch['OLD']['proconfig']
        try:
            authentication_without_inventory_wait(api, {'receiverUserId': None})
        except DBAPIError as error:
            assert error.orig.sqlstate=='55P03' and 'rsc_fence_scrap_seals_0165' in str(error.orig)
        else:
            raise AssertionError('predecessor did not reproduce inventory lock conflict')
        print('predecessor_authentication_lock_conflict REPRODUCED', flush=True)
        with owner.begin() as db:
            assert db.scalar(text('SELECT prosrc FROM pg_proc WHERE oid=to_regprocedure(:s)'),dict(s=patch['SIGNATURE']))==patch['EXPECTED_BODY']
            for statement in patch['statements'](): db.execute(text(statement))
            assert db.scalar(text('SELECT prosrc FROM pg_proc WHERE oid=to_regprocedure(:s)'),dict(s=patch['SIGNATURE']))==patch['BODY']
        assert authentication_without_inventory_wait(api, {'receiverUserId': None}) is True
        print('forward_authentication_without_inventory_wait PASS', flush=True)

        def invalid_write(kind):
            with Session(api) as db:
                db.execute(text("SET LOCAL lock_timeout='1500ms'"))
                at=datetime.now(timezone.utc)
                if kind=='reserved_marker':
                    append_authentication_event(db, actor_user_id=None, action='seal_scrap_request',
                        aggregate_type='authentication_attempt', aggregate_id=str(uuid4()), request_id=uuid4().hex,
                        client_type='web', outcome='rejected', reason_code='owned_fixture', occurred_at=at)
                else:
                    body=dict(operation='formal_authentication_state_transition', request_id='authreq-'+'a'*64)
                    if kind=='malformed_state': body['operation']='unknown'
                    else: body['request_reference']='inventory-request-'+'b'*64
                    db.add(StateTransitionEvent(aggregate_type='auth_session', aggregate_id=str(uuid4()), actor_id=None,
                        from_status=None,to_status='rejected',reason='owned_fixture',idempotency_key=uuid4().hex,
                        occurred_at=at,metadata_jsonb=body))
                try: db.commit()
                except DBAPIError as error:
                    assert error.orig.sqlstate=='55P03',str(error.orig)
                    db.rollback();return kind
                raise AssertionError('reserved or malformed authentication bypassed inventory serialization')
        refused=[]
        for kind in ('reserved_marker','malformed_state','inventory_reference'):
            with Session(api) as db, ThreadPoolExecutor(max_workers=1) as pool:
                try:
                    posting._lock_inventory_ledger_head_for_atomic_batch(db)
                    refused.append(pool.submit(invalid_write,kind).result(timeout=10))
                finally: db.rollback()
        with owner.connect() as db:
            for role in ('star_oam_api','star_oam_backup','star_oam_projector','star_oam_edge','edge_inbox'):
                assert not db.scalar(text("SELECT has_function_privilege(:role,:signature,'EXECUTE')"),dict(role=role,signature=patch['SIGNATURE']))
            assert db.scalar(text('SELECT count(*) FROM inventory_transactions'))==0
            assert db.scalar(text('SELECT count(*) FROM stock_scrap_request_seals'))==0
            assert db.scalar(text("SELECT count(*) FROM audit_events WHERE stream_key='authentication'"))==1
            assert db.scalar(text("SELECT count(*) FROM state_transition_events WHERE aggregate_type='authentication_attempt'"))==1
        assert frozen==manifest()
        report=dict(passed=True, reproducedOldLockTimeout=True, independentApiConnections=True,
            ordinaryAuthenticationActualCommit=True, rejected=refused, noInventoryOrSealWrites=True,
            privateFunctionPrivilegesRetained=True, previousBodySha256=patch['EXPECTED_SHA256'],
            candidateBodySha256=patch['BODY_SHA256'], sourceDrift=[], fullConditionGate=False, formalMigration=False)
        (directory/'checks.json').write_text(json.dumps(report,indent=2)+'\n')
    state=json.loads((directory/'cluster-state.json').read_text())
    assert (state['status'],state['checks'],state['serverExitCode'])==('stopped','passed',0)
    print(json.dumps(dict(directory=str(directory.relative_to(CLOUD)),**report)),flush=True)


if __name__=='__main__':
    main()
