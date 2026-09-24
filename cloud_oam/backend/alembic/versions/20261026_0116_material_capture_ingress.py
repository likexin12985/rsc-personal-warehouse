"""Dedicated material capture transport registration, immutable receipt and RLS."""
import hashlib
from pathlib import Path
import runpy

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '20261026_0116'
down_revision = '20261025_0115'
branch_labels = depends_on = None
OLD_HASH = '7dad591c17ca90b278337f7b012bd7e9d826e5fc7753dcb41b05e7baf518de97'
NEW_HASH = 'c3b42e2f0ae11dbb94829eae68fd0f21c36c9e8a27118be9042d885588d3db4e'
BINDINGS = 'oam_material_capture_bindings'
RECEIPTS = 'oam_material_capture_receipts'
POLICY = 'rsc_oam_material_capture_visible_0116((source_instance)::text)'

BINDING_BODY = """
DECLARE source public.source_systems%ROWTYPE;
BEGIN
    IF TG_OP IN ('DELETE','TRUNCATE') THEN
        RAISE EXCEPTION '0116 transport registration must be retained' USING ERRCODE='23514';
    END IF;
    IF session_user<>'star_oam_migrator' OR current_user<>'star_oam_migrator' THEN
        RAISE EXCEPTION '0116 direct migration identity required' USING ERRCODE='42501';
    END IF;
    IF TG_OP='UPDATE' THEN
        IF (to_jsonb(NEW)-'revoked_at') IS DISTINCT FROM (to_jsonb(OLD)-'revoked_at')
           OR OLD.revoked_at IS NOT NULL OR NEW.revoked_at IS NULL
           OR NEW.revoked_at<transaction_timestamp() OR NEW.revoked_at>clock_timestamp()
        THEN RAISE EXCEPTION '0116 only one explicit revocation may be appended' USING ERRCODE='23514'; END IF;
        RETURN NEW;
    END IF;
    SELECT * INTO source FROM public.source_systems WHERE id=NEW.source_system_id FOR SHARE;
    IF NOT FOUND OR source.code<>'oam' OR source.mode<>'read_only' OR NOT source.enabled
       OR NEW.source_instance !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
       OR NEW.key_id !~ '^[A-Za-z0-9._:-]{1,128}$' OR NEW.key_fingerprint !~ '^[a-f0-9]{64}$'
       OR NEW.created_at<transaction_timestamp() OR NEW.created_at>clock_timestamp()
       OR NEW.valid_from<NEW.created_at OR NEW.valid_to<=NEW.valid_from
       OR NOT isfinite(NEW.valid_from) OR NOT isfinite(NEW.valid_to) OR NEW.revoked_at IS NOT NULL
    THEN RAISE EXCEPTION '0116 exact read-only source registration required' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END;
"""

VISIBLE_BODY = """
BEGIN
    IF session_user<>'edge_inbox' OR current_user<>'star_oam_migrator' THEN RETURN false; END IF;
    RETURN EXISTS (SELECT 1 FROM public.oam_material_capture_bindings b
        JOIN public.source_systems s ON s.id=b.source_system_id
        WHERE b.source_instance=p_source AND b.revoked_at IS NULL
          AND b.valid_from<=clock_timestamp() AND clock_timestamp()<b.valid_to
          AND s.code='oam' AND s.mode='read_only' AND s.enabled);
END;
"""

LOCK_BODY = """
DECLARE binding public.oam_material_capture_bindings%ROWTYPE; source public.source_systems%ROWTYPE;
BEGIN
    IF session_user<>'edge_inbox' OR current_user<>'star_oam_migrator' THEN
        RAISE EXCEPTION '0116 direct edge identity required' USING ERRCODE='42501'; END IF;
    SELECT * INTO binding FROM public.oam_material_capture_bindings
        WHERE source_instance=p_source AND key_id=p_key AND key_fingerprint=p_fingerprint FOR SHARE;
    IF NOT FOUND THEN RAISE EXCEPTION '0116 material capture binding unavailable' USING ERRCODE='23514'; END IF;
    SELECT * INTO source FROM public.source_systems WHERE id=binding.source_system_id FOR SHARE;
    IF NOT FOUND OR source.code<>'oam' OR source.mode<>'read_only' OR NOT source.enabled
       OR binding.revoked_at IS NOT NULL OR binding.valid_from>clock_timestamp() OR binding.valid_to<=clock_timestamp()
    THEN RAISE EXCEPTION '0116 material capture binding unavailable' USING ERRCODE='23514'; END IF;
    RETURN jsonb_build_object('id',binding.id,'valid_from',binding.valid_from,'valid_to',binding.valid_to);
END;
"""

RECEIPT_BODY = """
DECLARE binding public.oam_material_capture_bindings%ROWTYPE; p jsonb; c jsonb; report jsonb;
BEGIN
    IF TG_OP<>'INSERT' THEN
        RAISE EXCEPTION '0116 material capture receipts are append-only' USING ERRCODE='23514'; END IF;
    IF session_user<>'edge_inbox' OR current_user<>'star_oam_migrator' THEN
        RAISE EXCEPTION '0116 direct edge identity required' USING ERRCODE='42501'; END IF;
    report:=public.rsc_oam_material_capture_binding_0116(NEW.source_instance,NEW.key_id,NEW.key_fingerprint);
    SELECT * INTO binding FROM public.oam_material_capture_bindings WHERE id=NEW.binding_id FOR SHARE;
    IF NOT FOUND OR binding.id::text IS DISTINCT FROM report->>'id' THEN
        RAISE EXCEPTION '0116 exact transport binding required' USING ERRCODE='23514'; END IF;
    p:=NEW.payload_jsonb; c:=p->'capture';
    IF jsonb_typeof(p) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(p))<>4
       OR p->>'schema_version' IS DISTINCT FROM 'rsc.oam_material_capture_request.v1'
       OR p->>'operation' IS DISTINCT FROM 'receive' OR p->>'key_id' IS DISTINCT FROM NEW.key_id
       OR jsonb_typeof(c) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(c))<>12
       OR c->>'schema_version' IS DISTINCT FROM 'rsc.oam_material_master_capture.v1'
       OR c->>'field_contract' IS DISTINCT FROM 'rsc.oam_spare_master_fields.v1'
       OR c->'binding' IS DISTINCT FROM jsonb_build_object('source_system','starcharge_oam','source_instance',NEW.source_instance,
           'scope_key','material:spares-visible','endpoint','/material_type/spare_list')
       OR c->>'capture_id' IS DISTINCT FROM NEW.capture_id::text
       OR NEW.request_id IS DISTINCT FROM 'material-'||NEW.capture_id::text||'-receive'
       OR (c->>'started_at')::timestamptz IS DISTINCT FROM NEW.capture_started_at
       OR (c->>'completed_at')::timestamptz IS DISTINCT FROM NEW.capture_completed_at
       OR c->'observed_count' IS DISTINCT FROM to_jsonb(NEW.observed_count)
       OR c->'empty_observation' IS DISTINCT FROM to_jsonb(NEW.observed_count=0)
       OR jsonb_typeof(c->'records') IS DISTINCT FROM 'array' OR jsonb_array_length(c->'records')<>NEW.observed_count
       OR jsonb_typeof(c->'scans') IS DISTINCT FROM 'array' OR jsonb_array_length(c->'scans')<>2
       OR c->>'records_sha256' IS DISTINCT FROM NEW.records_sha256
       OR NEW.records_sha256 IS DISTINCT FROM encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(c->'records'),'UTF8')),'hex')
       OR NEW.capture_sha256 IS DISTINCT FROM encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(c),'UTF8')),'hex')
       OR NEW.body_sha256 IS DISTINCT FROM encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(p),'UTF8')),'hex')
       OR octet_length(public.rsc_canonical_reconciliation_json_0026(p))>16778240
       OR NEW.created_at<transaction_timestamp() OR NEW.created_at>clock_timestamp()
       OR abs(extract(epoch FROM NEW.created_at-NEW.signed_at))>300
       OR NEW.capture_started_at<binding.valid_from OR NEW.created_at>=binding.valid_to
       OR NEW.capture_started_at>NEW.capture_completed_at OR NEW.capture_completed_at>NEW.created_at
       OR NEW.created_at-NEW.capture_started_at>interval '45 minutes'
    THEN RAISE EXCEPTION '0116 exact immutable material capture required' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END;
"""

FUNCTIONS = {
    'rsc_oam_material_binding_guard_0116()': ('', 'trigger', BINDING_BODY, True),
    'rsc_oam_material_capture_visible_0116(text)': ('p_source text', 'boolean', VISIBLE_BODY, False),
    'rsc_oam_material_capture_binding_0116(text,text,text)': ('p_source text,p_key text,p_fingerprint text', 'jsonb', LOCK_BODY, False),
    'rsc_oam_material_receipt_guard_0116()': ('', 'trigger', RECEIPT_BODY, True),
}
TRIGGERS = (
    (BINDINGS,'trg_material_binding_facts_0116','rsc_oam_material_binding_guard_0116()', 'INSERT OR UPDATE OR DELETE',31),
    (BINDINGS,'trg_material_binding_truncate_0116','rsc_oam_material_binding_guard_0116()', 'TRUNCATE',34),
    (RECEIPTS,'trg_material_receipt_facts_0116','rsc_oam_material_receipt_guard_0116()', 'INSERT OR UPDATE OR DELETE',31),
    (RECEIPTS,'trg_material_receipt_truncate_0116','rsc_oam_material_receipt_guard_0116()', 'TRUNCATE',34),
)


def _create():
    op.create_table(BINDINGS,
        sa.Column('id',sa.Uuid(),primary_key=True),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('source_system_id',sa.Uuid(),sa.ForeignKey('source_systems.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('source_instance',sa.String(128),nullable=False),sa.Column('key_id',sa.String(128),nullable=False),
        sa.Column('key_fingerprint',sa.String(64),nullable=False),sa.Column('valid_from',sa.DateTime(timezone=True),nullable=False),
        sa.Column('valid_to',sa.DateTime(timezone=True),nullable=False),sa.Column('revoked_at',sa.DateTime(timezone=True)),
        sa.UniqueConstraint('source_instance','key_id',name='uq_material_capture_source_key'),
        sa.CheckConstraint('valid_to > valid_from AND valid_from >= created_at',name='ck_material_capture_binding_window'),
        sa.CheckConstraint('revoked_at IS NULL OR revoked_at >= created_at',name='ck_material_capture_binding_revocation'),
        sa.CheckConstraint('length(key_fingerprint)=64',name='ck_material_capture_binding_hash'))
    op.create_table(RECEIPTS,
        sa.Column('id',sa.Uuid(),primary_key=True),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('binding_id',sa.Uuid(),sa.ForeignKey(BINDINGS+'.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('source_instance',sa.String(128),nullable=False),sa.Column('capture_id',sa.Uuid(),nullable=False),
        sa.Column('request_id',sa.String(128),nullable=False),sa.Column('key_id',sa.String(128),nullable=False),
        sa.Column('key_fingerprint',sa.String(64),nullable=False),sa.Column('signed_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('capture_started_at',sa.DateTime(timezone=True),nullable=False),sa.Column('capture_completed_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('observed_count',sa.Integer(),nullable=False),sa.Column('records_sha256',sa.String(64),nullable=False),
        sa.Column('capture_sha256',sa.String(64),nullable=False),sa.Column('body_sha256',sa.String(64),nullable=False),
        sa.Column('payload_jsonb',sa.JSON().with_variant(JSONB(),'postgresql'),nullable=False),
        sa.UniqueConstraint('source_instance','capture_id',name='uq_material_capture_receipt'),
        sa.UniqueConstraint('source_instance','request_id',name='uq_material_capture_request'),
        sa.CheckConstraint('observed_count BETWEEN 0 AND 100000',name='ck_material_capture_count'),
        sa.CheckConstraint('capture_started_at <= capture_completed_at AND capture_completed_at <= created_at',name='ck_material_capture_interval'),
        sa.CheckConstraint('length(key_fingerprint)=64 AND length(capture_sha256)=64 AND length(body_sha256)=64 AND length(records_sha256)=64',name='ck_material_capture_receipt_hashes'))


def _revoke(kind, name, catalog, reference):
    op.execute(f"""DO $body$ DECLARE entry record; BEGIN
        FOR entry IN SELECT DISTINCT a.grantee FROM {catalog} c,
            LATERAL aclexplode(COALESCE(c.{'proacl' if kind=='FUNCTION' else 'relacl'},acldefault(
                '{'f' if kind=='FUNCTION' else 'r'}',c.{'proowner' if kind=='FUNCTION' else 'relowner'}))) a
            WHERE c.oid='{reference}'::{'regprocedure' if kind=='FUNCTION' else 'regclass'}
              AND a.grantee<>c.{'proowner' if kind=='FUNCTION' else 'relowner'}
        LOOP EXECUTE format('REVOKE ALL ON {kind} {name} FROM %s',
            CASE WHEN entry.grantee=0 THEN 'PUBLIC' ELSE quote_ident(pg_get_userbyid(entry.grantee)) END); END LOOP;
    END $body$""")


def _transition(upgrade):
    dialect = op.get_bind().dialect.name
    if dialect not in {'sqlite','postgresql'}: raise RuntimeError('0116 supports PostgreSQL and SQLite only')
    folder = Path(__file__).parent
    helper = runpy.run_path(str(folder/'20260927_0087_inbound_fulfillment_boundary.py'))
    helper['_begin_sqlite']()
    if dialect == 'postgresql': op.execute('LOCK TABLE public.alembic_version IN ACCESS EXCLUSIVE MODE')
    if upgrade: _create()
    else:
        if dialect == 'postgresql': op.execute(f'LOCK TABLE public.{BINDINGS},public.{RECEIPTS} IN ACCESS EXCLUSIVE MODE')
        helper['_preflight'](f'EXISTS (SELECT 1 FROM {BINDINGS}) OR EXISTS (SELECT 1 FROM {RECEIPTS})',
                             '0116 downgrade blocked: material transport evidence must be retained')
    if dialect == 'postgresql':
        if upgrade:
            for signature,(args,result,body,private) in FUNCTIONS.items():
                name = signature.split('(')[0]
                op.execute(f'CREATE FUNCTION public.{name}({args}) RETURNS {result} LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $body${body}$body$')
                _revoke('FUNCTION','public.'+signature,'pg_proc','public.'+signature)
                if not private: op.execute(f'GRANT EXECUTE ON FUNCTION public.{signature} TO edge_inbox')
            for table in (BINDINGS,RECEIPTS):
                _revoke('TABLE','public.'+table,'pg_class','public.'+table)
                op.execute(f'GRANT SELECT ON TABLE public.{table} TO star_oam_backup')
                if table == RECEIPTS: op.execute(f'GRANT SELECT,INSERT ON TABLE public.{table} TO edge_inbox')
                op.execute(f'ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY')
                op.execute(f'ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY')
                op.execute(f'CREATE POLICY {table}_owner_0116 ON public.{table} TO star_oam_migrator USING (true) WITH CHECK (true)')
                op.execute(f'CREATE POLICY {table}_backup_0116 ON public.{table} FOR SELECT TO star_oam_backup USING (true)')
            op.execute(f'CREATE POLICY {RECEIPTS}_edge_select_0116 ON public.{RECEIPTS} FOR SELECT TO edge_inbox USING ({POLICY})')
            op.execute(f'CREATE POLICY {RECEIPTS}_edge_insert_0116 ON public.{RECEIPTS} FOR INSERT TO edge_inbox WITH CHECK ({POLICY})')
            for table,name,function,events,_ in TRIGGERS:
                op.execute(f"CREATE TRIGGER {name} BEFORE {events} ON public.{table} FOR EACH {'STATEMENT' if events=='TRUNCATE' else 'ROW'} EXECUTE FUNCTION public.{function}")
                op.execute(f'ALTER TABLE public.{table} ENABLE ALWAYS TRIGGER {name}')
        else:
            for table,name,_,_,_ in TRIGGERS: op.execute(f'DROP TRIGGER {name} ON public.{table}')
            op.drop_table(RECEIPTS)
            op.drop_table(BINDINGS)
            for signature in reversed(FUNCTIONS): op.execute(f'DROP FUNCTION public.{signature}')
        replace = runpy.run_path(str(folder/'20260912_0072_outbound_postings.py'))['_previous']()['_previous']()['_previous']()['_replace_function_source']
        replace(signature='public.rsc_oam_runtime_binding_ready_0044()',expected_hash=OLD_HASH if upgrade else NEW_HASH,
            replacement_hash=NEW_HASH if upgrade else OLD_HASH,replacements=((down_revision,revision),) if upgrade else ((revision,down_revision),),label='material_capture_readiness_0116')
    elif upgrade:
        for event in ('UPDATE','DELETE'):
            op.execute(f"CREATE TRIGGER trg_material_receipt_{event.lower()}_0116 BEFORE {event} ON {RECEIPTS} BEGIN SELECT RAISE(ABORT,'0116 material capture receipts are append-only'); END")
        op.execute(f"CREATE TRIGGER trg_material_binding_delete_0116 BEFORE DELETE ON {BINDINGS} BEGIN SELECT RAISE(ABORT,'0116 transport registration must be retained'); END")
        columns=('id','created_at','source_system_id','source_instance','key_id','key_fingerprint','valid_from','valid_to')
        changed=' OR '.join('NEW.'+field+' IS NOT OLD.'+field for field in columns)
        op.execute(f"CREATE TRIGGER trg_material_binding_update_0116 BEFORE UPDATE ON {BINDINGS} WHEN {changed} OR OLD.revoked_at IS NOT NULL OR NEW.revoked_at IS NULL BEGIN SELECT RAISE(ABORT,'0116 only one explicit revocation may be appended'); END")
    else:
        op.drop_table(RECEIPTS)
        op.drop_table(BINDINGS)


def upgrade(): _transition(True)
def downgrade(): _transition(False)
