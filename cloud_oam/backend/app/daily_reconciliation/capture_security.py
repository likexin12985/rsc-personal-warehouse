"""Read-only, exact capture-role checks shared by API, readers and provisioning."""
import json
from sqlalchemy import text
from .capture_role_contract import ROLES

MANIFEST = json.dumps([dict(role_name=role,tables=tables,policy_name=policy)
                       for role,(tables,policy) in ROLES.items()])

ROLE_CATALOG_SQL = """
WITH expected AS (
 SELECT * FROM jsonb_to_recordset(CAST(:manifest AS jsonb))
 AS s(role_name text,tables jsonb,policy_name text)
)
SELECT s.role_name,r.oid IS NOT NULL AS present,
 r.rolcanlogin = CAST(:login_enabled AS boolean) AND NOT (r.rolsuper OR r.rolinherit OR r.rolcreatedb OR r.rolcreaterole
   OR r.rolreplication OR r.rolbypassrls)
 AND (r.rolvaliduntil IS NULL OR r.rolvaliduntil>clock_timestamp())
 AND NOT EXISTS(SELECT 1 FROM pg_auth_members m WHERE m.member=r.oid OR m.roleid=r.oid)
 AND ARRAY(SELECT unnest(r.rolconfig) ORDER BY 1)=ARRAY['default_transaction_read_only=on','search_path=public']::text[]
 AS role_safe,
 NOT EXISTS(SELECT 1 FROM pg_database d WHERE
   d.datdba=r.oid OR has_database_privilege(r.oid,d.oid,'CREATE,TEMPORARY')
   OR (d.datallowconn AND has_database_privilege(r.oid,d.oid,'CONNECT')<>(d.datname=current_database()))
   OR EXISTS(SELECT 1 FROM aclexplode(d.datacl) a WHERE a.grantee IN (0,r.oid) AND a.is_grantable))
 AND (SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname=current_database())='star_oam_migrator'
 AS database_safe,
 has_schema_privilege(r.oid,'public','USAGE')
 AND NOT EXISTS(SELECT 1 FROM pg_namespace n WHERE n.nspowner=r.oid
   OR has_schema_privilege(r.oid,n.oid,'CREATE')
   OR (n.nspname !~ '^pg_' AND n.nspname NOT IN ('public','information_schema')
       AND has_schema_privilege(r.oid,n.oid,'USAGE'))
   OR EXISTS(SELECT 1 FROM aclexplode(n.nspacl) a WHERE a.grantee IN (0,r.oid) AND a.is_grantable))
 AS schema_safe,
 (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
   WHERE n.nspname='public' AND c.relkind='r' AND s.tables ? c.relname)=jsonb_array_length(s.tables)
 AND NOT EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
   WHERE n.nspname !~ '^pg_' AND n.nspname<>'information_schema'
   AND c.relkind IN ('r','p','v','m','f') AND (
    c.relowner=r.oid OR has_table_privilege(r.oid,c.oid,'SELECT')<>(n.nspname='public' AND s.tables ? c.relname)
    OR has_table_privilege(r.oid,c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
    OR EXISTS(SELECT 1 FROM aclexplode(c.relacl) a WHERE a.grantee IN (0,r.oid) AND a.is_grantable)))
 AS table_safe,
 NOT EXISTS(SELECT 1 FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid
   JOIN pg_namespace n ON n.oid=c.relnamespace CROSS JOIN LATERAL aclexplode(a.attacl) acl
   WHERE n.nspname !~ '^pg_' AND n.nspname<>'information_schema'
    AND a.attnum>0 AND NOT a.attisdropped AND acl.grantee IN (0,r.oid)) AS column_safe,
 NOT EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
   WHERE n.nspname !~ '^pg_' AND n.nspname<>'information_schema' AND c.relkind='S'
    AND (c.relowner=r.oid OR has_sequence_privilege(r.oid,c.oid,'SELECT,USAGE,UPDATE'))) AS sequence_safe,
 NOT EXISTS(SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
   WHERE n.nspname !~ '^pg_' AND n.nspname<>'information_schema'
    AND (p.proowner=r.oid OR has_function_privilege(r.oid,p.oid,'EXECUTE'))) AS function_safe,
 NOT EXISTS(SELECT 1 FROM pg_default_acl d CROSS JOIN LATERAL aclexplode(d.defaclacl) a
   WHERE d.defaclrole=r.oid OR a.grantee=r.oid)
 AND NOT EXISTS(SELECT 1 FROM pg_db_role_setting WHERE setrole=r.oid AND setdatabase<>0)
 AND NOT EXISTS(SELECT 1 FROM pg_type WHERE typowner=r.oid)
 AS defaults_safe,
 NOT has_parameter_privilege(r.oid,'session_replication_role','SET') AS parameter_safe,
 NOT EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
   WHERE n.nspname='public' AND s.tables ? c.relname AND c.relrowsecurity AND (
    EXISTS(SELECT 1 FROM pg_policy p WHERE p.polrelid=c.oid AND p.polcmd IN ('r','*') AND NOT p.polpermissive)
    OR (SELECT count(*) FROM pg_policy p WHERE p.polrelid=c.oid AND p.polcmd IN ('r','*')
         AND (0=ANY(p.polroles) OR r.oid=ANY(p.polroles)))<>1
    OR NOT EXISTS(SELECT 1 FROM pg_policy p WHERE p.polrelid=c.oid AND p.polname=s.policy_name
      AND p.polcmd='r' AND p.polpermissive AND p.polroles=ARRAY[r.oid]::oid[]
      AND pg_get_expr(p.polqual,p.polrelid)='true' AND p.polwithcheck IS NULL))) AS policy_safe
FROM expected s LEFT JOIN pg_roles r ON r.rolname=s.role_name ORDER BY s.role_name
"""

class CaptureRoleSecurityError(RuntimeError):
    pass

def assert_catalog(rows, *, allow_absent=False):
    if len(rows)!=len(ROLES) or {r['role_name'] for r in rows}!=set(ROLES):
        raise CaptureRoleSecurityError('daily_capture_role_set')
    if allow_absent and all(r['present'] is False for r in rows):
        return False
    failures=[r['role_name']+'.'+name for r in rows for name,value in r.items()
              if name!='role_name' and value is not True]
    if failures:
        raise CaptureRoleSecurityError('daily_capture_boundary: '+','.join(sorted(failures)))
    return True

def capture_role_rows(connection, *, login_enabled=True):
    return connection.execute(text(ROLE_CATALOG_SQL),
        dict(manifest=MANIFEST,login_enabled=login_enabled)).mappings().all()

def validate_capture_roles(connection, *, allow_absent=False):
    rows=capture_role_rows(connection)
    return assert_catalog(rows,allow_absent=allow_absent)

def validate_capture_roles_cursor(cursor):
    cursor.execute(ROLE_CATALOG_SQL.replace('%','%%').replace(':manifest','%(manifest)s')
        .replace(':login_enabled','%(login_enabled)s'),dict(manifest=MANIFEST,login_enabled=True))
    return assert_catalog(cursor.fetchall())
