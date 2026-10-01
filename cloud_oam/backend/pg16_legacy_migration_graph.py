"""Exact predecessor copies for constructing disposable historical fixtures.

This is not a production migration shortcut. A historical seed must execute
every ancestor of its explicit revision; its subsequent upgrade uses the
unmodified current graph. Reading later revision metadata must not execute
their recursively imported SQL catalogs just to build the old fixture.
"""
from contextlib import contextmanager
from configparser import ConfigParser
from dataclasses import dataclass
import ast
import hashlib
from pathlib import Path
import tempfile


_FIELDS = frozenset({'revision', 'down_revision', 'branch_labels', 'depends_on'})


@dataclass(frozen=True)
class HistoricalMigrationGraph:
    config_path: Path
    revision: str
    sources: tuple[tuple[str, str], ...]


def _metadata(path, content):
    values = {}
    for node in ast.parse(content, filename=str(path)).body:
        if isinstance(node, ast.AnnAssign):
            targets = [node.target]
        elif isinstance(node, ast.Assign):
            targets = node.targets
        else:
            continue
        for target in targets:
            if isinstance(target, ast.Name) and target.id in _FIELDS:
                if target.id in values:
                    raise ValueError('duplicate migration metadata assignment')
                try:
                    values[target.id] = ast.literal_eval(node.value)
                except (ValueError, TypeError) as error:
                    raise ValueError('historical graph requires literal migration metadata') from error
    if not _FIELDS <= values.keys() or not isinstance(values['revision'], str) or not values['revision']:
        raise ValueError('historical graph requires complete explicit revision metadata')
    for field in _FIELDS - {'revision'}:
        value = values[field]
        if value is not None and not (
            isinstance(value, str) and value
            or isinstance(value, (tuple, list)) and all(isinstance(item, str) and item for item in value)
        ):
            raise ValueError('invalid historical migration dependency metadata')
    return values


def _references(value):
    return () if value is None else (value,) if isinstance(value, str) else tuple(value)


@contextmanager
def historical_migration_graph(cloud_root: Path, *, revision: str):
    """Yield an owned, temporary Alembic configuration containing exact ancestors.

    No source is executed to discover the dependency graph. Both predecessor
    and explicit dependency edges are followed; missing/duplicate/cyclic
    revisions fail before any migration. Copied bytes and original files are
    checked again after use. This context never accepts a database URL.
    """
    root = cloud_root.resolve(strict=True)
    directory = root / 'backend/alembic'
    original = {}
    nodes = {}
    for path in sorted((directory / 'versions').glob('*.py')):
        if path.name == '__init__.py':
            continue
        if path.is_symlink():
            raise ValueError('historical migration sources must be regular files')
        content = path.read_bytes()
        metadata = _metadata(path, content)
        identifier = metadata['revision']
        if identifier in nodes:
            raise ValueError('duplicate historical migration revision')
        nodes[identifier] = (path, content, metadata)
        original[path] = content
    if not isinstance(revision, str) or revision not in nodes:
        raise ValueError('an exact existing historical revision is required')
    selected, pending = set(), set()

    def visit(identifier):
        if identifier in pending:
            raise ValueError('cyclic historical migration dependencies')
        if identifier in selected:
            return
        if identifier not in nodes:
            raise ValueError('missing historical migration predecessor')
        pending.add(identifier)
        metadata = nodes[identifier][2]
        for parent in (*_references(metadata['down_revision']), *_references(metadata['depends_on'])):
            visit(parent)
        pending.remove(identifier)
        selected.add(identifier)

    visit(revision)
    original_config = root / 'alembic.ini'
    original[original_config] = original_config.read_bytes()
    env = directory / 'env.py'
    original[env] = env.read_bytes()
    with tempfile.TemporaryDirectory(prefix='rsc-legacy-migrations-') as temporary:
        target = Path(temporary)
        versions = target / 'versions'
        versions.mkdir()
        copies = {target / 'env.py': original[env]}
        for identifier in sorted(selected):
            path, content, _ = nodes[identifier]
            copies[versions / path.name] = content
        for path, content in copies.items():
            path.write_bytes(content)
        config = ConfigParser(interpolation=None)
        config.read_string(original[original_config].decode('utf-8'))
        config.set('alembic', 'script_location', str(target))
        config.set('alembic', 'prepend_sys_path', str(root / 'backend'))
        config.set('alembic', 'version_locations', str(versions))
        config.set('alembic', 'recursive_version_locations', 'false')
        config_path = target / 'alembic.ini'
        with config_path.open('w') as output:
            config.write(output)
        copies[config_path] = config_path.read_bytes()
        manifest = tuple((nodes[identifier][0].name, hashlib.sha256(nodes[identifier][1]).hexdigest())
            for identifier in sorted(selected))
        try:
            yield HistoricalMigrationGraph(config_path, revision, manifest)
        finally:
            if any(path.read_bytes() != content for path, content in (*original.items(), *copies.items())):
                raise RuntimeError('historical migration source changed during fixture construction')
