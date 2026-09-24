"""Follow exact forward source patches without mistaking a predecessor for HEAD."""
import hashlib
from functools import cache
from pathlib import Path
import runpy

from alembic.script import ScriptDirectory


@cache
def _successor_paths(revision):
    scripts=ScriptDirectory(str(Path(__file__).parents[1]/'alembic'))
    return tuple((successor.revision, successor.path)
                 for successor in reversed(tuple(scripts.iterate_revisions('heads',revision))))


@cache
def _source_patches(path):
    # Most revisions cannot replace a function body.  Avoid executing their
    # nested runpy import chains merely to discover that _sources is absent.
    if '_sources' not in Path(path).read_text(encoding='utf-8'):
        return {}
    module=runpy.run_path(path)
    return module.get('_sources',lambda:{})()


def current_source_hash(revision, signature, body):
    # Migration files are immutable during one pytest process.  Several tests
    # inspect the same successor chain for different functions; loading every
    # revision on every assertion can dominate the complete static gate.
    for successor_revision, successor_path in _successor_paths(revision):
        replacement=_source_patches(successor_path).get(signature)
        if replacement:
            old,new=replacement
            assert body==old,(successor_revision,signature,'historical source discontinuity')
            assert old!=new
            body=new
    return hashlib.sha256(body.encode()).hexdigest()
