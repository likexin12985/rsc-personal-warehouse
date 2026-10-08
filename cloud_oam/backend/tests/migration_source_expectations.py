import ast
import hashlib
import json
from functools import cache
from pathlib import Path
import re
import runpy

from migration_script_cache import cache_migration_compilation

_REVISION_RE = re.compile(r"^revision\s*=\s*(['\"])([^'\"]+)\1\s*$", re.MULTILINE)
_DOWN_REVISION_RE = re.compile(r"^down_revision\s*=\s*(.+?)\s*$", re.MULTILINE)


@cache
def _successor_paths(revision):
    # The source-hash check only needs the immutable revision graph.  Loading
    # the Alembic ScriptDirectory here executes every migration module and its
    # nested predecessor loaders, which is both unnecessary and quadratic for
    # a long chain.  Revision/down_revision are declarative string literals in
    # this tree, so read those coordinates without executing migration code.
    root = Path(__file__).resolve().parents[2]
    versions = root / 'backend/alembic/versions'
    paths = {}
    children = {}
    for path in sorted(versions.glob('*.py')):
        text = path.read_text(encoding='utf-8')
        revision_match = _REVISION_RE.search(text)
        down_match = _DOWN_REVISION_RE.search(text)
        if revision_match is None or down_match is None:
            continue
        current = revision_match.group(2)
        try:
            parents = ast.literal_eval(down_match.group(1))
        except (SyntaxError, ValueError):
            continue
        if parents is None:
            parents = ()
        elif isinstance(parents, str):
            parents = (parents,)
        elif isinstance(parents, (tuple, list)):
            parents = tuple(item for item in parents if isinstance(item, str))
        else:
            continue
        paths[current] = path
        for parent in parents:
            children.setdefault(parent, []).append(current)

    result = []
    seen = set()

    def visit(parent):
        for child in sorted(children.get(parent, ())):
            if child in seen or child not in paths:
                continue
            seen.add(child)
            result.append((child, str(paths[child])))
            visit(child)

    visit(revision)
    return tuple(result)


@cache
def _source_patches(path):
    # Most revisions cannot replace a function body.  Avoid executing their
    # nested runpy import chains merely to discover that _sources is absent.
    if '_sources' not in Path(path).read_text(encoding='utf-8'):
        return {}
    module = runpy.run_path(path)
    return module.get('_sources', lambda: {})()


@cache
def _catalog_source_key(revision, target_name):
    """Whether a revision-scoped frozen catalog names this source key."""
    return bool(_catalog_source_patches(revision, target_name))


@cache
def _catalog_source_patches(revision, target_name=None):
    """Read only actual before/after source entries from frozen catalogs."""
    root = Path(__file__).resolve().parents[2] / 'backend/alembic'
    suffix = revision.rsplit('_', 1)[-1]
    result = {}
    for path in root.rglob('*'):
        if (not path.is_file() or suffix not in path.parent.name
                or path.suffix != '.json'):
            continue
        try:
            document = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, UnicodeDecodeError):
            continue
        candidates = []
        for key in ('patches', 'functions', 'replacedFunctions'):
            value = document.get(key) if isinstance(document, dict) else None
            if isinstance(value, dict):
                candidates.extend((str(name), row) for name, row in value.items())
            elif isinstance(value, list):
                candidates.extend((str(row.get('signature', '')), row)
                                  for row in value if isinstance(row, dict))
        if path.name == 'readiness.json' and isinstance(document, dict):
            candidates.append((str(document.get('before', {}).get('signature', '')), document))
        for signature, row in candidates:
            if not signature or (target_name is not None and target_name not in signature):
                continue
            old = new = None
            if isinstance(row, dict) and isinstance(row.get('before'), str):
                old, new = row.get('before'), row.get('after')
            elif isinstance(row, dict) and isinstance(row.get('before'), dict):
                old = row['before'].get('prosrc')
                new = row.get('after', {}).get('prosrc') if isinstance(row.get('after'), dict) else None
            elif isinstance(row, dict):
                old = row.get('before_prosrc')
                new = row.get('prosrc')
            if not isinstance(old, str) or not isinstance(new, str):
                continue
            coordinate = signature if signature.startswith('public.') else 'public.' + signature
            result[coordinate] = (old, new)
    return result


def _signature_coordinate(signature):
    # Frozen pg_get_functiondef signatures omit spaces after commas; older
    # migration helpers include them. Preserve schema/name/type identity.
    return re.sub(r'\s*,\s*', ', ', signature)


def current_source_body(revision, signature, body):
    versions = Path(__file__).resolve().parents[1] / 'alembic/versions'
    # Reuse bytecode, never migration globals, while evaluating the complete
    # source chain. Dynamic keys can be inherited from predecessor namespaces.
    with cache_migration_compilation(versions):
        return _current_source_body(revision, signature, body)


def _current_source_body(revision, signature, body):
    # Migration files are immutable during one pytest process.  Several tests
    # inspect the same successor chain for different functions; loading every
    # revision on every assertion can dominate the complete static gate.
    # Do not infer replacement keys from literal function names: 0150, for
    # example, inherits ACCOUNT_SIGNATURE without spelling its value locally.
    # Skip only files which have no source-patch hook or matching frozen entry.
    target_name = signature.split("(", 1)[0].rsplit(".", 1)[-1]
    for successor_revision, successor_path in _successor_paths(revision):
        if isinstance(successor_path, (str, Path)):
            successor_text = Path(successor_path).read_text(encoding="utf-8")
            if '_sources' not in successor_text and not _catalog_source_key(successor_revision, target_name):
                continue
        catalog_patches = _catalog_source_patches(successor_revision, target_name)
        source_patches = catalog_patches or _source_patches(successor_path)
        matches = [value for key, value in source_patches.items()
                   if _signature_coordinate(key) == _signature_coordinate(signature)]
        assert len(matches) <= 1, (successor_revision, signature, 'duplicate source coordinate')
        replacement = matches[0] if matches else None
        if replacement:
            old, new = replacement
            assert body == old, (successor_revision, signature, 'historical source discontinuity')
            assert old != new
            body = new
    return body


def current_source_hash(revision, signature, body):
    return hashlib.sha256(current_source_body(revision, signature, body).encode()).hexdigest()
