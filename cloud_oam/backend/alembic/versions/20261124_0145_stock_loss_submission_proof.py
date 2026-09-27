"""Prove an atomic own-stock loss freeze; no production loss permission seed.

This admits only complete submissions under explicitly provisioned authority.
Review, disposition and recovery endpoints remain unavailable until their own
proofs are implemented. Existing return facts keep their exact checker.
"""
from pathlib import Path
import hashlib
import runpy

from alembic import op

revision = '20261124_0145'
down_revision = '20261123_0144'
branch_labels = depends_on = None
FOLDER = Path(__file__).parent
previous = runpy.run_path(str(FOLDER/'20261123_0144_stock_loss_file_bindings.py'))
OLD_READY_HASH = previous['NEW_READY_HASH']
ready = previous['ready']
NEW_READY_HASH = hashlib.sha256(ready['_ready'].replace(ready['_ready_parent'], revision).encode()).hexdigest()
returns = runpy.run_path(str(FOLDER/'20261010_0100_stock_return_orders.py'))

AUTHORITY_BODY = """
DECLARE actor public.users%ROWTYPE; person public.people%ROWTYPE;
    actor_org public.organizations%ROWTYPE; location public.stock_locations%ROWTYPE;
    checked_at timestamptz; ancestors uuid[]; latest uuid[]; invalid_tree boolean;
BEGIN
    PERFORM public.rsc_lock_formal_principal_graph_0026(ARRAY[actor_id]::text[]);
    SELECT * INTO actor FROM public.users WHERE id=actor_id;
    SELECT * INTO person FROM public.people WHERE id=actor.person_id;
    SELECT * INTO actor_org FROM public.organizations WHERE id=person.organization_id FOR SHARE;
    SELECT * INTO location FROM public.stock_locations WHERE id=location_id FOR SHARE;
    IF actor.id IS NULL OR person.id IS NULL OR actor_org.id IS NULL OR location.id IS NULL
       OR NOT actor.is_active OR actor.account_status<>'active' OR person.employment_status<>'active'
       OR actor.person_id IS DISTINCT FROM person_id OR actor.authorization_version IS DISTINCT FROM actor_version
       OR actor_org.status<>'active' OR actor_org.org_type NOT IN ('headquarters','region_company','department')
       OR location.location_type<>'personal' OR location.status<>'active'
       OR location.custodian_person_id IS DISTINCT FROM person_id
       OR NOT EXISTS(SELECT 1 FROM public.auth_identities identity WHERE identity.user_id=actor.id
            AND identity.status='active' AND identity.verified_at IS NOT NULL AND identity.revoked_at IS NULL) THEN
        RAISE EXCEPTION '0145 current loss identity or personal location invalid' USING ERRCODE='23514';
    END IF;
    WITH RECURSIVE tree AS (
        SELECT id,parent_id,status,ARRAY[id] visited,false cycle FROM public.organizations WHERE id=person.organization_id
        UNION ALL SELECT o.id,o.parent_id,o.status,t.visited||o.id,o.id=ANY(t.visited)
            FROM public.organizations o JOIN tree t ON o.id=t.parent_id WHERE NOT t.cycle
    ) SELECT array_agg(id ORDER BY id),bool_or(cycle OR status<>'active') INTO ancestors,invalid_tree FROM tree;
    IF COALESCE(invalid_tree,true) THEN
        RAISE EXCEPTION '0145 loss authority organization tree invalid' USING ERRCODE='23514'; END IF;
    PERFORM id FROM public.organizations WHERE id=ANY(ancestors) OR id=location.owner_org_id
       OR id::text IN (SELECT scope_id FROM public.role_assignments WHERE user_id=actor_id AND scope_type='organization')
       ORDER BY id FOR SHARE;
    WITH RECURSIVE tree AS (
        SELECT id,parent_id,status,ARRAY[id] visited,false cycle FROM public.organizations WHERE id=person.organization_id
        UNION ALL SELECT o.id,o.parent_id,o.status,t.visited||o.id,o.id=ANY(t.visited)
            FROM public.organizations o JOIN tree t ON o.id=t.parent_id WHERE NOT t.cycle
    ) SELECT array_agg(id ORDER BY id),bool_or(cycle OR status<>'active') INTO latest,invalid_tree FROM tree;
    IF COALESCE(invalid_tree,true) OR ancestors IS DISTINCT FROM latest
       OR NOT EXISTS(SELECT 1 FROM public.organizations WHERE id=location.owner_org_id AND status='active' AND org_type='region_company') THEN
        RAISE EXCEPTION '0145 loss authority organization changed' USING ERRCODE='23514'; END IF;
    PERFORM id FROM public.custody_assignments WHERE custody_assignments.location_id=location.id ORDER BY id FOR SHARE;
    checked_at:=clock_timestamp();
    IF (SELECT count(*) FROM public.custody_assignments a WHERE a.location_id=location.id
        AND a.custodian_person_id=person_id AND a.valid_from<=checked_at
        AND (a.valid_to IS NULL OR a.valid_to>checked_at))<>1 THEN
        RAISE EXCEPTION '0145 current loss custody required' USING ERRCODE='23514'; END IF;
    IF EXISTS(SELECT 1 FROM public.role_assignments a JOIN public.roles r ON r.id=a.role_id
        LEFT JOIN public.organizations scope ON scope.id::text=a.scope_id
        WHERE a.user_id=actor.id AND a.status IN ('active','scheduled') AND a.revoked_at IS NULL
          AND a.valid_from<=checked_at AND (a.valid_to IS NULL OR a.valid_to>checked_at) AND r.status='active'
          AND NOT CASE r.code
            WHEN 'admin' THEN NOT r.is_external AND a.scope_type='national' AND a.scope_id='*' AND actor_org.org_type='headquarters'
            WHEN 'provincial_manager' THEN NOT r.is_external AND a.scope_type='organization' AND scope.id IS NOT NULL
                AND scope.org_type='region_company' AND scope.status='active'
            WHEN 'technician' THEN NOT r.is_external AND a.scope_type='person' AND a.scope_id=person.id::text
            ELSE false END) THEN
        RAISE EXCEPTION '0145 loss authorization graph invalid' USING ERRCODE='23514'; END IF;
    IF NOT EXISTS(SELECT 1 FROM public.role_assignments a JOIN public.roles r ON r.id=a.role_id
        JOIN public.role_permissions rp ON rp.role_id=r.id JOIN public.permissions p ON p.id=rp.permission_id
        WHERE a.user_id=actor.id AND a.status IN ('active','scheduled') AND a.revoked_at IS NULL
          AND a.valid_from<=checked_at AND (a.valid_to IS NULL OR a.valid_to>checked_at) AND r.status='active'
          AND NOT r.is_external AND rp.effect='allow' AND p.resource='stock_operation' AND p.action='submit_loss' AND p.field_code=''
          AND ((a.scope_type='national' AND a.scope_id='*') OR (a.scope_type='person' AND a.scope_id=person.id::text)
            OR (a.scope_type='organization' AND a.scope_id IN (SELECT value::text FROM unnest(ancestors) value))))
       OR EXISTS(SELECT 1 FROM public.role_assignments a JOIN public.roles r ON r.id=a.role_id
        JOIN public.role_permissions rp ON rp.role_id=r.id JOIN public.permissions p ON p.id=rp.permission_id
        WHERE a.user_id=actor.id AND a.status IN ('active','scheduled') AND a.revoked_at IS NULL
          AND a.valid_from<=checked_at AND (a.valid_to IS NULL OR a.valid_to>checked_at) AND r.status='active'
          AND rp.effect='deny' AND p.resource='stock_operation' AND p.action='submit_loss' AND p.field_code=''
          AND ((a.scope_type='national' AND a.scope_id='*') OR (a.scope_type='person' AND a.scope_id=person.id::text)
            OR (a.scope_type='organization' AND a.scope_id IN (SELECT value::text FROM unnest(ancestors) value)))) THEN
        RAISE EXCEPTION '0145 current submit_loss authority required' USING ERRCODE='23514';
    END IF;
END;
"""

HOLD_BODY = """
DECLARE needed numeric; observed numeric;
BEGIN
    SELECT sum(line.quantity) INTO needed FROM public.stock_operation_lines line
        JOIN public.stock_operation_orders parent ON parent.id=line.operation_id
        WHERE parent.operation_type='loss_report' AND line.reserved_account_id=checked_account;
    IF needed IS NULL THEN RETURN; END IF;
    SELECT quantity INTO observed FROM public.stock_balances WHERE stock_account_id=checked_account;
    IF observed IS NULL OR observed<needed THEN
        RAISE EXCEPTION '0145 active loss quantities must remain frozen' USING ERRCODE='23514'; END IF;
    IF EXISTS(SELECT held.serial_id FROM public.stock_operation_serials held
        JOIN public.stock_operation_lines line ON line.id=held.line_id
        WHERE line.operation_type='loss_report' AND line.reserved_account_id=checked_account
        GROUP BY held.serial_id HAVING count(*)<>1)
       OR EXISTS(SELECT 1 FROM public.stock_operation_serials held
        JOIN public.stock_operation_lines line ON line.id=held.line_id
        LEFT JOIN public.serial_current_positions position ON position.serial_id=held.serial_id
        LEFT JOIN public.inventory_serials serial ON serial.id=held.serial_id
        WHERE line.operation_type='loss_report' AND line.reserved_account_id=checked_account
          AND (position.stock_account_id IS DISTINCT FROM checked_account OR serial.lifecycle_status IS DISTINCT FROM 'active')) THEN
        RAISE EXCEPTION '0145 exact active loss serials must remain frozen' USING ERRCODE='23514'; END IF;
END;
"""

CHECK_BODY = """
DECLARE parent public.stock_operation_orders%ROWTYPE; tx public.inventory_transactions%ROWTYPE;
    item public.stock_operation_lines%ROWTYPE; source public.stock_accounts%ROWTYPE;
    target public.stock_accounts%ROWTYPE; move public.inventory_movements%ROWTYPE;
    policy public.material_inventory_policies%ROWTYPE; file public.files%ROWTYPE; binding public.stock_loss_files%ROWTYPE;
    selected jsonb; planned jsonb; physical jsonb; serial_ids jsonb; expected_intent jsonb;
    request_lines jsonb:='[]'::jsonb; stock_moves jsonb:='[]'::jsonb; evidence jsonb:='[]'::jsonb;
    evidence_ids jsonb:='[]'::jsonb; stock_command jsonb; body jsonb;
    ordinal integer:=0; available numeric; prior_version bigint; prior_cursor bigint;
    aggregate text:='stock_operation_order'; kind text:='stock_loss_submitted';
    fact_id uuid; fact_actor text; fact_person uuid; fact_request text; authority bigint;
BEGIN
    SELECT * INTO parent FROM public.stock_operation_orders WHERE id=checked_order;
    SELECT * INTO tx FROM public.inventory_transactions WHERE id=parent.posting_transaction_id;
    IF parent.id IS NULL OR tx.id IS NULL OR parent.operation_type<>'loss_report' OR parent.status<>'submitted'
       OR parent.operation_no<>'LOSS-'||upper(substr(parent.idempotency_key_hash,1,24))
       OR parent.idempotency_key_hash !~ '^[0-9a-f]{64}$' OR parent.request_id !~ '^[A-Za-z0-9._:-]{8,160}$'
       OR parent.oam_work_order_id IS NOT NULL OR parent.target_location_id IS NOT NULL
       OR parent.transit_location_id IS NOT NULL OR parent.target_custody_assignment_id IS NOT NULL
       OR parent.authorization_version<1 OR parent.created_at>clock_timestamp()
       OR length(trim(parent.reason)) NOT BETWEEN 1 AND 500 OR trim(parent.reason)<>parent.reason
       OR parent.plan_jsonb->'intent' IS DISTINCT FROM parent.command_jsonb
       OR parent.plan_jsonb->'authorization_version' IS DISTINCT FROM to_jsonb(parent.authorization_version)
       OR parent.plan_jsonb->'location_id' IS DISTINCT FROM to_jsonb(parent.source_location_id::text)
       OR parent.plan_jsonb->'ledger_cursor' IS DISTINCT FROM to_jsonb(tx.ledger_cursor-1)
       OR jsonb_typeof(parent.plan_jsonb->'lines') IS DISTINCT FROM 'array'
       OR jsonb_typeof(parent.command_jsonb->'lines') IS DISTINCT FROM 'array'
       OR COALESCE(parent.plan_jsonb->>'source_basis_hash','') !~ '^[0-9a-f]{64}$'
       OR parent.plan_hash IS DISTINCT FROM encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(parent.plan_jsonb),'UTF8')),'hex')
       OR parent.request_hash IS DISTINCT FROM encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(parent.command_jsonb),'UTF8')),'hex')
       OR tx.status<>'posted' OR tx.actor_user_id<>parent.actor_user_id OR tx.reversed_transaction_id IS NOT NULL
       OR tx.source_document_type<>'stock_operation_loss' OR tx.source_document_id<>parent.id::text
       OR tx.idempotency_key_hash<>parent.idempotency_key_hash OR tx.created_at<parent.created_at
       OR tx.effective_at IS DISTINCT FROM parent.created_at OR tx.movement_type<>'freeze'
       OR tx.transaction_no<>'INV-LOSS-S-'||upper(substr(parent.idempotency_key_hash,1,20))
       OR tx.posting_key<>'stock-loss:submit_loss:'||parent.id::text||':'||parent.idempotency_key_hash
       OR EXISTS(SELECT 1 FROM public.inventory_transactions WHERE reversed_transaction_id=tx.id)
       OR EXISTS(SELECT 1 FROM public.stock_operation_cancellations WHERE operation_id=parent.id)
       OR (SELECT count(*) FROM (
            SELECT actor_user_id,request_id FROM public.stock_operation_orders
            UNION ALL SELECT actor_user_id,request_id FROM public.stock_operation_cancellations
            UNION ALL SELECT actor_user_id,request_id FROM public.stock_operation_outbounds
            UNION ALL SELECT actor_user_id,request_id FROM public.stock_operation_shipments
            UNION ALL SELECT actor_user_id,request_id FROM public.stock_operation_receipts
            UNION ALL SELECT actor_user_id,request_id FROM public.stock_operation_return_inbounds
            UNION ALL SELECT actor_user_id,request_id FROM public.stock_operation_command_seals
            UNION ALL SELECT actor_user_id,request_id FROM public.stock_operation_return_inbound_seals
        ) requests WHERE actor_user_id=parent.actor_user_id AND request_id=parent.request_id)<>1 THEN
        RAISE EXCEPTION '0145 exact immutable loss submission required' USING ERRCODE='23514';
    END IF;
    PERFORM public.rsc_assert_loss_submit_authority_0145(parent.actor_user_id,parent.authorization_version,parent.requester_id,parent.source_location_id);
    FOR item IN SELECT * FROM public.stock_operation_lines WHERE operation_id=parent.id ORDER BY line_no LOOP
        ordinal:=ordinal+1;
        SELECT * INTO source FROM public.stock_accounts WHERE id=item.stock_account_id FOR SHARE;
        SELECT * INTO target FROM public.stock_accounts WHERE id=item.reserved_account_id FOR SHARE;
        SELECT * INTO move FROM public.inventory_movements WHERE transaction_id=tx.id AND line_no=item.line_no;
        selected:=parent.command_jsonb->'lines'->(ordinal-1); planned:=parent.plan_jsonb->'lines'->(ordinal-1);
        IF source.id IS NULL OR target.id IS NULL OR move.id IS NULL OR item.operation_type<>'loss_report'
           OR item.source_recovery_line_id IS NOT NULL OR item.line_no<>ordinal OR item.quantity<>move.quantity
           OR item.quantity<=0 OR item.reason<>parent.reason OR item.material_id<>source.material_id
           OR item.target_condition<>source.condition_code OR source.condition_code NOT IN ('new','used','damaged')
           OR source.availability_bucket<>'available' OR target.availability_bucket<>'frozen'
           OR source.custodian_person_id IS DISTINCT FROM parent.requester_id OR source.location_id<>parent.source_location_id
           OR target.owner_org_id<>source.owner_org_id OR target.custodian_person_id IS DISTINCT FROM source.custodian_person_id
           OR target.location_id<>source.location_id OR target.material_id<>source.material_id
           OR target.lot_id IS DISTINCT FROM source.lot_id OR target.condition_code<>source.condition_code
           OR move.from_account_id IS DISTINCT FROM source.id OR move.to_account_id IS DISTINCT FROM target.id
           OR move.external_boundary_code IS NOT NULL OR item.created_at IS DISTINCT FROM parent.created_at
           OR selected->>'stock_account_id' IS DISTINCT FROM source.id::text
           OR selected->>'quantity' IS DISTINCT FROM to_char(item.quantity,'FM999999999999990.000')
           OR planned->>'selected_quantity' IS DISTINCT FROM selected->>'quantity'
           OR planned->'source'->>'stock_account_id' IS DISTINCT FROM source.id::text
           OR planned->'source'->>'owner_org_id' IS DISTINCT FROM source.owner_org_id::text
           OR planned->'source'->>'custodian_person_id' IS DISTINCT FROM parent.requester_id::text
           OR planned->'source'->>'location_id' IS DISTINCT FROM source.location_id::text
           OR planned->'source'->>'material_id' IS DISTINCT FROM source.material_id::text
           OR planned->'source'->>'condition_code' IS DISTINCT FROM source.condition_code
           OR planned->'source'->>'availability_bucket' IS DISTINCT FROM 'available'
           OR planned->'source'->'lot_id' IS DISTINCT FROM COALESCE(to_jsonb(source.lot_id::text),'null'::jsonb)
           OR NOT EXISTS(SELECT 1 FROM public.materials WHERE id=source.material_id AND status='active')
           OR NOT EXISTS(SELECT 1 FROM public.inventory_opening_establishments e
                WHERE e.owner_org_id=source.owner_org_id AND e.location_id=source.location_id AND e.established_at<=parent.created_at) THEN
            RAISE EXCEPTION '0145 exact loss source dimensions or movement required' USING ERRCODE='23514'; END IF;
        SELECT COALESCE(sum(CASE WHEN m.to_account_id=source.id THEN m.quantity ELSE -m.quantity END),0),
            count(DISTINCT p.id),COALESCE(max(p.ledger_cursor),0) INTO available,prior_version,prior_cursor
            FROM public.inventory_movements m JOIN public.inventory_transactions p ON p.id=m.transaction_id
            WHERE source.id IN (m.from_account_id,m.to_account_id) AND p.ledger_cursor<tx.ledger_cursor;
        IF item.quantity>available OR planned->'source'->>'quantity' IS DISTINCT FROM to_char(available,'FM999999999999990.000')
           OR planned->'source'->'balance_version' IS DISTINCT FROM to_jsonb(prior_version)
           OR planned->'source'->'ledger_cursor' IS DISTINCT FROM to_jsonb(prior_cursor) THEN
            RAISE EXCEPTION '0145 loss source history mismatch' USING ERRCODE='23514'; END IF;
        SELECT * INTO STRICT policy FROM public.material_inventory_policies p WHERE p.material_id=source.material_id
            AND p.effective_from<=parent.created_at AND (p.effective_to IS NULL OR p.effective_to>parent.created_at) FOR SHARE;
        IF item.quantity<>round(item.quantity,policy.quantity_scale)
           OR (NOT policy.allow_fraction AND item.quantity<>trunc(item.quantity))
           OR ((policy.tracking_mode IN ('lot','lot_and_serial')) IS DISTINCT FROM (source.lot_id IS NOT NULL))
           OR planned->'source'->>'tracking_mode' IS DISTINCT FROM policy.tracking_mode THEN
            RAISE EXCEPTION '0145 loss quantity policy mismatch' USING ERRCODE='23514'; END IF;
        IF EXISTS(SELECT 1 FROM public.inventory_freezes f JOIN public.stocktake_scopes s
            ON s.id=f.stocktake_scope_id AND s.task_id=f.task_id
            WHERE f.freeze_mode='hard' AND f.status IN ('active','released','cancelled')
              AND ((f.valid_from<=clock_timestamp() AND (f.valid_to IS NULL OR f.valid_to>clock_timestamp()))
                OR (f.valid_from<=parent.created_at AND (f.valid_to IS NULL OR f.valid_to>parent.created_at)))
              AND s.owner_org_id=source.owner_org_id AND s.location_id=source.location_id
              AND (s.scope_mode='location_all' OR (s.scope_mode='filtered'
                AND (s.material_id IS NULL OR s.material_id=source.material_id)
                AND (s.condition_code IS NULL OR s.condition_code=source.condition_code)
                AND (s.availability_bucket IS NULL OR s.availability_bucket IN ('available','frozen'))))) THEN
            RAISE EXCEPTION '0145 loss source is hard frozen' USING ERRCODE='23514'; END IF;
        SELECT COALESCE(jsonb_agg(sn.serial_id::text ORDER BY sn.serial_id::text),'[]'::jsonb),
            COALESCE(jsonb_agg(jsonb_build_object('serial_id',sn.serial_id::text,'sku_code',sku.sku_code,
                'serial_no',serial.serial_no,'qr_code',serial.qr_code) ORDER BY sn.serial_id::text),'[]'::jsonb)
            INTO serial_ids,physical FROM public.stock_operation_serials sn JOIN public.inventory_serials serial ON serial.id=sn.serial_id
            JOIN public.materials sku ON sku.id=serial.material_id WHERE sn.line_id=item.id;
        IF selected->'serial_verifications' IS DISTINCT FROM physical
           OR (policy.tracking_mode IN ('serial','lot_and_serial') AND jsonb_array_length(serial_ids)<>item.quantity)
           OR (policy.tracking_mode NOT IN ('serial','lot_and_serial') AND serial_ids<>'[]'::jsonb)
           OR EXISTS(SELECT 1 FROM public.stock_operation_serials sn JOIN public.inventory_serials serial ON serial.id=sn.serial_id
                WHERE sn.line_id=item.id AND (NOT sn.sku_verified OR NOT sn.qr_verified OR sn.created_at<>parent.created_at
                  OR serial.material_id<>source.material_id OR serial.lot_id IS DISTINCT FROM source.lot_id
                  OR (SELECT m.to_account_id FROM public.inventory_movements m
                      JOIN public.inventory_movement_serials ms ON ms.movement_id=m.id
                      JOIN public.inventory_transactions t ON t.id=m.transaction_id
                      WHERE ms.serial_id=sn.serial_id AND t.ledger_cursor<tx.ledger_cursor
                      ORDER BY t.ledger_cursor DESC,m.line_no DESC LIMIT 1) IS DISTINCT FROM source.id))
           OR (SELECT COALESCE(jsonb_agg(serial_id::text ORDER BY serial_id::text),'[]'::jsonb)
                FROM public.inventory_movement_serials WHERE movement_id=move.id) IS DISTINCT FROM serial_ids
           OR planned->'selected_serials' IS DISTINCT FROM (SELECT COALESCE(jsonb_agg(
                jsonb_build_object('serial_id',value->>'serial_id','serial_no',value->>'serial_no') ORDER BY value->>'serial_id'),'[]'::jsonb)
                FROM jsonb_array_elements(physical) value) THEN
            RAISE EXCEPTION '0145 exact source SN and physical proofs required' USING ERRCODE='23514'; END IF;
        PERFORM public.rsc_check_loss_hold_0145(target.id);
        request_lines:=request_lines||jsonb_build_array(jsonb_build_object('stock_account_id',source.id::text,
            'quantity',to_char(item.quantity,'FM999999999999990.000'),'serial_verifications',physical));
        stock_moves:=stock_moves||jsonb_build_array(jsonb_build_object('external_boundary_code',NULL,
            'from_account_id',source.id::text,'to_account_id',target.id::text,'quantity',trim_scale(item.quantity)::text,'serial_ids',serial_ids));
    END LOOP;
    FOR binding IN SELECT * FROM public.stock_loss_files WHERE operation_id=parent.id ORDER BY file_id LOOP
        SELECT * INTO file FROM public.files WHERE id=binding.file_id FOR SHARE;
        evidence_ids:=evidence_ids||jsonb_build_array(file.id::text);
        evidence:=evidence||jsonb_build_array(jsonb_build_object('file_id',file.id::text,'original_filename',file.original_filename,
            'sha256',file.sha256,'size_bytes',file.size_bytes,'mime_type',file.mime_type,'metadata_sha256',binding.metadata_sha256));
    END LOOP;
    expected_intent:=jsonb_build_object('operation_type','loss_report','operator_person_id',parent.requester_id::text,
        'reason',parent.reason,'lines',request_lines,'evidence_file_ids',evidence_ids);
    IF ordinal NOT BETWEEN 1 AND 100 OR ordinal<>jsonb_array_length(parent.plan_jsonb->'lines')
       OR ordinal<>(SELECT count(*) FROM public.inventory_movements WHERE transaction_id=tx.id)
       OR parent.command_jsonb IS DISTINCT FROM expected_intent
       OR jsonb_array_length(evidence) NOT BETWEEN 1 AND 20 OR parent.plan_jsonb->'evidence' IS DISTINCT FROM evidence
       OR EXISTS(SELECT 1 FROM (SELECT stock_account_id,lag(stock_account_id::text) OVER(ORDER BY line_no) previous
            FROM public.stock_operation_lines WHERE operation_id=parent.id) sorted WHERE previous>=stock_account_id::text) THEN
        RAISE EXCEPTION '0145 complete canonical loss batch and attachments required' USING ERRCODE='23514'; END IF;
    fact_id:=parent.id; fact_actor:=parent.actor_user_id; fact_person:=parent.requester_id;
    authority:=parent.authorization_version; fact_request:=parent.request_id;
    stock_command:=jsonb_build_object('operation','post','actor',jsonb_build_object('authorization_version',authority,
        'person_id',fact_person::text,'user_id',fact_actor),'command',jsonb_build_object('effective_at',
        to_char(tx.effective_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),'movement_type',tx.movement_type,
        'movements',stock_moves,'posting_key',tx.posting_key,'source_document_id',parent.id::text,
        'source_document_type','stock_operation_loss','transaction_no',tx.transaction_no));
    IF tx.request_hash IS DISTINCT FROM encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(stock_command),'UTF8')),'hex') THEN
        RAISE EXCEPTION '0145 loss posting hash mismatch' USING ERRCODE='23514'; END IF;
    body:=jsonb_build_object('operation_id',parent.id::text,'requester_id',parent.requester_id::text,
        'posting_transaction_id',tx.id::text,'request_hash',parent.request_hash,'plan_hash',parent.plan_hash);
"""
# Preserve the established exact inventory audit/state/outbox vocabulary.
_event_proof = returns['CHECK_BODY'].split("    IF (SELECT count(*) FROM public.audit_events event WHERE event.stream_key='material_request'", 1)[1]
_event_proof = "    IF (SELECT count(*) FROM public.audit_events event WHERE event.stream_key='inventory'" + _event_proof
_event_proof = _event_proof.replace("(CASE WHEN checked_cancellation IS NULL THEN 'submitted' ELSE 'cancelled' END)", "'submitted'")
_event_proof = _event_proof.replace('0100 original audit, state or outbox evidence missing','0145 original loss audit, state or outbox evidence missing')
CHECK_BODY += _event_proof.rsplit('END;', 1)[0] + """
    IF (SELECT count(*) FROM public.notification_events n WHERE n.event_type=kind AND n.business_type=aggregate
        AND n.business_id=parent.id::text)<>1
       OR (SELECT count(*) FROM public.notification_events n WHERE n.event_type=kind AND n.business_type=aggregate
        AND n.business_id=parent.id::text AND n.dedup_key='stock-loss-notification:'||kind||':'||parent.id::text
        AND n.payload_jsonb=body AND n.target_manifest_sha256=encode(sha256(convert_to(
            'notification-person-targets.v1'||chr(10)||parent.requester_id::text,'UTF8')),'hex')
        AND (SELECT count(*) FROM public.notification_person_targets WHERE event_id=n.id)=1
        AND EXISTS(SELECT 1 FROM public.notification_person_targets WHERE event_id=n.id AND person_id=parent.requester_id))<>1 THEN
        RAISE EXCEPTION '0145 independent intended notification required' USING ERRCODE='23514'; END IF;
END;
"""

TRANSACTION_BODY = """
DECLARE tx public.inventory_transactions%ROWTYPE; identifier uuid; account_id uuid;
BEGIN
    SELECT * INTO tx FROM public.inventory_transactions WHERE id=checked_tx;
    IF tx.source_document_type='stock_operation_loss' THEN
        SELECT id INTO identifier FROM public.stock_operation_orders WHERE posting_transaction_id=tx.id;
        IF identifier IS NULL THEN RAISE EXCEPTION '0145 detached loss transaction' USING ERRCODE='23514'; END IF;
        PERFORM public.rsc_check_loss_submission_0145(identifier);
    END IF;
    FOR account_id IN SELECT DISTINCT line.reserved_account_id FROM public.stock_operation_lines line
        JOIN public.inventory_movements move ON line.reserved_account_id IN (move.from_account_id,move.to_account_id)
        WHERE line.operation_type='loss_report' AND move.transaction_id=tx.id ORDER BY line.reserved_account_id LOOP
        PERFORM public.rsc_check_loss_hold_0145(account_id);
    END LOOP;
END;
"""

NOTIFICATION_BODY = """
DECLARE event public.notification_events%ROWTYPE;
BEGIN
    IF TG_TABLE_NAME='notification_events' THEN event:=NEW;
    ELSE SELECT * INTO event FROM public.notification_events WHERE id=NEW.event_id; END IF;
    IF event.event_type IS DISTINCT FROM 'stock_loss_submitted' THEN
        IF event.business_type='stock_operation_order' AND EXISTS(SELECT 1 FROM public.stock_operation_orders
            WHERE id::text=event.business_id AND operation_type='loss_report') THEN
            RAISE EXCEPTION '0145 loss notification event type mismatch' USING ERRCODE='23514';
        END IF;
        RETURN NULL;
    END IF;
    PERFORM 1 FROM public.inventory_ledger_heads WHERE stream_key='inventory' FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION '0145 inventory ledger required' USING ERRCODE='23514'; END IF;
    IF event.business_type<>'stock_operation_order' THEN
        RAISE EXCEPTION '0145 detached loss notification' USING ERRCODE='23514'; END IF;
    PERFORM public.rsc_check_loss_submission_0145(event.business_id::uuid);
    RETURN NULL;
END;
"""

FUNCTIONS = {
    ('rsc_assert_loss_submit_authority_0145','text, bigint, uuid, uuid'): (
        'actor_id text, actor_version bigint, person_id uuid, location_id uuid','void',AUTHORITY_BODY),
    ('rsc_check_loss_hold_0145','uuid'): ('checked_account uuid','void',HOLD_BODY),
    ('rsc_check_loss_submission_0145','uuid'): ('checked_order uuid','void',CHECK_BODY),
    ('rsc_check_loss_transaction_0145','uuid'): ('checked_tx uuid','void',TRANSACTION_BODY),
    ('rsc_guard_loss_notification_0145',''): ('','trigger',NOTIFICATION_BODY),
}
FUNCTION_HASHES = {key:hashlib.sha256(value[2].encode()).hexdigest() for key,value in FUNCTIONS.items()}
TRIGGERS = {f'trg_{table}_loss_0145':(table,'rsc_guard_loss_notification_0145')
    for table in ('notification_events','notification_person_targets')}


def _replace_once(body, old, new):
    if body.count(old)!=1: raise RuntimeError('0145 frozen source anchor drift')
    return body.replace(old,new)


def _sources():
    dispatcher=runpy.run_path(str(FOLDER/'20261102_0123_seal_audit_lock_scope.py'))['_sources']()['public.rsc_dispatch_stock_return_0100()'][1]
    fixed=_replace_once(dispatcher,"        IF tx.reversed_transaction_id IS NOT NULL THEN", """        PERFORM public.rsc_check_loss_transaction_0145(tx.id);
        IF tx.source_document_type='stock_operation_loss' THEN RETURN NULL; END IF;
        IF tx.reversed_transaction_id IS NOT NULL THEN""")
    fixed=_replace_once(fixed,'WHERE movement.transaction_id = tx.id) THEN',"WHERE movement.transaction_id = tx.id AND line.operation_type='return') THEN")
    fixed=_replace_once(fixed,'    PERFORM public.rsc_check_stock_return_0100(checked_order, checked_cancel);',"""    IF EXISTS(SELECT 1 FROM public.stock_operation_orders WHERE id=checked_order AND operation_type='loss_report') THEN
        IF checked_cancel IS NOT NULL THEN RAISE EXCEPTION '0145 loss requires its dedicated reversal' USING ERRCODE='23514'; END IF;
        PERFORM public.rsc_check_loss_submission_0145(checked_order);
        RETURN NULL;
    END IF;
    PERFORM public.rsc_check_stock_return_0100(checked_order, checked_cancel);""")
    account=runpy.run_path(str(FOLDER/'20261013_0103_stock_return_outbounds.py'))['_sources']()['public.rsc_require_opening_observation_account_0023()'][1]
    anchor=runpy.run_path(str(FOLDER/'20260928_0088_receipt_account_admission.py'))['ANCHOR']
    branch=returns['ACCOUNT_BRANCH'].replace("NEW.availability_bucket = 'return_pending'", "NEW.availability_bucket = 'frozen' AND parent.operation_type='loss_report' AND line.operation_type='loss_report'")
    branch=branch.replace("tx.movement_type = 'reserve'", "tx.movement_type = 'freeze'").replace("'stock_operation_return'", "'stock_operation_loss'")
    branch=branch.replace('AND NEW.created_at >= established.established_at', 'AND NEW.created_at=parent.created_at AND NEW.created_at >= established.established_at')
    return {'public.rsc_dispatch_stock_return_0100()':(dispatcher,fixed),
        'public.rsc_require_opening_observation_account_0023()':(account,_replace_once(account,anchor,branch+anchor))}


def _transition(up):
    helper=runpy.run_path(str(FOLDER/'20260927_0087_inbound_fulfillment_boundary.py'))
    helper['_begin_sqlite']()
    dialect=op.get_bind().dialect.name
    if dialect not in ('postgresql','sqlite'): raise RuntimeError('0145 requires PostgreSQL or SQLite')
    if dialect=='postgresql':
        op.execute("DO $$ BEGIN IF current_user<>'star_oam_migrator' OR session_user<>'star_oam_migrator' THEN RAISE EXCEPTION '0145 direct schema owner required'; END IF; END $$")
        op.execute('LOCK TABLE public.alembic_version IN ACCESS EXCLUSIVE MODE')
        op.execute('LOCK TABLE public.inventory_ledger_heads,public.inventory_transactions,public.inventory_movements,public.inventory_movement_serials,public.stock_accounts,public.stock_operation_orders,public.stock_operation_lines,public.stock_operation_serials,public.stock_operation_cancellations,public.stock_loss_files,public.audit_events,public.outbox_events,public.state_transition_events,public.notification_events,public.notification_person_targets IN SHARE ROW EXCLUSIVE MODE')
    helper['_preflight']("EXISTS(SELECT 1 FROM stock_operation_orders WHERE operation_type='loss_report')",
        '0145 loss submission history requires retention')
    if dialect=='sqlite': return  # Prior SQLite admission remains closed.
    replace=runpy.run_path(str(FOLDER/'20260909_0069_stock_reservations.py'))['_replace_function_source']
    admission=runpy.run_path(str(FOLDER/'20261121_0142_stock_operation_typed_context.py'))
    if up:
        for (name,signature),(args,result,body) in FUNCTIONS.items():
            op.execute(f'CREATE FUNCTION public.{name}({args}) RETURNS {result} LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,public AS $body${body}$body$')
            op.execute(f'REVOKE ALL ON FUNCTION public.{name}({signature}) FROM PUBLIC,star_oam_api')
        for name,(table,function) in TRIGGERS.items():
            op.execute(f'CREATE CONSTRAINT TRIGGER {name} AFTER INSERT ON public.{table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.{function}()')
            op.execute(f'ALTER TABLE public.{table} ENABLE ALWAYS TRIGGER {name}')
    else:
        for (name,signature),digest in FUNCTION_HASHES.items():
            op.execute(f"""DO $$ BEGIN IF NOT EXISTS(SELECT 1 FROM pg_proc p WHERE p.oid='public.{name}({signature})'::regprocedure
                AND p.proowner=(SELECT oid FROM pg_roles WHERE rolname=current_user) AND p.prosecdef AND p.provolatile='v'
                AND p.proconfig=ARRAY['search_path=pg_catalog, public']
                AND encode(sha256(convert_to(p.prosrc,'UTF8')),'hex')='{digest}'
                AND NOT EXISTS(SELECT 1 FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a WHERE a.grantee<>p.proowner))
                THEN RAISE EXCEPTION '0145 owned loss function drift'; END IF; END $$""")
    for signature,(old,new) in _sources().items():
        replace(signature=signature,expected_hash=hashlib.sha256((old if up else new).encode()).hexdigest(),
            replacement_hash=hashlib.sha256((new if up else old).encode()).hexdigest(),
            replacements=((old,new),) if up else ((new,old),),label='loss_submission_dispatch_0145')
    replace(signature='public.rsc_oam_runtime_binding_ready_0044()',expected_hash=OLD_READY_HASH if up else NEW_READY_HASH,
        replacement_hash=NEW_READY_HASH if up else OLD_READY_HASH,replacements=((down_revision,revision),) if up else ((revision,down_revision),),
        label='loss_submission_ready_0145')
    if up:
        # The former unconditional rejection is replaced only after the full
        # deferred order/transaction proof has been installed in this DDL tx.
        for name,table in admission['TRIGGERS'].items(): op.execute(f'DROP TRIGGER {name} ON public.{table}')
    else:
        for name,table in admission['TRIGGERS'].items():
            op.execute(f"CREATE TRIGGER {name} BEFORE INSERT ON public.{table} FOR EACH ROW EXECUTE FUNCTION public.{admission['FUNCTION']}()")
            op.execute(f'ALTER TABLE public.{table} ENABLE ALWAYS TRIGGER {name}')
        for name,(table,_) in TRIGGERS.items(): op.execute(f'DROP TRIGGER {name} ON public.{table}')
        for name,signature in reversed(FUNCTIONS): op.execute(f'DROP FUNCTION public.{name}({signature})')


def upgrade(): _transition(True)
def downgrade(): _transition(False)
