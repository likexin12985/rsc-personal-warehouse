"""Native loss inverses, independent correction decisions and bound recovery.

Runtime DB privileges are minimal; business role configuration is separate.
SQLite preserves migration shape and rejects every new correction write.
"""
from functools import cache
from pathlib import Path
import runpy
from alembic import context, op

revision='20261208_0159'
down_revision='20261207_0158'
branch_labels=depends_on=None
FOLDER=Path(__file__).parent
OLD_READY_HASH='6c18da9162afedf2f302f9db0474d7b64f235766515614acd814eb08e6e160a2'
NEW_READY_HASH='240cad9ec9297ede89198d6c9aa891484c8ae3664c27bc4185951e605fa79bb2'


@cache
def _support():
    return runpy.run_path(str(FOLDER.parent/'stock_loss_corrections_0159/transition.py'))


def _sources():return _support()['sources']()
def _transition(*, up):
    # Catalog, role and retained-history checks require live database evidence.
    # Offline SQL generation cannot prove those prerequisites.
    if context.is_offline_mode():
        direction = "upgrade" if up else "downgrade"
        raise RuntimeError(f"0159 {direction} requires an online evidence check")
    _support()['transition'](op.get_bind(), up=up)


def upgrade():
    _transition(up=True)


def downgrade():
    _transition(up=False)
