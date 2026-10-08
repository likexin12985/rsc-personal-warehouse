"""Self-contained frozen PostgreSQL DDL transition, pending formal integration.

No application models, runpy migration imports or candidate service imports.
This does not advance Alembic, seed permissions or expose HTTP endpoints.
"""
from pathlib import Path
import hashlib,json
from sqlalchemy import text

FOLDER=Path(__file__).resolve().parent
CATALOG_SHA='29bee9907c271dfc9e8bc616e99c658f52de71261b9c6cbfc91b71ade6ec2d50'
raw=(FOLDER/'frozen-catalog.json').read_bytes()
if hashlib.sha256(raw).hexdigest()!=CATALOG_SHA:raise ValueError('0159 frozen catalog digest mismatch')
DATA=json.loads(raw)
TABLES=tuple(row['name'] for row in DATA['candidateTables'])
API='star_oam_api';BACKUP='star_oam_backup'


def quote(value):
    if not value.replace('_','').isalnum() or value[0].isdigit():raise ValueError('unexpected frozen identifier')
    return '"'+value+'"'


def signature(row):return 'public.'+quote(row['proname'])+'('+row['signature'].split('(',1)[1]


def preflight(db,expected_revision='20261207_0158'):
    row=db.execute(text("SELECT current_user,session_user,current_setting('server_version_num')::integer")).one()
    if row[0:2]!=('star_oam_migrator','star_oam_migrator') or row[2]//10000!=16:
        raise ValueError('0159 direct non-superuser PostgreSQL16 migration role required')
    if db.scalar(text('SELECT rolsuper FROM pg_roles WHERE rolname=current_user')):raise ValueError('0159 migrator must not be superuser')
    if db.scalar(text('SELECT version_num FROM alembic_version'))!=expected_revision:
        raise ValueError('0159 exact migration predecessor required')


def verify_function(db,expected,*,before=False):
    row=db.execute(text('''SELECT pg_get_functiondef(p.oid) AS definition,p.proname,p.prosrc,p.prosecdef,p.provolatile,p.proparallel,p.proisstrict,
        p.proleakproof,p.proconfig,pg_get_userbyid(p.proowner) AS owner,
        pg_get_function_identity_arguments(p.oid) AS identity_arguments,
        ARRAY(SELECT jsonb_build_object('grantee',CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END,
            'privilege',a.privilege_type,'grantable',a.is_grantable)
            FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a ORDER BY a.grantee,a.privilege_type) AS acl
        FROM pg_proc p WHERE p.oid=to_regprocedure(:signature)'''),dict(signature=signature(expected))).mappings().one_or_none()
    keys=('definition','proname','prosrc','prosecdef','provolatile','proparallel','proisstrict','proleakproof','proconfig','owner','identity_arguments','acl')
    wanted={key:expected[key] for key in keys}
    if before:
        wanted['prosrc']=expected['before_prosrc'];wanted['definition']=expected['before_definition']
    # ACL array order depends on role OIDs. Compare privileges by role names.
    normalize=lambda value:sorted(value,key=lambda item:(item['grantee'],item['privilege'],item['grantable']))
    if row is None:raise ValueError('0159 missing function: '+expected['proname'])
    actual=dict(row);actual['acl']=normalize(actual['acl']);wanted['acl']=normalize(wanted['acl'])
    if actual!=wanted:raise ValueError('0159 exact function catalog mismatch: '+expected['proname'])


def verify_uuid(db):
    row=db.execute(text('''SELECT e.extversion,n.nspname,pg_get_userbyid(e.extowner),p.prosrc,p.probin,
        l.lanname,p.provolatile,p.proisstrict,p.prosecdef,p.proparallel,p.prorettype::regtype::text,
        has_schema_privilege('star_oam_api',n.oid,'USAGE'),has_function_privilege('star_oam_api',p.oid,'EXECUTE'),
        has_function_privilege('star_oam_migrator',p.oid,'EXECUTE'),
        EXISTS(SELECT 1 FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
            WHERE a.privilege_type='EXECUTE' AND a.grantee NOT IN(p.proowner,'star_oam_migrator'::regrole))
        FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace
        JOIN pg_depend d ON d.refclassid='pg_extension'::regclass AND d.refobjid=e.oid AND d.deptype='e'
        JOIN pg_proc p ON d.classid='pg_proc'::regclass AND d.objid=p.oid JOIN pg_language l ON l.oid=p.prolang
        WHERE e.extname='uuid-ossp' AND p.oid=to_regprocedure('rsc_loss_uuid_0159.uuid_generate_v5(uuid,text)')''')).one_or_none()
    if row is None or tuple(row)!=('1.1','rsc_loss_uuid_0159','postgres','uuid_generate_v5','$libdir/uuid-ossp','c','i',True,False,'s','uuid',False,False,True,False):
        raise ValueError('0159 separately provisioned private UUID dependency required')


def verify(db,cloud=None,*,runtime=False):
    verify_uuid(db)
    for row in DATA['newFunctions']+DATA['replacedFunctions']:verify_function(db,row)
    for table in DATA['candidateTables']:
        name=table['name'];args=dict(table='public.'+name)
        columns=[dict(row) for row in db.execute(text('''SELECT a.attname AS name,format_type(a.atttypid,a.atttypmod) AS type,
            a.attnotnull AS not_null,pg_get_expr(d.adbin,d.adrelid) AS default_sql,a.attidentity,a.attgenerated
            FROM pg_attribute a LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
            WHERE a.attrelid=to_regclass(:table) AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum'''),args).mappings()]
        constraints=[dict(row) for row in db.execute(text('''SELECT conname AS name,contype AS type,pg_get_constraintdef(oid) AS definition,
            convalidated,condeferrable,condeferred FROM pg_constraint WHERE conrelid=to_regclass(:table) AND contype<>'t'
            ORDER BY conname'''),args).mappings()]
        indexes=[dict(row) for row in db.execute(text('''SELECT c.relname AS name,pg_get_indexdef(i.indexrelid) AS definition,
            i.indisunique,i.indisvalid,i.indisready FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid
            WHERE i.indrelid=to_regclass(:table) AND NOT EXISTS(SELECT 1 FROM pg_constraint k WHERE k.conindid=i.indexrelid)
            ORDER BY c.relname'''),args).mappings()]
        if columns!=table['columns'] or constraints!=table['constraints'] or indexes!=table['indexes']:
            raise ValueError('0159 exact table catalog mismatch: '+name)
        missing_fk_guard=db.scalar(text('''SELECT EXISTS(SELECT 1 FROM pg_constraint c
            WHERE c.conrelid=to_regclass(:table) AND c.contype='f' AND
                (SELECT count(*) FROM pg_trigger t WHERE t.tgconstraint=c.oid AND t.tgisinternal AND t.tgenabled='O')<>4)'''),args)
        if missing_fk_guard:raise ValueError('0159 native FK guard missing or disabled: '+name)
        invalid=db.scalar(text('''SELECT EXISTS(SELECT 1 FROM pg_index i WHERE i.indrelid=to_regclass(:table)
            AND (NOT i.indisvalid OR NOT i.indisready OR NOT i.indisunique OR NOT i.indimmediate))'''),args)
        if invalid:raise ValueError('0159 invalid backing index: '+name)
        # Later additive revisions may leave canonical PostgreSQL tombstones
        # after an empty schema downgrade. They have no addressable column or
        # privileges. Keep the frozen visible schema above exact, and reject
        # every column ACL and noncanonical dropped slot as before. This only
        # repairs catalog verification; the frozen DDL and business facts do
        # not change, and no table needs to be copied or rebuilt.
        table_flags=db.execute(text('''SELECT pg_get_userbyid(c.relowner),c.relkind,c.relpersistence,c.relrowsecurity,c.relforcerowsecurity,
            EXISTS(SELECT 1 FROM pg_attribute a WHERE a.attrelid=c.oid AND
                (a.attacl IS NOT NULL OR (a.attisdropped AND
                    (a.atttypid<>0 OR a.attnotnull OR a.attidentity<>'' OR a.attgenerated<>''))))
            FROM pg_class c WHERE c.oid=to_regclass(:table)'''),args).one()
        if tuple(table_flags)!=('star_oam_migrator','r','p',False,False,False):raise ValueError('0159 table security metadata mismatch: '+name)
        acl=db.execute(text('''SELECT CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END,
            a.privilege_type,a.is_grantable FROM pg_class c,
            LATERAL aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) a
            WHERE c.oid=to_regclass(:table) AND a.grantee<>c.relowner ORDER BY 1,2,3'''),args).all()
        expected_acl=[(API,'SELECT',False),(BACKUP,'SELECT',False)]
        if runtime and name!='stock_loss_request_key_bindings':expected_acl.insert(0,(API,'INSERT',False))
        if [tuple(row) for row in acl]!=expected_acl:
            raise ValueError('0159 table ACL mismatch: '+name)
    triggers=[dict(row) for row in db.execute(text('''SELECT c.relname AS table_name,t.tgname AS name,
        pg_get_triggerdef(t.oid) AS definition,t.tgenabled,t.tgtype::int,t.tgdeferrable,t.tginitdeferred,p.proname AS function_name
        FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_proc p ON p.oid=t.tgfoid
        WHERE c.relnamespace='public'::regnamespace AND NOT t.tgisinternal
          AND (c.relname=ANY(:tables) OR (p.pronamespace='public'::regnamespace AND right(p.proname,5)='_0159'))
        ORDER BY c.relname,t.tgname'''),dict(tables=list(TABLES))).mappings()]
    if triggers!=DATA['triggers']:raise ValueError('0159 exact trigger catalog mismatch')


def lock_existing(db):
    names=sorted(({row['table_name'] for row in DATA['triggers']}|{'alembic_version','inventory_ledger_heads','stock_accounts'})-set(TABLES))
    db.execute(text('LOCK TABLE '+','.join('public.'+quote(name) for name in names)+' IN SHARE ROW EXCLUSIVE MODE'))


def install(db,cloud=None):
    preflight(db);lock_existing(db);verify_uuid(db)
    for name in TABLES:
        if db.scalar(text('SELECT to_regclass(:name)'),dict(name='public.'+name)) is not None:
            raise ValueError('0159 table already exists: '+name)
    for row in DATA['newFunctions']:
        if db.scalar(text('SELECT to_regprocedure(:signature)'),dict(signature=signature(row))) is not None:
            raise ValueError('0159 function already exists: '+row['proname'])
    for row in DATA['replacedFunctions']:verify_function(db,row,before=True)
    for statement in DATA['statements']:db.execute(text(statement))
    for name in TABLES:
        db.execute(text('REVOKE ALL ON public.'+quote(name)+' FROM PUBLIC,star_oam_api,star_oam_projector,edge_inbox,star_oam_backup'))
        db.execute(text('GRANT SELECT ON public.'+quote(name)+' TO star_oam_api,star_oam_backup'))
    for row in DATA['newFunctions']:
        target=signature(row)
        db.execute(text('REVOKE ALL ON FUNCTION '+target+' FROM PUBLIC,star_oam_api,star_oam_projector,edge_inbox,star_oam_backup'))
        for grant in row['acl']:
            if grant['grantee']==row['owner']:continue
            if grant!={'grantee':API,'privilege':'EXECUTE','grantable':False}:raise ValueError('unexpected frozen function grant')
            db.execute(text('GRANT EXECUTE ON FUNCTION '+target+' TO star_oam_api'))
    verify(db)


def remove_empty(db,cloud=None,*,expected_revision='20261207_0158',runtime=False):
    preflight(db,expected_revision);lock_existing(db)
    db.execute(text('LOCK TABLE '+','.join('public.'+quote(name) for name in TABLES)+' IN ACCESS EXCLUSIVE MODE'))
    verify(db,runtime=runtime)
    # Historical normal and return roots also rely on the replaced proof graph.
    for name in (*TABLES,'stock_loss_dispositions'):
        if db.scalar(text('SELECT EXISTS(SELECT 1 FROM public.'+quote(name)+')')):
            raise ValueError('0159 immutable business history requires retention: '+name)
    for row in reversed(DATA['triggers']):db.execute(text('DROP TRIGGER '+quote(row['name'])+' ON public.'+quote(row['table_name'])))
    for row in DATA['replacedFunctions']:db.execute(text(row['before_definition']))
    for row in reversed(DATA['newFunctions']):db.execute(text('DROP FUNCTION '+signature(row)))
    db.execute(text('DROP TABLE '+','.join('public.'+quote(name) for name in reversed(TABLES))))
    for row in DATA['replacedFunctions']:verify_function(db,row,before=True)
