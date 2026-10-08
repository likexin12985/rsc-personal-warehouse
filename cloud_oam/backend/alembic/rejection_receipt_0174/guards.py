"""Warehouse acceptance guards for a caller-owned native PG16 candidate.

Unactivated: freeze the observed catalog and ship a forward migration before
registering metadata, HTTP or runtime admission. No inventory posting here.
"""
from pathlib import Path
import runpy

from sqlalchemy import text


AUTHORITY = """
DECLARE source public.stock_accounts%ROWTYPE; location public.stock_locations%ROWTYPE;
    checked_now timestamptz; ancestors uuid[]; invalid_tree boolean; tree_before jsonb; tree_after jsonb;
    eligible uuid[]; required record; allows boolean; denies boolean;
BEGIN
    PERFORM public.rsc_lock_formal_principal_graph_0026(ARRAY[actor_id]::text[]);
    SELECT a.* INTO STRICT source FROM public.stock_accounts a
        JOIN public.material_request_rejection_returns p ON p.return_source_account_id=a.id WHERE p.id=return_id;
    SELECT * INTO STRICT location FROM public.stock_locations WHERE id=source.location_id;
    WITH RECURSIVE chain AS (
        SELECT id,parent_id,status,ARRAY[id] path,false cycle FROM public.organizations WHERE id=location.owner_org_id
        UNION ALL SELECT o.id,o.parent_id,o.status,c.path||o.id,o.id=ANY(c.path)
        FROM chain c JOIN public.organizations o ON o.id=c.parent_id WHERE NOT c.cycle AND cardinality(c.path)<512
    ) SELECT array_agg(id),bool_or(cycle OR status<>'active' OR cardinality(path)>=512),
        jsonb_agg(jsonb_build_array(id,parent_id,status) ORDER BY id) INTO ancestors,invalid_tree,tree_before FROM chain;
    IF ancestors IS NULL OR invalid_tree THEN RAISE EXCEPTION '0174 receiver organization invalid' USING ERRCODE='42501'; END IF;
    PERFORM 1 FROM public.organizations WHERE id=ANY(ancestors) ORDER BY id FOR SHARE;
    SELECT jsonb_agg(jsonb_build_array(id,parent_id,status) ORDER BY id) INTO tree_after FROM public.organizations WHERE id=ANY(ancestors);
    IF tree_before IS DISTINCT FROM tree_after THEN RAISE EXCEPTION '0174 receiver organization changed' USING ERRCODE='40001'; END IF;
    checked_now:=clock_timestamp();
    IF location.location_type NOT IN ('headquarters','region') OR location.status<>'active'
       OR location.owner_org_id<>source.owner_org_id OR location.custodian_person_id IS DISTINCT FROM checked_person_id
       OR NOT EXISTS(SELECT 1 FROM public.users u JOIN public.people p ON p.id=u.person_id
          JOIN public.organizations o ON o.id=p.organization_id
          WHERE u.id=actor_id AND p.id=checked_person_id AND u.authorization_version=auth_version AND u.is_active
            AND u.account_status='active' AND p.employment_status='active' AND o.status='active'
            AND EXISTS(SELECT 1 FROM public.auth_identities i WHERE i.user_id=u.id AND i.status='active'
                AND i.verified_at<=checked_now AND i.revoked_at IS NULL))
       OR (SELECT count(*) FROM public.custody_assignments c WHERE c.location_id=location.id
            AND c.valid_from<=checked_now AND (c.valid_to IS NULL OR c.valid_to>checked_now))<>1
       OR NOT EXISTS(SELECT 1 FROM public.custody_assignments c WHERE c.id=custody_id AND c.location_id=location.id
            AND c.custodian_person_id=checked_person_id AND c.valid_from<=recorded_at
            AND (c.valid_to IS NULL OR c.valid_to>checked_now)) THEN
        RAISE EXCEPTION '0174 current warehouse receiver or custody required' USING ERRCODE='42501'; END IF;
    SELECT array_agg(a.id ORDER BY a.id) INTO eligible FROM public.role_assignments a JOIN public.roles r ON r.id=a.role_id
      JOIN public.people person ON person.id=checked_person_id JOIN public.organizations own ON own.id=person.organization_id
      WHERE a.user_id=actor_id AND a.status IN ('active','scheduled') AND a.revoked_at IS NULL
        AND a.valid_from<=checked_now AND (a.valid_to IS NULL OR a.valid_to>checked_now) AND r.status='active' AND NOT r.is_external
        AND ((r.code='admin' AND a.scope_type='national' AND a.scope_id='*' AND own.org_type='headquarters')
          OR (r.code='provincial_manager' AND a.scope_type='organization'
            AND lower(replace(a.scope_id,'-','')) IN (SELECT replace(v::text,'-','') FROM unnest(ancestors) v)
            AND EXISTS(SELECT 1 FROM public.organizations scope WHERE replace(scope.id::text,'-','')=lower(replace(a.scope_id,'-',''))
                AND scope.status='active' AND scope.org_type='region_company')))
        AND NOT EXISTS(SELECT 1 FROM (VALUES ('stock_operation'),('inventory')) need(resource)
          WHERE NOT EXISTS(SELECT 1 FROM public.role_permissions rp JOIN public.permissions p ON p.id=rp.permission_id
            WHERE rp.role_id=r.id AND rp.effect='allow' AND p.resource=need.resource AND p.action='read' AND p.field_code=''));
    IF eligible IS DISTINCT FROM ARRAY[assignment_id]::uuid[] THEN
        RAISE EXCEPTION '0174 unique warehouse grant required' USING ERRCODE='42501'; END IF;
    FOR required IN SELECT * FROM (VALUES ('stock_operation','read'),('inventory','read'),
        ('stock_operation','receive_return')) need(resource,action) LOOP
        SELECT COALESCE(bool_or(rp.effect='allow' AND a.id=assignment_id),false),COALESCE(bool_or(rp.effect='deny'),false)
          INTO allows,denies FROM public.role_assignments a JOIN public.roles r ON r.id=a.role_id
          JOIN public.role_permissions rp ON rp.role_id=r.id JOIN public.permissions p ON p.id=rp.permission_id
          WHERE a.user_id=actor_id AND a.status IN ('active','scheduled') AND a.revoked_at IS NULL
            AND a.valid_from<=checked_now AND (a.valid_to IS NULL OR a.valid_to>checked_now) AND r.status='active'
            AND p.resource=required.resource AND p.action=required.action AND p.field_code=''
            AND ((a.scope_type='national' AND a.scope_id='*') OR (a.scope_type='organization'
              AND lower(replace(a.scope_id,'-','')) IN (SELECT replace(v::text,'-','') FROM unnest(ancestors) v)));
        IF NOT allows OR denies THEN RAISE EXCEPTION '0174 warehouse permission denied' USING ERRCODE='42501'; END IF;
    END LOOP;
END;
"""

INSERT = """
DECLARE parent public.material_request_rejection_returns%ROWTYPE; r public.material_requests%ROWTYPE;
    handover public.material_request_rejection_progress%ROWTYPE; source public.stock_accounts%ROWTYPE;
BEGIN
    PERFORM 1 FROM public.inventory_ledger_heads WHERE stream_key='inventory' FOR UPDATE;
    PERFORM public.rsc_lock_formal_principal_graph_0026(ARRAY[NEW.actor_user_id]::text[]);
    PERFORM public.rsc_require_request_open_0169(NEW.request_id);
    SELECT * INTO STRICT r FROM public.material_requests WHERE id=NEW.request_id;
    SELECT * INTO STRICT parent FROM public.material_request_rejection_returns WHERE id=NEW.return_id;
    PERFORM public.rsc_lock_inventory_reference_graph_0027(ARRAY[parent.in_transit_account_id,parent.return_source_account_id],CURRENT_TIMESTAMP);
    PERFORM public.rsc_assert_rejection_receipt_authority_0174(parent.id,NEW.actor_user_id,NEW.actor_person_id,
        NEW.actor_role_assignment_id,NEW.authorization_version,NEW.custody_assignment_id,NEW.recorded_at);
    PERFORM public.rsc_validate_rejection_return_0172(parent.id);
    SELECT * INTO STRICT handover FROM public.material_request_rejection_progress WHERE id=NEW.handover_id;
    PERFORM public.rsc_validate_rejection_progress_0173(handover.id);
    SELECT * INTO STRICT source FROM public.stock_accounts WHERE id=parent.return_source_account_id;
    IF parent.request_id<>r.id OR NEW.request_version<>r.version OR NEW.request_version<parent.request_version
       OR r.status NOT IN ('approved','partially_approved')
       OR EXISTS(SELECT 1 FROM public.material_request_remaining_cancellations WHERE request_id=r.id)
       OR handover.return_id<>parent.id OR handover.action<>'handover'
       OR NEW.registration_request_hash<>parent.request_hash OR NEW.handover_request_hash<>handover.request_hash
       OR NEW.target_location_id<>source.location_id
       OR NEW.received_at<handover.physical_at OR NEW.recorded_at<handover.recorded_at
       OR NEW.recorded_at<>CURRENT_TIMESTAMP OR NEW.id='00000000-0000-0000-0000-000000000000'::uuid
       OR NEW.reason<>btrim(NEW.reason) OR NEW.reason ~ '[[:cntrl:]]'
       OR NEW.idempotency_key_hash !~ '^[a-f0-9]{64}$'
       OR NEW.trace_request_id !~ '^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,159}$'
       OR (SELECT count(*) FROM public.material_request_rejection_progress WHERE return_id=parent.id)<>2
       OR EXISTS(SELECT 1 FROM public.material_request_rejection_progress WHERE return_id=parent.id AND action='cancel_registration')
       OR COALESCE((SELECT sum(accepted_qty+rejected_qty) FROM public.material_request_rejection_receipts WHERE return_id=parent.id),0)
          +NEW.accepted_qty+NEW.rejected_qty+NEW.shortage_qty>parent.quantity THEN
        RAISE EXCEPTION '0174 warehouse receipt origin, state or quantity mismatch' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END;
"""

CHILD = """
DECLARE fact public.material_request_rejection_receipts%ROWTYPE; parent public.material_request_rejection_returns%ROWTYPE;
    source public.stock_accounts%ROWTYPE;
BEGIN
    SELECT * INTO STRICT fact FROM public.material_request_rejection_receipts WHERE id=NEW.receipt_id;
    PERFORM public.rsc_require_request_open_0169(fact.request_id);
    IF fact.recorded_at<>CURRENT_TIMESTAMP OR EXISTS(SELECT 1 FROM public.audit_events
        WHERE aggregate_type='material_request_rejection_receipt' AND aggregate_id=fact.id::text) THEN
        RAISE EXCEPTION '0174 acceptance children already sealed' USING ERRCODE='23514'; END IF;
    SELECT * INTO STRICT parent FROM public.material_request_rejection_returns WHERE id=fact.return_id;
    IF TG_TABLE_NAME='material_request_rejection_receipt_serials' THEN
        IF NEW.return_id<>parent.id OR NOT EXISTS(SELECT 1 FROM public.material_request_rejection_return_serials
            WHERE return_id=parent.id AND serial_id=NEW.serial_id)
           OR EXISTS(SELECT 1 FROM public.material_request_rejection_receipt_serials prior_seen
                WHERE prior_seen.return_id=parent.id AND prior_seen.serial_id=NEW.serial_id AND prior_seen.result IN ('accepted','rejected')) THEN
            RAISE EXCEPTION '0174 foreign or already confirmed warehouse serial' USING ERRCODE='23514'; END IF;
        IF NEW.result='accepted' THEN
            PERFORM public.rsc_lock_inventory_serial_graph_0027(ARRAY[NEW.serial_id]);
            SELECT * INTO STRICT source FROM public.stock_accounts WHERE id=parent.return_source_account_id;
            IF NOT EXISTS(SELECT 1 FROM public.inventory_serials s JOIN public.materials m ON m.id=s.material_id
                JOIN public.serial_current_positions pos ON pos.serial_id=s.id
                WHERE s.id=NEW.serial_id AND s.material_id=source.material_id AND s.lot_id IS NOT DISTINCT FROM source.lot_id
                  AND s.lifecycle_status='active' AND pos.stock_account_id=parent.in_transit_account_id
                  AND m.sku_code=NEW.sku_code AND s.serial_no=NEW.serial_no AND s.qr_code=NEW.qr_code) THEN
                RAISE EXCEPTION '0174 exact accepted serial scan required' USING ERRCODE='23514'; END IF;
        END IF;
    ELSE
        PERFORM 1 FROM public.files WHERE id=NEW.evidence_file_id FOR UPDATE;
        IF EXISTS(SELECT 1 FROM public.material_request_rejection_receipt_exceptions e
            WHERE e.evidence_file_id=NEW.evidence_file_id AND e.receipt_id<>fact.id)
           OR EXISTS(SELECT 1 FROM public.receipt_exceptions WHERE evidence_file_id=NEW.evidence_file_id)
           OR EXISTS(SELECT 1 FROM public.stock_operation_receipt_exceptions WHERE evidence_file_id=NEW.evidence_file_id)
           OR NOT (__FILE_BINDING__)
           OR NOT EXISTS(SELECT 1 FROM public.files f WHERE f.id=NEW.evidence_file_id
                AND f.metadata_jsonb->>'authorization_version'=fact.authorization_version::text) THEN
            RAISE EXCEPTION '0174 completed receiver-owned exception evidence required' USING ERRCODE='23514'; END IF;
    END IF;
    RETURN NEW;
END;
"""
files = runpy.run_path(str(Path(__file__).parents[1] / 'versions/20260926_0086_receipt_evidence_files.py'))['_files'](True)
CHILD = CHILD.replace('__FILE_BINDING__', files['_postgresql_binding_file_sql'](
    file_expression='NEW.evidence_file_id', purpose='receipt_exception_evidence', user_expression='fact.actor_user_id',
    person_expression='fact.actor_person_id', bound_at_expression='fact.recorded_at', require_current_identity=True))

VALIDATE = """
DECLARE fact public.material_request_rejection_receipts%ROWTYPE; parent public.material_request_rejection_returns%ROWTYPE;
    source public.stock_accounts%ROWTYPE; policy public.material_inventory_policies%ROWTYPE; tracked boolean;
    amounts jsonb; input jsonb; expected jsonb; proof_files jsonb; proof_exceptions jsonb; accepted jsonb;
    rejected jsonb; damaged jsonb; shortage jsonb; n bigint; matches bigint; q numeric;
BEGIN
    SELECT * INTO STRICT fact FROM public.material_request_rejection_receipts WHERE id=checked_id;
    SELECT * INTO STRICT parent FROM public.material_request_rejection_returns WHERE id=fact.return_id;
    SELECT * INTO STRICT source FROM public.stock_accounts WHERE id=parent.return_source_account_id;
    SELECT * INTO STRICT policy FROM public.material_inventory_policies WHERE material_id=source.material_id
        AND effective_from<=fact.recorded_at AND (effective_to IS NULL OR effective_to>fact.recorded_at);
    tracked:=policy.tracking_mode IN ('serial','lot_and_serial');
    IF policy.tracking_mode IS DISTINCT FROM parent.evidence_jsonb->'origin'->>'tracking_mode'
       OR (fact.accepted_qty>0 AND NOT EXISTS(SELECT 1 FROM public.materials WHERE id=source.material_id AND sku_code=fact.observed_sku_code))
       OR (parent.evidence_jsonb->'origin'->>'condition'='damaged' AND fact.damaged_qty<>fact.accepted_qty) THEN
        RAISE EXCEPTION '0174 warehouse material policy or condition mismatch' USING ERRCODE='23514'; END IF;
    FOREACH q IN ARRAY ARRAY[fact.accepted_qty,fact.rejected_qty,fact.damaged_qty,fact.shortage_qty] LOOP
        IF q<>round(q,policy.quantity_scale) OR (NOT policy.allow_fraction AND q<>trunc(q)) THEN
            RAISE EXCEPTION '0174 warehouse quantity precision mismatch' USING ERRCODE='23514'; END IF;
    END LOOP;
    SELECT COALESCE(jsonb_agg(jsonb_build_object('serial_id',serial_id::text,'sku_code',sku_code,'serial_no',serial_no,'qr_code',qr_code)
            ORDER BY serial_id) FILTER(WHERE result='accepted'),'[]'::jsonb),
        COALESCE(jsonb_agg(serial_id::text ORDER BY serial_id) FILTER(WHERE result='rejected'),'[]'::jsonb),
        COALESCE(jsonb_agg(serial_id::text ORDER BY serial_id) FILTER(WHERE sn.damaged),'[]'::jsonb),
        COALESCE(jsonb_agg(serial_id::text ORDER BY serial_id) FILTER(WHERE result='shortage'),'[]'::jsonb)
        INTO accepted,rejected,damaged,shortage FROM public.material_request_rejection_receipt_serials sn WHERE receipt_id=fact.id;
    IF (tracked AND (jsonb_array_length(accepted)<>fact.accepted_qty OR jsonb_array_length(rejected)<>fact.rejected_qty
        OR jsonb_array_length(damaged)<>fact.damaged_qty OR jsonb_array_length(shortage)<>fact.shortage_qty))
       OR (NOT tracked AND accepted||rejected||damaged||shortage<>'[]'::jsonb) THEN
        RAISE EXCEPTION '0174 exact warehouse serial counts required' USING ERRCODE='23514'; END IF;
    SELECT COALESCE(jsonb_agg(jsonb_build_object('exception_type',exception_type,'description',description,
        'evidence_file_id',evidence_file_id::text) ORDER BY exception_type),'[]'::jsonb) INTO proof_exceptions
        FROM public.material_request_rejection_receipt_exceptions WHERE receipt_id=fact.id;
    IF ((fact.shortage_qty>0) IS DISTINCT FROM EXISTS(SELECT 1 FROM public.material_request_rejection_receipt_exceptions
            WHERE receipt_id=fact.id AND exception_type='shortage'))
       OR ((fact.damaged_qty>0) IS DISTINCT FROM EXISTS(SELECT 1 FROM public.material_request_rejection_receipt_exceptions
            WHERE receipt_id=fact.id AND exception_type='damaged'))
       OR ((fact.rejected_qty>0) IS DISTINCT FROM EXISTS(SELECT 1 FROM public.material_request_rejection_receipt_exceptions
            WHERE receipt_id=fact.id AND exception_type IN ('rejected','wrong_material','wrong_serial')))
       OR EXISTS(SELECT 1 FROM public.material_request_rejection_receipt_exceptions WHERE receipt_id=fact.id
            AND (description<>btrim(description) OR length(description)=0)) THEN
        RAISE EXCEPTION '0174 quantity exceptions must be explained' USING ERRCODE='23514'; END IF;
    SELECT COALESCE(jsonb_agg(jsonb_build_object('id',f.id::text,'sha256',f.sha256,'size_bytes',f.size_bytes,'mime_type',f.mime_type,
        'metadata_sha256',encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(f.metadata_jsonb),'UTF8')),'hex'))
        ORDER BY f.id),'[]'::jsonb) INTO proof_files FROM public.files f WHERE f.id IN (
            SELECT evidence_file_id FROM public.material_request_rejection_receipt_exceptions WHERE receipt_id=fact.id);
    amounts:=jsonb_build_object('accepted_qty',fact.accepted_qty::text,'rejected_qty',fact.rejected_qty::text,
        'damaged_qty',fact.damaged_qty::text,'shortage_qty',fact.shortage_qty::text,'accepted_serial_verifications',accepted,
        'rejected_serial_ids',rejected,'damaged_serial_ids',damaged,'shortage_serial_ids',shortage,'exceptions',proof_exceptions);
    input:=jsonb_build_object('expected_request_version',fact.request_version,'reason',fact.reason,
        'registration_request_hash',fact.registration_request_hash,'handover_id',fact.handover_id::text,
        'handover_request_hash',fact.handover_request_hash,'custody_assignment_id',fact.custody_assignment_id::text,
        'received_at',to_char(fact.received_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS')||
            CASE WHEN mod(extract(microseconds FROM fact.received_at)::bigint,1000000)=0 THEN ''
            ELSE '.'||to_char(fact.received_at AT TIME ZONE 'UTC','US') END||'Z',
        'amounts',amounts,'observed_sku_code',fact.observed_sku_code);
    expected:=jsonb_build_object('schema','rsc.material_request_rejection_receipt.v1','input',input,'files',proof_files,
      'origin',jsonb_build_object('return_id',parent.id::text,'registration_evidence_sha256',parent.evidence_sha256,
        'handover_id',fact.handover_id::text,'handover_request_hash',fact.handover_request_hash,'request_id',fact.request_id::text,
        'in_transit_account_id',parent.in_transit_account_id::text,'return_source_account_id',source.id::text,
        'target_location_id',source.location_id::text,'material_id',source.material_id::text,'lot_id',source.lot_id::text,
        'source_condition',source.condition_code,'policy_id',policy.id::text,'tracking_mode',policy.tracking_mode,
        'quantity_scale',policy.quantity_scale,'allow_fraction',policy.allow_fraction));
    IF fact.evidence_jsonb IS DISTINCT FROM expected OR fact.evidence_sha256 IS DISTINCT FROM
        encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(expected),'UTF8')),'hex')
       OR fact.request_hash IS DISTINCT FROM encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(
        jsonb_build_object('return_id',parent.id::text,'actor_user_id',fact.actor_user_id,
            'actor_person_id',fact.actor_person_id::text,'input',input)),'UTF8')),'hex') THEN
        RAISE EXCEPTION '0174 exact warehouse receipt evidence required' USING ERRCODE='23514'; END IF;
    expected:=jsonb_build_object('id',fact.id::text,'return_id',parent.id::text,'request_id',fact.request_id::text,
        'handover_id',fact.handover_id::text,'actor_person_id',fact.actor_person_id::text,
        'actor_role_assignment_id',fact.actor_role_assignment_id::text,'target_location_id',fact.target_location_id::text,
        'custody_assignment_id',fact.custody_assignment_id::text,'request_version',fact.request_version,
        'authorization_version',fact.authorization_version,'request_hash',fact.request_hash,
        'idempotency_key_hash',fact.idempotency_key_hash,'evidence_sha256',fact.evidence_sha256,
        'accepted_qty',fact.accepted_qty::text,'rejected_qty',fact.rejected_qty::text,
        'damaged_qty',fact.damaged_qty::text,'shortage_qty',fact.shortage_qty::text);
    SELECT count(*),count(*) FILTER(WHERE stream_key='material_request' AND action='material_request.rejection_return.receive'
        AND actor_user_id=fact.actor_user_id AND request_id=fact.trace_request_id
        AND occurred_at=fact.recorded_at AND created_at=fact.recorded_at
        AND before_jsonb=jsonb_build_object('warehouse_receipt','not_registered') AND after_jsonb=expected)
        INTO n,matches FROM public.audit_events WHERE aggregate_type='material_request_rejection_receipt' AND aggregate_id=fact.id::text;
    IF n<>1 OR matches<>1 THEN RAISE EXCEPTION '0174 exact warehouse receipt audit required' USING ERRCODE='23514'; END IF;
END;
"""

DISPATCH = """
BEGIN
    IF TG_TABLE_NAME='audit_events' THEN
        IF NEW.aggregate_type<>'material_request_rejection_receipt' OR NEW.action<>'material_request.rejection_return.receive' THEN
            RAISE EXCEPTION '0174 warehouse receipt audit target mismatch' USING ERRCODE='23514'; END IF;
        PERFORM public.rsc_validate_rejection_receipt_0174(NEW.aggregate_id::uuid);
    ELSIF TG_TABLE_NAME='material_request_rejection_receipts' THEN
        PERFORM public.rsc_validate_rejection_receipt_0174(NEW.id);
    ELSE
        PERFORM public.rsc_validate_rejection_receipt_0174(NEW.receipt_id);
    END IF;
    RETURN NULL;
END;
"""

FUNCTIONS = {
    'rsc_assert_rejection_receipt_authority_0174(uuid,text,uuid,uuid,bigint,uuid,timestamptz)': (
        'return_id uuid, actor_id text, checked_person_id uuid, assignment_id uuid, auth_version bigint, custody_id uuid, recorded_at timestamptz', 'void', AUTHORITY),
    'rsc_guard_rejection_receipt_insert_0174()': ('', 'trigger', INSERT),
    'rsc_guard_rejection_receipt_child_0174()': ('', 'trigger', CHILD),
    'rsc_validate_rejection_receipt_0174(uuid)': ('checked_id uuid', 'void', VALIDATE),
    'rsc_dispatch_rejection_receipt_0174()': ('', 'trigger', DISPATCH),
}


def install(db):
    from app.material_request_rejection_receipt_schema import receipts, serials, exceptions
    from app import material_request_rejection_progress_security as predecessor
    identity = db.execute(text("SELECT current_user,session_user,current_setting('server_version_num')::int,current_setting('transaction_isolation')")).one()
    if identity[:2] != ('star_oam_migrator', 'star_oam_migrator') or identity[2] // 10000 != 16 or identity[3] != 'read committed':
        raise ValueError('0174 direct native PG16 read-committed migrator required')
    if db.execute(text('SELECT version_num FROM public.alembic_version')).scalars().all() != ['20261222_0173']:
        raise ValueError('0174 exact predecessor required')
    predecessor.verify(db)
    path = db.scalar(text("SELECT current_setting('search_path')"))
    db.execute(text("SELECT set_config('search_path','public',true)"))
    tables = (receipts, serials, exceptions)
    for table in tables:
        table.create(db)
        db.execute(text(f'REVOKE ALL ON public.{table.name} FROM PUBLIC,star_oam_api'))
        db.execute(text(f'GRANT SELECT,INSERT ON public.{table.name} TO star_oam_api'))
        for operations, level, suffix in (('UPDATE OR DELETE', 'ROW', 'immutable'), ('TRUNCATE', 'STATEMENT', 'truncate')):
            db.execute(text(f'CREATE TRIGGER rsc_rejection_receipt_{suffix}_0174 BEFORE {operations} ON public.{table.name} '
                f'FOR EACH {level} EXECUTE FUNCTION public.rsc_guard_closure_immutable_0169()'))
    for signature, (arguments, result, body) in FUNCTIONS.items():
        db.execute(text(f'CREATE FUNCTION public.{signature.split("(")[0]}({arguments}) RETURNS {result} '
            'LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,public AS $body$' + body + '$body$'))
        db.execute(text(f'REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC,star_oam_api'))
    for table in tables:
        function = 'insert' if table is receipts else 'child'
        db.execute(text(f'CREATE TRIGGER rsc_rejection_receipt_insert_0174 BEFORE INSERT ON public.{table.name} '
            f'FOR EACH ROW EXECUTE FUNCTION public.rsc_guard_rejection_receipt_{function}_0174()'))
    for table in (*tables,):
        db.execute(text(f'CREATE CONSTRAINT TRIGGER rsc_rejection_receipt_complete_0174 AFTER INSERT ON public.{table.name} '
            'DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.rsc_dispatch_rejection_receipt_0174()'))
        db.execute(text(f'ALTER TABLE public.{table.name} ENABLE ALWAYS TRIGGER rsc_rejection_receipt_complete_0174'))
    db.execute(text("CREATE CONSTRAINT TRIGGER rsc_rejection_receipt_complete_0174 AFTER INSERT ON public.audit_events "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN (NEW.aggregate_type='material_request_rejection_receipt' "
        "OR NEW.action='material_request.rejection_return.receive') EXECUTE FUNCTION public.rsc_dispatch_rejection_receipt_0174()"))
    db.execute(text('ALTER TABLE public.audit_events ENABLE ALWAYS TRIGGER rsc_rejection_receipt_complete_0174'))
    db.execute(text("SELECT set_config('search_path',:path,true)"), {'path': path})
