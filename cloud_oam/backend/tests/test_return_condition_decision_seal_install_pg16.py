"""Owned PG16 installer/ACL/rollback checks; no historical business proof.

The actual metadata and SQL are installed without replacing any authority or
history helper with a stub. Those dependency functions are deliberately not
called here; full migrated business gates must precede release.
"""
import hashlib
import os
import json
from pathlib import Path
import re
import runpy

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.return_condition_seal_schema import NAME as INITIAL_SEAL
from app.return_condition_settlement_input_schema import build_schema
from app.return_condition_decision_seal_schema import NAME, SOURCE_CONSTRAINT
from app.formal_services.stock_loss_corrections.return_condition_coordinates import LEGACY_TABLES
from local_pg16_cluster import native_cluster

CLOUD = Path(__file__).resolve().parents[2]
FOLDER = CLOUD/'backend/alembic/return_condition_candidate'
BIN = Path(os.environ.get('RSC_NATIVE_PG16_BIN', str(CLOUD/'artifacts/pg16-native-20260920/install/bin')))


def test_native_installer_catalog_acl_and_transactional_rollback():
    metadata, _, _ = build_schema()
    paths = [FOLDER/'decision_seals.py', *sorted(FOLDER.glob('decision_seal_*.sql')),
        CLOUD/'backend/app/return_condition_decision_seal_schema.py', Path(__file__)]
    sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    binding = {str(p.relative_to(CLOUD)):sha(p) for p in paths}
    migration = CLOUD/'backend/alembic/versions/20260831_0026_opening_control_reconciliation.py'
    utility = re.search(r'CREATE FUNCTION public\.\{PG_CANONICAL_JSON_FUNCTION\}.*?\n\$\$',
        migration.read_text(), re.S).group().replace('{PG_CANONICAL_JSON_FUNCTION}',
        'rsc_canonical_reconciliation_json_0026').replace('{{','{').replace('}}','}')
    binding[str(migration.relative_to(CLOUD))] = sha(migration)
    participants = (*LEGACY_TABLES, INITIAL_SEAL, NAME, 'stock_condition_settlement_requests',
        'audit_events','state_transition_events','outbox_events','notification_events')
    assert len(set(participants)) == len(participants)
    with native_cluster(postgres_bin=BIN, artifact_root=CLOUD/'artifacts/decision-seal-install-complete') as (directory, engines):
        owner = engines['star_oam_migrator']
        metadata.create_all(owner)
        with owner.begin() as db:
            db.execute(text(utility))
        table, statements = runpy.run_path(str(FOLDER/'decision_seals.py'))['statements'](
            metadata, additional_request_tables=('stock_condition_settlement_requests',))
        compiled = '\n\n'.join(s.rstrip().rstrip(';')+';' for s in statements)+'\n'
        assert not re.search(r'__[A-Z_]+__', compiled)
        (directory/'installed.sql').write_text(compiled)
        # The installer must add the exact referenced unique key to an already
        # existing cases table, not just mutate Python metadata.
        with owner.connect() as db:
            before = db.scalar(text('SELECT count(*) FROM pg_proc WHERE pronamespace=\'public\'::regnamespace'))
            db.rollback()
            transaction = db.begin()
            for statement in statements:
                db.execute(text(statement))
            assert db.scalar(text('SELECT count(*) FROM pg_constraint WHERE conname=:name'), {'name':SOURCE_CONSTRAINT}) == 1
            transaction.rollback()
            assert db.scalar(text('SELECT to_regclass(:name)'), {'name':'public.'+NAME}) is None
            assert db.scalar(text('SELECT count(*) FROM pg_constraint WHERE conname=:name'), {'name':SOURCE_CONSTRAINT}) == 0
            assert db.scalar(text('SELECT count(*) FROM pg_proc WHERE pronamespace=\'public\'::regnamespace')) == before
        with owner.begin() as db:
            for statement in statements:
                db.execute(text(statement))
        checked = []
        with owner.connect() as db:
            triggers = db.execute(text("SELECT c.relname,t.tgname,t.tgenabled,t.tgdeferrable,t.tginitdeferred "
                "FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid WHERE NOT t.tgisinternal "
                "AND t.tgname LIKE 'condition_decision_seal_%' ORDER BY c.relname,t.tgname")).all()
            expected = {(name,'condition_decision_seal_'+suffix) for name in participants for suffix in ('lock','fence')}
            expected |= {(NAME,'condition_decision_seal_input_and_immutable'),(NAME,'condition_decision_seal_no_truncate')}
            assert {(row[0],row[1]) for row in triggers} == expected
            assert all(row[2]=='A' for row in triggers)
            assert all(row[3] and row[4] for row in triggers if row[1].endswith('_fence'))
            functions = db.execute(text("SELECT p.oid::regprocedure::text,p.prosecdef,p.proconfig FROM pg_proc p "
                "WHERE p.pronamespace='public'::regnamespace AND "
                "(p.proname LIKE 'rsc_condition_decision_seal_%' OR p.proname IN "
                "('rsc_check_condition_decision_seal','rsc_register_condition_decision_seal'))")).all()
            assert len(functions)==15
            for signature, security_definer, settings in functions:
                assert any(s.startswith('search_path=pg_catalog') for s in settings)
                for role in ('star_oam_api','star_oam_backup','star_oam_projector','star_oam_edge','edge_inbox'):
                    permitted = role=='star_oam_api' and signature.startswith('rsc_register_condition_decision_seal(')
                    assert db.scalar(text('SELECT has_function_privilege(:role,:fn,\'EXECUTE\')'),dict(role=role,fn=signature)) == permitted
                    checked.append(dict(function=signature,role=role,execute=permitted))
            for role in ('star_oam_api','star_oam_backup','star_oam_projector','star_oam_edge','edge_inbox'):
                for privilege in ('SELECT','INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER'):
                    permitted = privilege=='SELECT' and role in ('star_oam_api','star_oam_backup')
                    assert db.scalar(text('SELECT has_table_privilege(:role,:name,:priv)'),dict(role=role,name=NAME,priv=privilege)) == permitted
        # Actual API attempts, not only catalog booleans; all must fail before
        # the missing historical business helpers could be reached.
        for sql in (f'DELETE FROM public.{NAME}', f'TRUNCATE public.{NAME}',
                "SELECT public.rsc_condition_decision_seal_canonical('{}'::jsonb)"):
            with pytest.raises(DBAPIError) as error:
                with engines['star_oam_api'].begin() as db:
                    db.execute(text(sql))
            assert error.value.orig.sqlstate=='42501'
        with engines['star_oam_api'].connect() as db:
            assert db.scalar(text('SELECT count(*) FROM public.'+NAME))==0
        assert all(sha(CLOUD/p)==digest for p,digest in binding.items())
        (directory/'checks.json').write_text(json.dumps(dict(passed=True,source=binding,
            sourceConstraintAltered=True,transactionalRollback=True,triggers=[list(t) for t in triggers],
            privileges=checked,actualApiRejections=3,fullBusinessProof=False,
            scope='actual metadata, installer, catalog, ACL, rollback only; no registrar/source/late-write business calls',
            productionAcceptance=False),indent=2)+'\n')
    state=json.loads((directory/'cluster-state.json').read_text())
    assert state['status']=='stopped' and state['checks']=='passed' and state['serverExitCode']==0
    print('decision seal installer native receipt:',directory,flush=True)
