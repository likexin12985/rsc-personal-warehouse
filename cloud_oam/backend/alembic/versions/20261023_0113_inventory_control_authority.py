"""Exact source/catalogue authority and revocation, separate from publication."""
import hashlib
from datetime import datetime, timezone
from pathlib import Path
import runpy
import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '20261023_0113'
down_revision = '20261022_0112'
branch_labels = depends_on = None
OLD_HASH = 'a00b94ede4e9dcefb19656119fc9de62345a6f10987cebde49d392d04de29ab8'
NEW_HASH = '5490b4be13d675f266046001f6196d839f08108c6560b5acf07f3ab88a413aa8'
TABLE = 'inventory_control_authority_decisions'
FUNCTION_NAME = 'rsc_guard_inventory_control_authority_0113'
PERMISSION_ID = uuid.UUID('dc70a539-9709-4c14-b343-a5fc8618e21c')
ROLE_PERMISSION_ID = uuid.UUID('242de6c0-7df5-4ac3-9a3f-020bb92c8c2d')
ADMIN_ROLE_ID = uuid.UUID('10000000-0000-4000-8000-000000000001')
BODY = """
DECLARE
    binding public.inventory_control_source_bindings%ROWTYPE;
    parent public.inventory_control_authority_decisions%ROWTYPE;
    catalogue public.inventory_control_catalog_versions%ROWTYPE;
    subject jsonb;
    expected_audit jsonb;
    request jsonb;
    file_evidence jsonb;
BEGIN
    IF TG_OP<>'INSERT' THEN
        RAISE EXCEPTION '0113 authority decisions are append-only' USING ERRCODE='23514';
    END IF;
    IF current_user<>'star_oam_migrator' OR session_user<>'star_oam_migrator' THEN
        RAISE EXCEPTION '0113 authority requires direct schema owner' USING ERRCODE='42501';
    END IF;
    PERFORM public.rsc_lock_formal_principal_graph_0026(ARRAY[NEW.actor_user_id]::text[]);
    SELECT * INTO STRICT binding FROM public.inventory_control_source_bindings WHERE id=NEW.binding_id FOR UPDATE;
    IF NEW.created_at<transaction_timestamp() OR NEW.created_at>clock_timestamp() THEN
        RAISE EXCEPTION '0113 authority cannot backdate its decision' USING ERRCODE='23514';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM public.users u JOIN public.people p ON p.id=u.person_id
        JOIN public.organizations o ON o.id=p.organization_id
        JOIN public.role_assignments a ON a.user_id=u.id
        JOIN public.roles r ON r.id=a.role_id
        JOIN public.role_permissions rp ON rp.role_id=r.id
        JOIN public.permissions permission ON permission.id=rp.permission_id
        WHERE u.id=NEW.actor_user_id AND u.person_id=NEW.actor_person_id AND u.is_active AND u.account_status='active'
          AND u.authorization_version=NEW.actor_authorization_version AND p.employment_status='active'
          AND o.status='active' AND o.org_type='headquarters'
          AND r.code='admin' AND r.status='active' AND NOT r.is_external
          AND a.scope_type='national' AND a.scope_id='*' AND a.status IN ('active','scheduled') AND a.revoked_at IS NULL
          AND a.valid_from<=NEW.created_at AND (a.valid_to IS NULL OR a.valid_to>NEW.created_at)
          AND permission.resource='inventory_control' AND permission.action='authorize' AND permission.field_code='' AND rp.effect='allow'
          AND EXISTS (SELECT 1 FROM public.auth_identities identity WHERE identity.user_id=u.id
              AND identity.status='active' AND identity.revoked_at IS NULL AND identity.verified_at<=NEW.created_at)
    ) OR EXISTS (
        SELECT 1 FROM public.role_assignments a JOIN public.roles r ON r.id=a.role_id
        JOIN public.role_permissions rp ON rp.role_id=r.id JOIN public.permissions p ON p.id=rp.permission_id
        WHERE a.user_id=NEW.actor_user_id AND r.status='active' AND a.scope_type='national' AND a.scope_id='*'
          AND a.status IN ('active','scheduled') AND a.revoked_at IS NULL AND a.valid_from<=NEW.created_at
          AND (a.valid_to IS NULL OR a.valid_to>NEW.created_at) AND rp.effect='deny'
          AND p.resource='inventory_control' AND p.action='authorize' AND p.field_code=''
    ) THEN RAISE EXCEPTION '0113 active headquarters authority required' USING ERRCODE='23514'; END IF;
    IF NEW.catalog_id IS NOT NULL THEN
        SELECT * INTO catalogue FROM public.inventory_control_catalog_versions WHERE id=NEW.catalog_id AND binding_id=NEW.binding_id;
        IF catalogue.id IS NULL THEN RAISE EXCEPTION '0113 catalogue binding mismatch' USING ERRCODE='23514'; END IF;
    END IF;
    subject:=jsonb_build_object('binding_id',binding.id::text,'binding_sha256',binding.binding_sha256,
        'source_system_id',binding.source_system_id::text,'region_org_id',binding.region_org_id::text,
        'target_region_code',binding.target_region_code,'catalog_id',NEW.catalog_id::text,'catalog_sha256',catalogue.catalog_sha256);
    SELECT jsonb_build_object('file_id',f.id::text,'sha256',f.sha256,'size_bytes',f.size_bytes,
        'mime_type',f.mime_type,'storage_key',f.storage_key) INTO file_evidence FROM public.files f
        WHERE f.id=NEW.evidence_file_id AND f.sha256=NEW.evidence_sha256 AND f.status='available' AND f.size_bytes>0 FOR SHARE;
    request:=NEW.payload_jsonb->'request';
    IF file_evidence IS NULL OR NEW.payload_jsonb->'evidence_file' IS DISTINCT FROM file_evidence
       OR NEW.payload_jsonb->'subject' IS DISTINCT FROM subject
       OR NEW.payload_jsonb->>'schema_version' IS DISTINCT FROM 'rsc.inventory_control_authority.v1'
       OR NEW.payload_jsonb->>'decision_id' IS DISTINCT FROM NEW.id::text
       OR NEW.payload_jsonb->>'action' IS DISTINCT FROM NEW.action
       OR NEW.payload_jsonb->>'source_grant_id' IS DISTINCT FROM NEW.source_grant_id::text
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
       OR request->>'action' IS DISTINCT FROM NEW.action OR request->>'binding_id' IS DISTINCT FROM NEW.binding_id::text
       OR request->>'catalog_id' IS DISTINCT FROM NEW.catalog_id::text
       OR request->>'source_grant_id' IS DISTINCT FROM NEW.source_grant_id::text
       OR request->>'revoked_grant_id' IS DISTINCT FROM NEW.revoked_grant_id::text
       OR request->>'evidence_file_id' IS DISTINCT FROM NEW.evidence_file_id::text
       OR request->>'evidence_sha256' IS DISTINCT FROM NEW.evidence_sha256
       OR request->>'request_id' IS DISTINCT FROM NEW.request_id
       OR request->>'idempotency_key' IS DISTINCT FROM NEW.idempotency_key
       OR (request->>'valid_to')::timestamptz IS DISTINCT FROM NEW.valid_to
       OR COALESCE((request->>'valid_from')::timestamptz,NEW.created_at) IS DISTINCT FROM NEW.valid_from
       OR length(btrim(COALESCE(request->>'reason',''))) NOT BETWEEN 1 AND 1000
       OR NEW.request_id !~ '^[A-Za-z0-9._:-]{8,160}$' OR NEW.idempotency_key !~ '^[A-Za-z0-9._:-]{16,128}$'
       OR NEW.payload_jsonb->>'request_sha256' IS DISTINCT FROM NEW.request_sha256
       OR NEW.request_sha256 IS DISTINCT FROM encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(
           jsonb_build_object('actor_user_id',NEW.actor_user_id,'request',request)),'UTF8')),'hex')
       OR NEW.payload_sha256 IS DISTINCT FROM encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(NEW.payload_jsonb),'UTF8')),'hex')
    THEN RAISE EXCEPTION '0113 authority evidence mismatch' USING ERRCODE='23514'; END IF;
    IF NEW.action='revoke' THEN
        SELECT * INTO parent FROM public.inventory_control_authority_decisions WHERE id=NEW.revoked_grant_id;
        IF parent.id IS NULL OR parent.action='revoke' OR parent.binding_id<>NEW.binding_id
           OR parent.catalog_id IS DISTINCT FROM NEW.catalog_id OR parent.created_at>NEW.created_at
           OR request->>'expected_subject_sha256' IS DISTINCT FROM parent.payload_sha256
           OR EXISTS (SELECT 1 FROM public.inventory_control_authority_decisions WHERE revoked_grant_id=parent.id)
        THEN RAISE EXCEPTION '0113 exact original grant required' USING ERRCODE='23514'; END IF;
    ELSE
        IF NOT EXISTS (SELECT 1 FROM public.source_systems s WHERE s.id=binding.source_system_id AND lower(btrim(s.code))='oam' AND s.enabled AND s.mode='read_only')
           OR NOT EXISTS (SELECT 1 FROM public.organizations o WHERE o.id=binding.region_org_id AND o.status='active' AND o.org_type='region_company')
           OR request->>'expected_subject_sha256' IS DISTINCT FROM (CASE WHEN NEW.action='source_grant' THEN binding.binding_sha256 ELSE catalogue.catalog_sha256 END)
        THEN RAISE EXCEPTION '0113 reviewed source or version unavailable' USING ERRCODE='23514'; END IF;
        IF NEW.action='catalog_grant' THEN
            SELECT * INTO parent FROM public.inventory_control_authority_decisions WHERE id=NEW.source_grant_id;
            IF parent.id IS NULL OR parent.action<>'source_grant' OR parent.binding_id<>NEW.binding_id
               OR parent.valid_from>NEW.valid_from OR (parent.valid_to IS NOT NULL AND (NEW.valid_to IS NULL OR parent.valid_to<NEW.valid_to))
               OR EXISTS (SELECT 1 FROM public.inventory_control_authority_decisions WHERE revoked_grant_id=parent.id)
            THEN RAISE EXCEPTION '0113 exact effective source grant required' USING ERRCODE='23514'; END IF;
        END IF;
        IF EXISTS (
            SELECT 1 FROM public.inventory_control_authority_decisions g
            LEFT JOIN public.inventory_control_authority_decisions revoked ON revoked.revoked_grant_id=g.id
            LEFT JOIN public.inventory_control_authority_decisions source_revoked ON source_revoked.revoked_grant_id=g.source_grant_id
            WHERE g.binding_id=NEW.binding_id AND g.catalog_id IS NOT DISTINCT FROM NEW.catalog_id AND g.action=NEW.action
              AND (LEAST(g.valid_to,revoked.created_at,source_revoked.created_at) IS NULL OR LEAST(g.valid_to,revoked.created_at,source_revoked.created_at)>NEW.valid_from)
              AND (NEW.valid_to IS NULL OR NEW.valid_to>g.valid_from)
        ) THEN RAISE EXCEPTION '0113 authority windows overlap' USING ERRCODE='23514'; END IF;
    END IF;
    expected_audit:=jsonb_build_object('decision_id',NEW.id::text,'action',NEW.action,'binding_id',NEW.binding_id::text,
        'catalog_id',NEW.catalog_id::text,'payload_sha256',NEW.payload_sha256);
    IF NOT EXISTS (SELECT 1 FROM public.audit_events a WHERE a.id=NEW.audit_event_id AND a.stream_key='authorization'
        AND a.actor_user_id=NEW.actor_user_id AND a.action='inventory_control.authority.'||NEW.action
        AND a.aggregate_type='inventory_control_authority_decision' AND a.aggregate_id=NEW.id::text
        AND a.request_id='control-authority:'||NEW.id::text AND a.occurred_at=NEW.created_at
        AND a.before_jsonb='{}'::jsonb AND a.after_jsonb=expected_audit)
    THEN RAISE EXCEPTION '0113 authority audit mismatch' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END;
"""
FUNCTION_HASH = hashlib.sha256(BODY.encode()).hexdigest()
TRIGGERS = {
    'trg_control_authority_facts_0113': (TABLE, 'INSERT OR UPDATE OR DELETE', 31),
    'trg_control_authority_truncate_0113': (TABLE, 'TRUNCATE', 34),
}


def _create_table():
    op.create_table(TABLE,
        sa.Column('id',sa.Uuid(),primary_key=True), sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('binding_id',sa.Uuid(),sa.ForeignKey('inventory_control_source_bindings.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('catalog_id',sa.Uuid(),sa.ForeignKey('inventory_control_catalog_versions.id',ondelete='RESTRICT'),nullable=True),
        sa.Column('action',sa.String(24),nullable=False),
        sa.Column('source_grant_id',sa.Uuid(),sa.ForeignKey(TABLE+'.id',ondelete='RESTRICT'),nullable=True),
        sa.Column('revoked_grant_id',sa.Uuid(),sa.ForeignKey(TABLE+'.id',ondelete='RESTRICT'),nullable=True),
        sa.Column('valid_from',sa.DateTime(timezone=True),nullable=False),sa.Column('valid_to',sa.DateTime(timezone=True),nullable=True),
        sa.Column('actor_user_id',sa.String(36),sa.ForeignKey('users.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('actor_person_id',sa.Uuid(),sa.ForeignKey('people.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('actor_authorization_version',sa.BigInteger(),nullable=False),
        sa.Column('evidence_file_id',sa.Uuid(),sa.ForeignKey('files.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('evidence_sha256',sa.String(64),nullable=False),sa.Column('idempotency_key',sa.String(128),nullable=False),
        sa.Column('request_id',sa.String(160),nullable=False),sa.Column('request_sha256',sa.String(64),nullable=False),
        sa.Column('payload_jsonb',sa.JSON().with_variant(JSONB(),'postgresql'),nullable=False),sa.Column('payload_sha256',sa.String(64),nullable=False),
        sa.Column('audit_event_id',sa.Uuid(),sa.ForeignKey('audit_events.id',ondelete='RESTRICT'),nullable=False,unique=True),
        sa.CheckConstraint("action IN ('source_grant','catalog_grant','revoke')",name='ck_control_authority_action'),
        sa.CheckConstraint('valid_from >= created_at AND (valid_to IS NULL OR valid_to > valid_from)',name='ck_control_authority_validity'),
        sa.CheckConstraint('actor_authorization_version > 0 AND length(request_sha256)=64 AND length(payload_sha256)=64 AND length(evidence_sha256)=64',name='ck_control_authority_proof'),
        sa.CheckConstraint("(action='source_grant' AND catalog_id IS NULL AND source_grant_id IS NULL AND revoked_grant_id IS NULL) OR (action='catalog_grant' AND catalog_id IS NOT NULL AND source_grant_id IS NOT NULL AND revoked_grant_id IS NULL) OR (action='revoke' AND revoked_grant_id IS NOT NULL AND source_grant_id IS NULL AND valid_to IS NULL AND valid_from=created_at)",name='ck_control_authority_shape'),
        sa.UniqueConstraint('actor_user_id','idempotency_key',name='uq_control_authority_actor_key'),
        sa.UniqueConstraint('actor_user_id','request_id',name='uq_control_authority_actor_request'),
        sa.UniqueConstraint('revoked_grant_id',name='uq_control_authority_one_revocation'))
    op.create_index('ix_control_authority_subject',TABLE,['binding_id','catalog_id','created_at','id'])


def _seed(upgrade):
    permission=sa.table('permissions',sa.column('id',sa.Uuid()),sa.column('resource',sa.String()),sa.column('action',sa.String()),sa.column('field_code',sa.String()),sa.column('description',sa.String()),sa.column('created_at',sa.DateTime(timezone=True)),sa.column('updated_at',sa.DateTime(timezone=True)))
    relation=sa.table('role_permissions',sa.column('id',sa.Uuid()),sa.column('role_id',sa.Uuid()),sa.column('permission_id',sa.Uuid()),sa.column('effect',sa.String()),sa.column('created_at',sa.DateTime(timezone=True)))
    if upgrade:
        now=datetime.now(timezone.utc)
        op.bulk_insert(permission,[dict(id=PERMISSION_ID,resource='inventory_control',action='authorize',field_code='',description='Review and revoke exact control source and catalogue versions',created_at=now,updated_at=now)])
        op.bulk_insert(relation,[dict(id=ROLE_PERMISSION_ID,role_id=ADMIN_ROLE_ID,permission_id=PERMISSION_ID,effect='allow',created_at=now)])
    else:
        op.execute(relation.delete().where(relation.c.permission_id==op.inline_literal(PERMISSION_ID,type_=sa.Uuid())))
        op.execute(permission.delete().where(permission.c.id==op.inline_literal(PERMISSION_ID,type_=sa.Uuid())))


def _transition(upgrade):
    dialect=op.get_bind().dialect.name
    if dialect not in {'sqlite','postgresql'}:raise RuntimeError('0113 supports PostgreSQL and SQLite only')
    folder=Path(__file__).parent
    helper=runpy.run_path(str(folder/'20260927_0087_inbound_fulfillment_boundary.py'))
    helper['_begin_sqlite']()
    if dialect=='postgresql':op.execute('LOCK TABLE public.alembic_version IN ACCESS EXCLUSIVE MODE')
    if upgrade:
        _create_table();_seed(True)
    else:
        if dialect=='postgresql':op.execute(f'LOCK TABLE public.{TABLE} IN ACCESS EXCLUSIVE MODE')
        helper['_preflight'](f"EXISTS (SELECT 1 FROM {TABLE}) OR EXISTS (SELECT 1 FROM audit_events WHERE stream_key='authorization' AND (aggregate_type='inventory_control_authority_decision' OR action LIKE 'inventory_control.authority.%'))", '0113 downgrade blocked: control authority must be retained')
        permission_id=str(PERMISSION_ID) if dialect=='postgresql' else PERMISSION_ID.hex
        role_permission_id=str(ROLE_PERMISSION_ID) if dialect=='postgresql' else ROLE_PERMISSION_ID.hex
        admin_role_id=str(ADMIN_ROLE_ID) if dialect=='postgresql' else ADMIN_ROLE_ID.hex
        helper['_preflight'](f"(SELECT count(*) FROM permissions WHERE id='{permission_id}' AND resource='inventory_control' AND action='authorize' AND field_code='')<>1 OR (SELECT count(*) FROM role_permissions WHERE permission_id='{permission_id}')<>1 OR NOT EXISTS (SELECT 1 FROM role_permissions WHERE id='{role_permission_id}' AND role_id='{admin_role_id}' AND permission_id='{permission_id}' AND effect='allow')",'0113 downgrade blocked: authority permission catalog drift')
    if dialect=='postgresql':
        if upgrade:
            op.execute(f'CREATE FUNCTION public.{FUNCTION_NAME}() RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog, public AS $body${BODY}$body$')
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
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='star_oam_backup') THEN GRANT SELECT ON TABLE public.{TABLE} TO star_oam_backup; END IF;
            END $body$""")
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
                THEN RAISE EXCEPTION '0113 guard ownership, source or ACL drift'; END IF; END $body$""")
            for name,(table,*_) in TRIGGERS.items():op.execute(f'DROP TRIGGER {name} ON public.{table}')
            op.execute(f'DROP FUNCTION public.{FUNCTION_NAME}()')
        replace=runpy.run_path(str(folder/'20260912_0072_outbound_postings.py'))['_previous']()['_previous']()['_previous']()['_replace_function_source']
        replace(signature='public.rsc_oam_runtime_binding_ready_0044()',expected_hash=OLD_HASH if upgrade else NEW_HASH,
            replacement_hash=NEW_HASH if upgrade else OLD_HASH,replacements=((down_revision,revision),) if upgrade else ((revision,down_revision),),label='control_authority_readiness_0113')
    elif upgrade:
        for event in ('UPDATE','DELETE'):
            op.execute(f"CREATE TRIGGER trg_control_authority_{event.lower()}_0113 BEFORE {event} ON {TABLE} BEGIN SELECT RAISE(ABORT,'0113 authority decisions are append-only'); END")
    if not upgrade:
        _seed(False);op.drop_table(TABLE)


def upgrade():_transition(True)
def downgrade():_transition(False)
