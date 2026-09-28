"""Atomically derive an exact regional return from an approved loss decision.

Private backend prerequisite: no routes or permission seeds. Preserve normal
work-order returns, original condition, append-only facts and API master ACLs.
"""
from pathlib import Path
import hashlib
import runpy
from alembic import op
import sqlalchemy as sa

revision='20261201_0152'
down_revision='20261130_0151'
branch_labels=depends_on=None
FOLDER=Path(__file__).parent
previous=runpy.run_path(str(FOLDER/'20261130_0151_stock_loss_custody_uniqueness.py'))
FUNCTION='rsc_check_loss_return_0152'
SIGNATURE='public.'+FUNCTION+'(uuid, boolean)'
OLD_READY_HASH=previous['NEW_READY_HASH']
ready=previous['ready']
NEW_READY_HASH=hashlib.sha256(ready['_ready'].replace(ready['_ready_parent'],revision).encode()).hexdigest()
CHECK_HASH='625cd33baabbd20436a850a776743229e81a9d3aff7f5213aa3fe5fb19bc484d'
CHECK_BODY="""
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
    child public.stock_operation_orders%ROWTYPE; child_line public.stock_operation_lines%ROWTYPE;
    receiver public.custody_assignments%ROWTYPE; receiver_location public.stock_locations%ROWTYPE;
    transit public.stock_locations%ROWTYPE; destination jsonb; child_body jsonb;
    child_kind text:='stock_loss.return_derived';
BEGIN
    SELECT * INTO fact FROM public.stock_loss_dispositions WHERE id=checked_fact;
    SELECT * INTO line FROM public.stock_operation_lines WHERE id=fact.line_id;
    SELECT * INTO child FROM public.stock_operation_orders WHERE id=fact.return_operation_id;
    SELECT * INTO child_line FROM public.stock_operation_lines WHERE operation_id=child.id;
    SELECT * INTO receiver FROM public.custody_assignments WHERE id=child.target_custody_assignment_id;
    SELECT * INTO receiver_location FROM public.stock_locations WHERE id=child.target_location_id;
    SELECT * INTO transit FROM public.stock_locations WHERE id=child.transit_location_id;
    destination:=fact.plan_jsonb->'destination';
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
       OR fact.disposition<>'return_to_region'
       OR fact.request_id !~ '^[A-Za-z0-9._:-]{8,160}$' OR fact.idempotency_key_hash !~ '^[a-f0-9]{64}$'
       OR fact.request_hash !~ '^[a-f0-9]{64}$' OR fact.plan_hash !~ '^[a-f0-9]{64}$'
       OR fact.created_at<review.created_at OR fact.created_at>clock_timestamp() OR fact.created_at>tx.created_at
       OR source.availability_bucket<>'frozen' OR target.availability_bucket<>'return_pending'
       OR source.id=target.id OR source.owner_org_id<>target.owner_org_id
       OR source.custodian_person_id IS DISTINCT FROM target.custodian_person_id
       OR source.custodian_person_id IS DISTINCT FROM parent.requester_id
       OR source.location_id<>target.location_id OR source.material_id<>target.material_id
       OR source.lot_id IS DISTINCT FROM target.lot_id OR source.location_id<>parent.source_location_id
       OR target.condition_code<>source.condition_code
       OR custody.location_id<>source.location_id OR custody.custodian_person_id<>source.custodian_person_id
       OR custody.valid_from>fact.created_at OR (custody.valid_to IS NOT NULL AND custody.valid_to<=fact.created_at)
       OR tx.status<>'posted' OR tx.actor_user_id<>fact.actor_user_id OR tx.reversed_transaction_id IS NOT NULL
       OR tx.source_document_type<>'stock_operation_return' OR tx.source_document_id<>child.id::text
       OR tx.effective_at<>fact.created_at OR tx.idempotency_key_hash<>fact.idempotency_key_hash
       OR tx.movement_type<>'reserve'
       OR tx.transaction_no<>'INV-LOSS-RETURN-'||upper(substr(fact.idempotency_key_hash,1,20))
       OR tx.posting_key<>'stock-loss:return_to_region:'||child.id::text||':'||fact.idempotency_key_hash
       OR move.transaction_id<>tx.id OR move.line_no<>1 OR move.from_account_id IS DISTINCT FROM source.id
       OR move.to_account_id IS DISTINCT FROM target.id OR move.quantity<>fact.quantity OR move.external_boundary_code IS NOT NULL
       OR (SELECT count(*) FROM public.inventory_movements WHERE transaction_id=tx.id)<>1
       OR EXISTS(SELECT 1 FROM public.inventory_transactions WHERE reversed_transaction_id=tx.id)
       OR NOT EXISTS(SELECT 1 FROM public.inventory_transactions original WHERE original.id=parent.posting_transaction_id
            AND original.status='posted' AND original.movement_type='freeze' AND original.source_document_type='stock_operation_loss'
            AND original.source_document_id=parent.id::text AND original.ledger_cursor<tx.ledger_cursor)
       OR EXISTS(SELECT 1 FROM public.inventory_transactions WHERE reversed_transaction_id=parent.posting_transaction_id) THEN
        RAISE EXCEPTION '0152 exact approved disposition graph required' USING ERRCODE='23514'; END IF;
    IF child.id IS NULL OR child_line.id IS NULL OR receiver.id IS NULL
       OR receiver_location.id IS NULL OR transit.id IS NULL
       OR child.id=parent.id OR child.operation_type<>'return' OR child.status<>'submitted'
       OR child.oam_work_order_id IS NOT NULL OR child.loss_headquarters_decision_id IS DISTINCT FROM decision.id
       OR child.requester_id<>parent.requester_id OR child.source_location_id<>source.location_id
       OR child.reason<>decision.reason OR child.operation_no<>'LOSS-RET-'||upper(substr(fact.idempotency_key_hash,1,24))
       OR child.actor_user_id<>fact.actor_user_id OR child.authorization_version<>fact.authorization_version
       OR child.request_id<>fact.request_id OR child.idempotency_key_hash<>fact.idempotency_key_hash
       OR child.request_hash<>fact.request_hash OR child.plan_hash<>fact.plan_hash
       OR child.command_jsonb IS DISTINCT FROM fact.command_jsonb OR child.plan_jsonb IS DISTINCT FROM fact.plan_jsonb
       OR child.posting_transaction_id IS DISTINCT FROM tx.id OR child.created_at<>fact.created_at
       OR EXISTS(SELECT 1 FROM public.stock_operation_cancellations WHERE operation_id=child.id)
       OR (SELECT count(*) FROM public.stock_operation_lines WHERE operation_id=child.id)<>1
       OR child_line.operation_type<>'return' OR child_line.line_no<>1
       OR child_line.source_recovery_line_id IS NOT NULL OR child_line.source_loss_line_id IS DISTINCT FROM line.id
       OR child_line.id=line.id OR child_line.stock_account_id<>source.id OR child_line.reserved_account_id<>target.id
       OR child_line.material_id<>source.material_id OR child_line.quantity<>fact.quantity
       OR child_line.target_condition<>source.condition_code OR child_line.reason<>decision.reason
       OR child_line.created_at<>fact.created_at
       OR receiver.location_id<>child.target_location_id
       OR receiver.valid_from>fact.created_at OR (receiver.valid_to IS NOT NULL AND receiver.valid_to<=fact.created_at)
       OR jsonb_typeof(destination) IS DISTINCT FROM 'object'
       OR (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(destination) key)
            IS DISTINCT FROM ARRAY['custodian_person_id','custody_assignment_id','custody_effective_from',
                'region_org_id','source_location_id','target_location_code','target_location_id','target_location_name',
                'transit_location_code','transit_location_id','transit_location_name']::text[]
       OR destination->>'source_location_id' IS DISTINCT FROM source.location_id::text
       OR destination->>'target_location_id' IS DISTINCT FROM child.target_location_id::text
       OR destination->>'transit_location_id' IS DISTINCT FROM child.transit_location_id::text
       OR destination->>'region_org_id' IS DISTINCT FROM source.owner_org_id::text
       OR destination->>'custody_assignment_id' IS DISTINCT FROM receiver.id::text
       OR destination->>'custodian_person_id' IS DISTINCT FROM receiver.custodian_person_id::text
       OR (destination->>'custody_effective_from')::timestamptz IS DISTINCT FROM receiver.valid_from
       OR EXISTS(SELECT 1 FROM jsonb_each(destination) item WHERE jsonb_typeof(item.value)<>'string') THEN
        RAISE EXCEPTION '0152 exact loss-derived child graph and original route required' USING ERRCODE='23514'; END IF;

    IF require_current THEN
        PERFORM public.rsc_assert_loss_disposition_authority_0150(fact.actor_user_id,fact.authorization_version,
            fact.executor_person_id,source.owner_org_id);
        SELECT * INTO location FROM public.stock_locations WHERE id=location.id FOR SHARE;
        PERFORM id FROM public.custody_assignments WHERE location_id=location.id ORDER BY id FOR SHARE;
        SELECT * INTO custody FROM public.custody_assignments WHERE id=fact.custody_assignment_id;
        IF location.status<>'active' OR location.location_type<>'personal' OR location.owner_org_id<>source.owner_org_id
           OR location.custodian_person_id<>source.custodian_person_id
           OR (SELECT count(*) FROM public.custody_assignments c WHERE c.location_id=location.id
                AND c.valid_from<=clock_timestamp()
                AND (c.valid_to IS NULL OR c.valid_to>clock_timestamp()))<>1
           OR (custody.valid_to IS NOT NULL AND custody.valid_to<=clock_timestamp())
           OR NOT EXISTS(SELECT 1 FROM public.materials WHERE id=source.material_id AND status='active') THEN
            RAISE EXCEPTION '0152 current disposition custody or material changed' USING ERRCODE='23514'; END IF;
        IF NOT EXISTS(SELECT 1 FROM public.inventory_opening_establishments e
            WHERE e.location_id=source.location_id AND e.owner_org_id=source.owner_org_id AND e.established_at<=fact.created_at) THEN
            RAISE EXCEPTION '0152 established opening required' USING ERRCODE='23514'; END IF;
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
            RAISE EXCEPTION '0152 disposition source or target is hard frozen' USING ERRCODE='23514'; END IF;
    END IF;
    IF require_current THEN
        PERFORM id FROM public.stock_locations WHERE id IN (source.location_id,child.target_location_id,child.transit_location_id)
            ORDER BY id FOR UPDATE;
        PERFORM id FROM public.custody_assignments WHERE location_id=child.target_location_id ORDER BY id FOR UPDATE;
        SELECT * INTO location FROM public.stock_locations WHERE id=source.location_id;
        SELECT * INTO receiver_location FROM public.stock_locations WHERE id=child.target_location_id;
        SELECT * INTO transit FROM public.stock_locations WHERE id=child.transit_location_id;
        SELECT * INTO receiver FROM public.custody_assignments WHERE id=child.target_custody_assignment_id;
        IF location.parent_id IS DISTINCT FROM receiver_location.id
           OR receiver_location.status<>'active' OR receiver_location.location_type<>'region'
           OR transit.status<>'active' OR transit.location_type<>'transit' OR transit.parent_id IS DISTINCT FROM receiver_location.id
           OR source.owner_org_id<>receiver_location.owner_org_id OR source.owner_org_id<>transit.owner_org_id
           OR receiver_location.custodian_person_id IS DISTINCT FROM receiver.custodian_person_id
           OR receiver.valid_from>clock_timestamp() OR (receiver.valid_to IS NOT NULL AND receiver.valid_to<=clock_timestamp())
           OR (SELECT count(*) FROM public.custody_assignments c WHERE c.location_id=receiver_location.id
                AND c.valid_from<=clock_timestamp() AND (c.valid_to IS NULL OR c.valid_to>clock_timestamp()))<>1
           OR destination->>'target_location_code' IS DISTINCT FROM receiver_location.code
           OR destination->>'target_location_name' IS DISTINCT FROM receiver_location.name
           OR destination->>'transit_location_code' IS DISTINCT FROM transit.code
           OR destination->>'transit_location_name' IS DISTINCT FROM transit.name THEN
            RAISE EXCEPTION '0152 current loss-return receiving route or custody changed' USING ERRCODE='23514'; END IF;
    END IF;

    SELECT * INTO policy FROM public.material_inventory_policies WHERE material_id=source.material_id
        AND effective_from<=fact.created_at AND (effective_to IS NULL OR effective_to>fact.created_at);
    IF policy.id IS NULL OR (SELECT count(*) FROM public.material_inventory_policies WHERE material_id=source.material_id
        AND effective_from<=fact.created_at AND (effective_to IS NULL OR effective_to>fact.created_at))<>1 THEN
        RAISE EXCEPTION '0152 exact historical disposition policy required' USING ERRCODE='23514'; END IF;
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
        RAISE EXCEPTION '0152 exact original held serials required' USING ERRCODE='23514'; END IF;
    IF (SELECT COALESCE(jsonb_agg(serial_id::text ORDER BY serial_id::text),'[]'::jsonb)
            FROM public.stock_operation_serials WHERE line_id=child_line.id) IS DISTINCT FROM serial_ids
       OR EXISTS(SELECT 1 FROM public.stock_operation_serials WHERE line_id=child_line.id
            AND (NOT sku_verified OR NOT qr_verified OR created_at<>fact.created_at)) THEN
        RAISE EXCEPTION '0152 derived return serials must equal original held serials' USING ERRCODE='23514'; END IF;
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
        RAISE EXCEPTION '0152 historical original holds not preserved' USING ERRCODE='23514'; END IF;
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
        RAISE EXCEPTION '0152 disposition policy snapshot changed' USING ERRCODE='23514'; END IF;
    wanted_intent:=jsonb_build_object('headquarters_decision_id',decision.id::text,
        'expected_headquarters_review_hash',review.request_hash,'expected_submission_plan_hash',parent.plan_hash,
        'target_location_id',child.target_location_id::text,'transit_location_id',child.transit_location_id::text);
    wanted_command:=jsonb_build_object('intent',wanted_intent,'request_id',fact.request_id,'expected_plan_hash',fact.plan_hash);
    wanted_plan:=jsonb_build_object('schema_version','1.0','origin_kind','loss_report','intent',wanted_intent,
        'loss_operation_id',parent.id::text,'loss_line_id',line.id::text,'headquarters_decision_id',decision.id::text,
        'derived_return_operation_id',child.id::text,'executor_person_id',fact.executor_person_id::text,
        'requester_id',parent.requester_id::text,'authorization_version',fact.authorization_version,'reason',decision.reason,
        'source_account_id',source.id::text,'pending_account_id',target.id::text,'owner_org_id',source.owner_org_id::text,
        'custodian_person_id',source.custodian_person_id::text,'location_id',source.location_id::text,
        'material_id',source.material_id::text,'lot_id',source.lot_id::text,'condition_code',source.condition_code,
        'source_custody_assignment_id',custody.id::text,'quantity',to_char(fact.quantity,'FM999999999999990.000'),
        'serial_ids',serial_ids,'destination',destination,'movement_type','reserve','source_document_type','stock_operation_return',
        'lifecycle_after','active','return_fulfillment_required',true,'ledger_cursor',tx.ledger_cursor-1,
        'source_balance_version',prior_version,'source_balance_quantity',to_char(prior_quantity,'FM999999999999990.000'),
        'policy_fingerprint',policy_basis,'holds',hold_basis);

    IF fact.command_jsonb IS DISTINCT FROM wanted_command OR fact.plan_jsonb IS DISTINCT FROM wanted_plan
       OR fact.request_hash IS DISTINCT FROM encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(wanted_command),'UTF8')),'hex')
       OR fact.plan_hash IS DISTINCT FROM encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(wanted_plan),'UTF8')),'hex') THEN
        RAISE EXCEPTION '0152 canonical disposition request or plan mismatch' USING ERRCODE='23514'; END IF;
    stock_command:=jsonb_build_object('operation','post','actor',jsonb_build_object('authorization_version',fact.authorization_version,
        'person_id',fact.executor_person_id::text,'user_id',fact.actor_user_id),'command',jsonb_build_object('effective_at',
        to_char(tx.effective_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),'movement_type',tx.movement_type,
        'movements',jsonb_build_array(jsonb_build_object('external_boundary_code',NULL,'from_account_id',source.id::text,
            'to_account_id',target.id::text,'quantity',trim_scale(fact.quantity)::text,'serial_ids',serial_ids)),
        'posting_key',tx.posting_key,'source_document_id',child.id::text,'source_document_type','stock_operation_return','transaction_no',tx.transaction_no));
    IF tx.request_hash IS DISTINCT FROM encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(stock_command),'UTF8')),'hex') THEN
        RAISE EXCEPTION '0152 disposition posting hash mismatch' USING ERRCODE='23514'; END IF;
    body:=jsonb_build_object('disposition_id',fact.id::text,'operation_id',parent.id::text,'line_id',line.id::text,
        'headquarters_decision_id',decision.id::text,'disposition',fact.disposition,'executor_person_id',fact.executor_person_id::text,
        'authorization_version',fact.authorization_version,'posting_transaction_id',tx.id::text,'posting_movement_id',move.id::text,
        'quantity',to_char(fact.quantity,'FM999999999999990.000'),'source_account_id',source.id::text,'target_account_id',target.id::text,
        'request_id',fact.request_id,'request_hash',fact.request_hash,'plan_hash',fact.plan_hash,'status','posted',
        'return_operation_id',child.id::text,'origin_kind','loss_report','stock_effect','frozen_to_return_pending',
        'return_fulfillment_required',true);
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
        RAISE EXCEPTION '0152 complete disposition audit state outbox required' USING ERRCODE='23514'; END IF;
    IF (SELECT count(*) FROM public.notification_events WHERE business_type=aggregate AND business_id=fact.id::text)<>1
       OR NOT EXISTS(SELECT 1 FROM public.notification_events n WHERE n.business_type=aggregate AND n.business_id=fact.id::text
            AND n.event_type=kind AND n.dedup_key=kind||':'||fact.id::text AND n.payload_jsonb=body
            AND n.target_manifest_sha256=encode(sha256(convert_to('notification-person-targets.v1'||chr(10)||parent.requester_id::text,'UTF8')),'hex')
            AND (SELECT count(*) FROM public.notification_person_targets WHERE event_id=n.id)=1
            AND EXISTS(SELECT 1 FROM public.notification_person_targets WHERE event_id=n.id AND person_id=parent.requester_id)) THEN
        RAISE EXCEPTION '0152 independent disposition notification required' USING ERRCODE='23514'; END IF;
    child_body:=jsonb_build_object('operation_id',child.id::text,'loss_disposition_id',fact.id::text,
        'loss_operation_id',parent.id::text,'loss_line_id',line.id::text,'headquarters_decision_id',decision.id::text,
        'requester_id',parent.requester_id::text,'executor_person_id',fact.executor_person_id::text,
        'posting_transaction_id',tx.id::text,'request_hash',fact.request_hash,'plan_hash',fact.plan_hash,
        'origin_kind','loss_report','stock_effect','frozen_to_return_pending');
    IF (SELECT count(*) FROM public.audit_events WHERE stream_key='material_request'
            AND aggregate_type='stock_operation_order' AND aggregate_id=child.id::text)<>1
       OR NOT EXISTS(SELECT 1 FROM public.audit_events e WHERE e.stream_key='material_request'
            AND e.aggregate_type='stock_operation_order' AND e.aggregate_id=child.id::text
            AND e.actor_user_id=fact.actor_user_id AND e.action=child_kind AND e.request_id=fact.request_id
            AND e.before_jsonb='{}'::jsonb AND e.after_jsonb=child_body
            AND e.occurred_at=fact.created_at AND e.created_at=fact.created_at)
       OR (SELECT count(*) FROM public.state_transition_events WHERE aggregate_type='stock_operation_order' AND aggregate_id=child.id::text)<>1
       OR NOT EXISTS(SELECT 1 FROM public.state_transition_events e WHERE e.aggregate_type='stock_operation_order'
            AND e.aggregate_id=child.id::text AND e.from_status IS NULL AND e.to_status='submitted'
            AND e.actor_id=fact.actor_user_id AND e.reason=child_kind AND e.idempotency_key=child_kind||':'||child.id::text
            AND e.metadata_jsonb=child_body AND e.occurred_at=fact.created_at AND e.created_at=fact.created_at)
       OR (SELECT count(*) FROM public.outbox_events WHERE aggregate_type='stock_operation_order' AND aggregate_id=child.id::text)<>1
       OR NOT EXISTS(SELECT 1 FROM public.outbox_events e WHERE e.aggregate_type='stock_operation_order'
            AND e.aggregate_id=child.id::text AND e.event_type=child_kind AND e.idempotency_key=child_kind||':'||child.id::text
            AND e.payload_jsonb=child_body AND e.created_at=fact.created_at) THEN
        RAISE EXCEPTION '0152 exact derived child audit state and outbox required' USING ERRCODE='23514'; END IF;

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
        RAISE EXCEPTION '0152 complete inventory posting evidence required' USING ERRCODE='23514'; END IF;
END;
"""
if hashlib.sha256(CHECK_BODY.encode()).hexdigest()!=CHECK_HASH:raise RuntimeError("0152 proof source digest drift")
SOURCE_HASHES={'public.rsc_require_opening_observation_account_0023()': {'beforeHash': 'f68e2e22fa484e1abd775253574a847679e31f06caffb18acd73c0e5e4e5ee7d', 'afterHash': 'b8ae67ff22df17c599eb72c9d7cf584bc348ed6cfc678085d4983c347761113b'}, 'public.rsc_check_loss_disposition_0150(uuid, boolean)': {'beforeHash': '6186d5278a90e85e5cf45cd260e117b7d4a2c5e6875cd2fdb28e6983a2138113', 'afterHash': 'c693ff953a248bf3565175322d7944c526f01197f287b86cfa46995ba590ee1c'}, 'public.rsc_check_stock_return_0100(uuid, uuid)': {'beforeHash': '13c5755bc4d06a8b4dfab76e4f1c6036cfe6776875f558b1e11d1a39736728a7', 'afterHash': 'aba275cb326ca7f21bf7eb3b7bf5005176c5ad2cfc2ea241ded3fb03945ec547'}}
ACCOUNT_BRANCH="""    IF EXISTS (
        SELECT 1 FROM public.stock_loss_dispositions fact
        JOIN public.stock_operation_lines line ON line.id=fact.line_id AND line.operation_id=fact.operation_id
        JOIN public.stock_loss_headquarters_decisions decision ON decision.id=fact.headquarters_decision_id AND decision.line_id=line.id
        JOIN public.stock_operation_orders child ON child.id=fact.return_operation_id
            AND child.loss_headquarters_decision_id=decision.id AND child.oam_work_order_id IS NULL
        JOIN public.stock_operation_lines child_line ON child_line.operation_id=child.id
            AND child_line.source_loss_line_id=line.id AND child_line.source_recovery_line_id IS NULL
            AND child_line.stock_account_id=fact.source_account_id AND child_line.reserved_account_id=NEW.id
            AND child_line.quantity=fact.quantity AND child_line.target_condition=NEW.condition_code
        JOIN public.inventory_transactions tx ON tx.id=fact.posting_transaction_id AND tx.id=child.posting_transaction_id
        JOIN public.inventory_movements move ON move.id=fact.posting_movement_id AND move.transaction_id=tx.id
        JOIN public.stock_accounts source ON source.id=fact.source_account_id
        JOIN public.stock_locations location ON location.id=NEW.location_id
        JOIN public.custody_assignments custody ON custody.id=fact.custody_assignment_id
        JOIN public.inventory_opening_establishments established ON established.owner_org_id=NEW.owner_org_id
            AND established.location_id=NEW.location_id
        JOIN public.stock_balances balance ON balance.stock_account_id=NEW.id
        WHERE fact.target_account_id=NEW.id AND fact.quantity=line.quantity AND fact.disposition=decision.disposition
          AND fact.disposition='return_to_region' AND child.operation_type='return' AND child_line.operation_type='return'
          AND source.id=line.reserved_account_id AND source.availability_bucket='frozen' AND NEW.availability_bucket='return_pending'
          AND NEW.owner_org_id=source.owner_org_id AND NEW.custodian_person_id=source.custodian_person_id
          AND NEW.location_id=source.location_id AND NEW.material_id=source.material_id AND NEW.lot_id IS NOT DISTINCT FROM source.lot_id
          AND NEW.condition_code=source.condition_code
          AND location.location_type='personal' AND location.status='active' AND location.owner_org_id=NEW.owner_org_id
          AND location.custodian_person_id=NEW.custodian_person_id AND custody.location_id=location.id
          AND custody.custodian_person_id=NEW.custodian_person_id AND custody.valid_from<=fact.created_at
          AND (custody.valid_to IS NULL OR custody.valid_to>fact.created_at)
          AND tx.status='posted' AND tx.movement_type='reserve'
          AND tx.source_document_type='stock_operation_return' AND tx.source_document_id=child.id::text
          AND tx.actor_user_id=fact.actor_user_id AND tx.reversed_transaction_id IS NULL
          AND move.from_account_id=source.id AND move.to_account_id=NEW.id AND move.quantity=fact.quantity AND move.line_no=1
          AND NEW.created_at=fact.created_at AND NEW.updated_at=NEW.created_at AND tx.effective_at=NEW.created_at
          AND established.established_at<=NEW.created_at AND fact.created_at<=tx.created_at
          AND balance.version=1 AND balance.ledger_cursor=tx.ledger_cursor AND balance.quantity=move.quantity
          AND NOT EXISTS(SELECT 1 FROM public.inventory_movements other JOIN public.inventory_transactions prior ON prior.id=other.transaction_id
            WHERE NEW.id IN (other.from_account_id,other.to_account_id) AND (prior.ledger_cursor<tx.ledger_cursor OR other.from_account_id=NEW.id))
    ) THEN
        PERFORM public.rsc_check_loss_return_0152((SELECT d.id FROM public.stock_loss_dispositions d JOIN public.inventory_transactions t ON t.id=d.posting_transaction_id WHERE d.target_account_id=NEW.id ORDER BY t.ledger_cursor LIMIT 1),false);
        RETURN NEW;
    END IF;

"""


def _replace_once(body,old,new):
    if body.count(old)!=1:raise RuntimeError('0152 predecessor source anchor drift')
    return body.replace(old,new)


def _sources():
    disposition=previous['previous']
    account_signature=disposition['previous']['ACCOUNT_SIGNATURE']
    account=disposition['_sources']()[account_signature][1]
    anchor=runpy.run_path(str(FOLDER/'20260928_0088_receipt_account_admission.py'))['ANCHOR']
    result={account_signature:(account,_replace_once(account,anchor,ACCOUNT_BRANCH+anchor))}
    old=previous['NEW_BODY']
    result['public.rsc_check_loss_disposition_0150(uuid, boolean)']=(old,_replace_once(old,'    SELECT * INTO line FROM public.stock_operation_lines WHERE id=fact.line_id;',"    IF fact.return_operation_id IS NOT NULL OR fact.disposition='return_to_region' THEN\n        PERFORM public.rsc_check_loss_return_0152(checked_fact,require_current);\n        RETURN;\n    END IF;\n    SELECT * INTO line FROM public.stock_operation_lines WHERE id=fact.line_id;"))
    old=runpy.run_path(str(FOLDER/'20261015_0105_stock_return_receipts.py'))['_sources']()['public.rsc_check_stock_return_0100(uuid, uuid)'][1]
    result['public.rsc_check_stock_return_0100(uuid, uuid)']=(old,_replace_once(old,'    SELECT * INTO parent FROM public.stock_operation_orders WHERE id = checked_order;',"    IF EXISTS(SELECT 1 FROM public.stock_operation_orders\n        WHERE id=checked_order AND loss_headquarters_decision_id IS NOT NULL) THEN\n        IF checked_cancellation IS NOT NULL THEN\n            RAISE EXCEPTION '0152 loss-derived return requires dedicated cancellation' USING ERRCODE='23514'; END IF;\n        PERFORM public.rsc_check_loss_return_0152((SELECT id FROM public.stock_loss_dispositions\n            WHERE return_operation_id=checked_order),false);\n        RETURN;\n    END IF;\n    SELECT * INTO parent FROM public.stock_operation_orders WHERE id = checked_order;"))
    for signature,(old,new) in result.items():
        expected=SOURCE_HASHES[signature]
        if hashlib.sha256(old.encode()).hexdigest()!=expected['beforeHash'] or hashlib.sha256(new.encode()).hexdigest()!=expected['afterHash']:
            raise RuntimeError('0152 canonical source digest drift: '+signature)
    return result

legacy = runpy.run_path(str(FOLDER/'20261121_0142_stock_operation_typed_context.py'))
TABLES = ('stock_operation_orders','stock_operation_lines','stock_loss_dispositions')
COLUMNS = ('loss_headquarters_decision_id','source_loss_line_id','return_operation_id')
GUARDS = tuple('trg_loss_return_origin_'+str(i)+'_0152' for i in range(3))


def _schema(up):
    db=op.get_bind(); dialect=db.dialect.name
    if dialect not in ('sqlite','postgresql'):
        raise RuntimeError('0152 PostgreSQL or SQLite required')
    saved=()
    if not up:
        helper=runpy.run_path(str(FOLDER/'20260927_0087_inbound_fulfillment_boundary.py'))
        helper['_preflight'](' OR '.join('EXISTS(SELECT 1 FROM '+table+' WHERE '+column+' IS NOT NULL)'
            for table,column in zip(TABLES,COLUMNS)), '0152 derived-return provenance history requires retention')
    if dialect=='sqlite':
        if db.exec_driver_sql('PRAGMA foreign_keys').scalar():
            raise RuntimeError('0152 SQLite migration requires foreign_keys OFF before the migration transaction')
        # Include triggers on other tables whose bodies reference any rebuilt
        # table. Preserve exact definitions, including immutable local guards.
        clauses=' OR '.join("instr(lower(sql),'"+table+"')>0" for table in TABLES)
        saved=tuple(db.execute(sa.text("SELECT name,sql FROM sqlite_master WHERE type='trigger' AND ("+clauses+") ORDER BY name")))
        for name,_ in saved:
            db.exec_driver_sql('DROP TRIGGER '+db.dialect.identifier_preparer.quote(name))
    if up:
        _upgrade_structure()
    else:
        with op.batch_alter_table(TABLES[2]) as b:
            b.drop_constraint('ck_loss_disposition_kind',type_='check')
            b.create_check_constraint('ck_loss_disposition_kind',"disposition IN ('restore_available','convert_used','convert_damaged')")
            b.drop_constraint('fk_loss_disposition_return_operation',type_='foreignkey')
            b.drop_constraint('uq_loss_disposition_return_operation',type_='unique')
            b.drop_column(COLUMNS[2])
        with op.batch_alter_table(TABLES[1]) as b:
            b.drop_constraint('ck_stock_operation_lines_dimensions',type_='check')
            b.create_check_constraint('ck_stock_operation_lines_dimensions',legacy['LINE_DIMENSIONS'])
            b.drop_constraint('ck_stock_operation_lines_loss_origin_not_self',type_='check')
            b.drop_constraint('fk_stock_operation_lines_loss_origin',type_='foreignkey')
            b.drop_constraint('uq_stock_operation_lines_loss_origin',type_='unique')
            b.drop_index('ix_stock_operation_lines_loss')
            b.drop_column(COLUMNS[1])
        with op.batch_alter_table(TABLES[0]) as b:
            b.drop_constraint('ck_stock_operation_orders_locations',type_='check')
            b.create_check_constraint('ck_stock_operation_orders_locations',legacy['ORDER_CONTEXT'])
            b.drop_constraint('fk_stock_operation_orders_loss_decision',type_='foreignkey')
            b.drop_constraint('uq_stock_operation_orders_loss_decision',type_='unique')
            b.drop_column(COLUMNS[0])
    if dialect=='sqlite':
        for name,statement in saved:
            if up or name not in GUARDS:db.exec_driver_sql(statement)
        if up:
            for name,table,column in zip(GUARDS,TABLES,COLUMNS):
                db.exec_driver_sql("CREATE TRIGGER "+name+" BEFORE INSERT ON "+table+" WHEN NEW."+column+" IS NOT NULL BEGIN SELECT RAISE(ABORT,'0152 PostgreSQL derived-return proof required'); END")



ORDER_CONTEXT = ' '.join("""(
    operation_type='return' AND (
        (oam_work_order_id IS NOT NULL AND loss_headquarters_decision_id IS NULL)
        OR (oam_work_order_id IS NULL AND loss_headquarters_decision_id IS NOT NULL)
    ) AND target_location_id IS NOT NULL AND transit_location_id IS NOT NULL
    AND target_custody_assignment_id IS NOT NULL
    AND source_location_id <> target_location_id
    AND source_location_id <> transit_location_id AND target_location_id <> transit_location_id
) OR (
    operation_type='loss_report' AND oam_work_order_id IS NULL AND loss_headquarters_decision_id IS NULL
    AND target_location_id IS NULL AND transit_location_id IS NULL AND target_custody_assignment_id IS NULL
)""".split())
LINE_CONTEXT = ' '.join("""stock_account_id <> reserved_account_id AND (
    (operation_type='return' AND (
        (source_recovery_line_id IS NOT NULL AND source_loss_line_id IS NULL AND target_condition IN ('used','damaged'))
        OR (source_recovery_line_id IS NULL AND source_loss_line_id IS NOT NULL AND target_condition IN ('new','used','damaged'))
    )) OR (operation_type='loss_report' AND source_recovery_line_id IS NULL
        AND source_loss_line_id IS NULL AND target_condition IN ('new','used','damaged'))
)""".split())
DISPOSITION_CONTEXT = "(disposition IN ('restore_available','convert_used','convert_damaged') AND return_operation_id IS NULL) OR (disposition='return_to_region' AND return_operation_id IS NOT NULL AND return_operation_id <> operation_id)"


def _upgrade_structure():
    """Structure only; calling this alone is never a supported application migration."""
    with op.batch_alter_table('stock_operation_orders') as batch:
        batch.add_column(sa.Column('loss_headquarters_decision_id', sa.Uuid(), nullable=True))
        batch.create_foreign_key('fk_stock_operation_orders_loss_decision', 'stock_loss_headquarters_decisions',
            ['loss_headquarters_decision_id'], ['id'], ondelete='RESTRICT')
        batch.create_unique_constraint('uq_stock_operation_orders_loss_decision', ['loss_headquarters_decision_id'])
        batch.drop_constraint('ck_stock_operation_orders_locations', type_='check')
        batch.create_check_constraint('ck_stock_operation_orders_locations', ORDER_CONTEXT)
    with op.batch_alter_table('stock_operation_lines') as batch:
        batch.add_column(sa.Column('source_loss_line_id', sa.Uuid(), nullable=True))
        batch.create_foreign_key('fk_stock_operation_lines_loss_origin', 'stock_operation_lines',
            ['source_loss_line_id'], ['id'], ondelete='RESTRICT')
        batch.create_unique_constraint('uq_stock_operation_lines_loss_origin', ['operation_id','source_loss_line_id'])
        batch.create_index('ix_stock_operation_lines_loss', ['source_loss_line_id'])
        batch.create_check_constraint('ck_stock_operation_lines_loss_origin_not_self', 'source_loss_line_id IS NULL OR source_loss_line_id <> id')
        batch.drop_constraint('ck_stock_operation_lines_dimensions', type_='check')
        batch.create_check_constraint('ck_stock_operation_lines_dimensions', LINE_CONTEXT)
    with op.batch_alter_table('stock_loss_dispositions') as batch:
        batch.add_column(sa.Column('return_operation_id', sa.Uuid(), nullable=True))
        batch.create_foreign_key('fk_loss_disposition_return_operation', 'stock_operation_orders',
            ['return_operation_id'], ['id'], ondelete='RESTRICT', deferrable=True, initially='DEFERRED')
        batch.create_unique_constraint('uq_loss_disposition_return_operation', ['return_operation_id'])
        batch.drop_constraint('ck_loss_disposition_kind', type_='check')
        batch.create_check_constraint('ck_loss_disposition_kind', DISPOSITION_CONTEXT)


def _transition(up):
    helper=runpy.run_path(str(FOLDER/'20260927_0087_inbound_fulfillment_boundary.py'))
    helper['_begin_sqlite']()
    dialect=op.get_bind().dialect.name
    if dialect not in ('postgresql','sqlite'):raise RuntimeError('0152 PostgreSQL or SQLite required')
    if dialect=='postgresql':
        op.execute("DO $$ BEGIN IF current_user<>'star_oam_migrator' OR session_user<>'star_oam_migrator' THEN RAISE EXCEPTION '0152 direct schema owner required'; END IF; END $$")
        op.execute('LOCK TABLE public.alembic_version IN ACCESS EXCLUSIVE MODE')
        op.execute('LOCK TABLE public.inventory_ledger_heads,public.stock_operation_orders,public.stock_operation_lines,public.stock_operation_serials,public.stock_loss_dispositions,public.stock_loss_headquarters_reviews,public.stock_loss_headquarters_decisions,public.stock_accounts,public.stock_balances,public.inventory_transactions,public.inventory_movements,public.stock_locations,public.custody_assignments,public.audit_events,public.state_transition_events,public.outbox_events,public.notification_events,public.notification_person_targets IN SHARE ROW EXCLUSIVE MODE')
    if not up:
        helper['_preflight'](' OR '.join('EXISTS(SELECT 1 FROM '+table+' WHERE '+column+' IS NOT NULL)' for table,column in zip(TABLES,COLUMNS)),
            '0152 derived-return provenance history requires retention')
    if dialect=='postgresql':
        verify=previous['previous']['hq']['previous']['previous']['_verify_function']
        patches=_sources()
        for signature,(old,new) in patches.items():
            name,args=signature.removeprefix('public.').rstrip(')').split('(',1)
            result='trigger' if not args else 'void'
            verify(name,args,'',result,old if up else new)
        if not up:verify(FUNCTION,'uuid, boolean','checked_fact uuid, require_current boolean','void',CHECK_BODY)
    if up:_schema(True)
    if dialect=='postgresql':
        if up:
            op.execute(f'CREATE FUNCTION public.{FUNCTION}(checked_fact uuid, require_current boolean) RETURNS void LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,public AS $body${CHECK_BODY}$body$')
            op.execute(f'REVOKE ALL ON FUNCTION {SIGNATURE} FROM PUBLIC,star_oam_api')
            verify(FUNCTION,'uuid, boolean','checked_fact uuid, require_current boolean','void',CHECK_BODY)
        replace=runpy.run_path(str(FOLDER/'20260909_0069_stock_reservations.py'))['_replace_function_source']
        for signature,(old,new) in patches.items():
            hashes=SOURCE_HASHES[signature]
            replace(signature=signature,expected_hash=hashes['beforeHash' if up else 'afterHash'],
                replacement_hash=hashes['afterHash' if up else 'beforeHash'],
                replacements=((old,new),) if up else ((new,old),),label='loss_return_provenance_0152')
            name,args=signature.removeprefix('public.').rstrip(')').split('(',1)
            verify(name,args,'','trigger' if not args else 'void',new if up else old)
        replace(signature='public.rsc_oam_runtime_binding_ready_0044()',
            expected_hash=OLD_READY_HASH if up else NEW_READY_HASH,replacement_hash=NEW_READY_HASH if up else OLD_READY_HASH,
            replacements=((down_revision,revision),) if up else ((revision,down_revision),),label='loss_return_ready_0152')
        if not up:op.execute(f'DROP FUNCTION {SIGNATURE}')
    if not up:_schema(False)


def upgrade():_transition(True)
def downgrade():_transition(False)
