"""Expose a narrow daily report view without giving the API raw archive access.

The projection contains no actor/session or raw national ledger evidence.
Downgrade drops only this rebuildable view/index and preserves every fact.
"""
from pathlib import Path
import runpy
from alembic import op

revision='20261110_0131'
down_revision='20261109_0130'
branch_labels=depends_on=None
OLD_HASH='ef4d75933015da040fbb6f632cab2c7741566608d87949bc391dcdf8cc6e7d7e'
NEW_HASH='c25cc7581169076ad39752300007a3dcc651daef9337b37dad1c0d1fc88441d5'
VIEW_SQL="""CREATE VIEW public.daily_reconciliation_reports
WITH (security_barrier=true,security_invoker=false) AS
SELECT c.id AS cutoff_id, c.business_date, c.source_system_id, c.region_org_id,
 c.source_publication_id, c.mapping_decision_id, c.local_ledger_cursor,
 c.source_captured_at, c.local_captured_at, c.created_at,
 c.payload_sha256 AS cutoff_sha256,
 c.payload_jsonb #>> '{comparison,comparison,status}' AS comparison_status,
 c.payload_jsonb #>> '{comparison,comparison,report_sha256}' AS comparison_sha256,
 c.payload_jsonb #> '{mapping,warehouses}' AS covered_warehouses,
 c.payload_jsonb #> '{mapping,included_buckets}' AS included_buckets,
 c.payload_jsonb #> '{comparison,comparison,items}' AS comparison_items,
 c.payload_jsonb #> '{comparison,comparison,excluded_local_quantities}' AS excluded_quantities
FROM public.daily_reconciliation_cutoffs AS c"""
INDEX_SQL='CREATE INDEX ix_daily_cutoff_region_id ON daily_reconciliation_cutoffs (region_org_id, id)'
# Frozen snapshot: never import mutable application code from a migration.
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


def _transition(up):
    dialect=op.get_bind().dialect.name
    if dialect not in {'postgresql','sqlite'}:raise RuntimeError('0131 unsupported database')
    folder=Path(__file__).parent
    helper=runpy.run_path(str(folder/'20260927_0087_inbound_fulfillment_boundary.py'))
    helper['_begin_sqlite']()
    if dialect=='postgresql':
        op.execute("DO $owner_0131$ BEGIN IF current_user<>'star_oam_migrator' OR session_user<>'star_oam_migrator' THEN RAISE EXCEPTION '0131 direct schema owner required'; END IF; END $owner_0131$")
        op.execute('LOCK TABLE public.alembic_version, public.daily_reconciliation_cutoffs IN ACCESS EXCLUSIVE MODE')
        if up:
            op.execute(VIEW_SQL)
            op.execute(INDEX_SQL)
            op.execute('REVOKE ALL ON public.daily_reconciliation_reports FROM PUBLIC,star_oam_api,star_oam_projector,star_oam_edge,edge_inbox')
            op.execute('GRANT SELECT ON public.daily_reconciliation_reports TO star_oam_api,star_oam_backup')
        # Offline SQL cannot execute a Python-side catalog read. Embed the same
        # exact predicates as a SQL assertion for both offline and online runs.
        import json
        catalog=str(CATALOG_SQL).replace(':view_sha',"'"+VIEW_SHA256+"'")
        expected=json.dumps(COLUMNS,separators=(',',':'))
        op.execute("DO $view_0131$ BEGIN IF NOT EXISTS (SELECT 1 FROM ("+catalog+") c WHERE c.shape_safe AND c.acl_safe AND c.rules_safe AND c.columns='"+expected+"'::jsonb) THEN RAISE EXCEPTION '0131 query catalog drift'; END IF; END $view_0131$")
        if not up:
            op.execute('DROP VIEW public.daily_reconciliation_reports')
            op.execute('DROP INDEX public.ix_daily_cutoff_region_id')
        replace=runpy.run_path(str(folder/'20260909_0069_stock_reservations.py'))['_replace_function_source']
        replace(signature='public.rsc_oam_runtime_binding_ready_0044()',expected_hash=OLD_HASH if up else NEW_HASH,
            replacement_hash=NEW_HASH if up else OLD_HASH,replacements=((down_revision,revision),) if up else ((revision,down_revision),),label='daily_query_readiness_0131')
    elif up:
        import re
        sql=VIEW_SQL.replace('public.','').replace('WITH (security_barrier=true,security_invoker=false) AS','AS')
        sql=re.sub(r"c.payload_jsonb #>>? '\{([^}]+)\}'",lambda m:"json_extract(c.payload_jsonb, '$."+m[1].replace(',','.')+"')",sql)
        op.execute(sql)
        op.execute(INDEX_SQL)
    else:
        op.execute('DROP VIEW daily_reconciliation_reports')
        op.execute('DROP INDEX ix_daily_cutoff_region_id')

def upgrade():_transition(True)
def downgrade():_transition(False)
