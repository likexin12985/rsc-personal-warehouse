"""Exact multi-generation loss correction plans and permanent inverse seals."""
from functools import cache
from pathlib import Path
import runpy
from alembic import context, op
revision = '20261209_0160'
down_revision = '20261208_0159'
branch_labels = depends_on = None
FOLDER = Path(__file__).parent
OLD_READY_HASH = '240cad9ec9297ede89198d6c9aa891484c8ae3664c27bc4185951e605fa79bb2'
NEW_READY_HASH = '2cc36b88c9ed37179c33411ca35670d705a8c963fc2bbd37c3e5c5d2f815f0e3'

@cache
def _support():
    return runpy.run_path(str(FOLDER.parent / 'stock_loss_multigeneration_0160/transition.py'))

def _sources():
    return _support()['sources']()

def _transition(*, up):
    # Catalog, role and retained-history checks require live database evidence.
    # Offline SQL generation cannot prove those prerequisites.
    if context.is_offline_mode():
        direction = "upgrade" if up else "downgrade"
        raise RuntimeError(f"0160 {direction} requires an online evidence check")
    _support()['transition'](op.get_bind(), up=up)


def upgrade():
    _transition(up=True)


def downgrade():
    _transition(up=False)
