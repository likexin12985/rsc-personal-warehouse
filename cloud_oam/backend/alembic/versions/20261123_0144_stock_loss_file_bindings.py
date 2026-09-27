"""Immutable typed loss evidence bindings, before loss posting activation.

The 0142 closed-admission guard remains installed. These foreign keys and file
proofs are prerequisites, not authority to create a loss or freeze inventory.
"""
from pathlib import Path
import hashlib
import runpy

from alembic import op
import sqlalchemy as sa

revision='20261123_0144'
down_revision='20261122_0143'
branch_labels=depends_on=None
FOLDER=Path(__file__).parent
previous=runpy.run_path(str(FOLDER/'20261122_0143_stock_loss_evidence_purpose.py'))
OLD_READY_HASH=previous['NEW_READY_HASH']
ready=previous['ready']
NEW_READY_HASH=hashlib.sha256(ready['_ready'].replace(ready['_ready_parent'],revision).encode()).hexdigest()
TABLE='stock_loss_files'
FUNCTION='rsc_guard_stock_loss_file_binding_0144'
BODY="""
DECLARE parent public.stock_operation_orders%ROWTYPE;
    evidence public.files%ROWTYPE;
    expected jsonb;
BEGIN
    PERFORM 1 FROM public.inventory_ledger_heads WHERE stream_key='inventory' FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION '0144 loss binding ledger missing' USING ERRCODE='23514'; END IF;
    SELECT * INTO parent FROM public.stock_operation_orders WHERE id=NEW.operation_id;
    SELECT * INTO evidence FROM public.files WHERE id=NEW.file_id FOR SHARE;
    IF parent.id IS NULL OR parent.operation_type<>'loss_report' OR parent.status<>'submitted'
       OR NEW.operation_type<>'loss_report' OR evidence.id IS NULL OR evidence.status<>'available'
       OR evidence.uploaded_by IS DISTINCT FROM parent.actor_user_id
       OR evidence.metadata_jsonb->>'purpose' IS DISTINCT FROM 'stock_loss_evidence'
       OR evidence.metadata_jsonb->>'provider' IS DISTINCT FROM 'aliyun_oss_v2'
       OR evidence.metadata_jsonb->>'uploader_person_id' IS DISTINCT FROM parent.requester_id::text
       OR evidence.metadata_jsonb->'authorization_version' IS DISTINCT FROM to_jsonb(parent.authorization_version)
       OR evidence.metadata_jsonb->>'uploader_user_id' IS DISTINCT FROM parent.actor_user_id
       OR evidence.metadata_jsonb->>'request_sha256' IS DISTINCT FROM encode(sha256(convert_to(
            public.rsc_canonical_reconciliation_json_0026(jsonb_build_object(
                'mime_type',evidence.mime_type,'original_filename',evidence.original_filename,
                'purpose','stock_loss_evidence','sha256',evidence.sha256,'size_bytes',evidence.size_bytes)),
            'UTF8')),'hex')
       OR evidence.metadata_jsonb->>'file_id' IS DISTINCT FROM evidence.id::text
       OR evidence.metadata_jsonb->>'storage_key' IS DISTINCT FROM evidence.storage_key
       OR jsonb_typeof(evidence.metadata_jsonb->'completion') IS DISTINCT FROM 'object'
       OR evidence.metadata_jsonb->'completion'->>'verified_at' IS NULL
       OR (evidence.metadata_jsonb->'completion'->>'verified_at')::timestamptz < evidence.created_at
       OR (evidence.metadata_jsonb->'completion'->>'verified_at')::timestamptz > parent.created_at
       OR NEW.created_at IS DISTINCT FROM parent.created_at
       OR NEW.metadata_sha256 !~ '^[0-9a-f]{64}$'
       OR NEW.metadata_sha256 <> encode(sha256(convert_to(
            public.rsc_canonical_reconciliation_json_0026(evidence.metadata_jsonb),'UTF8')),'hex') THEN
        RAISE EXCEPTION '0144 exact completed loss evidence binding required' USING ERRCODE='23514';
    END IF;
    expected:=jsonb_build_object('file_id',evidence.id::text,'original_filename',evidence.original_filename,
        'sha256',evidence.sha256,'size_bytes',evidence.size_bytes,'mime_type',evidence.mime_type,
        'metadata_sha256',NEW.metadata_sha256);
    IF jsonb_typeof(parent.command_jsonb->'evidence_file_ids') IS DISTINCT FROM 'array'
       OR jsonb_typeof(parent.plan_jsonb->'evidence') IS DISTINCT FROM 'array'
       OR (SELECT count(*) FROM jsonb_array_elements(parent.command_jsonb->'evidence_file_ids') value
            WHERE value=to_jsonb(evidence.id::text))<>1
       OR (SELECT count(*) FROM jsonb_array_elements(parent.plan_jsonb->'evidence') value WHERE value=expected)<>1
       OR jsonb_array_length(parent.command_jsonb->'evidence_file_ids') NOT BETWEEN 1 AND 20
       OR jsonb_array_length(parent.plan_jsonb->'evidence')<>jsonb_array_length(parent.command_jsonb->'evidence_file_ids') THEN
        RAISE EXCEPTION '0144 loss intent and file manifest mismatch' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END;
"""
BODY_HASH=hashlib.sha256(BODY.encode()).hexdigest()
TRIGGERS={
    'trg_stock_loss_files_binding_0144':(TABLE,'INSERT',FUNCTION,5,True),
    'trg_stock_loss_files_immutable_0144':(TABLE,'UPDATE OR DELETE','rsc_guard_work_order_facts_0090',27,False),
    'trg_stock_loss_files_truncate_0144':(TABLE,'TRUNCATE','rsc_guard_work_order_facts_0090',34,False),
}


def _schema():
    op.create_table(TABLE,
        sa.Column('id',sa.Uuid(),primary_key=True,nullable=False),
        sa.Column('operation_id',sa.Uuid(),nullable=False),
        sa.Column('operation_type',sa.String(24),nullable=False),
        sa.Column('file_id',sa.Uuid(),nullable=False),
        sa.Column('metadata_sha256',sa.String(64),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
        sa.ForeignKeyConstraint(['operation_id','operation_type'],
            ['stock_operation_orders.id','stock_operation_orders.operation_type'],
            name='fk_stock_loss_files_typed_parent',ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['file_id'],['files.id'],ondelete='RESTRICT'),
        sa.UniqueConstraint('file_id',name='uq_stock_loss_files_file'),
        sa.CheckConstraint("operation_type = 'loss_report'",name='ck_stock_loss_files_type'),
        sa.CheckConstraint('length(metadata_sha256) = 64',name='ck_stock_loss_files_digest'))
    op.create_index('ix_stock_loss_files_operation_id',TABLE,['operation_id'])


def _transition(up):
    db=op.get_bind();dialect=db.dialect.name
    if dialect not in ('postgresql','sqlite'):raise RuntimeError('0144 PostgreSQL or SQLite required')
    helper=runpy.run_path(str(FOLDER/'20260927_0087_inbound_fulfillment_boundary.py'))
    helper['_begin_sqlite']()
    if dialect=='postgresql':
        op.execute("DO $$ BEGIN IF current_user<>'star_oam_migrator' OR session_user<>'star_oam_migrator' THEN RAISE EXCEPTION '0144 direct schema owner required'; END IF; END $$")
        op.execute('LOCK TABLE public.alembic_version IN ACCESS EXCLUSIVE MODE')
        op.execute('LOCK TABLE public.inventory_ledger_heads,public.stock_operation_orders,public.files IN SHARE ROW EXCLUSIVE MODE')
        if not up:op.execute('LOCK TABLE public.stock_loss_files IN ACCESS EXCLUSIVE MODE')
    if not up:
        helper['_preflight']('EXISTS(SELECT 1 FROM stock_loss_files)',
            '0144 immutable loss attachment history requires retention')
    if up:_schema()
    if dialect=='postgresql':
        if up:
            op.execute(f'CREATE FUNCTION public.{FUNCTION}() RETURNS trigger LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,public AS $body${BODY}$body$')
            op.execute(f'REVOKE ALL ON FUNCTION public.{FUNCTION}() FROM PUBLIC,star_oam_api')
            for name,(table,events,function,_,deferred) in TRIGGERS.items():
                statement=(f'CREATE CONSTRAINT TRIGGER {name} AFTER {events} ON public.{table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW'
                    if deferred else f"CREATE TRIGGER {name} BEFORE {events} ON public.{table} FOR EACH {'STATEMENT' if events=='TRUNCATE' else 'ROW'}")
                op.execute(statement+f' EXECUTE FUNCTION public.{function}()')
                op.execute(f'ALTER TABLE public.{table} ENABLE ALWAYS TRIGGER {name}')
            op.execute(f'GRANT SELECT,INSERT ON public.{TABLE} TO star_oam_api')
        else:
            op.execute(f"""DO $$ BEGIN IF NOT EXISTS(SELECT 1 FROM pg_proc p WHERE p.oid='public.{FUNCTION}()'::regprocedure
                AND p.proowner=(SELECT oid FROM pg_roles WHERE rolname=current_user) AND p.prosecdef
                AND p.provolatile='v' AND p.prokind='f' AND NOT p.proleakproof
                AND p.prorettype='trigger'::regtype AND p.pronargs=0
                AND p.prolang=(SELECT oid FROM pg_language WHERE lanname='plpgsql')
                AND p.proconfig=ARRAY['search_path=pg_catalog, public']
                AND encode(sha256(convert_to(p.prosrc,'UTF8')),'hex')='{BODY_HASH}'
                AND NOT EXISTS(SELECT 1 FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a WHERE a.grantee<>p.proowner))
                THEN RAISE EXCEPTION '0144 binding function source ownership or ACL drift'; END IF; END $$""")
        replace=runpy.run_path(str(FOLDER/'20260909_0069_stock_reservations.py'))['_replace_function_source']
        replace(signature='public.rsc_oam_runtime_binding_ready_0044()',expected_hash=OLD_READY_HASH if up else NEW_READY_HASH,
            replacement_hash=NEW_READY_HASH if up else OLD_READY_HASH,
            replacements=((down_revision,revision),) if up else ((revision,down_revision),),label='stock_loss_file_bindings_readiness_0144')
        if not up:
            for name,(table,*_) in TRIGGERS.items():op.execute(f'DROP TRIGGER {name} ON public.{table}')
            op.execute(f'DROP FUNCTION public.{FUNCTION}()')
    elif up:
        # SQLite cannot provide the PG deferred whole-command proof. Keep the
        # new binding path closed there as well as the existing 0142 order.
        op.execute("CREATE TRIGGER trg_stock_loss_files_insert_0144 BEFORE INSERT ON stock_loss_files BEGIN SELECT RAISE(ABORT,'0144 PostgreSQL loss proof required'); END")
        for event in ('UPDATE','DELETE'):
            op.execute(f"CREATE TRIGGER trg_stock_loss_files_{event.lower()}_0144 BEFORE {event} ON stock_loss_files BEGIN SELECT RAISE(ABORT,'0144 immutable loss attachments'); END")
    if not up:op.drop_table(TABLE)


def upgrade():_transition(True)
def downgrade():_transition(False)
