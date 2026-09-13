"""Follow exact forward source patches without mistaking a predecessor for HEAD."""
import hashlib
from pathlib import Path
import runpy

from alembic.script import ScriptDirectory


def current_source_hash(revision, signature, body):
    scripts=ScriptDirectory(str(Path(__file__).parents[1]/'alembic'))
    successors=tuple(scripts.iterate_revisions('heads',revision))
    for successor in reversed(successors):
        module=runpy.run_path(successor.path)
        replacement=module.get('_sources',lambda:{})().get(signature)
        if replacement:
            old,new=replacement
            assert body==old,(successor.revision,signature,'historical source discontinuity')
            assert old!=new
            body=new
    return hashlib.sha256(body.encode()).hexdigest()
