"""Dedicated review file purpose; retain every earlier file and review fact."""
from pathlib import Path
import hashlib,runpy
from alembic import op
revision='20261112_0133'
down_revision='20261111_0132'
branch_labels=depends_on=None
PURPOSE='daily_reconciliation_evidence'
_FOLDER=Path(__file__).parent
_files=runpy.run_path(str(_FOLDER/'20261030_0120_source_configuration_files.py'))
_review=runpy.run_path(str(_FOLDER/'20261111_0132_daily_reconciliation_review.py'))
OLD_HASH=_review['NEW_HASH']
# Current readiness body is frozen through the historical source chain.
_prior=runpy.run_path(str(_FOLDER/'20261108_0129_notification_expansion_status.py'))
NEW_HASH=hashlib.sha256(_prior['_ready'].replace(_prior['_ready_parent'],revision).encode()).hexdigest()

def files(expanded):
    m=_files['files'](True)
    if expanded:
        g=m['_postgresql_file_function_sql'].__globals__
        old=g['FORMAL_PURPOSES_SQL'];assert old.endswith("'source_configuration_evidence')")
        g['FORMAL_PURPOSES_SQL']=old[:-1]+", '"+PURPOSE+"')"
    return m

def uploader_sql(dialect,alias='NEW'):
    prefix='public.' if dialect=='postgresql' else ''
    now='clock_timestamp()' if dialect=='postgresql' else 'CURRENT_TIMESTAMP'
    target="CAST(a.scope_id AS uuid)" if dialect=='postgresql' else "replace(a.scope_id,'-','')"
    return f"""EXISTS(SELECT 1 FROM {prefix}users u JOIN {prefix}people p ON p.id=u.person_id
     JOIN {prefix}organizations o ON o.id=p.organization_id JOIN {prefix}role_assignments a ON a.user_id=u.id
     JOIN {prefix}roles r ON r.id=a.role_id WHERE u.id={alias}.uploaded_by AND u.is_active AND u.account_status='active'
      AND p.employment_status='active' AND o.status='active' AND r.code='provincial_manager' AND r.status='active' AND NOT r.is_external
      AND a.scope_type='organization' AND a.status IN ('active','scheduled') AND a.revoked_at IS NULL
      AND a.valid_from<={now} AND (a.valid_to IS NULL OR a.valid_to>{now})
      AND EXISTS(SELECT 1 FROM {prefix}organizations target WHERE target.id={target} AND target.status='active' AND target.org_type='region_company')
      AND EXISTS(SELECT 1 FROM {prefix}role_permissions rp JOIN {prefix}permissions pm ON pm.id=rp.permission_id
       WHERE rp.role_id=r.id AND rp.effect='allow' AND pm.resource='reconciliation' AND pm.action='explain_daily' AND pm.field_code='')
      AND EXISTS(SELECT 1 FROM {prefix}role_permissions rp JOIN {prefix}permissions pm ON pm.id=rp.permission_id
       WHERE rp.role_id=r.id AND rp.effect='allow' AND pm.resource='reconciliation' AND pm.action='read' AND pm.field_code='')
      AND NOT EXISTS(SELECT 1 FROM {prefix}role_assignments da JOIN {prefix}roles dr ON dr.id=da.role_id
       JOIN {prefix}role_permissions dp ON dp.role_id=dr.id JOIN {prefix}permissions d ON d.id=dp.permission_id
       WHERE da.user_id=u.id AND dr.status='active' AND da.status IN ('active','scheduled') AND da.revoked_at IS NULL
        AND da.valid_from<={now} AND (da.valid_to IS NULL OR da.valid_to>{now}) AND dp.effect='deny'
        AND d.resource='reconciliation' AND d.action IN ('read','explain_daily'))
    )"""

def sources():
    old=_files['source_changes']()[1]
    expanded=old.replace("'source_configuration_evidence')", "'source_configuration_evidence', '"+PURPOSE+"')")
    assert expanded!=old
    check=f"""
    IF TG_OP IN ('INSERT','UPDATE') AND NEW.metadata_jsonb->>'purpose'='{PURPOSE}' THEN
        PERFORM public.rsc_lock_formal_principal_graph_0026(ARRAY[NEW.uploaded_by]::text[]);
        IF NOT ({uploader_sql('postgresql')}) THEN
            RAISE EXCEPTION '0133 current regional explanation authority required' USING ERRCODE='23514';
        END IF;
    END IF;
"""
    new=expanded.replace('BEGIN\n','BEGIN\n'+check,1)
    live=_review['FUNCTIONS']['rsc_daily_review_live_0132'][2]
    binding=files(True)['_postgresql_binding_file_sql'](file_expression="(f->>'file_id')::uuid",purpose=PURPOSE,
        user_expression='file_row.uploaded_by',person_expression=None,bound_at_expression='e.created_at',require_current_identity=False)
    branch=f"""
  IF NOT ({binding}) OR (op='explain' AND NOT EXISTS(SELECT 1 FROM public.files x
    WHERE x.id=(f->>'file_id')::uuid AND x.metadata_jsonb->>'uploader_person_id'=e.actor_person_id::text
     AND x.metadata_jsonb->>'authorization_version'=e.payload_jsonb#>>'{{actor,authorization_version}}'))
  THEN RAISE EXCEPTION '0133 completed daily review evidence required' USING ERRCODE='23514'; END IF;
"""
    needle=" FOR f IN SELECT value FROM jsonb_array_elements(e.payload_jsonb->'evidence') LOOP\n"
    assert needle in live
    return {'public.rsc_guard_formal_file_object_0036()':(old,new),
            'public.rsc_daily_review_live_0132(public.daily_review_events,timestamptz)':(live,live.replace(needle,needle+branch,1))}

SOURCES=sources()
HASHES={k:tuple(hashlib.sha256(v.encode()).hexdigest() for v in pair) for k,pair in SOURCES.items()}

def _sqlite_files(expanded):
    m=files(expanded)
    for name in m['_sqlite_trigger_names']():op.execute('DROP TRIGGER '+name)
    m['_create_sqlite_triggers']()
    for event in ('INSERT','UPDATE'):
        name='trg_files_daily_reviewer_'+event.lower()+'_0133'
        if expanded:op.execute(f"CREATE TRIGGER {name} BEFORE {event} ON files WHEN json_extract(NEW.metadata_jsonb,'$.purpose')='{PURPOSE}' AND NOT ({uploader_sql('sqlite')}) BEGIN SELECT RAISE(ABORT,'0133 current regional explanation authority required'); END")
        else:op.execute('DROP TRIGGER '+name)

def _transition(up):
    d=op.get_bind().dialect.name
    helper=runpy.run_path(str(_FOLDER/'20260927_0087_inbound_fulfillment_boundary.py'))
    helper['_begin_sqlite']()
    if d=='postgresql':
        op.execute("DO $$ BEGIN IF current_user<>'star_oam_migrator' OR session_user<>'star_oam_migrator' THEN RAISE EXCEPTION '0133 direct owner required'; END IF; END $$")
        op.execute('LOCK TABLE public.alembic_version,public.files,public.daily_review_events,public.daily_review_bindings,public.daily_review_consumptions IN ACCESS EXCLUSIVE MODE')
        if up:
            helper['_preflight']("EXISTS(SELECT 1 FROM daily_review_events e CROSS JOIN LATERAL jsonb_array_elements(e.payload_jsonb->'evidence') f LEFT JOIN files x ON x.id=(f->>'file_id')::uuid WHERE x.metadata_jsonb->>'purpose' IS DISTINCT FROM '"+PURPOSE+"')",'0133 existing review evidence requires explicit migration; no relabeling')
        else:
            helper['_preflight']("EXISTS(SELECT 1 FROM files WHERE metadata_jsonb->>'purpose'='"+PURPOSE+"')",'0133 daily evidence must be retained')
        replace=runpy.run_path(str(_FOLDER/'20260909_0069_stock_reservations.py'))['_replace_function_source']
        for signature,(old,new) in SOURCES.items():
            replace(signature=signature,expected_hash=HASHES[signature][0 if up else 1],replacement_hash=HASHES[signature][1 if up else 0],
                replacements=((old,new),) if up else ((new,old),),label='daily_evidence_0133')
        replace(signature='public.rsc_oam_runtime_binding_ready_0044()',expected_hash=OLD_HASH if up else NEW_HASH,replacement_hash=NEW_HASH if up else OLD_HASH,
            replacements=((down_revision,revision),) if up else ((revision,down_revision),),label='daily_evidence_readiness_0133')
    elif d=='sqlite':
        if up:helper['_preflight']("EXISTS(SELECT 1 FROM daily_review_events e,json_each(e.payload_jsonb,'$.evidence') f LEFT JOIN files x ON x.id=replace(json_extract(f.value,'$.file_id'),'-','') WHERE json_extract(x.metadata_jsonb,'$.purpose') IS NOT '"+PURPOSE+"')",'0133 existing review evidence requires explicit migration; no relabeling')
        else:helper['_preflight']("EXISTS(SELECT 1 FROM files WHERE json_extract(metadata_jsonb,'$.purpose')='"+PURPOSE+"')",'0133 daily evidence must be retained')
        _sqlite_files(up)
    else:raise RuntimeError('0133 PostgreSQL or SQLite required')

def upgrade():_transition(True)
def downgrade():_transition(False)
