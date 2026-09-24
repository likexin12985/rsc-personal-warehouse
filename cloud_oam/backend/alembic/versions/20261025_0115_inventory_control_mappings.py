"""Reviewed normalization mappings with immutable decisions and explicit revocation."""
import hashlib
from pathlib import Path
import runpy

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision='20261025_0115'
down_revision='20261024_0114'
branch_labels=depends_on=None
OLD_HASH='896564c364887715d00d349b51053db48b2c8517d2f0f8f17fc06c17880b9ed5'
NEW_HASH='7dad591c17ca90b278337f7b012bd7e9d826e5fc7753dcb41b05e7baf518de97'
TABLE='inventory_control_mapping_decisions'
FUNCTION_NAME='rsc_guard_inventory_control_mapping_0115'
# Frozen 0113 prefix: direct owner, exact principal graph/permission/identity,
# binding lock, catalogue coordinates and current evidence-file shared lock.
_legacy=runpy.run_path(str(Path(__file__).with_name('20261023_0113_inventory_control_authority.py')))
_prefix=_legacy['BODY'].split('    request:=NEW.payload_jsonb')[0].replace('0113','0115').replace(
    'parent public.inventory_control_authority_decisions%ROWTYPE','parent public.inventory_control_mapping_decisions%ROWTYPE')
BODY=_prefix+"""
    PERFORM id FROM public.source_systems WHERE id=binding.source_system_id FOR SHARE;
    PERFORM id FROM public.organizations WHERE id=binding.region_org_id FOR SHARE;
    request:=NEW.payload_jsonb->'request';
    IF file_evidence IS NULL OR NEW.payload_jsonb->'evidence_file' IS DISTINCT FROM file_evidence
       OR NEW.payload_jsonb->'subject' IS DISTINCT FROM subject
       OR NEW.payload_jsonb->>'schema_version' IS DISTINCT FROM 'rsc.inventory_control_mapping_decision.v1'
       OR NEW.payload_jsonb->>'decision_id' IS DISTINCT FROM NEW.id::text
       OR NEW.payload_jsonb->>'action' IS DISTINCT FROM NEW.action
       OR NEW.payload_jsonb->>'revoked_grant_id' IS DISTINCT FROM NEW.revoked_grant_id::text
       OR NEW.payload_jsonb->'rules' IS DISTINCT FROM NEW.rules_jsonb
       OR NEW.rules_jsonb->>'revision' IS DISTINCT FROM NEW.rules_revision
       OR NEW.payload_jsonb->>'rules_sha256' IS DISTINCT FROM NEW.rules_sha256
       OR NEW.rules_sha256 IS DISTINCT FROM encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(NEW.rules_jsonb),'UTF8')),'hex')
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
       OR request->>'catalog_id' IS DISTINCT FROM NEW.catalog_id::text
       OR request->>'revoked_grant_id' IS DISTINCT FROM NEW.revoked_grant_id::text
       OR request->>'evidence_file_id' IS DISTINCT FROM NEW.evidence_file_id::text
       OR request->>'evidence_sha256' IS DISTINCT FROM NEW.evidence_sha256
       OR request->>'request_id' IS DISTINCT FROM NEW.request_id
       OR request->>'idempotency_key' IS DISTINCT FROM NEW.idempotency_key
       OR (request->>'valid_to')::timestamptz IS DISTINCT FROM NEW.valid_to
       OR COALESCE((request->>'valid_from')::timestamptz,NEW.created_at) IS DISTINCT FROM NEW.valid_from
       OR length(btrim(COALESCE(request->>'reason',''))) NOT BETWEEN 1 AND 1000
       OR NEW.request_id !~ '^[A-Za-z0-9._:-]{8,160}$' OR NEW.idempotency_key !~ '^[A-Za-z0-9._:-]{16,128}$'
       OR NEW.review_sha256 !~ '^[a-f0-9]{64}$'
       OR NEW.payload_jsonb->>'review_sha256' IS DISTINCT FROM NEW.review_sha256
       OR NEW.payload_jsonb->>'request_sha256' IS DISTINCT FROM NEW.request_sha256
       OR NEW.request_sha256 IS DISTINCT FROM encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(
           jsonb_build_object('actor_user_id',NEW.actor_user_id,'request',request)),'UTF8')),'hex')
       OR NEW.payload_sha256 IS DISTINCT FROM encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(NEW.payload_jsonb),'UTF8')),'hex')
    THEN RAISE EXCEPTION '0115 mapping evidence mismatch' USING ERRCODE='23514'; END IF;
    IF jsonb_typeof(NEW.rules_jsonb) IS DISTINCT FROM 'object'
       OR (SELECT count(*) FROM jsonb_object_keys(NEW.rules_jsonb))<>2
       OR NEW.rules_revision !~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$'
       OR jsonb_typeof(NEW.rules_jsonb->'conditions') IS DISTINCT FROM 'array'
    THEN RAISE EXCEPTION '0115 invalid mapping rules' USING ERRCODE='23514'; END IF;
    IF jsonb_array_length(NEW.rules_jsonb->'conditions')>100 OR EXISTS (
        SELECT 1 FROM jsonb_array_elements(NEW.rules_jsonb->'conditions') r
        WHERE jsonb_typeof(r) IS DISTINCT FROM 'object'
    ) THEN RAISE EXCEPTION '0115 invalid mapping rules' USING ERRCODE='23514'; END IF;
    IF EXISTS (SELECT 1 FROM jsonb_array_elements(NEW.rules_jsonb->'conditions') r
        WHERE (SELECT count(*) FROM jsonb_object_keys(r))<>3
          OR COALESCE(r->>'condition_code','') NOT IN ('new','used','damaged','scrapped')
          OR EXISTS (SELECT 1 FROM (VALUES ('material_status'),('material_stock_type')) k(name)
            WHERE COALESCE(jsonb_typeof(r->k.name),'null') NOT IN ('string','number')
              OR (jsonb_typeof(r->k.name)='string' AND (length(r->>k.name) NOT BETWEEN 1 AND 80
                  OR r->>k.name IS DISTINCT FROM btrim(r->>k.name) OR r->>k.name ~ '[[:cntrl:]]'))
              OR (jsonb_typeof(r->k.name)='number' AND ((r->>k.name)!~'^-?[0-9]+$'
                  OR (r->>k.name)::numeric NOT BETWEEN -2147483648 AND 2147483647))))
       OR EXISTS (SELECT 1 FROM jsonb_array_elements(NEW.rules_jsonb->'conditions') r
           GROUP BY r->'material_status',r->'material_stock_type' HAVING count(*)>1)
    THEN RAISE EXCEPTION '0115 invalid mapping rules' USING ERRCODE='23514'; END IF;
    IF COALESCE(NEW.payload_jsonb->>'access_issued_at','') !~ '^[0-9]+$'
       OR COALESCE(NEW.payload_jsonb->>'access_expires_at','') !~ '^[0-9]+$'
       OR NOT EXISTS (SELECT 1 FROM public.auth_sessions s WHERE s.id=NEW.auth_session_id
          AND s.user_id=NEW.actor_user_id AND s.client_type='web' AND length(btrim(s.device_id))>0
          AND s.revoked_at IS NULL AND s.expires_at>NEW.created_at
          AND extract(epoch FROM s.created_at)<(NEW.payload_jsonb->>'access_issued_at')::bigint+1
          AND (NEW.payload_jsonb->>'access_issued_at')::bigint<=extract(epoch FROM NEW.created_at)
          AND extract(epoch FROM NEW.created_at)<(NEW.payload_jsonb->>'access_expires_at')::bigint)
    THEN RAISE EXCEPTION '0115 current web session required' USING ERRCODE='23514'; END IF;
    IF NEW.action='revoke' THEN
        SELECT * INTO parent FROM public.inventory_control_mapping_decisions WHERE id=NEW.revoked_grant_id;
        IF parent.id IS NULL OR parent.action<>'grant' OR parent.binding_id<>NEW.binding_id OR parent.catalog_id<>NEW.catalog_id
           OR parent.rules_jsonb IS DISTINCT FROM NEW.rules_jsonb OR parent.created_at>NEW.created_at
           OR request->>'expected_subject_sha256' IS DISTINCT FROM parent.payload_sha256
           OR request->'rules' IS DISTINCT FROM 'null'::jsonb
           OR EXISTS (SELECT 1 FROM public.inventory_control_mapping_decisions WHERE revoked_grant_id=parent.id)
        THEN RAISE EXCEPTION '0115 exact original mapping required' USING ERRCODE='23514'; END IF;
    ELSE
        IF request->'rules' IS DISTINCT FROM NEW.rules_jsonb
           OR request->>'expected_subject_sha256' IS DISTINCT FROM catalogue.catalog_sha256
           OR NOT EXISTS (SELECT 1 FROM public.source_systems s WHERE s.id=binding.source_system_id AND lower(btrim(s.code))='oam' AND s.enabled AND s.mode='read_only')
           OR NOT EXISTS (SELECT 1 FROM public.organizations o WHERE o.id=binding.region_org_id AND o.status='active' AND o.org_type='region_company')
        THEN RAISE EXCEPTION '0115 reviewed mapping source unavailable' USING ERRCODE='23514'; END IF;
        IF EXISTS (SELECT 1 FROM public.inventory_control_mapping_decisions g
            LEFT JOIN public.inventory_control_mapping_decisions revoked ON revoked.revoked_grant_id=g.id
            WHERE g.binding_id=NEW.binding_id AND g.catalog_id=NEW.catalog_id AND g.action='grant'
              AND (LEAST(g.valid_to,revoked.created_at) IS NULL OR LEAST(g.valid_to,revoked.created_at)>NEW.valid_from)
              AND (NEW.valid_to IS NULL OR NEW.valid_to>g.valid_from))
        THEN RAISE EXCEPTION '0115 mapping windows overlap' USING ERRCODE='23514'; END IF;
    END IF;
    expected_audit:=jsonb_build_object('decision_id',NEW.id::text,'action',NEW.action,'binding_id',NEW.binding_id::text,
        'catalog_id',NEW.catalog_id::text,'rules_sha256',NEW.rules_sha256,'payload_sha256',NEW.payload_sha256);
    IF NOT EXISTS (SELECT 1 FROM public.audit_events a WHERE a.id=NEW.audit_event_id AND a.stream_key='authorization'
        AND a.actor_user_id=NEW.actor_user_id AND a.action='inventory_control.mapping.'||NEW.action
        AND a.aggregate_type='inventory_control_mapping_decision' AND a.aggregate_id=NEW.id::text
        AND a.request_id='control-mapping:'||NEW.id::text AND a.occurred_at=NEW.created_at
        AND a.before_jsonb='{}'::jsonb AND a.after_jsonb=expected_audit)
    THEN RAISE EXCEPTION '0115 mapping audit mismatch' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END;
"""
FUNCTION_HASH=hashlib.sha256(BODY.encode()).hexdigest()
TRIGGERS={'trg_control_mapping_facts_0115':(TABLE,'INSERT OR UPDATE OR DELETE',31),
          'trg_control_mapping_truncate_0115':(TABLE,'TRUNCATE',34)}


def _create_table():
    op.create_table(TABLE,
        sa.Column('id',sa.Uuid(),primary_key=True),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('binding_id',sa.Uuid(),sa.ForeignKey('inventory_control_source_bindings.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('catalog_id',sa.Uuid(),sa.ForeignKey('inventory_control_catalog_versions.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('action',sa.String(24),nullable=False),
        sa.Column('revoked_grant_id',sa.Uuid(),sa.ForeignKey(TABLE+'.id',ondelete='RESTRICT'),nullable=True),
        sa.Column('rules_revision',sa.String(80),nullable=False),sa.Column('rules_jsonb',sa.JSON().with_variant(JSONB(),'postgresql'),nullable=False),
        sa.Column('rules_sha256',sa.String(64),nullable=False),
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
        sa.CheckConstraint("action IN ('grant','revoke')",name='ck_control_mapping_action'),
        sa.CheckConstraint('valid_from>=created_at AND (valid_to IS NULL OR valid_to>valid_from)',name='ck_control_mapping_validity'),
        sa.CheckConstraint("(action='grant' AND revoked_grant_id IS NULL) OR (action='revoke' AND revoked_grant_id IS NOT NULL AND valid_to IS NULL AND valid_from=created_at)",name='ck_control_mapping_shape'),
        sa.CheckConstraint('actor_authorization_version>0 AND length(rules_sha256)=64 AND length(payload_sha256)=64 AND length(request_sha256)=64 AND length(review_sha256)=64 AND length(evidence_sha256)=64',name='ck_control_mapping_proof'),
        sa.UniqueConstraint('actor_user_id','idempotency_key',name='uq_control_mapping_actor_key'),
        sa.UniqueConstraint('actor_user_id','request_id',name='uq_control_mapping_actor_request'),
        sa.UniqueConstraint('revoked_grant_id',name='uq_control_mapping_revocation'))
    op.create_index('uq_control_mapping_revision',TABLE,['binding_id','catalog_id','rules_revision'],unique=True,
                    postgresql_where=sa.text("action='grant'"),sqlite_where=sa.text("action='grant'"))
    op.create_index('ix_control_mapping_subject',TABLE,['binding_id','catalog_id','created_at','id'])


def _transition(upgrade):
    dialect=op.get_bind().dialect.name
    if dialect not in {'sqlite','postgresql'}:raise RuntimeError('0115 supports PostgreSQL and SQLite only')
    folder=Path(__file__).parent
    helper=runpy.run_path(str(folder/'20260927_0087_inbound_fulfillment_boundary.py'))
    helper['_begin_sqlite']()
    if dialect=='postgresql':op.execute('LOCK TABLE public.alembic_version IN ACCESS EXCLUSIVE MODE')
    if upgrade:_create_table()
    else:
        if dialect=='postgresql':op.execute(f'LOCK TABLE public.{TABLE} IN ACCESS EXCLUSIVE MODE')
        helper['_preflight'](f"EXISTS (SELECT 1 FROM {TABLE}) OR EXISTS (SELECT 1 FROM audit_events WHERE stream_key='authorization' AND (aggregate_type='inventory_control_mapping_decision' OR action LIKE 'inventory_control.mapping.%'))",'0115 downgrade blocked: mapping decisions must be retained')
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
                THEN RAISE EXCEPTION '0115 guard ownership, source or ACL drift'; END IF; END $body$""")
            for name,(table,*_) in TRIGGERS.items():op.execute(f'DROP TRIGGER {name} ON public.{table}')
            op.execute(f'DROP FUNCTION public.{FUNCTION_NAME}()')
        replace=runpy.run_path(str(folder/'20260912_0072_outbound_postings.py'))['_previous']()['_previous']()['_previous']()['_replace_function_source']
        replace(signature='public.rsc_oam_runtime_binding_ready_0044()',expected_hash=OLD_HASH if upgrade else NEW_HASH,
            replacement_hash=NEW_HASH if upgrade else OLD_HASH,replacements=((down_revision,revision),) if upgrade else ((revision,down_revision),),label='control_mapping_readiness_0115')
    elif upgrade:
        for event in ('UPDATE','DELETE'):
            op.execute(f"CREATE TRIGGER trg_control_mapping_{event.lower()}_0115 BEFORE {event} ON {TABLE} BEGIN SELECT RAISE(ABORT,'0115 mapping decisions are append-only'); END")
    if not upgrade:op.drop_table(TABLE)


def upgrade():_transition(True)
def downgrade():_transition(False)
