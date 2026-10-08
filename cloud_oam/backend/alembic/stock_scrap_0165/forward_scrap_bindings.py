"""Add raw-key provenance for original scrap and three independent reviews.

No legacy function is replaced. The cross-registry insert fence is additive.
This private candidate still requires the formal revision/ACL/readiness gate.
"""
from pathlib import Path
import runpy
from sqlalchemy import text

FOLDER = Path(__file__).resolve().parent
prior = runpy.run_path(str(FOLDER / 'forward_recovery_bindings.py'))


def patches():
    return []


def install(db):
    identity = db.execute(text("SELECT current_user,session_user,current_setting('server_version_num')::int")).one()
    if identity[:2] != ('star_oam_migrator', 'star_oam_migrator') or identity[2] // 10000 != 16:
        raise ValueError('0165 direct PostgreSQL16 migration owner required')
    if db.scalar(text('SELECT rolsuper FROM pg_roles WHERE rolname=current_user')):
        raise ValueError('0165 migration owner must not be superuser')
    if db.scalar(text('SELECT version_num FROM public.alembic_version')) != '20261213_0164':
        raise ValueError('0165 exact predecessor revision required')
    for patch in prior['patches']():
        prior['history']['verify'](db, patch['after'])
    db.execute(text((FOLDER / 'scrap_bindings.sql').read_text()))
    return []
