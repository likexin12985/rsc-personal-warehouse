"""Physical return departures, immutable custody and exact command recovery."""
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import runpy
from uuid import UUID
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '20261013_0103'
down_revision = '20261012_0102'
branch_labels = depends_on = None
OLD_HASH = '5fa738c93218aa8f0de434935f7c6a8f94ec5e71c99a9a283df3b64b142d0de9'
NEW_HASH = '24173a4f3c9cf3754b52559e15020694d268e21d022e0205a747937972f083f2'
TABLES = ('stock_operation_outbounds', 'stock_operation_outbound_lines', 'stock_operation_outbound_serials')

CHECK_BODY = """
DECLARE
    departure public.stock_operation_outbounds%ROWTYPE;
    parent public.stock_operation_orders%ROWTYPE;
    original_tx public.inventory_transactions%ROWTYPE;
    tx public.inventory_transactions%ROWTYPE;
    line public.stock_operation_outbound_lines%ROWTYPE;
    origin public.stock_operation_lines%ROWTYPE;
    source public.stock_accounts%ROWTYPE;
    target public.stock_accounts%ROWTYPE;
    movement public.inventory_movements%ROWTYPE;
    policy public.material_inventory_policies%ROWTYPE;
    selected jsonb; view jsonb; physical jsonb; identifiers jsonb;
    expected_lines jsonb := '[]'::jsonb;
    stock_moves jsonb := '[]'::jsonb;
    expected_intent jsonb; stock_command jsonb; body jsonb;
    available numeric; departed numeric; ordinal integer := 0;
    reference text;
BEGIN
    SELECT * INTO departure FROM public.stock_operation_outbounds WHERE id=checked_outbound;
    IF NOT FOUND THEN RAISE EXCEPTION '0103 detached physical departure' USING ERRCODE='23514'; END IF;
    SELECT * INTO parent FROM public.stock_operation_orders WHERE id=departure.operation_id;
    SELECT * INTO original_tx FROM public.inventory_transactions WHERE id=parent.posting_transaction_id;
    SELECT * INTO tx FROM public.inventory_transactions WHERE id=departure.posting_transaction_id;
    IF parent.id IS NULL OR tx.id IS NULL OR original_tx.id IS NULL
       OR departure.actor_user_id <> parent.actor_user_id OR departure.operator_person_id <> parent.requester_id
       OR departure.status <> 'outbound' OR departure.authorization_version < 1
       OR departure.idempotency_key_hash !~ '^[0-9a-f]{64}$'
       OR departure.request_id !~ '^[A-Za-z0-9._:-]{8,160}$'
       OR departure.outbound_no <> 'RET-OUT-' || upper(substr(departure.idempotency_key_hash,1,24))
       OR length(btrim(departure.reason,E' \\t\\r\\n')) NOT BETWEEN 1 AND 500
       OR btrim(departure.reason,E' \\t\\r\\n') <> departure.reason
       OR departure.outbound_at < parent.created_at OR departure.outbound_at > departure.created_at
       OR departure.created_at > tx.created_at OR departure.created_at > clock_timestamp()
       OR tx.status <> 'posted' OR tx.movement_type <> 'transfer' OR tx.reversed_transaction_id IS NOT NULL
       OR tx.actor_user_id <> departure.actor_user_id OR tx.effective_at <> departure.outbound_at
       OR tx.source_document_type <> 'stock_operation_return_outbound' OR tx.source_document_id <> departure.id::text
       OR tx.idempotency_key_hash <> departure.idempotency_key_hash
       OR tx.transaction_no <> 'INV-RETURN-O-' || upper(substr(departure.idempotency_key_hash,1,20))
       OR tx.posting_key <> 'stock-return:outbound_return:' || parent.id::text || ':' || departure.idempotency_key_hash
       OR tx.ledger_cursor <= original_tx.ledger_cursor
       OR EXISTS (SELECT 1 FROM public.stock_operation_cancellations WHERE operation_id=parent.id)
       OR EXISTS (SELECT 1 FROM public.inventory_transactions WHERE reversed_transaction_id IN (tx.id,original_tx.id))
       OR NOT EXISTS (SELECT 1 FROM public.users actor JOIN public.people person ON person.id=actor.person_id
            WHERE actor.id=departure.actor_user_id AND actor.person_id=departure.operator_person_id
              AND actor.authorization_version=departure.authorization_version AND actor.is_active
              AND actor.account_status='active' AND person.employment_status='active')
       OR (SELECT count(*) FROM (SELECT actor_user_id,request_id FROM public.stock_operation_orders
            UNION ALL SELECT actor_user_id,request_id FROM public.stock_operation_cancellations
            UNION ALL SELECT actor_user_id,request_id FROM public.stock_operation_outbounds) requests
            WHERE requests.actor_user_id=departure.actor_user_id AND requests.request_id=departure.request_id) <> 1
       OR jsonb_typeof(departure.command_jsonb) IS DISTINCT FROM 'object'
       OR jsonb_typeof(departure.plan_jsonb) IS DISTINCT FROM 'object'
       OR jsonb_typeof(departure.plan_jsonb->'lines') IS DISTINCT FROM 'array'
       OR jsonb_typeof(departure.plan_jsonb->'policies') IS DISTINCT FROM 'array'
       OR departure.plan_jsonb->'intent' IS DISTINCT FROM departure.command_jsonb
       OR departure.plan_jsonb->'authorization_version' IS DISTINCT FROM to_jsonb(departure.authorization_version)
       OR departure.plan_jsonb->'ledger_cursor' IS DISTINCT FROM to_jsonb(tx.ledger_cursor-1)
       OR departure.plan_jsonb->>'original_request_hash' IS DISTINCT FROM parent.request_hash
       OR departure.plan_hash <> encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(departure.plan_jsonb),'UTF8')),'hex')
       OR departure.request_hash <> encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(departure.command_jsonb),'UTF8')),'hex') THEN
        RAISE EXCEPTION '0103 departure identity, original history or posting mismatch' USING ERRCODE='23514';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public.stock_locations source_location
        JOIN public.stock_locations destination ON destination.id=parent.target_location_id
        JOIN public.stock_locations transit ON transit.id=parent.transit_location_id
        JOIN public.custody_assignments assignment ON assignment.id=departure.target_custody_assignment_id
        WHERE source_location.id=parent.source_location_id AND source_location.location_type='personal'
          AND source_location.status='active' AND source_location.custodian_person_id=departure.operator_person_id
          AND source_location.parent_id=destination.id AND source_location.owner_org_id=destination.owner_org_id
          AND destination.location_type='region' AND destination.status='active'
          AND transit.location_type='transit' AND transit.status='active' AND transit.parent_id=destination.id
          AND transit.owner_org_id=destination.owner_org_id AND assignment.location_id=destination.id
          AND assignment.custodian_person_id=destination.custodian_person_id
          AND assignment.valid_from<=departure.created_at AND (assignment.valid_to IS NULL OR assignment.valid_to>departure.created_at)
          AND departure.plan_jsonb->'destination'->>'source_location_id'=source_location.id::text
          AND departure.plan_jsonb->'destination'->>'target_location_id'=destination.id::text
          AND departure.plan_jsonb->'destination'->>'transit_location_id'=transit.id::text
          AND departure.plan_jsonb->'destination'->>'custody_assignment_id'=assignment.id::text
          AND departure.plan_jsonb->'destination'->>'custodian_person_id'=assignment.custodian_person_id::text) THEN
        RAISE EXCEPTION '0103 current physical route or receiving custody mismatch' USING ERRCODE='23514';
    END IF;
    FOR line IN SELECT * FROM public.stock_operation_outbound_lines WHERE outbound_id=departure.id ORDER BY line_no LOOP
        ordinal := ordinal+1;
        SELECT * INTO origin FROM public.stock_operation_lines WHERE id=line.operation_line_id;
        SELECT * INTO source FROM public.stock_accounts WHERE id=line.source_stock_account_id;
        SELECT * INTO target FROM public.stock_accounts WHERE id=line.transit_stock_account_id;
        SELECT * INTO movement FROM public.inventory_movements WHERE transaction_id=tx.id AND line_no=ordinal;
        selected := departure.command_jsonb->'lines'->(ordinal-1);
        view := departure.plan_jsonb->'lines'->(ordinal-1);
        IF line.line_no<>ordinal OR origin.id IS NULL OR source.id IS NULL OR target.id IS NULL OR movement.id IS NULL
           OR origin.operation_id<>parent.id OR origin.reserved_account_id<>source.id
           OR source.location_id<>parent.source_location_id OR source.custodian_person_id IS DISTINCT FROM departure.operator_person_id
           OR source.availability_bucket<>'return_pending' OR target.availability_bucket<>'in_transit'
           OR target.location_id<>parent.transit_location_id OR target.owner_org_id<>source.owner_org_id
           OR target.custodian_person_id IS DISTINCT FROM source.custodian_person_id OR target.material_id<>source.material_id
           OR target.condition_code<>source.condition_code OR target.lot_id IS DISTINCT FROM source.lot_id
           OR movement.from_account_id IS DISTINCT FROM source.id OR movement.to_account_id IS DISTINCT FROM target.id
           OR movement.quantity<>line.quantity OR movement.external_boundary_code IS NOT NULL
           OR selected->>'operation_line_id' IS DISTINCT FROM origin.id::text
           OR NOT EXISTS (SELECT 1 FROM public.inventory_opening_establishments established
                WHERE established.owner_org_id=target.owner_org_id AND established.location_id=target.location_id
                  AND established.established_at<=departure.outbound_at) THEN
            RAISE EXCEPTION '0103 exact return line, transit account or stock movement mismatch' USING ERRCODE='23514';
        END IF;
        SELECT COALESCE(sum(previous_line.quantity),0) INTO departed FROM public.stock_operation_outbound_lines previous_line
            JOIN public.stock_operation_outbounds previous_outbound ON previous_outbound.id=previous_line.outbound_id
            JOIN public.inventory_transactions previous_tx ON previous_tx.id=previous_outbound.posting_transaction_id
            WHERE previous_line.operation_line_id=origin.id AND previous_tx.ledger_cursor<tx.ledger_cursor;
        SELECT COALESCE(sum(CASE WHEN previous_move.to_account_id=source.id THEN previous_move.quantity ELSE -previous_move.quantity END),0)
            INTO available FROM public.inventory_movements previous_move
            JOIN public.inventory_transactions previous_tx ON previous_tx.id=previous_move.transaction_id
            WHERE source.id IN (previous_move.from_account_id,previous_move.to_account_id) AND previous_tx.ledger_cursor<tx.ledger_cursor;
        IF line.quantity<=0 OR line.quantity>origin.quantity-departed
           OR (SELECT sum(quantity) FROM public.stock_operation_outbound_lines WHERE outbound_id=departure.id AND source_stock_account_id=source.id)>available
           OR view->>'operation_line_id' IS DISTINCT FROM origin.id::text
           OR view->>'source_recovery_line_id' IS DISTINCT FROM origin.source_recovery_line_id::text
           OR view->>'source_stock_account_id' IS DISTINCT FROM source.id::text
           OR view->>'material_id' IS DISTINCT FROM source.material_id::text
           OR view->>'condition_code' IS DISTINCT FROM source.condition_code OR view->>'lot_id' IS DISTINCT FROM source.lot_id::text
           OR view->>'return_quantity' IS DISTINCT FROM to_char(origin.quantity,'FM999999999999990.000')
           OR view->>'departed_quantity' IS DISTINCT FROM to_char(departed,'FM999999999999990.000')
           OR view->>'remaining_quantity' IS DISTINCT FROM to_char(origin.quantity-departed,'FM999999999999990.000')
           OR view->>'held_quantity' IS DISTINCT FROM to_char(available,'FM999999999999990.000')
           OR view->>'selected_quantity' IS DISTINCT FROM to_char(line.quantity,'FM999999999999990.000') THEN
            RAISE EXCEPTION '0103 departure exceeds its exact remaining line or held stock' USING ERRCODE='23514';
        END IF;
        SELECT * INTO policy FROM public.material_inventory_policies WHERE material_id=source.material_id
            AND effective_from<=departure.created_at AND (effective_to IS NULL OR effective_to>departure.created_at);
        IF NOT FOUND OR line.quantity<>trunc(line.quantity,policy.quantity_scale)
           OR (NOT policy.allow_fraction AND line.quantity<>trunc(line.quantity))
           OR (policy.tracking_mode IN ('lot','lot_and_serial') AND source.lot_id IS NULL)
           OR NOT EXISTS (SELECT 1 FROM jsonb_array_elements(departure.plan_jsonb->'policies') planned_policy
                WHERE jsonb_array_length(planned_policy)=7 AND planned_policy->>0=source.material_id::text
                  AND planned_policy->>1=policy.id::text AND planned_policy->>2=policy.tracking_mode
                  AND planned_policy->3=to_jsonb(policy.quantity_scale) AND planned_policy->4=to_jsonb(policy.allow_fraction)
                  AND (planned_policy->>5)::timestamptz=policy.effective_from
                  AND (planned_policy->>6)::timestamptz IS NOT DISTINCT FROM policy.effective_to) THEN
            RAISE EXCEPTION '0103 departure material policy mismatch' USING ERRCODE='23514';
        END IF;
        SELECT COALESCE(jsonb_agg(serial.serial_id::text ORDER BY serial.serial_id),'[]'::jsonb)
            INTO identifiers FROM public.stock_operation_outbound_serials serial WHERE serial.line_id=line.id;
        SELECT COALESCE(jsonb_agg(jsonb_build_object('serial_id',serial.id::text,'sku_code',material.sku_code,
            'serial_no',serial.serial_no,'qr_code',serial.qr_code) ORDER BY serial.id),'[]'::jsonb)
            INTO physical FROM public.stock_operation_outbound_serials fact_serial
            JOIN public.inventory_serials serial ON serial.id=fact_serial.serial_id
            JOIN public.materials material ON material.id=serial.material_id
            WHERE fact_serial.line_id=line.id AND fact_serial.sku_verified AND fact_serial.qr_verified
              AND serial.material_id=source.material_id AND serial.lot_id IS NOT DISTINCT FROM source.lot_id AND serial.lifecycle_status='active'
              AND EXISTS (SELECT 1 FROM public.stock_operation_serials original_serial WHERE original_serial.line_id=origin.id AND original_serial.serial_id=serial.id);
        IF jsonb_array_length(identifiers)<>jsonb_array_length(physical)
           OR (policy.tracking_mode IN ('serial','lot_and_serial') AND jsonb_array_length(identifiers)<>line.quantity)
           OR (policy.tracking_mode NOT IN ('serial','lot_and_serial') AND jsonb_array_length(identifiers)<>0)
           OR identifiers IS DISTINCT FROM (SELECT COALESCE(jsonb_agg(serial_id::text ORDER BY serial_id),'[]'::jsonb)
                FROM public.inventory_movement_serials WHERE movement_id=movement.id)
           OR view->'selected_serials' IS DISTINCT FROM (SELECT COALESCE(jsonb_agg(jsonb_build_object('serial_id',proof->>'serial_id',
                'serial_no',proof->>'serial_no') ORDER BY proof->>'serial_id'),'[]'::jsonb) FROM jsonb_array_elements(physical) proof)
           OR EXISTS (SELECT 1 FROM public.stock_operation_outbound_serials previous_serial
                JOIN public.stock_operation_outbound_lines previous_line ON previous_line.id=previous_serial.line_id
                JOIN public.stock_operation_outbounds previous_outbound ON previous_outbound.id=previous_line.outbound_id
                JOIN public.inventory_transactions previous_tx ON previous_tx.id=previous_outbound.posting_transaction_id
                WHERE previous_line.operation_line_id=origin.id AND previous_tx.ledger_cursor<tx.ledger_cursor
                  AND identifiers ? previous_serial.serial_id::text) THEN
            RAISE EXCEPTION '0103 exact physical serial evidence or single departure violated' USING ERRCODE='23514';
        END IF;
        expected_lines := expected_lines || jsonb_build_array(jsonb_build_object('operation_line_id',origin.id::text,
            'quantity',to_char(line.quantity,'FM999999999999990.000'),'serial_verifications',physical));
        stock_moves := stock_moves || jsonb_build_array(jsonb_build_object('from_account_id',source.id::text,'to_account_id',target.id::text,
            'quantity',trim_scale(line.quantity)::text,'serial_ids',identifiers,'external_boundary_code',NULL));
    END LOOP;
    expected_intent := jsonb_build_object('operation_type','outbound_return','operation_id',parent.id::text,
        'operator_person_id',departure.operator_person_id::text,'outbound_at',to_char(departure.outbound_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
        'reason',departure.reason,'lines',expected_lines);
    IF ordinal NOT BETWEEN 1 AND 100 OR departure.command_jsonb IS DISTINCT FROM expected_intent
       OR jsonb_array_length(departure.plan_jsonb->'lines')<>ordinal
       OR (SELECT count(*) FROM public.inventory_movements WHERE transaction_id=tx.id)<>ordinal
       OR EXISTS (SELECT 1 FROM (SELECT operation_line_id,lag(operation_line_id::text) OVER (ORDER BY line_no) AS previous
            FROM public.stock_operation_outbound_lines WHERE outbound_id=departure.id) ordered WHERE previous>=operation_line_id::text)
       OR EXISTS (SELECT serial_id FROM public.stock_operation_outbound_serials serial
            JOIN public.stock_operation_outbound_lines detail ON detail.id=serial.line_id WHERE detail.outbound_id=departure.id
            GROUP BY serial_id HAVING count(*)<>1) THEN
        RAISE EXCEPTION '0103 entire canonical physical departure batch required' USING ERRCODE='23514';
    END IF;
    stock_command := jsonb_build_object('operation','post','actor',jsonb_build_object('authorization_version',departure.authorization_version,
        'person_id',departure.operator_person_id::text,'user_id',departure.actor_user_id),'command',jsonb_build_object('effective_at',
        to_char(tx.effective_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),'movement_type',tx.movement_type,'movements',stock_moves,
        'posting_key',tx.posting_key,'source_document_id',departure.id::text,'source_document_type','stock_operation_return_outbound','transaction_no',tx.transaction_no));
    IF tx.request_hash<>encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(stock_command),'UTF8')),'hex') THEN
        RAISE EXCEPTION '0103 posting hash differs from physical departure' USING ERRCODE='23514';
    END IF;
    body := jsonb_build_object('operation_id',parent.id::text,'outbound_id',departure.id::text,'work_order_id',parent.oam_work_order_id::text,
        'operator_person_id',departure.operator_person_id::text,'posting_transaction_id',tx.id::text,'request_hash',departure.request_hash);
    reference := 'inventory-request-' || encode(sha256(convert_to('cloud_oam.inventory.request.v1','UTF8') || decode('00','hex') || convert_to(departure.request_id,'UTF8')),'hex');
    IF (SELECT count(*) FROM public.audit_events WHERE stream_key='material_request' AND aggregate_type='stock_operation_outbound' AND aggregate_id=departure.id::text)<>1
       OR NOT EXISTS (SELECT 1 FROM public.audit_events WHERE stream_key='material_request' AND aggregate_type='stock_operation_outbound'
            AND aggregate_id=departure.id::text AND action='stock_return_outbound' AND actor_user_id=departure.actor_user_id
            AND request_id=departure.request_id AND before_jsonb='{}'::jsonb AND after_jsonb=body
            AND occurred_at=departure.created_at AND created_at=departure.created_at)
       OR (SELECT count(*) FROM public.state_transition_events WHERE aggregate_type='stock_operation_outbound' AND aggregate_id=departure.id::text)<>1
       OR NOT EXISTS (SELECT 1 FROM public.state_transition_events WHERE aggregate_type='stock_operation_outbound' AND aggregate_id=departure.id::text
            AND from_status IS NULL AND to_status='outbound' AND actor_id=departure.actor_user_id AND reason='stock_return_outbound'
            AND idempotency_key='stock_return_outbound:' || departure.id::text AND metadata_jsonb=body
            AND occurred_at=departure.created_at AND created_at=departure.created_at)
       OR (SELECT count(*) FROM public.outbox_events WHERE aggregate_type='stock_operation_outbound' AND aggregate_id=departure.id::text)<>1
       OR NOT EXISTS (SELECT 1 FROM public.outbox_events WHERE aggregate_type='stock_operation_outbound' AND aggregate_id=departure.id::text
            AND event_type='stock_return_outbound' AND idempotency_key='stock_return_outbound:' || departure.id::text AND payload_jsonb=body
            AND created_at=departure.created_at)
       OR NOT EXISTS (SELECT 1 FROM public.audit_events WHERE stream_key='inventory' AND aggregate_type='inventory_transaction' AND aggregate_id=tx.id::text
            AND actor_user_id=departure.actor_user_id AND action='inventory.transaction.posted' AND request_id=reference
            AND (before_jsonb IS NULL OR before_jsonb='null'::jsonb) AND after_jsonb=jsonb_build_object('ledger_cursor',tx.ledger_cursor,
                'movement_count',ordinal,'movement_type','transfer','posting_key',tx.posting_key,'reversed_transaction_id',NULL,'status','posted'))
       OR NOT EXISTS (SELECT 1 FROM public.state_transition_events WHERE aggregate_type='inventory_transaction' AND aggregate_id=tx.id::text
            AND actor_id=departure.actor_user_id AND from_status IS NULL AND to_status='posted' AND reason='inventory_transaction_posted'
            AND idempotency_key='inventory-state-' || encode(sha256(convert_to('cloud_oam.inventory.state.v1','UTF8') || decode('00','hex') || convert_to(tx.id::text,'UTF8') || decode('00','hex') || convert_to('posted','UTF8')),'hex')
            AND metadata_jsonb=jsonb_build_object('ledger_cursor',tx.ledger_cursor,'movement_type','transfer','request_reference',reference))
       OR NOT EXISTS (SELECT 1 FROM public.outbox_events WHERE aggregate_type='inventory_transaction' AND aggregate_id=tx.id::text
            AND event_type='inventory.transaction.posted' AND idempotency_key='inventory-outbox-' || encode(sha256(convert_to('cloud_oam.inventory.outbox.v1','UTF8') || decode('00','hex') || convert_to(tx.id::text,'UTF8') || decode('00','hex') || convert_to('posted','UTF8')),'hex')
            AND payload_jsonb=jsonb_build_object('transaction_id',tx.id::text,'transaction_no',tx.transaction_no,'movement_type','transfer','ledger_cursor',tx.ledger_cursor,'reversed_transaction_id',NULL)) THEN
        RAISE EXCEPTION '0103 complete departure audit, state and outbox required' USING ERRCODE='23514';
    END IF;
END;
"""

DISPATCH_BODY = """
DECLARE
    checked_outbound uuid;
    checked_tx uuid;
    tx public.inventory_transactions%ROWTYPE;
BEGIN
    PERFORM 1 FROM public.inventory_ledger_heads WHERE stream_key='inventory' FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION '0103 inventory ledger head missing' USING ERRCODE='23514'; END IF;
    IF TG_TABLE_NAME='stock_operation_outbounds' THEN checked_outbound := NEW.id;
    ELSIF TG_TABLE_NAME='stock_operation_outbound_lines' THEN checked_outbound := NEW.outbound_id;
    ELSIF TG_TABLE_NAME='stock_operation_outbound_serials' THEN
        SELECT outbound_id INTO checked_outbound FROM public.stock_operation_outbound_lines WHERE id=NEW.line_id;
    ELSIF TG_TABLE_NAME IN ('inventory_transactions','inventory_movements','inventory_movement_serials') THEN
        IF TG_TABLE_NAME='inventory_transactions' THEN checked_tx := NEW.id; ELSE checked_tx := NEW.transaction_id; END IF;
    ELSE
        IF NEW.aggregate_type='stock_operation_outbound' THEN checked_outbound := NEW.aggregate_id::uuid;
        ELSIF NEW.aggregate_type='inventory_transaction' THEN checked_tx := NEW.aggregate_id::uuid;
        ELSE RETURN NULL; END IF;
    END IF;
    IF checked_tx IS NOT NULL THEN
        SELECT * INTO tx FROM public.inventory_transactions WHERE id=checked_tx;
        IF NOT FOUND THEN RAISE EXCEPTION '0103 detached departure inventory event' USING ERRCODE='23514'; END IF;
        IF EXISTS (SELECT 1 FROM public.stock_operation_outbounds WHERE posting_transaction_id=tx.reversed_transaction_id)
           OR EXISTS (SELECT 1 FROM public.inventory_transactions WHERE id=tx.reversed_transaction_id
                AND source_document_type='stock_operation_return_outbound') THEN
            RAISE EXCEPTION '0103 physical departures cannot use generic reversal' USING ERRCODE='23514';
        END IF;
        IF tx.source_document_type='stock_operation_return_outbound' THEN
            SELECT id INTO checked_outbound FROM public.stock_operation_outbounds WHERE posting_transaction_id=tx.id;
        ELSIF EXISTS (SELECT 1 FROM public.inventory_movements movement
            JOIN public.stock_operation_outbound_lines detail ON detail.transit_stock_account_id IN (movement.from_account_id,movement.to_account_id)
            JOIN public.stock_operation_outbounds departure ON departure.id=detail.outbound_id
            JOIN public.inventory_transactions departure_tx ON departure_tx.id=departure.posting_transaction_id
            WHERE movement.transaction_id=tx.id AND departure_tx.ledger_cursor<=tx.ledger_cursor) THEN
            RAISE EXCEPTION '0103 return transit stock requires its typed fulfillment document' USING ERRCODE='23514';
        ELSE RETURN NULL; END IF;
    END IF;
    IF checked_outbound IS NULL THEN RAISE EXCEPTION '0103 departure evidence is detached' USING ERRCODE='23514'; END IF;
    PERFORM public.rsc_check_stock_return_outbound_0103(checked_outbound);
    RETURN NULL;
END;
"""

ACCOUNT_BRANCH = """    IF EXISTS (
        SELECT 1 FROM public.stock_operation_outbound_lines detail
        JOIN public.stock_operation_outbounds departure ON departure.id=detail.outbound_id
        JOIN public.stock_operation_orders parent ON parent.id=departure.operation_id
        JOIN public.inventory_transactions tx ON tx.id=departure.posting_transaction_id
        JOIN public.inventory_movements movement ON movement.transaction_id=tx.id AND movement.line_no=detail.line_no
        JOIN public.stock_accounts source ON source.id=detail.source_stock_account_id
        JOIN public.stock_balances balance ON balance.stock_account_id=NEW.id
        JOIN public.inventory_opening_establishments established ON established.owner_org_id=NEW.owner_org_id AND established.location_id=NEW.location_id
        WHERE detail.transit_stock_account_id=NEW.id AND NEW.availability_bucket='in_transit'
          AND NEW.location_id=parent.transit_location_id AND source.location_id=parent.source_location_id
          AND source.availability_bucket='return_pending' AND NEW.owner_org_id=source.owner_org_id
          AND NEW.custodian_person_id IS NOT DISTINCT FROM source.custodian_person_id
          AND NEW.material_id=source.material_id AND NEW.condition_code=source.condition_code AND NEW.lot_id IS NOT DISTINCT FROM source.lot_id
          AND movement.from_account_id=source.id AND movement.to_account_id=NEW.id AND movement.quantity=detail.quantity
          AND tx.status='posted' AND tx.movement_type='transfer' AND tx.source_document_type='stock_operation_return_outbound'
          AND tx.source_document_id=departure.id::text AND established.established_at<=departure.outbound_at
          AND NEW.created_at>=established.established_at AND NEW.created_at<=tx.created_at AND NEW.updated_at=NEW.created_at
          AND balance.version=1 AND balance.ledger_cursor=tx.ledger_cursor
          AND balance.quantity=(SELECT sum(incoming.quantity) FROM public.inventory_movements incoming WHERE incoming.transaction_id=tx.id AND incoming.to_account_id=NEW.id)
          AND NOT EXISTS (SELECT 1 FROM public.inventory_movements movement_before
            JOIN public.inventory_transactions previous ON previous.id=movement_before.transaction_id
            WHERE NEW.id IN (movement_before.from_account_id,movement_before.to_account_id) AND previous.ledger_cursor<tx.ledger_cursor)
    ) THEN RETURN NEW; END IF;

"""


def _replace_once(body, old, new):
    if body.count(old) != 1: raise RuntimeError('0103 predecessor source drift')
    return body.replace(old, new)


def _sources():
    folder = Path(__file__).parent
    returns = runpy.run_path(str(folder / '20261010_0100_stock_return_orders.py'))
    seals = runpy.run_path(str(folder / '20261011_0101_stock_return_request_seals.py'))
    old = returns['CHECK_BODY']
    new = _replace_once(old,
        'UNION ALL SELECT actor_user_id, request_id FROM public.stock_operation_cancellations) requests',
        'UNION ALL SELECT actor_user_id, request_id FROM public.stock_operation_cancellations\n            UNION ALL SELECT actor_user_id, request_id FROM public.stock_operation_outbounds) requests')
    new = _replace_once(new, 'IF cancellation.id IS NULL OR cancellation.operation_id <> parent.id OR tx.id IS NULL',
        'IF cancellation.id IS NULL OR cancellation.operation_id <> parent.id OR tx.id IS NULL\n           OR EXISTS (SELECT 1 FROM public.stock_operation_outbounds WHERE operation_id=parent.id)')
    result = {'public.rsc_check_stock_return_0100(uuid, uuid)': (old, new)}
    old = returns['DISPATCH_BODY']
    new = _replace_once(old, "        IF tx.source_document_type = 'stock_operation_return' THEN", """        IF tx.source_document_type='stock_operation_return_outbound' THEN
            SELECT id INTO checked_order FROM public.stock_operation_outbounds WHERE posting_transaction_id=tx.id;
            IF checked_order IS NULL THEN RAISE EXCEPTION '0103 detached return departure' USING ERRCODE='23514'; END IF;
            PERFORM public.rsc_check_stock_return_outbound_0103(checked_order);
            RETURN NULL;
        END IF;
        IF tx.source_document_type = 'stock_operation_return' THEN""")
    result['public.rsc_dispatch_stock_return_0100()'] = (old, new)
    old = seals['CHECK_BODY']
    new = _replace_once(old, "TG_TABLE_NAME IN ('stock_operation_orders','stock_operation_cancellations')",
        "TG_TABLE_NAME IN ('stock_operation_orders','stock_operation_cancellations','stock_operation_outbounds')")
    new = _replace_once(new, "seal.operation_type NOT IN ('submit_return','cancel_return')", "seal.operation_type NOT IN ('submit_return','cancel_return','outbound_return')")
    new = _replace_once(new, "seal.operation_type='cancel_return' AND NOT EXISTS", "seal.operation_type IN ('cancel_return','outbound_return') AND NOT EXISTS")
    new = _replace_once(new, "       OR EXISTS (SELECT 1 FROM public.audit_events event WHERE event.actor_user_id=seal.actor_user_id", """       OR EXISTS (SELECT 1 FROM public.stock_operation_outbounds departure
            WHERE departure.actor_user_id=seal.actor_user_id AND departure.request_id=seal.request_id)
       OR EXISTS (SELECT 1 FROM public.audit_events event WHERE event.actor_user_id=seal.actor_user_id""")
    new = _replace_once(new, "event.aggregate_type IN ('stock_operation_order','stock_operation_cancellation')",
        "event.aggregate_type IN ('stock_operation_order','stock_operation_cancellation','stock_operation_outbound')")
    if new.count("tx.source_document_type='stock_operation_return'") != 2: raise RuntimeError('0103 seal posting source drift')
    new = new.replace("tx.source_document_type='stock_operation_return'", "tx.source_document_type IN ('stock_operation_return','stock_operation_return_outbound')")
    result['public.rsc_guard_stock_operation_seal_0101()'] = (old, new)
    old = returns['_account_sources']()[1]
    anchor = runpy.run_path(str(folder / '20260928_0088_receipt_account_admission.py'))['ANCHOR']
    result['public.rsc_require_opening_observation_account_0023()'] = (old, _replace_once(old, anchor, ACCOUNT_BRANCH + anchor))
    return result


FUNCTIONS = {
    ('rsc_check_stock_return_outbound_0103', 'uuid'): ('checked_outbound uuid', 'void', CHECK_BODY),
    ('rsc_dispatch_stock_return_outbound_0103', ''): ('', 'trigger', DISPATCH_BODY),
}
FUNCTION_HASHES = {key: hashlib.sha256(value[2].encode()).hexdigest() for key, value in FUNCTIONS.items()}
TRIGGERS = {}
for _table in TABLES:
    TRIGGERS[f'trg_{_table}_proof_0103'] = (_table, 'INSERT', 'rsc_dispatch_stock_return_outbound_0103', 5, True)
    TRIGGERS[f'trg_{_table}_immutable_0103'] = (_table, 'UPDATE OR DELETE', 'rsc_guard_work_order_facts_0090', 27, False)
    TRIGGERS[f'trg_{_table}_no_truncate_0103'] = (_table, 'TRUNCATE', 'rsc_guard_work_order_facts_0090', 34, False)
for _table in ('inventory_transactions', 'inventory_movements', 'inventory_movement_serials', 'audit_events', 'outbox_events', 'state_transition_events'):
    TRIGGERS[f'trg_{_table}_return_outbound_0103'] = (_table, 'INSERT', 'rsc_dispatch_stock_return_outbound_0103', 5, True)
TRIGGERS['trg_stock_operation_outbounds_seal_0103'] = ('stock_operation_outbounds', 'INSERT', 'rsc_guard_stock_operation_seal_0101', 5, True)


def _create_tables():
    document = sa.JSON().with_variant(postgresql.JSONB(), 'postgresql')
    op.create_table(TABLES[0],
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('outbound_no', sa.String(100), nullable=False),
        sa.Column('operation_id', sa.Uuid(), sa.ForeignKey('stock_operation_orders.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('status', sa.String(24), nullable=False),
        sa.Column('actor_user_id', sa.String(36), sa.ForeignKey('users.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('operator_person_id', sa.Uuid(), sa.ForeignKey('people.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('target_custody_assignment_id', sa.Uuid(), sa.ForeignKey('custody_assignments.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('authorization_version', sa.BigInteger(), nullable=False),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('outbound_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('request_id', sa.String(160), nullable=False),
        sa.Column('idempotency_key_hash', sa.String(64), nullable=False),
        sa.Column('request_hash', sa.String(64), nullable=False),
        sa.Column('plan_hash', sa.String(64), nullable=False),
        sa.Column('command_jsonb', document, nullable=False),
        sa.Column('plan_jsonb', document, nullable=False),
        sa.Column('posting_transaction_id', sa.Uuid(), sa.ForeignKey('inventory_transactions.id', deferrable=True, initially='DEFERRED'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('outbound_no', name='uq_stock_operation_outbounds_no'),
        sa.UniqueConstraint('idempotency_key_hash', name='uq_stock_operation_outbounds_key'),
        sa.UniqueConstraint('actor_user_id', 'request_id', name='uq_stock_operation_outbounds_request'),
        sa.UniqueConstraint('posting_transaction_id', name='uq_stock_operation_outbounds_posting'),
        sa.CheckConstraint("status = 'outbound' AND authorization_version > 0 AND length(reason) BETWEEN 1 AND 500", name='ck_stock_operation_outbounds_context'),
        sa.CheckConstraint('outbound_at <= created_at', name='ck_stock_operation_outbounds_time'))
    op.create_index('ix_stock_operation_outbounds_operation_id', TABLES[0], ['operation_id'])
    op.create_table(TABLES[1],
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('outbound_id', sa.Uuid(), sa.ForeignKey('stock_operation_outbounds.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('line_no', sa.BigInteger(), nullable=False),
        sa.Column('operation_line_id', sa.Uuid(), sa.ForeignKey('stock_operation_lines.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('source_stock_account_id', sa.Uuid(), sa.ForeignKey('stock_accounts.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('transit_stock_account_id', sa.Uuid(), sa.ForeignKey('stock_accounts.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('quantity', sa.Numeric(18,3), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('outbound_id', 'line_no', name='uq_stock_operation_outbound_lines_order'),
        sa.UniqueConstraint('outbound_id', 'operation_line_id', name='uq_stock_operation_outbound_lines_origin'),
        sa.CheckConstraint('line_no > 0 AND quantity > 0 AND source_stock_account_id <> transit_stock_account_id', name='ck_stock_operation_outbound_lines_context'))
    op.create_index('ix_stock_operation_outbound_lines_operation_line_id', TABLES[1], ['operation_line_id'])
    op.create_table(TABLES[2],
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('line_id', sa.Uuid(), sa.ForeignKey('stock_operation_outbound_lines.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('serial_id', sa.Uuid(), sa.ForeignKey('inventory_serials.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('sku_verified', sa.Boolean(), nullable=False),
        sa.Column('qr_verified', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('line_id', 'serial_id', name='uq_stock_operation_outbound_serials_line'))


def _permissions(upgrade):
    now = datetime.now(timezone.utc)
    identifier = UUID('20000000-0000-4000-8000-000000000066')
    permissions = sa.table('permissions', sa.column('id', sa.Uuid()), sa.column('resource', sa.String()),
        sa.column('action', sa.String()), sa.column('field_code', sa.String()), sa.column('description', sa.String()),
        sa.column('created_at', sa.DateTime()), sa.column('updated_at', sa.DateTime()))
    grants = sa.table('role_permissions', sa.column('id', sa.Uuid()), sa.column('role_id', sa.Uuid()),
        sa.column('permission_id', sa.Uuid()), sa.column('effect', sa.String()), sa.column('created_at', sa.DateTime()))
    rows = [dict(id=UUID(f'21000000-0000-4000-8000-{120+role:012d}'), role_id=UUID(f'10000000-0000-4000-8000-{role:012d}'),
        permission_id=identifier, effect='allow', created_at=now) for role in (1,2,3)]
    if upgrade:
        op.bulk_insert(permissions, [dict(id=identifier, resource='stock_operation', action='outbound_return', field_code='',
            description='Formal physical return departure', created_at=now, updated_at=now)])
        op.bulk_insert(grants, rows)
    else:
        op.execute(grants.delete().where(grants.c.id.in_([op.inline_literal(row['id'], type_=sa.Uuid()) for row in rows])))
        op.execute(permissions.delete().where(permissions.c.id==op.inline_literal(identifier, type_=sa.Uuid())))


def _seal_constraints(upgrade):
    kinds = "'submit_return','cancel_return','outbound_return'" if upgrade else "'submit_return','cancel_return'"
    origin = "operation_type IN ('cancel_return','outbound_return')" if upgrade else "operation_type='cancel_return'"
    with op.batch_alter_table('stock_operation_command_seals') as batch:
        batch.drop_constraint('ck_stock_operation_seals_type', type_='check')
        batch.drop_constraint('ck_stock_operation_seals_origin', type_='check')
        batch.create_check_constraint('ck_stock_operation_seals_type', f'operation_type IN ({kinds})')
        batch.create_check_constraint('ck_stock_operation_seals_origin', f"(operation_type='submit_return' AND operation_id IS NULL) OR ({origin} AND operation_id IS NOT NULL)")
    if op.get_bind().dialect.name=='sqlite':
        for action in ('UPDATE','DELETE'):
            op.execute(f"CREATE TRIGGER trg_stock_operation_seals_{action.lower()}_0101 BEFORE {action} ON stock_operation_command_seals BEGIN SELECT RAISE(ABORT, '0101 return request seals are append-only'); END")


def _transition(upgrade):
    db = op.get_bind()
    if db.dialect.name not in {'postgresql','sqlite'}: raise RuntimeError('0103 supports PostgreSQL and SQLite only')
    folder = Path(__file__).parent
    helper = runpy.run_path(str(folder / '20260927_0087_inbound_fulfillment_boundary.py'))
    helper['_begin_sqlite']()
    if db.dialect.name=='postgresql':
        op.execute('LOCK TABLE public.alembic_version IN ACCESS EXCLUSIVE MODE')
        op.execute('LOCK TABLE public.inventory_ledger_heads, public.inventory_transactions, public.inventory_movements, public.stock_accounts, public.stock_operation_orders, public.stock_operation_cancellations, public.stock_operation_command_seals, public.audit_events IN SHARE ROW EXCLUSIVE MODE')
    if not upgrade:
        if db.dialect.name=='postgresql': op.execute('LOCK TABLE ' + ','.join('public.'+table for table in TABLES) + ' IN SHARE ROW EXCLUSIVE MODE')
        helper['_preflight'](' OR '.join(f'EXISTS (SELECT 1 FROM {table})' for table in TABLES) + " OR EXISTS (SELECT 1 FROM stock_operation_command_seals WHERE operation_type='outbound_return')",
            '0103 downgrade blocked: immutable physical departure history or request seals must be retained')
    if upgrade:
        _create_tables()
        _permissions(True)
    _seal_constraints(upgrade)
    if db.dialect.name=='postgresql':
        replace = runpy.run_path(str(folder / '20260912_0072_outbound_postings.py'))['_previous']()['_previous']()['_previous']()['_replace_function_source']
        if upgrade:
            for (name, signature), (args, result, body) in FUNCTIONS.items():
                op.execute(f'CREATE FUNCTION public.{name}({args}) RETURNS {result} LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $body${body}$body$')
                op.execute(f'REVOKE ALL ON FUNCTION public.{name}({signature}) FROM PUBLIC, star_oam_api')
            for name, (table, events, function, _, deferred) in TRIGGERS.items():
                sql = (f'CREATE CONSTRAINT TRIGGER {name} AFTER {events} ON public.{table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW' if deferred
                    else f"CREATE TRIGGER {name} BEFORE {events} ON public.{table} FOR EACH {'STATEMENT' if events=='TRUNCATE' else 'ROW'}")
                op.execute(sql + f' EXECUTE FUNCTION public.{function}()')
                op.execute(f'ALTER TABLE public.{table} ENABLE ALWAYS TRIGGER {name}')
            op.execute('GRANT SELECT, INSERT ON ' + ','.join('public.'+table for table in TABLES) + ' TO star_oam_api')
        else:
            for (name, signature), digest in FUNCTION_HASHES.items():
                op.execute(f"""DO $body$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_proc p
                    WHERE p.oid='public.{name}({signature})'::regprocedure
                      AND encode(sha256(convert_to(p.prosrc,'UTF8')),'hex')='{digest}'
                      AND p.proowner=(SELECT oid FROM pg_roles WHERE rolname=current_user) AND p.prosecdef
                      AND p.proconfig=ARRAY['search_path=pg_catalog, public']
                      AND NOT EXISTS (SELECT 1 FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) acl WHERE acl.grantee<>p.proowner))
                    THEN RAISE EXCEPTION '0103 function source, ownership or ACL drift'; END IF; END $body$""")
        for signature, (old,new) in _sources().items():
            replace(signature=signature, expected_hash=hashlib.sha256((old if upgrade else new).encode()).hexdigest(),
                replacement_hash=hashlib.sha256((new if upgrade else old).encode()).hexdigest(),
                replacements=((old,new),) if upgrade else ((new,old),), label='stock_return_outbound_0103')
        replace(signature='public.rsc_oam_runtime_binding_ready_0044()', expected_hash=OLD_HASH if upgrade else NEW_HASH,
            replacement_hash=NEW_HASH if upgrade else OLD_HASH, replacements=((down_revision,revision),) if upgrade else ((revision,down_revision),), label='stock_return_outbound_readiness_0103')
        if not upgrade:
            for name, (table,*_) in TRIGGERS.items(): op.execute(f'DROP TRIGGER {name} ON public.{table}')
            for name, signature in reversed(FUNCTIONS): op.execute(f'DROP FUNCTION public.{name}({signature})')
    if db.dialect.name=='sqlite' and upgrade:
        for table in TABLES:
            for action in ('UPDATE','DELETE'):
                op.execute(f"CREATE TRIGGER trg_{table}_{action.lower()}_0103 BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT, '0103 physical departures are append-only'); END")
    if not upgrade:
        _permissions(False)
        for table in reversed(TABLES): op.drop_table(table)


def upgrade(): _transition(True)
def downgrade(): _transition(False)
