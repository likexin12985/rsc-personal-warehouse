"""Immutable business closure, current authority, audit and fulfillment barrier."""
from functools import cache
from pathlib import Path
import runpy

from alembic import context, op
from sqlalchemy import text

revision = '20261218_0169'
down_revision = '20261217_0168'
branch_labels = depends_on = None
FOLDER = Path(__file__).parents[1] / 'request_closure_0169'


@cache
def _support(name):
    return runpy.run_path(str(FOLDER / (name + '.py')))


def _sources():
    stock = _support('transition')['DATA']
    ready = _support('readiness')['DATA']
    sources = {'public.' + signature: (change['before']['prosrc'], change['after']['prosrc'])
               for signature, change in stock['functions'].items() if change['before'] is not None}
    sources['public.' + ready['before']['signature']] = (ready['before']['prosrc'], ready['after']['prosrc'])
    return sources


def _transition(*, up):
    if context.is_offline_mode():
        raise RuntimeError('0169 online predecessor and retained-history verification required')
    db = op.get_bind()
    if db.dialect.name == 'sqlite':
        _support('sqlite_transition')['transition'](db, up=up)
        _support('permission_policy')['install' if up else 'preserve_on_downgrade'](db)
        return
    if db.dialect.name != 'postgresql':
        raise RuntimeError('0169 requires PostgreSQL16 or SQLite schema tooling')
    stock, ready = _support('transition'), _support('readiness')
    stock['preflight'](db, down_revision if up else revision)
    stock['lock_existing'](db)
    ready['verify'](db, 'before' if up else 'after')
    # Earlier revisions may leave pg_catalog first during a single full-chain
    # transaction. Frozen unqualified CREATE TABLE must target public instead.
    # Omitting explicit pg_catalog preserves its implicit lookup precedence;
    # neither function-local search paths nor any role grants are changed.
    previous_path = db.scalar(text("SELECT pg_catalog.current_setting('search_path')"))
    db.execute(text("SELECT pg_catalog.set_config('search_path', 'public', true)"))
    if up:
        stock['install'](db)
    else:
        stock['remove_empty'](db, expected_revision=revision)
    _support('permission_policy')['install' if up else 'preserve_on_downgrade'](db)
    ready['replace'](db, up=up)
    db.execute(text("SELECT pg_catalog.set_config('search_path', :previous_path, true)"),
               {'previous_path': previous_path})


def upgrade():
    _transition(up=True)


def downgrade():
    _transition(up=False)
