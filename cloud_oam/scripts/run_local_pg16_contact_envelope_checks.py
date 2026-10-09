#!/usr/bin/env python3
"""Run only the new 0181 contact leg in an owned native PostgreSQL 16.

No DSN, port, external credential or GitHub identity is accepted. A failed
0181 upgrade may resume only through its verified stopped-cluster receipt.
The cluster controller verifies and stops its own child.
Evidence and synthetic database files remain, including after a failure.
"""
from datetime import datetime, timezone
from contextlib import contextmanager
import argparse
import hashlib
import json
import os
from pathlib import Path
import runpy
import signal
import stat
import subprocess
import sys
import tempfile
import time
import traceback
import xml.etree.ElementTree as ET


CLOUD = Path(__file__).resolve().parents[1]
OLD, HEAD = '20261229_0180', '20261230_0181'


def checked_resume(receipt_path, binaries):
    """Accept only this runner's terminal 0181 failure, never a supplied DB."""
    path = Path(receipt_path).absolute()
    evidence_root = CLOUD / 'artifacts/linux-agent-runtime-20261009'
    if path.is_symlink() or path.resolve() != path or path.name != 'receipt.json' \
            or path.parent.parent != evidence_root or not path.parent.name.startswith('native-contact-0181-'):
        raise ValueError('owned native contact receipt path required')
    prior = json.loads(path.read_text())
    if prior.get('schema') != 'rsc.native-contact-0181.v1' or prior.get('result') != 'failed' \
            or prior.get('exitCode') != 1 or prior.get('failurePhase') != 'upgrade-0181' \
            or prior.get('sourceUnchanged') is not True or prior.get('hostedGate') is not False \
            or prior.get('externalProviderAccess') is not False:
        raise ValueError('only the exact failed 0181 upgrade can resume')
    expected = [('upgrade-0180', 0), ('edge-staging-acl', 0), ('edge-staging-acl-verify', 0),
        ('oam-source-readonly-binding', 0), ('upgrade-0181', 1)]
    if [(v['stage'], v['exitCode']) for v in prior['commands']] != expected:
        raise ValueError('completed predecessor stages required')
    for command in prior['commands']:
        content = (path.parent / (command['stage'] + '.log')).read_bytes()
        if hashlib.sha256(content).hexdigest() != command['sha256']:
            raise ValueError('prior command evidence changed')
    before_path = path.parent / 'source-before.json'
    if hashlib.sha256(before_path.read_bytes()).hexdigest() != prior['sourceManifestSha256']:
        raise ValueError('prior source evidence changed')
    previous_sources = json.loads(before_path.read_text())
    # Only the new 0181 candidate and proof/runner may differ. This protects
    # the already completed 0->0180 and ACL stages from silent source drift.
    allowed = {'cloud_oam/backend/alembic/contact_envelope_0181/catalog.json',
        'cloud_oam/backend/alembic/versions/20261230_0181_material_request_contact_v2.py',
        'cloud_oam/backend/app/material_request_contact_envelope_security.json',
        'cloud_oam/backend/app/material_request_contact_envelope_security.py',
        'cloud_oam/backend/tests/pg16_contact_envelope_gate.py',
        'cloud_oam/backend/tests/test_contact_envelope_plpgsql_0181.py',
        'cloud_oam/backend/tests/test_native_contact_0181_resume_boundary.py',
        'cloud_oam/scripts/run_local_pg16_contact_envelope_checks.py'}
    current = manifest()
    changed = {name for name in previous_sources.keys() | current.keys()
        if previous_sources.get(name) != current.get(name)}
    if changed - allowed:
        raise ValueError('completed predecessor source changed')
    cluster = Path(prior['clusterDirectory'])
    if cluster.parent != path.parent / 'cluster' or not cluster.name.startswith('run-'):
        raise ValueError('receipt cluster containment required')
    state_file = cluster / 'cluster-state.json'
    state = json.loads(state_file.read_text())
    data = cluster / 'data'
    for directory in (path.parent, cluster, data):
        info = directory.lstat()
        if directory.resolve() != directory or not stat.S_ISDIR(info.st_mode) \
                or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError('private owned real cluster directory required')
    if state != prior['cluster'] or state.get('scope') != 'local-development-checks' \
            or state.get('githubReleaseGate') is not False or state.get('status') != 'stopped' \
            or state.get('serverExitCode') != 0 or state.get('checks') != 'failed' \
            or state.get('runDirectory') != str(cluster) or state.get('dataDirectory') != str(data) \
            or state.get('binarySha256') != hashlib.sha256((binaries / 'postgres').read_bytes()).hexdigest() \
            or (data / 'PG_VERSION').read_text().strip() != '16' or (data / 'postmaster.pid').exists():
        raise ValueError('unchanged stopped PG16 cluster required')
    if prior.get('failureReadback') != dict(alembic_version=1, material_requests=1,
            material_request_revisions=1, openbao_data_key_pins=0, application_key_version_claims=0,
            inventory_transactions=0, inventory_movements=0, head=[OLD]):
        raise ValueError('exact failed-upgrade readback required')
    return path, prior, state


@contextmanager
def resumed_cluster(*, receipt_path, postgres_bin, artifact_root):
    import fcntl
    _, _, previous = checked_resume(receipt_path, postgres_bin)
    lock_path = Path(previous['runDirectory']) / 'contact-resume.lock'
    with lock_path.open('a+') as lock:
        lock_path.chmod(0o600)
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with _resumed_cluster(receipt_path=receipt_path, postgres_bin=postgres_bin,
                artifact_root=artifact_root) as value:
            yield value


@contextmanager
def _resumed_cluster(*, receipt_path, postgres_bin, artifact_root):
    """Restart only the verified owned child data; never bootstrap or migrate."""
    import psycopg
    from sqlalchemy import URL, create_engine
    from sqlalchemy.pool import NullPool
    from local_pg16_cluster import _identity, DATABASE, ROLES
    if any(key.startswith('PG') for key in os.environ):
        raise ValueError('local PG16 checks require an unconfigured libpq environment')
    prior_path, prior, previous = checked_resume(receipt_path, postgres_bin)
    artifact_root.mkdir(parents=True, exist_ok=False, mode=0o700)
    data = Path(previous['dataDirectory'])
    socket_directory = Path(tempfile.mkdtemp(prefix='rsc-pg16-', dir='/tmp')).resolve()
    socket_directory.chmod(0o700)
    state = dict(scope='local-development-checks', githubReleaseGate=False,
        status='resuming', runDirectory=str(artifact_root), dataDirectory=str(data),
        socketDirectory=str(socket_directory), binarySha256=previous['binarySha256'],
        priorReceipt=str(prior_path), priorReceiptSha256=hashlib.sha256(prior_path.read_bytes()).hexdigest(),
        repeatedPredecessorStages=False, createdAt=datetime.now(timezone.utc).isoformat())
    def save():
        (artifact_root / 'cluster-state.json').write_text(json.dumps(state, indent=2) + '\n')
    save(); process = None; engines = {}
    try:
        started = datetime.now(timezone.utc)
        with (artifact_root / 'postgres.log').open('xb') as output:
            process = subprocess.Popen([str(postgres_bin / 'postgres'), '-D', str(data),
                '-c', 'listen_addresses=', '-c', 'unix_socket_directories=' + str(socket_directory),
                '-c', 'unix_socket_permissions=0700', '-c', 'log_statement=none',
                '-c', 'log_min_error_statement=panic'], stdout=output, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 30
        while True:
            if process.poll() is not None:
                raise RuntimeError('owned resume child stopped before startup')
            try:
                with psycopg.connect(host=str(socket_directory), dbname='postgres', user='postgres',
                        connect_timeout=1, autocommit=True) as db:
                    state['identity'] = _identity(db, data, process.pid, started)
                    if state['identity']['systemIdentifier'] != previous['identity']['systemIdentifier']:
                        raise RuntimeError('resumed cluster system identity changed')
                    if db.execute("SELECT datname FROM pg_database WHERE NOT datistemplate ORDER BY datname").fetchall() \
                            != [('postgres',), (DATABASE,)]:
                        raise RuntimeError('owned resume database set changed')
                    if db.execute("SELECT rolname FROM pg_roles WHERE rolname NOT LIKE 'pg\\_%' ESCAPE '\\' ORDER BY rolname").fetchall() \
                            != [(name,) for name in sorted(('postgres', *ROLES))]:
                        raise RuntimeError('owned resume role set changed')
                break
            except psycopg.OperationalError:
                if time.monotonic() >= deadline:
                    raise RuntimeError('owned resume startup timed out') from None
                time.sleep(0.1)
        for role in ROLES:
            if role == 'star_oam_edge':
                continue
            url = URL.create('postgresql+psycopg', username=role, database=DATABASE,
                query={'host': str(socket_directory)})
            engines[role] = create_engine(url, poolclass=NullPool, hide_parameters=True,
                connect_args={'connect_timeout': 5})
        state['status'] = 'running_checks'; save()
        yield artifact_root, engines
        state['checks'] = 'passed'
    except BaseException:
        state['checks'] = 'failed'
        raise
    finally:
        for engine in engines.values():
            engine.dispose()
        if process is not None and process.poll() is None:
            if not (data / 'postmaster.pid').is_file() \
                    or int((data / 'postmaster.pid').read_text().splitlines()[0]) != process.pid:
                state['status'] = 'stop_identity_mismatch'; save()
                raise RuntimeError('cannot prove resumed child identity for shutdown')
            process.send_signal(signal.SIGINT)
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                state['status'] = 'shutdown_pending'; save()
                raise RuntimeError('owned resume child still shutting down') from None
        state.update(status='stopped', serverExitCode=process.returncode if process else None,
            finishedAt=datetime.now(timezone.utc).isoformat()); save()


def manifest():
    paths = {p for p in (CLOUD / 'backend').rglob('*') if p.is_file()
        and p.suffix in ('.py', '.sql', '.json', '.lock', '.txt')
        and not {'__pycache__', '.pytest_cache'}.intersection(p.parts)}
    paths.update({Path(__file__).resolve(), CLOUD / 'alembic.ini',
        CLOUD / 'deployment/postgres-init/20-loss-uuid.sql',
        CLOUD / 'deployment/create_oam_edge_staging.sql',
        CLOUD / 'deployment/verify_oam_edge_staging.sql',
        CLOUD / 'deployment/provision_oam_work_order_source.sql',
        CLOUD.parent / '.github/workflows/postgresql16-release-gate.yml',
        CLOUD.parent / 'docs/RSC个人仓与物资运营扩展系统_正式生产版需求与架构设计_V1.0.md'})
    return {str(p.relative_to(CLOUD.parent)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(paths)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--postgres-bin', required=True)
    parser.add_argument('--resume-failed-receipt')
    args = parser.parse_args(argv)
    sys.path[:0] = [str(CLOUD / 'backend'), str(CLOUD / 'backend/tests')]
    from local_pg16_cluster import native_cluster, _checked_bin
    from sqlalchemy import text

    binaries = _checked_bin(args.postgres_bin)
    psql = binaries / 'psql'
    version = subprocess.run([str(psql), '--version'], capture_output=True, text=True,
        check=True, timeout=10).stdout.strip()
    if not version.startswith('psql (PostgreSQL) 16.'):
        raise ValueError('matching PostgreSQL16 psql required')
    if args.resume_failed_receipt:
        checked_resume(args.resume_failed_receipt, binaries)
    root = CLOUD / 'artifacts/linux-agent-runtime-20261009'
    root.mkdir(parents=True, exist_ok=True)
    evidence = Path(tempfile.mkdtemp(prefix='native-contact-0181-', dir=root))
    evidence.chmod(0o700)
    before = manifest()
    (evidence / 'source-before.json').write_text(json.dumps(before, indent=2) + '\n')
    receipt = dict(schema='rsc.native-contact-0181.v1', result='failed', exitCode=1,
        productionReady=False, hostedGate=False, suppliedDsn=False, externalProviderAccess=False,
        hardMemoryCapApplied=False, startedAt=datetime.now(timezone.utc).isoformat(),
        binaryDirectory=str(binaries), psqlVersion=version, evidenceDirectory=str(evidence))
    cluster, phase = None, 'fresh_cluster'
    started = time.monotonic()
    try:
        controller = (resumed_cluster(receipt_path=args.resume_failed_receipt, postgres_bin=binaries,
            artifact_root=evidence / 'cluster-resume') if args.resume_failed_receipt else
            native_cluster(postgres_bin=binaries, artifact_root=evidence / 'cluster'))
        with controller as (cluster, engines):
            owner, api = engines['star_oam_migrator'], engines['star_oam_api']
            receipt['clusterDirectory'] = str(cluster)
            url = owner.url.render_as_string(hide_password=False)
            # A native Unix socket connection has no password. Child settings
            # are explicit and cannot inherit unrelated cloud credentials.
            environment = {key: value for key, value in os.environ.items()
                if key in ('PATH', 'HOME', 'TMPDIR', 'LANG', 'LC_ALL')}
            environment.update(OAM_ENVIRONMENT='production', OAM_DATABASE_URL=url,
                OAM_DATABASE_EXPECTED_MIGRATION_ROLE='star_oam_migrator',
                OAM_DATABASE_EXPECTED_RUNTIME_ROLE='star_oam_api', OAM_MIGRATION_CACHE_EXECUTION='1')
            commands = []

            def command(label, arguments):
                nonlocal phase
                phase = label
                path = evidence / (label + '.log')
                with path.open('xb') as output:
                    completed = subprocess.run(arguments, cwd=CLOUD, env=environment,
                        stdout=output, stderr=subprocess.STDOUT, timeout=900)
                commands.append(dict(stage=label, exitCode=completed.returncode,
                    sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
                if completed.returncode:
                    raise RuntimeError(label + '_failed')

            try:
                if not args.resume_failed_receipt:
                    command('upgrade-0180', [sys.executable, '-m', 'alembic', '-c', 'alembic.ini', 'upgrade', OLD])
                with owner.connect() as db:
                    assert db.scalar(text('SELECT version_num FROM alembic_version')) == OLD
                base = [str(psql), '-X', '-w', '--set=ON_ERROR_STOP=1', '--dbname',
                    url.replace('postgresql+psycopg:', 'postgresql:', 1)]
                if not args.resume_failed_receipt:
                    command('edge-staging-acl', [*base, '-v', 'edge_role=edge_inbox', '-f',
                        str(CLOUD / 'deployment/create_oam_edge_staging.sql')])
                    command('edge-staging-acl-verify', [*base, '-v', 'edge_role=edge_inbox',
                        '-v', 'projector_role=star_oam_projector', '-f',
                        str(CLOUD / 'deployment/verify_oam_edge_staging.sql')])
                    command('oam-source-readonly-binding', [*base,
                        '-v', 'edge_source_instance=pg16-reviewed-edge', '-v', 'company_id=pg16-reviewed-company',
                        '-v', 'org_code=pg16-reviewed-org', '-v', 'scope_key=work-orders:recent-30d',
                        '-f', str(CLOUD / 'deployment/provision_oam_work_order_source.sql')])
                runpy.run_path(str(CLOUD / 'backend/tests/conftest.py'))
                from app.database_security import validate_production_database_security
                from pg16_contact_envelope_gate import run

                def upgrade():
                    nonlocal phase
                    command('upgrade-0181', [sys.executable, '-m', 'alembic', '-c', 'alembic.ini', 'upgrade', HEAD])
                    phase = 'contact_0181_after_upgrade'

                def validate():
                    validate_production_database_security(api, expected_runtime_role='star_oam_api',
                        expected_migration_role='star_oam_migrator')

                phase = 'contact_0181_real_proof'
                receipt['proof'] = run(owner, api, upgrade_to_head=upgrade, validate_runtime=validate,
                    resume_predecessor=bool(args.resume_failed_receipt))
                assert receipt['proof']['result'] == 'passed'
                assert manifest() == before, 'source changed during owned contact proof'
                receipt.update(result='passed', exitCode=0)
            except BaseException:
                # Re-query this exact owned database before its controller
                # stops it. Do not replay an interrupted writer or migration.
                try:
                    with owner.connect() as db:
                        db.execute(text('SET TRANSACTION READ ONLY'))
                        db.execute(text("SET LOCAL statement_timeout='5s'"))
                        state = {}
                        for table in ('alembic_version', 'material_requests', 'material_request_revisions',
                            'openbao_data_key_pins', 'application_key_version_claims',
                            'inventory_transactions', 'inventory_movements'):
                            if db.scalar(text('SELECT to_regclass(:name)'), {'name': 'public.' + table}) is not None:
                                state[table] = db.scalar(text('SELECT count(*) FROM public.' + table))
                        if 'alembic_version' in state:
                            state['head'] = db.execute(text('SELECT version_num FROM alembic_version')).scalars().all()
                        receipt['failureReadback'] = state
                except BaseException as read_error:
                    receipt['failureReadbackErrorType'] = type(read_error).__name__
                raise
            finally:
                receipt['commands'] = commands
    except BaseException as error:
        receipt.update(result='failed', exitCode=1, failurePhase=phase, failureType=type(error).__name__,
            frames=[dict(file=f.filename, line=f.lineno, function=f.name)
                for f in traceback.extract_tb(error.__traceback__)])
        if type(error) in (RuntimeError, AssertionError, ValueError):
            receipt['failureLabel'] = str(error)
        if getattr(error, 'orig', None) is not None:
            receipt['sqlstate'] = getattr(error.orig, 'sqlstate', None)
    finally:
        after = manifest()
        (evidence / 'source-after.json').write_text(json.dumps(after, indent=2) + '\n')
        receipt['sourceUnchanged'] = before == after
        if before != after:
            receipt.update(result='failed', exitCode=1, failurePhase='source_drift')
        receipt['sourceFileCount'] = len(before)
        receipt['sourceManifestSha256'] = hashlib.sha256((evidence / 'source-before.json').read_bytes()).hexdigest()
        if cluster is not None:
            receipt['cluster'] = json.loads((cluster / 'cluster-state.json').read_text())
            if receipt['cluster'].get('status') != 'stopped' or receipt['cluster'].get('serverExitCode') != 0:
                receipt.update(result='failed', exitCode=1, failurePhase='owned_cluster_cleanup')
        receipt['finishedAt'] = datetime.now(timezone.utc).isoformat()
        receipt['elapsedSeconds'] = round(time.monotonic() - started, 3)
        suite = ET.Element('testsuite', name='native-contact-0181', tests='1',
            failures=str(int(receipt['exitCode'] != 0)), errors='0', skipped='0', time=str(receipt['elapsedSeconds']))
        case = ET.SubElement(suite, 'testcase', classname='owned_pg16', name='contact_0181', time=str(receipt['elapsedSeconds']))
        if receipt['exitCode']:
            ET.SubElement(case, 'failure', message=receipt.get('failurePhase', phase)).text = receipt.get('failureType', 'unknown')
        ET.ElementTree(suite).write(evidence / 'result.xml', encoding='utf-8', xml_declaration=True)
        (evidence / 'receipt.json').write_text(json.dumps(receipt, indent=2, sort_keys=True) + '\n')
        print(json.dumps(dict(result=receipt['result'], exitCode=receipt['exitCode'], evidenceDirectory=str(evidence),
            sourceUnchanged=receipt['sourceUnchanged'], phase=receipt.get('failurePhase', 'complete'))), flush=True)
    return receipt['exitCode']


if __name__ == '__main__':
    raise SystemExit(main())
