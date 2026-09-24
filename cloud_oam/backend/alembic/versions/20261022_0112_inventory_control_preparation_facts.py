"""Preserve control preparation versions; no operational publisher permission.

Source, catalogue and capture authority are not conferred by this migration.
Existing OAM projections, SyncRun payloads and opening admission are unchanged.
"""
import hashlib
from pathlib import Path
import runpy

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "20261022_0112"
down_revision = "20261021_0111"
branch_labels = depends_on = None
OLD_HASH = "13c5383d20ecc1bbb4a503a19dbd92f52259a61eea7a19d6c7fd3747f8109e13"
NEW_HASH = "a00b94ede4e9dcefb19656119fc9de62345a6f10987cebde49d392d04de29ab8"
TABLES = ("inventory_control_source_bindings", "inventory_control_catalog_versions",
    "inventory_control_capture_chains", "inventory_control_capture_snapshots", "inventory_control_preparations")
FUNCTION_NAME = "rsc_guard_inventory_control_facts_0112"
BODY = """
DECLARE
    parent jsonb;
    document jsonb;
    expected_hash text;
BEGIN
    IF TG_OP<>'INSERT' THEN
        RAISE EXCEPTION '0112 control preparation facts are append-only' USING ERRCODE='23514';
    END IF;
    IF current_user<>'star_oam_migrator' THEN
        RAISE EXCEPTION '0112 control preparation has no runtime writer' USING ERRCODE='42501';
    END IF;
    IF TG_TABLE_NAME='inventory_control_source_bindings' THEN
        document:=NEW.binding_jsonb; expected_hash:=NEW.binding_sha256;
        IF jsonb_typeof(document) IS DISTINCT FROM 'object'
           OR (SELECT count(*) FROM jsonb_object_keys(document))<>5
           OR NOT (document ?& ARRAY['source_system','source_instance','company_id','org_code','scope_key'])
           OR (SELECT bool_and(jsonb_typeof(value)='string' AND (value#>>'{}') ~ '^[A-Za-z0-9._:-]{1,160}$') FROM jsonb_each(document)) IS DISTINCT FROM true THEN
            RAISE EXCEPTION '0112 control binding shape mismatch' USING ERRCODE='23514';
        END IF;
        IF document->>'source_system' IS DISTINCT FROM 'starcharge_oam'
           OR NOT EXISTS (SELECT 1 FROM public.source_systems s WHERE s.id=NEW.source_system_id AND lower(btrim(s.code))='oam' AND s.mode='read_only' AND s.enabled)
           OR NOT EXISTS (SELECT 1 FROM public.organizations o WHERE o.id=NEW.region_org_id AND o.org_type='region_company' AND o.status='active') THEN
            RAISE EXCEPTION '0112 control source bridge mismatch' USING ERRCODE='23514';
        END IF;
    ELSIF TG_TABLE_NAME='inventory_control_catalog_versions' THEN
        document:=NEW.catalog_jsonb; expected_hash:=NEW.catalog_sha256;
        SELECT to_jsonb(b) INTO parent FROM public.inventory_control_source_bindings b WHERE b.id=NEW.binding_id;
        IF parent IS NULL OR document->>'catalog_revision' IS DISTINCT FROM NEW.catalog_revision
           OR document->>'target_region_code' IS DISTINCT FROM parent->>'target_region_code'
           OR jsonb_typeof(document->'warehouses') IS DISTINCT FROM 'array' OR jsonb_array_length(document->'warehouses')<1 THEN
            RAISE EXCEPTION '0112 control catalogue binding mismatch' USING ERRCODE='23514';
        END IF;
    ELSIF TG_TABLE_NAME='inventory_control_capture_chains' THEN
        document:=NEW.evidence_jsonb; expected_hash:=NEW.capture_chain_sha256;
        IF document->>'schema_version' IS DISTINCT FROM 'rsc.inventory_control_coverage.v1'
           OR jsonb_typeof(document->'snapshots') IS DISTINCT FROM 'array'
           OR jsonb_array_length(document->'snapshots')<>NEW.snapshot_count THEN
            RAISE EXCEPTION '0112 control capture shape mismatch' USING ERRCODE='23514';
        END IF;
    ELSIF TG_TABLE_NAME='inventory_control_capture_snapshots' THEN
        SELECT evidence_jsonb->'snapshots'->(NEW.sequence::integer-1) INTO document
            FROM public.inventory_control_capture_chains WHERE id=NEW.capture_chain_id;
        expected_hash:=NEW.evidence_sha256;
        IF document IS NULL OR NEW.staging_sha256 !~ '^[a-f0-9]{64}$'
           OR EXISTS (SELECT 1 FROM public.inventory_control_preparations WHERE capture_chain_id=NEW.capture_chain_id)
           OR NOT EXISTS (SELECT 1 FROM public.external_sync_snapshots s
                WHERE s.id=NEW.snapshot_ref_id AND s.status='complete'
                  AND s.snapshot_id=document->'manifest'->>'snapshot_id'
                  AND s.source_instance=document->>'source_instance'
                  AND s.manifest_sha256=NEW.manifest_sha256) THEN
            RAISE EXCEPTION '0112 exact capture snapshot required' USING ERRCODE='23514';
        END IF;
    ELSIF TG_TABLE_NAME='inventory_control_preparations' THEN
        document:=NEW.control_manifest_jsonb; expected_hash:=NEW.control_manifest_sha256;
        IF document->>'schema_version' IS DISTINCT FROM 'rsc.inventory_control_publication.v1'
           OR document->>'evidence_status' IS DISTINCT FROM 'evidence_consistent'
           OR document->'projection_published' IS DISTINCT FROM 'false'::jsonb
           OR document->'start_ready' IS DISTINCT FROM 'false'::jsonb
           OR NOT EXISTS (SELECT 1 FROM public.inventory_control_source_bindings b
                JOIN public.inventory_control_catalog_versions c ON c.binding_id=b.id
                JOIN public.inventory_control_capture_chains a ON a.catalog_id=c.id
                WHERE b.id=NEW.binding_id AND c.id=NEW.catalog_id AND a.id=NEW.capture_chain_id
                  AND document->>'source_binding_sha256'=b.binding_sha256
                  AND document->>'catalog_sha256'=c.catalog_sha256
                  AND document->>'capture_chain_sha256'=a.capture_chain_sha256
                  AND (SELECT count(*) FROM public.inventory_control_capture_snapshots s WHERE s.capture_chain_id=a.id)=a.snapshot_count) THEN
            RAISE EXCEPTION '0112 preparation graph cannot confer publication' USING ERRCODE='23514';
        END IF;
    ELSE RAISE EXCEPTION '0112 unexpected control fact table' USING ERRCODE='23514';
    END IF;
    IF document IS NULL OR expected_hash !~ '^[a-f0-9]{64}$'
       OR expected_hash<>encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(document),'UTF8')),'hex') THEN
        RAISE EXCEPTION '0112 control fact digest mismatch' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
"""
FUNCTION_HASH = hashlib.sha256(BODY.encode()).hexdigest()
TRIGGERS = {f"trg_{table}_facts_0112": (table,"INSERT OR UPDATE OR DELETE",31) for table in TABLES}
TRIGGERS.update({f"trg_{table}_truncate_0112": (table,"TRUNCATE",34) for table in TABLES})


def _create_tables():
    j=sa.JSON().with_variant(JSONB(),'postgresql')
    def identifier():return sa.Column('id',sa.Uuid(),primary_key=True)
    def created():return sa.Column('created_at',sa.DateTime(timezone=True),nullable=False)
    op.create_table(TABLES[0],identifier(),created(),
        sa.Column('source_system_id',sa.Uuid(),sa.ForeignKey('source_systems.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('region_org_id',sa.Uuid(),sa.ForeignKey('organizations.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('target_region_code',sa.String(160),nullable=False),sa.Column('binding_jsonb',j,nullable=False),sa.Column('binding_sha256',sa.String(64),nullable=False),
        sa.UniqueConstraint('source_system_id','region_org_id','target_region_code','binding_sha256',name='uq_control_binding_identity'),
        sa.CheckConstraint('length(binding_sha256)=64 AND length(target_region_code) BETWEEN 1 AND 160',name='ck_control_binding_context'))
    op.create_table(TABLES[1],identifier(),created(),
        sa.Column('binding_id',sa.Uuid(),sa.ForeignKey(TABLES[0]+'.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('catalog_revision',sa.String(160),nullable=False),sa.Column('catalog_jsonb',j,nullable=False),sa.Column('catalog_sha256',sa.String(64),nullable=False),
        sa.UniqueConstraint('binding_id','catalog_revision',name='uq_control_catalog_revision'),sa.UniqueConstraint('id','binding_id',name='uq_control_catalog_binding'),
        sa.CheckConstraint('length(catalog_sha256)=64 AND length(catalog_revision) BETWEEN 1 AND 160',name='ck_control_catalog_context'))
    op.create_table(TABLES[2],identifier(),created(),
        sa.Column('catalog_id',sa.Uuid(),sa.ForeignKey(TABLES[1]+'.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('evidence_jsonb',j,nullable=False),sa.Column('capture_chain_sha256',sa.String(64),nullable=False),sa.Column('snapshot_count',sa.BigInteger(),nullable=False),
        sa.UniqueConstraint('catalog_id','capture_chain_sha256',name='uq_control_capture_content'),sa.UniqueConstraint('id','catalog_id',name='uq_control_capture_catalog'),
        sa.CheckConstraint('length(capture_chain_sha256)=64 AND snapshot_count BETWEEN 1 AND 64',name='ck_control_capture_context'))
    op.create_table(TABLES[3],identifier(),created(),
        sa.Column('capture_chain_id',sa.Uuid(),sa.ForeignKey(TABLES[2]+'.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('sequence',sa.BigInteger(),nullable=False),
        sa.Column('snapshot_ref_id',sa.String(36),sa.ForeignKey('external_sync_snapshots.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('manifest_sha256',sa.String(64),nullable=False),sa.Column('evidence_sha256',sa.String(64),nullable=False),sa.Column('staging_sha256',sa.String(64),nullable=False),
        sa.UniqueConstraint('capture_chain_id','sequence',name='uq_control_capture_sequence'),sa.UniqueConstraint('capture_chain_id','snapshot_ref_id',name='uq_control_capture_snapshot'),
        sa.CheckConstraint('sequence BETWEEN 1 AND 64 AND length(manifest_sha256)=64 AND length(evidence_sha256)=64 AND length(staging_sha256)=64',name='ck_control_snapshot_context'))
    op.create_table(TABLES[4],identifier(),created(),
        sa.Column('binding_id',sa.Uuid(),nullable=False),sa.Column('catalog_id',sa.Uuid(),nullable=False),sa.Column('capture_chain_id',sa.Uuid(),nullable=False),
        sa.Column('checked_at',sa.DateTime(timezone=True),nullable=False),sa.Column('control_manifest_jsonb',j,nullable=False),sa.Column('control_manifest_sha256',sa.String(64),nullable=False),
        sa.ForeignKeyConstraint(['catalog_id','binding_id'],[TABLES[1]+'.id',TABLES[1]+'.binding_id'],ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['capture_chain_id','catalog_id'],[TABLES[2]+'.id',TABLES[2]+'.catalog_id'],ondelete='RESTRICT'),
        sa.UniqueConstraint('capture_chain_id','control_manifest_sha256',name='uq_control_preparation_content'),
        sa.CheckConstraint('length(control_manifest_sha256)=64 AND checked_at<=created_at',name='ck_control_preparation_context'))


def _transition(upgrade):
    dialect=op.get_bind().dialect.name
    if dialect not in {'sqlite','postgresql'}:raise RuntimeError('0112 supports PostgreSQL and SQLite only')
    folder=Path(__file__).parent
    helper=runpy.run_path(str(folder/'20260927_0087_inbound_fulfillment_boundary.py'))
    helper['_begin_sqlite']()
    if dialect=='postgresql':op.execute('LOCK TABLE public.alembic_version IN ACCESS EXCLUSIVE MODE')
    if upgrade:_create_tables()
    else:
        if dialect=='postgresql':op.execute('LOCK TABLE '+', '.join('public.'+table for table in TABLES)+' IN ACCESS EXCLUSIVE MODE')
        helper['_preflight'](' OR '.join(f'EXISTS (SELECT 1 FROM {table})' for table in TABLES),
            '0112 downgrade blocked: control preparation facts must be retained')
    if dialect=='postgresql':
        if upgrade:
            op.execute(f'CREATE FUNCTION public.{FUNCTION_NAME}() RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog, public AS $body${BODY}$body$')
            op.execute(f'REVOKE ALL ON FUNCTION public.{FUNCTION_NAME}() FROM PUBLIC')
            op.execute(f"""DO $body$ DECLARE entry record; BEGIN
                FOR entry IN SELECT DISTINCT a.grantee FROM pg_proc p,
                    LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
                    WHERE p.oid='public.{FUNCTION_NAME}()'::regprocedure AND a.grantee<>p.proowner
                LOOP EXECUTE format('REVOKE ALL ON FUNCTION public.{FUNCTION_NAME}() FROM %s',
                    CASE WHEN entry.grantee=0 THEN 'PUBLIC' ELSE quote_ident(pg_get_userbyid(entry.grantee)) END); END LOOP;
            END $body$""")
            for table in TABLES:
                # Clear every inherited default table grant, then allow only
                # backup reads. Operational roles have no access to these facts.
                op.execute(f"""DO $body$ DECLARE entry record; BEGIN
                    FOR entry IN SELECT DISTINCT a.grantee FROM pg_class c,
                        LATERAL aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) a
                        WHERE c.oid='public.{table}'::regclass AND a.grantee<>c.relowner
                    LOOP EXECUTE format('REVOKE ALL ON TABLE public.{table} FROM %s',
                        CASE WHEN entry.grantee=0 THEN 'PUBLIC' ELSE quote_ident(pg_get_userbyid(entry.grantee)) END); END LOOP;
                    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='star_oam_backup') THEN
                        GRANT SELECT ON TABLE public.{table} TO star_oam_backup;
                    END IF;
                END $body$""")
            for name,(table,events,_) in TRIGGERS.items():
                op.execute(f"CREATE TRIGGER {name} BEFORE {events} ON public.{table} FOR EACH {'STATEMENT' if events=='TRUNCATE' else 'ROW'} EXECUTE FUNCTION public.{FUNCTION_NAME}()")
                op.execute(f'ALTER TABLE public.{table} ENABLE ALWAYS TRIGGER {name}')
        else:
            op.execute(f"""DO $body$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_proc p
                WHERE p.oid='public.{FUNCTION_NAME}()'::regprocedure
                  AND encode(sha256(convert_to(p.prosrc,'UTF8')),'hex')='{FUNCTION_HASH}'
                  AND p.proowner=(SELECT oid FROM pg_roles WHERE rolname=current_user) AND NOT p.prosecdef
                  AND p.proconfig=ARRAY['search_path=pg_catalog, public']
                  AND NOT EXISTS (SELECT 1 FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a WHERE a.grantee<>p.proowner))
                THEN RAISE EXCEPTION '0112 guard ownership, source or ACL drift'; END IF; END $body$""")
            for name,(table,*_) in TRIGGERS.items():op.execute(f'DROP TRIGGER {name} ON public.{table}')
            op.execute(f'DROP FUNCTION public.{FUNCTION_NAME}()')
        replace=runpy.run_path(str(folder/'20260912_0072_outbound_postings.py'))['_previous']()['_previous']()['_previous']()['_replace_function_source']
        replace(signature='public.rsc_oam_runtime_binding_ready_0044()',expected_hash=OLD_HASH if upgrade else NEW_HASH,
            replacement_hash=NEW_HASH if upgrade else OLD_HASH,replacements=((down_revision,revision),) if upgrade else ((revision,down_revision),),label='control_preparation_readiness_0112')
    elif upgrade:
        for table in TABLES:
            for event in ('UPDATE','DELETE'):
                op.execute(f"CREATE TRIGGER trg_{table}_{event.lower()}_0112 BEFORE {event} ON {table} BEGIN SELECT RAISE(ABORT,'0112 control preparation facts are append-only'); END")
    if not upgrade:
        for table in reversed(TABLES):op.drop_table(table)


def upgrade():_transition(True)
def downgrade():_transition(False)
