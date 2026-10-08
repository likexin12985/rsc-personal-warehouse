"""Historical and maintenance sources must never become interchangeable."""
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile

import pytest

CLOUD = Path(__file__).resolve().parents[2]


@pytest.fixture
def builder(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('rollback_builder', CLOUD / 'scripts/prepare_condition_predecessor.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # Small explicit archive fixture; the real pinned Git archive is exercised
    # by the native history gate. This tests packaging, separation and refusal.
    files = {
        module.ROLLBACK_FILE: b"guard = '''WHERE " + module.ROLLBACK_BEFORE + b"'''\n",
        'cloud_oam/backend/app/unchanged.py': b'unchanged = True\n',
    }
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w') as archive:
        for name, content in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(content)
            member.mode = 0o644
            archive.addfile(member, io.BytesIO(content))
    raw = buffer.getvalue()
    monkeypatch.setattr(module, 'reviewed_archive', lambda *_: (raw, dict(files)))
    commit, _, _, head = module.SOURCES['0164']
    monkeypatch.setitem(module.SOURCES, '0164', (commit, module.digest(raw), len(files), head))
    return module, tmp_path, files, raw


def test_maintenance_build_changes_only_guard_and_preserves_pristine_archive(builder):
    module, repository, files, raw = builder
    original = repository / 'cloud_oam/artifacts/original'
    rollback = repository / 'cloud_oam/artifacts/rollback'
    pristine = module.prepare(repository, original, '0164')
    maintained = module.prepare(repository, rollback, '0164', rollback_compatibility=True)
    assert 'rollbackCompatibility' not in pristine
    assert maintained['rollbackCompatibility']['profile'] == module.ROLLBACK_PROFILE
    assert (original / 'source.tar').read_bytes() == (rollback / 'source.tar').read_bytes() == raw
    differences = []
    with tarfile.open(rollback / 'rollback-source.tar') as archive:
        assert set(archive.getnames()) == set(files)
        for name, old in files.items():
            assert (original / 'source' / name).read_bytes() == old
            new = (rollback / 'source' / name).read_bytes()
            assert archive.extractfile(name).read() == new
            if old != new:
                differences.append(name)
                assert new == old.replace(module.ROLLBACK_BEFORE, module.ROLLBACK_AFTER)
    assert differences == [module.ROLLBACK_FILE]
    assert module.prepare(repository, rollback, '0164', rollback_compatibility=True) == maintained
    with pytest.raises(ValueError, match='receipt'):
        module.prepare(repository, rollback, '0164')
    with pytest.raises(ValueError, match='receipt'):
        module.prepare(repository, original, '0164', rollback_compatibility=True)


@pytest.mark.parametrize('target', ['source.tar', 'rollback-source.tar', 'source/cloud_oam/backend/app/unchanged.py', 'manifest.json'])
def test_existing_build_tampering_is_rejected_without_repair(builder, target):
    module, repository, _, _ = builder
    destination = repository / 'cloud_oam/artifacts/rollback'
    module.prepare(repository, destination, '0164', rollback_compatibility=True)
    path = destination / target
    damaged = b'{}\n' if target == 'manifest.json' else b'unreviewed bytes\n'
    path.write_bytes(damaged)
    with pytest.raises(ValueError):
        module.prepare(repository, destination, '0164', rollback_compatibility=True)
    assert path.read_bytes() == damaged


def test_wrong_revision_never_creates_compatibility_source(builder):
    module, repository, _, _ = builder
    destination = repository / 'cloud_oam/artifacts/wrong'
    with pytest.raises(ValueError, match='only to reviewed 0164'):
        module.prepare(repository, destination, '0157', rollback_compatibility=True)
    assert not destination.exists()


@pytest.mark.parametrize('flags', [[], ['--runtime-catalog-only'], ['--legacy-history-only']])
def test_rollback_cli_requires_explicit_original_history_source(flags):
    result = subprocess.run([sys.executable, str(CLOUD / 'scripts/run_local_pg16_scrap_business_checks.py'),
        '--postgres-bin', '/unused', '--rollback-source', '/unused', *flags],
        capture_output=True, text=True, timeout=15)
    assert result.returncode == 2
    assert 'requires' in result.stderr
    assert 'Traceback' not in result.stderr
