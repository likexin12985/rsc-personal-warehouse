"""Read-only exact catalog for loss correction stock and request proofs."""
from pathlib import Path
import hashlib,json
from sqlalchemy import text
RAW=(Path(__file__).with_suffix('.json')).read_bytes()
if hashlib.sha256(RAW).hexdigest()!='4a5664b877a90188587784bea44389f422d777f82fc16e551e7a657281ebe0de':
    raise ValueError('0161 runtime catalog artifact digest mismatch')
DATA=json.loads(RAW)
TABLES=tuple(row['name'] for row in DATA['candidateTables'])
API='star_oam_api';BACKUP='star_oam_backup'


def register(namespace):
    """Extend existing security allowlists, verifying every published body patch."""
    contract=DATA['registrations']
    namespace['RUNTIME_READ_TABLES']|=frozenset(contract['tables'])
    namespace['RUNTIME_INSERT_TABLES']|=frozenset(contract['insertTables'])
    for row in contract['functions']:
        coordinate=(row['name'],row['arguments'])
        if row['api']:
            definitions,shapes,digests=('RUNTIME_EXECUTE_FUNCTIONS','RUNTIME_FUNCTION_SHAPES','RUNTIME_FUNCTION_BODY_SHA256')
        else:
            definitions,shapes,digests=('FORMAL_FILE_INTERNAL_FUNCTIONS','FORMAL_FILE_INTERNAL_FUNCTION_SHAPES','FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256')
        if coordinate in namespace[definitions]:raise ValueError('0159 duplicate runtime function registration')
        definition=row['definition']
        namespace[definitions][coordinate]=(*definition[:3],tuple(definition[3]))
        namespace[shapes][coordinate]=tuple(row['shape'])
        namespace[digests][coordinate]=row['sha256']
    for row in contract['patches']:
        coordinate=(row['name'],row['arguments']);registry=namespace[row['registry']]
        if registry.get(coordinate)!=row['beforeSha256']:
            raise ValueError('0159 published runtime source discontinuity: '+row['name'])
        registry[coordinate]=row['afterSha256']
    for name,value in contract['triggers'].items():
        if name in namespace['EXPECTED_MATERIAL_REQUEST_APPROVAL_TRIGGERS']:
            raise ValueError('0159 duplicate runtime trigger registration')
        namespace['EXPECTED_MATERIAL_REQUEST_APPROVAL_TRIGGERS'][name]=tuple(value)
        table,function,enabled,kind,constraint,deferred,initial=value
        if table=='audit_events':
            namespace['EXPECTED_AUDIT_TRIGGERS'][name]=(table,function,kind,constraint,deferred,initial)

def quote(value):
    if not value.replace('_','').isalnum() or value[0].isdigit():raise ValueError('unexpected frozen identifier')
    return '"'+value+'"'

def signature(row):return 'public.'+quote(row['proname'])+'('+row['signature'].split('(',1)[1]

def verify_function(db,expected):
    row=db.execute(text('''SELECT pg_get_functiondef(p.oid) AS definition,p.proname,p.prosrc,p.prosecdef,p.provolatile,p.proparallel,p.proisstrict,
        p.proleakproof,p.proconfig,pg_get_userbyid(p.proowner) AS owner,
        pg_get_function_identity_arguments(p.oid) AS identity_arguments,
        ARRAY(SELECT jsonb_build_object('grantee',CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END,
            'privilege',a.privilege_type,'grantable',a.is_grantable)
            FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a ORDER BY a.grantee,a.privilege_type) AS acl
        FROM pg_proc p WHERE p.oid=to_regprocedure(:signature)'''),dict(signature=signature(expected))).mappings().one_or_none()
    keys=('definition','proname','prosrc','prosecdef','provolatile','proparallel','proisstrict','proleakproof','proconfig','owner','identity_arguments','acl')
    wanted={key:expected[key] for key in keys}
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
        WHERE e.extname='uuid-ossp' AND p.proname='uuid_generate_v5' AND p.pronargs=2 AND p.pronamespace=n.oid
          AND p.proargtypes[0]='uuid'::regtype AND p.proargtypes[1]='text'::regtype''')).one_or_none()
    if row is None or tuple(row)!=('1.1','rsc_loss_uuid_0159','postgres','uuid_generate_v5','$libdir/uuid-ossp','c','i',True,False,'s','uuid',False,False,True,False):
        raise ValueError('0159 separately provisioned private UUID dependency required')

def verify(db,cloud=None,*,runtime=True):
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
        table_flags=db.execute(text('''SELECT pg_get_userbyid(c.relowner),c.relkind,c.relpersistence,c.relrowsecurity,c.relforcerowsecurity,
            EXISTS(SELECT 1 FROM pg_attribute a WHERE a.attrelid=c.oid AND (a.attisdropped OR a.attacl IS NOT NULL))
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
          AND (c.relname=ANY(:tables) OR (p.pronamespace='public'::regnamespace AND right(p.proname,5) IN ('_0159','_0161')))
        ORDER BY c.relname,t.tgname'''),dict(tables=list(TABLES))).mappings()]
    if triggers!=DATA['triggers']:raise ValueError('0159 exact trigger catalog mismatch')
