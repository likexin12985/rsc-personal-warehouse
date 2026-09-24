"""Exact catalog for the public, read-only daily comparison projection."""
from sqlalchemy import text

VIEW_NAME = 'daily_reconciliation_reports'
VIEW_SHA256 = '23923ad9e8e4ccd6ec29eb15ca4e41dc85194999d04e15f11f3aad532f7faea1'
COLUMNS = [
    ['cutoff_id','uuid'], ['business_date','date'], ['source_system_id','uuid'],
    ['region_org_id','uuid'], ['source_publication_id','uuid'], ['mapping_decision_id','uuid'],
    ['local_ledger_cursor','bigint'], ['source_captured_at','timestamp with time zone'],
    ['local_captured_at','timestamp with time zone'], ['created_at','timestamp with time zone'],
    ['cutoff_sha256','character varying(64)'], ['comparison_status','text'],
    ['comparison_sha256','text'], ['covered_warehouses','jsonb'], ['included_buckets','jsonb'],
    ['comparison_items','jsonb'], ['excluded_quantities','jsonb'],
]
CATALOG_SQL = text("""
SELECT c.relkind='v' AND r.rolname='star_oam_migrator'
 AND c.reloptions @> ARRAY['security_barrier=true','security_invoker=false']
 AND cardinality(c.reloptions)=2 AND NOT c.relrowsecurity AND NOT c.relforcerowsecurity
 AND encode(sha256(convert_to(pg_get_viewdef(c.oid,true),'UTF8')),'hex')=:view_sha AS shape_safe,
 (SELECT jsonb_agg(jsonb_build_array(a.attname,format_type(a.atttypid,a.atttypmod)) ORDER BY a.attnum)
  FROM pg_attribute a WHERE a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped) AS columns,
 NOT EXISTS (SELECT 1 FROM aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) a
  LEFT JOIN pg_roles g ON g.oid=a.grantee WHERE a.grantee<>c.relowner
   AND NOT (COALESCE(g.rolname,'') IN ('star_oam_api','star_oam_backup')
     AND a.privilege_type='SELECT' AND NOT a.is_grantable))
 AND has_table_privilege('star_oam_api',c.oid,'SELECT')
 AND has_table_privilege('star_oam_backup',c.oid,'SELECT')
 AND NOT EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid=c.oid AND a.attacl IS NOT NULL) AS acl_safe,
 (SELECT count(*)=1 AND bool_and(w.rulename='_RETURN' AND w.ev_type='1' AND w.is_instead AND w.ev_enabled='O')
  FROM pg_rewrite w WHERE w.ev_class=c.oid)
 AND NOT EXISTS (SELECT 1 FROM pg_trigger t WHERE t.tgrelid=c.oid) AS rules_safe
FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_roles r ON r.oid=c.relowner
WHERE n.nspname='public' AND c.relname='daily_reconciliation_reports'
""")

class DailyQuerySecurityError(RuntimeError):
    pass

def validate_query_catalog(connection):
    rows=connection.execute(CATALOG_SQL,{'view_sha':VIEW_SHA256}).mappings().all()
    if len(rows)!=1 or rows[0]['columns']!=COLUMNS or any(
        rows[0][key] is not True for key in ('shape_safe','acl_safe','rules_safe')
    ):
        raise DailyQuerySecurityError('daily_reconciliation_query_catalog_drift')
