#!/usr/bin/env python3
"""Run the shared control gates on fresh or previously published owned PG16.

Accept no DSN and never execute the destructive hosted release driver. Existing
publication history is created by the same real fixture used before these CI
gates, with synthetic external transport and without production credentials.
"""
import argparse
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import traceback

CLOUD = Path(__file__).resolve().parents[1]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--postgres-bin', required=True)
    parser.add_argument('--history', required=True, choices=('fresh', 'published'))
    args = parser.parse_args(argv)
    sys.path[:0] = [str(CLOUD/'backend'), str(CLOUD/'backend/tests')]
    runpy.run_path(str(CLOUD/'backend/tests/conftest.py'))
    from sqlalchemy import select, text
    from sqlalchemy.orm import Session
    from local_pg16_cluster import native_cluster
    from run_local_pg16_stock_loss_sources_checks import manifest
    from app.database_security import validate_production_database_security
    from app.edge_database_security import verify_edge_database_boundary
    from app.foundation_models import Role, SourceSystem
    from test_formal_access import make_organization, make_user, assign
    from pg16_gate_progress import run_gate_phase
    from pg16_opening_publication_fixture import prepare_stocktake_inventory
    from pg16_inventory_control_preparation_gate import assert_inventory_control_preparation_gate, snapshot
    from pg16_inventory_control_authority_gate import assert_inventory_control_authority_gate
    from pg16_inventory_control_attestation_gate import assert_inventory_control_attestation_gate
    from pg16_inventory_control_mapping_gate import assert_inventory_control_mapping_gate
    from pg16_material_capture_gate import assert_material_capture_gate
    from pg16_material_source_authority_gate import assert_material_source_authority_gate

    frozen = manifest()
    directory = None
    try:
        with native_cluster(postgres_bin=args.postgres_bin,
                artifact_root=CLOUD/'artifacts/local-control-preparation-pg16/checks') as (directory, engines):
            (directory/'source-manifest.json').write_text(json.dumps(frozen, indent=2)+'\n')
            owner, api, edge, projector, backup = (engines[name] for name in (
                'star_oam_migrator', 'star_oam_api', 'edge_inbox', 'star_oam_projector', 'star_oam_backup'))
            url = owner.url.render_as_string(hide_password=False)
            environment = dict(os.environ, OAM_ENVIRONMENT='production', OAM_DATABASE_URL=url,
                OAM_DATABASE_EXPECTED_MIGRATION_ROLE='star_oam_migrator', OAM_DATABASE_EXPECTED_RUNTIME_ROLE='star_oam_api')
            for label, command in (
                ('migration', [sys.executable, '-m', 'alembic', '-c', 'alembic.ini', 'upgrade', 'head']),
                ('edge-provision', [str(Path(args.postgres_bin).resolve()/'psql'), '-X', '-w', '--set=ON_ERROR_STOP=1',
                    '--dbname', url.replace('postgresql+psycopg:', 'postgresql:', 1), '-v', 'edge_role=edge_inbox',
                    '-f', str(CLOUD/'deployment/create_oam_edge_staging.sql')]),
            ):
                with (directory/(label+'.log')).open('wb') as output:
                    subprocess.run(command, cwd=CLOUD, env=environment, stdout=output,
                                   stderr=subprocess.STDOUT, check=True, timeout=600)

            def security(engine):
                validate_production_database_security(engine, expected_runtime_role='star_oam_api',
                                                      expected_migration_role='star_oam_migrator')

            security(api)
            with Session(owner) as db:
                hq = make_organization(db, name='Synthetic control predecessor HQ')
                region = make_organization(db, name='Synthetic control predecessor region', parent=hq)
                admin, _ = make_user(db, hq, name='Synthetic control reviewer')
                manager, _ = make_user(db, region, name='Synthetic control counter')
                roles = {row.code: row for row in db.scalars(select(Role))}
                assign(db, admin, roles['admin'], scope_type='national', scope_id='*')
                assign(db, manager, roles['provincial_manager'], scope_type='organization', scope_id=str(region.id))
                if not db.scalar(select(SourceSystem.id).where(SourceSystem.code == 'oam')):
                    db.add(SourceSystem(code='oam', name='Synthetic OAM control', mode='read_only', enabled=True))
                db.commit()
                admin_id, manager_id = admin.id, manager.id
            if args.history == 'published':
                run_gate_phase('predecessor_publication', lambda: prepare_stocktake_inventory(
                    owner, edge, actor_user_id=admin_id, assignee_user_id=manager_id))
            counts_before = tuple(map(len, snapshot(owner)))
            assert counts_before == ((0, 0, 0, 0, 0) if args.history == 'fresh' else (1, 1, 2, 2, 2))
            phases = (
                ('preparation', lambda: assert_inventory_control_preparation_gate(owner, edge, api, projector, backup, security)),
                ('authority', lambda: assert_inventory_control_authority_gate(owner, api, edge, projector, backup)),
                ('attestation', lambda: assert_inventory_control_attestation_gate(owner, edge, api, projector, backup, security, verify_edge_database_boundary)),
                ('mapping', lambda: assert_inventory_control_mapping_gate(owner, api, edge, projector, backup)),
                ('material_capture', lambda: assert_material_capture_gate(owner, edge, api, projector, backup, security, verify_edge_database_boundary)),
                ('material_authority', lambda: assert_material_source_authority_gate(owner, api, edge, projector, backup)),
            )
            completed = []
            for label, check in phases:
                run_gate_phase(label, check)
                completed.append(label)
            if args.history == 'published':
                # Continue in the hosted order to expose shared-history issues
                # beyond the first previously failing assertion.
                from pg16_material_source_time_gate import assert_material_source_time_gate
                from pg16_material_projection_gate import assert_material_projection_gate
                from pg16_material_source_proof_gate import assert_material_source_proof_gate
                from pg16_inventory_control_normalization_gate import assert_inventory_control_normalization_gate
                fixture_object_id = None
                for label, check in (
                    ('material_source_time', lambda: assert_material_source_time_gate(owner, api, backup)),
                    ('material_projection', lambda: assert_material_projection_gate(owner, api, edge, projector, backup)),
                    ('material_source_proof', lambda: assert_material_source_proof_gate(owner, api, edge, projector, backup, fixture_object_id=fixture_object_id)),
                    ('control_normalization_and_publication', lambda: assert_inventory_control_normalization_gate(
                        owner, edge, api, projector, backup, fixture_object_id=fixture_object_id, publication_check=True)),
                ):
                    outcome = run_gate_phase(label, check)
                    if label == 'material_projection':
                        fixture_object_id = outcome
                    completed.append(label)
            security(api)
            assert manifest() == frozen, 'release sources changed during native checks'
            with owner.connect() as connection:
                head = connection.scalar(text('SELECT version_num FROM alembic_version'))
            result = dict(status='passed', migrationHead=head, history=args.history, predecessorCounts=counts_before,
                completedPhases=completed, sourceDrift=[], actualPostgreSQL16=True,
                githubReleaseGate=False, productionAcceptance=False)
            (directory/'checks.json').write_text(json.dumps(result, indent=2)+'\n')
        state = json.loads((directory/'cluster-state.json').read_text())
        assert state['status']=='stopped' and state['checks']=='passed' and state['serverExitCode']==0
        print(json.dumps(dict(result, evidenceDirectory=str(directory))), flush=True)
        return 0
    except BaseException as error:
        result = dict(status='failed', errorType=type(error).__name__,
            evidenceDirectory=str(directory) if directory else None,
            frames=[dict(file=f.filename, line=f.lineno, function=f.name) for f in traceback.extract_tb(error.__traceback__)])
        if directory:
            (directory/'failure.json').write_text(json.dumps(result, indent=2)+'\n')
        print(json.dumps(result), flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
