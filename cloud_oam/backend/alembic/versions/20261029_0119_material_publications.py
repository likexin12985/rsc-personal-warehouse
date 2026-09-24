"""Reviewed material publications, immutable version lineage and graph guards."""
import hashlib
from pathlib import Path
import runpy

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '20261029_0119'
down_revision = '20261028_0118'
branch_labels = depends_on = None
OLD_HASH = '35088a5ce50b2eaa912a873d69ae86fec16eb24e230be756069de3e1994ddb1a'
_older = runpy.run_path(str(next(Path(__file__).parent.glob('*0052*.py'))))
_ready = _older['_oam_runtime_ready_function_sql'](_older['revision']).split('AS $$',1)[1].rsplit('$$',1)[0]
NEW_HASH = hashlib.sha256(_ready.replace(_older['revision'], revision).encode()).hexdigest()
TABLES = ('material_projection_publications', 'material_projection_lines')
CORE = ('materials','material_inventory_policies','external_objects','external_object_versions')
FACT_FUNCTION = 'rsc_guard_material_publication_0119'
GRAPH_FUNCTION = 'rsc_guard_material_projection_graph_0119'
_previous = runpy.run_path(str(Path(__file__).with_name('20261027_0117_material_source_authority.py')))
_principal = _previous['_prefix'].replace('0117','0119').replace(
    "    PERFORM public.rsc_lock_formal_principal_graph_0026", "    PERFORM id FROM public.auth_sessions WHERE id=NEW.auth_session_id FOR UPDATE;\n    PERFORM public.rsc_lock_formal_principal_graph_0026")
_principal = _principal.replace('    file_evidence jsonb;', '    file_evidence jsonb;\n    receipt public.oam_material_capture_receipts%ROWTYPE;\n    coverage tstzmultirange;')
_principal = _principal.replace("    PERFORM id FROM public.auth_sessions", "    IF TG_TABLE_NAME='material_projection_lines' THEN RETURN NEW; END IF;\n    PERFORM id FROM public.auth_sessions")
FACT_BODY = _principal + """
    SELECT * INTO STRICT receipt FROM public.oam_material_capture_receipts WHERE id=NEW.receipt_id;
    PERFORM id FROM public.source_systems WHERE id=binding.source_system_id FOR SHARE;
    SELECT jsonb_build_object('file_id',f.id::text,'sha256',f.sha256,'size_bytes',f.size_bytes,
        'mime_type',f.mime_type,'storage_key',f.storage_key) INTO file_evidence FROM public.files f
        WHERE f.id=NEW.evidence_file_id AND f.sha256=NEW.evidence_sha256 AND f.status='available' AND f.size_bytes>0 FOR SHARE;
    request:=NEW.payload_jsonb->'request';
    IF receipt.binding_id<>binding.id OR receipt.capture_started_at<binding.valid_from OR binding.revoked_at IS NOT NULL
       OR NOT binding.valid_from<=NEW.created_at OR binding.valid_to<=clock_timestamp()
       OR receipt.capture_started_at+interval '45 minutes'<=clock_timestamp()
       OR NEW.created_at<receipt.created_at OR NEW.record_count<>receipt.observed_count
       OR NEW.record_count<>jsonb_array_length(request->'decisions') OR file_evidence IS NULL
       OR NOT EXISTS (SELECT 1 FROM public.source_systems s WHERE s.id=binding.source_system_id AND s.code='oam' AND s.mode='read_only' AND s.enabled)
       OR jsonb_typeof(NEW.payload_jsonb) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(NEW.payload_jsonb))<>18
       OR jsonb_typeof(request) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(request))<>8
       OR NEW.payload_jsonb->>'schema_version' IS DISTINCT FROM 'rsc.material_publication.v1'
       OR NEW.payload_jsonb->>'publication_id' IS DISTINCT FROM NEW.id::text
       OR NEW.payload_jsonb->>'receipt_id' IS DISTINCT FROM NEW.receipt_id::text
       OR NEW.payload_jsonb->>'binding_id' IS DISTINCT FROM NEW.binding_id::text
       OR (NEW.payload_jsonb->>'created_at')::timestamptz IS DISTINCT FROM NEW.created_at
       OR NEW.payload_jsonb->>'actor_user_id' IS DISTINCT FROM NEW.actor_user_id
       OR NEW.payload_jsonb->>'actor_person_id' IS DISTINCT FROM NEW.actor_person_id::text
       OR (NEW.payload_jsonb->>'actor_authorization_version')::bigint IS DISTINCT FROM NEW.actor_authorization_version
       OR NEW.payload_jsonb->>'auth_session_id' IS DISTINCT FROM NEW.auth_session_id
       OR (NEW.payload_jsonb->>'record_count')::integer IS DISTINCT FROM NEW.record_count
       OR NEW.payload_jsonb->'evidence_file' IS DISTINCT FROM file_evidence
       OR NEW.payload_jsonb->>'review_sha256' IS DISTINCT FROM NEW.review_sha256
       OR NEW.payload_jsonb->>'request_sha256' IS DISTINCT FROM NEW.request_sha256
       OR request->>'receipt_id' IS DISTINCT FROM NEW.receipt_id::text OR request->>'capture_sha256' IS DISTINCT FROM receipt.capture_sha256
       OR request->>'evidence_file_id' IS DISTINCT FROM NEW.evidence_file_id::text
       OR request->>'evidence_sha256' IS DISTINCT FROM NEW.evidence_sha256
       OR request->>'request_id' IS DISTINCT FROM NEW.request_id OR request->>'idempotency_key' IS DISTINCT FROM NEW.idempotency_key
       OR length(btrim(COALESCE(request->>'reason',''))) NOT BETWEEN 1 AND 1000
       OR NEW.request_id !~ '^[A-Za-z0-9._:-]{8,160}$' OR NEW.idempotency_key !~ '^[A-Za-z0-9._:-]{16,128}$'
       OR NEW.review_sha256 !~ '^[a-f0-9]{64}$' OR NEW.evidence_sha256 !~ '^[a-f0-9]{64}$'
       OR NEW.request_sha256 IS DISTINCT FROM encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(
           jsonb_build_object('actor_user_id',NEW.actor_user_id,'request',request)),'UTF8')),'hex')
       OR NEW.payload_sha256 IS DISTINCT FROM encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(NEW.payload_jsonb),'UTF8')),'hex')
       OR COALESCE(NEW.payload_jsonb->>'access_issued_at','') !~ '^[0-9]+$'
       OR COALESCE(NEW.payload_jsonb->>'access_expires_at','') !~ '^[0-9]+$'
       OR NOT EXISTS (SELECT 1 FROM public.auth_sessions s WHERE s.id=NEW.auth_session_id AND s.user_id=NEW.actor_user_id
          AND s.client_type='web' AND length(btrim(s.device_id))>0 AND s.ip_address ~ '^hmac:[0-9]+:[a-f0-9]{64}$'
          AND s.revoked_at IS NULL AND s.expires_at>clock_timestamp()
          AND extract(epoch FROM s.created_at)<(NEW.payload_jsonb->>'access_issued_at')::bigint+1
          AND (NEW.payload_jsonb->>'access_issued_at')::bigint<=extract(epoch FROM NEW.created_at)
          AND extract(epoch FROM clock_timestamp())<(NEW.payload_jsonb->>'access_expires_at')::bigint)
    THEN RAISE EXCEPTION '0119 publication evidence or current session mismatch' USING ERRCODE='23514'; END IF;
    -- A naturally contiguous renewal covers a capture; an explicit revocation
    -- invalidates the old capture. Transport registration alone is insufficient.
    SELECT range_agg(tstzrange(g.valid_from,g.valid_to,'[)')) INTO coverage
        FROM public.material_source_authority_decisions g JOIN public.files f ON f.id=g.evidence_file_id
        WHERE g.binding_id=binding.id AND g.action='grant'
          AND NOT EXISTS (SELECT 1 FROM public.material_source_authority_decisions r WHERE r.revoked_grant_id=g.id)
          AND f.status='available' AND jsonb_build_object('file_id',f.id::text,'sha256',f.sha256,'size_bytes',f.size_bytes,
              'mime_type',f.mime_type,'storage_key',f.storage_key)=g.payload_jsonb->'evidence_file';
    IF coverage IS NULL OR NOT coverage @> tstzrange(receipt.capture_started_at,receipt.created_at,'[]')
       OR NOT coverage @> clock_timestamp()
       OR NOT EXISTS (SELECT 1 FROM public.material_source_authority_decisions g
           WHERE g.id=(NEW.payload_jsonb->'source'->>'current_decision_id')::uuid AND g.binding_id=binding.id AND g.action='grant'
             AND g.payload_sha256=NEW.payload_jsonb->'source'->>'current_decision_sha256'
             AND g.valid_from<=NEW.created_at AND g.valid_to>clock_timestamp()
             AND NOT EXISTS (SELECT 1 FROM public.material_source_authority_decisions r WHERE r.revoked_grant_id=g.id))
       OR NEW.payload_jsonb->'source'->>'receipt_id' IS DISTINCT FROM receipt.id::text
       OR NEW.payload_jsonb->'source'->>'capture_sha256' IS DISTINCT FROM receipt.capture_sha256
       OR NEW.payload_jsonb->'source'->'subject'->>'source_system_id' IS DISTINCT FROM binding.source_system_id::text
       OR NEW.payload_jsonb->'source'->'subject'->>'binding_id' IS DISTINCT FROM binding.id::text
    THEN RAISE EXCEPTION '0119 current material source authority required' USING ERRCODE='23514'; END IF;
    expected_audit:=jsonb_build_object('publication_id',NEW.id::text,'receipt_id',NEW.receipt_id::text,
        'record_count',NEW.record_count,'payload_sha256',NEW.payload_sha256,'review_sha256',NEW.review_sha256);
    IF NOT EXISTS (SELECT 1 FROM public.audit_events a WHERE a.id=NEW.audit_event_id AND a.stream_key='authorization'
        AND a.actor_user_id=NEW.actor_user_id AND a.action='material_source.publish' AND a.aggregate_type='material_projection_publication'
        AND a.aggregate_id=NEW.id::text AND a.request_id='material-publication:'||NEW.id::text
        AND a.occurred_at=NEW.created_at AND a.before_jsonb='{}'::jsonb AND a.after_jsonb=expected_audit)
    THEN RAISE EXCEPTION '0119 publication audit mismatch' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END;
"""

GRAPH_BODY = """
DECLARE
    item public.material_projection_lines%ROWTYPE;
    publication public.material_projection_publications%ROWTYPE;
    receipt public.oam_material_capture_receipts%ROWTYPE;
    binding public.oam_material_capture_bindings%ROWTYPE;
    material public.materials%ROWTYPE;
    object public.external_objects%ROWTYPE;
    version public.external_object_versions%ROWTYPE;
    policy public.material_inventory_policies%ROWTYPE;
    parent public.material_projection_lines%ROWTYPE;
    child public.material_projection_lines%ROWTYPE;
    parent_publication public.material_projection_publications%ROWTYPE;
    parent_receipt public.oam_material_capture_receipts%ROWTYPE;
    changed uuid;
    related uuid;
    raw jsonb;
    decision jsonb;
    normalized jsonb;
    policy_value jsonb;
    version_value jsonb;
    line_value jsonb;
BEGIN
    IF TG_OP='TRUNCATE' THEN
        IF EXISTS (SELECT 1 FROM public.material_projection_lines) THEN
            RAISE EXCEPTION '0119 managed material graph must be retained' USING ERRCODE='23514';
        END IF;
        RETURN NULL;
    END IF;
    changed:=CASE WHEN TG_OP='DELETE' THEN OLD.id ELSE NEW.id END;
    IF TG_TABLE_NAME='material_inventory_policies' THEN
        related:=CASE WHEN TG_OP='DELETE' THEN OLD.material_id ELSE NEW.material_id END;
    ELSIF TG_TABLE_NAME='external_object_versions' THEN
        related:=CASE WHEN TG_OP='DELETE' THEN OLD.external_object_id ELSE NEW.external_object_id END;
    END IF;
    IF TG_TABLE_NAME='material_projection_publications' THEN
        SELECT * INTO STRICT publication FROM public.material_projection_publications WHERE id=changed;
        IF (SELECT count(*) FROM public.material_projection_lines WHERE publication_id=changed)<>publication.record_count
           OR publication.payload_jsonb->'lines' IS DISTINCT FROM (SELECT jsonb_agg(jsonb_build_array(id::text,payload_sha256) ORDER BY sequence)
                FROM public.material_projection_lines WHERE publication_id=changed)
           OR EXISTS (SELECT 1 FROM generate_series(1,publication.record_count) n WHERE NOT EXISTS (
               SELECT 1 FROM public.material_projection_lines WHERE publication_id=changed AND sequence=n))
        THEN RAISE EXCEPTION '0119 incomplete publication' USING ERRCODE='23514'; END IF;
    END IF;
    IF TG_TABLE_NAME='external_object_versions' AND TG_OP<>'DELETE' THEN
        IF NEW.payload_jsonb->>'schema_version'='rsc.reviewed_material_observation.v1'
           AND NOT EXISTS (SELECT 1 FROM public.material_projection_lines WHERE version_id=changed)
        THEN RAISE EXCEPTION '0119 unbound reviewed material version' USING ERRCODE='23514'; END IF;
    END IF;
    FOR item IN SELECT * FROM public.material_projection_lines l WHERE
        (TG_TABLE_NAME='material_projection_publications' AND l.publication_id=changed)
        OR (TG_TABLE_NAME='material_projection_lines' AND l.id=changed)
        OR (TG_TABLE_NAME='materials' AND l.material_id=changed)
        OR (TG_TABLE_NAME='external_objects' AND l.external_object_id=changed)
        OR (TG_TABLE_NAME='external_object_versions' AND l.version_id=changed)
        OR (TG_TABLE_NAME='material_inventory_policies' AND l.policy_id=changed)
        OR (TG_TABLE_NAME='external_object_versions' AND l.external_object_id=related)
        OR (TG_TABLE_NAME='material_inventory_policies' AND l.material_id=related)
    LOOP
        SELECT * INTO STRICT publication FROM public.material_projection_publications WHERE id=item.publication_id;
        SELECT * INTO STRICT receipt FROM public.oam_material_capture_receipts WHERE id=publication.receipt_id;
        SELECT * INTO STRICT binding FROM public.oam_material_capture_bindings WHERE id=receipt.binding_id;
        SELECT * INTO material FROM public.materials WHERE id=item.material_id;
        SELECT * INTO object FROM public.external_objects WHERE id=item.external_object_id;
        SELECT * INTO version FROM public.external_object_versions WHERE id=item.version_id;
        SELECT * INTO policy FROM public.material_inventory_policies WHERE id=item.policy_id;
        raw:=receipt.payload_jsonb->'capture'->'records'->(item.sequence-1)->'data';
        decision:=publication.payload_jsonb->'request'->'decisions'->(item.sequence-1);
        normalized:=jsonb_build_object('sku_code',item.sku_code,'name',raw->>'materialName','specification',COALESCE(raw->>'regularModel',''),
            'base_unit',decision->>'base_unit','status',decision->>'status','source_updated_at',NULL);
        policy_value:=jsonb_build_object('tracking_mode',decision->>'tracking_mode','quantity_scale',decision->'quantity_scale','allow_fraction',decision->'allow_fraction');
        version_value:=jsonb_build_object('schema_version','rsc.reviewed_material_observation.v1','receipt_id',receipt.id::text,
            'capture_id',receipt.capture_id::text,'raw',raw,'semantics',decision,'source_updated_at',NULL);
        line_value:=jsonb_build_object('line_id',item.id::text,'publication_id',publication.id::text,'sequence',item.sequence,
            'material_id',item.material_id::text,'external_object_id',item.external_object_id::text,'version_id',item.version_id::text,
            'policy_id',item.policy_id::text,'previous_line_id',item.previous_line_id::text,'normalized',normalized,'policy',policy_value,'version_payload',version_value);
        IF material.id IS NULL OR object.id IS NULL OR version.id IS NULL OR policy.id IS NULL
           OR jsonb_typeof(decision) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(decision))<>7
           OR decision->>'sku_code' IS DISTINCT FROM item.sku_code OR raw->>'materialCode' IS DISTINCT FROM item.sku_code
           OR decision->>'raw_sha256' IS DISTINCT FROM encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(raw),'UTF8')),'hex')
           OR decision->>'status' NOT IN ('active','inactive') OR decision->>'tracking_mode' NOT IN ('none','lot','serial','lot_and_serial')
           OR length(btrim(COALESCE(decision->>'base_unit',''))) NOT BETWEEN 1 AND 32
           OR decision->>'base_unit' IS DISTINCT FROM btrim(decision->>'base_unit') OR decision->>'base_unit' ~ '[[:cntrl:]]'
           OR jsonb_typeof(decision->'quantity_scale') IS DISTINCT FROM 'number' OR decision->>'quantity_scale' !~ '^[0-3]$'
           OR jsonb_typeof(decision->'allow_fraction') IS DISTINCT FROM 'boolean'
           OR (decision->>'allow_fraction')::boolean IS DISTINCT FROM ((decision->>'quantity_scale')::integer>0)
           OR (decision->>'tracking_mode' IN ('serial','lot_and_serial') AND (decision->>'allow_fraction')::boolean)
           OR item.payload_jsonb IS DISTINCT FROM line_value
           OR item.payload_sha256 IS DISTINCT FROM encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(line_value),'UTF8')),'hex')
           OR object.source_system_id<>binding.source_system_id OR object.entity_type<>'material' OR object.external_id<>item.sku_code
           OR object.deleted_at IS NOT NULL OR material.external_object_id<>object.id OR material.sku_code<>item.sku_code
           OR version.external_object_id<>object.id OR version.source_version IS DISTINCT FROM 'capture-v1:'||receipt.capture_id::text
           OR version.source_updated_at IS NOT NULL OR version.valid_from<>publication.created_at OR version.created_at<>publication.created_at
           OR version.payload_jsonb IS DISTINCT FROM version_value
           OR version.payload_sha256 IS DISTINCT FROM encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(version_value),'UTF8')),'hex')
           OR policy.material_id<>material.id OR policy.effective_to IS NOT NULL
           OR (SELECT count(*) FROM public.material_inventory_policies WHERE material_id=material.id)<>1
           OR EXISTS (SELECT 1 FROM public.external_object_versions v WHERE v.external_object_id=object.id
                AND NOT EXISTS (SELECT 1 FROM public.material_projection_lines l WHERE l.version_id=v.id))
           OR jsonb_build_object('tracking_mode',policy.tracking_mode,'quantity_scale',policy.quantity_scale,'allow_fraction',policy.allow_fraction) IS DISTINCT FROM policy_value
        THEN RAISE EXCEPTION '0119 material projection proof mismatch' USING ERRCODE='23514'; END IF;
        SELECT * INTO parent FROM public.material_projection_lines WHERE id=item.previous_line_id;
        IF item.previous_line_id IS NOT NULL THEN
            SELECT * INTO parent_publication FROM public.material_projection_publications WHERE id=parent.publication_id;
            SELECT * INTO parent_receipt FROM public.oam_material_capture_receipts WHERE id=parent_publication.receipt_id;
            IF parent.id IS NULL OR parent.material_id<>item.material_id OR parent.external_object_id<>item.external_object_id OR parent.policy_id<>item.policy_id
               OR parent.payload_jsonb->'normalized'->>'base_unit' IS DISTINCT FROM normalized->>'base_unit'
               OR parent_receipt.source_instance<>receipt.source_instance OR parent_receipt.capture_completed_at>=receipt.capture_started_at
               OR parent_publication.created_at>=publication.created_at
            THEN RAISE EXCEPTION '0119 invalid material version lineage' USING ERRCODE='23514'; END IF;
        ELSIF policy.effective_from<>publication.created_at OR material.created_at<>publication.created_at OR object.created_at<>publication.created_at
              OR EXISTS (SELECT 1 FROM public.material_projection_lines l WHERE l.material_id=item.material_id AND l.previous_line_id IS NULL AND l.id<>item.id) THEN
            RAISE EXCEPTION '0119 initial material identity conflict' USING ERRCODE='23514';
        END IF;
        SELECT * INTO child FROM public.material_projection_lines WHERE previous_line_id=item.id;
        IF child.id IS NULL THEN
            IF NOT version.is_current OR version.valid_to IS NOT NULL OR object.current_version_id IS DISTINCT FROM version.id
               OR material.updated_at<>publication.created_at OR object.updated_at<>publication.created_at
               OR jsonb_build_object('sku_code',material.sku_code,'name',material.name,'specification',material.specification,
                    'base_unit',material.base_unit,'status',material.status,'source_updated_at',material.source_updated_at) IS DISTINCT FROM normalized
            THEN RAISE EXCEPTION '0119 current material projection drift' USING ERRCODE='23514'; END IF;
        ELSIF version.is_current OR version.valid_to IS DISTINCT FROM (SELECT created_at FROM public.material_projection_publications WHERE id=child.publication_id) THEN
            RAISE EXCEPTION '0119 closed material version drift' USING ERRCODE='23514';
        END IF;
    END LOOP;
    RETURN NULL;
END;
"""
FUNCTIONS = {FACT_FUNCTION:(FACT_BODY,False),GRAPH_FUNCTION:(GRAPH_BODY,True)}
FUNCTION_HASHES = {name:hashlib.sha256(body.encode()).hexdigest() for name,(body,_) in FUNCTIONS.items()}
TRIGGERS = {}
TRIGGERS['trg_material_publication_commit_0119'] = (TABLES[0],FACT_FUNCTION,'INSERT',5,True)
for _table in TABLES:
    TRIGGERS['trg_'+_table+'_facts_0119'] = (_table,FACT_FUNCTION,'INSERT OR UPDATE OR DELETE',31,False)
    TRIGGERS['trg_'+_table+'_truncate_0119'] = (_table,FACT_FUNCTION,'TRUNCATE',34,False)
for _table in (*TABLES,*CORE):
    TRIGGERS['trg_'+_table+'_graph_0119'] = (_table,GRAPH_FUNCTION,'INSERT OR UPDATE OR DELETE',29,True)
for _table in CORE:
    TRIGGERS['trg_'+_table+'_truncate_0119'] = (_table,GRAPH_FUNCTION,'TRUNCATE',34,False)


def _create_tables():
    # Frozen DDL: never import the current ORM into a historical migration.
    op.create_table(TABLES[0],
        sa.Column('id',sa.Uuid(),primary_key=True),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('receipt_id',sa.Uuid(),sa.ForeignKey('oam_material_capture_receipts.id',ondelete='RESTRICT'),nullable=False,unique=True),
        sa.Column('binding_id',sa.Uuid(),sa.ForeignKey('oam_material_capture_bindings.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('actor_user_id',sa.String(36),sa.ForeignKey('users.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('actor_person_id',sa.Uuid(),sa.ForeignKey('people.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('actor_authorization_version',sa.BigInteger(),nullable=False),
        sa.Column('auth_session_id',sa.String(36),sa.ForeignKey('auth_sessions.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('evidence_file_id',sa.Uuid(),sa.ForeignKey('files.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('evidence_sha256',sa.String(64),nullable=False),sa.Column('record_count',sa.Integer(),nullable=False),
        sa.Column('idempotency_key',sa.String(128),nullable=False),sa.Column('request_id',sa.String(160),nullable=False),
        sa.Column('request_sha256',sa.String(64),nullable=False),sa.Column('review_sha256',sa.String(64),nullable=False),
        sa.Column('payload_jsonb',sa.JSON().with_variant(JSONB(),'postgresql'),nullable=False),sa.Column('payload_sha256',sa.String(64),nullable=False),
        sa.Column('audit_event_id',sa.Uuid(),sa.ForeignKey('audit_events.id',ondelete='RESTRICT'),nullable=False,unique=True),
        sa.UniqueConstraint('actor_user_id','idempotency_key',name='uq_material_publication_key'),
        sa.UniqueConstraint('actor_user_id','request_id',name='uq_material_publication_request'),
        sa.CheckConstraint('record_count BETWEEN 1 AND 1000 AND actor_authorization_version>0',name='ck_material_publication_count'),
        sa.CheckConstraint('length(payload_sha256)=64 AND length(request_sha256)=64 AND length(review_sha256)=64 AND length(evidence_sha256)=64',name='ck_material_publication_hashes'))
    op.create_table(TABLES[1],
        sa.Column('id',sa.Uuid(),primary_key=True),
        sa.Column('publication_id',sa.Uuid(),sa.ForeignKey(TABLES[0]+'.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('sequence',sa.Integer(),nullable=False),sa.Column('sku_code',sa.String(80),nullable=False),
        sa.Column('material_id',sa.Uuid(),sa.ForeignKey('materials.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('external_object_id',sa.Uuid(),sa.ForeignKey('external_objects.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('version_id',sa.Uuid(),sa.ForeignKey('external_object_versions.id',ondelete='RESTRICT'),nullable=False,unique=True),
        sa.Column('policy_id',sa.Uuid(),sa.ForeignKey('material_inventory_policies.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('previous_line_id',sa.Uuid(),sa.ForeignKey(TABLES[1]+'.id',ondelete='RESTRICT'),nullable=True,unique=True),
        sa.Column('payload_jsonb',sa.JSON().with_variant(JSONB(),'postgresql'),nullable=False),sa.Column('payload_sha256',sa.String(64),nullable=False),
        sa.UniqueConstraint('publication_id','sequence',name='uq_material_projection_sequence'),
        sa.UniqueConstraint('publication_id','sku_code',name='uq_material_projection_sku'),
        sa.CheckConstraint('sequence BETWEEN 1 AND 1000 AND length(payload_sha256)=64',name='ck_material_projection_line'))
    op.create_index('ix_material_projection_lines_material_id',TABLES[1],['material_id'])


def _transition(upgrade):
    dialect=op.get_bind().dialect.name
    if dialect not in {'sqlite','postgresql'}: raise RuntimeError('0119 supports PostgreSQL and SQLite only')
    folder=Path(__file__).parent
    helper=runpy.run_path(str(folder/'20260927_0087_inbound_fulfillment_boundary.py'))
    helper['_begin_sqlite']()
    if dialect=='postgresql': op.execute('LOCK TABLE public.alembic_version IN ACCESS EXCLUSIVE MODE')
    if upgrade: _create_tables()
    else:
        if dialect=='postgresql': op.execute('LOCK TABLE '+', '.join('public.'+t for t in (*TABLES,*CORE))+' IN ACCESS EXCLUSIVE MODE')
        helper['_preflight']("EXISTS (SELECT 1 FROM material_projection_publications) OR EXISTS (SELECT 1 FROM material_projection_lines) OR EXISTS (SELECT 1 FROM audit_events WHERE stream_key='authorization' AND (action='material_source.publish' OR aggregate_type='material_projection_publication'))",
            '0119 downgrade blocked: material publications must be retained')
    if dialect=='postgresql':
        previous=runpy.run_path(str(folder/'20261026_0116_material_capture_ingress.py'))
        if upgrade:
            for name,(body,definer) in FUNCTIONS.items():
                op.execute(f"CREATE FUNCTION public.{name}() RETURNS trigger LANGUAGE plpgsql SECURITY {'DEFINER' if definer else 'INVOKER'} SET search_path=pg_catalog, public AS $body${body}$body$")
                previous['_revoke']('FUNCTION','public.'+name+'()','pg_proc','public.'+name+'()')
            for table in TABLES:
                previous['_revoke']('TABLE','public.'+table,'pg_class','public.'+table)
                op.execute(f'GRANT SELECT ON TABLE public.{table} TO star_oam_backup')
            for name,(table,function,events,_,deferred) in TRIGGERS.items():
                prefix='CREATE CONSTRAINT TRIGGER' if deferred else 'CREATE TRIGGER'
                timing='AFTER' if deferred else 'BEFORE'
                defer=' DEFERRABLE INITIALLY DEFERRED' if deferred else ''
                each='STATEMENT' if events=='TRUNCATE' else 'ROW'
                op.execute(f'{prefix} {name} {timing} {events} ON public.{table}{defer} FOR EACH {each} EXECUTE FUNCTION public.{function}()')
                op.execute(f'ALTER TABLE public.{table} ENABLE ALWAYS TRIGGER {name}')
        else:
            for name,(body,definer) in FUNCTIONS.items():
                op.execute(f"""DO $body$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_proc p WHERE p.oid='public.{name}()'::regprocedure
                    AND p.prosecdef={'true' if definer else 'false'} AND p.proowner=(SELECT oid FROM pg_roles WHERE rolname=current_user)
                    AND encode(sha256(convert_to(p.prosrc,'UTF8')),'hex')='{FUNCTION_HASHES[name]}' AND p.proconfig=ARRAY['search_path=pg_catalog, public']
                    AND NOT EXISTS (SELECT 1 FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a WHERE a.grantee<>p.proowner))
                    THEN RAISE EXCEPTION '0119 guard source, ownership or ACL drift'; END IF; END $body$""")
            for name,(table,*_) in TRIGGERS.items(): op.execute(f'DROP TRIGGER {name} ON public.{table}')
            for name in FUNCTIONS: op.execute(f'DROP FUNCTION public.{name}()')
        replace=runpy.run_path(str(folder/'20260912_0072_outbound_postings.py'))['_previous']()['_previous']()['_previous']()['_replace_function_source']
        replace(signature='public.rsc_oam_runtime_binding_ready_0044()',expected_hash=OLD_HASH if upgrade else NEW_HASH,
            replacement_hash=NEW_HASH if upgrade else OLD_HASH,replacements=((down_revision,revision),) if upgrade else ((revision,down_revision),),label='material_publication_readiness_0119')
    elif upgrade:
        for table in TABLES:
            for event in ('UPDATE','DELETE'):
                op.execute(f"CREATE TRIGGER trg_{table}_{event.lower()}_0119 BEFORE {event} ON {table} BEGIN SELECT RAISE(ABORT,'0119 material publications are append-only'); END")
    if not upgrade:
        for table in reversed(TABLES): op.drop_table(table)


def upgrade(): _transition(True)
def downgrade(): _transition(False)
