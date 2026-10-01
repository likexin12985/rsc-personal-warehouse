"""Immutable 0159 schema transition; separate DBA UUID bootstrap required."""
from pathlib import Path
from functools import cache
import hashlib,json,runpy
from sqlalchemy import text

HERE=Path(__file__).resolve().parent
PREVIOUS='20261207_0158';REVISION='20261208_0159'
raw=(HERE/'readiness.json').read_bytes()
if hashlib.sha256(raw).hexdigest()!='c2a58f4378028c44f58a1792cfbe6b89dc23f65ec6064ee3be82609c1dd546e1':
    raise ValueError('0159 readiness artifact digest mismatch')
READY=json.loads(raw)


@cache
def support(dialect):
    if dialect not in ('postgresql','sqlite'):raise ValueError('0159 PostgreSQL16 or SQLite tooling required')
    return runpy.run_path(str(HERE/('frozen_install.py' if dialect=='postgresql' else 'sqlite_install.py')))


def sources():
    result={'public.'+row['proname']+'('+', '.join(row['signature'].split('(',1)[1][:-1].split(','))+')':(row['before_prosrc'],row['prosrc'])
        for row in support('postgresql')['DATA']['replacedFunctions']}
    result['public.rsc_oam_runtime_binding_ready_0044()']=(READY['beforeBody'],READY['afterBody'])
    return result


def ready_state(db,*,upgraded):
    row=db.execute(text('''SELECT p.prosrc,pg_get_userbyid(p.proowner) AS owner,p.prosecdef,p.provolatile,
        p.proparallel,p.proisstrict,p.proleakproof,p.prokind,p.prorettype::regtype::text AS result,
        p.proconfig,l.lanname,pg_get_function_identity_arguments(p.oid) AS args
        FROM pg_proc p JOIN pg_language l ON l.oid=p.prolang
        WHERE p.oid=to_regprocedure('public.rsc_oam_runtime_binding_ready_0044()')''')).mappings().one_or_none()
    expected=dict(prosrc=READY['afterBody' if upgraded else 'beforeBody'],owner='star_oam_migrator',
        prosecdef=True,provolatile='s',proparallel='u',proisstrict=False,proleakproof=False,
        prokind='f',result='boolean',proconfig=['search_path=pg_catalog'],lanname='sql',args='')
    if row is None or dict(row)!=expected:raise ValueError('0159 exact readiness source or metadata drift')
    return db.execute(text('''SELECT CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END,
        a.privilege_type,a.is_grantable FROM pg_proc p,
        LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
        WHERE p.oid=to_regprocedure('public.rsc_oam_runtime_binding_ready_0044()') ORDER BY 1,2,3''')).all()


def transition(db,*,up):
    dialect=db.dialect.name;core=support(dialect)
    if dialect=='sqlite':
        if up:core['install'](db)
        else:core['remove_empty'](db,expected_revision=REVISION)
        return
    core['preflight'](db,PREVIOUS if up else REVISION)
    db.exec_driver_sql('LOCK TABLE public.alembic_version IN ACCESS EXCLUSIVE MODE')
    acl=ready_state(db,upgraded=not up)
    if up:
        core['install'](db)
        # Binding provenance is only writable through its controlled function.
        for name in core['TABLES']:
            if name!='stock_loss_request_key_bindings':
                db.exec_driver_sql('GRANT INSERT ON public.'+core['quote'](name)+' TO star_oam_api')
        core['verify'](db,runtime=True)
    else:
        core['remove_empty'](db,expected_revision=REVISION,runtime=True)
    db.exec_driver_sql(READY['upgradeSql' if up else 'downgradeSql'])
    if ready_state(db,upgraded=up)!=acl:raise ValueError('0159 readiness ACL changed')
