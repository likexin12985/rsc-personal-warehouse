import hashlib
from pathlib import Path
import runpy

from alembic.config import Config
from alembic.script import ScriptDirectory
import pytest

from pg16_legacy_migration_graph import historical_migration_graph


def _repository(root):
    versions = root / 'backend/alembic/versions'
    versions.mkdir(parents=True)
    (versions.parent / 'env.py').write_text('pass\n')
    (root / 'alembic.ini').write_text(
        '[alembic]\nscript_location = %(here)s/backend/alembic\n'
        'prepend_sys_path = %(here)s/backend\npath_separator = os\n'
    )
    return versions


def _revision(directory, name, parent=None, dependency=None, extra=''):
    path = directory / (name + '.py')
    path.write_text(f'revision = {name!r}\ndown_revision = {parent!r}\n'
        f'branch_labels = None\ndepends_on = {dependency!r}\n' + extra)
    return path


def test_exact_ancestors_and_dependencies_execute_without_later_catalogs(tmp_path):
    versions = _repository(tmp_path)
    _revision(versions, 'ancestor')
    dependency = _revision(versions, 'dependency', 'ancestor')
    target = _revision(versions, 'target', 'ancestor', 'dependency', 'values = []\n')
    _revision(versions, 'future', 'target', extra='raise AssertionError("later catalog executed")\n')
    with historical_migration_graph(tmp_path, revision='target') as graph:
        config_path = graph.config_path
        scripts = ScriptDirectory.from_config(Config(str(config_path)))
        assert {row.revision for row in scripts.walk_revisions()} == {'ancestor', 'dependency', 'target'}
        assert dict(graph.sources)[target.name] == hashlib.sha256(target.read_bytes()).hexdigest()
        assert dict(graph.sources)[dependency.name] == hashlib.sha256(dependency.read_bytes()).hexdigest()
        copy = config_path.parent / 'versions' / target.name
        assert copy.read_bytes() == target.read_bytes()
        one = runpy.run_path(str(copy)); two = runpy.run_path(str(copy))
        one['values'].append(1)
        assert two['values'] == []
        assert not (copy.parent / 'future.py').exists()
    assert not config_path.exists()
    assert target.exists()


@pytest.mark.parametrize('case', ['missing', 'cycle', 'duplicate', 'dynamic', 'unknown'])
def test_incomplete_or_ambiguous_graph_is_rejected_before_yield(tmp_path, case):
    versions = _repository(tmp_path)
    target = _revision(versions, 'target')
    if case == 'missing':
        _revision(versions, 'target', 'absent')
    elif case == 'cycle':
        _revision(versions, 'target', 'other'); _revision(versions, 'other', 'target')
    elif case == 'duplicate':
        (versions / 'duplicate.py').write_bytes(target.read_bytes())
    elif case == 'dynamic':
        target.write_text(target.read_text().replace("revision = 'target'", "revision = str('target')"))
    with pytest.raises(ValueError):
        with historical_migration_graph(tmp_path, revision='unknown' if case == 'unknown' else 'target'):
            pytest.fail('invalid graph must not reach a migration')


@pytest.mark.parametrize('which', ['original', 'copy', 'configuration'])
def test_changed_source_or_execution_copy_cannot_be_reported_as_verified(tmp_path, which):
    versions = _repository(tmp_path)
    target = _revision(versions, 'target')
    with pytest.raises(RuntimeError, match='source changed'):
        with historical_migration_graph(tmp_path, revision='target') as graph:
            selected = {'original': target,
                'copy': graph.config_path.parent / 'versions' / target.name,
                'configuration': graph.config_path}[which]
            selected.write_text(selected.read_text() + '\n# changed\n')
    assert not graph.config_path.exists()


def test_body_exception_keeps_its_identity_and_cleans_owned_copy(tmp_path):
    versions = _repository(tmp_path); _revision(versions, 'target')
    error = RuntimeError('migration rejected exact historical facts')
    with pytest.raises(RuntimeError) as caught:
        with historical_migration_graph(tmp_path, revision='target') as graph:
            raise error
    assert caught.value is error
    assert not graph.config_path.exists()


def test_real_0051_graph_has_every_historical_revision_and_no_newer_imports():
    cloud = Path(__file__).resolve().parents[2]
    with historical_migration_graph(cloud, revision='20260903_0051') as graph:
        scripts = ScriptDirectory.from_config(Config(str(graph.config_path)))
        assert scripts.get_heads() == ['20260903_0051']
        actual = {row.revision for row in scripts.walk_revisions()}
        assert len(actual) == len(graph.sources) == 51
        assert {int(revision.rsplit('_', 1)[1]) for revision in actual} == set(range(1, 52))
