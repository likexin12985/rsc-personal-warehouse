"""Permanent correction approval/execution request seals and exact catalog."""
from functools import cache
from pathlib import Path
import runpy
from alembic import context, op

revision = '20261210_0161'
down_revision = '20261209_0160'
branch_labels = depends_on = None
OLD_READY_HASH = '2cc36b88c9ed37179c33411ca35670d705a8c963fc2bbd37c3e5c5d2f815f0e3'
NEW_READY_HASH = '4bc307952f65f5ea237bcdefbc850d160543690df2ec97aaad9595c3958fe403'
FOLDER = Path(__file__).parent.parent / 'stock_loss_correction_seals_0161'
CATALOG_SHA = 'b79b960823852cd9ad1474c0da78bdcfcfbe3c513253d5d9b255264f5ae5bc89'
SQLITE_CATALOG_SHA = '073dc69ab861041c6db6f0eecc7c6506d8e2cbf96d4008fa10add711c395c340'

@cache
def _support(dialect='postgresql'):
    filename = 'transition.py' if dialect == 'postgresql' else 'sqlite_transition.py'
    return runpy.run_path(str(FOLDER / filename))

def _sources():
    support = _support()
    return support['sources'](support['load_catalog'](FOLDER / 'catalog.json', CATALOG_SHA))

def _transition(*, up):
    if context.is_offline_mode():
        direction = 'upgrade' if up else 'downgrade'
        raise RuntimeError(f'0161 {direction} requires an online evidence check')
    db = op.get_bind()
    dialect = db.dialect.name
    if dialect not in ('postgresql', 'sqlite'):
        raise RuntimeError('0161 requires PostgreSQL16 or SQLite tooling')
    _support(dialect)['transition'](
        db, up=up, catalog_path=FOLDER / ('catalog.json' if dialect == 'postgresql' else 'sqlite-catalog.json'),
        expected_sha=CATALOG_SHA if dialect == 'postgresql' else SQLITE_CATALOG_SHA,
        migration_root=FOLDER.parent,
    )

def upgrade():
    _transition(up=True)

def downgrade():
    _transition(up=False)
