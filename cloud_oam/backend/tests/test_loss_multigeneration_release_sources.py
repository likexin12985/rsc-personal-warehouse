"""Independent current-runtime and complete historical migration source links."""
from pathlib import Path
import hashlib
import runpy

from app import database_security, oam_sync_scope_security
import test_postgresql16_release_gate as gate


def test_current_readiness_hash_follows_0164_through_0167_and_runtime_contract():
    source = runpy.run_path(str(Path(__file__).parents[1] /
        'alembic/versions/20261213_0164_authentication_audit_fence.py'))
    assert source['revision'] == '20261213_0164'
    assert source['down_revision'] == '20261212_0163'
    signature = 'public.rsc_oam_runtime_binding_ready_0044()'
    before, after = source['_sources']()[signature]
    transition = next(patch for patch in source['_catalog']()['patches']
        if 'public.' + patch['after']['signature'] == signature)
    assert hashlib.sha256(before.encode()).hexdigest() == hashlib.sha256(transition['before']['prosrc'].encode()).hexdigest()
    assert hashlib.sha256(after.encode()).hexdigest() == transition['afterSha256']
    # Preserve the historical 0164 anchor and verify the exact forward link;
    # each successor must link its frozen before/after body without skipping a revision.
    current = runpy.run_path(str(Path(__file__).parents[1] /
        'alembic/versions/20261214_0165_stock_scrap_and_recovery.py'))
    assert current['revision'] == '20261214_0165'
    assert current['down_revision'] == source['revision']
    ready = current['_support']('readiness')['DATA']
    assert ready['previousRevision'] == source['revision']
    assert ready['revision'] == current['revision']
    assert 'public.' + ready['before']['signature'] == signature
    assert ready['after']['signature'] == ready['before']['signature']
    assert ready['before']['prosrc'] == after
    current_before, current_after = current['_sources']()[signature]
    assert (current_before, current_after) == (ready['before']['prosrc'], ready['after']['prosrc'])
    assert hashlib.sha256(current_before.encode()).hexdigest() == ready['beforeSha256'] == transition['afterSha256']
    assert hashlib.sha256(current_after.encode()).hexdigest() == ready['afterSha256']
    forward = runpy.run_path(str(Path(__file__).parents[1] /
        'alembic/versions/20261215_0166_scrap_authentication_fence.py'))
    assert forward['down_revision'] == current['revision']
    assert forward['revision'] == '20261215_0166'
    change = next(patch for patch in forward['_catalog']()['patches']
                  if 'public.' + patch['before']['signature'] == signature)
    assert change['before'] == ready['after']
    assert forward['_sources']()[signature] == (change['before']['prosrc'], change['after']['prosrc'])
    condition = runpy.run_path(str(Path(__file__).parents[1] /
        'alembic/versions/20261216_0167_return_condition_corrections.py'))
    assert condition['down_revision'] == forward['revision']
    # 0167 is the historical source-chain anchor; the current release head
    # has since advanced through the retained fulfillment migrations.
    assert condition['revision'] == '20261216_0167'
    condition_ready = condition['_support']('readiness')['DATA']
    assert condition_ready['previousRevision'] == forward['revision']
    assert condition_ready['revision'] == condition['revision']
    assert condition_ready['before'] == change['after']
    assert condition['_sources']()[signature] == (condition_ready['before']['prosrc'], condition_ready['after']['prosrc'])
    assert hashlib.sha256(condition_ready['before']['prosrc'].encode()).hexdigest() == condition_ready['beforeSha256'] == change['afterSha256']
    assert hashlib.sha256(condition_ready['after']['prosrc'].encode()).hexdigest() == condition_ready['afterSha256']
    # Compare the historical 0167 body with its frozen 0167 manifest.  The
    # current-head helper intentionally follows the later 0174/0178 chain.
    assert oam_sync_scope_security.OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0167[
        signature.removeprefix('public.')
    ][-1] == condition_ready['afterSha256']
    assert oam_sync_scope_security.OAM_SYNC_FUNCTION_MANIFEST[
        signature.removeprefix('public.')
    ][-1] == gate._head_runtime_ready_hash()


def test_current_account_admission_hash_follows_all_successor_patches():
    # A current-head gate must follow 0159's admission change even though its
    # historical source anchor was introduced by 0155.
    coordinate = ('rsc_require_opening_observation_account_0023', '')
    hashes = {**database_security.RUNTIME_FUNCTION_BODY_SHA256,
        **database_security.FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256}
    assert gate._head_account_admission_hash() == hashes[coordinate]
