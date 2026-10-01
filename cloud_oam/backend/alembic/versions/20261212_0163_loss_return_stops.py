"""Stop unshipped original loss returns atomically with their dedicated inverse.

Migration draft. Must pass schema/ACL/startup and native business gates before
registration or application; never bypass the existing generic return guards.
"""
from functools import cache
from pathlib import Path
import hashlib,json,runpy
import sqlalchemy as sa
from alembic import context,op

revision='20261212_0163'
down_revision='20261211_0162'
branch_labels=depends_on=None
TABLE='stock_loss_return_stops'
SQLITE_CREATE_SHA='fbc627547192d6aa8c05055abe68f7b4644d7cce84fafae098dd80d652c05b51'
CATALOG_SHA='cab3ea4a356b2bd8a21d8207b6f46c4023bce492f5401380dd225cda43e14b42'
FOLDER=Path(__file__).parent.parent/'stock_loss_return_stops_0163'


@cache
def _support():
    return runpy.run_path(str(FOLDER/'transition.py'))


def _sources():
    return {'public.'+row['signature']:(row['before_prosrc'],row['prosrc']) for row in _support()['DATA']['replacedFunctions']}


def _sqlite_guard_sql(action):
    reason='0163 PostgreSQL return-stop proof required' if action=='insert' else '0163 stop history is immutable'
    return 'CREATE TRIGGER trg_loss_return_stop_'+action+'_0163 BEFORE '+action.upper()+' ON '+TABLE+" BEGIN SELECT RAISE(ABORT,'"+reason+"'); END"


def _transition(*,up):
    if context.is_offline_mode():
        raise RuntimeError('0163 online history and catalog verification required')
    support=_support();db=op.get_bind()
    if db.dialect.name=='postgresql':
        if up:support['install'](db)
        else:support['remove_empty'](db,expected_revision=revision,runtime=True)
        return
    if db.dialect.name!='sqlite':raise RuntimeError('0163 PostgreSQL16 or SQLite tooling required')
    runpy.run_path(str(Path(__file__).parent/'20260927_0087_inbound_fulfillment_boundary.py'))['_begin_sqlite']()
    exists=sa.inspect(db).has_table(TABLE)
    if exists==up:raise RuntimeError('0163 exact predecessor table state required')
    if up:
        # Frozen native metadata translated only for schema tooling. All new
        # stop insertions require PostgreSQL's complete deferred stock proof.
        raw=(FOLDER/'sqlite-create.sql').read_bytes()
        if hashlib.sha256(raw).hexdigest()!=SQLITE_CREATE_SHA:raise RuntimeError('0163 frozen SQLite schema digest mismatch')
        db.exec_driver_sql(raw.decode())
        for action in ('insert','update','delete'):db.exec_driver_sql(_sqlite_guard_sql(action))
    else:
        guards=dict(db.execute(sa.text("SELECT name,sql FROM sqlite_master WHERE type='trigger' AND tbl_name=:table"),dict(table=TABLE)).all())
        if guards!={'trg_loss_return_stop_'+action+'_0163':_sqlite_guard_sql(action) for action in ('insert','update','delete')}:
            raise RuntimeError('0163 exact SQLite closed-write guards required')
        if db.scalar(sa.text('SELECT EXISTS(SELECT 1 FROM '+TABLE+')')) or db.scalar(sa.text("SELECT EXISTS(SELECT 1 FROM stock_loss_inverse_request_seals s JOIN stock_loss_dispositions d ON d.id=s.root_disposition_id WHERE d.disposition='return_to_region')")):
            raise RuntimeError('0163 immutable stop or return inverse seal history requires retention')
        op.drop_table(TABLE)


def upgrade():_transition(up=True)


def downgrade():_transition(up=False)
