"""Exact approved loss dispositions and immutable line hold releases.

No public endpoint or role permission seed is activated by this migration.
"""
from pathlib import Path
import hashlib
import runpy

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '20261129_0150'
down_revision = '20261128_0149'
branch_labels = depends_on = None
FOLDER = Path(__file__).parent
previous = runpy.run_path(str(FOLDER/'20261128_0149_stock_return_inbound_account_admission.py'))
hq = runpy.run_path(str(FOLDER/'20261127_0148_stock_loss_headquarters_review.py'))
loss = runpy.run_path(str(FOLDER/'20261124_0145_stock_loss_submission_proof.py'))
OLD_READY_HASH = previous['NEW_READY_HASH']
ready = previous['ready']
NEW_READY_HASH = hashlib.sha256(ready['_ready'].replace(ready['_ready_parent'],revision).encode()).hexdigest()
TABLE = 'stock_loss_dispositions'
AUTHORITY_BODY = hq['AUTHORITY_BODY'].replace('0148','0150').replace('finalize_loss','dispose_loss').replace('reviewer','executor').replace('review authority','disposition authority')
LOCK_BODY = hq['LOCK_BODY'].replace('0148','0150').replace('headquarters review','loss disposition')

CHECK_BODY = """
DECLARE fact public.stock_loss_dispositions%ROWTYPE; line public.stock_operation_lines%ROWTYPE;
    parent public.stock_operation_orders%ROWTYPE; decision public.stock_loss_headquarters_decisions%ROWTYPE;
    review public.stock_loss_headquarters_reviews%ROWTYPE; source public.stock_accounts%ROWTYPE;
    target public.stock_accounts%ROWTYPE; tx public.inventory_transactions%ROWTYPE;
    move public.inventory_movements%ROWTYPE; custody public.custody_assignments%ROWTYPE;
    policy public.material_inventory_policies%ROWTYPE; location public.stock_locations%ROWTYPE;
    wanted_intent jsonb; wanted_command jsonb; wanted_plan jsonb; stock_command jsonb;
    serial_ids jsonb; hold_basis jsonb; policy_basis jsonb; prior_quantity numeric; prior_version bigint;
    kind text:='stock_loss.disposition_posted'; aggregate text:='stock_loss_disposition'; body jsonb;
    request_reference text; inventory_body jsonb;
BEGIN
    SELECT * INTO fact FROM public.stock_loss_dispositions WHERE id=checked_fact;
    SELECT * INTO line FROM public.stock_operation_lines WHERE id=fact.line_id;
    SELECT * INTO parent FROM public.stock_operation_orders WHERE id=fact.operation_id;
    SELECT * INTO decision FROM public.stock_loss_headquarters_decisions WHERE id=fact.headquarters_decision_id;
    SELECT * INTO review FROM public.stock_loss_headquarters_reviews WHERE id=decision.review_id;
    SELECT * INTO source FROM public.stock_accounts WHERE id=fact.source_account_id;
    SELECT * INTO target FROM public.stock_accounts WHERE id=fact.target_account_id;
    SELECT * INTO tx FROM public.inventory_transactions WHERE id=fact.posting_transaction_id;
    SELECT * INTO move FROM public.inventory_movements WHERE id=fact.posting_movement_id;
    SELECT * INTO custody FROM public.custody_assignments WHERE id=fact.custody_assignment_id;
    SELECT * INTO location FROM public.stock_locations WHERE id=source.location_id;
    IF fact.id IS NULL OR line.id IS NULL OR parent.id IS NULL OR decision.id IS NULL OR review.id IS NULL
       OR source.id IS NULL OR target.id IS NULL OR tx.id IS NULL OR move.id IS NULL OR custody.id IS NULL OR location.id IS NULL
       OR parent.operation_type<>'loss_report' OR parent.status<>'submitted' OR line.operation_type<>'loss_report'
       OR line.operation_id<>parent.id OR line.reserved_account_id<>source.id OR line.quantity<>fact.quantity
       OR decision.line_id<>line.id OR decision.disposition<>fact.disposition OR review.operation_id<>parent.id
       OR review.operation_type<>'loss_report' OR review.decision<>'approved' OR review.submission_plan_hash<>parent.plan_hash
       OR review.owner_org_id<>source.owner_org_id OR fact.authorization_version<1 OR fact.quantity<=0
       OR fact.disposition NOT IN ('restore_available','convert_used','convert_damaged')
       OR fact.request_id !~ '^[A-Za-z0-9._:-]{8,160}$' OR fact.idempotency_key_hash !~ '^[a-f0-9]{64}$'
       OR fact.request_hash !~ '^[a-f0-9]{64}$' OR fact.plan_hash !~ '^[a-f0-9]{64}$'
       OR fact.created_at<review.created_at OR fact.created_at>clock_timestamp() OR fact.created_at>tx.created_at
       OR source.availability_bucket<>'frozen' OR target.availability_bucket<>'available'
       OR source.id=target.id OR source.owner_org_id<>target.owner_org_id
       OR source.custodian_person_id IS DISTINCT FROM target.custodian_person_id
       OR source.custodian_person_id IS DISTINCT FROM parent.requester_id
       OR source.location_id<>target.location_id OR source.material_id<>target.material_id
       OR source.lot_id IS DISTINCT FROM target.lot_id OR source.location_id<>parent.source_location_id
       OR target.condition_code<>(CASE fact.disposition WHEN 'restore_available' THEN source.condition_code
            WHEN 'convert_used' THEN 'used' ELSE 'damaged' END)
       OR custody.location_id<>source.location_id OR custody.custodian_person_id<>source.custodian_person_id
       OR custody.valid_from>fact.created_at OR (custody.valid_to IS NOT NULL AND custody.valid_to<=fact.created_at)
       OR tx.status<>'posted' OR tx.actor_user_id<>fact.actor_user_id OR tx.reversed_transaction_id IS NOT NULL
       OR tx.source_document_type<>aggregate OR tx.source_document_id<>fact.id::text
       OR tx.effective_at<>fact.created_at OR tx.idempotency_key_hash<>fact.idempotency_key_hash
       OR tx.movement_type<>(CASE fact.disposition WHEN 'restore_available' THEN 'unfreeze' ELSE 'status_change' END)
       OR tx.transaction_no<>'INV-LOSS-D-'||upper(substr(fact.idempotency_key_hash,1,20))
       OR tx.posting_key<>'stock-loss:dispose_loss:'||fact.id::text||':'||fact.idempotency_key_hash
       OR move.transaction_id<>tx.id OR move.line_no<>1 OR move.from_account_id IS DISTINCT FROM source.id
       OR move.to_account_id IS DISTINCT FROM target.id OR move.quantity<>fact.quantity OR move.external_boundary_code IS NOT NULL
       OR (SELECT count(*) FROM public.inventory_movements WHERE transaction_id=tx.id)<>1
       OR EXISTS(SELECT 1 FROM public.inventory_transactions WHERE reversed_transaction_id=tx.id)
       OR NOT EXISTS(SELECT 1 FROM public.inventory_transactions original WHERE original.id=parent.posting_transaction_id
            AND original.status='posted' AND original.movement_type='freeze' AND original.source_document_type='stock_operation_loss'
            AND original.source_document_id=parent.id::text AND original.ledger_cursor<tx.ledger_cursor)
       OR EXISTS(SELECT 1 FROM public.inventory_transactions WHERE reversed_transaction_id=parent.posting_transaction_id) THEN
        RAISE EXCEPTION '0150 exact approved disposition graph required' USING ERRCODE='23514'; END IF;
    IF require_current THEN
        PERFORM public.rsc_assert_loss_disposition_authority_0150(fact.actor_user_id,fact.authorization_version,
            fact.executor_person_id,source.owner_org_id);
        SELECT * INTO location FROM public.stock_locations WHERE id=location.id FOR SHARE;
        PERFORM id FROM public.custody_assignments WHERE location_id=location.id ORDER BY id FOR SHARE;
        SELECT * INTO custody FROM public.custody_assignments WHERE id=fact.custody_assignment_id;
        IF location.status<>'active' OR location.location_type<>'personal' OR location.owner_org_id<>source.owner_org_id
           OR location.custodian_person_id<>source.custodian_person_id
           OR (SELECT count(*) FROM public.custody_assignments c WHERE c.location_id=location.id
                AND c.custodian_person_id=source.custodian_person_id AND c.valid_from<=clock_timestamp()
                AND (c.valid_to IS NULL OR c.valid_to>clock_timestamp()))<>1
           OR (custody.valid_to IS NOT NULL AND custody.valid_to<=clock_timestamp())
           OR NOT EXISTS(SELECT 1 FROM public.materials WHERE id=source.material_id AND status='active') THEN
            RAISE EXCEPTION '0150 current disposition custody or material changed' USING ERRCODE='23514'; END IF;
        IF NOT EXISTS(SELECT 1 FROM public.inventory_opening_establishments e
            WHERE e.location_id=source.location_id AND e.owner_org_id=source.owner_org_id AND e.established_at<=fact.created_at) THEN
            RAISE EXCEPTION '0150 established opening required' USING ERRCODE='23514'; END IF;
        IF EXISTS(SELECT 1 FROM public.inventory_freezes f JOIN public.stocktake_scopes s ON s.id=f.stocktake_scope_id AND s.task_id=f.task_id
            JOIN public.stock_accounts a ON a.id IN (source.id,target.id)
            WHERE f.status IN ('active','released','cancelled') AND f.freeze_mode='hard'
              AND ((f.valid_from<=clock_timestamp() AND (f.valid_to IS NULL OR f.valid_to>clock_timestamp()))
                OR (f.valid_from<=fact.created_at AND (f.valid_to IS NULL OR f.valid_to>fact.created_at)))
              AND s.owner_org_id=a.owner_org_id AND s.location_id=a.location_id
              AND (s.scope_mode='location_all' OR (s.scope_mode='filtered'
                AND (s.material_id IS NULL OR s.material_id=a.material_id)
                AND (s.condition_code IS NULL OR s.condition_code=a.condition_code)
                AND (s.availability_bucket IS NULL OR s.availability_bucket=a.availability_bucket)))) THEN
            RAISE EXCEPTION '0150 disposition source or target is hard frozen' USING ERRCODE='23514'; END IF;
    END IF;
    SELECT * INTO policy FROM public.material_inventory_policies WHERE material_id=source.material_id
        AND effective_from<=fact.created_at AND (effective_to IS NULL OR effective_to>fact.created_at);
    IF policy.id IS NULL OR (SELECT count(*) FROM public.material_inventory_policies WHERE material_id=source.material_id
        AND effective_from<=fact.created_at AND (effective_to IS NULL OR effective_to>fact.created_at))<>1 THEN
        RAISE EXCEPTION '0150 exact historical disposition policy required' USING ERRCODE='23514'; END IF;
    SELECT COALESCE(jsonb_agg(serial_id::text ORDER BY serial_id::text),'[]'::jsonb) INTO serial_ids
        FROM public.stock_operation_serials WHERE line_id=line.id;
    IF (policy.tracking_mode IN ('serial','lot_and_serial') AND jsonb_array_length(serial_ids)<>fact.quantity)
       OR (policy.tracking_mode NOT IN ('serial','lot_and_serial') AND serial_ids<>'[]'::jsonb)
       OR (SELECT COALESCE(jsonb_agg(serial_id::text ORDER BY serial_id::text),'[]'::jsonb)
            FROM public.inventory_movement_serials WHERE movement_id=move.id) IS DISTINCT FROM serial_ids
       OR EXISTS(SELECT 1 FROM public.stock_operation_serials sn JOIN public.inventory_serials serial ON serial.id=sn.serial_id
            WHERE sn.line_id=line.id AND (NOT sn.sku_verified OR NOT sn.qr_verified
              OR serial.material_id<>source.material_id OR serial.lot_id IS DISTINCT FROM source.lot_id
              OR (SELECT m.to_account_id FROM public.inventory_movements m
                  JOIN public.inventory_movement_serials ms ON ms.movement_id=m.id
                  JOIN public.inventory_transactions t ON t.id=m.transaction_id
                  WHERE ms.serial_id=sn.serial_id AND t.ledger_cursor<tx.ledger_cursor
                  ORDER BY t.ledger_cursor DESC,m.line_no DESC LIMIT 1) IS DISTINCT FROM source.id)) THEN
        RAISE EXCEPTION '0150 exact original held serials required' USING ERRCODE='23514'; END IF;
    SELECT COALESCE(sum(CASE WHEN m.to_account_id=source.id THEN m.quantity ELSE -m.quantity END),0),count(DISTINCT m.transaction_id)
        INTO prior_quantity,prior_version FROM public.inventory_movements m JOIN public.inventory_transactions t ON t.id=m.transaction_id
        WHERE t.ledger_cursor<tx.ledger_cursor AND source.id IN (m.from_account_id,m.to_account_id);
    SELECT COALESCE(jsonb_agg(jsonb_build_object('line_id',l.id::text,'operation_id',l.operation_id::text,
        'quantity',to_char(l.quantity,'FM999999999999990.000'),
        'serial_ids',(SELECT COALESCE(jsonb_agg(s.serial_id::text ORDER BY s.serial_id::text),'[]'::jsonb)
            FROM public.stock_operation_serials s WHERE s.line_id=l.id),
        'disposition_id',(SELECT d.id::text FROM public.stock_loss_dispositions d
            JOIN public.inventory_transactions dt ON dt.id=d.posting_transaction_id WHERE d.line_id=l.id AND dt.ledger_cursor<tx.ledger_cursor))
        ORDER BY l.id),'[]'::jsonb) INTO hold_basis FROM public.stock_operation_lines l
        JOIN public.stock_operation_orders p ON p.id=l.operation_id JOIN public.inventory_transactions t ON t.id=p.posting_transaction_id
        WHERE l.operation_type='loss_report' AND l.reserved_account_id=source.id AND t.ledger_cursor<tx.ledger_cursor;
    IF prior_quantity<(SELECT COALESCE(sum((value->>'quantity')::numeric),0) FROM jsonb_array_elements(hold_basis)
            WHERE value->'disposition_id'='null'::jsonb)
       OR EXISTS(SELECT 1 FROM public.stock_operation_serials sn JOIN public.stock_operation_lines l ON l.id=sn.line_id
            JOIN public.stock_operation_orders p ON p.id=l.operation_id JOIN public.inventory_transactions freeze_tx ON freeze_tx.id=p.posting_transaction_id
            WHERE l.operation_type='loss_report' AND l.reserved_account_id=source.id AND freeze_tx.ledger_cursor<tx.ledger_cursor
              AND NOT EXISTS(SELECT 1 FROM public.stock_loss_dispositions d JOIN public.inventory_transactions dt ON dt.id=d.posting_transaction_id
                WHERE d.line_id=l.id AND dt.ledger_cursor<tx.ledger_cursor)
              AND (SELECT m.to_account_id FROM public.inventory_movements m JOIN public.inventory_movement_serials ms ON ms.movement_id=m.id
                JOIN public.inventory_transactions t ON t.id=m.transaction_id WHERE ms.serial_id=sn.serial_id AND t.ledger_cursor<tx.ledger_cursor
                ORDER BY t.ledger_cursor DESC,m.line_no DESC LIMIT 1) IS DISTINCT FROM source.id) THEN
        RAISE EXCEPTION '0150 historical original holds not preserved' USING ERRCODE='23514'; END IF;
    policy_basis:=fact.plan_jsonb->'policy_fingerprint';
    IF jsonb_typeof(policy_basis) IS DISTINCT FROM 'array' OR jsonb_array_length(policy_basis)<>1
       OR jsonb_typeof(policy_basis->0) IS DISTINCT FROM 'array' OR jsonb_array_length(policy_basis->0)<>7
       OR policy_basis->0->0 IS DISTINCT FROM to_jsonb(source.material_id::text)
       OR policy_basis->0->1 IS DISTINCT FROM to_jsonb(policy.id::text)
       OR policy_basis->0->2 IS DISTINCT FROM to_jsonb(policy.tracking_mode)
       OR policy_basis->0->3 IS DISTINCT FROM to_jsonb(policy.quantity_scale)
       OR policy_basis->0->4 IS DISTINCT FROM to_jsonb(policy.allow_fraction)
       OR (policy_basis->0->>5)::timestamptz IS DISTINCT FROM policy.effective_from
       OR (policy_basis->0->6<>'null'::jsonb AND (policy_basis->0->>6)::timestamptz IS DISTINCT FROM policy.effective_to) THEN
        RAISE EXCEPTION '0150 disposition policy snapshot changed' USING ERRCODE='23514'; END IF;
    wanted_intent:=jsonb_build_object('headquarters_decision_id',decision.id::text,
        'expected_headquarters_review_hash',review.request_hash,'expected_submission_plan_hash',parent.plan_hash);
    wanted_command:=jsonb_build_object('intent',wanted_intent,'request_id',fact.request_id,'expected_plan_hash',fact.plan_hash);
    wanted_plan:=jsonb_build_object('schema_version','1.0','intent',wanted_intent,'operation_id',parent.id::text,'line_id',line.id::text,
        'executor_person_id',fact.executor_person_id::text,'authorization_version',fact.authorization_version,
        'disposition',fact.disposition,'reason',decision.reason,'source_account_id',source.id::text,'target_account_id',target.id::text,
        'owner_org_id',source.owner_org_id::text,'custodian_person_id',source.custodian_person_id::text,'location_id',source.location_id::text,
        'material_id',source.material_id::text,'lot_id',source.lot_id::text,'source_condition',source.condition_code,'target_condition',target.condition_code,
        'custody_assignment_id',custody.id::text,'quantity',to_char(fact.quantity,'FM999999999999990.000'),'serial_ids',serial_ids,
        'movement_type',tx.movement_type,'ledger_cursor',tx.ledger_cursor-1,'source_balance_version',prior_version,
        'source_balance_quantity',to_char(prior_quantity,'FM999999999999990.000'),'policy_fingerprint',policy_basis,'holds',hold_basis);
    IF fact.command_jsonb IS DISTINCT FROM wanted_command OR fact.plan_jsonb IS DISTINCT FROM wanted_plan
       OR fact.request_hash IS DISTINCT FROM encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(wanted_command),'UTF8')),'hex')
       OR fact.plan_hash IS DISTINCT FROM encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(wanted_plan),'UTF8')),'hex') THEN
        RAISE EXCEPTION '0150 canonical disposition request or plan mismatch' USING ERRCODE='23514'; END IF;
    stock_command:=jsonb_build_object('operation','post','actor',jsonb_build_object('authorization_version',fact.authorization_version,
        'person_id',fact.executor_person_id::text,'user_id',fact.actor_user_id),'command',jsonb_build_object('effective_at',
        to_char(tx.effective_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),'movement_type',tx.movement_type,
        'movements',jsonb_build_array(jsonb_build_object('external_boundary_code',NULL,'from_account_id',source.id::text,
            'to_account_id',target.id::text,'quantity',trim_scale(fact.quantity)::text,'serial_ids',serial_ids)),
        'posting_key',tx.posting_key,'source_document_id',fact.id::text,'source_document_type',aggregate,'transaction_no',tx.transaction_no));
    IF tx.request_hash IS DISTINCT FROM encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(stock_command),'UTF8')),'hex') THEN
        RAISE EXCEPTION '0150 disposition posting hash mismatch' USING ERRCODE='23514'; END IF;
    body:=jsonb_build_object('disposition_id',fact.id::text,'operation_id',parent.id::text,'line_id',line.id::text,
        'headquarters_decision_id',decision.id::text,'disposition',fact.disposition,'executor_person_id',fact.executor_person_id::text,
        'authorization_version',fact.authorization_version,'posting_transaction_id',tx.id::text,'posting_movement_id',move.id::text,
        'quantity',to_char(fact.quantity,'FM999999999999990.000'),'source_account_id',source.id::text,'target_account_id',target.id::text,
        'request_id',fact.request_id,'request_hash',fact.request_hash,'plan_hash',fact.plan_hash,'status','posted');
    IF (SELECT count(*) FROM public.audit_events WHERE stream_key='inventory' AND aggregate_type=aggregate AND aggregate_id=fact.id::text)<>1
       OR NOT EXISTS(SELECT 1 FROM public.audit_events e WHERE e.stream_key='inventory' AND e.aggregate_type=aggregate
            AND e.aggregate_id=fact.id::text AND e.actor_user_id=fact.actor_user_id AND e.action=kind AND e.request_id=fact.request_id
            AND e.before_jsonb='{}'::jsonb AND e.after_jsonb=body AND e.occurred_at=fact.created_at AND e.created_at=fact.created_at)
       OR (SELECT count(*) FROM public.state_transition_events WHERE aggregate_type=aggregate AND aggregate_id=fact.id::text)<>1
       OR NOT EXISTS(SELECT 1 FROM public.state_transition_events e WHERE e.aggregate_type=aggregate AND e.aggregate_id=fact.id::text
            AND e.from_status='pending' AND e.to_status='posted' AND e.actor_id=fact.actor_user_id AND e.reason=kind
            AND e.idempotency_key=kind||':'||fact.id::text AND e.metadata_jsonb=body AND e.occurred_at=fact.created_at AND e.created_at=fact.created_at)
       OR (SELECT count(*) FROM public.outbox_events WHERE aggregate_type=aggregate AND aggregate_id=fact.id::text)<>1
       OR NOT EXISTS(SELECT 1 FROM public.outbox_events e WHERE e.aggregate_type=aggregate AND e.aggregate_id=fact.id::text
            AND e.event_type=kind AND e.idempotency_key=kind||':'||fact.id::text AND e.payload_jsonb=body AND e.created_at=fact.created_at) THEN
        RAISE EXCEPTION '0150 complete disposition audit state outbox required' USING ERRCODE='23514'; END IF;
    IF (SELECT count(*) FROM public.notification_events WHERE business_type=aggregate AND business_id=fact.id::text)<>1
       OR NOT EXISTS(SELECT 1 FROM public.notification_events n WHERE n.business_type=aggregate AND n.business_id=fact.id::text
            AND n.event_type=kind AND n.dedup_key=kind||':'||fact.id::text AND n.payload_jsonb=body
            AND n.target_manifest_sha256=encode(sha256(convert_to('notification-person-targets.v1'||chr(10)||parent.requester_id::text,'UTF8')),'hex')
            AND (SELECT count(*) FROM public.notification_person_targets WHERE event_id=n.id)=1
            AND EXISTS(SELECT 1 FROM public.notification_person_targets WHERE event_id=n.id AND person_id=parent.requester_id)) THEN
        RAISE EXCEPTION '0150 independent disposition notification required' USING ERRCODE='23514'; END IF;
    request_reference:='inventory-request-'||encode(sha256(convert_to('cloud_oam.inventory.request.v1','UTF8')||decode('00','hex')||convert_to(fact.request_id,'UTF8')),'hex');
    inventory_body:=jsonb_build_object('ledger_cursor',tx.ledger_cursor,'movement_count',1,'movement_type',tx.movement_type,
        'posting_key',tx.posting_key,'reversed_transaction_id',NULL,'status','posted');
    IF (SELECT count(*) FROM public.audit_events WHERE stream_key='inventory' AND aggregate_type='inventory_transaction' AND aggregate_id=tx.id::text)<>1
       OR NOT EXISTS(SELECT 1 FROM public.audit_events e WHERE e.stream_key='inventory' AND e.aggregate_type='inventory_transaction'
            AND e.aggregate_id=tx.id::text AND e.actor_user_id=fact.actor_user_id AND e.action='inventory.transaction.posted'
            AND e.request_id=request_reference AND (e.before_jsonb IS NULL OR e.before_jsonb='null'::jsonb) AND e.after_jsonb=inventory_body)
       OR (SELECT count(*) FROM public.state_transition_events WHERE aggregate_type='inventory_transaction' AND aggregate_id=tx.id::text)<>1
       OR NOT EXISTS(SELECT 1 FROM public.state_transition_events e WHERE e.aggregate_type='inventory_transaction' AND e.aggregate_id=tx.id::text
            AND e.from_status IS NULL AND e.to_status='posted' AND e.actor_id=fact.actor_user_id AND e.reason='inventory_transaction_posted'
            AND e.idempotency_key='inventory-state-'||encode(sha256(convert_to('cloud_oam.inventory.state.v1','UTF8')||decode('00','hex')||convert_to(tx.id::text,'UTF8')||decode('00','hex')||convert_to('posted','UTF8')),'hex')
            AND e.metadata_jsonb=jsonb_build_object('ledger_cursor',tx.ledger_cursor,'movement_type',tx.movement_type,'request_reference',request_reference))
       OR (SELECT count(*) FROM public.outbox_events WHERE aggregate_type='inventory_transaction' AND aggregate_id=tx.id::text)<>1
       OR NOT EXISTS(SELECT 1 FROM public.outbox_events e WHERE e.aggregate_type='inventory_transaction' AND e.aggregate_id=tx.id::text
            AND e.event_type='inventory.transaction.posted'
            AND e.idempotency_key='inventory-outbox-'||encode(sha256(convert_to('cloud_oam.inventory.outbox.v1','UTF8')||decode('00','hex')||convert_to(tx.id::text,'UTF8')||decode('00','hex')||convert_to('posted','UTF8')),'hex')
            AND e.payload_jsonb=jsonb_build_object('transaction_id',tx.id::text,
                'transaction_no',tx.transaction_no,'movement_type',tx.movement_type,'ledger_cursor',tx.ledger_cursor,'reversed_transaction_id',NULL)) THEN
        RAISE EXCEPTION '0150 complete inventory posting evidence required' USING ERRCODE='23514'; END IF;
END;
"""

HOLD_BODY = """
DECLARE needed numeric; observed numeric; identifier uuid;
BEGIN
    -- Prove every release before subtracting any of it from the original line.
    -- Historical authority is deliberately not re-required by this branch.
    FOR identifier IN SELECT id FROM public.stock_loss_dispositions WHERE source_account_id=checked_account ORDER BY id LOOP
        PERFORM public.rsc_check_loss_disposition_0150(identifier,false);
    END LOOP;
    SELECT sum(line.quantity) INTO needed FROM public.stock_operation_lines line
        JOIN public.stock_operation_orders parent ON parent.id=line.operation_id
        WHERE parent.operation_type='loss_report' AND line.reserved_account_id=checked_account
          AND NOT EXISTS(SELECT 1 FROM public.stock_loss_dispositions d WHERE d.line_id=line.id);
    IF needed IS NULL THEN RETURN; END IF;
    SELECT quantity INTO observed FROM public.stock_balances WHERE stock_account_id=checked_account;
    IF observed IS NULL OR observed<needed THEN
        RAISE EXCEPTION '0150 unreleased loss quantities must remain frozen' USING ERRCODE='23514'; END IF;
    IF EXISTS(SELECT held.serial_id FROM public.stock_operation_serials held
        JOIN public.stock_operation_lines line ON line.id=held.line_id
        WHERE line.operation_type='loss_report' AND line.reserved_account_id=checked_account
          AND NOT EXISTS(SELECT 1 FROM public.stock_loss_dispositions d WHERE d.line_id=line.id)
        GROUP BY held.serial_id HAVING count(*)<>1)
       OR EXISTS(SELECT 1 FROM public.stock_operation_serials held
        JOIN public.stock_operation_lines line ON line.id=held.line_id
        LEFT JOIN public.serial_current_positions position ON position.serial_id=held.serial_id
        LEFT JOIN public.inventory_serials serial ON serial.id=held.serial_id
        WHERE line.operation_type='loss_report' AND line.reserved_account_id=checked_account
          AND NOT EXISTS(SELECT 1 FROM public.stock_loss_dispositions d WHERE d.line_id=line.id)
          AND (position.stock_account_id IS DISTINCT FROM checked_account OR serial.lifecycle_status IS DISTINCT FROM 'active')) THEN
        RAISE EXCEPTION '0150 exact unreleased loss serials must remain frozen' USING ERRCODE='23514'; END IF;
END;
"""

TRANSACTION_BRANCH = """    IF tx.source_document_type='stock_loss_disposition' OR EXISTS(
        SELECT 1 FROM public.stock_loss_dispositions WHERE posting_transaction_id=tx.id) THEN
        SELECT id INTO identifier FROM public.stock_loss_dispositions WHERE posting_transaction_id=tx.id;
        IF identifier IS NULL THEN RAISE EXCEPTION '0150 detached disposition transaction' USING ERRCODE='23514'; END IF;
        PERFORM public.rsc_check_loss_disposition_0150(identifier,true);
    END IF;
    IF tx.reversed_transaction_id IS NOT NULL AND EXISTS(SELECT 1 FROM public.stock_loss_dispositions
        WHERE posting_transaction_id=tx.reversed_transaction_id) THEN
        RAISE EXCEPTION '0150 disposition requires its dedicated reversal' USING ERRCODE='23514'; END IF;
"""

GUARD_BODY = """
DECLARE identifier uuid; event public.notification_events%ROWTYPE; transaction_id uuid;
BEGIN
    IF TG_TABLE_NAME='stock_loss_dispositions' THEN identifier:=NEW.id;
    ELSIF TG_TABLE_NAME IN ('notification_events','notification_person_targets') THEN
        IF TG_TABLE_NAME='notification_events' THEN event:=NEW;
        ELSE SELECT * INTO event FROM public.notification_events WHERE id=NEW.event_id; END IF;
        IF event.business_type<>'stock_loss_disposition' AND event.event_type<>'stock_loss.disposition_posted' THEN RETURN NULL; END IF;
        IF event.business_type<>'stock_loss_disposition' OR event.event_type<>'stock_loss.disposition_posted' THEN
            RAISE EXCEPTION '0150 detached disposition notification' USING ERRCODE='23514'; END IF;
        identifier:=event.business_id::uuid;
    ELSE
        IF NEW.aggregate_type='stock_loss_disposition' THEN identifier:=NEW.aggregate_id::uuid;
        ELSIF NEW.aggregate_type='inventory_transaction' THEN
            SELECT id INTO identifier FROM public.stock_loss_dispositions WHERE posting_transaction_id::text=NEW.aggregate_id;
            IF identifier IS NULL THEN RETURN NULL; END IF;
        ELSE RETURN NULL;
        END IF;
    END IF;
    IF current_setting('transaction_isolation')<>'read committed' THEN
        RAISE EXCEPTION '0150 disposition proof requires read committed' USING ERRCODE='23514'; END IF;
    PERFORM 1 FROM public.inventory_ledger_heads WHERE stream_key='inventory' FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION '0150 inventory ledger required' USING ERRCODE='23514'; END IF;
    PERFORM public.rsc_check_loss_disposition_0150(identifier,true);
    RETURN NULL;
END;
"""

ACCOUNT_BRANCH = """    IF EXISTS (
        SELECT 1 FROM public.stock_loss_dispositions fact
        JOIN public.stock_operation_lines line ON line.id=fact.line_id AND line.operation_id=fact.operation_id
        JOIN public.stock_loss_headquarters_decisions decision ON decision.id=fact.headquarters_decision_id AND decision.line_id=line.id
        JOIN public.inventory_transactions tx ON tx.id=fact.posting_transaction_id
        JOIN public.inventory_movements move ON move.id=fact.posting_movement_id AND move.transaction_id=tx.id
        JOIN public.stock_accounts source ON source.id=fact.source_account_id
        JOIN public.stock_locations location ON location.id=NEW.location_id
        JOIN public.custody_assignments custody ON custody.id=fact.custody_assignment_id
        JOIN public.inventory_opening_establishments established ON established.owner_org_id=NEW.owner_org_id
            AND established.location_id=NEW.location_id
        JOIN public.stock_balances balance ON balance.stock_account_id=NEW.id
        WHERE fact.target_account_id=NEW.id AND fact.quantity=line.quantity AND fact.disposition=decision.disposition
          AND fact.disposition IN ('restore_available','convert_used','convert_damaged')
          AND source.id=line.reserved_account_id AND source.availability_bucket='frozen' AND NEW.availability_bucket='available'
          AND NEW.owner_org_id=source.owner_org_id AND NEW.custodian_person_id=source.custodian_person_id
          AND NEW.location_id=source.location_id AND NEW.material_id=source.material_id AND NEW.lot_id IS NOT DISTINCT FROM source.lot_id
          AND NEW.condition_code=CASE fact.disposition WHEN 'restore_available' THEN source.condition_code WHEN 'convert_used' THEN 'used' ELSE 'damaged' END
          AND location.location_type='personal' AND location.status='active' AND location.owner_org_id=NEW.owner_org_id
          AND location.custodian_person_id=NEW.custodian_person_id AND custody.location_id=location.id
          AND custody.custodian_person_id=NEW.custodian_person_id AND custody.valid_from<=fact.created_at
          AND (custody.valid_to IS NULL OR custody.valid_to>fact.created_at)
          AND tx.status='posted' AND tx.source_document_type='stock_loss_disposition' AND tx.source_document_id=fact.id::text
          AND tx.actor_user_id=fact.actor_user_id AND tx.reversed_transaction_id IS NULL
          AND move.from_account_id=source.id AND move.to_account_id=NEW.id AND move.quantity=fact.quantity AND move.line_no=1
          AND NEW.created_at=fact.created_at AND NEW.updated_at=NEW.created_at AND tx.effective_at=NEW.created_at
          AND established.established_at<=NEW.created_at AND fact.created_at<=tx.created_at
          AND balance.version=1 AND balance.ledger_cursor=tx.ledger_cursor AND balance.quantity=move.quantity
          AND NOT EXISTS(SELECT 1 FROM public.inventory_movements other JOIN public.inventory_transactions prior ON prior.id=other.transaction_id
            WHERE NEW.id IN (other.from_account_id,other.to_account_id) AND (prior.ledger_cursor<tx.ledger_cursor OR other.from_account_id=NEW.id))
    ) THEN
        PERFORM public.rsc_check_loss_disposition_0150((SELECT d.id FROM public.stock_loss_dispositions d JOIN public.inventory_transactions t ON t.id=d.posting_transaction_id WHERE d.target_account_id=NEW.id ORDER BY t.ledger_cursor LIMIT 1),false);
        RETURN NEW;
    END IF;

"""

FUNCTIONS = {
    ('rsc_assert_loss_disposition_authority_0150','text, bigint, uuid, uuid'):
        ('actor_id text, actor_version bigint, person_id uuid, owner_id uuid','void',AUTHORITY_BODY),
    ('rsc_lock_loss_disposition_0150',''):('','trigger',LOCK_BODY),
    ('rsc_check_loss_disposition_0150','uuid, boolean'):('checked_fact uuid, require_current boolean','void',CHECK_BODY),
    ('rsc_guard_loss_disposition_0150',''):('','trigger',GUARD_BODY),
}
FUNCTION_HASHES = {key:hashlib.sha256(value[2].encode()).hexdigest() for key,value in FUNCTIONS.items()}
TRIGGERS = {f'trg_{table}_loss_disposition_0150':(table,'INSERT','rsc_guard_loss_disposition_0150',5,True)
    for table in (TABLE,'audit_events','state_transition_events','outbox_events','notification_events','notification_person_targets')}
TRIGGERS.update({
    'trg_loss_disposition_lock_0150':(TABLE,'INSERT','rsc_lock_loss_disposition_0150',7,False),
    'trg_loss_disposition_immutable_0150':(TABLE,'UPDATE OR DELETE','rsc_guard_work_order_facts_0090',27,False),
    'trg_loss_disposition_truncate_0150':(TABLE,'TRUNCATE','rsc_guard_work_order_facts_0090',34,False),
})


def _sources():
    account = previous['_sources']()[previous['ACCOUNT_SIGNATURE']][1]
    anchor = runpy.run_path(str(FOLDER/'20260928_0088_receipt_account_admission.py'))['ANCHOR']
    if account.count(anchor)!=1:
        raise RuntimeError('0150 canonical account source anchor drift')
    transaction = loss['TRANSACTION_BODY']
    marker = '    FOR account_id IN SELECT DISTINCT line.reserved_account_id'
    if transaction.count(marker)!=1:
        raise RuntimeError('0150 canonical transaction source anchor drift')
    return {
        previous['ACCOUNT_SIGNATURE']:(account,account.replace(anchor,ACCOUNT_BRANCH+anchor)),
        'public.rsc_check_loss_hold_0145(uuid)':(loss['HOLD_BODY'],HOLD_BODY),
        'public.rsc_check_loss_transaction_0145(uuid)':(transaction,transaction.replace(marker,TRANSACTION_BRANCH+marker)),
    }


def _schema():
    json_type=sa.JSON().with_variant(postgresql.JSONB(),'postgresql')
    columns=[sa.Column('id',sa.Uuid(),primary_key=True,nullable=False)]
    for name,target in (
        ('operation_id','stock_operation_orders'),('line_id','stock_operation_lines'),
        ('headquarters_decision_id','stock_loss_headquarters_decisions'),('executor_person_id','people'),
        ('source_account_id','stock_accounts'),('target_account_id','stock_accounts'),('custody_assignment_id','custody_assignments'),
        ('posting_transaction_id','inventory_transactions'),('posting_movement_id','inventory_movements')):
        options={'deferrable':True,'initially':'DEFERRED'} if name.startswith('posting_') else {}
        columns.append(sa.Column(name,sa.Uuid(),sa.ForeignKey(target+'.id',ondelete='RESTRICT',**options),nullable=False))
    columns += [sa.Column('actor_user_id',sa.String(36),sa.ForeignKey('users.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('authorization_version',sa.BigInteger(),nullable=False),sa.Column('disposition',sa.String(24),nullable=False),
        sa.Column('quantity',sa.Numeric(18,3),nullable=False),sa.Column('request_id',sa.String(160),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False)]
    columns += [sa.Column(name,sa.String(64),nullable=False) for name in ('idempotency_key_hash','request_hash','plan_hash')]
    columns += [sa.Column(name,json_type,nullable=False) for name in ('command_jsonb','plan_jsonb')]
    op.create_table(TABLE,*columns,
        sa.UniqueConstraint('line_id',name='uq_loss_disposition_original_line'),
        sa.UniqueConstraint('headquarters_decision_id',name='uq_loss_disposition_original_decision'),
        sa.UniqueConstraint('posting_transaction_id',name='uq_loss_disposition_transaction'),
        sa.UniqueConstraint('posting_movement_id',name='uq_loss_disposition_movement'),
        sa.UniqueConstraint('actor_user_id','request_id',name='uq_loss_disposition_request'),
        sa.UniqueConstraint('idempotency_key_hash',name='uq_loss_disposition_key'),
        sa.CheckConstraint("disposition IN ('restore_available','convert_used','convert_damaged')",name='ck_loss_disposition_kind'),
        sa.CheckConstraint('quantity > 0 AND authorization_version > 0 AND source_account_id <> target_account_id',name='ck_loss_disposition_dimensions'),
        sa.CheckConstraint('length(request_hash)=64 AND length(plan_hash)=64 AND length(idempotency_key_hash)=64',name='ck_loss_disposition_hashes'),
        sa.CheckConstraint('length(request_id) BETWEEN 8 AND 160',name='ck_loss_disposition_request'))


def _transition(up):
    helper=runpy.run_path(str(FOLDER/'20260927_0087_inbound_fulfillment_boundary.py'))
    helper['_begin_sqlite']()
    dialect=op.get_bind().dialect.name
    if dialect not in ('postgresql','sqlite'):raise RuntimeError('0150 requires PostgreSQL or SQLite')
    if dialect=='postgresql':
        op.execute("DO $$ BEGIN IF current_user<>'star_oam_migrator' OR session_user<>'star_oam_migrator' THEN RAISE EXCEPTION '0150 direct schema owner required'; END IF; END $$")
        op.execute('LOCK TABLE public.alembic_version IN ACCESS EXCLUSIVE MODE')
        op.execute('LOCK TABLE public.inventory_ledger_heads,public.stock_operation_orders,public.stock_operation_lines,public.stock_accounts,public.stock_balances,public.inventory_transactions,public.inventory_movements,public.stock_loss_headquarters_reviews,public.stock_loss_headquarters_decisions,public.audit_events,public.state_transition_events,public.outbox_events,public.notification_events,public.notification_person_targets IN SHARE ROW EXCLUSIVE MODE')
        if not up:op.execute('LOCK TABLE public.stock_loss_dispositions IN ACCESS EXCLUSIVE MODE')
    if not up:helper['_preflight']('EXISTS(SELECT 1 FROM stock_loss_dispositions)','0150 immutable disposition history requires retention')
    if up:_schema()
    if dialect=='postgresql':
        if up:
            for (name,signature),(args,result,body) in FUNCTIONS.items():
                op.execute(f'CREATE FUNCTION public.{name}({args}) RETURNS {result} LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,public AS $body${body}$body$')
                op.execute(f'REVOKE ALL ON FUNCTION public.{name}({signature}) FROM PUBLIC,star_oam_api')
            for name,(table,events,function,_,deferred) in TRIGGERS.items():
                statement=(f'CREATE CONSTRAINT TRIGGER {name} AFTER {events} ON public.{table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW'
                    if deferred else f"CREATE TRIGGER {name} BEFORE {events} ON public.{table} FOR EACH {'STATEMENT' if events=='TRUNCATE' else 'ROW'}")
                op.execute(statement+f' EXECUTE FUNCTION public.{function}()')
                op.execute(f'ALTER TABLE public.{table} ENABLE ALWAYS TRIGGER {name}')
            op.execute(f'GRANT SELECT,INSERT ON public.{TABLE} TO star_oam_api')
        for (name,signature),(args,result,body) in FUNCTIONS.items():
            hq['previous']['previous']['_verify_function'](name,signature,args,result,body)
        replace=runpy.run_path(str(FOLDER/'20260909_0069_stock_reservations.py'))['_replace_function_source']
        for signature,(old,new) in _sources().items():
            replace(signature=signature,expected_hash=hashlib.sha256((old if up else new).encode()).hexdigest(),
                replacement_hash=hashlib.sha256((new if up else old).encode()).hexdigest(),
                replacements=((old,new),) if up else ((new,old),),label='loss_disposition_proof_0150')
        replace(signature='public.rsc_oam_runtime_binding_ready_0044()',expected_hash=OLD_READY_HASH if up else NEW_READY_HASH,
            replacement_hash=NEW_READY_HASH if up else OLD_READY_HASH,
            replacements=((down_revision,revision),) if up else ((revision,down_revision),),label='loss_disposition_ready_0150')
        if not up:
            for name,(table,*_) in TRIGGERS.items():op.execute(f'DROP TRIGGER {name} ON public.{table}')
            for name,signature in reversed(FUNCTIONS):op.execute(f'DROP FUNCTION public.{name}({signature})')
    elif up:
        op.execute(f"CREATE TRIGGER trg_loss_disposition_insert_0150 BEFORE INSERT ON {TABLE} BEGIN SELECT RAISE(ABORT,'0150 PostgreSQL disposition proof required'); END")
        for event in ('UPDATE','DELETE'):
            op.execute(f"CREATE TRIGGER trg_loss_disposition_{event.lower()}_0150 BEFORE {event} ON {TABLE} BEGIN SELECT RAISE(ABORT,'0150 immutable disposition'); END")
    if not up:op.drop_table(TABLE)


def upgrade():_transition(True)
def downgrade():_transition(False)
