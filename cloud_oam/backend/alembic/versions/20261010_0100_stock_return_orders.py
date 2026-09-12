"""Immutable formal return reservations, cancellations and source commitments."""
import hashlib
from datetime import datetime, timezone
from pathlib import Path
import runpy
import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261010_0100"
down_revision = "20261009_0099"
branch_labels = depends_on = None
OLD_HASH = 'b243cded179bdc3db917929adedc477a32f34eef1d1988319d77529058f0e393'
NEW_HASH = 'ffe5a9a8416226cd3bb3a2ac4d8bfcd6d3ed0c176fa66ed29e2c86cc5e74ca85'
TABLES = ("stock_operation_orders", "stock_operation_lines", "stock_operation_serials", "stock_operation_cancellations")

CHECK_BODY = """
DECLARE
    parent public.stock_operation_orders%ROWTYPE;
    cancellation public.stock_operation_cancellations%ROWTYPE;
    tx public.inventory_transactions%ROWTYPE;
    original_tx public.inventory_transactions%ROWTYPE;
    item public.stock_operation_lines%ROWTYPE;
    source public.stock_accounts%ROWTYPE;
    target public.stock_accounts%ROWTYPE;
    recovery public.work_order_material_lines%ROWTYPE;
    recovery_operation public.work_order_material_operations%ROWTYPE;
    move public.inventory_movements%ROWTYPE;
    selected jsonb;
    planned jsonb;
    request_lines jsonb := '[]'::jsonb;
    physical jsonb;
    serial_ids jsonb;
    expected_intent jsonb;
    stock_moves jsonb := '[]'::jsonb;
    stock_command jsonb;
    body jsonb;
    kind text;
    aggregate text;
    fact_id uuid;
    fact_actor text;
    fact_person uuid;
    authority bigint;
    fact_request text;
    fact_hash text;
    digest text;
    at_time timestamptz;
    available numeric;
    committed numeric;
    before_cursor bigint;
    ordinal integer := 0;
BEGIN
    SELECT * INTO parent FROM public.stock_operation_orders WHERE id = checked_order;
    IF NOT FOUND THEN RAISE EXCEPTION '0100 return order missing' USING ERRCODE = '23514'; END IF;
    SELECT * INTO original_tx FROM public.inventory_transactions WHERE id = parent.posting_transaction_id;
    IF original_tx.id IS NULL OR parent.operation_type <> 'return' OR parent.status <> 'submitted'
       OR parent.operation_no <> 'RET-' || upper(substr(parent.idempotency_key_hash,1,24))
       OR parent.idempotency_key_hash !~ '^[0-9a-f]{64}$' OR parent.plan_hash !~ '^[0-9a-f]{64}$'
       OR parent.request_id !~ '^[A-Za-z0-9._:-]{8,160}$' OR parent.authorization_version < 1
       OR length(trim(parent.reason)) NOT BETWEEN 1 AND 500 OR trim(parent.reason) <> parent.reason
       OR jsonb_typeof(parent.plan_jsonb->'lines') IS DISTINCT FROM 'array'
       OR parent.plan_jsonb->'intent' IS DISTINCT FROM parent.command_jsonb
       OR parent.plan_jsonb->'authorization_version' IS DISTINCT FROM to_jsonb(parent.authorization_version)
       OR parent.plan_jsonb->'ledger_cursor' IS DISTINCT FROM to_jsonb(original_tx.ledger_cursor - 1)
       OR jsonb_typeof(parent.plan_jsonb) IS DISTINCT FROM 'object'
       OR jsonb_typeof(parent.command_jsonb) IS DISTINCT FROM 'object'
       OR jsonb_typeof(parent.plan_jsonb->'destination') IS DISTINCT FROM 'object'
       OR COALESCE(parent.plan_jsonb->>'source_basis_hash', '') !~ '^[0-9a-f]{64}$'
       OR parent.plan_hash <> encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(parent.plan_jsonb),'UTF8')),'hex')
       OR parent.request_hash <> encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(parent.command_jsonb),'UTF8')),'hex')
       OR NOT EXISTS (SELECT 1 FROM public.users actor WHERE actor.id = parent.actor_user_id AND actor.person_id = parent.requester_id)
       OR NOT EXISTS (SELECT 1 FROM public.custody_assignments assignment
          WHERE assignment.id = parent.target_custody_assignment_id AND assignment.location_id = parent.target_location_id
            AND assignment.valid_from <= parent.created_at AND (assignment.valid_to IS NULL OR assignment.valid_to > parent.created_at)
            AND parent.plan_jsonb->'destination'->>'custodian_person_id' = assignment.custodian_person_id::text)
       OR parent.plan_jsonb->'destination'->>'source_location_id' IS DISTINCT FROM parent.source_location_id::text
       OR parent.plan_jsonb->'destination'->>'target_location_id' IS DISTINCT FROM parent.target_location_id::text
       OR parent.plan_jsonb->'destination'->>'transit_location_id' IS DISTINCT FROM parent.transit_location_id::text
       OR parent.plan_jsonb->'destination'->>'custody_assignment_id' IS DISTINCT FROM parent.target_custody_assignment_id::text THEN
        RAISE EXCEPTION '0100 return command or immutable destination evidence invalid' USING ERRCODE = '23514';
    END IF;
    IF checked_cancellation IS NULL THEN
        IF NOT EXISTS (SELECT 1 FROM public.stock_locations source_location
            JOIN public.stock_locations destination ON destination.id = parent.target_location_id
            JOIN public.stock_locations transit ON transit.id = parent.transit_location_id
            JOIN public.custody_assignments assignment ON assignment.id = parent.target_custody_assignment_id
            JOIN public.oam_work_orders wo ON wo.id = parent.oam_work_order_id
            WHERE source_location.id = parent.source_location_id AND source_location.location_type = 'personal'
              AND source_location.custodian_person_id = parent.requester_id AND source_location.parent_id = destination.id
              AND source_location.status = 'active' AND destination.status = 'active' AND transit.status = 'active'
              AND destination.location_type = 'region' AND transit.location_type = 'transit' AND transit.parent_id = destination.id
              AND source_location.owner_org_id = destination.owner_org_id AND transit.owner_org_id = destination.owner_org_id
              AND destination.custodian_person_id = assignment.custodian_person_id
              AND wo.engineer_person_id = parent.requester_id) THEN
            RAISE EXCEPTION '0100 current return destination or own source invalid' USING ERRCODE = '23514';
        END IF;
        tx := original_tx; fact_id := parent.id; fact_actor := parent.actor_user_id; fact_person := parent.requester_id;
        authority := parent.authorization_version; fact_request := parent.request_id; fact_hash := parent.request_hash;
        digest := parent.idempotency_key_hash; at_time := parent.created_at;
        kind := 'stock_return_submitted'; aggregate := 'stock_operation_order';
    ELSE
        SELECT * INTO cancellation FROM public.stock_operation_cancellations WHERE id = checked_cancellation;
        SELECT * INTO tx FROM public.inventory_transactions WHERE id = cancellation.posting_transaction_id;
        IF cancellation.id IS NULL OR cancellation.operation_id <> parent.id OR tx.id IS NULL
           OR cancellation.actor_user_id <> parent.actor_user_id OR cancellation.operator_person_id <> parent.requester_id
           OR cancellation.authorization_version < 1 OR cancellation.idempotency_key_hash !~ '^[0-9a-f]{64}$'
           OR cancellation.request_id !~ '^[A-Za-z0-9._:-]{8,160}$'
           OR length(trim(cancellation.reason)) NOT BETWEEN 1 AND 500 OR trim(cancellation.reason) <> cancellation.reason
           OR cancellation.command_jsonb <> jsonb_build_object('operation_id',parent.id::text,
                'operator_person_id',parent.requester_id::text,'reason',cancellation.reason)
           OR cancellation.request_hash <> encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(cancellation.command_jsonb),'UTF8')),'hex')
           OR tx.ledger_cursor <= original_tx.ledger_cursor THEN
            RAISE EXCEPTION '0100 cancellation must refer to its complete original return' USING ERRCODE = '23514';
        END IF;
        fact_id := cancellation.id; fact_actor := cancellation.actor_user_id; fact_person := cancellation.operator_person_id;
        authority := cancellation.authorization_version; fact_request := cancellation.request_id; fact_hash := cancellation.request_hash;
        digest := cancellation.idempotency_key_hash; at_time := cancellation.created_at;
        kind := 'stock_return_cancelled'; aggregate := 'stock_operation_cancellation';
    END IF;
    IF tx.status <> 'posted' OR tx.actor_user_id <> fact_actor OR tx.reversed_transaction_id IS NOT NULL
       OR tx.source_document_type <> 'stock_operation_return' OR tx.source_document_id <> parent.id::text
       OR tx.idempotency_key_hash <> digest OR tx.created_at < at_time
       OR tx.movement_type <> (CASE WHEN checked_cancellation IS NULL THEN 'reserve' ELSE 'release' END)
       OR tx.transaction_no <> 'INV-RETURN-' || (CASE WHEN checked_cancellation IS NULL THEN 'S-' ELSE 'C-' END) || upper(substr(digest,1,20))
       OR tx.posting_key <> 'stock-return:' || (CASE WHEN checked_cancellation IS NULL THEN 'submit_return:' ELSE 'cancel_return:' END) || parent.id::text || ':' || digest
       OR EXISTS (SELECT 1 FROM public.inventory_transactions inverse WHERE inverse.reversed_transaction_id = tx.id)
       OR (SELECT count(*) FROM (SELECT actor_user_id, request_id FROM public.stock_operation_orders
            UNION ALL SELECT actor_user_id, request_id FROM public.stock_operation_cancellations) requests
            WHERE requests.actor_user_id = fact_actor AND requests.request_id = fact_request) <> 1 THEN
        RAISE EXCEPTION '0100 return posting or request identity mismatch' USING ERRCODE = '23514';
    END IF;
    before_cursor := original_tx.ledger_cursor - 1;
    FOR item IN SELECT * FROM public.stock_operation_lines WHERE operation_id = parent.id ORDER BY line_no LOOP
        ordinal := ordinal + 1;
        SELECT * INTO source FROM public.stock_accounts WHERE id = item.stock_account_id;
        SELECT * INTO target FROM public.stock_accounts WHERE id = item.reserved_account_id;
        SELECT * INTO recovery FROM public.work_order_material_lines WHERE id = item.source_recovery_line_id;
        SELECT * INTO recovery_operation FROM public.work_order_material_operations WHERE id = recovery.operation_id;
        SELECT * INTO move FROM public.inventory_movements WHERE transaction_id = tx.id AND line_no = item.line_no;
        selected := parent.command_jsonb->'lines'->(ordinal-1); planned := parent.plan_jsonb->'lines'->(ordinal-1);
        IF source.id IS NULL OR target.id IS NULL OR recovery.id IS NULL OR recovery_operation.id IS NULL OR move.id IS NULL
           OR item.line_no <> ordinal OR item.stock_account_id <> recovery.stock_account_id
           OR recovery_operation.oam_work_order_id <> parent.oam_work_order_id OR recovery_operation.operator_person_id <> parent.requester_id
           OR recovery_operation.operation_type <> 'recover' OR recovery_operation.status <> 'posted'
           OR recovery.quantity < item.quantity OR item.quantity <> move.quantity OR item.reason <> parent.reason
           OR source.custodian_person_id IS DISTINCT FROM parent.requester_id OR source.location_id <> parent.source_location_id
           OR source.availability_bucket <> 'available' OR target.availability_bucket <> 'return_pending'
           OR target.owner_org_id <> source.owner_org_id OR target.location_id <> source.location_id
           OR target.custodian_person_id IS DISTINCT FROM source.custodian_person_id OR target.material_id <> source.material_id
           OR target.lot_id IS DISTINCT FROM source.lot_id OR target.condition_code <> source.condition_code
           OR source.material_id <> item.material_id OR recovery.material_id <> source.material_id
           OR item.target_condition <> source.condition_code OR recovery.condition_before <> source.condition_code
           OR move.external_boundary_code IS NOT NULL
           OR move.from_account_id IS DISTINCT FROM (CASE WHEN checked_cancellation IS NULL THEN source.id ELSE target.id END)
           OR move.to_account_id IS DISTINCT FROM (CASE WHEN checked_cancellation IS NULL THEN target.id ELSE source.id END)
           OR selected->>'source_recovery_line_id' IS DISTINCT FROM recovery.id::text
           OR selected->>'stock_account_id' IS DISTINCT FROM source.id::text
           OR selected->>'quantity' IS DISTINCT FROM to_char(item.quantity,'FM999999999999990.000')
           OR planned->>'selected_quantity' IS DISTINCT FROM selected->>'quantity'
           OR planned->'source'->>'source_recovery_line_id' IS DISTINCT FROM recovery.id::text
           OR planned->'source'->>'recovery_operation_id' IS DISTINCT FROM recovery_operation.id::text
           OR planned->'source'->>'stock_account_id' IS DISTINCT FROM source.id::text
           OR planned->'source'->>'material_id' IS DISTINCT FROM source.material_id::text
           OR planned->'source'->>'owner_org_id' IS DISTINCT FROM source.owner_org_id::text
           OR planned->'source'->>'location_id' IS DISTINCT FROM source.location_id::text
           OR planned->'source'->>'custodian_person_id' IS DISTINCT FROM parent.requester_id::text
           OR planned->'source'->>'condition_code' IS DISTINCT FROM source.condition_code
           OR planned->'source'->'lot_id' IS DISTINCT FROM COALESCE(to_jsonb(source.lot_id::text),'null'::jsonb)
           OR planned->'source'->>'owed_quantity' IS DISTINCT FROM to_char(recovery.quantity,'FM999999999999990.000')
           OR NOT EXISTS (SELECT 1 FROM public.inventory_transactions recovered
                JOIN public.inventory_movements recovered_move ON recovered_move.transaction_id = recovered.id
                  AND recovered_move.line_no = recovery.line_no AND recovered_move.to_account_id = source.id
                  AND recovered_move.from_account_id IS NULL AND recovered_move.quantity = recovery.quantity
                WHERE recovered.id = recovery_operation.posting_transaction_id AND recovered.status = 'posted'
                  AND recovered.movement_type = 'inbound' AND recovered.actor_user_id = parent.actor_user_id
                  AND recovered.source_document_type = 'work_order_material' AND recovered.source_document_id = parent.oam_work_order_id::text
                  AND recovered.ledger_cursor < original_tx.ledger_cursor
                  AND NOT EXISTS (SELECT 1 FROM public.inventory_transactions inverse
                    WHERE inverse.reversed_transaction_id = recovered.id AND inverse.ledger_cursor < original_tx.ledger_cursor)) THEN
            RAISE EXCEPTION '0100 exact return origin, dimensions or movements invalid' USING ERRCODE = '23514';
        END IF;
        SELECT COALESCE(sum(CASE WHEN movement.to_account_id = source.id THEN movement.quantity ELSE -movement.quantity END),0)
            INTO available FROM public.inventory_movements movement JOIN public.inventory_transactions previous ON previous.id = movement.transaction_id
            WHERE (movement.to_account_id = source.id OR movement.from_account_id = source.id) AND previous.ledger_cursor <= before_cursor;
        SELECT COALESCE(sum(held.quantity),0) INTO committed FROM public.stock_operation_lines held
            JOIN public.stock_operation_orders earlier ON earlier.id = held.operation_id
            JOIN public.inventory_transactions reserved ON reserved.id = earlier.posting_transaction_id
            WHERE held.source_recovery_line_id = recovery.id AND reserved.ledger_cursor <= before_cursor
              AND NOT EXISTS (SELECT 1 FROM public.stock_operation_cancellations released
                JOIN public.inventory_transactions released_tx ON released_tx.id = released.posting_transaction_id
                WHERE released.operation_id = earlier.id AND released_tx.ledger_cursor <= before_cursor);
        IF item.quantity > recovery.quantity - committed OR item.quantity > available
           OR planned->'source'->>'committed_quantity' IS DISTINCT FROM to_char(committed,'FM999999999999990.000')
           OR planned->'source'->>'available_quantity' IS DISTINCT FROM to_char(available,'FM999999999999990.000') THEN
            RAISE EXCEPTION '0100 recovery quantity was already committed or unavailable' USING ERRCODE = '23514';
        END IF;
        SELECT COALESCE(jsonb_agg(sn.serial_id::text ORDER BY sn.serial_id::text),'[]'::jsonb),
            COALESCE(jsonb_agg(jsonb_build_object('serial_id', sn.serial_id::text,'sku_code',sku.sku_code,
                'serial_no',serial.serial_no,'qr_code',serial.qr_code) ORDER BY sn.serial_id::text),'[]'::jsonb)
            INTO serial_ids, physical FROM public.stock_operation_serials sn
            JOIN public.inventory_serials serial ON serial.id = sn.serial_id JOIN public.materials sku ON sku.id = serial.material_id
            WHERE sn.line_id = item.id;
        IF selected->'serial_verifications' IS DISTINCT FROM physical
           OR EXISTS (SELECT 1 FROM public.stock_operation_serials sn WHERE sn.line_id = item.id
                AND (NOT sn.sku_verified OR NOT sn.qr_verified OR NOT EXISTS (SELECT 1 FROM public.work_order_material_serials origin_sn
                    WHERE origin_sn.operation_line_id = recovery.id AND origin_sn.serial_id = sn.serial_id)))
           OR (SELECT COALESCE(jsonb_agg(sn.serial_id::text ORDER BY sn.serial_id::text),'[]'::jsonb)
                FROM public.inventory_movement_serials sn WHERE sn.movement_id = move.id) IS DISTINCT FROM serial_ids
           OR (SELECT COALESCE(jsonb_agg(value->>'serial_id' ORDER BY value->>'serial_id'),'[]'::jsonb)
                FROM jsonb_array_elements(planned->'selected_serials')) IS DISTINCT FROM serial_ids THEN
            RAISE EXCEPTION '0100 return serials require exact original and physical codes' USING ERRCODE = '23514';
        END IF;
        request_lines := request_lines || jsonb_build_array(jsonb_build_object('source_recovery_line_id',recovery.id::text,
            'stock_account_id',source.id::text,'quantity',to_char(item.quantity,'FM999999999999990.000'),'serial_verifications',physical));
        stock_moves := stock_moves || jsonb_build_array(jsonb_build_object('external_boundary_code',NULL,
            'from_account_id',move.from_account_id::text,'to_account_id',move.to_account_id::text,
            'quantity',trim_scale(move.quantity)::text,'serial_ids',serial_ids));
    END LOOP;
    expected_intent := jsonb_build_object('operation_type','return','work_order_id',parent.oam_work_order_id::text,
        'operator_person_id',parent.requester_id::text,'target_location_id',parent.target_location_id::text,
        'transit_location_id',parent.transit_location_id::text,'reason',parent.reason,'lines',request_lines);
    IF ordinal NOT BETWEEN 1 AND 100 OR ordinal <> jsonb_array_length(parent.plan_jsonb->'lines')
       OR ordinal <> (SELECT count(*) FROM public.inventory_movements WHERE transaction_id = tx.id)
       OR expected_intent <> parent.command_jsonb
       OR EXISTS (SELECT 1 FROM (SELECT line.source_recovery_line_id,
            lag(line.source_recovery_line_id::text) OVER (ORDER BY line.line_no) previous
            FROM public.stock_operation_lines line WHERE line.operation_id = parent.id) sorted
            WHERE previous >= source_recovery_line_id::text) THEN
        RAISE EXCEPTION '0100 entire canonical return batch required' USING ERRCODE = '23514';
    END IF;
    stock_command := jsonb_build_object('operation','post','actor',jsonb_build_object('authorization_version',authority,
        'person_id',fact_person::text,'user_id',fact_actor),'command',jsonb_build_object('effective_at',
        to_char(tx.effective_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),'movement_type',tx.movement_type,
        'movements',stock_moves,'posting_key',tx.posting_key,'source_document_id',parent.id::text,
        'source_document_type','stock_operation_return','transaction_no',tx.transaction_no));
    IF tx.request_hash <> encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(stock_command),'UTF8')),'hex') THEN
        RAISE EXCEPTION '0100 stock posting hash differs from return command' USING ERRCODE = '23514'; END IF;
    body := jsonb_build_object('operation_id',parent.id::text,'work_order_id',parent.oam_work_order_id::text,
        'requester_id',parent.requester_id::text,'posting_transaction_id',tx.id::text,'request_hash',fact_hash,
        'cancellation_id',checked_cancellation::text);
    IF (SELECT count(*) FROM public.audit_events event WHERE event.stream_key='material_request'
            AND event.aggregate_type=aggregate AND event.aggregate_id=fact_id::text AND event.actor_user_id=fact_actor
            AND event.action=kind AND event.request_id=fact_request AND event.before_jsonb='{}'::jsonb AND event.after_jsonb=body) <> 1
       OR (SELECT count(*) FROM public.outbox_events event WHERE event.aggregate_type=aggregate AND event.aggregate_id=fact_id::text
            AND event.event_type=kind AND event.idempotency_key=kind || ':' || fact_id::text AND event.payload_jsonb=body) <> 1
       OR (SELECT count(*) FROM public.state_transition_events event WHERE event.aggregate_type=aggregate AND event.aggregate_id=fact_id::text
            AND event.from_status IS NULL AND event.to_status=(CASE WHEN checked_cancellation IS NULL THEN 'submitted' ELSE 'cancelled' END)
            AND event.actor_id=fact_actor AND event.reason=kind AND event.idempotency_key=kind || ':' || fact_id::text
            AND event.metadata_jsonb=body) <> 1
       OR (SELECT count(*) FROM public.audit_events event WHERE event.stream_key='inventory'
            AND event.aggregate_type='inventory_transaction' AND event.aggregate_id=tx.id::text AND event.actor_user_id=fact_actor
            AND event.action='inventory.transaction.posted' AND event.request_id='inventory-request-' || encode(sha256(convert_to('cloud_oam.inventory.request.v1','UTF8') || decode('00','hex') || convert_to(fact_request,'UTF8')),'hex')
            AND (event.before_jsonb IS NULL OR event.before_jsonb = 'null'::jsonb) AND event.after_jsonb=jsonb_build_object('ledger_cursor',tx.ledger_cursor,
                'movement_count',ordinal,'movement_type',tx.movement_type,'posting_key',tx.posting_key,'reversed_transaction_id',NULL,'status','posted')) <> 1
       OR (SELECT count(*) FROM public.state_transition_events event
            WHERE event.aggregate_type='inventory_transaction' AND event.aggregate_id=tx.id::text
              AND event.actor_id=fact_actor AND event.from_status IS NULL AND event.to_status='posted'
              AND event.reason='inventory_transaction_posted'
              AND event.idempotency_key='inventory-state-' || encode(sha256(convert_to('cloud_oam.inventory.state.v1','UTF8') || decode('00','hex') || convert_to(tx.id::text,'UTF8') || decode('00','hex') || convert_to('posted','UTF8')),'hex')
              AND event.metadata_jsonb=jsonb_build_object('ledger_cursor',tx.ledger_cursor,'movement_type',tx.movement_type,
                'request_reference','inventory-request-' || encode(sha256(convert_to('cloud_oam.inventory.request.v1','UTF8') || decode('00','hex') || convert_to(fact_request,'UTF8')),'hex'))) <> 1
       OR (SELECT count(*) FROM public.outbox_events event
            WHERE event.aggregate_type='inventory_transaction' AND event.aggregate_id=tx.id::text
              AND event.event_type='inventory.transaction.posted'
              AND event.idempotency_key='inventory-outbox-' || encode(sha256(convert_to('cloud_oam.inventory.outbox.v1','UTF8') || decode('00','hex') || convert_to(tx.id::text,'UTF8') || decode('00','hex') || convert_to('posted','UTF8')),'hex')
              AND event.payload_jsonb=jsonb_build_object('transaction_id',tx.id::text,'transaction_no',tx.transaction_no,
                'movement_type',tx.movement_type,'ledger_cursor',tx.ledger_cursor,'reversed_transaction_id',NULL)) <> 1 THEN
        RAISE EXCEPTION '0100 original audit, state or outbox evidence missing' USING ERRCODE = '23514';
    END IF;
END;
"""

DISPATCH_BODY = """
DECLARE
    checked_order uuid;
    checked_cancel uuid;
    checked_tx uuid;
    tx public.inventory_transactions%ROWTYPE;
BEGIN
    PERFORM 1 FROM public.inventory_ledger_heads WHERE stream_key = 'inventory' FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION '0100 inventory ledger head missing' USING ERRCODE = '23514'; END IF;
    IF TG_TABLE_NAME = 'stock_operation_orders' THEN checked_order := NEW.id;
    ELSIF TG_TABLE_NAME = 'stock_operation_lines' THEN checked_order := NEW.operation_id;
    ELSIF TG_TABLE_NAME = 'stock_operation_serials' THEN
        SELECT operation_id INTO checked_order FROM public.stock_operation_lines WHERE id = NEW.line_id;
    ELSIF TG_TABLE_NAME = 'stock_operation_cancellations' THEN checked_order := NEW.operation_id; checked_cancel := NEW.id;
    ELSIF TG_TABLE_NAME IN ('inventory_transactions','inventory_movements','inventory_movement_serials') THEN
        IF TG_TABLE_NAME = 'inventory_transactions' THEN checked_tx := NEW.id; ELSE checked_tx := NEW.transaction_id; END IF;
    ELSE
        IF NEW.aggregate_type = 'stock_operation_order' THEN checked_order := NEW.aggregate_id::uuid;
        ELSIF NEW.aggregate_type = 'stock_operation_cancellation' THEN
            SELECT operation_id, id INTO checked_order, checked_cancel FROM public.stock_operation_cancellations WHERE id = NEW.aggregate_id::uuid;
            IF checked_order IS NULL THEN RAISE EXCEPTION '0100 cancellation event detached from its command' USING ERRCODE = '23514'; END IF;
        ELSIF NEW.aggregate_type = 'inventory_transaction' THEN checked_tx := NEW.aggregate_id::uuid;
        ELSE RETURN NULL; END IF;
    END IF;
    IF checked_tx IS NOT NULL THEN
        SELECT * INTO tx FROM public.inventory_transactions WHERE id = checked_tx;
        IF NOT FOUND THEN RAISE EXCEPTION '0100 return evidence has no stock transaction' USING ERRCODE = '23514'; END IF;
        IF tx.reversed_transaction_id IS NOT NULL THEN
            IF EXISTS (SELECT 1 FROM public.inventory_transactions original WHERE original.id = tx.reversed_transaction_id
                  AND original.source_document_type = 'stock_operation_return')
               OR EXISTS (SELECT 1 FROM public.stock_operation_orders WHERE posting_transaction_id = tx.reversed_transaction_id)
               OR EXISTS (SELECT 1 FROM public.stock_operation_cancellations WHERE posting_transaction_id = tx.reversed_transaction_id) THEN
                RAISE EXCEPTION '0100 return stock cannot use generic reversal' USING ERRCODE = '23514'; END IF;
            IF EXISTS (SELECT 1 FROM public.stock_operation_lines line
                JOIN public.work_order_material_lines origin ON origin.id = line.source_recovery_line_id
                JOIN public.work_order_material_operations recovery ON recovery.id = origin.operation_id
                JOIN public.stock_operation_orders parent ON parent.id = line.operation_id
                JOIN public.inventory_transactions reserved ON reserved.id = parent.posting_transaction_id
                WHERE recovery.posting_transaction_id = tx.reversed_transaction_id AND reserved.ledger_cursor < tx.ledger_cursor
                  AND NOT EXISTS (SELECT 1 FROM public.stock_operation_cancellations cancelled
                    JOIN public.inventory_transactions released ON released.id = cancelled.posting_transaction_id
                    WHERE cancelled.operation_id = parent.id AND released.ledger_cursor < tx.ledger_cursor)) THEN
                RAISE EXCEPTION '0100 active return prevents reversal of its original recovery' USING ERRCODE = '23514'; END IF;
        END IF;
        IF tx.source_document_type = 'stock_operation_return' THEN
            SELECT id INTO checked_order FROM public.stock_operation_orders WHERE posting_transaction_id = tx.id;
            IF checked_order IS NULL THEN
                SELECT operation_id, id INTO checked_order, checked_cancel FROM public.stock_operation_cancellations WHERE posting_transaction_id = tx.id;
            END IF;
            IF checked_order IS NULL THEN RAISE EXCEPTION '0100 detached return inventory transaction' USING ERRCODE = '23514'; END IF;
        ELSIF EXISTS (SELECT 1 FROM public.inventory_movements movement
            JOIN public.stock_operation_lines line ON line.reserved_account_id IN (movement.from_account_id,movement.to_account_id)
            WHERE movement.transaction_id = tx.id) THEN
            RAISE EXCEPTION '0100 held return stock requires its exact business document' USING ERRCODE = '23514';
        ELSE RETURN NULL; END IF;
    END IF;
    IF checked_order IS NULL THEN RAISE EXCEPTION '0100 return evidence is detached' USING ERRCODE = '23514'; END IF;
    PERFORM public.rsc_check_stock_return_0100(checked_order, checked_cancel);
    RETURN NULL;
END;
"""

ACCOUNT_BRANCH = """    IF EXISTS (
        SELECT 1 FROM public.stock_operation_lines line
        JOIN public.stock_operation_orders parent ON parent.id = line.operation_id
        JOIN public.inventory_transactions tx ON tx.id = parent.posting_transaction_id
        JOIN public.inventory_movements movement ON movement.transaction_id = tx.id AND movement.line_no = line.line_no
        JOIN public.stock_accounts source ON source.id = line.stock_account_id
        JOIN public.stock_balances balance ON balance.stock_account_id = NEW.id
        JOIN public.inventory_opening_establishments established ON established.owner_org_id = NEW.owner_org_id
          AND established.location_id = NEW.location_id
        WHERE line.reserved_account_id = NEW.id AND NEW.availability_bucket = 'return_pending'
          AND movement.to_account_id = NEW.id AND movement.from_account_id = source.id AND movement.quantity = line.quantity
          AND tx.status = 'posted' AND tx.movement_type = 'reserve' AND tx.source_document_type = 'stock_operation_return'
          AND tx.source_document_id = parent.id::text AND parent.requester_id = NEW.custodian_person_id
          AND source.availability_bucket = 'available' AND NEW.owner_org_id = source.owner_org_id
          AND NEW.custodian_person_id = source.custodian_person_id AND NEW.location_id = source.location_id
          AND NEW.material_id = source.material_id AND NEW.condition_code = source.condition_code
          AND NEW.lot_id IS NOT DISTINCT FROM source.lot_id
          AND NEW.created_at >= established.established_at AND NEW.created_at <= tx.created_at AND NEW.updated_at = NEW.created_at
          AND balance.version = 1 AND balance.ledger_cursor = tx.ledger_cursor
          AND balance.quantity = (SELECT sum(incoming.quantity) FROM public.inventory_movements incoming
            WHERE incoming.transaction_id = tx.id AND incoming.to_account_id = NEW.id)
          AND NOT EXISTS (SELECT 1 FROM public.inventory_movements movement_before
            JOIN public.inventory_transactions previous ON previous.id = movement_before.transaction_id
            WHERE (movement_before.from_account_id = NEW.id OR movement_before.to_account_id = NEW.id) AND previous.ledger_cursor < tx.ledger_cursor)
    ) THEN RETURN NEW; END IF;

"""


def _account_sources():
    prior = runpy.run_path(str(Path(__file__).with_name("20261003_0093_work_order_replacements.py")))
    _, old = prior["_sources"]()["public.rsc_require_opening_observation_account_0023()"]
    anchor = runpy.run_path(str(Path(__file__).with_name("20260928_0088_receipt_account_admission.py")))["ANCHOR"]
    if old.count(anchor) != 1: raise RuntimeError("0100 account admission source drift")
    return old, old.replace(anchor, ACCOUNT_BRANCH + anchor)


FUNCTIONS = {
    ("rsc_check_stock_return_0100", "uuid, uuid"): ("checked_order uuid, checked_cancellation uuid", "void", CHECK_BODY),
    ("rsc_dispatch_stock_return_0100", ""): ("", "trigger", DISPATCH_BODY),
}
FUNCTION_HASHES = {key: hashlib.sha256(value[2].encode()).hexdigest() for key, value in FUNCTIONS.items()}
TRIGGERS = {}
for _table in TABLES:
    TRIGGERS[f"trg_{_table}_proof_0100"] = (_table, "INSERT", "rsc_dispatch_stock_return_0100", 5, True)
    TRIGGERS[f"trg_{_table}_immutable_0100"] = (_table, "UPDATE OR DELETE", "rsc_guard_work_order_facts_0090", 27, False)
    TRIGGERS[f"trg_{_table}_no_truncate_0100"] = (_table, "TRUNCATE", "rsc_guard_work_order_facts_0090", 34, False)
for _table in ("inventory_transactions", "inventory_movements", "inventory_movement_serials", "audit_events", "outbox_events", "state_transition_events"):
    TRIGGERS[f"trg_{_table}_return_0100"] = (_table, "INSERT", "rsc_dispatch_stock_return_0100", 5, True)


def _permissions(upgrade):
    identifiers = tuple(uuid.UUID(f"20000000-0000-4000-8000-{number:012d}") for number in (63,64,65))
    now = datetime.now(timezone.utc)
    permissions = sa.table("permissions", sa.column("id", sa.Uuid()), sa.column("resource", sa.String()),
        sa.column("action", sa.String()), sa.column("field_code", sa.String()), sa.column("description", sa.String()),
        sa.column("created_at", sa.DateTime()), sa.column("updated_at", sa.DateTime()))
    grants = sa.table("role_permissions", sa.column("id", sa.Uuid()), sa.column("role_id", sa.Uuid()),
        sa.column("permission_id", sa.Uuid()), sa.column("effect", sa.String()), sa.column("created_at", sa.DateTime()))
    rows = [dict(id=uuid.UUID(f"21000000-0000-4000-8000-{112 + (role-1)*3 + index:012d}"),
        role_id=uuid.UUID(f"10000000-0000-4000-8000-{role:012d}"), permission_id=identifier, effect="allow", created_at=now)
        for role in (1,2,3) for index, identifier in enumerate(identifiers)]
    if upgrade:
        op.bulk_insert(permissions, [dict(id=identifier, resource="stock_operation", action=action, field_code="",
            description="Formal return " + action, created_at=now, updated_at=now)
            for identifier, action in zip(identifiers, ("read", "submit_return", "cancel_return"))])
        op.bulk_insert(grants, rows)
    else:
        op.execute(grants.delete().where(grants.c.id.in_([row["id"] for row in rows])))
        op.execute(permissions.delete().where(permissions.c.id.in_(identifiers)))


def _transition(upgrade):
    db = op.get_bind()
    if db.dialect.name not in {"postgresql", "sqlite"}: raise RuntimeError("0100 supports PostgreSQL and SQLite only")
    folder = Path(__file__).parent
    previous = runpy.run_path(str(folder / "20260927_0087_inbound_fulfillment_boundary.py"))
    previous["_begin_sqlite"]()
    if db.dialect.name == "postgresql":
        op.execute("LOCK TABLE public.alembic_version IN ACCESS EXCLUSIVE MODE")
        op.execute("LOCK TABLE public.inventory_ledger_heads, public.inventory_transactions, public.inventory_movements, public.stock_accounts, public.work_order_material_lines, public.work_order_material_operations, public.audit_events IN SHARE ROW EXCLUSIVE MODE")
    if not upgrade:
        if db.dialect.name == "postgresql": op.execute("LOCK TABLE " + ",".join("public." + table for table in TABLES) + " IN SHARE ROW EXCLUSIVE MODE")
        previous["_preflight"](" OR ".join(f"EXISTS (SELECT 1 FROM {table})" for table in TABLES),
            "0100 downgrade blocked: immutable return history must be retained")
    if upgrade:
        _create_tables()
        op.create_index("ix_stock_operation_lines_recovery", "stock_operation_lines", ["source_recovery_line_id"])
        _permissions(True)
    if db.dialect.name == "postgresql":
        replace = runpy.run_path(str(folder / "20260912_0072_outbound_postings.py"))["_previous"]()["_previous"]()["_previous"]()["_replace_function_source"]
        if upgrade:
            for (name, signature), (args, result, body) in FUNCTIONS.items():
                op.execute(f"CREATE FUNCTION public.{name}({args}) RETURNS {result} LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $body${body}$body$")
                op.execute(f"REVOKE ALL ON FUNCTION public.{name}({signature}) FROM PUBLIC, star_oam_api")
            for name, (table, events, function, _, deferred) in TRIGGERS.items():
                sql = (f"CREATE CONSTRAINT TRIGGER {name} AFTER {events} ON public.{table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW" if deferred
                    else f"CREATE TRIGGER {name} BEFORE {events} ON public.{table} FOR EACH {'STATEMENT' if events == 'TRUNCATE' else 'ROW'}")
                op.execute(sql + f" EXECUTE FUNCTION public.{function}()")
                op.execute(f"ALTER TABLE public.{table} ENABLE ALWAYS TRIGGER {name}")
            op.execute("GRANT SELECT, INSERT ON " + ",".join("public." + table for table in TABLES) + " TO star_oam_api")
        else:
            for (name, signature), digest in FUNCTION_HASHES.items():
                op.execute(f"""DO $body$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_proc p
                    WHERE p.oid='public.{name}({signature})'::regprocedure
                      AND encode(sha256(convert_to(p.prosrc,'UTF8')),'hex')='{digest}'
                      AND p.proowner=(SELECT oid FROM pg_roles WHERE rolname=current_user) AND p.prosecdef
                      AND p.proconfig=ARRAY['search_path=pg_catalog, public']
                      AND NOT EXISTS (SELECT 1 FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) acl WHERE acl.grantee<>p.proowner))
                    THEN RAISE EXCEPTION '0100 function source, ownership or ACL drift'; END IF; END $body$""")
        old, new = _account_sources()
        replace(signature="public.rsc_require_opening_observation_account_0023()",
            expected_hash=hashlib.sha256((old if upgrade else new).encode()).hexdigest(),
            replacement_hash=hashlib.sha256((new if upgrade else old).encode()).hexdigest(),
            replacements=((old,new),) if upgrade else ((new,old),), label="stock_return_account_admission_0100")
        replace(signature="public.rsc_oam_runtime_binding_ready_0044()", expected_hash=OLD_HASH if upgrade else NEW_HASH,
            replacement_hash=NEW_HASH if upgrade else OLD_HASH, replacements=((down_revision,revision),) if upgrade else ((revision,down_revision),),
            label="stock_return_readiness_0100")
        if not upgrade:
            for name, (table, *_) in TRIGGERS.items(): op.execute(f"DROP TRIGGER {name} ON public.{table}")
            for name, signature in reversed(FUNCTIONS): op.execute(f"DROP FUNCTION public.{name}({signature})")
    if db.dialect.name == "sqlite" and upgrade:
        for table in TABLES:
            for action in ("UPDATE", "DELETE"):
                op.execute(f"CREATE TRIGGER trg_{table}_{action.lower()}_0100 BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT, '0100 return facts are append-only'); END")
    if not upgrade:
        _permissions(False)
        for table in reversed(TABLES): op.drop_table(table)


def upgrade(): _transition(True)
def downgrade(): _transition(False)

def _create_tables():
    op.create_table('stock_operation_orders',
        sa.Column('id', sa.Uuid(), nullable=False, primary_key=True),
        sa.Column('operation_no', sa.String(100), nullable=False),
        sa.Column('operation_type', sa.String(24), nullable=False),
        sa.Column('status', sa.String(24), nullable=False),
        sa.Column('oam_work_order_id', sa.Uuid(), nullable=False),
        sa.Column('source_location_id', sa.Uuid(), nullable=False),
        sa.Column('target_location_id', sa.Uuid(), nullable=False),
        sa.Column('transit_location_id', sa.Uuid(), nullable=False),
        sa.Column('target_custody_assignment_id', sa.Uuid(), nullable=False),
        sa.Column('requester_id', sa.Uuid(), nullable=False),
        sa.Column('actor_user_id', sa.String(36), nullable=False),
        sa.Column('authorization_version', sa.BigInteger(), nullable=False),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('request_id', sa.String(160), nullable=False),
        sa.Column('idempotency_key_hash', sa.String(64), nullable=False),
        sa.Column('request_hash', sa.String(64), nullable=False),
        sa.Column('plan_hash', sa.String(64), nullable=False),
        sa.Column('command_jsonb', sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=False),
        sa.Column('plan_jsonb', sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=False),
        sa.Column('posting_transaction_id', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint('authorization_version > 0 AND length(reason) BETWEEN 1 AND 500', name='ck_stock_operation_orders_context'),
        sa.CheckConstraint('source_location_id <> target_location_id AND source_location_id <> transit_location_id AND target_location_id <> transit_location_id', name='ck_stock_operation_orders_locations'),
        sa.CheckConstraint("operation_type = 'return' AND status = 'submitted'", name='ck_stock_operation_orders_type_status'),
        sa.ForeignKeyConstraint(['oam_work_order_id'], ['oam_work_orders.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['posting_transaction_id'], ['inventory_transactions.id'], deferrable=True, initially='DEFERRED'),
        sa.ForeignKeyConstraint(['requester_id'], ['people.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['source_location_id'], ['stock_locations.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['transit_location_id'], ['stock_locations.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['actor_user_id'], ['users.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['target_location_id'], ['stock_locations.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['target_custody_assignment_id'], ['custody_assignments.id'], ondelete='RESTRICT'),
        sa.UniqueConstraint('idempotency_key_hash', name='uq_stock_operation_orders_key'),
        sa.UniqueConstraint('operation_no', name='uq_stock_operation_orders_no'),
        sa.UniqueConstraint('posting_transaction_id', name='uq_stock_operation_orders_posting'),
        sa.UniqueConstraint('actor_user_id', 'request_id', name='uq_stock_operation_orders_request'),
    )
    op.create_table('stock_operation_lines',
        sa.Column('id', sa.Uuid(), nullable=False, primary_key=True),
        sa.Column('operation_id', sa.Uuid(), nullable=False),
        sa.Column('line_no', sa.BigInteger(), nullable=False),
        sa.Column('source_recovery_line_id', sa.Uuid(), nullable=False),
        sa.Column('stock_account_id', sa.Uuid(), nullable=False),
        sa.Column('reserved_account_id', sa.Uuid(), nullable=False),
        sa.Column('material_id', sa.Uuid(), nullable=False),
        sa.Column('quantity', sa.Numeric(18, 3), nullable=False),
        sa.Column('target_condition', sa.String(24), nullable=False),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("stock_account_id <> reserved_account_id AND target_condition IN ('used','damaged')", name='ck_stock_operation_lines_dimensions'),
        sa.CheckConstraint('line_no > 0 AND quantity > 0', name='ck_stock_operation_lines_quantity'),
        sa.ForeignKeyConstraint(['material_id'], ['materials.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['operation_id'], ['stock_operation_orders.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['stock_account_id'], ['stock_accounts.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['reserved_account_id'], ['stock_accounts.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['source_recovery_line_id'], ['work_order_material_lines.id'], ondelete='RESTRICT'),
        sa.UniqueConstraint('operation_id', 'line_no', name='uq_stock_operation_lines_order'),
        sa.UniqueConstraint('operation_id', 'source_recovery_line_id', name='uq_stock_operation_lines_origin'),
    )
    op.create_table('stock_operation_serials',
        sa.Column('id', sa.Uuid(), nullable=False, primary_key=True),
        sa.Column('line_id', sa.Uuid(), nullable=False),
        sa.Column('serial_id', sa.Uuid(), nullable=False),
        sa.Column('sku_verified', sa.Boolean(), nullable=False),
        sa.Column('qr_verified', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['line_id'], ['stock_operation_lines.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['serial_id'], ['inventory_serials.id'], ondelete='RESTRICT'),
        sa.UniqueConstraint('line_id', 'serial_id', name='uq_stock_operation_serials_line'),
    )
    op.create_table('stock_operation_cancellations',
        sa.Column('id', sa.Uuid(), nullable=False, primary_key=True),
        sa.Column('operation_id', sa.Uuid(), nullable=False),
        sa.Column('actor_user_id', sa.String(36), nullable=False),
        sa.Column('operator_person_id', sa.Uuid(), nullable=False),
        sa.Column('authorization_version', sa.BigInteger(), nullable=False),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('request_id', sa.String(160), nullable=False),
        sa.Column('idempotency_key_hash', sa.String(64), nullable=False),
        sa.Column('request_hash', sa.String(64), nullable=False),
        sa.Column('command_jsonb', sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=False),
        sa.Column('posting_transaction_id', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint('authorization_version > 0 AND length(reason) BETWEEN 1 AND 500', name='ck_stock_operation_cancellations_context'),
        sa.ForeignKeyConstraint(['operation_id'], ['stock_operation_orders.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['operator_person_id'], ['people.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['actor_user_id'], ['users.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['posting_transaction_id'], ['inventory_transactions.id'], deferrable=True, initially='DEFERRED'),
        sa.UniqueConstraint('idempotency_key_hash', name='uq_stock_operation_cancellations_key'),
        sa.UniqueConstraint('operation_id', name='uq_stock_operation_cancellations_order'),
        sa.UniqueConstraint('posting_transaction_id', name='uq_stock_operation_cancellations_posting'),
        sa.UniqueConstraint('actor_user_id', 'request_id', name='uq_stock_operation_cancellations_request'),
    )
