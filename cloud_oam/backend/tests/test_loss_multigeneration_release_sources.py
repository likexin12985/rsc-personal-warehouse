"""Independent current-runtime and complete historical migration source links."""
from pathlib import Path
import hashlib
import runpy

from app import database_security, oam_sync_scope_security
import test_postgresql16_release_gate as gate


def test_current_readiness_hash_comes_from_0163_migration_and_runtime_contract():
    source = runpy.run_path(str(Path(__file__).parents[1] /
        'alembic/versions/20261212_0163_loss_return_stops.py'))
    assert gate.HEAD_REVISION == source['revision'] == '20261212_0163'
    assert source['down_revision'] == '20261211_0162'
    signature = 'public.rsc_oam_runtime_binding_ready_0044()'
    before, after = source['_sources']()[signature]
    transition = next(row for row in source['_support']()['DATA']['replacedFunctions']
        if 'public.' + row['signature'] == signature)
    assert hashlib.sha256(before.encode()).hexdigest() == hashlib.sha256(transition['before_prosrc'].encode()).hexdigest()
    assert hashlib.sha256(after.encode()).hexdigest() == transition['prosrcSha256']
    assert gate._head_runtime_ready_hash() == transition['prosrcSha256']
    assert oam_sync_scope_security.OAM_SYNC_FUNCTION_MANIFEST[signature.removeprefix('public.')][-1] == transition['prosrcSha256']


def test_current_account_admission_hash_follows_all_successor_patches():
    # A current-head gate must follow 0159's admission change even though its
    # historical source anchor was introduced by 0155.
    coordinate = ('rsc_require_opening_observation_account_0023', '')
    hashes = {**database_security.RUNTIME_FUNCTION_BODY_SHA256,
        **database_security.FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256}
    assert gate._head_account_admission_hash() == hashes[coordinate]
