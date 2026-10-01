"""Forward-only native catalog transition, awaiting the verified frozen catalog.

The caller supplies a literal catalog digest from the eventual Alembic entry.
No application models are imported and no old migration files are modified.
This module alone is not a registered migration or a release certificate.
"""
from pathlib import Path
import copy
import hashlib
import json
import runpy
from sqlalchemy import text


def quote(value):
    if not value or not value.replace('_','').isalnum() or value[0].isdigit():
        raise ValueError('0161 frozen identifier required')
    return '"'+value+'"'


def signature(row):
    return 'public.'+quote(row['proname'])+'('+row['signature'].split('(',1)[1]


def load_catalog(path,expected_sha):
    raw=Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=expected_sha:
        raise ValueError('0161 frozen forward catalog digest mismatch')
    data=json.loads(raw)
    if (data['previousRevision'],data['revision'])!=('20261209_0160','20261210_0161'):
        raise ValueError('0161 exact forward revision pair required')
    return data


def sources(data):
    """Exact predecessor/successor bodies for migration history assertions."""
    result={}
    for row in data['changedFunctions']:
        before,after=row['before'],row['after']
        arguments=before['signature'].split('(',1)[1][:-1]
        coordinate='public.'+before['proname']+'('+', '.join(part.strip() for part in arguments.split(','))+')'
        if coordinate in result:raise ValueError('0161 duplicate function transition')
        result[coordinate]=(before['prosrc'],after['prosrc'])
    result['public.rsc_oam_runtime_binding_ready_0044()']=(
        data['readiness']['beforeBody'],data['readiness']['afterBody'])
    return result


def previous_module(migration_root,data,*,upgraded):
    migration_root=Path(migration_root)
    for relative,expected in (
        ('stock_loss_corrections_0159/frozen-catalog.json',data['frozen0159Sha256']),
        ('stock_loss_multigeneration_0160/catalog.json',data['frozen0160Sha256']),
    ):
        if hashlib.sha256((migration_root/relative).read_bytes()).hexdigest()!=expected:
            raise ValueError('0161 immutable predecessor catalog drift')
    support=runpy.run_path(str(migration_root/'stock_loss_multigeneration_0160/transition.py'))
    module=support['_previous']('postgresql',upgraded=True)
    if upgraded:
        # A fresh verifier instance receives the successor's exact catalog;
        # the frozen file and shared runtime module remain untouched.
        module.DATA=copy.deepcopy(data['after'])
        module.TABLES=tuple(row['name'] for row in module.DATA['candidateTables'])
        # The predecessor query sees its suffix plus all candidate tables.
        # Additional 0161 fences on other tables are checked in full below.
        module.DATA['triggers']=[row for row in module.DATA['triggers']
            if row['table_name'] in module.TABLES or row['function_name'].endswith('_0159')]
    return module


def verify_catalog(db,migration_root,data,*,upgraded):
    module=previous_module(migration_root,data,upgraded=upgraded)
    module.verify(db,runtime=True)
    tables=[row['name'] for row in data['after']['candidateTables']]
    actual=[dict(row) for row in db.execute(text('''SELECT c.relname AS table_name,t.tgname AS name,
        pg_get_triggerdef(t.oid) AS definition,t.tgenabled,t.tgtype::int,t.tgdeferrable,t.tginitdeferred,p.proname AS function_name
        FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_proc p ON p.oid=t.tgfoid
        WHERE c.relnamespace='public'::regnamespace AND NOT t.tgisinternal
          AND (c.relname=ANY(:tables) OR (p.pronamespace='public'::regnamespace AND right(p.proname,5) IN ('_0159','_0161')))
        ORDER BY c.relname,t.tgname'''),dict(tables=tables)).mappings()]
    if actual!=data['after' if upgraded else 'before']['triggers']:
        raise ValueError('0161 exact bidirectional trigger catalog mismatch')


def ready(db,data,*,upgraded):
    row=db.execute(text('''SELECT p.prosrc,pg_get_userbyid(p.proowner) AS owner,p.prosecdef,p.provolatile,
        p.proparallel,p.proisstrict,p.proleakproof,p.prokind,p.prorettype::regtype::text AS result,
        p.proconfig,l.lanname,pg_get_function_identity_arguments(p.oid) AS args
        FROM pg_proc p JOIN pg_language l ON l.oid=p.prolang
        WHERE p.oid=to_regprocedure('public.rsc_oam_runtime_binding_ready_0044()')''')).mappings().one_or_none()
    expected=dict(prosrc=data['readiness']['afterBody' if upgraded else 'beforeBody'],
        owner='star_oam_migrator',prosecdef=True,provolatile='s',proparallel='u',proisstrict=False,
        proleakproof=False,prokind='f',result='boolean',proconfig=['search_path=pg_catalog'],lanname='sql',args='')
    if row is None or dict(row)!=expected:raise ValueError('0161 exact readiness definition required')
    return [tuple(row) for row in db.execute(text('''SELECT CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END,
        a.privilege_type,a.is_grantable FROM pg_proc p,
        LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
        WHERE p.oid=to_regprocedure('public.rsc_oam_runtime_binding_ready_0044()') ORDER BY 1,2,3'''))]


def set_ready(db,data,*,upgraded):
    body=data['readiness']['afterBody' if upgraded else 'beforeBody']
    if '$function$' in body:raise ValueError('0161 unexpected readiness delimiter')
    db.execute(text('CREATE OR REPLACE FUNCTION public.rsc_oam_runtime_binding_ready_0044() '
        'RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $function$'+body+'$function$'))


def create_trigger(db,row):
    db.execute(text(row['definition']))
    db.execute(text('ALTER TABLE public.'+quote(row['table_name'])+' ENABLE ALWAYS TRIGGER '+quote(row['name'])))


def grants(db,table,*,writable):
    db.execute(text('REVOKE ALL ON public.'+quote(table)+' FROM PUBLIC,star_oam_api,star_oam_backup,star_oam_projector,edge_inbox'))
    db.execute(text('GRANT SELECT'+(',INSERT' if writable else '')+' ON public.'+quote(table)+' TO star_oam_api'))
    db.execute(text('GRANT SELECT ON public.'+quote(table)+' TO star_oam_backup'))


def upgrade(db,data):
    for statement in data['schemaStatements']:db.execute(text(statement))
    for row in data['changedFunctions']:db.execute(text(row['after']['definition']))
    for row in data['addedFunctions']:
        db.execute(text(row['definition']))
        db.execute(text('REVOKE ALL ON FUNCTION '+signature(row)+
            ' FROM PUBLIC,star_oam_api,star_oam_backup,star_oam_projector,edge_inbox'))
    for table in data['addedTables']:grants(db,table,writable=True)
    for row in data['addedTriggers']:create_trigger(db,row)


def downgrade_empty(db,data):
    binding='stock_loss_request_key_bindings'
    # A downgraded application can continue historical reads with the upgraded
    # database. Schema downgrade is deliberately restricted to empty key/seal
    # history, never deleting or copying immutable production request records.
    for table in (*data['addedTables'],binding):
        if db.scalar(text('SELECT EXISTS(SELECT 1 FROM public.'+quote(table)+')')):
            raise ValueError('0161 immutable correction request history requires retention: '+table)
    for row in reversed(data['addedTriggers']):
        db.execute(text('DROP TRIGGER '+quote(row['name'])+' ON public.'+quote(row['table_name'])))
    for row in data['changedFunctions']:db.execute(text(row['before']['definition']))
    for row in reversed(data['addedFunctions']):db.execute(text('DROP FUNCTION '+signature(row)))
    # DROP COLUMN would leave attisdropped catalog entries and fail the exact
    # predecessor verifier. Recreate only the proven-empty binding table.
    db.execute(text('DROP TABLE public.'+quote(binding)))
    for table in reversed(data['addedTables']):db.execute(text('DROP TABLE public.'+quote(table)))
    for statement in data['restoreEmptyBindingStatements']:db.execute(text(statement))
    grants(db,binding,writable=False)
    for row in data['before']['triggers']:
        if row['table_name']==binding:create_trigger(db,row)


def transition(db,*,up,catalog_path,expected_sha,migration_root):
    data=load_catalog(catalog_path,expected_sha)
    if db.dialect.name!='postgresql':raise ValueError('0161 native transition requires PostgreSQL16')
    before=previous_module(migration_root,data,upgraded=not up)
    expected_revision=data['previousRevision' if up else 'revision']
    before.preflight(db,expected_revision)
    if tuple(db.scalars(text('SELECT version_num FROM public.alembic_version')))!=(expected_revision,):
        raise ValueError('0161 exactly one current revision required')
    db.execute(text('SET LOCAL search_path=pg_catalog,public'))
    tables=sorted({row['table_name'] for row in data['before']['triggers']}|{'alembic_version','inventory_ledger_heads'})
    if not up:tables=sorted(set(tables)|set(data['addedTables']))
    db.execute(text('LOCK TABLE '+','.join('public.'+quote(table) for table in tables)+' IN SHARE ROW EXCLUSIVE MODE'))
    if tuple(db.scalars(text('SELECT version_num FROM public.alembic_version')))!=(expected_revision,):
        raise ValueError('0161 migration revision changed while acquiring locks')
    db.execute(text("SELECT stream_key FROM public.inventory_ledger_heads WHERE stream_key='inventory' FOR UPDATE"))
    verify_catalog(db,migration_root,data,upgraded=not up)
    original_ready_acl=ready(db,data,upgraded=not up)
    if up:upgrade(db,data)
    else:downgrade_empty(db,data)
    set_ready(db,data,upgraded=up)
    verify_catalog(db,migration_root,data,upgraded=up)
    if ready(db,data,upgraded=up)!=original_ready_acl:
        raise ValueError('0161 readiness permissions changed')
