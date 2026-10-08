"""Read-only startup of a preserved old application in its own Python process.

Only an owned local gate passes its API engine here. The current production
application never switches versions or admits predecessor schemas.
"""
import hashlib
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
from sqlalchemy import text

CLOUD = Path(__file__).resolve().parents[2]
PROGRAM = '''
import os, runpy
runpy.run_path('backend/tests/conftest.py')
from sqlalchemy import create_engine, text
from app.database_security import validate_production_database_security
engine = create_engine(os.environ['RSC_OWNED_TEST_API_URL'])
try:
    with engine.connect() as db:
        assert db.scalar(text('SELECT current_database()')) == 'rsc_pg16_release_gate'
        assert db.scalar(text('SELECT current_user')) == 'star_oam_api'
    validate_production_database_security(engine, expected_runtime_role='star_oam_api',
                                          expected_migration_role='star_oam_migrator')
    print('preserved 0164 full API startup PASS')
finally:
    engine.dispose()
'''


def validate_source(cloud):
    cloud = Path(cloud).resolve()
    if cloud.name != 'cloud_oam' or cloud == CLOUD:
        raise ValueError('explicit preserved predecessor source required')
    manifest = cloud.parent.parent / 'manifest.json'
    receipt = json.loads((cloud.parent.parent / 'receipt.json').read_text())
    raw = manifest.read_bytes()
    sources = json.loads(raw)
    if receipt.get('formalHead') != '20261213_0164' or receipt.get('sourceFiles') != len(sources):
        raise ValueError('predecessor source receipt mismatch')
    recorded_digest = receipt.get('manifestSha256')
    if recorded_digest is not None and recorded_digest != hashlib.sha256(raw).hexdigest():
        raise ValueError('predecessor source manifest digest mismatch')
    for original, digest in sources.items():
        path = Path(original)
        relative = path.relative_to(CLOUD.parent) if path.is_absolute() else path
        copied = cloud.parent / relative
        if (copied.is_symlink() or not copied.resolve().is_relative_to(cloud.parent)
                or hashlib.sha256(copied.read_bytes()).hexdigest() != digest):
            raise ValueError('preserved predecessor source drift: ' + str(relative))
    return dict(sourceFiles=len(sources), manifestSha256=hashlib.sha256(raw).hexdigest())


def validate_rollback_pair(predecessor, rollback):
    """Recheck the pristine generator and separately labelled maintenance build.

    Never patch preserved source at runtime or accept a receipt alone: rederive
    every expected byte from the pinned Git archive before admitting either.
    """
    predecessor, rollback = Path(predecessor).resolve(), Path(rollback).resolve()
    if predecessor == rollback:
        raise ValueError('original predecessor and maintenance rollback must be separate')
    validate_source(predecessor)
    validate_source(rollback)
    builder = runpy.run_path(str(CLOUD / 'scripts/prepare_condition_predecessor.py'))
    original = builder['prepare'](CLOUD.parent, predecessor.parent.parent, '0164')
    maintained = builder['prepare'](CLOUD.parent, rollback.parent.parent, '0164',
                                    rollback_compatibility=True)
    return dict(original=original, rollback=maintained,
                originalGitRollbackSupported=False)


def admission(engines, *, cloud, directory, label='preserved-0164-runtime'):
    cloud = Path(cloud).resolve()
    proof = validate_source(cloud)
    engine = engines['star_oam_api']
    if engine.url.database != 'rsc_pg16_release_gate' or engine.url.username != 'star_oam_api':
        raise ValueError('owned local API gate engine required')
    # The API deliberately cannot SELECT alembic_version. Check revision as
    # the migrator and keep the child's real API grants unchanged.
    with engines['star_oam_migrator'].connect() as db:
        if db.scalar(text('SELECT version_num FROM alembic_version')) != '20261213_0164':
            raise ValueError('preserved application requires exact 0164 database')
    environment = dict(os.environ, PYTHONPATH=str(cloud / 'backend') + os.pathsep + str(cloud / 'backend/tests'),
                       RSC_OWNED_TEST_API_URL=engine.url.render_as_string(hide_password=False))
    if label not in ('preserved-0164-runtime', 'pristine-0164-before-upgrade'):
        raise ValueError('unrecognized predecessor admission evidence label')
    path = directory / (label + '.log')
    with path.open('wb') as log:
        result = subprocess.run([sys.executable, '-c', PROGRAM], cwd=cloud, env=environment,
                                stdout=log, stderr=subprocess.STDOUT, timeout=120)
    if result.returncode or 'preserved 0164 full API startup PASS' not in path.read_text():
        raise AssertionError('preserved 0164 startup failed; inspect owned gate log')
    assert validate_source(cloud) == proof
    receipt = json.loads((cloud.parent.parent / 'receipt.json').read_text())
    return dict(passed=True, revision='20261213_0164',
        rollbackCompatibility=receipt.get('rollbackCompatibility'), **proof)


def populate_history(engines, *, cloud, directory, tracking):
    """Run an unchanged old service/fixture stack; retain only synthetic data."""
    cloud = Path(cloud).resolve()
    proof = validate_source(cloud)
    output = (directory / 'preserved-0164-requests.json').resolve()
    if output.exists():
        raise ValueError('old business history must be generated once in a fresh owned cluster')
    roles = ('star_oam_migrator', 'star_oam_api', 'edge_inbox')
    for role in roles:
        if engines[role].url.database != 'rsc_pg16_release_gate' or engines[role].url.username != role:
            raise ValueError('owned local history gate engines required')
    environment = dict(os.environ, PYTHONPATH=str(cloud / 'backend') + os.pathsep + str(cloud / 'backend/tests'),
        RSC_OWNED_TEST_ENGINE_URLS=json.dumps({role: engines[role].url.render_as_string(hide_password=False) for role in roles}),
        RSC_OWNED_HISTORY_TRACKING=tracking, RSC_OWNED_HISTORY_OUTPUT=str(output))
    program = Path(__file__).with_name('pg16_preserved_loss_history.py').read_text()
    with (directory / 'preserved-0164-history.log').open('wb') as log:
        result = subprocess.run([sys.executable, '-c', program], cwd=cloud, env=environment,
                                stdout=log, stderr=subprocess.STDOUT, timeout=1200)
    if result.returncode or not output.exists():
        raise AssertionError('preserved 0164 history creation failed; inspect owned gate log')
    assert validate_source(cloud) == proof
    history = json.loads(output.read_text())
    if history['revision'] != '20261213_0164' or history['tracking'] != tracking or len(history['committedCommands']) != 11:
        raise AssertionError('incomplete committed predecessor history')
    return dict(history=history, source=proof, outputSha256=hashlib.sha256(output.read_bytes()).hexdigest())
