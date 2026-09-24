"""Headquarters material source authority, immutable decisions and current scope."""
import hashlib
from datetime import datetime, timezone
from pathlib import Path
import runpy
import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision='20261027_0117'
down_revision='20261026_0116'
branch_labels=depends_on=None
OLD_HASH='c3b42e2f0ae11dbb94829eae68fd0f21c36c9e8a27118be9042d885588d3db4e'
NEW_HASH='cfa7f20da8cc4dfecf6d5aa2d146afc333c90b00366e29ed14811a2a042140ab'
TABLE='material_source_authority_decisions'
FUNCTION_NAME='rsc_guard_material_source_authority_0117'
PERMISSION_ID=uuid.UUID('36245f43-4d78-46e7-a6e0-8bf2274ba9e4')
ROLE_PERMISSION_ID=uuid.UUID('a39302cb-e162-42cf-b8e9-c441f48976d4')
ADMIN_ROLE_ID=uuid.UUID('10000000-0000-4000-8000-000000000001')
# Frozen principal checks from 0113, with a distinct material-source permission.
_legacy=runpy.run_path(str(Path(__file__).with_name('20261023_0113_inventory_control_authority.py')))
_prefix=_legacy['BODY'].split('    IF NEW.catalog_id IS NOT NULL THEN')[0].replace('0113','0117').replace(
    'public.inventory_control_source_bindings','public.oam_material_capture_bindings').replace(
    'public.inventory_control_authority_decisions','public.material_source_authority_decisions').replace(
    '    catalogue public.inventory_control_catalog_versions%ROWTYPE;\n','').replace(
    "resource='inventory_control'","resource='material_source'")
BODY=_prefix+"""
    PERFORM id FROM public.source_systems WHERE id=binding.source_system_id FOR SHARE;
    subject:=jsonb_build_object('binding_id',binding.id::text,'source_system_id',binding.source_system_id::text,
        'wire_binding',jsonb_build_object('source_system','starcharge_oam','source_instance',binding.source_instance,
            'scope_key','material:spares-visible','endpoint','/material_type/spare_list'),
        'key_id',binding.key_id,'key_fingerprint',binding.key_fingerprint,
        'registered_at_us',(extract(epoch FROM binding.created_at)*1000000)::bigint,
        'valid_from_us',(extract(epoch FROM binding.valid_from)*1000000)::bigint,
        'valid_to_us',(extract(epoch FROM binding.valid_to)*1000000)::bigint);
    SELECT jsonb_build_object('file_id',f.id::text,'sha256',f.sha256,'size_bytes',f.size_bytes,
        'mime_type',f.mime_type,'storage_key',f.storage_key) INTO file_evidence FROM public.files f
        WHERE f.id=NEW.evidence_file_id AND f.sha256=NEW.evidence_sha256 AND f.status='available' AND f.size_bytes>0 FOR SHARE;
    request:=NEW.payload_jsonb->'request';
    IF jsonb_typeof(NEW.payload_jsonb) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(NEW.payload_jsonb))<>20
       OR jsonb_typeof(request) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(request))<>11
       OR file_evidence IS NULL OR NEW.payload_jsonb->'evidence_file' IS DISTINCT FROM file_evidence
       OR NEW.payload_jsonb->'subject' IS DISTINCT FROM subject
       OR NEW.payload_jsonb->>'schema_version' IS DISTINCT FROM 'rsc.material_source_authority.v1'
       OR NEW.payload_jsonb->>'decision_id' IS DISTINCT FROM NEW.id::text
       OR NEW.payload_jsonb->>'action' IS DISTINCT FROM NEW.action
       OR NEW.payload_jsonb->>'subject_sha256' IS DISTINCT FROM NEW.subject_sha256
       OR NEW.subject_sha256 IS DISTINCT FROM encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(subject),'UTF8')),'hex')
       OR NEW.payload_jsonb->>'revoked_grant_id' IS DISTINCT FROM NEW.revoked_grant_id::text
       OR (NEW.payload_jsonb->>'created_at')::timestamptz IS DISTINCT FROM NEW.created_at
       OR (NEW.payload_jsonb->>'valid_from')::timestamptz IS DISTINCT FROM NEW.valid_from
       OR (NEW.payload_jsonb->>'valid_to')::timestamptz IS DISTINCT FROM NEW.valid_to
       OR NEW.payload_jsonb->>'actor_user_id' IS DISTINCT FROM NEW.actor_user_id
       OR NEW.payload_jsonb->>'actor_person_id' IS DISTINCT FROM NEW.actor_person_id::text
       OR (NEW.payload_jsonb->>'actor_authorization_version')::bigint IS DISTINCT FROM NEW.actor_authorization_version
       OR NEW.payload_jsonb->'actor_snapshot'->>'user_id' IS DISTINCT FROM NEW.actor_user_id
       OR NEW.payload_jsonb->'actor_snapshot'->>'person_id' IS DISTINCT FROM NEW.actor_person_id::text
       OR (NEW.payload_jsonb->'actor_snapshot'->>'authorization_version')::bigint IS DISTINCT FROM NEW.actor_authorization_version
       OR NEW.payload_jsonb->>'auth_session_id' IS DISTINCT FROM NEW.auth_session_id
       OR request->>'action' IS DISTINCT FROM NEW.action OR request->>'binding_id' IS DISTINCT FROM NEW.binding_id::text
       OR request->>'revoked_grant_id' IS DISTINCT FROM NEW.revoked_grant_id::text
       OR request->>'evidence_file_id' IS DISTINCT FROM NEW.evidence_file_id::text
       OR request->>'evidence_sha256' IS DISTINCT FROM NEW.evidence_sha256
       OR request->>'request_id' IS DISTINCT FROM NEW.request_id
       OR request->>'idempotency_key' IS DISTINCT FROM NEW.idempotency_key
       OR (request->>'valid_to')::timestamptz IS DISTINCT FROM NEW.valid_to
       OR COALESCE((request->>'valid_from')::timestamptz,NEW.created_at) IS DISTINCT FROM NEW.valid_from
       OR length(btrim(COALESCE(request->>'reason',''))) NOT BETWEEN 1 AND 1000
       OR NEW.request_id !~ '^[A-Za-z0-9._:-]{8,160}$' OR NEW.idempotency_key !~ '^[A-Za-z0-9._:-]{16,128}$'
       OR NEW.review_sha256 !~ '^[a-f0-9]{64}$' OR NEW.evidence_sha256 !~ '^[a-f0-9]{64}$'
       OR NEW.payload_jsonb->>'review_sha256' IS DISTINCT FROM NEW.review_sha256
       OR NEW.payload_jsonb->>'request_sha256' IS DISTINCT FROM NEW.request_sha256
       OR NEW.request_sha256 IS DISTINCT FROM encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(
           jsonb_build_object('actor_user_id',NEW.actor_user_id,'request',request)),'UTF8')),'hex')
       OR NEW.payload_sha256 IS DISTINCT FROM encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(NEW.payload_jsonb),'UTF8')),'hex')
       OR NOT isfinite(NEW.created_at) OR NOT isfinite(NEW.valid_from) OR (NEW.valid_to IS NOT NULL AND NOT isfinite(NEW.valid_to))
    THEN RAISE EXCEPTION '0117 material authority evidence mismatch' USING ERRCODE='23514'; END IF;
    IF COALESCE(NEW.payload_jsonb->>'access_issued_at','') !~ '^[0-9]+$'
       OR COALESCE(NEW.payload_jsonb->>'access_expires_at','') !~ '^[0-9]+$'
       OR NOT EXISTS (SELECT 1 FROM public.auth_sessions s WHERE s.id=NEW.auth_session_id
          AND s.user_id=NEW.actor_user_id AND s.client_type='web' AND length(btrim(s.device_id))>0
          AND s.ip_address ~ '^hmac:[0-9]+:[a-f0-9]{64}$'
          AND s.revoked_at IS NULL AND s.expires_at>NEW.created_at
          AND extract(epoch FROM s.created_at)<(NEW.payload_jsonb->>'access_issued_at')::bigint+1
          AND (NEW.payload_jsonb->>'access_issued_at')::bigint<=extract(epoch FROM NEW.created_at)
          AND extract(epoch FROM NEW.created_at)<(NEW.payload_jsonb->>'access_expires_at')::bigint)
    THEN RAISE EXCEPTION '0117 current web session required' USING ERRCODE='23514'; END IF;
    IF NEW.action='revoke' THEN
        SELECT * INTO parent FROM public.material_source_authority_decisions WHERE id=NEW.revoked_grant_id;
        IF parent.id IS NULL OR parent.action<>'grant' OR parent.binding_id<>NEW.binding_id
           OR parent.subject_sha256 IS DISTINCT FROM NEW.subject_sha256 OR parent.created_at>NEW.created_at
           OR request->>'expected_subject_sha256' IS DISTINCT FROM parent.payload_sha256
           OR EXISTS (SELECT 1 FROM public.material_source_authority_decisions WHERE revoked_grant_id=parent.id)
        THEN RAISE EXCEPTION '0117 exact original grant required' USING ERRCODE='23514'; END IF;
    ELSE
        IF request->>'expected_subject_sha256' IS DISTINCT FROM NEW.subject_sha256
           OR NOT EXISTS (SELECT 1 FROM public.source_systems s WHERE s.id=binding.source_system_id AND s.code='oam' AND s.enabled AND s.mode='read_only')
           OR binding.revoked_at IS NOT NULL OR binding.valid_to<=clock_timestamp()
           OR NEW.valid_from<binding.valid_from OR NEW.valid_to>binding.valid_to
        THEN RAISE EXCEPTION '0117 reviewed material source unavailable' USING ERRCODE='23514'; END IF;
        IF EXISTS (SELECT 1 FROM public.material_source_authority_decisions g
            LEFT JOIN public.material_source_authority_decisions revoked ON revoked.revoked_grant_id=g.id
            WHERE g.binding_id=NEW.binding_id AND g.action='grant'
              AND LEAST(g.valid_to,revoked.created_at)>NEW.valid_from AND NEW.valid_to>g.valid_from)
        THEN RAISE EXCEPTION '0117 authority windows overlap' USING ERRCODE='23514'; END IF;
    END IF;
    expected_audit:=jsonb_build_object('decision_id',NEW.id::text,'action',NEW.action,'binding_id',NEW.binding_id::text,
        'subject_sha256',NEW.subject_sha256,'payload_sha256',NEW.payload_sha256);
    IF NOT EXISTS (SELECT 1 FROM public.audit_events a WHERE a.id=NEW.audit_event_id AND a.stream_key='authorization'
        AND a.actor_user_id=NEW.actor_user_id AND a.action='material_source.authority.'||NEW.action
        AND a.aggregate_type='material_source_authority_decision' AND a.aggregate_id=NEW.id::text
        AND a.request_id='material-authority:'||NEW.id::text AND a.occurred_at=NEW.created_at
        AND a.before_jsonb='{}'::jsonb AND a.after_jsonb=expected_audit)
    THEN RAISE EXCEPTION '0117 material authority audit mismatch' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END;
"""
FUNCTION_HASH=hashlib.sha256(BODY.encode()).hexdigest()
TRIGGERS={'trg_material_authority_facts_0117':(TABLE,'INSERT OR UPDATE OR DELETE',31),
          'trg_material_authority_truncate_0117':(TABLE,'TRUNCATE',34)}


def _create_table():
    op.create_table(TABLE,
        sa.Column('id',sa.Uuid(),primary_key=True),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('binding_id',sa.Uuid(),sa.ForeignKey('oam_material_capture_bindings.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('action',sa.String(24),nullable=False),
        sa.Column('revoked_grant_id',sa.Uuid(),sa.ForeignKey(TABLE+'.id',ondelete='RESTRICT'),nullable=True),
        sa.Column('subject_sha256',sa.String(64),nullable=False),
        sa.Column('valid_from',sa.DateTime(timezone=True),nullable=False),sa.Column('valid_to',sa.DateTime(timezone=True),nullable=True),
        sa.Column('actor_user_id',sa.String(36),sa.ForeignKey('users.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('actor_person_id',sa.Uuid(),sa.ForeignKey('people.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('actor_authorization_version',sa.BigInteger(),nullable=False),
        sa.Column('auth_session_id',sa.String(36),sa.ForeignKey('auth_sessions.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('evidence_file_id',sa.Uuid(),sa.ForeignKey('files.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('evidence_sha256',sa.String(64),nullable=False),sa.Column('idempotency_key',sa.String(128),nullable=False),
        sa.Column('request_id',sa.String(160),nullable=False),sa.Column('request_sha256',sa.String(64),nullable=False),
        sa.Column('review_sha256',sa.String(64),nullable=False),
        sa.Column('payload_jsonb',sa.JSON().with_variant(JSONB(),'postgresql'),nullable=False),sa.Column('payload_sha256',sa.String(64),nullable=False),
        sa.Column('audit_event_id',sa.Uuid(),sa.ForeignKey('audit_events.id',ondelete='RESTRICT'),nullable=False,unique=True),
        sa.CheckConstraint("action IN ('grant','revoke')",name='ck_material_authority_action'),
        sa.CheckConstraint('valid_from>=created_at AND (valid_to IS NULL OR valid_to>valid_from)',name='ck_material_authority_validity'),
        sa.CheckConstraint("(action='grant' AND revoked_grant_id IS NULL AND valid_to IS NOT NULL) OR (action='revoke' AND revoked_grant_id IS NOT NULL AND valid_to IS NULL AND valid_from=created_at)",name='ck_material_authority_shape'),
        sa.CheckConstraint('actor_authorization_version>0 AND length(subject_sha256)=64 AND length(payload_sha256)=64 AND length(request_sha256)=64 AND length(review_sha256)=64 AND length(evidence_sha256)=64',name='ck_material_authority_proof'),
        sa.UniqueConstraint('actor_user_id','idempotency_key',name='uq_material_authority_actor_key'),
        sa.UniqueConstraint('actor_user_id','request_id',name='uq_material_authority_actor_request'),
        sa.UniqueConstraint('revoked_grant_id',name='uq_material_authority_revocation'))
    op.create_index('ix_material_authority_subject',TABLE,['binding_id','created_at','id'])


def _seed(upgrade):
    permission=sa.table('permissions',sa.column('id',sa.Uuid()),sa.column('resource',sa.String()),sa.column('action',sa.String()),sa.column('field_code',sa.String()),sa.column('description',sa.String()),sa.column('created_at',sa.DateTime(timezone=True)),sa.column('updated_at',sa.DateTime(timezone=True)))
    relation=sa.table('role_permissions',sa.column('id',sa.Uuid()),sa.column('role_id',sa.Uuid()),sa.column('permission_id',sa.Uuid()),sa.column('effect',sa.String()),sa.column('created_at',sa.DateTime(timezone=True)))
    if upgrade:
        now=datetime.now(timezone.utc)
        op.bulk_insert(permission,[dict(id=PERMISSION_ID,resource='material_source',action='authorize',field_code='',description='Review and revoke exact OAM material source authority',created_at=now,updated_at=now)])
        op.bulk_insert(relation,[dict(id=ROLE_PERMISSION_ID,role_id=ADMIN_ROLE_ID,permission_id=PERMISSION_ID,effect='allow',created_at=now)])
    else:
        op.execute(relation.delete().where(relation.c.permission_id==op.inline_literal(PERMISSION_ID,type_=sa.Uuid())))
        op.execute(permission.delete().where(permission.c.id==op.inline_literal(PERMISSION_ID,type_=sa.Uuid())))


def _transition(upgrade):
    dialect=op.get_bind().dialect.name
    if dialect not in {'sqlite','postgresql'}:raise RuntimeError('0117 supports PostgreSQL and SQLite only')
    folder=Path(__file__).parent
    helper=runpy.run_path(str(folder/'20260927_0087_inbound_fulfillment_boundary.py'))
    helper['_begin_sqlite']()
    if dialect=='postgresql':op.execute('LOCK TABLE public.alembic_version IN ACCESS EXCLUSIVE MODE')
    if upgrade:_create_table();_seed(True)
    else:
        if dialect=='postgresql':op.execute(f'LOCK TABLE public.{TABLE} IN ACCESS EXCLUSIVE MODE')
        helper['_preflight'](f"EXISTS (SELECT 1 FROM {TABLE}) OR EXISTS (SELECT 1 FROM audit_events WHERE stream_key='authorization' AND (aggregate_type='material_source_authority_decision' OR action LIKE 'material_source.authority.%'))",'0117 downgrade blocked: material source authority must be retained')
        if dialect=='postgresql':op.execute('LOCK TABLE public.permissions, public.role_permissions IN SHARE MODE')
        permission_id=str(PERMISSION_ID) if dialect=='postgresql' else PERMISSION_ID.hex
        role_permission_id=str(ROLE_PERMISSION_ID) if dialect=='postgresql' else ROLE_PERMISSION_ID.hex
        admin_role_id=str(ADMIN_ROLE_ID) if dialect=='postgresql' else ADMIN_ROLE_ID.hex
        helper['_preflight'](f"(SELECT count(*) FROM permissions WHERE id='{permission_id}' AND resource='material_source' AND action='authorize' AND field_code='')<>1 OR (SELECT count(*) FROM role_permissions WHERE permission_id='{permission_id}')<>1 OR NOT EXISTS (SELECT 1 FROM role_permissions WHERE id='{role_permission_id}' AND role_id='{admin_role_id}' AND permission_id='{permission_id}' AND effect='allow')",'0117 downgrade blocked: material authority permission catalog drift')
    if dialect=='postgresql':
        if upgrade:
            op.execute(f'CREATE FUNCTION public.{FUNCTION_NAME}() RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog, public AS $body${BODY}$body$')
            previous=runpy.run_path(str(folder/'20261026_0116_material_capture_ingress.py'))
            previous['_revoke']('FUNCTION','public.'+FUNCTION_NAME+'()','pg_proc','public.'+FUNCTION_NAME+'()')
            previous['_revoke']('TABLE','public.'+TABLE,'pg_class','public.'+TABLE)
            op.execute(f'GRANT SELECT ON TABLE public.{TABLE} TO star_oam_backup')
            for name,(table,events,_) in TRIGGERS.items():
                op.execute(f"CREATE TRIGGER {name} BEFORE {events} ON public.{table} FOR EACH {'STATEMENT' if events=='TRUNCATE' else 'ROW'} EXECUTE FUNCTION public.{FUNCTION_NAME}()")
                op.execute(f'ALTER TABLE public.{table} ENABLE ALWAYS TRIGGER {name}')
        else:
            op.execute(f"""DO $body$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_proc p
                WHERE p.oid='public.{FUNCTION_NAME}()'::regprocedure AND NOT p.prosecdef
                  AND p.proowner=(SELECT oid FROM pg_roles WHERE rolname=current_user)
                  AND encode(sha256(convert_to(p.prosrc,'UTF8')),'hex')='{FUNCTION_HASH}'
                  AND p.proconfig=ARRAY['search_path=pg_catalog, public']
                  AND NOT EXISTS (SELECT 1 FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a WHERE a.grantee<>p.proowner))
                THEN RAISE EXCEPTION '0117 guard ownership, source or ACL drift'; END IF; END $body$""")
            for name,(table,*_) in TRIGGERS.items():op.execute(f'DROP TRIGGER {name} ON public.{table}')
            op.execute(f'DROP FUNCTION public.{FUNCTION_NAME}()')
        replace=runpy.run_path(str(folder/'20260912_0072_outbound_postings.py'))['_previous']()['_previous']()['_previous']()['_replace_function_source']
        replace(signature='public.rsc_oam_runtime_binding_ready_0044()',expected_hash=OLD_HASH if upgrade else NEW_HASH,
            replacement_hash=NEW_HASH if upgrade else OLD_HASH,replacements=((down_revision,revision),) if upgrade else ((revision,down_revision),),label='material_authority_readiness_0117')
    elif upgrade:
        for event in ('UPDATE','DELETE'):
            op.execute(f"CREATE TRIGGER trg_material_authority_{event.lower()}_0117 BEFORE {event} ON {TABLE} BEGIN SELECT RAISE(ABORT,'0117 material source authority is append-only'); END")
    if not upgrade:_seed(False);op.drop_table(TABLE)


def upgrade():_transition(True)
def downgrade():_transition(False)
