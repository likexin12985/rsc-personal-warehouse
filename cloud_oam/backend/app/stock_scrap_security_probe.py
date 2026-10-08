"""Stable PG16 catalog observations, independent of application metadata.

Role/object OIDs and dropped-column tombstones are deliberately excluded.
Visible column order, every ACL, FK guard and backing index remain checked.
"""
from sqlalchemy import text


def rows(db, sql, **parameters):
    return [dict(row) for row in db.execute(text(sql), parameters).mappings()]


def snapshot(db):
    functions = rows(db, """SELECT p.proname, p.proname||'('||oidvectortypes(p.proargtypes)||')' AS signature,
        pg_get_functiondef(p.oid) AS definition,p.prosrc,p.prosecdef,p.provolatile,p.proparallel,
        p.proisstrict,p.proleakproof,p.proconfig,pg_get_userbyid(p.proowner) AS owner,
        pg_get_function_identity_arguments(p.oid) AS identity_arguments,
        ARRAY(SELECT jsonb_build_object('grantee',CASE WHEN a.grantee=0 THEN 'PUBLIC'
            ELSE pg_get_userbyid(a.grantee) END,'privilege',a.privilege_type,'grantable',a.is_grantable)
            FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
            ORDER BY CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END,
                a.privilege_type,a.is_grantable) AS acl
        FROM pg_proc p WHERE p.pronamespace='public'::regnamespace AND p.prokind IN ('f','p')
        ORDER BY p.proname,oidvectortypes(p.proargtypes)""")
    tables = {}
    for table in rows(db, """SELECT c.relname AS name,pg_get_userbyid(c.relowner) AS owner,
        c.relkind,c.relpersistence,c.relrowsecurity,c.relforcerowsecurity,c.relreplident,c.reloptions,
        ARRAY(SELECT jsonb_build_object('grantee',CASE WHEN a.grantee=0 THEN 'PUBLIC'
            ELSE pg_get_userbyid(a.grantee) END,'privilege',a.privilege_type,'grantable',a.is_grantable)
            FROM aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) a
            ORDER BY CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END,
                a.privilege_type,a.is_grantable) AS acl
        FROM pg_class c WHERE c.relnamespace='public'::regnamespace AND c.relkind IN ('r','p')
        ORDER BY c.relname"""):
        name = table['name']
        parameters = dict(table='public.' + name)
        table['columns'] = rows(db, """SELECT a.attname AS name,format_type(a.atttypid,a.atttypmod) AS type,
            a.attnotnull AS not_null,pg_get_expr(d.adbin,d.adrelid) AS default_sql,
            a.attidentity,a.attgenerated,a.attcollation::regcollation::text AS collation,
            ARRAY(SELECT jsonb_build_object('grantee',CASE WHEN x.grantee=0 THEN 'PUBLIC'
                ELSE pg_get_userbyid(x.grantee) END,'privilege',x.privilege_type,'grantable',x.is_grantable)
                FROM aclexplode(a.attacl) x ORDER BY CASE WHEN x.grantee=0 THEN 'PUBLIC'
                ELSE pg_get_userbyid(x.grantee) END,x.privilege_type,x.is_grantable) AS acl
            FROM pg_attribute a LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
            WHERE a.attrelid=to_regclass(:table) AND a.attnum>0 AND NOT a.attisdropped
            ORDER BY a.attnum""", **parameters)
        table['constraints'] = rows(db, """SELECT conname AS name,contype AS type,
            pg_get_constraintdef(oid) AS definition,convalidated,condeferrable,condeferred
            FROM pg_constraint WHERE conrelid=to_regclass(:table) AND contype<>'t' ORDER BY conname""", **parameters)
        table['indexes'] = rows(db, """SELECT c.relname AS name,pg_get_indexdef(i.indexrelid) AS definition,
            i.indisunique,i.indisvalid,i.indisready,i.indimmediate,i.indisreplident
            FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid
            WHERE i.indrelid=to_regclass(:table) ORDER BY c.relname""", **parameters)
        table['triggers'] = rows(db, """SELECT t.tgname AS name,pg_get_triggerdef(t.oid) AS definition,
            t.tgenabled,t.tgtype::int,t.tgdeferrable,t.tginitdeferred,
            p.proname||'('||oidvectortypes(p.proargtypes)||')' AS function_signature
            FROM pg_trigger t JOIN pg_proc p ON p.oid=t.tgfoid
            WHERE t.tgrelid=to_regclass(:table) AND NOT t.tgisinternal ORDER BY t.tgname""", **parameters)
        table['fk_guards'] = rows(db, """SELECT k.conname AS constraint_name,c.relname AS table_name,
            p.proname AS function_name,t.tgenabled,t.tgtype::int,t.tgdeferrable,t.tginitdeferred
            FROM pg_constraint k JOIN pg_trigger t ON t.tgconstraint=k.oid
            JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_proc p ON p.oid=t.tgfoid
            WHERE k.conrelid=to_regclass(:table) AND k.contype='f' AND t.tgisinternal
            ORDER BY k.conname,c.relname,p.proname,t.tgtype""", **parameters)
        tables[name] = table
    return dict(tables=tables, functions={row['signature']: row for row in functions})


def difference(before, after):
    """Freeze only changed objects, retaining both sides and exact absences."""
    return {kind: {name: dict(before=before[kind].get(name), after=after[kind].get(name))
                  for name in sorted(set(before[kind]) | set(after[kind]))
                  if before[kind].get(name) != after[kind].get(name)}
            for kind in ('tables', 'functions')}
