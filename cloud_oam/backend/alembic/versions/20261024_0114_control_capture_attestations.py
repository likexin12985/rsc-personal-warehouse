"""Authenticated inventory capture receipts, exact edge scope and retention."""
import hashlib
from pathlib import Path
import runpy

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '20261024_0114'
down_revision = '20261023_0113'
branch_labels = depends_on = None
OLD_HASH = '5490b4be13d675f266046001f6196d839f08108c6560b5acf07f3ab88a413aa8'
NEW_HASH = '896564c364887715d00d349b51053db48b2c8517d2f0f8f17fc06c17880b9ed5'
TABLE = 'inventory_control_capture_attestations'
FUNCTION_NAME = 'rsc_oam_capture_attestation_guard_0114'
POLICY = "rsc_oam_rls_check_0044('external_sync_snapshot_records'::text, 'select'::text, to_jsonb(inventory_control_capture_attestations.*))"
BODY = """
DECLARE
    snapshot public.external_sync_snapshots%ROWTYPE;
    p jsonb;
    began timestamptz;
    ended timestamptz;
BEGIN
    IF TG_OP<>'INSERT' THEN
        RAISE EXCEPTION '0114 capture receipts are append-only' USING ERRCODE='23514';
    END IF;
    IF session_user<>'edge_inbox' OR current_user<>'star_oam_migrator' THEN
        RAISE EXCEPTION '0114 capture requires direct edge ingress' USING ERRCODE='42501';
    END IF;
    p:=NEW.payload_jsonb;
    SELECT * INTO STRICT snapshot FROM public.external_sync_snapshots WHERE id=NEW.snapshot_ref_id FOR UPDATE;
    began:=(p->>'capture_started_at')::timestamptz;
    ended:=(p->>'capture_completed_at')::timestamptz;
    IF snapshot.status<>'complete' OR snapshot.completed_at IS NULL OR snapshot.completed_at>NEW.created_at
       OR NEW.entity_type<>'inventory' OR NEW.source_instance<>snapshot.source_instance
       OR NEW.request_id<>snapshot.snapshot_id||'-capture'
       OR NEW.created_at<transaction_timestamp() OR NEW.created_at>clock_timestamp()
       OR NEW.signed_at IS NULL OR abs(extract(epoch FROM NEW.created_at-NEW.signed_at))>300
       OR began IS NULL OR ended IS NULL OR began>ended OR ended>snapshot.snapshot_at
       OR snapshot.snapshot_at>NEW.created_at OR NEW.created_at-began>interval '45 minutes'
       OR jsonb_typeof(p)<>'object' OR (SELECT count(*) FROM jsonb_object_keys(p))<>18
       OR EXISTS (SELECT 1 FROM jsonb_each(p) item WHERE jsonb_typeof(item.value)<>'string')
       OR p->>'schema_version' IS DISTINCT FROM 'rsc.inventory_control_attestation.v1'
       OR p->>'collector_contract' IS DISTINCT FROM 'rsc.inventory_control_capture.v1'
       OR p->>'key_id' IS DISTINCT FROM NEW.key_id
       OR p->>'source_instance' IS DISTINCT FROM snapshot.source_instance
       OR p->>'source_system' IS DISTINCT FROM snapshot.source_system
       OR p->>'company_id' IS DISTINCT FROM snapshot.company_id OR p->>'org_code' IS DISTINCT FROM snapshot.org_code
       OR p->>'scope_key' IS DISTINCT FROM snapshot.scope_key OR p->>'snapshot_id' IS DISTINCT FROM snapshot.snapshot_id
       OR p->>'sync_mode' IS DISTINCT FROM snapshot.sync_mode OR (p->>'snapshot_at')::timestamptz IS DISTINCT FROM snapshot.snapshot_at
       OR COALESCE(p->>'catalog_revision','') !~ '^[A-Za-z0-9._:-]{1,160}$'
       OR COALESCE(p->>'target_region_code','') !~ '^[A-Za-z0-9._:-]{1,160}$'
       OR COALESCE(p->>'source_binding_sha256','') !~ '^[a-f0-9]{64}$'
       OR p->>'source_binding_sha256' IS DISTINCT FROM encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(
           jsonb_build_object('source_system',snapshot.source_system,'source_instance',snapshot.source_instance,
               'company_id',snapshot.company_id,'org_code',snapshot.org_code,'scope_key',snapshot.scope_key)),'UTF8')),'hex')
       OR COALESCE(p->>'catalog_sha256','') !~ '^[a-f0-9]{64}$'
       OR COALESCE(p->>'capture_chain_sha256','') !~ '^[a-f0-9]{64}$'
       OR NEW.key_id !~ '^[A-Za-z0-9._:-]{1,128}$' OR NEW.key_fingerprint !~ '^[a-f0-9]{64}$'
       OR NEW.payload_sha256 IS DISTINCT FROM encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(p),'UTF8')),'hex')
       OR NEW.body_sha256 IS DISTINCT FROM NEW.payload_sha256
       OR jsonb_array_length(snapshot.manifest_json::jsonb->'entities')<>1
       OR snapshot.manifest_json::jsonb->'entities'->0->>'entity_type' IS DISTINCT FROM 'inventory'
       OR NOT public.rsc_oam_binding_allowed_0044(session_user::text,'edge_ingress',snapshot.source_system,
           snapshot.source_instance,snapshot.scope_key,snapshot.company_id,snapshot.org_code,'inventory')
    THEN RAISE EXCEPTION '0114 exact authenticated inventory capture required' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END;
"""
FUNCTION_HASH = hashlib.sha256(BODY.encode()).hexdigest()
TRIGGERS = {
    'trg_control_capture_attestation_facts_0114': ('INSERT OR UPDATE OR DELETE',31),
    'trg_control_capture_attestation_truncate_0114': ('TRUNCATE',34),
}


def _create():
    op.create_table(TABLE,
        sa.Column('id',sa.Uuid(),primary_key=True),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('snapshot_ref_id',sa.String(36),sa.ForeignKey('external_sync_snapshots.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('source_instance',sa.String(128),nullable=False),sa.Column('entity_type',sa.String(32),nullable=False),
        sa.Column('request_id',sa.String(128),nullable=False),sa.Column('key_id',sa.String(128),nullable=False),
        sa.Column('key_fingerprint',sa.String(64),nullable=False),sa.Column('signed_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('payload_jsonb',sa.JSON().with_variant(JSONB(),'postgresql'),nullable=False),
        sa.Column('payload_sha256',sa.String(64),nullable=False),sa.Column('body_sha256',sa.String(64),nullable=False),
        sa.UniqueConstraint('snapshot_ref_id',name='uq_control_attestation_snapshot'),
        sa.UniqueConstraint('source_instance','request_id',name='uq_control_attestation_request'),
        sa.CheckConstraint('length(key_fingerprint)=64 AND length(payload_sha256)=64 AND length(body_sha256)=64',name='ck_control_attestation_hashes'),
        sa.CheckConstraint('length(key_id) BETWEEN 1 AND 128 AND length(source_instance) BETWEEN 1 AND 128 AND length(request_id) BETWEEN 1 AND 128',name='ck_control_attestation_identity'),
        sa.CheckConstraint("entity_type='inventory'",name='ck_control_attestation_entity'))


def _transition(upgrade):
    dialect=op.get_bind().dialect.name
    if dialect not in {'sqlite','postgresql'}: raise RuntimeError('0114 supports PostgreSQL and SQLite only')
    folder=Path(__file__).parent;helper=runpy.run_path(str(folder/'20260927_0087_inbound_fulfillment_boundary.py'))
    helper['_begin_sqlite']()
    if dialect=='postgresql':op.execute('LOCK TABLE public.alembic_version IN ACCESS EXCLUSIVE MODE')
    if upgrade:_create()
    else:
        if dialect=='postgresql':op.execute(f'LOCK TABLE public.{TABLE} IN ACCESS EXCLUSIVE MODE')
        helper['_preflight'](f'EXISTS (SELECT 1 FROM {TABLE})','0114 downgrade blocked: capture receipts must be retained')
    if dialect=='postgresql':
        if upgrade:
            op.execute(f'CREATE FUNCTION public.{FUNCTION_NAME}() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $body${BODY}$body$')
            op.execute(f"""DO $body$ DECLARE entry record; BEGIN
                FOR entry IN SELECT DISTINCT a.grantee FROM pg_proc p,
                    LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
                    WHERE p.oid='public.{FUNCTION_NAME}()'::regprocedure AND a.grantee<>p.proowner
                LOOP EXECUTE format('REVOKE ALL ON FUNCTION public.{FUNCTION_NAME}() FROM %s',
                    CASE WHEN entry.grantee=0 THEN 'PUBLIC' ELSE quote_ident(pg_get_userbyid(entry.grantee)) END); END LOOP;
                FOR entry IN SELECT DISTINCT a.grantee FROM pg_class c,
                    LATERAL aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) a
                    WHERE c.oid='public.{TABLE}'::regclass AND a.grantee<>c.relowner
                LOOP EXECUTE format('REVOKE ALL ON TABLE public.{TABLE} FROM %s',
                    CASE WHEN entry.grantee=0 THEN 'PUBLIC' ELSE quote_ident(pg_get_userbyid(entry.grantee)) END); END LOOP;
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='edge_inbox') THEN GRANT SELECT,INSERT ON TABLE public.{TABLE} TO edge_inbox; END IF;
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='star_oam_backup') THEN GRANT SELECT ON TABLE public.{TABLE} TO star_oam_backup; END IF;
            END $body$""")
            for name,(events,_) in TRIGGERS.items():
                op.execute(f"CREATE TRIGGER {name} BEFORE {events} ON public.{TABLE} FOR EACH {'STATEMENT' if events=='TRUNCATE' else 'ROW'} EXECUTE FUNCTION public.{FUNCTION_NAME}()")
                op.execute(f'ALTER TABLE public.{TABLE} ENABLE ALWAYS TRIGGER {name}')
            op.execute(f'ALTER TABLE public.{TABLE} ENABLE ROW LEVEL SECURITY')
            op.execute(f'ALTER TABLE public.{TABLE} FORCE ROW LEVEL SECURITY')
            op.execute(f'CREATE POLICY {TABLE}_migrator_0114 ON public.{TABLE} TO star_oam_migrator USING (true) WITH CHECK (true)')
            op.execute(f'CREATE POLICY {TABLE}_backup_0114 ON public.{TABLE} FOR SELECT TO star_oam_backup USING (true)')
            op.execute(f'CREATE POLICY {TABLE}_edge_select_0114 ON public.{TABLE} FOR SELECT TO edge_inbox USING ({POLICY})')
            op.execute(f'CREATE POLICY {TABLE}_edge_insert_0114 ON public.{TABLE} FOR INSERT TO edge_inbox WITH CHECK ({POLICY})')
        else:
            op.execute(f"""DO $body$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_proc p
                WHERE p.oid='public.{FUNCTION_NAME}()'::regprocedure AND p.prosecdef
                  AND p.proowner=(SELECT oid FROM pg_roles WHERE rolname=current_user)
                  AND encode(sha256(convert_to(p.prosrc,'UTF8')),'hex')='{FUNCTION_HASH}'
                  AND p.proconfig=ARRAY['search_path=pg_catalog']
                  AND NOT EXISTS (SELECT 1 FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a WHERE a.grantee<>p.proowner))
                THEN RAISE EXCEPTION '0114 guard ownership, source or ACL drift'; END IF; END $body$""")
            for name in TRIGGERS:op.execute(f'DROP TRIGGER {name} ON public.{TABLE}')
            op.execute(f'DROP FUNCTION public.{FUNCTION_NAME}()')
        replace=runpy.run_path(str(folder/'20260912_0072_outbound_postings.py'))['_previous']()['_previous']()['_previous']()['_replace_function_source']
        replace(signature='public.rsc_oam_runtime_binding_ready_0044()',expected_hash=OLD_HASH if upgrade else NEW_HASH,
            replacement_hash=NEW_HASH if upgrade else OLD_HASH,replacements=((down_revision,revision),) if upgrade else ((revision,down_revision),),label='capture_attestation_readiness_0114')
    elif upgrade:
        for event in ('UPDATE','DELETE'):
            op.execute(f"CREATE TRIGGER trg_control_attestation_{event.lower()}_0114 BEFORE {event} ON {TABLE} BEGIN SELECT RAISE(ABORT,'0114 capture receipts are append-only'); END")
    if not upgrade:op.drop_table(TABLE)


def upgrade():_transition(True)
def downgrade():_transition(False)
